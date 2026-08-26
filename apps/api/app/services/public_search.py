"""Public search projection, sanitization, and Agent Identity enrichment.

This module contains the shared read-only search service used by HTTP, MCP, and
A2A callers.  Route handlers remain responsible for authentication, argument
parsing, and transport-specific status mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import Parameter, signature
from typing import Any, Literal, cast

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentIdentity, Document
from app.schemas import (
    SearchAgentIdentityReference,
    SearchHit,
    SearchResponse,
    TaxonomyFacetEntry,
)
from app.services.exact_search import (
    ExactSearchCursorMalformed,
    ExactSearchCursorStale,
    ExactSearchResult,
    ExactSearchTooBroad,
    ExactSearchUnavailable,
)
from app.services.search import MAX_AGENT_SEARCH_RESULTS, SearchUnavailable
from app.services.taxonomy import (
    TaxonomyInvalidValue,
    TaxonomyUnavailable,
    ensure_search_repeated_value_cap,
)

_INTERNAL_CONTACT_REQUEST_CAPABILITY: Literal["internal_contact_request"] = (
    "internal_contact_request"
)
_AGENT_IDENTITY_SEARCH_CHUNK_SIZE = 200
_MAX_SEARCH_AGENT_IDENTITIES_PER_PROFILE = 10


def markdown_url(document: Document, version: int | None = None) -> str:
    base = (
        f"/v1/profiles/{document.public_identifier}.md"
        if document.kind == "profile"
        else f"/v1/resumes/{document.public_identifier}.md"
    )
    return base if version is None else base.removesuffix(".md") + f"/versions/{version}.md"


def sanitized_search_hit(hit: dict[str, Any], document: Document) -> dict[str, Any]:
    """Project search data through the public contract and authoritative identity."""
    clean = dict(hit)
    clean.update(
        {
            "id": document.id,
            "kind": document.kind,
            "identifier": document.public_identifier,
            "version": document.current_version,
            "updated_at": document.updated_at,
            "excerpt": str(clean.get("headline", ""))[:240] or None,
            "html_url": (
                f"/p/{document.public_identifier}"
                if document.kind == "profile"
                else f"/r/{document.public_identifier}"
            ),
            "markdown_url": markdown_url(document),
        }
    )
    return SearchHit.model_validate(clean).model_dump(mode="json")


def public_agent_identity_eligibility_filters() -> tuple[Any, ...]:
    return (
        AgentIdentity.status == "active",
        Document.kind == "profile",
        Document.visibility == "public",
        Document.owner_id == AgentIdentity.owner_id,
    )


async def search_agent_identity_references(
    session: AsyncSession, profile_document_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    unique_profile_ids = list(dict.fromkeys(profile_document_ids))
    references: dict[str, list[dict[str, Any]]] = {
        profile_id: [] for profile_id in unique_profile_ids
    }
    for start in range(0, len(unique_profile_ids), _AGENT_IDENTITY_SEARCH_CHUNK_SIZE):
        chunk = unique_profile_ids[start : start + _AGENT_IDENTITY_SEARCH_CHUNK_SIZE]
        rows = await session.execute(
            select(
                AgentIdentity.profile_document_id,
                AgentIdentity.handle,
                AgentIdentity.created_at,
                AgentIdentity.id,
            )
            .join(Document, Document.id == AgentIdentity.profile_document_id)
            .where(
                *public_agent_identity_eligibility_filters(),
                AgentIdentity.profile_document_id.in_(chunk),
            )
            .order_by(AgentIdentity.created_at.desc(), AgentIdentity.id.desc())
        )
        for row in rows.all():
            profile_id = cast(str, row[0])
            profile_references = references.get(profile_id)
            if profile_references is None or len(profile_references) >= (
                _MAX_SEARCH_AGENT_IDENTITIES_PER_PROFILE
            ):
                continue
            profile_references.append(
                SearchAgentIdentityReference(
                    handle=cast(str, row[1]),
                    capabilities=[_INTERNAL_CONTACT_REQUEST_CAPABILITY],
                ).model_dump(mode="json")
            )
    return references


async def enrich_public_search_hits(
    session: AsyncSession,
    hits: list[dict[str, Any]],
    *,
    agent_capability: str | None,
) -> list[dict[str, Any]]:
    profile_ids = [
        cast(str, hit["id"])
        for hit in hits
        if hit.get("kind") == "profile" and isinstance(hit.get("id"), str)
    ]
    references = await search_agent_identity_references(session, profile_ids)
    enriched: list[dict[str, Any]] = []
    for hit in hits:
        profile_id = hit.get("id") if hit.get("kind") == "profile" else None
        agent_references = references.get(profile_id, []) if isinstance(profile_id, str) else []
        if agent_capability is not None and not agent_references:
            continue
        enriched.append({**hit, "agent_identities": agent_references})
    return enriched


async def execute_public_search(
    request: Request,
    session: AsyncSession,
    arguments: dict[str, Any],
    *,
    allow_long_canonical: bool,
) -> SearchResponse:
    ensure_search_repeated_value_cap(arguments)
    query = arguments.get("q", arguments.get("query", ""))
    if not isinstance(query, str):
        raise TaxonomyInvalidValue("search query is invalid")
    mode = arguments.get("mode", "projection")
    if mode not in {"projection", "exact"}:
        raise TaxonomyInvalidValue("search mode is invalid")
    kind = arguments.get("kind")
    skills = list(arguments.get("skills") or [])
    location = arguments.get("location")
    facets = list(arguments.get("facets") or [])
    offset = int(arguments.get("offset", 0))
    limit = int(arguments.get("limit", 20))
    cursor = arguments.get("cursor")
    facet_limit = int(arguments.get("facet_limit", 100))
    if not 1 <= facet_limit <= 500:
        raise TaxonomyInvalidValue("facet_limit is invalid")
    if mode == "projection" and cursor is not None:
        raise ExactSearchCursorMalformed("cursor is available only in exact mode")
    parsed_bounds: dict[str, datetime] = {}
    for field in ("updated_after", "updated_before"):
        value = arguments.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise TaxonomyInvalidValue("search time bounds are invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TaxonomyInvalidValue("search time bounds are invalid") from exc
        if parsed.tzinfo is None:
            raise TaxonomyInvalidValue("search time bounds require a timezone")
        parsed_bounds[field] = parsed.astimezone(UTC)
    if (
        "updated_after" in parsed_bounds
        and "updated_before" in parsed_bounds
        and parsed_bounds["updated_after"] > parsed_bounds["updated_before"]
    ):
        raise TaxonomyInvalidValue("search time bounds are invalid")
    allowed_facets = {
        "kind",
        "skills",
        "skill_ids",
        "occupation_ids",
        "industry_ids",
        "language_ids",
        "location_id",
        "location_country_code",
        "seniority_id",
        "seniority_ids",
        "work_modes",
        "availability_status",
        "open_to_ids",
        "organization_ids",
        "representative_id",
        "representative_ids",
        "representation_status",
        "contact_disclosure",
        "taxonomy_versions",
    }
    unknown_facets = sorted(set(facets) - allowed_facets)
    if unknown_facets:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported facets: {', '.join(unknown_facets)}",
        )
    taxonomy_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"facets", "offset", "limit", "agent_capability"}
    }
    taxonomy_arguments["query"] = query
    taxonomy_arguments.pop("q", None)
    resolved = await request.app.state.taxonomy.resolve_search(
        session,
        taxonomy_arguments,
        allow_long_canonical=allow_long_canonical,
    )
    typed_facet_names = {
        "occupation_ids",
        "industry_ids",
        "skill_ids",
        "language_ids",
        "location_id",
        "seniority_id",
        "seniority_ids",
        "work_modes",
        "open_to",
        "open_to_ids",
        "organization_ids",
        "representative_id",
        "representative_ids",
    }
    if not resolved.installed and typed_facet_names.intersection(facets):
        raise TaxonomyUnavailable("typed taxonomy facets require the installed public projection")
    empty_facets: dict[str, dict[str, int]] = {facet: {} for facet in facets}
    empty_taxonomy_facets: dict[str, list[TaxonomyFacetEntry]] = {
        facet: []
        for facet in facets
        if facet
        in {
            "occupation_ids",
            "industry_ids",
            "skill_ids",
            "language_ids",
            "location_id",
            "seniority_id",
            "seniority_ids",
            "work_modes",
            "open_to",
            "open_to_ids",
            "organization_ids",
            "representative_id",
            "representative_ids",
        }
    }
    if resolved.empty and mode != "exact":
        return SearchResponse(
            hits=[],
            offset=offset,
            limit=limit,
            total=0,
            indexing_available=True,
            warning=None,
            facets=empty_facets,
            taxonomy_facets=empty_taxonomy_facets,
            mode="projection",
            complete=False,
        )
    if mode == "exact":
        if offset != 0:
            raise ExactSearchCursorMalformed("exact search requires offset 0")
        try:
            exact_result: ExactSearchResult = await request.app.state.exact_search.search(
                session,
                arguments=arguments,
                resolved=resolved,
            )
        except ValueError as exc:
            if isinstance(
                exc,
                (ExactSearchCursorMalformed, ExactSearchCursorStale, ExactSearchTooBroad),
            ):
                raise
            raise TaxonomyInvalidValue(str(exc)) from exc
        exact_safe_hits = await request.app.state.taxonomy.hydrate_hits(
            session, exact_result.hits, resolved
        )
        if len(exact_safe_hits) != len(exact_result.hits):
            raise ExactSearchUnavailable("exact search canonical reauthorization failed")
        exact_safe_hits = await enrich_public_search_hits(
            session,
            exact_safe_hits,
            agent_capability=arguments.get("agent_capability"),
        )
        if len(exact_safe_hits) != len(exact_result.hits):
            raise ExactSearchUnavailable("exact search agent authorization changed")
        exact_facet_hits = await request.app.state.taxonomy.hydrate_hits(
            session, exact_result.facet_hits, resolved
        )
        if len(exact_facet_hits) != len(exact_result.facet_hits):
            raise ExactSearchUnavailable("exact search facet authorization changed")
        exact_facet_hits = await enrich_public_search_hits(
            session,
            exact_facet_hits,
            agent_capability=arguments.get("agent_capability"),
        )
        if len(exact_facet_hits) != len(exact_result.facet_hits):
            raise ExactSearchUnavailable("exact search facet agent authorization changed")
        taxonomy_facets = (
            await request.app.state.taxonomy.taxonomy_facets(session, exact_facet_hits, facets)
            if resolved.installed
            else {}
        )
        exact_facet_counts: dict[str, dict[str, int]] = {facet: {} for facet in facets}
        for facet in facets:
            counts = exact_facet_counts[facet]
            for hit in exact_facet_hits:
                raw = hit.get(facet)
                values = raw if isinstance(raw, list) else [raw]
                exact_seen_values: set[str] = set()
                for value in values:
                    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                        label = str(value)
                        if label in exact_seen_values:
                            continue
                        exact_seen_values.add(label)
                        counts[label] = counts.get(label, 0) + 1
        facet_truncated: dict[str, bool] = {}
        for facet in facets:
            ordered_counts = sorted(
                exact_facet_counts[facet].items(), key=lambda item: (-item[1], item[0])
            )
            facet_truncated[facet] = len(ordered_counts) > facet_limit
            exact_facet_counts[facet] = dict(ordered_counts[:facet_limit])
            entries = taxonomy_facets.get(facet, [])
            ordered_entries = sorted(
                entries,
                key=lambda entry: (-int(entry["count"]), str(entry["canonical_id"])),
            )
            if len(ordered_entries) > facet_limit:
                facet_truncated[facet] = True
            taxonomy_facets[facet] = ordered_entries[:facet_limit]
        page = exact_safe_hits[:limit]
        return SearchResponse(
            hits=[SearchHit.model_validate(hit) for hit in page],
            offset=0,
            limit=limit,
            total=exact_result.total,
            indexing_available=True,
            warning=None,
            facets=exact_facet_counts,
            taxonomy_facets=taxonomy_facets,
            mode="exact",
            next_cursor=exact_result.next_cursor,
            search_revision=exact_result.revision,
            complete=exact_result.complete,
            facet_truncated=facet_truncated,
        )
    search_method = request.app.state.search.search
    search_kwargs: dict[str, Any] = {
        "query": query,
        "kind": kind,
        "skills": skills,
        "location": location,
        "owner_id": None,
    }
    optional_filters = {
        "occupation_ids": resolved.meili.get("occupation_ids", []),
        "industry_ids": resolved.meili.get("industry_ids", []),
        "skill_ids": resolved.meili.get("skill_ids", []),
        "language_ids": resolved.meili.get("language_ids", []),
        "location_id": resolved.meili.get("location_id"),
        "location_country_code": resolved.meili.get("location_country_code"),
        "location_region": resolved.meili.get("location_region"),
        "location_city": resolved.meili.get("location_city"),
        "seniority_ids": resolved.meili.get("seniority_ids", []),
        "work_modes": resolved.meili.get("work_modes", []),
        "availability_status": resolved.meili.get("availability_status"),
        "availability_from": resolved.meili.get("availability_from"),
        "open_to_ids": resolved.meili.get("open_to_ids", []),
        "organization_ids": resolved.meili.get("organization_ids", []),
        "representative_ids": resolved.meili.get("representative_ids", []),
        "representation_status": resolved.meili.get("representation_status"),
        "contact_disclosure": resolved.meili.get("contact_disclosure"),
        "updated_after": resolved.meili.get("updated_after"),
        "updated_before": resolved.meili.get("updated_before"),
        "sort_updated": resolved.meili.get("sort_updated"),
    }
    parameters = signature(search_method).parameters
    accepts_keywords = any(
        parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    search_kwargs.update(
        {
            name: value
            for name, value in optional_filters.items()
            if accepts_keywords or name in parameters
        }
    )
    try:
        projection_result = await search_method(**search_kwargs)
    except SearchUnavailable:
        raise
    hits, _ = projection_result[:2]
    hit_ids = [hit.get("id") for hit in hits if isinstance(hit.get("id"), str)]
    rows = (
        (await session.scalars(select(Document).where(Document.id.in_(hit_ids)))).all()
        if hit_ids
        else []
    )
    authoritative = {row.id: row for row in rows}
    safe_hits: list[dict[str, Any]] = []
    for hit in hits:
        document = authoritative.get(hit.get("id"))
        if document is None or hit.get("version") != document.current_version:
            continue
        if (
            document.visibility != "public"
            or document.kind not in {"profile", "resume"}
            or (kind is not None and document.kind != kind)
            or hit.get("kind") != document.kind
        ):
            continue
        safe_hits.append(sanitized_search_hit(hit, document))
    candidate_window_reached = len(hits) >= MAX_AGENT_SEARCH_RESULTS
    safe_hits = await request.app.state.taxonomy.hydrate_hits(session, safe_hits, resolved)
    safe_hits = await enrich_public_search_hits(
        session,
        safe_hits,
        agent_capability=arguments.get("agent_capability"),
    )
    sort_updated = arguments.get("sort_updated")
    if sort_updated is not None:

        def authoritative_sort_key(hit: dict[str, Any]) -> tuple[datetime, str]:
            raw_updated = hit.get("updated_at")
            if not isinstance(raw_updated, str):
                return datetime.min.replace(tzinfo=UTC), str(hit.get("id", ""))
            try:
                updated = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                else:
                    updated = updated.astimezone(UTC)
            except ValueError:
                updated = datetime.min.replace(tzinfo=UTC)
            return updated, str(hit.get("id", ""))

        safe_hits = sorted(
            safe_hits,
            key=authoritative_sort_key,
            reverse=sort_updated == "desc",
        )
    taxonomy_facets = (
        await request.app.state.taxonomy.taxonomy_facets(session, safe_hits, facets)
        if resolved.installed
        else {}
    )
    facet_counts: dict[str, dict[str, int]] = {facet: {} for facet in facets}
    for facet in facets:
        counts = facet_counts[facet]
        for hit in safe_hits:
            raw = hit.get(facet)
            values = raw if isinstance(raw, list) else [raw]
            seen_values: set[str] = set()
            for value in values:
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    label = str(value)
                    if label in seen_values:
                        continue
                    seen_values.add(label)
                    counts[label] = counts.get(label, 0) + 1
    warnings: list[str] = []
    if candidate_window_reached:
        warnings.append(
            "results reached the searchable 1050-document candidate window; "
            "totals and completeness are bounded to that window; narrow the query "
            "for a more complete count"
        )
    page = safe_hits[offset : offset + limit]
    return SearchResponse(
        hits=[SearchHit.model_validate(hit) for hit in page],
        offset=offset,
        limit=limit,
        total=len(safe_hits),
        indexing_available=True,
        warning="; ".join(warnings) or None,
        facets=facet_counts,
        taxonomy_facets=taxonomy_facets,
    )


def rest_search_unavailable(exc: SearchUnavailable, *, offset: int, limit: int) -> SearchResponse:
    return SearchResponse(
        hits=[],
        offset=offset,
        limit=limit,
        total=0,
        indexing_available=False,
        warning=str(exc),
        facets={},
        taxonomy_facets={},
    )
