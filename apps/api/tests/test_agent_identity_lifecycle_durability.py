from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.auth import Principal, optional_principal, require_principal
from app.models import AgentIdentity, ChangeEvent, Document, IdempotencyRecord

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def create_profile(client: AsyncClient, handle: str) -> None:
    response = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", handle)},
        headers={"Idempotency-Key": f"profile-{handle}-create"},
    )
    assert response.status_code == 201, response.text


def identity_body(
    *, handle: str = "durable-agent", profile: str = "durable-profile"
) -> dict[str, str]:
    return {
        "handle": handle,
        "display_name": "Durable Agent",
        "description": "A bounded public identity for lifecycle testing.",
        "profile_handle": profile,
    }


async def create_identity(
    client: AsyncClient,
    *,
    key: str = "identity-create-0001",
    handle: str = "durable-agent",
    profile: str = "durable-profile",
) -> object:
    response = await client.post(
        "/v1/agent-identities",
        json=identity_body(handle=handle, profile=profile),
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response


def assert_sanitized_replay_failure(
    response, *, owner: str, resource_id: str | None = None
) -> None:
    assert response.status_code == 503, response.text
    assert "idempotent agent identity receipt cannot be reconstructed" in response.text
    assert owner not in response.text
    assert "agent_identity:v1:" not in response.text
    assert "sha256" not in response.text.lower()
    assert "pepper" not in response.text.lower()
    if resource_id is not None:
        assert resource_id not in response.text


async def test_agent_identity_keys_openapi_and_clerk_boundary(api_client) -> None:
    app, client = api_client
    as_principal(app, human("identity-owner"))
    await create_profile(client, "durable-profile")
    body = identity_body()

    missing = await client.post("/v1/agent-identities", json=body)
    assert missing.status_code == 428
    invalid_space = await client.post(
        "/v1/agent-identities", json=body, headers={"Idempotency-Key": "bad key"}
    )
    assert invalid_space.status_code == 400
    invalid_length = await client.post(
        "/v1/agent-identities", json=body, headers={"Idempotency-Key": "x" * 129}
    )
    assert invalid_length.status_code == 400

    created = await create_identity(client)
    assert set(created.json()) == {
        "handle",
        "display_name",
        "description",
        "profile_handle",
        "capabilities",
    }
    assert "owner_id" not in created.json()
    assert "status" not in created.json()
    assert "credential" not in created.text.lower()
    missing_withdraw = await client.delete("/v1/agent-identities/durable-agent")
    assert missing_withdraw.status_code == 428
    invalid_withdraw = await client.delete(
        "/v1/agent-identities/durable-agent", headers={"Idempotency-Key": "bad key"}
    )
    assert invalid_withdraw.status_code == 400
    assert created.status_code == 201
    assert (await client.get("/v1/agent-identities/durable-agent")).status_code == 200

    schema = app.openapi()
    expected_parameter = {
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "description": "A 1-128 character visible-ASCII key for this logical request.",
        "schema": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[\x21-\x7E]{1,128}$",
        },
    }
    for path, method in (
        ("/v1/agent-identities", "post"),
        ("/v1/agent-identities/{agent_handle}", "delete"),
    ):
        operation = schema["paths"][path][method]
        assert expected_parameter in operation["parameters"]
        assert operation["x-connectmd-human-only"] is True
        assert operation["security"] == [{"ClerkBearerAuth": []}]

    as_principal(app, Principal(subject="agent-grant", method="agent_grant", scopes=frozenset()))
    denied = await client.post(
        "/v1/agent-identities",
        json=body,
        headers={"Idempotency-Key": "agent-grant-create-0001"},
    )
    assert denied.status_code == 403

    tools = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": "identity-tools", "method": "tools/list", "params": {}},
    )
    assert tools.status_code == 200
    tool_names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert "create_agent_identity" not in tool_names
    assert "withdraw_agent_identity" not in tool_names
    card = await client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert "create-agent-identity" not in {skill["id"] for skill in card.json()["skills"]}
    assert "withdraw-agent-identity" not in {skill["id"] for skill in card.json()["skills"]}


