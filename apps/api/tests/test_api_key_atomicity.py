from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import delete, select

from app.auth import Principal, optional_principal, require_principal
from app.models import ApiKey, ChangeEvent, IdempotencyRecord


def human(subject: str = "user_test", *, method: str = "clerk_jwt") -> Principal:
    return Principal(subject=subject, method=method, scopes=frozenset({"*"}))


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def create_key(client, scopes: list[str], key: str):
    return await client.post(
        "/v1/api-keys",
        json={"scopes": scopes},
        headers={"Idempotency-Key": key},
    )


async def test_create_replay_is_metadata_only_and_missing_row_fails_closed(api_client) -> None:
    app, client = api_client
    first = await create_key(
        client,
        ["search:read", "documents:write", "search:read"],
        "api-key-create-recovery-0001",
    )
    assert first.status_code == 201, first.text
    created = first.json()
    assert created["key"].startswith("cnd_")
    assert created["recovery_required"] is False
    assert created["scopes"] == ["documents:write", "search:read"]

    async with app.state.session_factory() as session:
        api_key = await session.get(ApiKey, created["id"])
        assert api_key is not None
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "api-key-create-recovery-0001"
            )
        )
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.owner_id == "user_test",
                    ChangeEvent.resource_type == "api_key",
                    ChangeEvent.event_type == "api_key.created",
                )
            )
        ).all()
        assert receipt is not None
        assert receipt.response_body == ""
        assert receipt.response_headers == "{}"
        assert receipt.resource_type == "api_key"
        assert receipt.resource_id == created["id"]
        assert len(events) == 1
        assert json.loads(events[0].payload) == {"scopes": ["documents:write", "search:read"]}
        assert created["key"] not in api_key.secret_hash
        assert created["key"] not in events[0].payload
        assert created["key"] not in receipt.idempotency_key
        assert created["key"] not in receipt.response_body

    replay = await create_key(
        client,
        ["documents:write", "search:read"],
        "api-key-create-recovery-0001",
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    recovered = replay.json()
    assert recovered == {
        "id": created["id"],
        "prefix": created["prefix"],
        "scopes": ["documents:write", "search:read"],
        "created_at": created["created_at"],
        "recovery_required": True,
    }
    assert "key" not in recovered

    app.state.api_keys.settings.api_key_pepper = "rotated-test-pepper-is-long-enough"
    rotated_replay = await create_key(
        client,
        ["documents:write", "search:read"],
        "api-key-create-recovery-0001",
    )
    assert rotated_replay.status_code == 201
    assert rotated_replay.json()["recovery_required"] is True
    assert "key" not in rotated_replay.json()

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "api-key-create-recovery-0001"
            )
        )
        assert receipt is not None
        receipt.response_headers = '{"unexpected":"header"}'
        await session.commit()
    corrupt_headers = await create_key(
        client,
        ["documents:write", "search:read"],
        "api-key-create-recovery-0001",
    )
    assert corrupt_headers.status_code == 503
    assert "unexpected" not in corrupt_headers.text
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "api-key-create-recovery-0001"
            )
        )
        assert receipt is not None
        receipt.response_headers = "{}"
        await session.commit()

    collision = await create_key(
        client,
        ["documents:read"],
        "api-key-create-recovery-0001",
    )
    assert collision.status_code == 409

    async with app.state.session_factory() as session:
        await session.execute(delete(ApiKey).where(ApiKey.id == created["id"]))
        await session.commit()
    missing = await create_key(
        client,
        ["documents:write", "search:read"],
        "api-key-create-recovery-0001",
    )
    assert missing.status_code == 503
    assert created["key"] not in missing.text
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(ApiKey))).all() == []


async def test_create_and_revoke_validate_headers_and_authority(api_client) -> None:
    app, client = api_client
    missing = await client.post("/v1/api-keys", json={"scopes": ["search:read"]})
    assert missing.status_code == 428
    malformed = await client.post(
        "/v1/api-keys",
        json={"scopes": ["search:read"]},
        headers={"Idempotency-Key": "bad\nkey"},
    )
    assert malformed.status_code == 400

    first = await create_key(client, ["search:read"], "api-key-header-0001")
    assert first.status_code == 201, first.text
    key_id = first.json()["id"]
    missing_revoke = await client.delete(f"/v1/api-keys/{key_id}")
    assert missing_revoke.status_code == 428
    malformed_revoke = await client.delete(
        f"/v1/api-keys/{key_id}", headers={"Idempotency-Key": "\u007f"}
    )
    assert malformed_revoke.status_code == 400

    as_principal(app, human(method="agent_api_key"))
    denied_create = await create_key(client, ["search:read"], "api-key-authority-create-0001")
    denied_revoke = await client.delete(
        f"/v1/api-keys/{key_id}", headers={"Idempotency-Key": "api-key-authority-revoke-0001"}
    )
    assert denied_create.status_code == 403
    assert denied_revoke.status_code == 403


