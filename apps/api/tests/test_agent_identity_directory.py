from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.main import create_app
from app.models import AgentIdentity, Document, PublicTaxonomyTerm

from .helpers import profile_markdown, resume_markdown
from .test_taxonomy import _install_ready, _profile_v2_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_human(app, subject: str) -> None:
    async def current() -> Principal:
        return human(subject)

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def create_public_identity(app, client, *, owner: str, profile: str, agent: str) -> None:
    as_human(app, owner)
    created_profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", profile)},
        headers={"Idempotency-Key": f"agent-directory-profile-{profile}"},
    )
    assert created_profile.status_code == 201, created_profile.text
    created_identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": agent,
            "display_name": f"{agent} display",
            "description": f"{agent} handles consent-gated internal contact.",
            "profile_handle": profile,
        },
        headers={"Idempotency-Key": f"agent-directory-create-{agent}"},
    )
    assert created_identity.status_code == 201, created_identity.text


async def test_agent_directory_is_public_bounded_and_hides_ineligible_identities(
    api_client,
) -> None:
    app, client = api_client
    await create_public_identity(
        app, client, owner="public-owner", profile="public-profile", agent="public-agent-one"
    )
    as_human(app, "public-owner")
    second = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "public-agent-two",
            "display_name": "public-agent-two display",
            "description": "public-agent-two handles consent-gated internal contact.",
            "profile_handle": "public-profile",
        },
        headers={"Idempotency-Key": "agent-directory-create-public-agent-two"},
    )
    assert second.status_code == 201, second.text
    await create_public_identity(
        app,
        client,
        owner="withdrawn-owner",
        profile="withdrawn-profile",
        agent="withdrawn-agent",
    )
    withdrawn = await client.delete(
        "/v1/agent-identities/withdrawn-agent",
        headers={"Idempotency-Key": "agent-directory-withdrawn-agent-0001"},
    )
    assert withdrawn.status_code == 204
    await create_public_identity(
        app,
        client,
        owner="withheld-owner",
        profile="withheld-profile",
        agent="withheld-agent",
    )
    await create_public_identity(
        app,
        client,
        owner="private-owner",
        profile="private-profile",
        agent="private-agent",
    )
    async with app.state.session_factory() as session:
        withheld = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "withheld-agent")
        )
        private_profile = await session.scalar(
            select(Document).where(Document.public_identifier == "private-profile")
        )
        assert withheld is not None and private_profile is not None
        withheld.status = "withheld"
        private_profile.visibility = "private"
        await session.commit()

    first = await client.get("/v1/agent-directory", params={"q": "agent", "limit": 1})
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["identities"]) == 1
    assert first_body["next_cursor"]
    assert set(first_body["identities"][0]) == {
        "handle",
        "display_name",
        "description",
        "profile_handle",
        "capabilities",
    }
    assert {"owner_id", "grant", "mandate", "status", "presence", "external_endpoint"}.isdisjoint(
        first_body["identities"][0]
    )
    cursor = first_body["next_cursor"]
    second_page = await client.get(
        "/v1/agent-directory", params={"q": "agent", "limit": 1, "cursor": cursor}
    )
    assert second_page.status_code == 200, second_page.text
    assert second_page.json()["identities"][0]["handle"] != first_body["identities"][0]["handle"]
    all_handles = {
        first_body["identities"][0]["handle"],
        second_page.json()["identities"][0]["handle"],
    }
    assert all_handles == {"public-agent-one", "public-agent-two"}
    assert second_page.json()["next_cursor"] is None
    duplicate_directory_cursor = await client.get(
        "/v1/agent-directory",
        params=[("cursor", cursor), ("cursor", cursor)],
    )
    assert duplicate_directory_cursor.status_code == 422

    assert (
        await client.get("/v1/agent-directory", params={"q": "other", "cursor": cursor})
    ).status_code == 400
    assert (
        await client.get(
            "/v1/agent-directory",
            params={"q": "agent", "profile_handle": "public-profile", "cursor": cursor},
        )
    ).status_code == 400
    tampered = f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}"
    assert (
        await client.get("/v1/agent-directory", params={"q": "agent", "cursor": tampered})
    ).status_code == 400
    assert (
        await client.get("/v1/agent-directory", params={"cursor": "x" * 501})
    ).status_code == 422
    assert (
        await client.get("/v1/agent-directory", params={"unexpected": "value"})
    ).status_code == 422

    for supplied_cursor, expected_status in (("", 422), (" \t ", 400)):
        assert (
            await client.get("/v1/agent-directory", params={"cursor": supplied_cursor})
        ).status_code == expected_status

    profile_agents = await client.get("/v1/profiles/public-profile/agent-identities")
    assert profile_agents.status_code == 200, profile_agents.text
    assert {item["handle"] for item in profile_agents.json()["identities"]} == all_handles
    profile_page = await client.get(
        "/v1/profiles/public-profile/agent-identities", params={"limit": 1}
    )
    profile_cursor = profile_page.json()["next_cursor"]
    assert profile_cursor is not None
    duplicate_profile_cursor = await client.get(
        "/v1/profiles/public-profile/agent-identities",
        params=[("cursor", profile_cursor), ("cursor", profile_cursor)],
    )
    assert duplicate_profile_cursor.status_code == 422
    assert (await client.get("/v1/profiles/private-profile/agent-identities")).status_code == 404
    assert (
        await client.get(
            "/v1/profiles/public-profile/agent-identities", params={"unexpected": "value"}
        )
    ).status_code == 422
    for supplied_cursor, expected_status in (("", 422), (" \t ", 400)):
        assert (
            await client.get(
                "/v1/profiles/public-profile/agent-identities",
                params={"cursor": supplied_cursor},
            )
        ).status_code == expected_status
    hidden = await client.get("/v1/agent-directory", params={"q": "agent", "limit": 50})
    assert hidden.status_code == 200
    assert {item["handle"] for item in hidden.json()["identities"]} == all_handles

    openapi = (await client.get("/openapi.json")).json()
    for path in (
        "/v1/agent-directory",
        "/v1/profiles/{handle}/agent-identities",
    ):
        parameters = {
            parameter["name"]: parameter
            for parameter in openapi["paths"][path]["get"]["parameters"]
        }
        assert parameters["cursor"]["schema"]["anyOf"][0]["minLength"] == 1
        assert parameters["cursor"]["schema"]["anyOf"][0]["maxLength"] == 500
    response_cursor_schema = openapi["components"]["schemas"]["AgentIdentityDirectoryResponse"][
        "properties"
    ]["next_cursor"]["anyOf"][0]
    assert response_cursor_schema["minLength"] == 1
    assert response_cursor_schema["maxLength"] == 500

    restarted = create_app(app.state.settings)
    try:
        transport = ASGITransport(app=restarted)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as restarted_client:
            continued = await restarted_client.get(
                "/v1/agent-directory", params={"q": "agent", "limit": 1, "cursor": cursor}
            )
        assert continued.status_code == 200, continued.text
        assert continued.json()["identities"][0]["handle"] in all_handles
        assert continued.json()["identities"][0]["handle"] != first_body["identities"][0]["handle"]
    finally:
        await restarted.state.engine.dispose()


