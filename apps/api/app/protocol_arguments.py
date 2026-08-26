"""Pure argument validation shared by the HTTP, MCP, and A2A protocol edges."""

from __future__ import annotations

import re
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas import AgentOutreachCreate
from app.services.documents import STRONG_DOCUMENT_ETAG_PATTERN

DocumentKind = Literal["profile", "resume"]

IDEMPOTENCY_KEY_PATTERN = r"^[\x21-\x7E]{1,128}$"
IDEMPOTENCY_KEY_RE = re.compile(IDEMPOTENCY_KEY_PATTERN)


def canonical_agent_outreach_request_id(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("agent outreach request id must be a canonical UUID")
    try:
        normalized = str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("agent outreach request id must be a canonical UUID") from exc
    if normalized != value:
        raise ValueError("agent outreach request id must be a canonical UUID")
    return value


def _ensure_search_repeated_value_cap(
    arguments: dict[str, Any], *, max_repeated_values: int
) -> None:
    repeated_values = sum(len(value) for value in arguments.values() if isinstance(value, list))
    if arguments.get("seniority_id") is not None:
        repeated_values += 1
    if repeated_values > max_repeated_values:
        raise ValueError("search contains too many repeated taxonomy values")


def protocol_search_arguments(
    arguments: dict[str, Any],
    *,
    internal_contact_request_capability: str,
    max_repeated_values: int,
) -> dict[str, Any]:
    """Validate the shared structured search envelope used by MCP and A2A."""
    if not isinstance(arguments, dict):
        raise ValueError("search fields are invalid")
    allowed = {
        "mode",
        "q",
        "query",
        "kind",
        "skills",
        "location",
        "occupation_ids",
        "industry_ids",
        "skill_ids",
        "language_ids",
        "location_id",
        "location_country_code",
        "location_region",
        "location_city",
        "seniority_ids",
        "seniority_id",
        "work_modes",
        "availability_status",
        "availability_from",
        "open_to",
        "open_to_ids",
        "organization_ids",
        "representative_ids",
        "representation_status",
        "contact_disclosure",
        "agent_capability",
        "updated_after",
        "updated_before",
        "sort_updated",
        "facets",
        "offset",
        "limit",
        "cursor",
        "facet_limit",
    }
    if set(arguments) - allowed:
        raise ValueError("search contains unknown fields")
    if "q" in arguments and "query" in arguments:
        raise ValueError("q and query cannot both be supplied")
    raw_query = arguments.get("q", arguments.get("query", ""))
    if not isinstance(raw_query, str) or len(raw_query) > 200:
        raise ValueError("search fields are invalid")
    query = raw_query.strip()
    kind = arguments.get("kind")
    location = arguments.get("location")
    if kind not in {None, "profile", "resume"} or (
        location is not None
        and (not isinstance(location, str) or not 0 <= len(location.strip()) <= 160)
    ):
        raise ValueError("search fields are invalid")
    if location is not None:
        location = location.strip()

    mode = arguments.get("mode", "projection")
    if mode not in {"projection", "exact"}:
        raise ValueError("search fields are invalid")
    normalized: dict[str, Any] = {
        "mode": mode,
        "q": query,
        "kind": kind,
        "location": location,
    }
    list_bounds = {
        "skills": (50, 80),
        "occupation_ids": (50, 336),
        "industry_ids": (50, 336),
        "skill_ids": (50, 336),
        "language_ids": (50, 336),
        "seniority_ids": (50, 336),
        "work_modes": (20, 80),
        "open_to": (50, 336),
        "open_to_ids": (50, 336),
        "organization_ids": (50, 336),
        "representative_ids": (50, 336),
        "facets": (30, 80),
    }
    for name, (maximum, value_length) in list_bounds.items():
        value = arguments.get(name, [])
        if (
            not isinstance(value, list)
            or len(value) > maximum
            or not all(
                isinstance(item, str) and 0 < len(item.strip()) <= value_length for item in value
            )
        ):
            raise ValueError("search fields are invalid")
        normalized[name] = [item.strip() for item in value]

    try:
        cap_arguments = dict(normalized)
        if arguments.get("seniority_id") is not None:
            cap_arguments["seniority_id"] = arguments.get("seniority_id")
        _ensure_search_repeated_value_cap(cap_arguments, max_repeated_values=max_repeated_values)
    except ValueError as exc:
        raise ValueError("search fields are invalid") from exc

    scalar_bounds = {
        "location_id": 336,
        "location_region": 160,
        "location_city": 160,
        "seniority_id": 336,
        "availability_status": 80,
        "availability_from": 40,
        "representation_status": 80,
        "contact_disclosure": 80,
        "updated_after": 40,
        "updated_before": 40,
    }
    for name, maximum in scalar_bounds.items():
        value = arguments.get(name)
        if value is not None and (
            not isinstance(value, str) or not 0 < len(value.strip()) <= maximum
        ):
            raise ValueError("search fields are invalid")
        normalized[name] = value.strip() if isinstance(value, str) else value
    location_country_code = arguments.get("location_country_code")
    if location_country_code is not None and (
        not isinstance(location_country_code, str)
        or not 0 < len(location_country_code.strip()) <= 3
    ):
        raise ValueError("search fields are invalid")
    normalized["location_country_code"] = (
        location_country_code.strip() if isinstance(location_country_code, str) else None
    )
    normalized["sort_updated"] = arguments.get("sort_updated")
    if normalized["sort_updated"] not in {None, "asc", "desc"}:
        raise ValueError("search fields are invalid")
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", 20)
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= 1000
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 50
    ):
        raise ValueError("search fields are invalid")
    normalized["offset"] = offset
    normalized["limit"] = limit
    cursor = arguments.get("cursor")
    if cursor is not None and (
        not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048
    ):
        raise ValueError("search fields are invalid")
    normalized["cursor"] = cursor
    facet_limit = arguments.get("facet_limit", 100)
    if (
        isinstance(facet_limit, bool)
        or not isinstance(facet_limit, int)
        or not 1 <= facet_limit <= 500
    ):
        raise ValueError("search fields are invalid")
    normalized["facet_limit"] = facet_limit
    agent_capability = arguments.get("agent_capability")
    if agent_capability is not None and agent_capability != internal_contact_request_capability:
        raise ValueError("search fields are invalid")
    normalized["agent_capability"] = agent_capability
    return normalized


