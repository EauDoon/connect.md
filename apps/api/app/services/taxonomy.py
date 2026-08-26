"""Authoritative public taxonomy projection and search resolver.

The projection is deliberately derived from current public v2 Markdown rows in
PostgreSQL.  Meilisearch may narrow the candidate set, but it never supplies
taxonomy authority, labels, or facet values.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import and_, delete, exists, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.markdown import validate_canonical
from app.models import (
    Document,
    DocumentVersion,
    PublicTaxonomyDocumentSnapshot,
    PublicTaxonomyMembership,
    PublicTaxonomyProjectionState,
    PublicTaxonomyTerm,
)

REFERENCE_TAXONOMIES = (
    "occupation",
    "industry",
    "location",
    "skill",
    "language",
    "seniority",
    "open_to",
    "organization",
    "representative",
)
TAXONOMY_TYPES = (*REFERENCE_TAXONOMIES, "work_mode")
TaxonomyName = Literal[
    "occupation",
    "industry",
    "location",
    "skill",
    "language",
    "seniority",
    "open_to",
    "organization",
    "representative",
    "work_mode",
]

TAXONOMY_CONTRACT_VERSION = 1
MAX_SEARCH_REPEATED_VALUES = 50
TAXONOMY_CONTRACT_DIGEST = hashlib.sha256(
    b"connect.md:public-taxonomy:v1:current-public-v2"
).hexdigest()
WORK_MODE_SCHEME = "connect.md"
WORK_MODE_LABELS = {
    "on_site": "On-site",
    "hybrid": "Hybrid",
    "remote": "Remote",
}

_FILTER_VALUE_RE = re.compile(r"^tx1_[0-9a-f]{64}$")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9._-]{0,79}$")
_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,253}[A-Za-z0-9])?$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

TAXONOMY_FIELD_MAP: dict[str, tuple[str, str]] = {
    "occupation": ("occupation_ids", "occupations"),
    "industry": ("industry_ids", "industries"),
    "skill": ("skill_ids", "skills"),
    "language": ("language_ids", "languages"),
    "open_to": ("open_to_ids", "open_to"),
    "organization": ("organization_ids", "organizations"),
    "representative": ("representative_ids", "representative"),
    "location": ("location_id", "location"),
    "seniority": ("seniority_ids", "seniority"),
    "work_mode": ("work_modes", "work_modes"),
}


class TaxonomyProjectionError(RuntimeError):
    """A projection invariant failed and the enclosing canonical write must roll back."""


class TaxonomyUnavailable(RuntimeError):
    """The installed projection is missing, not ready, or corrupt."""


class TaxonomyUnknown(ValueError):
    pass


class TaxonomyInvalidValue(ValueError):
    pass


class TaxonomyCursorMalformed(ValueError):
    pass


class TaxonomyCursorStale(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedSearchFilters:
    """Canonical search values after PostgreSQL taxonomy resolution."""

    meili: dict[str, Any]
    canonical: dict[str, Any]
    filter_values: dict[str, Any]
    requested: dict[str, Any]
    installed: bool
    empty: bool = False


def ensure_search_repeated_value_cap(arguments: dict[str, Any]) -> None:
    """Reject oversized submitted repeated values before any database/backend work."""
    repeated_values = sum(len(value) for value in arguments.values() if isinstance(value, list))
    # The legacy singular seniority alias is merged into the OR set by the
    # resolver, so it consumes one semantic value before that merge.  The
    # location selector remains a true singleton and is intentionally not
    # counted as a repeated value.
    if arguments.get("seniority_id") is not None:
        repeated_values += 1
    if repeated_values > MAX_SEARCH_REPEATED_VALUES:
        raise TaxonomyInvalidValue("search contains too many repeated taxonomy values")


def taxonomy_filter_value(taxonomy: str, scheme: str, external_id: str) -> str:
    """Return the stable, typed transport alias required by the public contract."""
    preimage = f"connect.md:taxonomy-filter:v1\0{taxonomy}\0{scheme}\0{external_id}"
    return "tx1_" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def canonical_id(scheme: str, external_id: str) -> str:
    return f"{scheme}:{external_id}"


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _value_is_well_formed(value: str, *, max_length: int) -> bool:
    if len(value) > max_length or not value or _CONTROL_RE.search(value):
        return False
    if ":" not in value:
        return False
    scheme, external_id = value.split(":", 1)
    return bool(_SCHEME_RE.fullmatch(scheme) and _EXTERNAL_ID_RE.fullmatch(external_id))


def _cursor_encode(payload: dict[str, Any], secret: bytes) -> str:
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signature = (
        base64.urlsafe_b64encode(hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return f"{encoded}.{signature}"


def _cursor_decode(cursor: str, secret: bytes) -> dict[str, Any]:
    if not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048:
        raise TaxonomyCursorMalformed("taxonomy cursor is malformed")
    try:
        encoded, supplied = cursor.rsplit(".", 1)
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode((encoded + padding).encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise TaxonomyCursorMalformed("taxonomy cursor is malformed") from exc
    if not isinstance(payload, dict):
        raise TaxonomyCursorMalformed("taxonomy cursor is malformed")
    return payload


def _reference_entries(frontmatter: dict[str, Any]) -> list[dict[str, Any]]:
    if frontmatter.get("schema_version") != 2:
        return []
    entries: list[dict[str, Any]] = []

    def add_reference(
        taxonomy: str,
        field_name: str,
        reference: dict[str, Any],
        source_ordinal: int,
    ) -> None:
        entries.append(
            {
                "taxonomy": taxonomy,
                "scheme": str(reference["scheme"]),
                "external_id": str(reference["id"]),
                "label": str(reference["label"]),
                "vocabulary_version": reference.get("version"),
                "field_name": field_name,
                "source_ordinal": source_ordinal,
                "language_proficiency": reference.get("proficiency"),
                "organization_relationship": reference.get("relationship"),
                "location_country_code": reference.get("country_code"),
                "location_region": reference.get("region"),
                "location_city": reference.get("city"),
            }
        )

    for ordinal, reference in enumerate(frontmatter["occupations"]):
        add_reference("occupation", "occupation_ids", reference, ordinal)
    for ordinal, reference in enumerate(frontmatter["industries"]):
        add_reference("industry", "industry_ids", reference, ordinal)
    add_reference("location", "location_id", frontmatter["location"], 0)
    for ordinal, reference in enumerate(frontmatter["skills"]):
        add_reference("skill", "skill_ids", reference, ordinal)
    for ordinal, reference in enumerate(frontmatter["languages"]):
        add_reference("language", "language_ids", reference, ordinal)
    add_reference("seniority", "seniority_ids", frontmatter["seniority"], 0)
    for ordinal, reference in enumerate(frontmatter["open_to"]):
        add_reference("open_to", "open_to_ids", reference, ordinal)
    for ordinal, reference in enumerate(frontmatter["organizations"]):
        add_reference("organization", "organization_ids", reference, ordinal)
    representative = frontmatter["public_representation"].get("representative")
    if representative is not None:
        add_reference("representative", "representative_ids", representative, 0)
    for ordinal, value in enumerate(frontmatter["work_modes"]):
        if value not in WORK_MODE_LABELS:
            raise TaxonomyProjectionError("canonical work mode is outside the schema contract")
        entries.append(
            {
                "taxonomy": "work_mode",
                "scheme": WORK_MODE_SCHEME,
                "external_id": value,
                "label": WORK_MODE_LABELS[value],
                "vocabulary_version": None,
                "field_name": "work_modes",
                "source_ordinal": ordinal,
                "language_proficiency": None,
                "organization_relationship": None,
                "location_country_code": None,
                "location_region": None,
                "location_city": None,
            }
        )
    return entries


def _is_missing_table_error(exc: SQLAlchemyError, table: str) -> bool:
    original = getattr(exc, "orig", exc)
    if getattr(original, "pgcode", None) == "42P01":
        return table in str(original).lower()
    message = str(original).lower()
    return table in message and ("no such table" in message or "does not exist" in message)


async def _projection_installed(session: AsyncSession) -> bool:
    # Probe Alembic first in a savepoint. A known pre-0022 marker is legacy;
    # 0022 and every unknown/future marker are installed and must fail closed
    # if their state is missing or corrupt.
    marker_status = "missing"
    markers: list[str] = []
    try:
        async with session.begin_nested():
            result = await session.scalars(text("SELECT version_num FROM alembic_version"))
            markers = [str(value) for value in result.all()]
        if not markers:
            marker_status = "installed"
        elif all(
            (match := re.match(r"^(\d+)", marker)) is not None and int(match.group(1)) <= 21
            for marker in markers
        ):
            marker_status = "legacy"
        else:
            marker_status = "installed"
    except SQLAlchemyError as exc:
        if not _is_missing_table_error(exc, "alembic_version"):
            return True

    if marker_status == "installed":
        return True

    # A legacy/test database may have the ORM tables without Alembic state.
    # Missing/corrupt projection tables do not get treated as legacy.
    try:
        async with session.begin_nested():
            state_count = await session.scalar(
                select(func.count()).select_from(PublicTaxonomyProjectionState)
            )
    except SQLAlchemyError as exc:
        if _is_missing_table_error(exc, "public_taxonomy_projection_state"):
            return False
        return True
    return bool(state_count)


async def _states(
    session: AsyncSession, taxonomies: Iterable[str], *, lock: bool = False
) -> dict[str, PublicTaxonomyProjectionState]:
    names = list(dict.fromkeys(taxonomies))
    if not names:
        return {}
    statement = select(PublicTaxonomyProjectionState).where(
        PublicTaxonomyProjectionState.taxonomy.in_(names)
    )
    if lock:
        statement = statement.with_for_update()
    try:
        rows = (await session.scalars(statement)).all()
    except SQLAlchemyError as exc:
        raise TaxonomyUnavailable("public taxonomy projection state is unreadable") from exc
    return {row.taxonomy: row for row in rows}


async def _require_ready(
    session: AsyncSession, taxonomies: Iterable[str]
) -> dict[str, PublicTaxonomyProjectionState]:
    states = await _states(session, taxonomies)
    names = list(dict.fromkeys(taxonomies))
    if len(states) != len(names) or any(
        states[name].status != "ready" or states[name].contract_digest != TAXONOMY_CONTRACT_DIGEST
        for name in names
    ):
        raise TaxonomyUnavailable("public taxonomy projection is not ready")
    return states


async def _taxonomy_digest(session: AsyncSession, taxonomy: str) -> str:
    rows = await session.execute(
        select(PublicTaxonomyTerm, PublicTaxonomyMembership)
        .outerjoin(
            PublicTaxonomyMembership,
            PublicTaxonomyMembership.term_id == PublicTaxonomyTerm.id,
        )
        .where(PublicTaxonomyTerm.taxonomy == taxonomy)
        .order_by(
            PublicTaxonomyTerm.canonical_id,
            PublicTaxonomyMembership.document_id,
            PublicTaxonomyMembership.field_name,
            PublicTaxonomyMembership.source_ordinal,
            PublicTaxonomyMembership.id,
        )
    )
    values: list[dict[str, Any]] = []
    for term, membership in rows.all():
        values.append(
            {
                "term": (
                    term.taxonomy,
                    term.scheme,
                    term.external_id,
                    term.canonical_id,
                    term.filter_value,
                    term.label,
                    term.label_conflict,
                    term.vocabulary_version,
                    term.version_conflict,
                ),
                "membership": None
                if membership is None
                else (
                    membership.document_id,
                    membership.field_name,
                    membership.source_ordinal,
                    membership.label_assertion,
                    membership.vocabulary_version,
                    membership.language_proficiency,
                    membership.organization_relationship,
                    membership.location_country_code,
                    membership.location_region,
                    membership.location_city,
                ),
            }
        )
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


TermAggregate = tuple[str, str | None, bool, str | None, bool]


async def _term_aggregate_states(
    session: AsyncSession, term_ids: Iterable[str]
) -> dict[str, TermAggregate]:
    ids = list(dict.fromkeys(term_ids))
    if not ids:
        return {}
    rows = await session.execute(
        select(PublicTaxonomyTerm, PublicTaxonomyMembership)
        .outerjoin(
            PublicTaxonomyMembership,
            PublicTaxonomyMembership.term_id == PublicTaxonomyTerm.id,
        )
        .where(PublicTaxonomyTerm.id.in_(ids))
        .order_by(
            PublicTaxonomyTerm.id,
            PublicTaxonomyMembership.source_ordinal,
            PublicTaxonomyMembership.id,
        )
    )
    grouped: dict[str, tuple[PublicTaxonomyTerm, list[PublicTaxonomyMembership]]] = {}
    for term, membership in rows.all():
        if term.id not in grouped:
            grouped[term.id] = (term, [])
        if membership is not None:
            grouped[term.id][1].append(membership)
    result: dict[str, TermAggregate] = {}
    for term_id, (term, memberships) in grouped.items():
        if not memberships:
            continue
        labels = {membership.label_assertion for membership in memberships}
        versions = [membership.vocabulary_version for membership in memberships]
        non_null_versions = {version for version in versions if version is not None}
        if not non_null_versions:
            vocabulary_version = None
            version_conflict = False
        elif len(non_null_versions) == 1 and len(non_null_versions) == len(versions):
            vocabulary_version = next(iter(non_null_versions))
            version_conflict = False
        else:
            vocabulary_version = None
            version_conflict = True
        result[term_id] = (
            term.taxonomy,
            next(iter(labels)) if len(labels) == 1 else None,
            len(labels) != 1,
            vocabulary_version,
            version_conflict,
        )
    return result


async def _recalculate_terms(session: AsyncSession, term_ids: Iterable[str]) -> None:
    ids = list(dict.fromkeys(term_ids))
    if not ids:
        return
    terms = (
        await session.scalars(select(PublicTaxonomyTerm).where(PublicTaxonomyTerm.id.in_(ids)))
    ).all()
    memberships = (
        await session.scalars(
            select(PublicTaxonomyMembership)
            .where(PublicTaxonomyMembership.term_id.in_(ids))
            .order_by(
                PublicTaxonomyMembership.term_id,
                PublicTaxonomyMembership.source_ordinal,
                PublicTaxonomyMembership.id,
            )
        )
    ).all()
    by_term: dict[str, list[PublicTaxonomyMembership]] = {}
    for membership in memberships:
        by_term.setdefault(membership.term_id, []).append(membership)
    for term in terms:
        rows = by_term.get(term.id, [])
        if not rows:
            await session.delete(term)
            continue
        labels = {row.label_assertion for row in rows}
        term.label_conflict = len(labels) != 1
        term.label = rows[0].label_assertion if len(labels) == 1 else None
        versions = [row.vocabulary_version for row in rows]
        non_null_versions = {version for version in versions if version is not None}
        if not non_null_versions:
            term.vocabulary_version = None
            term.version_conflict = False
        elif len(non_null_versions) == 1 and len(non_null_versions) == len(versions):
            term.vocabulary_version = next(iter(non_null_versions))
            term.version_conflict = False
        else:
            term.vocabulary_version = None
            term.version_conflict = True
    await session.flush()


async def _get_or_create_term(session: AsyncSession, entry: dict[str, Any]) -> PublicTaxonomyTerm:
    taxonomy = str(entry["taxonomy"])
    scheme = str(entry["scheme"])
    external_id = str(entry["external_id"])
    expected_filter_value = taxonomy_filter_value(taxonomy, scheme, external_id)
    row = await session.scalar(
        select(PublicTaxonomyTerm)
        .where(
            PublicTaxonomyTerm.taxonomy == taxonomy,
            PublicTaxonomyTerm.scheme == scheme,
            PublicTaxonomyTerm.external_id == external_id,
        )
        .with_for_update()
    )
    if row is None:
        candidate = PublicTaxonomyTerm(
            taxonomy=taxonomy,
            scheme=scheme,
            external_id=external_id,
            canonical_id=canonical_id(scheme, external_id),
            filter_value=expected_filter_value,
            label=None,
            label_conflict=False,
            vocabulary_version=None,
            version_conflict=False,
        )
        try:
            # The caller locks the taxonomy state row before entering this
            # function.  The savepoint keeps a legitimate concurrent insert
            # from poisoning the enclosing canonical-write transaction.
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            row = candidate
        except IntegrityError:
            row = await session.scalar(
                select(PublicTaxonomyTerm).where(
                    PublicTaxonomyTerm.taxonomy == taxonomy,
                    PublicTaxonomyTerm.scheme == scheme,
                    PublicTaxonomyTerm.external_id == external_id,
                )
            )
            if row is None:
                collision = await session.scalar(
                    select(PublicTaxonomyTerm).where(
                        PublicTaxonomyTerm.filter_value == expected_filter_value
                    )
                )
                if collision is not None:
                    raise TaxonomyProjectionError("taxonomy filter identity collision") from None
                raise TaxonomyProjectionError("taxonomy identity insert failed") from None
            if row.filter_value != expected_filter_value:
                raise TaxonomyProjectionError("taxonomy filter identity collision") from None
    elif row.filter_value != expected_filter_value:
        raise TaxonomyProjectionError("taxonomy filter identity collision")
    return row


async def replace_document_projection(
    session: AsyncSession,
    *,
    document: Document,
    frontmatter: dict[str, Any] | None,
    document_version: int,
    bump_revisions: bool = True,
) -> None:
    """Replace one document's public-v2 projection inside its caller transaction."""
    installed = await _projection_installed(session)
    if not installed:
        return
    is_public_v2 = (
        frontmatter is not None
        and frontmatter.get("schema_version") == 2
        and document.visibility == "public"
    )
    if is_public_v2 and bump_revisions:
        # Canonical public writes must never advance while discovery/search is
        # unavailable or a backfill is in progress.
        await _require_ready(session, TAXONOMY_TYPES)

    old_rows = await session.execute(
        select(PublicTaxonomyTerm.id, PublicTaxonomyTerm.taxonomy)
        .join(PublicTaxonomyMembership, PublicTaxonomyMembership.term_id == PublicTaxonomyTerm.id)
        .where(PublicTaxonomyMembership.document_id == document.id)
    )
    old_pairs = old_rows.all()
    old_term_ids = [str(term_id) for term_id, _ in old_pairs]
    old_taxonomies = list(dict.fromkeys(str(taxonomy) for _, taxonomy in old_pairs))
    new_entries = _reference_entries(frontmatter or {})
    affected = set(old_taxonomies) | {str(entry["taxonomy"]) for entry in new_entries}
    states = await _states(session, sorted(affected), lock=True)
    before = await _term_aggregate_states(session, old_term_ids)

    snapshot = await session.get(PublicTaxonomyDocumentSnapshot, document.id, with_for_update=True)
    if snapshot is not None:
        await session.execute(
            delete(PublicTaxonomyMembership).where(
                PublicTaxonomyMembership.document_id == document.id
            )
        )
        await session.delete(snapshot)
        await session.flush()
    new_term_ids: set[str] = set()

    if is_public_v2:
        assert frontmatter is not None
        snapshot = PublicTaxonomyDocumentSnapshot(
            document_id=document.id,
            document_version=document_version,
            schema_version=2,
            availability_status=str(frontmatter["availability"]["status"]),
            availability_from=frontmatter["availability"].get("available_from"),
            representation_status=str(frontmatter["public_representation"]["status"]),
            contact_disclosure=str(frontmatter["contact"]["disclosure"]),
            updated_at=_utc(cast(datetime, frontmatter["updated_at"])),
        )
        session.add(snapshot)
        await session.flush()
        for entry in new_entries:
            term = await _get_or_create_term(session, entry)
            new_term_ids.add(term.id)
            session.add(
                PublicTaxonomyMembership(
                    document_id=document.id,
                    term_id=term.id,
                    field_name=str(entry["field_name"]),
                    source_ordinal=int(entry["source_ordinal"]),
                    label_assertion=str(entry["label"]),
                    vocabulary_version=entry["vocabulary_version"],
                    language_proficiency=entry["language_proficiency"],
                    organization_relationship=entry["organization_relationship"],
                    location_country_code=entry["location_country_code"],
                    location_region=entry["location_region"],
                    location_city=entry["location_city"],
                )
            )
    await session.flush()
    await _recalculate_terms(session, set(old_term_ids) | new_term_ids)

    if not bump_revisions or not affected:
        return
    after = await _term_aggregate_states(session, set(old_term_ids) | new_term_ids)

    def taxonomy_state(
        values: dict[str, TermAggregate], taxonomy: str
    ) -> list[tuple[str, TermAggregate]]:
        return sorted((term_id, value) for term_id, value in values.items() if value[0] == taxonomy)

    for taxonomy in affected:
        if taxonomy_state(before, taxonomy) == taxonomy_state(after, taxonomy):
            continue
        state = states.get(taxonomy)
        if state is not None and state.status == "ready":
            state.revision += 1
            state.updated_at = datetime.now(UTC)