async def test_global_agent_directory_http_mcp_a2a_parity_and_privacy(api_client) -> None:
    app, client = api_client
    await create_public_identity(
        app,
        client,
        owner="global-public-owner",
        profile="global-public-profile-one",
        agent="global-agent-one",
    )
    await create_public_identity(
        app,
        client,
        owner="global-public-owner-two",
        profile="global-public-profile-two",
        agent="global-agent-two",
    )
    await create_public_identity(
        app,
        client,
        owner="global-withdrawn-owner",
        profile="global-withdrawn-profile",
        agent="global-withdrawn-agent",
    )
    assert (
        await client.delete(
            "/v1/agent-identities/global-withdrawn-agent",
            headers={"Idempotency-Key": "agent-directory-global-withdraw-0001"},
        )
    ).status_code == 204
    await create_public_identity(
        app,
        client,
        owner="global-private-owner",
        profile="global-private-profile",
        agent="global-private-agent",
    )
    await create_public_identity(
        app,
        client,
        owner="global-mismatch-owner",
        profile="global-mismatch-profile",
        agent="global-mismatch-agent",
    )
    async with app.state.session_factory() as session:
        private_profile = await session.scalar(
            select(Document).where(Document.public_identifier == "global-private-profile")
        )
        mismatched_identity = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "global-mismatch-agent")
        )
        assert private_profile is not None and mismatched_identity is not None
        private_profile.visibility = "private"
        mismatched_identity.owner_id = "a-different-owner"
        await session.commit()

    http = await client.get("/v1/agent-directory", params={"q": "global-agent", "limit": 1})
    assert http.status_code == 200, http.text
    http_body = http.json()
    assert http_body["identities"]
    assert http_body["next_cursor"]

    mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "global-directory-mcp",
            "method": "tools/call",
            "params": {
                "name": "list_agent_directory",
                "arguments": {"q": "global-agent", "limit": 1, "cursor": None},
            },
        },
    )
    assert mcp.status_code == 200, mcp.text
    assert mcp.json()["result"]["structuredContent"] == http_body

    a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "global-directory-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "list_agent_directory",
                            "q": "global-agent",
                            "limit": 1,
                            "cursor": None,
                        }
                    }
                ],
            }
        },
    )
    assert a2a.status_code == 200, a2a.text
    assert a2a.json()["task"]["artifacts"][0]["parts"][0]["data"] == http_body

    all_public = await client.get("/v1/agent-directory", params={"q": "global-agent", "limit": 50})
    assert all_public.status_code == 200
    all_body = all_public.json()
    assert {item["handle"] for item in all_body["identities"]} == {
        "global-agent-one",
        "global-agent-two",
    }
    for item in all_body["identities"]:
        assert set(item) == {
            "handle",
            "display_name",
            "description",
            "profile_handle",
            "capabilities",
        }
        assert item["capabilities"] == ["internal_contact_request"]
        assert not {
            "owner_id",
            "grant",
            "mandate",
            "status",
            "presence",
            "external_endpoint",
            "contacts:write",
        }.intersection(item)

    cursor = http_body["next_cursor"]
    assert cursor is not None
    http_next = await client.get(
        "/v1/agent-directory",
        params={"q": "global-agent", "limit": 1, "cursor": cursor},
    )
    mcp_next = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "global-directory-next-mcp",
            "method": "tools/call",
            "params": {
                "name": "list_agent_directory",
                "arguments": {"q": "global-agent", "limit": 1, "cursor": cursor},
            },
        },
    )
    a2a_next = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "global-directory-next-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "list_agent_directory",
                            "q": "global-agent",
                            "limit": 1,
                            "cursor": cursor,
                        }
                    }
                ],
            }
        },
    )
    assert http_next.status_code == 200
    assert mcp_next.json()["result"]["structuredContent"] == http_next.json()
    assert a2a_next.json()["task"]["artifacts"][0]["parts"][0]["data"] == http_next.json()

    for index, supplied_cursor in enumerate(("", " \t ")):
        blank_mcp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": f"global-directory-blank-mcp-{index}",
                "method": "tools/call",
                "params": {
                    "name": "list_agent_directory",
                    "arguments": {"cursor": supplied_cursor},
                },
            },
        )
        assert blank_mcp.json()["result"]["isError"] is True
        assert blank_mcp.json()["result"]["structuredContent"]["code"] == "validation_failed"

        blank_a2a = await client.post(
            "/a2a/message:send",
            headers={"A2A-Version": "1.0"},
            json={
                "message": {
                    "messageId": f"global-directory-blank-a2a-{index}",
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "data": {
                                "action": "list_agent_directory",
                                "cursor": supplied_cursor,
                            }
                        }
                    ],
                }
            },
        )
        assert blank_a2a.json()["task"]["status"]["state"] == "TASK_STATE_FAILED"
        assert (
            blank_a2a.json()["task"]["artifacts"][0]["parts"][0]["data"]["error"]["code"]
            == "bad_request"
        )

    anchor_handle = http_body["identities"][0]["handle"]
    async with app.state.session_factory() as session:
        anchor = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == anchor_handle)
        )
        assert anchor is not None
        anchor_id = anchor.id
        anchor_profile_id = anchor.profile_document_id
        anchor_owner_id = anchor.owner_id
        anchor_created_at = anchor.created_at
        anchor.status = "withdrawn"
        await session.commit()

    withdrawn_http = await client.get(
        "/v1/agent-directory",
        params={"q": "global-agent", "limit": 1, "cursor": cursor},
    )
    withdrawn_mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "global-directory-withdrawn-anchor-mcp",
            "method": "tools/call",
            "params": {
                "name": "list_agent_directory",
                "arguments": {"q": "global-agent", "limit": 1, "cursor": cursor},
            },
        },
    )
    withdrawn_a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "global-directory-withdrawn-anchor-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "list_agent_directory",
                            "q": "global-agent",
                            "limit": 1,
                            "cursor": cursor,
                        }
                    }
                ],
            }
        },
    )
    assert withdrawn_http.status_code == 400
    assert withdrawn_mcp.json()["result"]["isError"] is True
    assert withdrawn_mcp.json()["result"]["structuredContent"]["code"] == "bad_request"
    assert withdrawn_a2a.json()["task"]["status"]["state"] == "TASK_STATE_FAILED"
    assert (
        withdrawn_a2a.json()["task"]["artifacts"][0]["parts"][0]["data"]["error"]["code"]
        == "bad_request"
    )

    async with app.state.session_factory() as session:
        anchor = await session.get(AgentIdentity, anchor_id)
        assert anchor is not None
        anchor.status = "active"
        await session.commit()

    async with app.state.session_factory() as session:
        anchor_profile = await session.get(Document, anchor_profile_id)
        assert anchor_profile is not None
        anchor_profile.visibility = "private"
        await session.commit()
    assert (
        await client.get(
            "/v1/agent-directory",
            params={"q": "global-agent", "limit": 1, "cursor": cursor},
        )
    ).status_code == 400
    async with app.state.session_factory() as session:
        anchor_profile = await session.get(Document, anchor_profile_id)
        assert anchor_profile is not None
        anchor_profile.visibility = "public"
        await session.commit()

    async with app.state.session_factory() as session:
        anchor = await session.get(AgentIdentity, anchor_id)
        assert anchor is not None
        anchor.owner_id = "mismatched-anchor-owner"
        await session.commit()
    assert (
        await client.get(
            "/v1/agent-directory",
            params={"q": "global-agent", "limit": 1, "cursor": cursor},
        )
    ).status_code == 400
    async with app.state.session_factory() as session:
        anchor = await session.get(AgentIdentity, anchor_id)
        assert anchor is not None
        anchor.owner_id = anchor_owner_id
        await session.commit()

    async with app.state.session_factory() as session:
        anchor = await session.get(AgentIdentity, anchor_id)
        assert anchor is not None
        anchor.created_at = anchor_created_at + timedelta(seconds=1)
        await session.commit()
    assert (
        await client.get(
            "/v1/agent-directory",
            params={"q": "global-agent", "limit": 1, "cursor": cursor},
        )
    ).status_code == 400
    async with app.state.session_factory() as session:
        anchor = await session.get(AgentIdentity, anchor_id)
        assert anchor is not None
        anchor.created_at = anchor_created_at
        await session.commit()

    mcp_mismatch = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "global-directory-mismatch",
            "method": "tools/call",
            "params": {
                "name": "list_agent_directory",
                "arguments": {"q": "different", "limit": 1, "cursor": cursor},
            },
        },
    )
    assert mcp_mismatch.status_code == 200
    assert mcp_mismatch.json()["result"]["isError"] is True
    assert mcp_mismatch.json()["result"]["structuredContent"]["code"] == "bad_request"

    tampered = f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}"
    a2a_tampered = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "global-directory-tampered",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "list_agent_directory",
                            "q": "global-agent",
                            "limit": 1,
                            "cursor": tampered,
                        }
                    }
                ],
            }
        },
    )
    assert a2a_tampered.status_code == 200
    assert a2a_tampered.json()["task"]["status"]["state"] == "TASK_STATE_FAILED"
    assert (
        a2a_tampered.json()["task"]["artifacts"][0]["parts"][0]["data"]["error"]["code"]
        == "bad_request"
    )

    async with app.state.session_factory() as session:
        anchor = await session.get(AgentIdentity, anchor_id)
        assert anchor is not None
        await session.delete(anchor)
        await session.commit()
    assert (
        await client.get(
            "/v1/agent-directory",
            params={"q": "global-agent", "limit": 1, "cursor": cursor},
        )
    ).status_code == 400


