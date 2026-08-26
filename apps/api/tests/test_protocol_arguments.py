from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import HTTPException

from app.protocol_arguments import (
    mcp_agent_outreach_arguments,
    mcp_agent_outreach_status_argument,
    mcp_create_arguments,
    mcp_get_changes_arguments,
    mcp_idempotency_argument,
    mcp_list_my_documents_arguments,
    mcp_raw_markdown_is_bounded,
    mcp_read_document_arguments,
    mcp_update_arguments,
    protocol_agent_directory_arguments,
    protocol_agent_identity_argument,
    protocol_profile_agents_arguments,
    protocol_search_arguments,
)

MAX_UPLOAD_BYTES = 16


def search_arguments(arguments: dict[str, object]) -> dict[str, object]:
    return protocol_search_arguments(
        arguments,
        internal_contact_request_capability="internal_contact_request",
        max_repeated_values=50,
    )


def assert_http_error(parser: Callable[[], object], *, status_code: int, detail: str) -> None:
    with pytest.raises(HTTPException) as raised:
        parser()
    assert raised.value.status_code == status_code
    assert raised.value.detail == detail


def test_search_defaults_alias_and_normalization() -> None:
    normalized = search_arguments(
        {
            "query": "  platform engineer ",
            "kind": "profile",
            "location": "  Singapore ",
            "skills": [" api ", "python"],
            "offset": 2,
            "limit": 5,
            "facet_limit": 7,
            "agent_capability": "internal_contact_request",
        }
    )

    assert normalized["q"] == "platform engineer"
    assert normalized["kind"] == "profile"
    assert normalized["location"] == "Singapore"
    assert normalized["skills"] == ["api", "python"]
    assert normalized["offset"] == 2
    assert normalized["limit"] == 5
    assert normalized["facet_limit"] == 7
    assert normalized["agent_capability"] == "internal_contact_request"
    assert normalized["mode"] == "projection"
    assert normalized["occupation_ids"] == []


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"q": "one", "query": "two"}, "q and query cannot both be supplied"),
        ({"unknown": True}, "search contains unknown fields"),
        ({"offset": True}, "search fields are invalid"),
        ({"limit": False}, "search fields are invalid"),
        ({"facet_limit": True}, "search fields are invalid"),
        (
            {"occupation_ids": ["a"] * 26, "skill_ids": ["b"] * 25},
            "search fields are invalid",
        ),
        (
            {"agent_capability": "external_contact"},
            "search fields are invalid",
        ),
    ],
)
def test_search_argument_boundaries_fail_closed(arguments: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        search_arguments(arguments)


@pytest.mark.parametrize(
    ("parser", "arguments", "expected"),
    [
        (
            protocol_profile_agents_arguments,
            {"profile_handle": " ada-lovelace ", "limit": 3, "cursor": "next"},
            ("ada-lovelace", 3, "next"),
        ),
        (
            protocol_agent_directory_arguments,
            {"q": "  ada ", "profile_handle": " profile ", "limit": 4},
            ("ada", "profile", 4, None),
        ),
        (protocol_agent_identity_argument, {"agent_handle": "ada-lovelace"}, "ada-lovelace"),
    ],
)
def test_agent_discovery_arguments_normalize(
    parser: Callable[[dict[str, object]], object],
    arguments: dict[str, object],
    expected: object,
) -> None:
    assert parser(arguments) == expected


@pytest.mark.parametrize(
    ("parser", "arguments", "message"),
    [
        (
            protocol_profile_agents_arguments,
            {"profile_handle": "ada", "limit": True},
            "profile-agent listing fields are invalid",
        ),
        (
            protocol_agent_directory_arguments,
            {"q": "a" * 101},
            "agent-directory listing fields are invalid",
        ),
        (
            protocol_agent_identity_argument,
            {"agent_handle": "Ada"},
            "agent identity handle is invalid",
        ),
    ],
)
def test_agent_discovery_argument_boundaries(
    parser: Callable[[dict[str, object]], object],
    arguments: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parser(arguments)


@pytest.mark.parametrize(
    ("arguments", "status_code", "detail"),
    [
        ({}, 428, "Idempotency-Key is required for this operation"),
        (
            {"idempotency_key": "contains space"},
            400,
            "Idempotency-Key must contain 1-128 visible ASCII characters",
        ),
        (
            {"idempotency_key": "é"},
            400,
            "Idempotency-Key must contain 1-128 visible ASCII characters",
        ),
    ],
)
def test_mcp_idempotency_argument_boundaries(
    arguments: dict[str, object], status_code: int, detail: str
) -> None:
    assert_http_error(
        lambda: mcp_idempotency_argument(arguments), status_code=status_code, detail=detail
    )
    assert mcp_idempotency_argument({"idempotency_key": "mcp-valid-key-0001"}) == (
        "mcp-valid-key-0001"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a" * 16, True),
        ("é" * 8, True),
        ("é" * 9, False),
        ("😀" * 4, True),
        ("😀" * 5, False),
        ("", False),
        ("\ud800", False),
        (123, False),
    ],
)
def test_raw_markdown_uses_utf8_bytes(value: object, expected: bool) -> None:
    assert mcp_raw_markdown_is_bounded(value, max_upload_bytes=MAX_UPLOAD_BYTES) is expected


def test_mcp_create_arguments_preserve_schema_and_namespace() -> None:
    arguments = {
        "kind": "profile",
        "markdown": "# Profile",
        "idempotency_key": "mcp-create-profile-0001",
    }
    assert mcp_create_arguments(arguments, max_upload_bytes=MAX_UPLOAD_BYTES) == (
        "profile",
        "# Profile",
        "mcp-create-profile-0001",
    )
    assert_http_error(
        lambda: mcp_create_arguments(
            {"kind": "profile", "markdown": "# Profile"}, max_upload_bytes=MAX_UPLOAD_BYTES
        ),
        status_code=428,
        detail="Idempotency-Key is required for this operation",
    )
    assert_http_error(
        lambda: mcp_create_arguments(
            {**arguments, "extra": True}, max_upload_bytes=MAX_UPLOAD_BYTES
        ),
        status_code=422,
        detail="create_document arguments do not match its advertised schema",
    )
    assert_http_error(
        lambda: mcp_create_arguments(
            {**arguments, "markdown": "x" * 17}, max_upload_bytes=MAX_UPLOAD_BYTES
        ),
        status_code=422,
        detail="kind and markdown must be bounded valid values",
    )


def test_mcp_update_arguments_preserve_if_match_and_operation_details() -> None:
    base = {
        "kind": "resume",
        "identifier": "ada-resume",
        "markdown": "# Resume",
        "if_match": '"sha256-' + "a" * 64 + '"',
        "idempotency_key": "mcp-update-resume-0001",
    }
    assert mcp_update_arguments(
        base, operation="update_document", max_upload_bytes=MAX_UPLOAD_BYTES
    ) == ("resume", "ada-resume", "# Resume", base["if_match"], base["idempotency_key"])
    assert_http_error(
        lambda: mcp_update_arguments(
            {key: value for key, value in base.items() if key != "if_match"},
            operation="update_document",
            max_upload_bytes=MAX_UPLOAD_BYTES,
        ),
        status_code=428,
        detail="If-Match is required to update resume",
    )
    assert_http_error(
        lambda: mcp_update_arguments(
            {key: value for key, value in base.items() if key != "if_match"},
            operation="propose_document_update",
            max_upload_bytes=MAX_UPLOAD_BYTES,
        ),
        status_code=422,
        detail="if_match is required for this proposal",
    )
    assert_http_error(
        lambda: mcp_update_arguments(
            {**base, "if_match": "*"},
            operation="update_document",
            max_upload_bytes=MAX_UPLOAD_BYTES,
        ),
        status_code=422,
        detail="if_match must be an exact strong document ETag",
    )


@pytest.mark.parametrize(
    ("parser", "arguments", "expected"),
    [
        (mcp_list_my_documents_arguments, {}, (None, 25, None)),
        (mcp_list_my_documents_arguments, {"kind": "profile", "limit": 2}, ("profile", 2, None)),
        (
            mcp_read_document_arguments,
            {"kind": "resume", "identifier": "ada-resume"},
            ("resume", "ada-resume"),
        ),
        (mcp_get_changes_arguments, {}, (0, 50)),
        (mcp_get_changes_arguments, {"after_sequence": 3, "limit": 4}, (3, 4)),
    ],
)
def test_mcp_inventory_read_and_change_defaults(
    parser: Callable[[dict[str, object]], object],
    arguments: dict[str, object],
    expected: object,
) -> None:
    assert parser(arguments) == expected


@pytest.mark.parametrize(
    ("parser", "arguments", "status_code", "detail"),
    [
        (mcp_list_my_documents_arguments, {"limit": True}, 422, "limit must be between 1 and 100"),
        (
            mcp_read_document_arguments,
            {"kind": "profile"},
            422,
            "read_document arguments do not match its advertised schema",
        ),
        (
            mcp_get_changes_arguments,
            {"after_sequence": True},
            422,
            "after_sequence and limit must match their advertised bounds",
        ),
    ],
)
def test_mcp_inventory_read_and_change_boundaries(
    parser: Callable[[dict[str, object]], object],
    arguments: dict[str, object],
    status_code: int,
    detail: str,
) -> None:
    assert_http_error(lambda: parser(arguments), status_code=status_code, detail=detail)


def test_mcp_outreach_arguments_validate_body_and_canonical_request_id() -> None:
    valid = {
        "target_agent_handle": "ada-agent",
        "purpose": "Project collaboration",
        "message": "Would you like to compare notes?",
        "idempotency_key": "mcp-outreach-0001",
    }
    body, key = mcp_agent_outreach_arguments(valid)
    assert body.target_agent_handle == "ada-agent"
    assert body.purpose == "Project collaboration"
    assert body.message == "Would you like to compare notes?"
    assert key == "mcp-outreach-0001"
    request_id = "00000000-0000-4000-8000-00000000000a"
    assert mcp_agent_outreach_status_argument({"request_id": request_id}) == request_id
    assert_http_error(
        lambda: mcp_agent_outreach_arguments({**valid, "message": ""}),
        status_code=422,
        detail="agent outreach arguments are invalid",
    )
    assert_http_error(
        lambda: mcp_agent_outreach_status_argument({"request_id": request_id.upper()}),
        status_code=422,
        detail="request_id must be a UUID",
    )
    assert_http_error(
        lambda: mcp_agent_outreach_status_argument({}),
        status_code=422,
        detail="get_agent_outreach_status arguments do not match its advertised schema",
    )
