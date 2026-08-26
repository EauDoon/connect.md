"""Canonical PostgreSQL exact search over a verified public projection.

Meilisearch remains the default candidate/ranking projection.  This service is
explicitly selected by ``mode=exact`` and never falls back to that projection.
The projection stores only public, version-bound display/search data; canonical
Markdown remains in the version store and is the source of truth.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, exists, func, literal, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.markdown import (
    MarkdownSizeError,
    require_canonical_document_size,
    validate_canonical,
)
from app.models import (
    AgentIdentity,
    Document,
    DocumentVersion,
    PublicExactSearchCompactValue,
    PublicExactSearchDocumentSnapshot,
    PublicExactSearchProjectionState,
    PublicTaxonomyMembership,
    PublicTaxonomyProjectionState,
    PublicTaxonomyTerm,
)
from app.services.exact_search_documents import (
    _SHA256_RE,
    ExactSearchUnavailable,
    _normalize_search_text,
    _snapshot_values,
)
from app.services.storage import VersionStore

EXACT_SEARCH_SCOPE = "documents"
EXACT_SEARCH_CONTRACT_DIGEST = hashlib.sha256(b"connect.md:exact-public-search:v1").hexdigest()
EXACT_SEARCH_MAX_DOCUMENTS = 50_000
EXACT_SEARCH_MATERIALIZATION_LIMIT = EXACT_SEARCH_MAX_DOCUMENTS + 1
EXACT_SEARCH_CURSOR_MAX_LENGTH = 2048
EXACT_SEARCH_CURSOR_DOMAIN = b"connect.md:exact-search-cursor:v1"
EXACT_SEARCH_AGENT_ELIGIBILITY_DOMAIN = b"connect.md:exact-search-agent-eligibility:v1"
EXACT_SEARCH_TOO_BROAD_MESSAGE = "exact search requires a narrower query"
_KID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


class ExactSearchTooBroad(ValueError):
    """The canonical candidate set exceeds the complete exact-search bound."""


class ExactSearchCursorMalformed(ValueError):
    """The exact cursor cannot be trusted or does not match its contract."""


class ExactSearchCursorStale(ValueError):
    """The exact or taxonomy revision changed since the cursor was issued."""


@dataclass(frozen=True)
class ExactSearchCursorKey:
    kid: str
    secret: bytes


@dataclass(frozen=True)
class ExactSearchResult:
    hits: list[dict[str, Any]]
    facet_hits: list[dict[str, Any]]
    total: int
    next_cursor: str | None
    revision: int
    complete: bool = True


def _decode_secret(raw: str) -> bytes:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        secret = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("exact search cursor secret is invalid") from exc
    if len(secret) < 32:
        raise ValueError("exact search cursor secret is too short")
    return secret


def _parse_keyring(
    raw: str | None, *, production: bool, fallback_material: str
) -> tuple[ExactSearchCursorKey, ...]:
    if raw is None:
        if production:
            raise ValueError("CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING is required in production")
        return (
            ExactSearchCursorKey(
                kid="development",
                secret=hashlib.sha256(fallback_material.encode("utf-8")).digest(),
            ),
        )
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("exact search cursor keyring is invalid") from exc
    values = parsed if isinstance(parsed, list) else [parsed]
    if not 1 <= len(values) <= 3:
        raise ValueError("exact search cursor keyring must contain one to three keys")
    keys: list[ExactSearchCursorKey] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"kid", "secret"}:
            raise ValueError("exact search cursor keyring is invalid")
        kid = value.get("kid")
        secret_value = value.get("secret")
        if not isinstance(kid, str) or not _KID_RE.fullmatch(kid) or kid in seen:
            raise ValueError("exact search cursor keyring is invalid")
        if not isinstance(secret_value, str):
            raise ValueError("exact search cursor keyring is invalid")
        keys.append(ExactSearchCursorKey(kid=kid, secret=_decode_secret(secret_value)))
        seen.add(kid)
    return tuple(keys)


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("search time bounds are invalid") from exc
    else:
        raise ValueError("search time bounds are invalid")
    if parsed.tzinfo is None:
        raise ValueError("search time bounds require a timezone")
    return parsed.astimezone(UTC)


class ExactSearchService:
    def __init__(self, settings: Any) -> None:
        fallback = f"development:{settings.database_url}:{settings.storage_path}"
        self.cursor_keys = _parse_keyring(
            getattr(settings, "exact_search_cursor_keyring", None),
            production=bool(getattr(settings, "is_production", False)),
            fallback_material=fallback,
        )
        self.cursor_ttl_seconds = int(getattr(settings, "exact_search_cursor_ttl_seconds", 900))
        self.database_url = str(settings.database_url)

    @staticmethod
    async def _table_exists(session: AsyncSession, table_name: str) -> bool:
        dialect = session.get_bind().dialect.name
        try:
            if dialect == "sqlite":
                value = await session.scalar(
                    text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
                    {"table_name": table_name},
                )
                return value is not None
            if dialect == "postgresql":
                value = await session.scalar(
                    text("SELECT to_regclass(:qualified_name)"),
                    {"qualified_name": f"public.{table_name}"},
                )
                return value is not None
            return True
        except SQLAlchemyError as exc:
            raise ExactSearchUnavailable("exact search projection is unreadable") from exc

    @staticmethod
    async def _state(
        session: AsyncSession, *, lock: bool = False
    ) -> PublicExactSearchProjectionState | None:
        statement = select(PublicExactSearchProjectionState).where(
            PublicExactSearchProjectionState.scope == EXACT_SEARCH_SCOPE
        )
        if lock:
            statement = statement.with_for_update()
        statement = statement.execution_options(populate_existing=True)
        try:
            return await session.scalar(statement)
        except SQLAlchemyError as exc:
            raise ExactSearchUnavailable("exact search projection is unreadable") from exc

    @staticmethod
    async def _state_values(session: AsyncSession) -> tuple[int, str, str] | None:
        statement = select(
            PublicExactSearchProjectionState.revision,
            PublicExactSearchProjectionState.status,
            PublicExactSearchProjectionState.contract_digest,
        ).where(PublicExactSearchProjectionState.scope == EXACT_SEARCH_SCOPE)
        try:
            row = (await session.execute(statement)).one_or_none()
        except SQLAlchemyError as exc:
            raise ExactSearchUnavailable("exact search projection is unreadable") from exc
        if row is None:
            return None
        return int(row.revision), str(row.status), str(row.contract_digest)

    @staticmethod
    async def _alembic_marker(session: AsyncSession) -> str | None:
        if not await ExactSearchService._table_exists(session, "alembic_version"):
            return None
        try:
            value = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        except SQLAlchemyError as exc:
            raise ExactSearchUnavailable("exact search migration state is unreadable") from exc
        return str(value) if value is not None else None

    async def _installed(self, session: AsyncSession) -> bool:
        marker = await self._alembic_marker(session)
        state_table_exists = await self._table_exists(
            session, PublicExactSearchProjectionState.__tablename__
        )
        if marker is None:
            if not state_table_exists:
                return False
            # Base.metadata.create_all in local tests has no Alembic marker.
            # Treat an explicit state row as an intentionally installed test
            # projection, but an empty table as legacy/pre-0025 compatibility.
            return await self._state(session) is not None
        match = re.fullmatch(r"00(\d+)_.*", marker)
        if match is not None:
            migration_number = int(match.group(1))
            if migration_number >= 25:
                # A current/future marker is authoritative: missing state or
                # tables must fail closed in require_ready, never look legacy.
                return True
            if state_table_exists:
                raise ExactSearchUnavailable(
                    "exact search projection exists before its migration marker"
                )
            return False
        # An unrecognized Alembic marker may be a future branch.  Treat it as
        # installed so the subsequent state query fails closed if unreadable.
        return True

    async def is_installed(self, session: AsyncSession) -> bool:
        """Return whether the 0025 projection is present without requiring readiness."""
        return await self._installed(session)

    async def require_ready(
        self, session: AsyncSession, *, require_postgresql: bool = False
    ) -> PublicExactSearchProjectionState:
        if not await self._installed(session):
            raise ExactSearchUnavailable("exact search projection is not installed")
        state = await self._state(session)
        if state is None or state.status != "ready":
            raise ExactSearchUnavailable("exact search projection is not ready")
        if state.contract_digest != EXACT_SEARCH_CONTRACT_DIGEST:
            raise ExactSearchUnavailable("exact search projection contract is invalid")
        if require_postgresql and session.get_bind().dialect.name != "postgresql":
            raise ExactSearchUnavailable("exact search requires PostgreSQL")
        return state

    async def require_ready_for_write(self, session: AsyncSession) -> None:
        if not await self._installed(session):
            return
        await self.require_ready(session)

    async def _locked_write_state(
        self,
        session: AsyncSession,
        *,
        installed: bool,
        rebuild: bool,
    ) -> PublicExactSearchProjectionState | None:
        """Acquire the state row and validate it at the mutation boundary."""
        state = await self._state(session, lock=True)
        if state is None:
            if installed or rebuild:
                raise ExactSearchUnavailable("exact search projection state is missing")
            return None
        if not rebuild and (
            state.status != "ready" or state.contract_digest != EXACT_SEARCH_CONTRACT_DIGEST
        ):
            raise ExactSearchUnavailable("exact search projection is not ready")
        return state

    @staticmethod
    async def _taxonomy_revision_digest(session: AsyncSession) -> str:
        if not await ExactSearchService._table_exists(
            session, PublicTaxonomyProjectionState.__tablename__
        ):
            return hashlib.sha256(b"taxonomy:absent").hexdigest()
        try:
            rows = (
                await session.execute(
                    select(
                        PublicTaxonomyProjectionState.taxonomy,
                        PublicTaxonomyProjectionState.revision,
                        PublicTaxonomyProjectionState.status,
                        PublicTaxonomyProjectionState.contract_digest,
                    ).order_by(PublicTaxonomyProjectionState.taxonomy)
                )
            ).all()
        except SQLAlchemyError as exc:
            raise ExactSearchUnavailable("taxonomy revision state is unreadable") from exc
        payload = json.dumps(
            [tuple(row) for row in rows], separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _filter_digest(arguments: dict[str, Any], resolved: Any) -> str:
        relevant = {
            key: value
            for key, value in arguments.items()
            if key not in {"mode", "cursor", "offset", "limit", "facet_limit"}
        }
        relevant["canonical"] = resolved.canonical
        payload = json.dumps(relevant, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _encode_cursor(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        key = self.cursor_keys[0]
        signature = (
            hmac.new(key.secret, EXACT_SEARCH_CURSOR_DOMAIN + b"\0" + body, hashlib.sha256)
            .hexdigest()
            .encode("ascii")
        )
        token = base64.urlsafe_b64encode(body + b"." + signature).decode("ascii").rstrip("=")
        if len(token) > EXACT_SEARCH_CURSOR_MAX_LENGTH:
            raise ExactSearchUnavailable("exact search cursor exceeded its bound")
        return token

    def _decode_cursor(self, cursor: str) -> dict[str, Any]:
        if (
            not isinstance(cursor, str)
            or not cursor.strip()
            or len(cursor) > EXACT_SEARCH_CURSOR_MAX_LENGTH
        ):
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            encoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            body, supplied_signature = encoded.rsplit(b".", 1)
            payload = json.loads(body)
        except (UnicodeEncodeError, ValueError, json.JSONDecodeError) as exc:
            raise ExactSearchCursorMalformed("exact search cursor is malformed") from exc
        if not isinstance(payload, dict) or not isinstance(supplied_signature, bytes):
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        kid = payload.get("kid")
        if not isinstance(kid, str):
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        key = next((item for item in self.cursor_keys if item.kid == kid), None)
        if key is None:
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        expected = (
            hmac.new(key.secret, EXACT_SEARCH_CURSOR_DOMAIN + b"\0" + body, hashlib.sha256)
            .hexdigest()
            .encode("ascii")
        )
        if not hmac.compare_digest(expected, supplied_signature):
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        if payload.get("v") != 1 or not isinstance(payload.get("exp"), int):
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        if payload["exp"] < int(datetime.now(UTC).timestamp()):
            raise ExactSearchCursorMalformed("exact search cursor is expired")
        if not isinstance(payload.get("revision"), int) or payload["revision"] < 0:
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        if not isinstance(payload.get("anchor"), str) or not payload["anchor"]:
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        for field in ("filter_digest", "taxonomy_revision_digest"):
            if not isinstance(payload.get(field), str) or not _SHA256_RE.fullmatch(payload[field]):
                raise ExactSearchCursorMalformed("exact search cursor is malformed")
        if payload.get("sort") not in {None, "asc", "desc"}:
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        return payload

    async def upsert_document(
        self,
        session: AsyncSession,
        *,
        document: Document,
        canonical: str,
        frontmatter: dict[str, Any],
        digest: str,
        document_version: int,
        rebuild: bool = False,
    ) -> None:
        if document.kind not in {"profile", "resume"}:
            return
        installed = await self._installed(session)
        if document.visibility != "public" or document.schema_version not in {1, 2}:
            if installed:
                await self.remove_document(session, document.id)
            return
        if not rebuild:
            await self.require_ready_for_write(session)
        state = await self._locked_write_state(session, installed=installed, rebuild=rebuild)
        if state is None:
            return
        try:
            _, body = validate_canonical(document.kind, canonical)
            fields, compact_values = _snapshot_values(
                document.kind,
                document.public_identifier,
                frontmatter,
                body,
                digest,
                document_version,
                document.updated_at,
            )
            existing = await session.get(
                PublicExactSearchDocumentSnapshot, document.id, with_for_update=True
            )
            if existing is not None:
                same = all(getattr(existing, key) == value for key, value in fields.items())
                if same:
                    return
                await session.execute(
                    delete(PublicExactSearchCompactValue).where(
                        PublicExactSearchCompactValue.document_id == document.id
                    )
                )
                await session.delete(existing)
                await session.flush()
            vector: Any = fields["normalized_search_text"]
            if session.get_bind().dialect.name == "postgresql":
                vector = func.to_tsvector("simple", fields["normalized_search_text"])
            snapshot = PublicExactSearchDocumentSnapshot(
                document_id=document.id,
                **fields,
                search_vector=vector,
            )
            session.add(snapshot)
            await session.flush()
            session.add_all(
                PublicExactSearchCompactValue(
                    document_id=document.id,
                    field_name=field_name,
                    value=value,
                    source_ordinal=ordinal,
                )
                for field_name, value, ordinal in compact_values
            )
            state.revision += 1
            state.updated_at = datetime.now(UTC)
        except MarkdownSizeError:
            raise
        except ExactSearchUnavailable:
            raise
        except (KeyError, TypeError, ValueError, SQLAlchemyError) as exc:
            raise ExactSearchUnavailable("exact search projection could not be updated") from exc

    async def remove_document(
        self, session: AsyncSession, document_id: str, *, rebuild: bool = False
    ) -> None:
        installed = await self._installed(session)
        if not installed and not rebuild:
            return
        # Destructive withdrawal must remain possible while a rebuild or failed
        # projection is unavailable.  The state row is still locked; a missing
        # installed state remains fail-closed rather than silently drifting.
        state = await self._state(session, lock=True)
        if state is None:
            if installed or rebuild:
                raise ExactSearchUnavailable("exact search projection state is missing")
            return
        snapshot = await session.get(
            PublicExactSearchDocumentSnapshot, document_id, with_for_update=True
        )
        if snapshot is None:
            return
        await session.execute(
            delete(PublicExactSearchCompactValue).where(
                PublicExactSearchCompactValue.document_id == document_id
            )
        )
        await session.delete(snapshot)
        await session.flush()
        state.revision += 1
        state.updated_at = datetime.now(UTC)

    @staticmethod
    def _taxonomy_exists(
        snapshot: Any, taxonomy: str, field_name: str, values: list[str], *, external: bool = False
    ) -> Any:
        column = PublicTaxonomyTerm.external_id if external else PublicTaxonomyTerm.canonical_id
        return exists(
            select(1)
            .select_from(PublicTaxonomyMembership)
            .join(PublicTaxonomyTerm, PublicTaxonomyTerm.id == PublicTaxonomyMembership.term_id)
            .where(
                PublicTaxonomyMembership.document_id == snapshot.document_id,
                PublicTaxonomyMembership.field_name == field_name,
                PublicTaxonomyTerm.taxonomy == taxonomy,
                column.in_(values),
            )
        )

    @staticmethod
    def _agent_capability_exists(snapshot: Any) -> Any:
        agent_profile = aliased(Document)
        return exists(
            select(1)
            .select_from(AgentIdentity)
            .join(agent_profile, agent_profile.id == AgentIdentity.profile_document_id)
            .where(
                AgentIdentity.profile_document_id == snapshot.document_id,
                AgentIdentity.status == "active",
                agent_profile.kind == "profile",
                agent_profile.visibility == "public",
                agent_profile.owner_id == AgentIdentity.owner_id,
            )
        )

    async def _agent_eligibility_digest(self, session: AsyncSession) -> str:
        agent_profile = aliased(Document)
        statement = (
            select(
                AgentIdentity.id,
                AgentIdentity.handle,
                AgentIdentity.profile_document_id,
                AgentIdentity.owner_id,
            )
            .select_from(AgentIdentity)
            .join(agent_profile, agent_profile.id == AgentIdentity.profile_document_id)
            .where(
                AgentIdentity.status == "active",
                agent_profile.kind == "profile",
                agent_profile.visibility == "public",
                agent_profile.owner_id == AgentIdentity.owner_id,
            )
            .order_by(
                AgentIdentity.id,
                AgentIdentity.handle,
                AgentIdentity.profile_document_id,
                AgentIdentity.owner_id,
            )
        )
        try:
            rows = (await session.execute(statement)).all()
        except SQLAlchemyError as exc:
            raise ExactSearchUnavailable("agent eligibility state is unreadable") from exc
        payload = json.dumps(
            [tuple(row) for row in rows], separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hmac.new(
            self.cursor_keys[0].secret,
            EXACT_SEARCH_AGENT_ELIGIBILITY_DOMAIN + b"\0" + payload,
            hashlib.sha256,
        ).hexdigest()

    def _base_statement(
        self,
        *,
        arguments: dict[str, Any],
        resolved: Any,
        query: str,
    ) -> tuple[Any, Any]:
        snapshot = PublicExactSearchDocumentSnapshot
        document = Document
        version = DocumentVersion
        ts_query = func.websearch_to_tsquery("simple", query) if query else None
        rank = (
            func.ts_rank_cd(snapshot.search_vector, ts_query, 32)
            if ts_query is not None
            else literal(0.0)
        )
        statement = (
            select(snapshot, document, version, rank.label("rank"))
            .join(document, document.id == snapshot.document_id)
            .join(
                version,
                and_(
                    version.document_id == document.id,
                    version.version == document.current_version,
                    version.sha256 == snapshot.source_sha256,
                ),
            )
            .where(
                document.visibility == "public",
                document.kind.in_(("profile", "resume")),
                document.current_version == snapshot.document_version,
            )
        )
        if query:
            statement = statement.where(snapshot.search_vector.op("@@")(ts_query))
        kind = arguments.get("kind")
        if kind is not None:
            statement = statement.where(document.kind == kind, snapshot.kind == kind)
        skills = [str(value) for value in arguments.get("skills") or []]
        for skill in dict.fromkeys(skills):
            statement = statement.where(
                exists(
                    select(1).where(
                        PublicExactSearchCompactValue.document_id == snapshot.document_id,
                        PublicExactSearchCompactValue.field_name == "skill",
                        PublicExactSearchCompactValue.value == skill,
                    )
                )
            )
        location = arguments.get("location")
        if location is not None:
            statement = statement.where(
                exists(
                    select(1).where(
                        PublicExactSearchCompactValue.document_id == snapshot.document_id,
                        PublicExactSearchCompactValue.field_name == "location",
                        PublicExactSearchCompactValue.value == location,
                    )
                )
            )
        scalar_filters = {
            "availability_status": snapshot.availability_status,
            "representation_status": snapshot.representation_status,
            "contact_disclosure": snapshot.contact_disclosure,
        }
        for field, scalar_column in scalar_filters.items():
            value = arguments.get(field)
            if value is not None:
                statement = statement.where(scalar_column == value)
        for field, membership_column in (
            ("location_country_code", PublicTaxonomyMembership.location_country_code),
            ("location_region", PublicTaxonomyMembership.location_region),
            ("location_city", PublicTaxonomyMembership.location_city),
        ):
            value = arguments.get(field)
            if value is not None:
                statement = statement.where(
                    exists(
                        select(1).where(
                            PublicTaxonomyMembership.document_id == snapshot.document_id,
                            PublicTaxonomyMembership.field_name == "location",
                            membership_column == value,
                        )
                    )
                )
        availability_from = arguments.get("availability_from")
        if availability_from is not None:
            parsed_availability = _parse_time(availability_from)
            if parsed_availability is not None:
                statement = statement.where(
                    snapshot.availability_from >= parsed_availability.date().isoformat()
                )
        for field, updated_column in (
            ("updated_after", snapshot.updated_at),
            ("updated_before", snapshot.updated_at),
        ):
            value = arguments.get(field)
            if value is None:
                continue
            parsed = _parse_time(value)
            if parsed is None:
                continue
            statement = statement.where(
                updated_column >= parsed if field == "updated_after" else updated_column <= parsed
            )
        canonical = resolved.canonical
        typed = (
            ("occupation_ids", "occupation", "occupations", False, "all"),
            ("industry_ids", "industry", "industries", False, "all"),
            ("skill_ids", "skill", "skills", False, "all"),
            ("language_ids", "language", "languages", False, "all"),
            ("open_to_ids", "open_to", "open_to", False, "all"),
            ("organization_ids", "organization", "organizations", False, "all"),
            ("representative_ids", "representative", "public_representation", False, "any"),
            ("seniority_ids", "seniority", "seniority", False, "any"),
            ("location_id", "location", "location", False, "all"),
            ("work_modes", "work_mode", "work_modes", True, "all"),
        )
        for field, taxonomy, field_name, external, semantics in typed:
            raw_values = canonical.get(field)
            if field == "location_id" and raw_values:
                raw_values = [raw_values]
            values = [str(value) for value in raw_values or []]
            if not values:
                continue
            if semantics == "any":
                statement = statement.where(
                    exists(
                        select(1)
                        .select_from(PublicTaxonomyMembership)
                        .join(
                            PublicTaxonomyTerm,
                            PublicTaxonomyTerm.id == PublicTaxonomyMembership.term_id,
                        )
                        .where(
                            PublicTaxonomyMembership.document_id == snapshot.document_id,
                            PublicTaxonomyMembership.field_name == field_name,
                            PublicTaxonomyTerm.taxonomy == taxonomy,
                            (
                                PublicTaxonomyTerm.external_id.in_(values)
                                if external
                                else PublicTaxonomyTerm.canonical_id.in_(values)
                            ),
                        )
                    )
                )
            else:
                for value in dict.fromkeys(values):
                    statement = statement.where(
                        self._taxonomy_exists(
                            snapshot, taxonomy, field_name, [value], external=external
                        )
                    )
        if arguments.get("agent_capability") == "internal_contact_request":
            statement = statement.where(self._agent_capability_exists(snapshot))
        return statement, rank

    async def _rows_to_hits(
        self, session: AsyncSession, rows: Sequence[Any]
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        document_ids = [row[0].document_id for row in rows]
        compact_rows = (
            await session.scalars(
                select(PublicExactSearchCompactValue).where(
                    PublicExactSearchCompactValue.document_id.in_(document_ids)
                )
            )
        ).all()
        skills_by_document: dict[str, list[str]] = {}
        for compact in compact_rows:
            if compact.field_name == "skill":
                skills_by_document.setdefault(compact.document_id, []).append(compact.value)
        hits: list[dict[str, Any]] = []
        for row in rows:
            snapshot = row[0]
            document = row[1]
            version = row[2]
            if hashlib.sha256(snapshot.normalized_search_text.encode("utf-8")).hexdigest() != (
                snapshot.search_sha256
            ):
                raise ExactSearchUnavailable("exact search projection integrity failed")
            if (
                document.visibility != "public"
                or document.current_version != snapshot.document_version
                or version.version != document.current_version
                or version.sha256 != snapshot.source_sha256
            ):
                raise ExactSearchUnavailable("exact search projection is stale")
            hits.append(
                {
                    "id": document.id,
                    "kind": snapshot.kind,
                    "identifier": snapshot.identifier,
                    "name": snapshot.name,
                    "headline": snapshot.headline,
                    "title": snapshot.title,
                    "location": snapshot.location,
                    "skills": skills_by_document.get(document.id, []),
                    "updated_at": snapshot.updated_at,
                    "version": snapshot.document_version,
                    "schema_version": snapshot.schema_version,
                    "excerpt": snapshot.headline[:240] or None,
                    "html_url": (
                        f"/p/{snapshot.identifier}"
                        if snapshot.kind == "profile"
                        else f"/r/{snapshot.identifier}"
                    ),
                    "markdown_url": (
                        f"/v1/profiles/{snapshot.identifier}.md"
                        if snapshot.kind == "profile"
                        else f"/v1/resumes/{snapshot.identifier}.md"
                    ),
                }
            )
        return hits

    async def search(
        self,
        session: AsyncSession,
        *,
        arguments: dict[str, Any],
        resolved: Any,
    ) -> ExactSearchResult:
        state = await self.require_ready(session, require_postgresql=True)
        initial_revision = int(state.revision)
        initial_status = str(state.status)
        initial_contract_digest = str(state.contract_digest)
        if int(arguments.get("offset", 0)) != 0:
            raise ValueError("exact search requires offset 0")
        cursor = arguments.get("cursor")
        sort_updated = arguments.get("sort_updated")
        if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
            raise ExactSearchCursorMalformed("exact search cursor is malformed")
        query_value = arguments.get("q", "")
        if not isinstance(query_value, str):
            raise ValueError("search query is invalid")
        query = _normalize_search_text(query_value)
        updated_after = _parse_time(arguments.get("updated_after"))
        updated_before = _parse_time(arguments.get("updated_before"))
        if (
            updated_after is not None
            and updated_before is not None
            and updated_after > updated_before
        ):
            raise ValueError("search time bounds are invalid")
        taxonomy_digest = await self._taxonomy_revision_digest(session)
        agent_eligibility_digest = None
        if arguments.get("agent_capability") == "internal_contact_request":
            agent_eligibility_digest = await self._agent_eligibility_digest(session)
        filter_digest = self._filter_digest(arguments, resolved)
        cursor_payload: dict[str, Any] | None = None
        if cursor is not None:
            cursor_payload = self._decode_cursor(cursor)
            if (
                cursor_payload["revision"] != initial_revision
                or cursor_payload["taxonomy_revision_digest"] != taxonomy_digest
            ):
                raise ExactSearchCursorStale("exact search cursor is stale")
            if agent_eligibility_digest is not None:
                supplied_agent_digest = cursor_payload.get("agent_eligibility_digest")
                if not isinstance(supplied_agent_digest, str) or not _SHA256_RE.fullmatch(
                    supplied_agent_digest
                ):
                    raise ExactSearchCursorMalformed("exact search cursor is malformed")
                if supplied_agent_digest != agent_eligibility_digest:
                    raise ExactSearchCursorStale("exact search cursor is stale")
            if cursor_payload["filter_digest"] != filter_digest or cursor_payload["sort"] != (
                sort_updated
            ):
                raise ExactSearchCursorMalformed("exact search cursor does not match the query")
        limit = int(arguments.get("limit", 20))
        statement, rank = self._base_statement(arguments=arguments, resolved=resolved, query=query)
        base_statement = statement
        if sort_updated == "asc":
            full_statement = base_statement.order_by(
                PublicExactSearchDocumentSnapshot.updated_at.asc(),
                PublicExactSearchDocumentSnapshot.document_id.asc(),
            )
        elif sort_updated == "desc":
            full_statement = base_statement.order_by(
                PublicExactSearchDocumentSnapshot.updated_at.desc(),
                PublicExactSearchDocumentSnapshot.document_id.desc(),
            )
        else:
            full_statement = base_statement.order_by(
                rank.desc(),
                PublicExactSearchDocumentSnapshot.updated_at.desc(),
                PublicExactSearchDocumentSnapshot.document_id.desc(),
            )
        try:
            full_rows = (
                await session.execute(full_statement.limit(EXACT_SEARCH_MATERIALIZATION_LIMIT))
            ).all()
        except SQLAlchemyError as exc:
            raise ExactSearchUnavailable("exact PostgreSQL search is unavailable") from exc
        if len(full_rows) > EXACT_SEARCH_MAX_DOCUMENTS:
            raise ExactSearchTooBroad(EXACT_SEARCH_TOO_BROAD_MESSAGE)
        if cursor_payload is not None:
            candidate = statement.subquery("exact_candidates")
            anchor_rank = (
                select(candidate.c.rank)
                .where(candidate.c.document_id == cursor_payload["anchor"])
                .scalar_subquery()
            )
            anchor_updated_at = (
                select(candidate.c.updated_at)
                .where(candidate.c.document_id == cursor_payload["anchor"])
                .scalar_subquery()
            )
            try:
                anchor_exists = await session.scalar(
                    select(candidate.c.document_id)
                    .where(candidate.c.document_id == cursor_payload["anchor"])
                    .limit(1)
                )
            except SQLAlchemyError as exc:
                raise ExactSearchUnavailable("exact search anchor is unavailable") from exc
            if anchor_exists is None:
                raise ExactSearchUnavailable("exact search anchor is unavailable")
            if sort_updated == "asc":
                continuation = (
                    PublicExactSearchDocumentSnapshot.updated_at > anchor_updated_at
                ) | (
                    (PublicExactSearchDocumentSnapshot.updated_at == anchor_updated_at)
                    & (PublicExactSearchDocumentSnapshot.document_id > cursor_payload["anchor"])
                )
            elif sort_updated == "desc":
                continuation = (
                    PublicExactSearchDocumentSnapshot.updated_at < anchor_updated_at
                ) | (
                    (PublicExactSearchDocumentSnapshot.updated_at == anchor_updated_at)
                    & (PublicExactSearchDocumentSnapshot.document_id < cursor_payload["anchor"])
                )
            else:
                continuation = (
                    (rank < anchor_rank)
                    | (
                        (rank == anchor_rank)
                        & (PublicExactSearchDocumentSnapshot.updated_at < anchor_updated_at)
                    )
                    | (
                        (rank == anchor_rank)
                        & (PublicExactSearchDocumentSnapshot.updated_at == anchor_updated_at)
                        & (PublicExactSearchDocumentSnapshot.document_id < cursor_payload["anchor"])
                    )
                )
            statement = statement.where(continuation)
        if sort_updated == "asc":
            statement = statement.order_by(
                PublicExactSearchDocumentSnapshot.updated_at.asc(),
                PublicExactSearchDocumentSnapshot.document_id.asc(),
            )
        elif sort_updated == "desc":
            statement = statement.order_by(
                PublicExactSearchDocumentSnapshot.updated_at.desc(),
                PublicExactSearchDocumentSnapshot.document_id.desc(),
            )
        else:
            statement = statement.order_by(
                rank.desc(),
                PublicExactSearchDocumentSnapshot.updated_at.desc(),
                PublicExactSearchDocumentSnapshot.document_id.desc(),
            )
        if cursor_payload is None:
            rows = full_rows
        else:
            try:
                rows = (await session.execute(statement.limit(limit + 1))).all()
            except SQLAlchemyError as exc:
                raise ExactSearchUnavailable("exact PostgreSQL search is unavailable") from exc
        facet_hits = await self._rows_to_hits(session, full_rows)
        hits = facet_hits if cursor_payload is None else await self._rows_to_hits(session, rows)
        next_cursor = None
        if len(hits) > limit and hits:
            last = hits[limit - 1]
            cursor_values: dict[str, Any] = {
                "v": 1,
                "kid": self.cursor_keys[0].kid,
                "exp": int(datetime.now(UTC).timestamp()) + self.cursor_ttl_seconds,
                "revision": initial_revision,
                "taxonomy_revision_digest": taxonomy_digest,
                "filter_digest": filter_digest,
                "sort": sort_updated,
                "anchor": last["id"],
            }
            if agent_eligibility_digest is not None:
                cursor_values["agent_eligibility_digest"] = agent_eligibility_digest
            next_cursor = self._encode_cursor(cursor_values)
        final_taxonomy_digest = await self._taxonomy_revision_digest(session)
        if final_taxonomy_digest != taxonomy_digest:
            raise ExactSearchCursorStale("exact search cursor is stale")
        if agent_eligibility_digest is not None:
            if await self._agent_eligibility_digest(session) != agent_eligibility_digest:
                raise ExactSearchCursorStale("exact search cursor is stale")
        current_state = await self._state_values(session)
        if current_state is None:
            raise ExactSearchCursorStale("exact search cursor is stale")
        current_revision, current_status, current_contract_digest = current_state
        if (
            initial_status != "ready"
            or initial_contract_digest != EXACT_SEARCH_CONTRACT_DIGEST
            or current_status != "ready"
            or current_contract_digest != initial_contract_digest
            or current_revision != initial_revision
        ):
            raise ExactSearchCursorStale("exact search cursor is stale")
        return ExactSearchResult(
            hits=hits,
            facet_hits=facet_hits,
            total=len(full_rows),
            next_cursor=next_cursor,
            revision=initial_revision,
        )

    async def backfill(
        self, session: AsyncSession, store: VersionStore, *, if_required: bool = False
    ) -> dict[str, Any]:
        if not await self._installed(session):
            raise ExactSearchUnavailable("exact search migration is incomplete")
        state = await self._state(session, lock=True)
        if state is None:
            raise ExactSearchUnavailable("exact search projection state is missing")
        if (
            if_required
            and state.status == "ready"
            and state.contract_digest == EXACT_SEARCH_CONTRACT_DIGEST
        ):
            try:
                await self.verify_integrity(session, store, require_ready=True)
                return {"status": "ready", "backfilled": 0, "reused": True}
            except ExactSearchUnavailable:
                await session.rollback()
                state = await self._state(session, lock=True)
                if state is None:
                    raise
        state.status = "building"
        state.last_error_code = None
        state.updated_at = datetime.now(UTC)
        await session.commit()
        try:
            async with session.begin():
                state = await self._state(session, lock=True)
                if state is None:
                    raise ExactSearchUnavailable("exact search projection state is missing")
                await session.execute(delete(PublicExactSearchCompactValue))
                await session.execute(delete(PublicExactSearchDocumentSnapshot))
                documents = (
                    await session.scalars(
                        select(Document)
                        .where(
                            Document.visibility == "public",
                            Document.kind.in_(("profile", "resume")),
                        )
                        .options(selectinload(Document.versions))
                        .order_by(Document.id)
                    )
                ).all()
                count = 0
                for document in documents:
                    current = next(
                        (
                            item
                            for item in document.versions
                            if item.version == document.current_version
                        ),
                        None,
                    )
                    if current is None:
                        raise ExactSearchUnavailable("public document current version is missing")
                    canonical = store.read_verified(current.storage_path, current.sha256)
                    require_canonical_document_size(canonical)
                    frontmatter, _ = validate_canonical(document.kind, canonical)
                    document.schema_version = int(frontmatter["schema_version"])
                    await self.upsert_document(
                        session,
                        document=document,
                        canonical=canonical,
                        frontmatter=frontmatter,
                        digest=current.sha256,
                        document_version=current.version,
                        rebuild=True,
                    )
                    count += 1
                state.status = "ready"
                state.contract_digest = EXACT_SEARCH_CONTRACT_DIGEST
                state.revision += 1
                state.updated_at = datetime.now(UTC)
                await session.flush()
            return {"status": "ready", "backfilled": count, "reused": False}
        except Exception as exc:
            await session.rollback()
            state = await self._state(session, lock=True)
            if state is not None:
                state.status = "failed"
                state.last_error_code = "backfill_failed"
                state.updated_at = datetime.now(UTC)
                await session.commit()
            if isinstance(exc, ExactSearchUnavailable):
                raise
            raise ExactSearchUnavailable("exact search backfill failed") from exc

    async def verify_integrity(
        self,
        session: AsyncSession,
        store: VersionStore,
        *,
        require_ready: bool,
    ) -> None:
        state = await self.require_ready(session) if require_ready else await self._state(session)
        if state is None or state.contract_digest != EXACT_SEARCH_CONTRACT_DIGEST:
            raise ExactSearchUnavailable("exact search projection contract is invalid")
        invalid_schema_versions = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.visibility == "public",
                Document.kind.in_(("profile", "resume")),
                or_(
                    Document.schema_version.not_in((1, 2)),
                    Document.schema_version.is_(None),
                ),
            )
        )
        if int(invalid_schema_versions or 0) != 0:
            raise ExactSearchUnavailable("public document schema version is invalid")
        if session.get_bind().dialect.name == "postgresql":
            invalid_vectors = await session.scalar(
                select(func.count())
                .select_from(PublicExactSearchDocumentSnapshot)
                .where(
                    or_(
                        PublicExactSearchDocumentSnapshot.search_vector.is_(None),
                        PublicExactSearchDocumentSnapshot.search_vector
                        != func.to_tsvector(
                            "simple", PublicExactSearchDocumentSnapshot.normalized_search_text
                        ),
                    )
                )
            )
            if int(invalid_vectors or 0) != 0:
                raise ExactSearchUnavailable("exact search vector integrity failed")
        documents = (
            await session.scalars(
                select(Document)
                .where(
                    Document.visibility == "public",
                    Document.kind.in_(("profile", "resume")),
                    Document.schema_version.in_((1, 2)),
                )
                .options(selectinload(Document.versions))
            )
        ).all()
        snapshots = {
            row.document_id: row
            for row in (await session.scalars(select(PublicExactSearchDocumentSnapshot))).all()
        }
        if len(snapshots) != len(documents):
            raise ExactSearchUnavailable("exact search snapshot coverage is invalid")
        for document in documents:
            snapshot = snapshots.get(document.id)
            current = next(
                (item for item in document.versions if item.version == document.current_version),
                None,
            )
            if snapshot is None or current is None:
                raise ExactSearchUnavailable("exact search snapshot coverage is invalid")
            canonical = store.read_verified(current.storage_path, current.sha256)
            require_canonical_document_size(canonical)
            frontmatter, body = validate_canonical(document.kind, canonical)
            fields, compact = _snapshot_values(
                document.kind,
                document.public_identifier,
                frontmatter,
                body,
                current.sha256,
                current.version,
                document.updated_at,
            )
            if any(getattr(snapshot, key) != value for key, value in fields.items()):
                raise ExactSearchUnavailable("exact search snapshot integrity failed")
            actual_compact = {
                (row.field_name, row.value, row.source_ordinal)
                for row in (
                    await session.scalars(
                        select(PublicExactSearchCompactValue).where(
                            PublicExactSearchCompactValue.document_id == document.id
                        )
                    )
                ).all()
            }
            if actual_compact != set(compact):
                raise ExactSearchUnavailable("exact search compact values are invalid")
        private_snapshots = await session.scalar(
            select(func.count())
            .select_from(PublicExactSearchDocumentSnapshot)
            .join(Document, Document.id == PublicExactSearchDocumentSnapshot.document_id)
            .where(Document.visibility != "public")
        )
        if int(private_snapshots or 0) != 0:
            raise ExactSearchUnavailable("private exact search snapshot exists")