async def test_agent_identity_create_replays_and_collisions_are_atomic(api_client) -> None:
    app, client = api_client
    owner = "identity-owner"
    as_principal(app, human(owner))
    await create_profile(client, "durable-profile")
    body = identity_body()
    first = await client.post(
        "/v1/agent-identities", json=body, headers={"Idempotency-Key": "identity-create-0001"}
    )
    assert first.status_code == 201, first.text
    replay = await client.post(
        "/v1/agent-identities", json=body, headers={"Idempotency-Key": "identity-create-0001"}
    )
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert replay.headers["idempotency-replayed"] == "true"

    changed = {**body, "display_name": "A different intent"}
    collision = await client.post(
        "/v1/agent-identities",
        json=changed,
        headers={"Idempotency-Key": "identity-create-0001"},
    )
    assert collision.status_code == 409, collision.text
    cross_operation = await client.delete(
        "/v1/agent-identities/durable-agent",
        headers={"Idempotency-Key": "identity-create-0001"},
    )
    assert cross_operation.status_code == 409, cross_operation.text

    async with app.state.session_factory() as session:
        identities = (
            await session.scalars(select(AgentIdentity).where(AgentIdentity.owner_id == owner))
        ).all()
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.owner_id == owner,
                    ChangeEvent.event_type == "agent_identity.created",
                )
            )
        ).all()
        records = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.resource_type == "agent_identity",
                )
            )
        ).all()
        assert len(identities) == len(events) == len(records) == 1
        receipt = records[0]
        assert receipt.response_headers == "{}"
        assert owner not in (receipt.resource_id or "")
        assert owner not in receipt.response_body
        assert "secret" not in receipt.response_body.lower()
        assert json_safe_event(events[0].payload)


def json_safe_event(payload: str) -> bool:
    return payload == '{"handle": "durable-agent"}'


async def test_agent_identity_withdraw_replays_empty_response_and_removes_public_identity(
    api_client,
) -> None:
    app, client = api_client
    owner = "withdraw-owner"
    as_principal(app, human(owner))
    await create_profile(client, "withdraw-profile")
    await create_identity(
        client,
        key="withdraw-create-0001",
        handle="withdraw-agent",
        profile="withdraw-profile",
    )
    assert (await client.get("/v1/agent-identities/withdraw-agent")).status_code == 200
    first = await client.delete(
        "/v1/agent-identities/withdraw-agent",
        headers={"Idempotency-Key": "withdraw-agent-0001"},
    )
    assert first.status_code == 204
    assert first.content == b""
    replay = await client.delete(
        "/v1/agent-identities/withdraw-agent",
        headers={"Idempotency-Key": "withdraw-agent-0001"},
    )
    assert replay.status_code == 204
    assert replay.content == b""
    assert replay.headers["idempotency-replayed"] == "true"
    changed_handle = await client.delete(
        "/v1/agent-identities/other-agent",
        headers={"Idempotency-Key": "withdraw-agent-0001"},
    )
    assert changed_handle.status_code == 409
    assert (await client.get("/v1/agent-identities/withdraw-agent")).status_code == 404
    directory = await client.get("/v1/agent-directory", params={"q": "withdraw-agent"})
    assert directory.status_code == 200
    assert directory.json()["identities"] == []

    async with app.state.session_factory() as session:
        identity_events = await session.scalar(
            select(func.count(ChangeEvent.sequence)).where(
                ChangeEvent.owner_id == owner,
                ChangeEvent.event_type == "agent_identity.withdrawn",
            )
        )
        receipt_count = await session.scalar(
            select(func.count(IdempotencyRecord.id)).where(
                IdempotencyRecord.owner_id == owner,
                IdempotencyRecord.operation == "DELETE:/v1/agent-identities/withdraw-agent",
            )
        )
        assert identity_events == 1
        assert receipt_count == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "response_status",
        "response_body",
        "response_headers",
        "resource_type",
        "resource_id",
        "digest",
        "identity_missing",
        "identity_owner",
        "identity_handle",
        "profile_owner",
        "profile_visibility",
        "identity_created_at",
    ],
)
async def test_agent_identity_create_replay_corruption_fails_closed(
    api_client, corruption: str
) -> None:
    app, client = api_client
    owner = "corrupt-owner"
    as_principal(app, human(owner))
    await create_profile(client, "corrupt-profile")
    body = identity_body(handle="corrupt-agent", profile="corrupt-profile")
    first = await client.post(
        "/v1/agent-identities", json=body, headers={"Idempotency-Key": "corrupt-create-0001"}
    )
    assert first.status_code == 201, first.text

    async with app.state.session_factory() as session:
        record = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == owner,
                IdempotencyRecord.idempotency_key == "corrupt-create-0001",
            )
        )
        identity = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "corrupt-agent")
        )
        profile = await session.scalar(
            select(Document).where(Document.public_identifier == "corrupt-profile")
        )
        assert record is not None and identity is not None and profile is not None
        original_resource_id = record.resource_id or ""
        if corruption == "response_status":
            record.response_status = 204
        elif corruption == "response_body":
            record.response_body = '{"private-secret":"never-return"}'
        elif corruption == "response_headers":
            record.response_headers = '{"ETag":"private"}'
        elif corruption == "resource_type":
            record.resource_type = "private_identity"
        elif corruption == "resource_id":
            record.resource_id = "malformed"
        elif corruption == "digest":
            replacement = "0" if original_resource_id[-1] != "0" else "1"
            record.resource_id = f"{original_resource_id[:-1]}{replacement}"
        elif corruption == "identity_missing":
            await session.delete(identity)
        elif corruption == "identity_owner":
            identity.owner_id = "substituted-owner"
        elif corruption == "identity_handle":
            identity.handle = "substituted-agent"
        elif corruption == "profile_owner":
            profile.owner_id = "substituted-owner"
        elif corruption == "profile_visibility":
            profile.visibility = "private"
        elif corruption == "identity_created_at":
            identity.created_at = datetime.now(UTC) + timedelta(days=1)
        await session.commit()

    retry = await client.post(
        "/v1/agent-identities",
        json=body,
        headers={"Idempotency-Key": "corrupt-create-0001"},
    )
    assert_sanitized_replay_failure(retry, owner=owner, resource_id=original_resource_id)


