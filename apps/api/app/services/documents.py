from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Select, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.markdown import (
    MarkdownValidationError,
    canonical_document_max_utf8_bytes,
    prepare_client_document,
    validate_canonical,
)
from app.models import (
    ChangeEvent,
    Document,
    DocumentVersion,
    IdempotencyRecord,
    SearchProjectionTask,
)
from app.services.artifact_durability import (
    CANONICAL_DOCUMENT_CREATE_TARGET_IDS,
    ArtifactDescriptor,
    ArtifactDurabilityUnavailable,
    ArtifactIntentGateLease,
    ArtifactReconciler,
    acquire_artifact_intent_lock,
    derive_artifact_intent_uuid,
    stage_artifact,
)
from app.services.exact_search import ExactSearchService, ExactSearchUnavailable
from app.services.reservations import identifier_is_reserved
from app.services.storage import StorageIntegrityError, VersionStore
from app.services.taxonomy import (
    TaxonomyProjectionError,
    TaxonomyUnavailable,
    replace_document_projection,
)


class DocumentNotFoundError(LookupError):
    pass


class DocumentForbiddenError(PermissionError):
    pass


class DocumentConflictError(ValueError):
    pass


class DocumentPreconditionError(ValueError):
    pass


_PUBLIC_OWNER_NAMESPACE = UUID("7d9c9663-2778-51a2-b6be-171d10dcf968")


def public_owner_id(principal_subject: str) -> str:
    """Return a stable connect.md-local owner ID without publishing Clerk's subject."""
    return str(uuid5(_PUBLIC_OWNER_NAMESPACE, principal_subject))


def strong_etag(digest: str) -> str:
    return f'"sha256-{digest}"'


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
STRONG_DOCUMENT_ETAG_PATTERN = r'^"sha256-[0-9a-f]{64}"$'


def validate_proposal_decision_receipt_prefix(prefix: str, document_id: str, version: int) -> None:
    parts = prefix.split(":")
    if len(parts) != 2 or parts[1] != "accept":
        raise DocumentConflictError("proposal decision receipt is malformed")
    try:
        proposal_uuid = UUID(parts[0])
        document_uuid = UUID(document_id)
    except (AttributeError, ValueError) as exc:
        raise DocumentConflictError("proposal decision receipt is malformed") from exc
    if str(proposal_uuid) != parts[0] or str(document_uuid) != document_id or version < 1:
        raise DocumentConflictError("proposal decision receipt is malformed")


def bind_proposal_decision_receipt(prefix: str, document_id: str, version: int, digest: str) -> str:
    validate_proposal_decision_receipt_prefix(prefix, document_id, version)
    if not _SHA256_HEX_RE.fullmatch(digest):
        raise DocumentConflictError("proposal decision receipt is malformed")
    return f"{prefix}:{document_id}:{version}:{digest}"


def if_match_satisfied(header: str, current_etag: str) -> bool:
    """Require one byte-exact current strong document ETag."""

    return header == current_etag