async def test_agent_identity_never_hydrates_as_a_public_search_hit(api_client) -> None:
    app, client = api_client
    await create_public_identity(
        app,
        client,
        owner="search-owner",
        profile="search-profile",
        agent="sql-directory-only-agent",
    )
    async with app.state.session_factory() as session:
        identity = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "sql-directory-only-agent")
        )
    assert identity is not None

    class IdentityOnlyProjection:
        calls: list[dict[str, object]] = []

        async def search(
            self,
            query: str,
            kind: object,
            skills: object,
            location: object,
            owner_id: object,
        ):
            self.calls.append(
                {
                    "query": query,
                    "kind": kind,
                    "skills": skills,
                    "location": location,
                    "owner_id": owner_id,
                }
            )
            return ([{"id": identity.id, "kind": "agent_identity", "version": 1}], 1)

    projection = IdentityOnlyProjection()
    app.state.search = projection
    response = await client.get("/v1/search", params={"q": "sql-directory-only-agent"})
    assert response.status_code == 200, response.text
    assert response.json()["hits"] == []
    assert response.json()["total"] == 0
    assert projection.calls == [
        {
            "query": "sql-directory-only-agent",
            "kind": None,
            "skills": [],
            "location": None,
            "owner_id": None,
        }
    ]


async def test_profile_agent_protocols_match_and_search_filters_are_bounded(api_client) -> None:
    app, client = api_client
    await _install_ready(app)
    as_human(app, "protocol-owner")
    created_profile = await client.post(
        "/v1/profiles",
        json={"markdown": _profile_v2_markdown(name="Protocol Owner", skills=("Python",))},
        headers={"Idempotency-Key": "agent-directory-v2-profile"},
    )
    assert created_profile.status_code == 201, created_profile.text
    created_identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "protocol-agent",
            "display_name": "protocol-agent display",
            "description": "protocol-agent handles consent-gated internal contact.",
            "profile_handle": "protocol-owner",
        },
        headers={"Idempotency-Key": "agent-directory-protocol-agent-create"},
    )
    assert created_identity.status_code == 201, created_identity.text
    second_identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "protocol-agent-two",
            "display_name": "protocol-agent-two display",
            "description": "protocol-agent-two handles consent-gated internal contact.",
            "profile_handle": "protocol-owner",
        },
        headers={"Idempotency-Key": "agent-directory-protocol-agent-two-create"},
    )
    assert second_identity.status_code == 201, second_identity.text
    async with app.state.session_factory() as session:
        terms = (await session.scalars(select(PublicTaxonomyTerm))).all()
    terms_by_taxonomy = {term.taxonomy: term for term in terms}

    class CapturingProjection:
        calls: list[dict[str, object]] = []

        async def search(self, **kwargs: object):
            self.calls.append(kwargs)
            async with app.state.session_factory() as session:
                profile = await session.scalar(
                    select(Document).where(Document.public_identifier == "protocol-owner")
                )
            assert profile is not None
            return (
                [
                    {
                        "id": profile.id,
                        "kind": "profile",
                        "identifier": "protocol-owner",
                        "name": "Protocol Owner",
                        "headline": "Protocol test",
                        "location": "Singapore",
                        "skills": ["Python"],
                        "version": profile.current_version,
                    }
                ],
                1,
            )

    projection = CapturingProjection()
    app.state.search = projection
    filters = {
        "q": "payments",
        "occupation_ids": [terms_by_taxonomy["occupation"].canonical_id],
        "skill_ids": [terms_by_taxonomy["skill"].canonical_id],
        "language_ids": [terms_by_taxonomy["language"].canonical_id],
        "location_id": terms_by_taxonomy["location"].canonical_id,
        "seniority_ids": [terms_by_taxonomy["seniority"].canonical_id],
        "availability_status": "not_disclosed",
        "work_modes": ["remote"],
        "representation_status": "not_disclosed",
        "contact_disclosure": "none",
        "agent_capability": "internal_contact_request",
    }
    a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "directory-filter-a2a",
                "role": "ROLE_USER",
                "parts": [{"data": {"action": "search", **filters}}],
            }
        },
    )
    assert a2a.status_code == 200, a2a.text
    mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_documents", "arguments": filters},
        },
    )
    assert mcp.status_code == 200, mcp.text
    a2a_result = a2a.json()["task"]["artifacts"][0]["parts"][0]["data"]
    mcp_result = mcp.json()["result"]["structuredContent"]
    assert a2a_result == mcp_result
    a2a_hits = a2a_result["hits"]
    for call in projection.calls:
        assert call["query"] == filters["q"]
        assert "q" not in call
        for name in (
            "occupation_ids",
            "skill_ids",
            "language_ids",
            "location_id",
            "seniority_ids",
            "work_modes",
            "availability_status",
            "representation_status",
            "contact_disclosure",
        ):
            assert call[name] == filters[name]
        assert "agent_capability" not in call
    assert {reference["handle"] for reference in a2a_hits[0]["agent_identities"]} == {
        "protocol-agent",
        "protocol-agent-two",
    }
    assert all(
        reference["capabilities"] == ["internal_contact_request"]
        for reference in a2a_hits[0]["agent_identities"]
    )

    a2a_agents = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "directory-agents-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "list_profile_agents",
                            "profile_handle": "protocol-owner",
                            "cursor": None,
                        }
                    }
                ],
            }
        },
    )
    assert a2a_agents.status_code == 200, a2a_agents.text
    mcp_agents = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "list_profile_agents",
                "arguments": {"profile_handle": "protocol-owner", "cursor": None},
            },
        },
    )
    assert mcp_agents.status_code == 200, mcp_agents.text
    a2a_identities = a2a_agents.json()["task"]["artifacts"][0]["parts"][0]["data"]
    assert a2a_identities == mcp_agents.json()["result"]["structuredContent"]
    assert {identity["handle"] for identity in a2a_identities["identities"]} == {
        "protocol-agent",
        "protocol-agent-two",
    }
    http_agents = await client.get("/v1/profiles/protocol-owner/agent-identities")
    assert http_agents.status_code == 200
    assert http_agents.json() == a2a_identities
    whitespace_http = await client.get("/v1/profiles/%20protocol-owner%20/agent-identities")
    assert whitespace_http.status_code == 200
    assert whitespace_http.json() == a2a_identities

    profile_first = await client.get(
        "/v1/profiles/protocol-owner/agent-identities", params={"limit": 1}
    )
    profile_cursor = profile_first.json()["next_cursor"]
    assert profile_cursor is not None
    profile_http_next = await client.get(
        "/v1/profiles/protocol-owner/agent-identities",
        params={"limit": 1, "cursor": profile_cursor},
    )
    profile_mcp_next = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "profile-agents-next-mcp",
            "method": "tools/call",
            "params": {
                "name": "list_profile_agents",
                "arguments": {
                    "profile_handle": "protocol-owner",
                    "limit": 1,
                    "cursor": profile_cursor,
                },
            },
        },
    )
    profile_a2a_next = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "profile-agents-next-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "list_profile_agents",
                            "profile_handle": "protocol-owner",
                            "limit": 1,
                            "cursor": profile_cursor,
                        }
                    }
                ],
            }
        },
    )
    assert profile_http_next.status_code == 200
    assert profile_mcp_next.json()["result"]["structuredContent"] == profile_http_next.json()
    assert (
        profile_a2a_next.json()["task"]["artifacts"][0]["parts"][0]["data"]
        == profile_http_next.json()
    )

    for index, supplied_cursor in enumerate(("", " \t ")):
        blank_mcp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": f"profile-agents-blank-mcp-{index}",
                "method": "tools/call",
                "params": {
                    "name": "list_profile_agents",
                    "arguments": {
                        "profile_handle": "protocol-owner",
                        "cursor": supplied_cursor,
                    },
                },
            },
        )
        assert blank_mcp.json()["result"]["isError"] is True
        assert blank_mcp.json()["result"]["structuredContent"]["code"] == "validation_failed"

        blank_a2a = await client.post(
            "/a2a/message:send",
            headers={"A2A-Version": "1.0"},
            json={
                "message": {
                    "messageId": f"profile-agents-blank-a2a-{index}",
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "data": {
                                "action": "list_profile_agents",
                                "profile_handle": "protocol-owner",
                                "cursor": supplied_cursor,
                            }
                        }
                    ],
                }
            },
        )
        task = blank_a2a.json()["task"]
        assert task["status"]["state"] == "TASK_STATE_REJECTED"
        assert task["artifacts"][0]["parts"][0]["data"]["error"]["code"] == "invalid_params"

    profile_anchor_handle = profile_first.json()["identities"][0]["handle"]
    async with app.state.session_factory() as session:
        profile_anchor = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == profile_anchor_handle)
        )
        assert profile_anchor is not None
        profile_anchor_id = profile_anchor.id
        profile_anchor.status = "withdrawn"
        await session.commit()

    withdrawn_profile_http = await client.get(
        "/v1/profiles/protocol-owner/agent-identities",
        params={"limit": 1, "cursor": profile_cursor},
    )
    withdrawn_profile_mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "profile-agents-withdrawn-anchor-mcp",
            "method": "tools/call",
            "params": {
                "name": "list_profile_agents",
                "arguments": {
                    "profile_handle": "protocol-owner",
                    "limit": 1,
                    "cursor": profile_cursor,
                },
            },
        },
    )
    withdrawn_profile_a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "profile-agents-withdrawn-anchor-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "list_profile_agents",
                            "profile_handle": "protocol-owner",
                            "limit": 1,
                            "cursor": profile_cursor,
                        }
                    }
                ],
            }
        },
    )
    assert withdrawn_profile_http.status_code == 400
    assert withdrawn_profile_mcp.json()["result"]["isError"] is True
    assert (
        withdrawn_profile_mcp.json()["result"]["structuredContent"]["code"] == "validation_failed"
    )
    withdrawn_profile_task = withdrawn_profile_a2a.json()["task"]
    assert withdrawn_profile_task["status"]["state"] == "TASK_STATE_REJECTED"
    assert (
        withdrawn_profile_task["artifacts"][0]["parts"][0]["data"]["error"]["code"]
        == "invalid_params"
    )

    async with app.state.session_factory() as session:
        profile_anchor = await session.get(AgentIdentity, profile_anchor_id)
        assert profile_anchor is not None
        profile_anchor.status = "active"
        await session.commit()

    oversized_handle = "x" * 101
    oversized_http = await client.get(f"/v1/profiles/{oversized_handle}/agent-identities")
    assert oversized_http.status_code == 422
    oversized_mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "profile-agents-oversized-mcp",
            "method": "tools/call",
            "params": {
                "name": "list_profile_agents",
                "arguments": {"profile_handle": oversized_handle},
            },
        },
    )
    assert oversized_mcp.status_code == 200
    assert oversized_mcp.json()["result"]["isError"] is True
    oversized_a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "profile-agents-oversized-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "list_profile_agents",
                            "profile_handle": oversized_handle,
                        }
                    }
                ],
            }
        },
    )

    def assert_invalid_a2a_action(response) -> None:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/a2a+json")
        task = response.json()["task"]
        assert task["status"]["state"] == "TASK_STATE_REJECTED"
        assert task["artifacts"][0]["parts"][0]["data"] == {
            "error": {
                "code": "invalid_params",
                "message": "the action parameters are invalid",
            }
        }

    assert_invalid_a2a_action(oversized_a2a)

    invalid_a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "directory-invalid-a2a",
                "role": "ROLE_USER",
                "parts": [{"data": {"action": "search", "occupation_ids": ["x"] * 51}}],
            }
        },
    )
    assert_invalid_a2a_action(invalid_a2a)
    unknown_a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "directory-unknown-a2a",
                "role": "ROLE_USER",
                "parts": [{"data": {"action": "search", "unknown": "field"}}],
            }
        },
    )
    assert_invalid_a2a_action(unknown_a2a)
    invalid_capability_a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "directory-invalid-capability-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "search",
                            "agent_capability": "external_contact",
                        }
                    }
                ],
            }
        },
    )
    assert_invalid_a2a_action(invalid_capability_a2a)
    invalid_mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search_documents",
                "arguments": {"unknown": "field"},
            },
        },
    )
    assert invalid_mcp.status_code == 200
    assert invalid_mcp.json()["result"]["isError"] is True
    invalid_capability_mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {
                "name": "search_documents",
                "arguments": {"agent_capability": "external_contact"},
            },
        },
    )
    assert invalid_capability_mcp.status_code == 200
    assert invalid_capability_mcp.json()["result"]["isError"] is True
    bounded_mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "search_documents",
                "arguments": {"occupation_ids": ["x"] * 51},
            },
        },
    )
    assert bounded_mcp.status_code == 200
    assert bounded_mcp.json()["result"]["isError"] is True

    invalid_work_mode_a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "directory-work-mode-bounds-a2a",
                "role": "ROLE_USER",
                "parts": [{"data": {"action": "search", "work_modes": ["remote"] * 21}}],
            }
        },
    )
    assert_invalid_a2a_action(invalid_work_mode_a2a)
    invalid_language_mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "search_documents",
                "arguments": {"language_ids": ["x" * 337]},
            },
        },
    )
    assert invalid_language_mcp.status_code == 200
    assert invalid_language_mcp.json()["result"]["isError"] is True

    tools = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
    )
    assert tools.status_code == 200, tools.text
    search_schema = next(
        tool["inputSchema"]
        for tool in tools.json()["result"]["tools"]
        if tool["name"] == "search_documents"
    )
    for name in ("language_ids", "organization_ids"):
        assert search_schema["properties"][name]["items"]["maxLength"] == 336
    assert search_schema["properties"]["work_modes"]["items"]["maxLength"] == 80
    assert search_schema["properties"]["work_modes"]["maxItems"] == 20
    assert search_schema["properties"]["contact_disclosure"]["maxLength"] == 80
    assert search_schema["properties"]["agent_capability"] == {
        "type": "string",
        "enum": ["internal_contact_request"],
        "description": "Discovery-only profile filter; never proves outreach authority.",
    }