async def remove_document_projection(
    session: AsyncSession, document_id: str, *, bump_revisions: bool = True
) -> None:
    document = await session.get(Document, document_id)
    if document is None:
        return
    await replace_document_projection(
        session,
        document=document,
        frontmatter=None,
        document_version=document.current_version,
        bump_revisions=bump_revisions,
    )


class TaxonomyService:
    def __init__(self, cursor_secret: bytes) -> None:
        self.cursor_secret = cursor_secret

    async def check_ready(self, session: AsyncSession) -> bool | None:
        """Return None for pre-0022 test/legacy databases, otherwise enforce readiness."""
        if not await _projection_installed(session):
            return None
        await _require_ready(session, TAXONOMY_TYPES)
        return True

    async def verify_integrity(
        self,
        session: AsyncSession,
        *,
        require_ready: bool,
        deterministic: bool = False,
    ) -> None:
        states = await _states(session, TAXONOMY_TYPES)
        if len(states) != len(TAXONOMY_TYPES):
            raise TaxonomyUnavailable("public taxonomy projection state is incomplete")
        for taxonomy in TAXONOMY_TYPES:
            state = states[taxonomy]
            if state.contract_digest != TAXONOMY_CONTRACT_DIGEST:
                raise TaxonomyUnavailable("public taxonomy contract digest is invalid")
            if require_ready and state.status != "ready":
                raise TaxonomyUnavailable("public taxonomy projection is not ready")

        invalid_snapshots = await session.scalar(
            select(func.count())
            .select_from(PublicTaxonomyDocumentSnapshot)
            .join(Document, Document.id == PublicTaxonomyDocumentSnapshot.document_id)
            .where(
                or_(
                    Document.visibility != "public",
                    Document.schema_version != 2,
                    Document.current_version != PublicTaxonomyDocumentSnapshot.document_version,
                )
            )
        )
        if invalid_snapshots:
            raise TaxonomyUnavailable("public taxonomy contains a stale document snapshot")
        missing_snapshot = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.visibility == "public",
                Document.schema_version == 2,
                ~exists(
                    select(PublicTaxonomyDocumentSnapshot.document_id).where(
                        PublicTaxonomyDocumentSnapshot.document_id == Document.id,
                        PublicTaxonomyDocumentSnapshot.document_version == Document.current_version,
                    )
                ),
            )
        )
        if missing_snapshot:
            raise TaxonomyUnavailable("public taxonomy is missing a current document snapshot")
        invalid_schema_versions = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.visibility == "public",
                Document.kind.in_(("profile", "resume")),
                or_(
                    Document.schema_version.is_(None),
                    Document.schema_version.not_in((1, 2)),
                ),
            )
        )
        if invalid_schema_versions:
            raise TaxonomyUnavailable("public document schema version is invalid")
        invalid_canonical = await session.scalar(
            select(func.count())
            .select_from(PublicTaxonomyTerm)
            .where(
                or_(
                    PublicTaxonomyTerm.canonical_id
                    != PublicTaxonomyTerm.scheme + ":" + PublicTaxonomyTerm.external_id,
                    func.length(PublicTaxonomyTerm.canonical_id) > 336,
                )
            )
        )
        if invalid_canonical:
            raise TaxonomyUnavailable("public taxonomy canonical identity is invalid")
        orphan_terms = await session.scalar(
            select(func.count())
            .select_from(PublicTaxonomyTerm)
            .where(
                ~exists(
                    select(PublicTaxonomyMembership.id).where(
                        PublicTaxonomyMembership.term_id == PublicTaxonomyTerm.id
                    )
                )
            )
        )
        if orphan_terms:
            raise TaxonomyUnavailable("public taxonomy contains an unreferenced term")
        if not deterministic:
            return
        # The explicit CLI verify may afford a bounded full scan to validate
        # the deterministic tx1 mapping; readiness remains set-based.
        terms = (await session.scalars(select(PublicTaxonomyTerm))).all()
        for term in terms:
            expected = taxonomy_filter_value(term.taxonomy, term.scheme, term.external_id)
            if (
                term.filter_value != expected
                or not _FILTER_VALUE_RE.fullmatch(term.filter_value)
                or term.canonical_id != canonical_id(term.scheme, term.external_id)
                or not _SCHEME_RE.fullmatch(term.scheme)
                or not _EXTERNAL_ID_RE.fullmatch(term.external_id)
                or (
                    term.taxonomy == "work_mode"
                    and (
                        term.scheme != WORK_MODE_SCHEME or term.external_id not in WORK_MODE_LABELS
                    )
                )
            ):
                raise TaxonomyUnavailable("public taxonomy filter identity is invalid")

    async def catalog(self, session: AsyncSession) -> list[dict[str, Any]]:
        await _require_ready(session, TAXONOMY_TYPES)
        states = await _states(session, TAXONOMY_TYPES)
        definitions = {
            "occupation": (["occupation_ids"], "reference", "AND", "canonical Markdown"),
            "industry": (["industry_ids"], "reference", "AND", "canonical Markdown"),
            "location": (["location_id"], "reference", "singleton", "canonical Markdown"),
            "skill": (["skill_ids"], "reference", "AND", "canonical Markdown"),
            "language": (["language_ids"], "reference", "AND", "canonical Markdown"),
            "seniority": (
                ["seniority_ids", "seniority_id"],
                "reference",
                "OR",
                "canonical Markdown",
            ),
            "open_to": (
                ["open_to_ids", "open_to"],
                "reference",
                "AND",
                "canonical Markdown",
            ),
            "organization": (["organization_ids"], "reference", "AND", "canonical Markdown"),
            "representative": (
                ["representative_ids"],
                "reference",
                "OR",
                "owner-attested public_representation claim",
            ),
            "work_mode": (["work_modes"], "connect.md enum", "AND", "schema-owned value"),
        }
        return [
            {
                "taxonomy": taxonomy,
                "parameters": definitions[taxonomy][0],
                "kind": definitions[taxonomy][1],
                "semantics": definitions[taxonomy][2],
                "source": "current public v2 canonical Markdown projected in PostgreSQL",
                "authority": (
                    f"{definitions[taxonomy][3]}; discovery and search filtering only; "
                    "never identity, mandate, grant, consent, or outreach authority"
                ),
                "current_revision": states[taxonomy].revision,
            }
            for taxonomy in TAXONOMY_TYPES
        ]

    async def terms(
        self,
        session: AsyncSession,
        *,
        taxonomy: str,
        query: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        if taxonomy not in TAXONOMY_TYPES:
            raise TaxonomyUnknown("unknown taxonomy")
        if len(query) > 100 or not 1 <= limit <= 100:
            raise TaxonomyInvalidValue("taxonomy list bounds are invalid")
        if cursor is not None and (
            not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048
        ):
            raise TaxonomyCursorMalformed("taxonomy cursor is malformed")
        state = (await _require_ready(session, [taxonomy]))[taxonomy]
        normalized_query = query.strip().lower()
        query_digest = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
        last_sort_label: str | None = None
        last_canonical: str | None = None
        if cursor is not None:
            payload = _cursor_decode(cursor, self.cursor_secret)
            try:
                if (
                    payload["v"] != 2
                    or payload["taxonomy"] != taxonomy
                    or payload["query_digest"] != query_digest
                ):
                    raise TaxonomyCursorMalformed("taxonomy cursor is malformed")
                revision = payload["revision"]
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise TaxonomyCursorMalformed("taxonomy cursor is malformed")
                if revision != state.revision:
                    raise TaxonomyCursorStale("taxonomy cursor is stale")
                term_id = payload["term_id"]
                filter_value = payload["filter_value"]
                if (
                    not isinstance(term_id, str)
                    or not isinstance(filter_value, str)
                    or not _FILTER_VALUE_RE.fullmatch(filter_value)
                ):
                    raise TaxonomyCursorMalformed("taxonomy cursor is malformed")
            except TaxonomyCursorStale:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise TaxonomyCursorMalformed("taxonomy cursor is malformed") from exc

        sort_label = func.lower(
            func.coalesce(PublicTaxonomyTerm.label, PublicTaxonomyTerm.canonical_id)
        )
        if cursor is not None:
            last_row = (
                await session.execute(
                    select(PublicTaxonomyTerm, sort_label.label("sort_label")).where(
                        PublicTaxonomyTerm.id == term_id,
                        PublicTaxonomyTerm.taxonomy == taxonomy,
                        PublicTaxonomyTerm.filter_value == filter_value,
                    )
                )
            ).first()
            if last_row is None or not isinstance(last_row[1], str):
                raise TaxonomyCursorMalformed("taxonomy cursor is malformed")
            last_term, last_sort_label = last_row
            last_canonical = last_term.canonical_id
        statement = select(PublicTaxonomyTerm, sort_label.label("sort_label")).where(
            PublicTaxonomyTerm.taxonomy == taxonomy
        )
        if normalized_query:
            escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            statement = statement.where(
                or_(
                    PublicTaxonomyTerm.filter_value == normalized_query,
                    sort_label.like(pattern, escape="\\"),
                    func.lower(PublicTaxonomyTerm.canonical_id).like(pattern, escape="\\"),
                    func.lower(PublicTaxonomyTerm.scheme).like(pattern, escape="\\"),
                    func.lower(PublicTaxonomyTerm.external_id).like(pattern, escape="\\"),
                    exists(
                        select(PublicTaxonomyMembership.id).where(
                            PublicTaxonomyMembership.term_id == PublicTaxonomyTerm.id,
                            func.lower(PublicTaxonomyMembership.label_assertion).like(
                                pattern, escape="\\"
                            ),
                        )
                    ),
                )
            )
        if last_sort_label is not None and last_canonical is not None:
            statement = statement.where(
                or_(
                    sort_label > last_sort_label,
                    and_(
                        sort_label == last_sort_label,
                        PublicTaxonomyTerm.canonical_id > last_canonical,
                    ),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(sort_label.asc(), PublicTaxonomyTerm.canonical_id.asc()).limit(
                    limit + 1
                )
            )
        ).all()
        current_state = (await _require_ready(session, [taxonomy]))[taxonomy]
        if current_state.revision != state.revision:
            raise TaxonomyCursorStale("taxonomy cursor is stale")
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last, label = page[-1]
            next_cursor = _cursor_encode(
                {
                    "v": 2,
                    "taxonomy": taxonomy,
                    "query_digest": query_digest,
                    "revision": state.revision,
                    "term_id": last.id,
                    "filter_value": last.filter_value,
                },
                self.cursor_secret,
            )
        return [self._term_response(row) for row, _ in page], next_cursor, state.revision

    @staticmethod
    def _term_response(row: PublicTaxonomyTerm) -> dict[str, Any]:
        return {
            "taxonomy": row.taxonomy,
            "scheme": row.scheme,
            "external_id": row.external_id,
            "canonical_id": row.canonical_id,
            "filter_value": row.filter_value,
            "label": row.label,
            "label_conflict": row.label_conflict,
            "vocabulary_version": row.vocabulary_version,
            "version_conflict": row.version_conflict,
        }

    async def taxonomy_facets(
        self,
        session: AsyncSession,
        hits: list[dict[str, Any]],
        requested: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Build privacy-minimal facet entries from current PostgreSQL terms.

        Search hits already passed the canonical snapshot recheck.  We still
        hydrate facet labels and conflict metadata from the term rows rather
        than trusting Meilisearch display fields.  Each document contributes
        at most once for each alias in a requested facet.
        """
        facet_map: dict[str, tuple[str, str]] = {
            "occupation_ids": ("occupation", "occupation_ids"),
            "industry_ids": ("industry", "industry_ids"),
            "skill_ids": ("skill", "skill_ids"),
            "language_ids": ("language", "language_ids"),
            "location_id": ("location", "location_id"),
            "seniority_id": ("seniority", "seniority_ids"),
            "seniority_ids": ("seniority", "seniority_ids"),
            "work_modes": ("work_mode", "work_modes"),
            "open_to": ("open_to", "open_to_ids"),
            "open_to_ids": ("open_to", "open_to_ids"),
            "organization_ids": ("organization", "organization_ids"),
            "representative_id": ("representative", "representative_ids"),
            "representative_ids": ("representative", "representative_ids"),
        }
        result: dict[str, list[dict[str, Any]]] = {
            name: [] for name in requested if name in facet_map
        }
        aliases_by_facet: dict[str, set[str]] = {name: set() for name in result}
        aliases_by_hit: dict[tuple[str, str], set[str]] = {}
        alias_fields = {
            "occupation_ids": "occupation_filter_values",
            "industry_ids": "industry_filter_values",
            "skill_ids": "skill_filter_values",
            "language_ids": "language_filter_values",
            "location_id": "location_filter_value",
            "seniority_id": "seniority_filter_value",
            "seniority_ids": "seniority_filter_values",
            "work_modes": "work_mode_filter_values",
            "open_to": "open_to_filter_values",
            "open_to_ids": "open_to_filter_values",
            "organization_ids": "organization_filter_values",
            "representative_id": "representative_filter_value",
            "representative_ids": "representative_filter_values",
        }
        for facet in result:
            field = alias_fields[facet]
            for hit in hits:
                raw = hit.get(field)
                values = raw if isinstance(raw, list) else [raw]
                aliases = {
                    value
                    for value in values
                    if isinstance(value, str) and _FILTER_VALUE_RE.fullmatch(value)
                }
                if aliases:
                    aliases_by_hit[(facet, str(hit.get("id")))] = aliases
                    aliases_by_facet[facet].update(aliases)
        all_aliases = set().union(*aliases_by_facet.values()) if aliases_by_facet else set()
        if not all_aliases:
            return result
        rows = (
            await session.scalars(
                select(PublicTaxonomyTerm).where(PublicTaxonomyTerm.filter_value.in_(all_aliases))
            )
        ).all()
        terms = {row.filter_value: row for row in rows}
        for facet, aliases in aliases_by_facet.items():
            taxonomy, parameter = facet_map[facet]
            counts = {alias: 0 for alias in aliases}
            for hit in hits:
                counts_for_hit = aliases_by_hit.get((facet, str(hit.get("id"))), set())
                for alias in counts_for_hit:
                    if alias in counts:
                        counts[alias] += 1
            entries: list[dict[str, Any]] = []
            for alias, count in counts.items():
                term = terms.get(alias)
                if term is None or term.taxonomy != taxonomy:
                    continue
                entries.append(
                    {
                        "taxonomy": taxonomy,
                        "parameter": parameter,
                        "canonical_id": term.canonical_id,
                        "filter_value": term.filter_value,
                        "label": term.label,
                        "label_conflict": term.label_conflict,
                        "vocabulary_version": term.vocabulary_version,
                        "version_conflict": term.version_conflict,
                        "count": count,
                    }
                )
            result[facet] = sorted(entries, key=lambda entry: entry["canonical_id"])
        return result

    async def resolve_search(
        self,
        session: AsyncSession,
        arguments: dict[str, Any],
        *,
        allow_long_canonical: bool,
    ) -> ResolvedSearchFilters:
        typed_repeated_values = sum(
            len(arguments.get(field) or [])
            for field in (
                "occupation_ids",
                "industry_ids",
                "skill_ids",
                "language_ids",
                "seniority_ids",
                "open_to_ids",
                "organization_ids",
                "representative_ids",
                "work_modes",
            )
        )
        if typed_repeated_values > MAX_SEARCH_REPEATED_VALUES:
            raise TaxonomyInvalidValue("search contains too many repeated taxonomy values")
        normalized_arguments = dict(arguments)
        normalized_arguments["open_to_ids"] = [
            *list(arguments.get("open_to_ids") or []),
            *list(arguments.get("open_to") or []),
        ]
        normalized_arguments["seniority_ids"] = list(
            dict.fromkeys(
                [
                    *list(arguments.get("seniority_ids") or []),
                    *(
                        [str(arguments["seniority_id"])]
                        if arguments.get("seniority_id") is not None
                        else []
                    ),
                ]
            )
        )
        installed = await _projection_installed(session)
        if not installed:
            if any(
                normalized_arguments.get(field)
                for field in (
                    "occupation_ids",
                    "industry_ids",
                    "skill_ids",
                    "language_ids",
                    "seniority_ids",
                    "open_to_ids",
                    "organization_ids",
                    "representative_ids",
                    "work_modes",
                    "location_id",
                )
            ):
                raise TaxonomyUnavailable(
                    "typed taxonomy search requires the installed public projection"
                )
            return ResolvedSearchFilters(
                meili=normalized_arguments,
                canonical=normalized_arguments,
                filter_values={},
                requested=dict(arguments),
                installed=False,
            )
        # The complete registry must be ready before the caller can perform a
        # Meilisearch request, including an unfiltered search.
        await _require_ready(session, TAXONOMY_TYPES)
        requested = dict(arguments)
        typed_fields = {
            "occupation_ids": "occupation",
            "industry_ids": "industry",
            "skill_ids": "skill",
            "language_ids": "language",
            "seniority_ids": "seniority",
            "open_to_ids": "open_to",
            "organization_ids": "organization",
            "representative_ids": "representative",
            "work_modes": "work_mode",
        }
        nonempty_taxonomies = [
            taxonomy for field, taxonomy in typed_fields.items() if normalized_arguments.get(field)
        ]
        if normalized_arguments.get("location_id"):
            nonempty_taxonomies.append("location")
        if nonempty_taxonomies:
            await _require_ready(session, nonempty_taxonomies)

        canonical = dict(normalized_arguments)
        meili = dict(normalized_arguments)
        filter_values: dict[str, Any] = {}
        for field, taxonomy in typed_fields.items():
            values = list(normalized_arguments.get(field) or [])
            if not values:
                continue
            resolved_rows = await self._resolve_many(
                session,
                taxonomy,
                [str(value) for value in values],
                allow_long_canonical=allow_long_canonical,
            )
            if resolved_rows is None:
                return ResolvedSearchFilters(
                    meili=meili,
                    canonical=canonical,
                    filter_values={},
                    requested=requested,
                    installed=True,
                    empty=True,
                )
            resolved = [row[0] for row in resolved_rows]
            aliases = [row[1] for row in resolved_rows]
            external_values = [row[2] for row in resolved_rows]
            # Work-mode canonical identities use the internal connect.md
            # scheme, while the existing Meili/index and hydrated source
            # values remain the raw schema literals.
            canonical[field] = external_values if taxonomy == "work_mode" else resolved
            filter_values[field] = aliases
            meili[field] = external_values if taxonomy == "work_mode" else resolved

        location_value = arguments.get("location_id")
        if location_value:
            resolved_rows = await self._resolve_many(
                session,
                "location",
                [str(location_value)],
                allow_long_canonical=allow_long_canonical,
            )
            if not resolved_rows:
                return ResolvedSearchFilters(
                    meili=meili,
                    canonical=canonical,
                    filter_values={},
                    requested=requested,
                    installed=True,
                    empty=True,
                )
            canonical_value, filter_value, _ = resolved_rows[0]
            canonical["location_id"] = canonical_value
            meili["location_id"] = canonical_value
            filter_values["location_id"] = filter_value

        return ResolvedSearchFilters(
            meili=meili,
            canonical=canonical,
            filter_values=filter_values,
            requested=requested,
            installed=True,
        )

    async def _resolve_many(
        self,
        session: AsyncSession,
        taxonomy: str,
        values: list[str],
        *,
        allow_long_canonical: bool,
    ) -> list[tuple[str, str, str]] | None:
        """Resolve one typed value set with one bounded PostgreSQL query."""
        alias_values: list[str] = []
        canonical_values: list[str] = []
        parsed: list[tuple[str, str]] = []
        max_length = 336 if allow_long_canonical else 80
        for value in values:
            if taxonomy == "work_mode" and value in WORK_MODE_LABELS:
                value = canonical_id(WORK_MODE_SCHEME, value)
            if _FILTER_VALUE_RE.fullmatch(value):
                parsed.append(("filter_value", value))
                if value not in alias_values:
                    alias_values.append(value)
                continue
            if len(value) > max_length:
                raise TaxonomyInvalidValue("taxonomy identity exceeds the transport bound")
            if not _value_is_well_formed(value, max_length=max_length):
                raise TaxonomyInvalidValue("taxonomy identity is malformed")
            parsed.append(("canonical_id", value))
            if value not in canonical_values:
                canonical_values.append(value)
        conditions = []
        if alias_values:
            conditions.append(PublicTaxonomyTerm.filter_value.in_(alias_values))
        if canonical_values:
            conditions.append(PublicTaxonomyTerm.canonical_id.in_(canonical_values))
        rows = (
            await session.scalars(
                select(PublicTaxonomyTerm).where(
                    PublicTaxonomyTerm.taxonomy == taxonomy,
                    or_(*conditions),
                )
            )
        ).all()
        by_alias = {row.filter_value: row for row in rows}
        by_canonical = {row.canonical_id: row for row in rows}
        result: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for kind, value in parsed:
            row = by_alias.get(value) if kind == "filter_value" else by_canonical.get(value)
            if row is None:
                return None
            if row.canonical_id in seen:
                continue
            seen.add(row.canonical_id)
            result.append((row.canonical_id, row.filter_value, row.external_id))
        return result

    async def _resolve_one(
        self,
        session: AsyncSession,
        taxonomy: str,
        value: str,
        *,
        allow_long_canonical: bool,
    ) -> tuple[str | None, str | None, str]:
        rows = await self._resolve_many(
            session,
            taxonomy,
            [value],
            allow_long_canonical=allow_long_canonical,
        )
        if not rows:
            return None, None, value
        return rows[0]

    async def hydrate_hits(
        self,
        session: AsyncSession,
        hits: list[dict[str, Any]],
        resolved: ResolvedSearchFilters,
    ) -> list[dict[str, Any]]:
        if not resolved.installed:
            return hits
        await _require_ready(session, TAXONOMY_TYPES)
        if not hits:
            return []
        document_ids = list(
            dict.fromkeys(str(hit["id"]) for hit in hits if isinstance(hit.get("id"), str))
        )
        if not document_ids:
            return []
        documents = {
            document.id: document
            for document in (
                await session.scalars(select(Document).where(Document.id.in_(document_ids)))
            ).all()
        }
        typed_fields = (
            "occupation_ids",
            "industry_ids",
            "skill_ids",
            "language_ids",
            "seniority_ids",
            "open_to_ids",
            "organization_ids",
            "representative_ids",
            "location_id",
            "work_modes",
        )
        has_typed_predicate = any(resolved.canonical.get(field) for field in typed_fields)
        rows = await session.execute(
            select(PublicTaxonomyDocumentSnapshot, PublicTaxonomyMembership, PublicTaxonomyTerm)
            .join(
                PublicTaxonomyMembership,
                PublicTaxonomyMembership.document_id == PublicTaxonomyDocumentSnapshot.document_id,
                isouter=True,
            )
            .join(
                PublicTaxonomyTerm,
                PublicTaxonomyTerm.id == PublicTaxonomyMembership.term_id,
                isouter=True,
            )
            .where(PublicTaxonomyDocumentSnapshot.document_id.in_(document_ids))
            .order_by(
                PublicTaxonomyDocumentSnapshot.document_id,
                PublicTaxonomyMembership.field_name,
                PublicTaxonomyMembership.source_ordinal,
                PublicTaxonomyMembership.id,
            )
        )
        hydrated: dict[str, dict[str, Any]] = {}
        hydrated_versions: dict[str, int] = {}
        for snapshot, membership, term in rows.all():
            hydrated_versions[snapshot.document_id] = snapshot.document_version
            data = hydrated.setdefault(
                snapshot.document_id,
                {
                    "occupations": [],
                    "occupation_ids": [],
                    "occupation_filter_values": [],
                    "industries": [],
                    "industry_ids": [],
                    "industry_filter_values": [],
                    "location": None,
                    "location_id": None,
                    "location_filter_value": None,
                    "location_label": None,
                    "location_country_code": None,
                    "location_region": None,
                    "location_city": None,
                    "skills": [],
                    "skill_ids": [],
                    "skill_filter_values": [],
                    "languages": [],
                    "language_ids": [],
                    "language_filter_values": [],
                    "language_proficiencies": [],
                    "seniority": None,
                    "seniority_id": None,
                    "seniority_filter_value": None,
                    "seniority_ids": [],
                    "seniority_filter_values": [],
                    "work_modes": [],
                    "work_mode_filter_values": [],
                    "open_to": [],
                    "open_to_ids": [],
                    "open_to_filter_values": [],
                    "organizations": [],
                    "organization_ids": [],
                    "organization_filter_values": [],
                    "organization_relationships": [],
                    "representative": None,
                    "representative_id": None,
                    "representative_ids": [],
                    "representative_filter_value": None,
                    "representative_filter_values": [],
                    "taxonomy_versions": [],
                    "availability_status": snapshot.availability_status,
                    "availability_from": self._search_scalar(snapshot.availability_from),
                    "representation_status": snapshot.representation_status,
                    "contact_disclosure": snapshot.contact_disclosure,
                    "updated_at": self._search_scalar(snapshot.updated_at),
                    "schema_version": 2,
                },
            )
            if membership is None or term is None:
                continue
            label = membership.label_assertion
            identity = term.canonical_id
            alias = term.filter_value
            taxonomy = term.taxonomy
            if taxonomy == "occupation":
                data["occupations"].append(label)
                data["occupation_ids"].append(identity)
                data["occupation_filter_values"].append(alias)
            elif taxonomy == "industry":
                data["industries"].append(label)
                data["industry_ids"].append(identity)
                data["industry_filter_values"].append(alias)
            elif taxonomy == "location":
                data.update(
                    location=label,
                    location_id=identity,
                    location_filter_value=alias,
                    location_label=label,
                    location_country_code=membership.location_country_code,
                    location_region=membership.location_region,
                    location_city=membership.location_city,
                )
            elif taxonomy == "skill":
                data["skills"].append(label)
                data["skill_ids"].append(identity)
                data["skill_filter_values"].append(alias)
            elif taxonomy == "language":
                data["languages"].append(label)
                data["language_ids"].append(identity)
                data["language_filter_values"].append(alias)
                data["language_proficiencies"].append(membership.language_proficiency)
            elif taxonomy == "seniority":
                data["seniority"] = label
                data["seniority_id"] = identity
                data["seniority_filter_value"] = alias
                data["seniority_ids"].append(identity)
                data["seniority_filter_values"].append(alias)
            elif taxonomy == "work_mode":
                data["work_modes"].append(term.external_id)
                data["work_mode_filter_values"].append(alias)
            elif taxonomy == "open_to":
                data["open_to"].append(label)
                data["open_to_ids"].append(identity)
                data["open_to_filter_values"].append(alias)
            elif taxonomy == "organization":
                data["organizations"].append(label)
                data["organization_ids"].append(identity)
                data["organization_filter_values"].append(alias)
                if membership.organization_relationship is not None:
                    data["organization_relationships"].append(membership.organization_relationship)
            elif taxonomy == "representative":
                data["representative"] = label
                data["representative_id"] = identity
                data["representative_ids"].append(identity)
                data["representative_filter_value"] = alias
                data["representative_filter_values"].append(alias)
            if membership.vocabulary_version is not None:
                data["taxonomy_versions"].append(f"{term.scheme}@{membership.vocabulary_version}")
        for data in hydrated.values():
            data["taxonomy_versions"] = sorted(set(data["taxonomy_versions"]))

        filtered: list[dict[str, Any]] = []
        for hit in hits:
            document = documents.get(str(hit.get("id")))
            if document is None or document.visibility != "public":
                continue
            try:
                hit_version = int(hit["version"])
            except (KeyError, TypeError, ValueError):
                continue
            if hit_version != document.current_version:
                continue
            hit_data = hydrated.get(str(hit.get("id")))
            if document.schema_version != 2:
                if has_typed_predicate:
                    continue
                legacy: dict[str, Any] = {
                    "skill_ids": [],
                    "occupation_ids": [],
                    "occupations": [],
                    "industry_ids": [],
                    "industries": [],
                    "language_ids": [],
                    "languages": [],
                    "language_proficiencies": [],
                    "location_id": None,
                    "location_label": None,
                    "location_country_code": None,
                    "location_region": None,
                    "location_city": None,
                    "seniority_ids": [],
                    "seniority_id": None,
                    "seniority": None,
                    "work_modes": [],
                    "open_to": [],
                    "open_to_ids": [],
                    "organization_ids": [],
                    "organizations": [],
                    "representative": None,
                    "representative_id": None,
                    "representative_ids": [],
                    "taxonomy_versions": [],
                    "schema_version": document.schema_version,
                }
                for key in (
                    "occupation_filter_values",
                    "industry_filter_values",
                    "skill_filter_values",
                    "language_filter_values",
                    "location_filter_value",
                    "seniority_filter_value",
                    "seniority_filter_values",
                    "work_mode_filter_values",
                    "open_to_filter_values",
                    "organization_filter_values",
                    "representative_filter_value",
                    "representative_filter_values",
                ):
                    legacy[key] = [] if key.endswith("values") else None
                filtered.append({**hit, **legacy})
                continue
            if hit_data is None:
                # A ready projection should make this impossible; dropping a
                # forged/stale v2 hit is safer than hydrating from Meili.
                continue
            if hydrated_versions.get(str(hit.get("id"))) != document.current_version:
                continue
            if not self._matches(hit, hit_data, resolved.canonical):
                continue
            filtered.append({**hit, **hit_data})
        return filtered

    @staticmethod
    def _search_scalar(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return _utc(value).isoformat()
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        return str(value)

    @staticmethod
    def _matches(hit: dict[str, Any], data: dict[str, Any], filters: dict[str, Any]) -> bool:
        def all_values(field: str, values: list[str]) -> bool:
            return not values or all(value in data.get(field, []) for value in values)

        if not all_values("occupation_ids", list(filters.get("occupation_ids") or [])):
            return False
        if not all_values("industry_ids", list(filters.get("industry_ids") or [])):
            return False
        if not all_values("skill_ids", list(filters.get("skill_ids") or [])):
            return False
        if not all_values("language_ids", list(filters.get("language_ids") or [])):
            return False
        if not all_values("open_to_ids", list(filters.get("open_to_ids") or [])):
            return False
        if not all_values("organization_ids", list(filters.get("organization_ids") or [])):
            return False
        if filters.get("seniority_ids") and not any(
            value in data.get("seniority_ids", []) for value in filters["seniority_ids"]
        ):
            return False
        if filters.get("representative_ids") and not any(
            value in data.get("representative_ids", []) for value in filters["representative_ids"]
        ):
            return False
        if filters.get("location_id") and data.get("location_id") != filters["location_id"]:
            return False
        if not all_values("work_modes", list(filters.get("work_modes") or [])):
            return False
        if filters.get("skills") and not all(
            value in data.get("skills", []) for value in filters["skills"]
        ):
            return False
        if filters.get("location") is not None and data.get("location") != filters["location"]:
            return False
        for field in (
            "location_country_code",
            "location_region",
            "location_city",
            "availability_status",
            "availability_from",
            "representation_status",
            "contact_disclosure",
        ):
            if filters.get(field) is not None and data.get(field) != filters[field]:
                return False
        updated_at = data.get("updated_at")
        if filters.get("updated_after") is not None or filters.get("updated_before") is not None:
            if not isinstance(updated_at, str):
                return False
            try:
                updated_value = _utc(updated_at)
                lower_bound = (
                    _utc(str(filters["updated_after"]))
                    if filters.get("updated_after") is not None
                    else None
                )
                upper_bound = (
                    _utc(str(filters["updated_before"]))
                    if filters.get("updated_before") is not None
                    else None
                )
            except (TypeError, ValueError):
                return False
            if lower_bound is not None and updated_value < lower_bound:
                return False
            if upper_bound is not None and updated_value > upper_bound:
                return False
        return True

    async def backfill(
        self, session: AsyncSession, store: Any, *, if_required: bool
    ) -> dict[str, Any]:
        states = await _states(session, TAXONOMY_TYPES)
        if len(states) != len(TAXONOMY_TYPES):
            raise TaxonomyUnavailable("public taxonomy projection migration is incomplete")
        if if_required and all(
            state.status == "ready" and state.contract_digest == TAXONOMY_CONTRACT_DIGEST
            for state in states.values()
        ):
            try:
                await self.verify_integrity(session, require_ready=True, deterministic=True)
            except TaxonomyUnavailable:
                pass
            else:
                return {"status": "ready", "backfilled": 0, "reused": True}
        before = {
            taxonomy: await _taxonomy_digest(session, taxonomy) for taxonomy in TAXONOMY_TYPES
        }
        for state in states.values():
            state.status = "building"
            state.last_error_code = None
            state.updated_at = datetime.now(UTC)
        await session.commit()
        try:
            await session.execute(delete(PublicTaxonomyMembership))
            await session.execute(delete(PublicTaxonomyDocumentSnapshot))
            await session.execute(delete(PublicTaxonomyTerm))
            documents = (
                await session.scalars(
                    select(Document)
                    .where(
                        Document.visibility == "public",
                        Document.kind.in_(("profile", "resume")),
                    )
                    .order_by(Document.id)
                    .with_for_update()
                )
            ).all()
            count = 0
            for document in documents:
                version = await session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.version == document.current_version,
                    )
                )
                if version is None:
                    raise TaxonomyProjectionError("current document version is missing")
                markdown = store.read_verified(version.storage_path, version.sha256)
                frontmatter, _ = validate_canonical(document.kind, markdown)
                if frontmatter["visibility"] != "public":
                    raise TaxonomyProjectionError(
                        "public document Markdown visibility does not match the database"
                    )
                document.schema_version = int(frontmatter["schema_version"])
                if frontmatter["schema_version"] == 2:
                    count += 1
                    await replace_document_projection(
                        session,
                        document=document,
                        frontmatter=frontmatter,
                        document_version=document.current_version,
                        bump_revisions=False,
                    )
                else:
                    await replace_document_projection(
                        session,
                        document=document,
                        frontmatter=None,
                        document_version=document.current_version,
                        bump_revisions=False,
                    )
            after = {
                taxonomy: await _taxonomy_digest(session, taxonomy) for taxonomy in TAXONOMY_TYPES
            }
            for taxonomy, state in states.items():
                if before[taxonomy] != after[taxonomy] or state.revision == 0:
                    state.revision = max(1, state.revision + 1)
                state.status = "ready"
                state.contract_digest = TAXONOMY_CONTRACT_DIGEST
                state.updated_at = datetime.now(UTC)
            await session.commit()
            return {"status": "ready", "backfilled": count, "reused": False}
        except Exception:
            await session.rollback()
            for state in states.values():
                state.status = "failed"
                state.last_error_code = "backfill_failed"
                state.updated_at = datetime.now(UTC)
            await session.commit()
            raise