async def test_openapi_declares_typed_api_key_recovery_and_idempotency_contract(api_client) -> None:
    _, client = api_client
    document = (await client.get("/openapi.json")).json()
    api_key_create = document["paths"]["/v1/api-keys"]["post"]
    assert api_key_create["security"] == [{"ClerkBearerAuth": []}]
    expected_header = {
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "schema": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[\x21-\x7E]{1,128}$",
        },
    }
    assert api_key_create["parameters"] == [expected_header]
    api_key_revoke = document["paths"]["/v1/api-keys/{key_id}"]["delete"]
    assert api_key_revoke["parameters"][-1] == expected_header
    create_schema = api_key_create["responses"]["201"]["content"]["application/json"]["schema"]
    assert create_schema["discriminator"]["propertyName"] == "recovery_required"
    assert create_schema["discriminator"]["mapping"] == {
        "False": "#/components/schemas/ApiKeyCreatedResponse",
        "True": "#/components/schemas/ApiKeyRecoveryResponse",
    }
    assert api_key_revoke["responses"]["204"]["description"]

    llms_full = (await client.get("/llms-full.txt")).text
    repository_root = Path(__file__).resolve().parents[3]
    discovery_sources = (
        llms_full,
        (repository_root / "docs" / "agent-interoperability.md").read_text(encoding="utf-8"),
        (repository_root / "docs" / "acceptance.md").read_text(encoding="utf-8"),
    )
    required_fragments = (
        "owner API keys are Clerk-human-managed bootstrap/simple-automation credentials",
        "Continuous agents should prefer a scoped, expiring Agent Grant",
        "POST /v1/api-keys",
        "DELETE /v1/api-keys/{key_id}",
        "1-128 visible-ASCII",
        "recovery_required=true",
        "safe recovery, not exact body replay",
        "revoke the returned key/prefix and create a replacement",
        "credential row, safe event, and empty receipt commit atomically",
        "no credential secret, hash, or pepper",
        "Revocation replay is an exact empty `204`",
        "accept receipt binds proposal/action/document/version/SHA-256",
        "tamper or corruption fails closed",
        "Clerk-owner HTTP only",
    )
    for source in discovery_sources:
        for fragment in required_fragments:
            assert fragment in source