async def test_public_search_enriches_only_live_agent_references_and_filters_before_paging(
    api_client,
) -> None:
    app, client = api_client
    await create_public_identity(
        app,
        client,
        owner="active-owner-one",
        profile="active-profile-one",
        agent="active-agent-one",
    )
    as_human(app, "active-owner-one")
    for index in range(9):
        created = await client.post(
            "/v1/agent-identities",
            json={
                "handle": f"active-agent-one-{index:02d}",
                "display_name": f"active-agent-one-{index:02d} display",
                "description": "Handles consent-gated internal contact.",
                "profile_handle": "active-profile-one",
            },
            headers={"Idempotency-Key": f"agent-directory-active-agent-{index:02d}"},
        )
        assert created.status_code == 201, created.text
    await create_public_identity(
        app,
        client,
        owner="active-owner-two",
        profile="active-profile-two",
        agent="active-agent-two",
    )
    await create_public_identity(
        app,
        client,
        owner="withdrawn-owner",
        profile="withdrawn-profile",
        agent="withdrawn-agent",
    )
    withdrawn = await client.delete(
        "/v1/agent-identities/withdrawn-agent",
        headers={"Idempotency-Key": "agent-directory-search-withdraw-0001"},
    )
    assert withdrawn.status_code == 204
    await create_public_identity(
        app,
        client,
        owner="withheld-owner",
        profile="withheld-profile",
        agent="withheld-agent",
    )
    await create_public_identity(
        app,
        client,
        owner="private-owner",
        profile="private-profile",
        agent="private-agent",
    )
    await create_public_identity(
        app,
        client,
        owner="mismatch-owner",
        profile="mismatch-profile",
        agent="mismatch-agent",
    )
    as_human(app, "resume-owner")
    public_resume = await client.post(
        "/v1/resumes",
        json={
            "markdown": resume_markdown()
            .replace("ada-lovelace-resume", "public-resume")
            .replace("visibility: private", "visibility: public")
        },
        headers={"Idempotency-Key": "public-search-resume-create-0001"},
    )
    assert public_resume.status_code == 201, public_resume.text

    async with app.state.session_factory() as session:
        documents = {
            row.public_identifier: row for row in (await session.scalars(select(Document))).all()
        }
        withheld = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "withheld-agent")
        )
        private_profile = documents["private-profile"]
        mismatch = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "mismatch-agent")
        )
        active_one_id = await session.scalar(
            select(AgentIdentity.id).where(AgentIdentity.handle == "active-agent-one")
        )
        active_one_rows = (
            await session.scalars(
                select(AgentIdentity)
                .where(
                    AgentIdentity.profile_document_id == documents["active-profile-one"].id,
                    AgentIdentity.status == "active",
                )
                .order_by(AgentIdentity.created_at.desc(), AgentIdentity.id.desc())
            )
        ).all()
        assert withheld is not None and mismatch is not None and active_one_id is not None
        withheld.status = "withheld"
        private_profile.visibility = "private"
        mismatch.owner_id = "different-owner"
        await session.commit()
        expected_active_one_handles = [row.handle for row in active_one_rows[:10]]

    def projected(document: Document, *, version: int | None = None) -> dict[str, object]:
        return {
            "id": document.id,
            "kind": document.kind,
            "identifier": document.public_identifier,
            "name": "Projected public name",
            "headline": "Projected public headline",
            "location": "Singapore",
            "skills": ["Python"],
            "version": document.current_version if version is None else version,
        }

    class CapturingProjection:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def search(self, **kwargs: object):
            self.calls.append(kwargs)
            return (
                [
                    projected(documents["private-profile"]),
                    projected(documents["active-profile-one"]),
                    projected(documents["active-profile-two"]),
                    projected(documents["public-resume"]),
                    projected(
                        documents["active-profile-one"],
                        version=documents["active-profile-one"].current_version + 1,
                    ),
                    projected(
                        Document(
                            id="missing-search-document",
                            kind="profile",
                            owner_id="missing-owner",
                            public_identifier="missing-profile",
                            visibility="public",
                            current_version=1,
                        )
                    ),
                    {"id": active_one_id, "kind": "agent_identity", "version": 1},
                ],
                7,
            )

    projection = CapturingProjection()
    app.state.search = projection
    filtered = await client.get(
        "/v1/search",
        params={
            "agent_capability": "internal_contact_request",
            "facets": "kind",
            "offset": 1,
            "limit": 1,
        },
    )
    assert filtered.status_code == 200, filtered.text
    filtered_body = filtered.json()
    assert filtered_body["total"] == 2
    assert filtered_body["facets"] == {"kind": {"profile": 2}}
    assert [hit["identifier"] for hit in filtered_body["hits"]] == ["active-profile-two"]
    assert "agent_capability" not in projection.calls[0]

    unfiltered = await client.get("/v1/search", params={"limit": 50})
    assert unfiltered.status_code == 200, unfiltered.text
    unfiltered_hits = {hit["identifier"]: hit for hit in unfiltered.json()["hits"]}
    assert set(unfiltered_hits) == {
        "active-profile-one",
        "active-profile-two",
        "public-resume",
    }
    assert [
        reference["handle"]
        for reference in unfiltered_hits["active-profile-one"]["agent_identities"]
    ] == expected_active_one_handles
    assert len(unfiltered_hits["active-profile-one"]["agent_identities"]) == 10
    assert unfiltered_hits["active-profile-two"]["agent_identities"] == [
        {"handle": "active-agent-two", "capabilities": ["internal_contact_request"]}
    ]
    assert unfiltered_hits["public-resume"]["agent_identities"] == []
    for hit in unfiltered_hits.values():
        for reference in hit["agent_identities"]:
            assert set(reference) == {"handle", "capabilities"}
            assert reference["capabilities"] == ["internal_contact_request"]
    assert "agent_capability" not in projection.calls[1]