def _canonical_timestamp(value: object) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise StorageIntegrityError("canonical document timestamp is unavailable") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StorageIntegrityError("canonical document timestamp is unavailable")
    return parsed.astimezone(UTC)


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        store: VersionStore,
        settings: Settings | None = None,
        artifact_reconciler: ArtifactReconciler | None = None,
    ) -> None:
        self.session = session
        self.store = store
        self.settings = settings
        self.exact_search = ExactSearchService(settings) if settings is not None else None
        self.artifact_reconciler = artifact_reconciler

    def _artifact_intent(
        self,
        record: IdempotencyRecord | None,
        *,
        target_id: str,
        owner_id: str,
        resource_types: frozenset[str],
        response_status: int,
    ) -> tuple[ArtifactReconciler, str, str] | None:
        reconciler = self.artifact_reconciler
        if reconciler is None or not reconciler.enabled:
            return None
        pepper = self.settings.api_key_pepper if self.settings is not None else None
        if (
            record is None
            or record.owner_id != owner_id
            or record.resource_type not in resource_types
            or record.response_status != response_status
            or not record.operation
            or not record.idempotency_key
            or pepper is None
            or len(pepper.encode("utf-8")) < 16
            or _SHA256_HEX_RE.fullmatch(record.request_hash) is None
        ):
            reconciler.mark_unavailable()
            raise StorageIntegrityError("canonical artifact durability is unavailable")
        try:
            intent_id = derive_artifact_intent_uuid(
                pepper,
                flow="canonical_document_version",
                owner_id=record.owner_id,
                target_id=target_id,
                idempotency_key=record.idempotency_key,
            )
        except ValueError as exc:
            reconciler.mark_unavailable()
            raise StorageIntegrityError("canonical artifact durability is unavailable") from exc
        return reconciler, pepper, intent_id

    async def _reconcile_failed_artifact(
        self,
        descriptor: ArtifactDescriptor | None,
        relative_path: str,
        *,
        commit_started: bool,
        known_uncommitted: bool,
    ) -> None:
        reconciler = self.artifact_reconciler
        if descriptor is None:
            if reconciler is not None and reconciler.enabled:
                return
            if known_uncommitted or not commit_started:
                self.store.remove_new_file(relative_path)
            return
        if reconciler is None:
            return
        await reconciler.reconcile_descriptor(
            descriptor,
            respect_grace=False,
            gate_held=True,
        )

    def _retire_committed_artifact(self, descriptor: ArtifactDescriptor | None) -> None:
        if descriptor is None:
            return
        try:
            self.store.retire_staged_artifact(
                descriptor.staged_payload_path,
                descriptor.staged_descriptor_path,
            )
        except StorageIntegrityError:
            if self.artifact_reconciler is not None:
                self.artifact_reconciler.mark_unavailable()

    @staticmethod
    def _lookup(kind: str, identifier: str) -> Select[tuple[Document]]:
        return select(Document).where(
            Document.kind == kind, Document.public_identifier == identifier
        )

    async def create(
        self,
        kind: str,
        markdown: str,
        owner_id: str,
        *,
        actor_id: str | None = None,
        actor_method: str = "clerk_jwt",
        grant_id: str | None = None,
        idempotency_record: IdempotencyRecord | None = None,
    ) -> Document:
        create_target_id = CANONICAL_DOCUMENT_CREATE_TARGET_IDS.get(kind)
        artifact_context = (
            self._artifact_intent(
                idempotency_record,
                target_id=create_target_id,
                owner_id=owner_id,
                resource_types=frozenset({kind}),
                response_status=201,
            )
            if create_target_id is not None
            else None
        )
        document_id = artifact_context[2] if artifact_context is not None else str(uuid4())
        now = datetime.now(UTC)
        canonical, frontmatter = prepare_client_document(
            kind,
            markdown,
            document_id=document_id,
            owner_id=public_owner_id(owner_id),
            version=1,
            updated_at=now,
        )
        identifier = frontmatter["handle"] if kind == "profile" else frontmatter["slug"]
        if self.settings is not None and await identifier_is_reserved(
            self.session,
            self.settings,
            namespace=f"document:{kind}",
            identifier=identifier,
        ):
            raise DocumentConflictError("a document already uses that public identifier")
        relative_path = self.store.relative_path(kind, document_id, 1)
        descriptor: ArtifactDescriptor | None = None
        lease: ArtifactIntentGateLease | None = None
        commit_started = False
        try:
            if artifact_context is None:
                digest = self.store.write_immutable(relative_path, canonical)
            else:
                reconciler, pepper, intent_id = artifact_context
                lease = await reconciler.acquire_intent_gate(intent_id)
                await acquire_artifact_intent_lock(self.session, intent_id)
                assert idempotency_record is not None
                assert create_target_id is not None
                with self.session.no_autoflush:
                    existing = await self.session.scalar(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.owner_id == idempotency_record.owner_id,
                            IdempotencyRecord.idempotency_key == idempotency_record.idempotency_key,
                        )
                    )
                if existing is not None:
                    raise DocumentConflictError("idempotent document creation already committed")
                descriptor = stage_artifact(
                    self.store,
                    pepper,
                    flow="canonical_document_version",
                    owner_id=owner_id,
                    target_id=create_target_id,
                    idempotency_key=idempotency_record.idempotency_key,
                    request_hash=idempotency_record.request_hash,
                    canonical_path=relative_path,
                    payload=canonical.encode("utf-8"),
                    max_size_bytes=canonical_document_max_utf8_bytes(),
                    resource_id=document_id,
                )
                try:
                    canonical = self.store.read_verified_bytes(
                        descriptor.canonical_path,
                        descriptor.payload_sha256,
                        expected_size_bytes=descriptor.payload_size_bytes,
                        max_size_bytes=canonical_document_max_utf8_bytes(),
                    ).decode("utf-8")
                    frontmatter, _ = validate_canonical(kind, canonical)
                    if (
                        frontmatter.get("id") != document_id
                        or frontmatter.get("owner_id") != public_owner_id(owner_id)
                        or frontmatter.get("version") != 1
                        or frontmatter.get("handle" if kind == "profile" else "slug") != identifier
                    ):
                        raise StorageIntegrityError(
                            "canonical document does not match its staged authority"
                        )
                    now = _canonical_timestamp(frontmatter.get("updated_at"))
                except (MarkdownValidationError, UnicodeDecodeError) as exc:
                    raise StorageIntegrityError(
                        "canonical document staging is unavailable"
                    ) from exc
                digest = descriptor.payload_sha256

            document = Document(
                id=document_id,
                kind=kind,
                owner_id=owner_id,
                public_identifier=identifier,
                visibility=frontmatter["visibility"],
                schema_version=int(frontmatter["schema_version"]),
                current_version=1,
                created_at=now,
                updated_at=now,
            )
            document.versions.append(
                DocumentVersion(
                    version=1,
                    sha256=digest,
                    storage_path=relative_path,
                    actor_id=actor_id or owner_id,
                    actor_method=actor_method,
                    grant_id=grant_id,
                    created_at=now,
                )
            )
            self.session.add(document)
            if idempotency_record is not None:
                idempotency_record.resource_id = f"{document_id}@1"
                self.session.add(idempotency_record)
            self.session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type="document.created",
                    resource_type=kind,
                    resource_id=document_id,
                    actor_id=actor_id or owner_id,
                    actor_method=actor_method,
                    grant_id=grant_id,
                    payload=json.dumps(
                        {
                            "identifier": identifier,
                            "version": 1,
                            "visibility": frontmatter["visibility"],
                            "etag": strong_etag(digest),
                        },
                        sort_keys=True,
                    ),
                    occurred_at=now,
                )
            )
            self.session.add(
                SearchProjectionTask(
                    document_id=document_id,
                    version=1,
                    state="pending",
                    attempts=0,
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            # The existing-document uniqueness check serializes against a concurrent
            # erasure. Rechecking after this flush closes the delete/reserve race.
            await replace_document_projection(
                self.session,
                document=document,
                frontmatter=frontmatter,
                document_version=1,
            )
            if self.exact_search is not None:
                await self.exact_search.upsert_document(
                    self.session,
                    document=document,
                    canonical=canonical,
                    frontmatter=frontmatter,
                    digest=digest,
                    document_version=1,
                )
            await self.session.flush()
            if self.settings is not None and await identifier_is_reserved(
                self.session,
                self.settings,
                namespace=f"document:{kind}",
                identifier=identifier,
            ):
                raise DocumentConflictError("a document already uses that public identifier")
            commit_started = True
            await self.session.commit()
        except DocumentConflictError:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise
        except TaxonomyUnavailable:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise
        except ExactSearchUnavailable:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise
        except TaxonomyProjectionError as exc:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise DocumentConflictError("public taxonomy projection rejected the document") from exc
        except IntegrityError as exc:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise DocumentConflictError("a document already uses that public identifier") from exc
        except ArtifactDurabilityUnavailable as exc:
            await self.session.rollback()
            if self.artifact_reconciler is not None:
                self.artifact_reconciler.mark_unavailable()
            raise StorageIntegrityError("canonical artifact durability is unavailable") from exc
        except asyncio.CancelledError:
            await self.session.rollback()
            if descriptor is not None and self.artifact_reconciler is not None:
                self.artifact_reconciler.mark_unavailable()
            elif not commit_started:
                self.store.remove_new_file(relative_path)
            raise
        except BaseException:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=False,
            )
            raise
        finally:
            if lease is not None:
                await lease.release()
        self._retire_committed_artifact(descriptor)
        await self.session.refresh(document, attribute_names=["versions"])
        return document

    async def get(self, kind: str, identifier: str) -> Document:
        document = await self.session.scalar(
            self._lookup(kind, identifier).options(selectinload(Document.versions))
        )
        if document is None:
            raise DocumentNotFoundError("document was not found")
        return document

    async def get_version(self, document: Document, version: int) -> DocumentVersion:
        row = await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id, DocumentVersion.version == version
            )
        )
        if row is None:
            raise DocumentNotFoundError("document version was not found")
        return row

    async def update(
        self,
        kind: str,
        identifier: str,
        markdown: str,
        owner_id: str,
        *,
        if_match: str | None = None,
        actor_id: str | None = None,
        actor_method: str = "clerk_jwt",
        grant_id: str | None = None,
        resource_id: str | None = None,
        idempotency_record: IdempotencyRecord | None = None,
    ) -> Document:
        document = await self.session.scalar(
            self._lookup(kind, identifier)
            .with_for_update()
            .options(selectinload(Document.versions))
        )
        if document is None:
            raise DocumentNotFoundError("document was not found")
        if self.session.get_bind().dialect.name == "postgresql":
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:document_id, 0))"),
                {"document_id": document.id},
            )
        if document.owner_id != owner_id:
            # Do not let authenticated enumerators distinguish private identifiers.
            raise DocumentNotFoundError("document was not found")
        if resource_id is not None and document.id != resource_id:
            raise DocumentNotFoundError("document was not found")
        version = document.current_version + 1
        proposal_decision_prefix = None
        if (
            idempotency_record is not None
            and idempotency_record.resource_type == "proposal_decision"
        ):
            proposal_decision_prefix = idempotency_record.resource_id or ""
            validate_proposal_decision_receipt_prefix(
                proposal_decision_prefix, document.id, version
            )
        now = datetime.now(UTC)
        current_row = next(
            row for row in document.versions if row.version == document.current_version
        )
        if if_match is not None and not if_match_satisfied(
            if_match, strong_etag(current_row.sha256)
        ):
            raise DocumentPreconditionError("If-Match does not match the current document ETag")
        current_frontmatter, _ = validate_canonical(kind, self.read_markdown(current_row))
        canonical, frontmatter = prepare_client_document(
            kind,
            markdown,
            document_id=document.id,
            owner_id=public_owner_id(owner_id),
            version=version,
            updated_at=now,
            expected_server_fields=current_frontmatter,
        )
        identity_field = "handle" if kind == "profile" else "slug"
        if frontmatter[identity_field] != document.public_identifier:
            raise MarkdownValidationError(
                f"{kind} {identity_field} is immutable; create a new {kind} to use another {identity_field}"
            )
        relative_path = self.store.relative_path(kind, document.id, version)
        artifact_context = self._artifact_intent(
            idempotency_record,
            target_id=document.id,
            owner_id=owner_id,
            resource_types=frozenset({kind, "proposal_decision"}),
            response_status=200,
        )
        descriptor: ArtifactDescriptor | None = None
        lease: ArtifactIntentGateLease | None = None
        commit_started = False
        try:
            if artifact_context is None:
                digest = self.store.write_immutable(relative_path, canonical)
            else:
                reconciler, pepper, intent_id = artifact_context
                lease = await reconciler.acquire_intent_gate(intent_id)
                await acquire_artifact_intent_lock(self.session, intent_id)
                assert idempotency_record is not None
                descriptor = stage_artifact(
                    self.store,
                    pepper,
                    flow="canonical_document_version",
                    owner_id=owner_id,
                    target_id=document.id,
                    idempotency_key=idempotency_record.idempotency_key,
                    request_hash=idempotency_record.request_hash,
                    canonical_path=relative_path,
                    payload=canonical.encode("utf-8"),
                    max_size_bytes=canonical_document_max_utf8_bytes(),
                    resource_id=document.id,
                )
                try:
                    canonical = self.store.read_verified_bytes(
                        descriptor.canonical_path,
                        descriptor.payload_sha256,
                        expected_size_bytes=descriptor.payload_size_bytes,
                        max_size_bytes=canonical_document_max_utf8_bytes(),
                    ).decode("utf-8")
                    frontmatter, _ = validate_canonical(kind, canonical)
                    if (
                        frontmatter.get("id") != document.id
                        or frontmatter.get("owner_id") != public_owner_id(owner_id)
                        or frontmatter.get("version") != version
                        or frontmatter.get(identity_field) != document.public_identifier
                    ):
                        raise StorageIntegrityError(
                            "canonical document does not match its staged authority"
                        )
                    now = _canonical_timestamp(frontmatter.get("updated_at"))
                except (MarkdownValidationError, UnicodeDecodeError) as exc:
                    raise StorageIntegrityError(
                        "canonical document staging is unavailable"
                    ) from exc
                digest = descriptor.payload_sha256

            proposal_decision_resource_id = None
            if proposal_decision_prefix is not None:
                proposal_decision_resource_id = bind_proposal_decision_receipt(
                    proposal_decision_prefix, document.id, version, digest
                )
            document.current_version = version
            document.visibility = frontmatter["visibility"]
            document.schema_version = int(frontmatter["schema_version"])
            document.updated_at = now
            document.versions.append(
                DocumentVersion(
                    version=version,
                    sha256=digest,
                    storage_path=relative_path,
                    actor_id=actor_id or owner_id,
                    actor_method=actor_method,
                    grant_id=grant_id,
                    created_at=now,
                )
            )
            if idempotency_record is not None:
                idempotency_record.resource_id = (
                    proposal_decision_resource_id
                    if proposal_decision_resource_id is not None
                    else f"{document.id}@{version}"
                )
                self.session.add(idempotency_record)
            self.session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type="document.updated",
                    resource_type=kind,
                    resource_id=document.id,
                    actor_id=actor_id or owner_id,
                    actor_method=actor_method,
                    grant_id=grant_id,
                    payload=json.dumps(
                        {
                            "identifier": document.public_identifier,
                            "version": version,
                            "visibility": frontmatter["visibility"],
                            "etag": strong_etag(digest),
                        },
                        sort_keys=True,
                    ),
                    occurred_at=now,
                )
            )
            await self.session.execute(
                delete(SearchProjectionTask).where(
                    SearchProjectionTask.document_id == document.id,
                    SearchProjectionTask.version < version,
                    SearchProjectionTask.state == "dead_letter",
                )
            )
            await replace_document_projection(
                self.session,
                document=document,
                frontmatter=frontmatter,
                document_version=version,
            )
            if self.exact_search is not None:
                await self.exact_search.upsert_document(
                    self.session,
                    document=document,
                    canonical=canonical,
                    frontmatter=frontmatter,
                    digest=digest,
                    document_version=version,
                )
            self.session.add(
                SearchProjectionTask(
                    document_id=document.id,
                    version=version,
                    state="pending",
                    attempts=0,
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await self.session.flush()
            commit_started = True
            await self.session.commit()
        except DocumentConflictError:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise
        except TaxonomyUnavailable:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise
        except ExactSearchUnavailable:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise
        except TaxonomyProjectionError as exc:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise DocumentConflictError("public taxonomy projection rejected the document") from exc
        except IntegrityError as exc:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=True,
            )
            raise DocumentConflictError("document update could not be committed") from exc
        except ArtifactDurabilityUnavailable as exc:
            await self.session.rollback()
            if self.artifact_reconciler is not None:
                self.artifact_reconciler.mark_unavailable()
            raise StorageIntegrityError("canonical artifact durability is unavailable") from exc
        except asyncio.CancelledError:
            await self.session.rollback()
            if descriptor is not None and self.artifact_reconciler is not None:
                self.artifact_reconciler.mark_unavailable()
            elif not commit_started:
                self.store.remove_new_file(relative_path)
            raise
        except BaseException:
            await self.session.rollback()
            await self._reconcile_failed_artifact(
                descriptor,
                relative_path,
                commit_started=commit_started,
                known_uncommitted=False,
            )
            raise
        finally:
            if lease is not None:
                await lease.release()
        self._retire_committed_artifact(descriptor)
        await self.session.refresh(document, attribute_names=["versions"])
        return document

    def read_markdown(self, version: DocumentVersion) -> str:
        return self.store.read_verified(version.storage_path, version.sha256)
