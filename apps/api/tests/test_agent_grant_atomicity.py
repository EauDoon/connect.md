from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models import AgentGrant, ChangeEvent, IdempotencyRecord, Organization


def grant_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Search assistant",
        "mode": "proposal_only",
        "resource": {"type": "owner"},
        "scopes": ["documents:read", "search:read"],
        "expires_in_seconds": 86_400,
    }
    body.update(overrides)
    return body


async def create_grant(client, key: str, **overrides: object):
    return await client.post(
        "/v1/agent-grants",
        json=grant_body(**overrides),
        headers={"Idempotency-Key": key},
    )


async def test_agent_grant_requires_key_and_recovery_is_secret_safe(api_client) -> None:
    app, client = api_client
    operation = await client.get("/openapi.json")
    assert operation.status_code == 200
    post_schema = operation.json()["paths"]["/v1/agent-grants"]["post"]
    key_parameter = next(
        parameter
        for parameter in post_schema["parameters"]
        if parameter["name"] == "Idempotency-Key"
    )
    assert key_parameter["required"] is True
    assert key_parameter["schema"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[\x21-\x7E]{1,128}$",
    }
    response_schema = post_schema["responses"]["201"]["content"]["application/json"]["schema"]
    assert "AgentGrantCreatedResponse" in json.dumps(response_schema)
    assert "AgentGrantRecoveryResponse" in json.dumps(response_schema)

    missing = await client.post("/v1/agent-grants", json=grant_body())
    assert missing.status_code == 428
    malformed = await client.post(
        "/v1/agent-grants",
        json=grant_body(),
        headers={"Idempotency-Key": "k" * 129},
    )
    assert malformed.status_code == 400
    non_visible = await client.post(
        "/v1/agent-grants",
        json=grant_body(),
        headers={"Idempotency-Key": "bad key"},
    )
    assert non_visible.status_code == 400
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(AgentGrant))).all() == []

    first = await create_grant(client, "grant-recovery-0001")
    assert first.status_code == 201, first.text
    created = first.json()
    raw_key = created["key"]
    assert raw_key.startswith("cng_")

    recovered = await create_grant(client, "grant-recovery-0001")
    assert recovered.status_code == 201, recovered.text
    assert recovered.headers["idempotency-replayed"] == "true"
    safe = recovered.json()
    assert safe["recovery_required"] is True
    assert "key" not in safe
    assert set(safe) == {
        "id",
        "name",
        "prefix",
        "scopes",
        "mode",
        "resource",
        "expires_at",
        "recovery_required",
        "created_at",
    }
    assert safe["id"] == created["id"]
    assert safe["prefix"] == raw_key[:20]

    async with app.state.session_factory() as session:
        grants = (await session.scalars(select(AgentGrant))).all()
        events = (
            await session.scalars(
                select(ChangeEvent).where(ChangeEvent.event_type == "agent_grant.created")
            )
        ).all()
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "grant-recovery-0001"
            )
        )
        assert len(grants) == len(events) == 1
        assert receipt is not None
        assert receipt.resource_type == "agent_grant_recovery"
        assert receipt.response_body == ""
        assert receipt.response_headers == "{}"
        event_payload = events[0].payload
        assert raw_key not in event_payload
        assert "secret_hash" not in event_payload
        assert "pepper" not in event_payload
        assert raw_key not in receipt.resource_id
        assert raw_key not in receipt.response_body
        assert "secret_hash" not in receipt.response_body


async def test_agent_grant_fingerprint_normalizes_manager_name_and_rejects_changes(
    api_client,
) -> None:
    _, client = api_client
    first = await create_grant(
        client,
        "grant-fingerprint-0001",
        name="  Search assistant  ",
    )
    assert first.status_code == 201, first.text
    equivalent = await create_grant(
        client,
        "grant-fingerprint-0001",
        name="Search assistant",
    )
    assert equivalent.status_code == 201, equivalent.text
    assert equivalent.json()["recovery_required"] is True

    changed_name = await create_grant(
        client,
        "grant-fingerprint-0001",
        name="Different assistant",
    )
    assert changed_name.status_code == 409
    changed_scopes = await create_grant(
        client,
        "grant-fingerprint-0001",
        scopes=["documents:read"],
    )
    assert changed_scopes.status_code == 409
    changed_mode = await create_grant(
        client,
        "grant-fingerprint-0001",
        mode="direct",
    )
    assert changed_mode.status_code == 409
    changed_expiry = await create_grant(
        client,
        "grant-fingerprint-0001",
        expires_in_seconds=3_600,
    )
    assert changed_expiry.status_code == 409
    changed_resource = await create_grant(
        client,
        "grant-fingerprint-0001",
        resource={"type": "document", "id": "different-resource"},
    )
    assert changed_resource.status_code == 409
    cross_operation = await client.post(
        "/v1/api-keys",
        json={"scopes": ["search:read"]},
        headers={"Idempotency-Key": "grant-fingerprint-0001"},
    )
    assert cross_operation.status_code == 409