async def test_public_search_agent_enrichment_handles_more_than_200_profiles(api_client) -> None:
    app, client = api_client
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    documents: list[Document] = []
    async with app.state.session_factory() as session:
        for index in range(201):
            document = Document(
                kind="profile",
                owner_id="chunk-owner",
                public_identifier=f"chunk-profile-{index:03d}",
                visibility="public",
                current_version=1,
            )
            session.add(document)
            documents.append(document)
        await session.flush()
        for index, document in enumerate(documents):
            session.add(
                AgentIdentity(
                    owner_id="chunk-owner",
                    handle=f"chunk-agent-{index:03d}",
                    display_name="Chunk agent",
                    description="Handles consent-gated internal contact.",
                    profile_document_id=document.id,
                    status="active",
                    created_at=base_time + timedelta(seconds=index),
                    updated_at=base_time + timedelta(seconds=index),
                )
            )
        await session.commit()

    class ChunkProjection:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def search(self, **kwargs: object):
            self.calls.append(kwargs)
            return (
                [
                    {
                        "id": document.id,
                        "kind": "profile",
                        "identifier": document.public_identifier,
                        "name": "Chunk profile",
                        "headline": "Chunk headline",
                        "location": "Singapore",
                        "skills": ["Python"],
                        "version": 1,
                    }
                    for document in documents
                ],
                len(documents),
            )

    projection = ChunkProjection()
    app.state.search = projection
    response = await client.get(
        "/v1/search",
        params={
            "agent_capability": "internal_contact_request",
            "offset": 200,
            "limit": 1,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 201
    assert body["hits"][0]["identifier"] == "chunk-profile-200"
    assert body["hits"][0]["agent_identities"] == [
        {"handle": "chunk-agent-200", "capabilities": ["internal_contact_request"]}
    ]
    assert "agent_capability" not in projection.calls[0]


async def test_single_public_agent_identity_http_mcp_a2a_parity_and_safe_errors(
    api_client,
) -> None:
    app, client = api_client
    await create_public_identity(
        app,
        client,
        owner="single-read-owner",
        profile="single-read-profile",
        agent="single-read-agent",
    )
    await create_public_identity(
        app,
        client,
        owner="inactive-owner",
        profile="inactive-profile",
        agent="inactive-agent",
    )
    await create_public_identity(
        app,
        client,
        owner="private-owner",
        profile="private-profile",
        agent="private-agent",
    )
    await create_public_identity(
        app,
        client,
        owner="mismatch-owner",
        profile="mismatch-profile",
        agent="mismatch-agent",
    )
    async with app.state.session_factory() as session:
        inactive = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "inactive-agent")
        )
        private_profile = await session.scalar(
            select(Document).where(Document.public_identifier == "private-profile")
        )
        mismatch = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "mismatch-agent")
        )
        assert inactive is not None
        assert private_profile is not None
        assert mismatch is not None
        inactive.status = "withdrawn"
        private_profile.visibility = "private"
        mismatch.owner_id = "different-owner"
        await session.commit()

    expected = {
        "handle": "single-read-agent",
        "display_name": "single-read-agent display",
        "description": "single-read-agent handles consent-gated internal contact.",
        "profile_handle": "single-read-profile",
        "capabilities": ["internal_contact_request"],
    }
    http = await client.get("/v1/agent-identities/single-read-agent")
    assert http.status_code == 200
    assert http.json() == expected
    assert set(http.json()) == {
        "handle",
        "display_name",
        "description",
        "profile_handle",
        "capabilities",
    }
    assert not {
        "owner_id",
        "status",
        "created_at",
        "updated_at",
        "grant",
        "mandate",
        "presence",
        "external_endpoint",
        "contact_policy",
        "outreach",
    }.intersection(http.json())

    openapi = await client.get("/openapi.json")
    assert openapi.status_code == 200
    identity_parameters = openapi.json()["paths"]["/v1/agent-identities/{agent_handle}"]["get"][
        "parameters"
    ]
    assert identity_parameters == [
        {
            "name": "agent_handle",
            "in": "path",
            "required": True,
            "schema": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "pattern": r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$",
                "title": "Agent Handle",
            },
        }
    ]
    for invalid_handle in (
        "Invalid-agent",
        "under_score",
        "-leading-hyphen",
        "trailing-hyphen-",
        "a" * 101,
    ):
        invalid_http = await client.get(f"/v1/agent-identities/{invalid_handle}")
        assert invalid_http.status_code == 422
        assert "agent identity was not found" not in invalid_http.text
        assert "single-read-agent display" not in invalid_http.text

    mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "single-read-mcp",
            "method": "tools/call",
            "params": {
                "name": "get_agent_identity",
                "arguments": {"agent_handle": "single-read-agent"},
            },
        },
    )
    assert mcp.status_code == 200
    assert mcp.json()["result"]["structuredContent"] == expected

    a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "single-read-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "get_agent_identity",
                            "agent_handle": "single-read-agent",
                        }
                    }
                ],
            }
        },
    )
    assert a2a.status_code == 200
    assert a2a.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert a2a.json()["task"]["artifacts"][0]["parts"][0]["data"] == expected

    tools = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": "single-read-tools", "method": "tools/list"}
    )
    assert tools.status_code == 200
    get_tool = next(
        tool for tool in tools.json()["result"]["tools"] if tool["name"] == "get_agent_identity"
    )
    assert get_tool["inputSchema"] == {
        "type": "object",
        "required": ["agent_handle"],
        "properties": {
            "agent_handle": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "pattern": r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$",
            }
        },
        "additionalProperties": False,
    }
    assert get_tool["annotations"] == {"readOnlyHint": True, "openWorldHint": False}

    async def mcp_get(arguments: dict[str, object], request_id: str):
        return await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": "get_agent_identity", "arguments": arguments},
            },
        )

    for request_id, arguments in (
        ("single-read-invalid-uppercase", {"agent_handle": "Invalid"}),
        ("single-read-invalid-long", {"agent_handle": "a" * 101}),
        ("single-read-invalid-extra", {"agent_handle": "single-read-agent", "status": "active"}),
    ):
        invalid_mcp = await mcp_get(arguments, request_id)
        assert invalid_mcp.status_code == 200
        assert invalid_mcp.json()["result"]["isError"] is True
        assert invalid_mcp.json()["result"]["structuredContent"] == {
            "code": "validation_failed",
            "message": "the public agent identity request is invalid",
        }

    invalid_a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "single-read-invalid-a2a",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "get_agent_identity",
                            "agent_handle": "Invalid",
                        }
                    }
                ],
            }
        },
    )
    assert invalid_a2a.status_code == 200
    assert invalid_a2a.json()["task"]["status"]["state"] == "TASK_STATE_REJECTED"
    assert invalid_a2a.json()["task"]["artifacts"][0]["parts"][0]["data"] == {
        "error": {
            "code": "invalid_params",
            "message": "the action parameters are invalid",
        }
    }

    for index, handle in enumerate(
        ("missing-single-read-agent", "inactive-agent", "private-agent", "mismatch-agent")
    ):
        missing_mcp = await mcp_get({"agent_handle": handle}, f"single-read-missing-{index}")
        assert missing_mcp.status_code == 200
        assert missing_mcp.json()["result"]["isError"] is True
        assert missing_mcp.json()["result"]["structuredContent"] == {
            "code": "not_found",
            "message": "the public agent identity or profile was not found",
        }
        assert handle not in missing_mcp.text

        missing_a2a = await client.post(
            "/a2a/message:send",
            headers={"A2A-Version": "1.0"},
            json={
                "message": {
                    "messageId": f"single-read-a2a-missing-{index}",
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "data": {
                                "action": "get_agent_identity",
                                "agent_handle": handle,
                            }
                        }
                    ],
                }
            },
        )
        assert missing_a2a.status_code == 200
        assert missing_a2a.json()["task"]["status"]["state"] == "TASK_STATE_REJECTED"
        assert missing_a2a.json()["task"]["artifacts"][0]["parts"][0]["data"] == {
            "error": {
                "code": "request_rejected",
                "message": "the action request was not accepted",
            }
        }

    missing_profile = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "single-read-missing-profile",
            "method": "tools/call",
            "params": {
                "name": "list_profile_agents",
                "arguments": {"profile_handle": "missing-profile-for-agent-list"},
            },
        },
    )
    assert missing_profile.status_code == 200
    assert missing_profile.json()["result"]["isError"] is True
    assert missing_profile.json()["result"]["structuredContent"] == {
        "code": "not_found",
        "message": "the public agent identity or profile was not found",
    }
    assert "HTTPException" not in missing_profile.text