def protocol_profile_agents_arguments(arguments: dict[str, Any]) -> tuple[str, int, str | None]:
    allowed = {"profile_handle", "limit", "cursor"}
    if set(arguments) - allowed:
        raise ValueError("profile-agent listing contains unknown fields")
    profile_handle = arguments.get("profile_handle")
    limit = arguments.get("limit", 20)
    cursor = arguments.get("cursor")
    if (
        not isinstance(profile_handle, str)
        or not 0 < len(profile_handle) <= 100
        or not profile_handle.strip()
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 50
        or (
            cursor is not None
            and (not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 500)
        )
    ):
        raise ValueError("profile-agent listing fields are invalid")
    return profile_handle.strip(), limit, cursor


def protocol_agent_directory_arguments(
    arguments: dict[str, Any],
) -> tuple[str, str | None, int, str | None]:
    allowed = {"q", "profile_handle", "limit", "cursor"}
    if set(arguments) - allowed:
        raise ValueError("agent-directory listing contains unknown fields")
    query = arguments.get("q", "")
    profile_handle = arguments.get("profile_handle")
    limit = arguments.get("limit", 20)
    cursor = arguments.get("cursor")
    if (
        not isinstance(query, str)
        or len(query) > 100
        or (
            profile_handle is not None
            and (not isinstance(profile_handle, str) or not 0 < len(profile_handle.strip()) <= 100)
        )
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 50
        or (
            cursor is not None
            and (not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 500)
        )
    ):
        raise ValueError("agent-directory listing fields are invalid")
    return (
        query.strip(),
        profile_handle.strip() if profile_handle is not None else None,
        limit,
        cursor,
    )


def protocol_agent_identity_argument(arguments: dict[str, Any]) -> str:
    if set(arguments) != {"agent_handle"}:
        raise ValueError("agent identity lookup contains unknown fields")
    handle = arguments.get("agent_handle")
    if (
        not isinstance(handle, str)
        or not 0 < len(handle) <= 100
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?", handle) is None
    ):
        raise ValueError("agent identity handle is invalid")
    return handle


def mcp_idempotency_argument(arguments: dict[str, Any]) -> str:
    if "idempotency_key" not in arguments:
        raise HTTPException(
            status_code=428,
            detail="Idempotency-Key is required for this operation",
        )
    key = arguments["idempotency_key"]
    if not isinstance(key, str) or not IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 1-128 visible ASCII characters",
        )
    return key


def mcp_raw_markdown_is_bounded(value: Any, *, max_upload_bytes: int) -> bool:
    if not isinstance(value, str):
        return False
    try:
        byte_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return 1 <= byte_length <= max_upload_bytes


def mcp_create_arguments(
    arguments: dict[str, Any], *, max_upload_bytes: int
) -> tuple[DocumentKind, str, str]:
    if "idempotency_key" not in arguments:
        mcp_idempotency_argument(arguments)
    if set(arguments) != {"kind", "markdown", "idempotency_key"}:
        raise HTTPException(
            status_code=422,
            detail="create_document arguments do not match its advertised schema",
        )
    kind = arguments["kind"]
    markdown = arguments["markdown"]
    if (
        not isinstance(kind, str)
        or kind not in {"profile", "resume"}
        or not mcp_raw_markdown_is_bounded(markdown, max_upload_bytes=max_upload_bytes)
    ):
        raise HTTPException(
            status_code=422,
            detail="kind and markdown must be bounded valid values",
        )
    return cast(DocumentKind, kind), markdown, mcp_idempotency_argument(arguments)