@pytest.mark.parametrize("state_change", ["active", "missing"])
async def test_agent_identity_withdraw_replay_state_drift_fails_closed(
    api_client, state_change: str
) -> None:
    app, client = api_client
    owner = "withdraw-corrupt-owner"
    as_principal(app, human(owner))
    await create_profile(client, "withdraw-corrupt-profile")
    await create_identity(
        client,
        key="withdraw-corrupt-create-0001",
        handle="withdraw-corrupt-agent",
        profile="withdraw-corrupt-profile",
    )
    first = await client.delete(
        "/v1/agent-identities/withdraw-corrupt-agent",
        headers={"Idempotency-Key": "withdraw-corrupt-0001"},
    )
    assert first.status_code == 204
    async with app.state.session_factory() as session:
        identity = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "withdraw-corrupt-agent")
        )
        assert identity is not None
        if state_change == "active":
            identity.status = "active"
            identity.withdrawn_at = None
        else:
            await session.delete(identity)
        await session.commit()
    retry = await client.delete(
        "/v1/agent-identities/withdraw-corrupt-agent",
        headers={"Idempotency-Key": "withdraw-corrupt-0001"},
    )
    assert_sanitized_replay_failure(retry, owner=owner)


async def test_agent_identity_same_key_gather_has_one_effect_and_safe_replay(api_client) -> None:
    """SQLite gather coverage; this does not prove PostgreSQL row-lock scheduling."""

    app, client = api_client
    owner = "gather-owner"
    as_principal(app, human(owner))
    await create_profile(client, "gather-profile")
    body = identity_body(handle="gather-agent", profile="gather-profile")
    first, second = await asyncio.gather(
        client.post("/v1/agent-identities", json=body, headers={"Idempotency-Key": "gather-0001"}),
        client.post("/v1/agent-identities", json=body, headers={"Idempotency-Key": "gather-0001"}),
    )
    assert sorted((first.status_code, second.status_code)) == [201, 201]
    assert (
        sum(response.headers.get("idempotency-replayed") == "true" for response in (first, second))
        == 1
    )
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(AgentIdentity.id)).where(
                    AgentIdentity.owner_id == owner,
                    AgentIdentity.handle == "gather-agent",
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(ChangeEvent.sequence)).where(
                    ChangeEvent.owner_id == owner,
                    ChangeEvent.event_type == "agent_identity.created",
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "gather-0001",
                )
            )
            == 1
        )