async def test_revoke_replays_before_lookup_and_only_emits_transition_event(api_client) -> None:
    app, client = api_client
    first = await create_key(client, ["search:read"], "api-key-revoke-create-0001")
    second = await create_key(client, ["search:read"], "api-key-revoke-create-0002")
    assert first.status_code == 201 and second.status_code == 201
    first_id = first.json()["id"]
    second_id = second.json()["id"]

    revoked = await client.delete(
        f"/v1/api-keys/{first_id}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert revoked.status_code == 204
    assert revoked.content == b""
    assert "content-type" not in revoked.headers
    replay = await client.delete(
        f"/v1/api-keys/{first_id}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert replay.status_code == 204
    assert replay.content == b""
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert "content-type" not in replay.headers

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "api-key-revoke-0001"
            )
        )
        assert receipt is not None
        receipt.resource_id = second_id
        await session.commit()
    mismatched_resource = await client.delete(
        f"/v1/api-keys/{first_id}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert mismatched_resource.status_code == 503

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "api-key-revoke-0001"
            )
        )
        assert receipt is not None
        receipt.resource_id = first_id
        receipt.resource_type = "unexpected"
        await session.commit()
    mismatched_type = await client.delete(
        f"/v1/api-keys/{first_id}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert mismatched_type.status_code == 503

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "api-key-revoke-0001"
            )
        )
        assert receipt is not None
        receipt.resource_type = "api_key"
        receipt.response_headers = '{"X-Unexpected":"value"}'
        await session.commit()
    unexpected_headers = await client.delete(
        f"/v1/api-keys/{first_id}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert unexpected_headers.status_code == 503
    assert "X-Unexpected" not in unexpected_headers.headers

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "api-key-revoke-0001"
            )
        )
        assert receipt is not None
        receipt.response_headers = "malformed"
        await session.commit()
    malformed_headers = await client.delete(
        f"/v1/api-keys/{first_id}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert malformed_headers.status_code == 503

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "api-key-revoke-0001"
            )
        )
        assert receipt is not None
        receipt.response_headers = "{}"
        await session.execute(delete(ApiKey).where(ApiKey.id == first_id))
        await session.commit()
    replay_without_row = await client.delete(
        f"/v1/api-keys/{first_id}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert replay_without_row.status_code == 503

    cross_target = await client.delete(
        f"/v1/api-keys/{second_id}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert cross_target.status_code == 409

    async with app.state.session_factory() as session:
        second_row = await session.get(ApiKey, second_id)
        assert second_row is not None
        second_row.revoked = True
        await session.commit()
        before_events = len(
            (
                await session.scalars(
                    select(ChangeEvent).where(
                        ChangeEvent.resource_type == "api_key",
                        ChangeEvent.event_type == "api_key.revoked",
                    )
                )
            ).all()
        )
    already_revoked = await client.delete(
        f"/v1/api-keys/{second_id}", headers={"Idempotency-Key": "api-key-revoke-0002"}
    )
    assert already_revoked.status_code == 204
    async with app.state.session_factory() as session:
        after_events = len(
            (
                await session.scalars(
                    select(ChangeEvent).where(
                        ChangeEvent.resource_type == "api_key",
                        ChangeEvent.event_type == "api_key.revoked",
                    )
                )
            ).all()
        )
        assert after_events == before_events

    unknown = await client.delete(
        "/v1/api-keys/does-not-exist",
        headers={"Idempotency-Key": "api-key-revoke-unknown-0001"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "API key was not found"


async def test_concurrent_same_key_create_has_one_credential_and_one_event(api_client) -> None:
    app, client = api_client
    responses = await asyncio.gather(
        create_key(client, ["search:read", "documents:read"], "api-key-race-0001"),
        create_key(client, ["documents:read", "search:read"], "api-key-race-0001"),
    )
    assert sorted(response.status_code for response in responses) == [201, 201], [
        response.text for response in responses
    ]
    payloads = [response.json() for response in responses]
    assert sum("key" in payload for payload in payloads) == 1
    assert sum(payload.get("recovery_required") is True for payload in payloads) == 1
    assert (
        sum(response.headers.get("Idempotency-Replayed") == "true" for response in responses) == 1
    )
    async with app.state.session_factory() as session:
        assert len((await session.scalars(select(ApiKey))).all()) == 1
        assert (
            len(
                (
                    await session.scalars(
                        select(ChangeEvent).where(
                            ChangeEvent.resource_type == "api_key",
                            ChangeEvent.event_type == "api_key.created",
                        )
                    )
                ).all()
            )
            == 1
        )
        assert (
            len(
                (
                    await session.scalars(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.idempotency_key == "api-key-race-0001"
                        )
                    )
                ).all()
            )
            == 1
        )


async def test_concurrent_same_key_revoke_has_one_transition_and_receipt(api_client) -> None:
    app, client = api_client
    created = await create_key(client, ["search:read"], "api-key-revoke-race-create-0001")
    assert created.status_code == 201, created.text
    key_id = created.json()["id"]
    responses = await asyncio.gather(
        client.delete(
            f"/v1/api-keys/{key_id}", headers={"Idempotency-Key": "api-key-revoke-race-0001"}
        ),
        client.delete(
            f"/v1/api-keys/{key_id}", headers={"Idempotency-Key": "api-key-revoke-race-0001"}
        ),
    )
    assert [response.status_code for response in responses] == [204, 204], [
        response.text for response in responses
    ]
    assert all(response.content == b"" for response in responses)
    async with app.state.session_factory() as session:
        assert (
            len(
                (
                    await session.scalars(
                        select(ChangeEvent).where(
                            ChangeEvent.resource_type == "api_key",
                            ChangeEvent.event_type == "api_key.revoked",
                        )
                    )
                ).all()
            )
            == 1
        )
        assert (
            len(
                (
                    await session.scalars(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.idempotency_key == "api-key-revoke-race-0001"
                        )
                    )
                ).all()
            )
            == 1
        )