def mcp_update_arguments(
    arguments: dict[str, Any], *, operation: str, max_upload_bytes: int
) -> tuple[DocumentKind, str, str, str, str]:
    if "idempotency_key" not in arguments:
        mcp_idempotency_argument(arguments)
    if "if_match" not in arguments:
        kind = arguments.get("kind")
        status_code = 428 if operation == "update_document" else 422
        detail = (
            f"If-Match is required to update {kind}"
            if status_code == 428 and kind in {"profile", "resume"}
            else "if_match is required for this proposal"
        )
        raise HTTPException(status_code=status_code, detail=detail)
    if set(arguments) != {"kind", "identifier", "markdown", "if_match", "idempotency_key"}:
        raise HTTPException(
            status_code=422,
            detail=f"{operation} arguments do not match its advertised schema",
        )
    kind = arguments["kind"]
    identifier = arguments["identifier"]
    markdown = arguments["markdown"]
    if_match = arguments["if_match"]
    if (
        not isinstance(kind, str)
        or kind not in {"profile", "resume"}
        or not isinstance(identifier, str)
        or not 1 <= len(identifier) <= 100
        or not mcp_raw_markdown_is_bounded(markdown, max_upload_bytes=max_upload_bytes)
    ):
        raise HTTPException(
            status_code=422,
            detail="kind, identifier, and markdown must be bounded valid values",
        )
    if not (
        isinstance(if_match, str)
        and re.fullmatch(STRONG_DOCUMENT_ETAG_PATTERN, if_match) is not None
    ):
        raise HTTPException(
            status_code=422,
            detail="if_match must be an exact strong document ETag",
        )
    return (
        cast(DocumentKind, kind),
        identifier,
        markdown,
        if_match,
        mcp_idempotency_argument(arguments),
    )


def mcp_list_my_documents_arguments(
    arguments: dict[str, Any],
) -> tuple[DocumentKind | None, int, str | None]:
    allowed = {"kind", "limit", "cursor"}
    if set(arguments) - allowed:
        raise HTTPException(
            status_code=422,
            detail="list_my_documents arguments do not match its advertised schema",
        )
    kind = arguments.get("kind")
    limit = arguments.get("limit", 25)
    cursor = arguments.get("cursor")
    if kind is not None and kind not in {"profile", "resume"}:
        raise HTTPException(status_code=422, detail="kind must be profile or resume")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 500):
        raise HTTPException(status_code=422, detail="cursor is malformed")
    return cast(DocumentKind | None, kind), limit, cursor


def mcp_read_document_arguments(arguments: dict[str, Any]) -> tuple[DocumentKind, str]:
    if set(arguments) != {"kind", "identifier"}:
        raise HTTPException(
            status_code=422,
            detail="read_document arguments do not match its advertised schema",
        )
    kind = arguments["kind"]
    identifier = arguments["identifier"]
    if (
        not isinstance(kind, str)
        or kind not in {"profile", "resume"}
        or not isinstance(identifier, str)
        or not 1 <= len(identifier) <= 100
    ):
        raise HTTPException(
            status_code=422,
            detail="kind and identifier must be bounded valid values",
        )
    return cast(DocumentKind, kind), identifier


def mcp_get_changes_arguments(arguments: dict[str, Any]) -> tuple[int, int]:
    if set(arguments) - {"after_sequence", "limit"}:
        raise HTTPException(
            status_code=422,
            detail="get_changes arguments do not match its advertised schema",
        )
    after_sequence = arguments.get("after_sequence", 0)
    limit = arguments.get("limit", 50)
    if (
        isinstance(after_sequence, bool)
        or not isinstance(after_sequence, int)
        or after_sequence < 0
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
    ):
        raise HTTPException(
            status_code=422,
            detail="after_sequence and limit must match their advertised bounds",
        )
    return after_sequence, limit


def mcp_agent_outreach_arguments(
    arguments: dict[str, Any],
) -> tuple[AgentOutreachCreate, str]:
    key = mcp_idempotency_argument(arguments)
    if set(arguments) != {
        "target_agent_handle",
        "purpose",
        "message",
        "idempotency_key",
    }:
        raise HTTPException(
            status_code=422,
            detail="send_agent_outreach arguments do not match its advertised schema",
        )
    try:
        body = AgentOutreachCreate.model_validate(
            {
                "target_agent_handle": arguments["target_agent_handle"],
                "purpose": arguments["purpose"],
                "message": arguments["message"],
            }
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="agent outreach arguments are invalid",
        ) from exc
    return body, key


def mcp_agent_outreach_status_argument(arguments: dict[str, Any]) -> str:
    if set(arguments) != {"request_id"}:
        raise HTTPException(
            status_code=422,
            detail="get_agent_outreach_status arguments do not match its advertised schema",
        )
    try:
        return canonical_agent_outreach_request_id(arguments["request_id"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="request_id must be a UUID") from exc