async def test_agent_grant_absolute_expiry_replay_uses_utc_intent(api_client) -> None:
    _, client = api_client
    instant = datetime.now(UTC) + timedelta(days=1)
    first = await create_grant(
        client,
        "grant-absolute-expiry-0001",
        expires_at=instant.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    )
    assert first.status_code == 201, first.text
    equivalent = await create_grant(
        client,
        "grant-absolute-expiry-0001",
        expires_at=instant.isoformat().replace("+00:00", "Z"),
        expires_in_seconds=3_600,
    )
    assert equivalent.status_code == 201, equivalent.text
    assert equivalent.json()["recovery_required"] is True


async def test_agent_grant_replay_rechecks_resource_authority_and_expiry(api_client) -> None:
    app, client = api_client
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "grant-replay-org", "name": "Grant Replay Org", "visibility": "private"},
        headers={"Idempotency-Key": "grant-replay-org-0001"},
    )
    assert organization.status_code == 201, organization.text
    organization_id = organization.json()["id"]
    body = grant_body(
        resource={"type": "organization", "id": organization_id},
        scopes=["organizations:read"],
    )
    first = await client.post(
        "/v1/agent-grants",
        json=body,
        headers={"Idempotency-Key": "grant-replay-org-grant-0001"},
    )
    assert first.status_code == 201, first.text
    async with app.state.session_factory() as session:
        row = await session.get(Organization, organization_id)
        assert row is not None
        row.owner_id = "different-owner"
        await session.commit()
    replay = await client.post(
        "/v1/agent-grants",
        json=body,
        headers={"Idempotency-Key": "grant-replay-org-grant-0001"},
    )
    assert replay.status_code == 503
    assert "different-owner" not in replay.text

    owner = await create_grant(client, "grant-expired-replay-0001")
    assert owner.status_code == 201, owner.text
    async with app.state.session_factory() as session:
        row = await session.get(AgentGrant, owner.json()["id"])
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    expired = await create_grant(client, "grant-expired-replay-0001")
    assert expired.status_code == 503


async def test_agent_grant_receipt_and_manual_mandate_tamper_fail_closed(api_client) -> None:
    app, client = api_client
    first = await create_grant(client, "grant-receipt-tamper-0001")
    assert first.status_code == 201, first.text
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "grant-receipt-tamper-0001"
            )
        )
        assert receipt is not None and receipt.resource_id is not None
        last = receipt.resource_id[-1]
        receipt.resource_id = receipt.resource_id[:-1] + ("0" if last != "0" else "1")
        await session.commit()
    tampered_receipt = await create_grant(client, "grant-receipt-tamper-0001")
    assert tampered_receipt.status_code == 503

    mandate = await create_grant(client, "grant-mandate-tamper-0001")
    assert mandate.status_code == 201, mandate.text
    async with app.state.session_factory() as session:
        await session.execute(text("PRAGMA foreign_keys = OFF"))
        row = await session.get(AgentGrant, mandate.json()["id"])
        assert row is not None
        row.mandate_id = "substituted-mandate"
        await session.commit()
    tampered_mandate = await create_grant(client, "grant-mandate-tamper-0001")
    assert tampered_mandate.status_code == 503


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_grant",
        "owner_substitution",
        "revoked",
        "prefix",
        "name",
        "scopes",
        "mode",
        "resource",
        "created_at",
        "expires_at",
        "mandate",
        "receipt_status",
        "receipt_body",
        "receipt_headers",
        "receipt_type",
        "receipt_id",
        "receipt_digest",
    ],
)
async def test_agent_grant_corruption_never_replays_secret(api_client, corruption: str) -> None:
    app, client = api_client
    key = f"grant-corruption-{corruption}"
    first = await create_grant(client, key)
    assert first.status_code == 201, first.text
    raw_key = first.json()["key"]
    async with app.state.session_factory() as session:
        row = await session.get(AgentGrant, first.json()["id"])
        receipt = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)
        )
        assert row is not None and receipt is not None and receipt.resource_id is not None
        secret_hash = row.secret_hash
        if corruption == "missing_grant":
            await session.delete(row)
        elif corruption == "owner_substitution":
            row.owner_id = "substituted-owner"
        elif corruption == "revoked":
            row.revoked = True
        elif corruption == "prefix":
            row.prefix = "not-a-grant-prefix"
        elif corruption == "name":
            row.name = "Corrupted grant name"
        elif corruption == "scopes":
            row.scopes = '["documents:read"]'
        elif corruption == "mode":
            row.mode = "corrupted-mode"
        elif corruption == "resource":
            row.resource_type = "document"
            row.resource_id = "substituted-resource"
        elif corruption == "created_at":
            row.created_at = datetime.now(UTC)
        elif corruption == "expires_at":
            row.expires_at = datetime.now(UTC) + timedelta(days=2)
        elif corruption == "mandate":
            await session.execute(text("PRAGMA foreign_keys = OFF"))
            row.mandate_id = "substituted-mandate"
        elif corruption == "receipt_status":
            receipt.response_status = 200
        elif corruption == "receipt_body":
            receipt.response_body = "{}"
        elif corruption == "receipt_headers":
            receipt.response_headers = '{"X-Secret": "not-returned"}'
        elif corruption == "receipt_type":
            receipt.resource_type = "other-receipt"
        elif corruption == "receipt_id":
            receipt.resource_id = "not-a-valid-recovery-receipt"
        elif corruption == "receipt_digest":
            last = receipt.resource_id[-1]
            receipt.resource_id = receipt.resource_id[:-1] + ("0" if last != "0" else "1")
        else:  # pragma: no cover - pytest supplies only the cases above
            raise AssertionError(f"unhandled corruption case: {corruption}")
        await session.commit()
    replay = await create_grant(client, key)
    assert replay.status_code == 503, replay.text
    assert raw_key not in replay.text
    assert secret_hash not in replay.text
    assert "secret_hash" not in replay.text
    assert "pepper" not in replay.text


async def test_agent_grant_same_key_sqlite_gather_keeps_one_safe_receipt(api_client) -> None:
    """SQLite gather evidence is useful, but does not prove PostgreSQL scheduling."""

    app, client = api_client
    responses = await asyncio.gather(
        create_grant(client, "grant-same-key-gather-0001"),
        create_grant(client, "grant-same-key-gather-0001"),
    )
    assert [response.status_code for response in responses] == [201, 201]
    bodies = [response.json() for response in responses]
    assert sum("key" in body for body in bodies) == 1
    assert sum(body.get("recovery_required") is True for body in bodies) == 1
    assert all(response.status_code == 201 for response in responses)
    async with app.state.session_factory() as session:
        assert len((await session.scalars(select(AgentGrant))).all()) == 1
        assert (
            len(
                (
                    await session.scalars(
                        select(ChangeEvent).where(ChangeEvent.event_type == "agent_grant.created")
                    )
                ).all()
            )
            == 1
        )
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "grant-same-key-gather-0001"
            )
        )
        assert receipt is not None
        assert receipt.response_body == ""
        assert receipt.response_headers == "{}"
        assert receipt.resource_type == "agent_grant_recovery"


async def test_agent_grant_issuance_is_not_an_mcp_or_a2a_action(api_client) -> None:
    _, client = api_client
    tools = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert tools.status_code == 200, tools.text
    names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert "create_agent_grant" not in names
    assert "issue_agent_grant" not in names

    card = await client.get("/.well-known/agent-card.json")
    assert card.status_code == 200, card.text
    card_text = json.dumps(card.json())
    assert '"name": "create_agent_grant"' not in card_text
    assert '"name": "issue_agent_grant"' not in card_text
