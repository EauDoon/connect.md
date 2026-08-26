from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    AgentGrant,
    AgentMandate,
    AgentOutreachDirectPeerRateBucket,
    AgentOutreachRecipientRateBucket,
    ChangeEvent,
    ContactRateBucket,
    ContactRequest,
    IdempotencyRecord,
)

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def profile_for(handle: str) -> str:
    return profile_markdown(visibility="public").replace("ada-lovelace", handle)


def a2a_action_payload(message_id: str, action: str, **fields: object) -> dict[str, object]:
    data: dict[str, object] = {"action": action, **fields}
    return {
        "message": {
            "messageId": message_id,
            "role": "ROLE_USER",
            "parts": [{"data": data, "mediaType": "application/json"}],
        }
    }


def assert_action_error(response, *, state: str, code: str, message: str) -> None:
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["status"]["state"] == state
    data = task["artifacts"][0]["parts"][0]["data"]
    assert data == {"error": {"code": code, "message": message}}
    error = data["error"]
    assert error == {"code": code, "message": message}
    assert set(error) == {"code", "message"}
    assert "status" not in error
    assert "detail" not in error


def as_principal(app, value: Principal) -> None:
    async def current() -> Principal:
        return value

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def setup_identities(api_client) -> tuple[object, object, str]:
    app, client = api_client
    as_principal(app, human("recipient"))
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_for("recipient-profile")},
        headers={"Idempotency-Key": "recipient-profile-create-0001"},
    )
    assert profile.status_code == 201, profile.text
    identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "recipient-agent",
            "display_name": "Recipient Agent",
            "description": "Handles consent-gated internal introductions.",
            "profile_handle": "recipient-profile",
        },
        headers={"Idempotency-Key": "recipient-agent-create-0001"},
    )
    assert identity.status_code == 201, identity.text
    policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 5},
        headers={
            "Idempotency-Key": "mandate-contact-policy-0001",
            "If-Match": '"policy-0"',
        },
    )
    assert policy.status_code == 200, policy.text

    as_principal(app, human("sender"))
    sender_profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_for("sender-profile")},
        headers={"Idempotency-Key": "sender-profile-create-0001"},
    )
    assert sender_profile.status_code == 201, sender_profile.text
    sender_identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "sender-agent",
            "display_name": "Sender Agent",
            "description": "Represents the sender for bounded internal requests.",
            "profile_handle": "sender-profile",
        },
        headers={"Idempotency-Key": "sender-agent-create-0001"},
    )
    assert sender_identity.status_code == 201, sender_identity.text
    return app, client, sender_profile.headers["etag"]


async def issue_mandate(
    client,
    *,
    key: str,
    expires_at: str | None = None,
    agent_handle: str = "sender-agent",
) -> object:
    return await client.post(
        f"/v1/agent-identities/{agent_handle}/mandates",
        json={"expires_at": expires_at or (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        headers={"Idempotency-Key": key},
    )


async def test_public_identity_and_mandate_lost_ack_recovery_are_safe(api_client) -> None:
    app, client, _ = await setup_identities(api_client)
    public = await client.get("/v1/agent-identities/sender-agent")
    assert public.status_code == 200
    assert public.json() == {
        "handle": "sender-agent",
        "display_name": "Sender Agent",
        "description": "Represents the sender for bounded internal requests.",
        "profile_handle": "sender-profile",
        "capabilities": ["internal_contact_request"],
    }
    owned = await client.get("/v1/agent-identities")
    assert owned.status_code == 200
    assert len(owned.json()) == 1
    assert set(owned.json()[0]) == {
        "handle",
        "display_name",
        "description",
        "profile_handle",
        "status",
        "created_at",
        "updated_at",
    }
    assert "owner_id" not in owned.json()[0]
    assert "grant" not in owned.json()[0]
    assert "mandate" not in owned.json()[0]

    expiry = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    issued = await client.post(
        "/v1/agent-identities/sender-agent/mandates",
        json={"expires_at": expiry},
        headers={"Idempotency-Key": "mandate-lost-ack-0001"},
    )
    assert issued.status_code == 201, issued.text
    assert issued.json()["grant"]["key"].startswith("cng_")
    raw_key = issued.json()["grant"]["key"]
    recovered = await issue_mandate(client, key="mandate-lost-ack-0001", expires_at=expiry)
    assert recovered.status_code == 201, recovered.text
    assert recovered.json()["recovery_required"] is True
    assert "key" not in recovered.json()

    inventory = await client.get("/v1/agent-identities/sender-agent/mandates")
    assert inventory.status_code == 200
    assert len(inventory.json()) == 1
    summary = inventory.json()[0]
    assert summary["id"] == issued.json()["id"]
    assert summary["scope"] == "internal_contact_request"
    assert summary["status"] == "active"
    assert datetime.fromisoformat(summary["expires_at"]) == datetime.fromisoformat(
        issued.json()["expires_at"].replace("Z", "+00:00")
    ).replace(tzinfo=None)
    assert summary["grant_prefix"] == raw_key[:20]
    async with app.state.session_factory() as session:
        mandates = (await session.scalars(select(AgentMandate))).all()
        grants = (await session.scalars(select(AgentGrant))).all()
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "mandate-lost-ack-0001"
            )
        )
        assert len(mandates) == len(grants) == 1
        assert receipt is not None
        assert raw_key not in receipt.response_body


async def test_concurrent_mandate_issuance_keeps_one_active_mandate(api_client) -> None:
    app, client, _ = await setup_identities(api_client)
    first, second = await asyncio.gather(
        issue_mandate(client, key="mandate-race-0001"),
        issue_mandate(client, key="mandate-race-0002"),
    )
    assert sorted((first.status_code, second.status_code)) == [201, 409]
    async with app.state.session_factory() as session:
        mandates = (
            await session.scalars(select(AgentMandate).where(AgentMandate.status == "active"))
        ).all()
        assert len(mandates) == 1


async def test_profile_can_have_multiple_bounded_agent_identities(api_client) -> None:
    _, client, _ = await setup_identities(api_client)
    second = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "sender-agent-2",
            "display_name": "Sender Agent Two",
            "description": "A second distinct representative for the same public profile.",
            "profile_handle": "sender-profile",
        },
        headers={"Idempotency-Key": "sender-agent-2-create-0001"},
    )
    assert second.status_code == 201, second.text
    second_mandate = await client.post(
        "/v1/agent-identities/sender-agent-2/mandates",
        json={"expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        headers={"Idempotency-Key": "second-agent-mandate-0001"},
    )
    assert second_mandate.status_code == 201, second_mandate.text
    for index in range(3, 11):
        created = await client.post(
            "/v1/agent-identities",
            json={
                "handle": f"sender-agent-{index}",
                "display_name": f"Sender Agent {index}",
                "description": "A bounded representative for the same public profile.",
                "profile_handle": "sender-profile",
            },
            headers={"Idempotency-Key": f"sender-agent-{index}-create-0001"},
        )
        assert created.status_code == 201, created.text
    over_limit = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "sender-agent-11",
            "display_name": "Sender Agent 11",
            "description": "This eleventh active representative must be rejected.",
            "profile_handle": "sender-profile",
        },
        headers={"Idempotency-Key": "sender-agent-11-create-0001"},
    )
    assert over_limit.status_code == 429


async def test_agent_outreach_uses_mandate_and_safe_receipts(api_client) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="mandate-outreach-0001")
    raw_key = issued.json()["grant"]["key"]
    app.dependency_overrides.clear()
    headers = {
        "Authorization": f"Bearer {raw_key}",
        "Idempotency-Key": "agent-outreach-0001",
    }
    body = {
        "target_agent_handle": "recipient-agent",
        "purpose": "Interview",
        "message": "Would you be open to a consent-gated internal introduction?",
    }
    outreach = await client.post("/v1/agent-outreach", json=body, headers=headers)
    assert outreach.status_code == 201, outreach.text
    payload = outreach.json()
    assert set(payload) == {
        "id",
        "origin",
        "status",
        "sender_identity_handle",
        "target_identity_handle",
        "created_at",
    }
    assert payload["origin"] == "agent_outreach"
    assert payload["status"] == "pending"
    assert payload["sender_identity_handle"] == "sender-agent"

    assert payload["target_identity_handle"] == "recipient-agent"
    assert body["purpose"] not in payload.values()
    assert body["message"] not in payload.values()
    replay = await client.post("/v1/agent-outreach", json=body, headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json() == payload
    schema = app.openapi()
    assert (
        schema["paths"]["/v1/agent-outreach"]["post"]["responses"]["201"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/AgentOutreachReceipt"
    )
    assert (
        schema["paths"]["/v1/agent-identities/{agent_handle}/mandates"]["post"][
            "x-connectmd-human-only"
        ]
        is True
    )
    assert schema["paths"]["/v1/agent-outreach"]["post"]["security"] == [{"AgentGrantAuth": []}]
    expected_idempotency_parameter = {
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
    for path in ("/v1/contact-requests", "/v1/agent-outreach"):
        assert schema["paths"][path]["post"]["parameters"] == [expected_idempotency_parameter]
    assert "security" not in schema["paths"]["/v1/agent-identities/{agent_handle}"]["get"]
    assert schema["paths"]["/v1/agent-identities"]["post"]["security"] == [{"ClerkBearerAuth": []}]
    assert schema["paths"]["/v1/agent-identities"]["get"]["security"] == [{"ClerkBearerAuth": []}]
    assert schema["paths"]["/v1/agent-identities/{agent_handle}"]["delete"]["security"] == [
        {"ClerkBearerAuth": []}
    ]
    for method in ("get", "post"):
        assert schema["paths"]["/v1/agent-identities/{agent_handle}/mandates"][method][
            "security"
        ] == [{"ClerkBearerAuth": []}]
    assert schema["paths"]["/v1/agent-identities/{agent_handle}/mandates/{mandate_id}"]["delete"][
        "security"
    ] == [{"ClerkBearerAuth": []}]

    denied_policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 5},
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "mandate-denied-contact-policy-0001",
        },
    )
    assert denied_policy.status_code == 403
    denied_legacy = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": "recipient-profile",
            "purpose": "Interview",
            "message": "Mandates must not use the legacy contact route.",
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "mandate-legacy-contact-0001",
        },
    )
    assert denied_legacy.status_code == 403
    denied_decision = await client.post(
        f"/v1/contact-requests/{payload['id']}/accept",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert denied_decision.status_code == 403
    denied_a2a = await client.post(
        "/a2a/message:send",
        json={
            "message": {
                "messageId": "mandate-a2a-legacy-0001",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "contact_request",
                            "target_profile_handle": "recipient-profile",
                            "purpose": "Interview",
                            "message": "Mandates must not use the legacy A2A action.",
                        },
                        "mediaType": "application/json",
                    }
                ],
            }
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "A2A-Version": "1.0",
            "Idempotency-Key": "mandate-a2a-legacy-0001",
        },
    )
    assert denied_a2a.status_code == 200
    assert denied_a2a.json()["task"]["status"]["state"] == "TASK_STATE_REJECTED"

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "agent-outreach-0001"
            )
        )
        assert receipt is not None
        assert receipt.response_body == ""
        assert body["purpose"] not in receipt.response_body
        assert body["message"] not in receipt.response_body
        row = await session.get(ContactRequest, payload["id"])
        assert row is not None and row.sender_mandate_id == issued.json()["id"]
        sender_bucket = await session.scalar(select(ContactRateBucket))
        recipient_bucket = await session.scalar(select(AgentOutreachRecipientRateBucket))
        direct_peer_bucket = await session.scalar(select(AgentOutreachDirectPeerRateBucket))
        assert sender_bucket is not None and sender_bucket.request_count == 1
        assert recipient_bucket is not None and recipient_bucket.request_count == 1
        assert direct_peer_bucket is not None and direct_peer_bucket.request_count == 1
        assert len(direct_peer_bucket.direct_peer_hmac) == 64
        assert "127.0.0.1" not in str(direct_peer_bucket.__dict__)

    as_principal(app, human("recipient"))
    inbox = await client.get("/v1/contact-requests/inbox")
    assert inbox.status_code == 200
    inbox_item = inbox.json()["requests"][0]
    assert inbox_item["purpose"] == body["purpose"]
    assert inbox_item["message"] == body["message"]
    assert inbox_item["sender_identity_handle"] == "sender-agent"
    assert inbox_item["sender_grant_id"] is None
    representative = await client.post(
        "/v1/agent-grants",
        json={
            "name": "Contact representative",
            "mode": "direct",
            "resource": {"type": "owner"},
            "scopes": ["contacts:write"],
        },
        headers={"Idempotency-Key": "mandate-representative-grant-0001"},
    )
    assert representative.status_code == 201
    app.dependency_overrides.clear()
    denied_ordinary_grant_outreach = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "sender-agent",
            "purpose": "Credential boundary",
            "message": "An ordinary direct grant must not become mandate outreach authority.",
        },
        headers={
            "Authorization": f"Bearer {representative.json()['key']}",
            "Idempotency-Key": "ordinary-grant-outreach-denied-0001",
        },
    )
    assert denied_ordinary_grant_outreach.status_code == 403
    denied = await client.post(
        f"/v1/contact-requests/{payload['id']}/accept",
        headers={
            "Authorization": f"Bearer {representative.json()['key']}",
            "Idempotency-Key": "mandate-denied-decision-0001",
        },
    )
    assert denied.status_code == 404
    as_principal(app, human("recipient"))
    accepted = await client.post(
        f"/v1/contact-requests/{payload['id']}/accept",
        headers={"Idempotency-Key": "mandate-human-accept-0001"},
    )
    assert accepted.status_code == 200, accepted.text


async def test_agent_outreach_status_is_exact_origin_private_and_a2a_equivalent(
    api_client,
) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="status-origin-mandate-0001")
    raw_key = issued.json()["grant"]["key"]
    app.dependency_overrides.clear()
    created = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Bounded status check",
            "message": "The sender must only learn the privacy-minimal consent state.",
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "status-origin-outreach-0001",
        },
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    status = await client.get(
        f"/v1/agent-outreach/{request_id}",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert status.status_code == 200, status.text
    assert set(status.json()) == {
        "id",
        "origin",
        "status",
        "sender_identity_handle",
        "target_identity_handle",
        "created_at",
        "decided_at",
    }
    assert status.json()["status"] == "pending"
    assert status.json()["decided_at"] is None
    assert "purpose" not in status.json()
    assert "message" not in status.json()
    assert "sender_owner_id" not in status.json()
    assert "recipient_owner_id" not in status.json()
    assert "report_reason" not in status.json()
    assert "decision_actor_id" not in status.json()

    a2a = await client.post(
        "/a2a/message:send",
        json={
            "message": {
                "messageId": "status-origin-a2a-0001",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "action": "get_agent_outreach_status",
                            "request_id": request_id,
                        },
                        "mediaType": "application/json",
                    }
                ],
            }
        },
        headers={"Authorization": f"Bearer {raw_key}", "A2A-Version": "1.0"},
    )
    assert a2a.status_code == 200, a2a.text
    assert a2a.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    a2a_status = a2a.json()["task"]["artifacts"][0]["parts"][0]["data"]["agent_outreach"]
    assert a2a_status == status.json()

    as_principal(app, human("sender"))
    owner_status = await client.get(f"/v1/agent-outreach/{request_id}")
    assert owner_status.status_code == 200
    assert owner_status.json() == status.json()
    alternate_identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "alternate-sender-agent",
            "display_name": "Alternate Sender Agent",
            "description": "A distinct mandate used to prove exact-origin isolation.",
            "profile_handle": "sender-profile",
        },
        headers={"Idempotency-Key": "alternate-sender-agent-create-0001"},
    )
    assert alternate_identity.status_code == 201, alternate_identity.text
    alternate = await issue_mandate(
        client,
        key="status-alternate-mandate-0001",
        agent_handle="alternate-sender-agent",
    )
    assert alternate.status_code == 201, alternate.text
    app.dependency_overrides.clear()
    wrong_mandate = await client.get(
        f"/v1/agent-outreach/{request_id}",
        headers={"Authorization": f"Bearer {alternate.json()['grant']['key']}"},
    )
    assert wrong_mandate.status_code == 404
    assert wrong_mandate.json()["detail"] == "agent outreach was not found"

    as_principal(app, human("recipient"))
    wrong_owner = await client.get(f"/v1/agent-outreach/{request_id}")
    assert wrong_owner.status_code == 404
    assert wrong_owner.json()["detail"] == "agent outreach was not found"

    schema = app.openapi()
    status_operation = schema["paths"]["/v1/agent-outreach/{request_id}"]["get"]
    assert status_operation["security"] == [
        {"ClerkBearerAuth": []},
        {"AgentGrantAuth": []},
    ]
    assert status_operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AgentOutreachStatusResponse"
    }


async def test_agent_outreach_status_maps_terminal_states_and_expires_with_retention(
    api_client,
) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="status-terminal-mandate-0001")
    raw_key = issued.json()["grant"]["key"]
    app.dependency_overrides.clear()
    created = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Terminal status mapping",
            "message": "Internal decisions must map to the bounded external vocabulary.",
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "status-terminal-outreach-0001",
        },
    )
    request_id = created.json()["id"]
    as_principal(app, human("sender"))
    for internal_status, external_status in (
        ("accepted", "accepted"),
        ("rejected", "declined"),
        ("blocked", "declined"),
        ("reported", "declined"),
    ):
        async with app.state.session_factory() as session:
            row = await session.get(ContactRequest, request_id)
            assert row is not None
            row.status = internal_status
            row.decided_at = datetime.now(UTC)
            await session.commit()
        response = await client.get(f"/v1/agent-outreach/{request_id}")
        assert response.status_code == 200
        assert response.json()["status"] == external_status
        assert response.json()["decided_at"] is not None

    async with app.state.session_factory() as session:
        row = await session.get(ContactRequest, request_id)
        assert row is not None
        row.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    expired = await client.get(f"/v1/agent-outreach/{request_id}")
    assert expired.status_code == 404
    assert expired.json()["detail"] == "agent outreach was not found"


async def test_agent_outreach_status_rejects_revoked_and_expired_origin_credentials(
    api_client,
) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="status-revoked-mandate-0001")
    raw_key = issued.json()["grant"]["key"]
    app.dependency_overrides.clear()
    created = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Credential lifecycle",
            "message": "Revoked and expired credentials must lose status access.",
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "status-revoked-outreach-0001",
        },
    )
    request_id = created.json()["id"]
    as_principal(app, human("sender"))
    missing_revocation = await client.delete(
        "/v1/agent-identities/sender-agent/mandates/missing-mandate"
    )
    assert missing_revocation.status_code == 404
    assert missing_revocation.json()["detail"] == "agent mandate was not found"
    revoked = await client.delete(
        f"/v1/agent-identities/sender-agent/mandates/{issued.json()['id']}"
    )
    assert revoked.status_code == 204
    assert revoked.content == b""
    app.dependency_overrides.clear()
    denied_revoked = await client.get(
        f"/v1/agent-outreach/{request_id}",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert denied_revoked.status_code == 401

    as_principal(app, human("recipient"))
    decided = await client.post(
        f"/v1/contact-requests/{request_id}/reject",
        headers={"Idempotency-Key": "mandate-contact-reject-0001"},
    )
    assert decided.status_code == 200
    as_principal(app, human("sender"))
    alternate_identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "expiring-sender-agent",
            "display_name": "Expiring Sender Agent",
            "description": "A distinct identity for expiry isolation.",
            "profile_handle": "sender-profile",
        },
        headers={"Idempotency-Key": "expiring-sender-agent-create-0001"},
    )
    assert alternate_identity.status_code == 201
    expiring = await issue_mandate(
        client,
        key="status-expired-mandate-0001",
        agent_handle="expiring-sender-agent",
    )
    expiring_key = expiring.json()["grant"]["key"]
    app.dependency_overrides.clear()
    expiring_outreach = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Credential expiry",
            "message": "An expired origin credential must lose status access.",
        },
        headers={
            "Authorization": f"Bearer {expiring_key}",
            "Idempotency-Key": "status-expired-outreach-0001",
        },
    )
    assert expiring_outreach.status_code == 201
    async with app.state.session_factory() as session:
        mandate = await session.get(AgentMandate, expiring.json()["id"])
        grant = await session.get(AgentGrant, expiring.json()["grant"]["id"])
        assert mandate is not None and grant is not None
        mandate.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        grant.expires_at = mandate.expires_at
        await session.commit()
    denied_expired = await client.get(
        f"/v1/agent-outreach/{expiring_outreach.json()['id']}",
        headers={"Authorization": f"Bearer {expiring_key}"},
    )
    assert denied_expired.status_code == 401


async def test_stored_mandate_grant_cannot_expand_beyond_exact_outreach_scope(
    api_client,
) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="mandate-corruption-0001")
    assert issued.status_code == 201, issued.text
    raw_key = issued.json()["grant"]["key"]
    grant_id = issued.json()["grant"]["id"]
    app.dependency_overrides.clear()

    valid_outreach = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Bounded introduction",
            "message": "This unchanged mandate remains valid only for internal outreach.",
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "mandate-corruption-outreach-0001",
        },
    )
    assert valid_outreach.status_code == 201, valid_outreach.text

    async with app.state.session_factory() as session:
        stored = await session.get(AgentGrant, grant_id)
        assert stored is not None and stored.mandate_id is not None
        stored.scopes = (
            '["changes:read", "documents:read", "documents:write", "inventory:read", "search:read"]'
        )
        await session.commit()

    rejected = await client.get("/v1/documents", headers={"Authorization": f"Bearer {raw_key}"})
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "invalid, revoked, or expired agent grant"


async def test_agent_outreach_recipient_and_direct_peer_limits_cover_http_and_a2a(
    api_client,
) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="mandate-abuse-boundaries-0001")
    raw_key = issued.json()["grant"]["key"]
    app.dependency_overrides.clear()
    body = {
        "target_agent_handle": "recipient-agent",
        "purpose": "Bounded introduction",
        "message": "This request exercises durable outreach admission limits.",
    }
    first = await client.post(
        "/v1/agent-outreach",
        json=body,
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "outreach-abuse-first",
            "X-Forwarded-For": "203.0.113.10",
        },
    )
    assert first.status_code == 201, first.text
    async with app.state.session_factory() as session:
        contact = await session.get(ContactRequest, first.json()["id"])
        sender_bucket = await session.scalar(select(ContactRateBucket))
        recipient_bucket = await session.scalar(select(AgentOutreachRecipientRateBucket))
        direct_peer_bucket = await session.scalar(select(AgentOutreachDirectPeerRateBucket))
        assert contact is not None
        assert sender_bucket is not None and sender_bucket.request_count == 1
        assert recipient_bucket is not None and recipient_bucket.request_count == 1
        assert direct_peer_bucket is not None and direct_peer_bucket.request_count == 1
        assert len(direct_peer_bucket.direct_peer_hmac) == 64
        assert "127.0.0.1" not in str(direct_peer_bucket.__dict__)
        assert "203.0.113.10" not in str(direct_peer_bucket.__dict__)
        contact.status = "rejected"
        contact.decided_at = datetime.now(UTC)
        recipient_bucket.request_count = 5
        await session.commit()

    recipient_limited = await client.post(
        "/v1/agent-outreach",
        json=body,
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "outreach-recipient-limited",
            "X-Forwarded-For": "198.51.100.20",
        },
    )
    assert recipient_limited.status_code == 429
    assert recipient_limited.json()["detail"] == "agent outreach rate limit reached"
    assert recipient_limited.headers["retry-after"] == "86400"
    a2a_recipient_limited = await client.post(
        "/a2a/message:send",
        json={
            "message": {
                "messageId": "outreach-recipient-limited-a2a",
                "role": "ROLE_USER",
                "parts": [{"data": {"action": "agent_outreach", **body}}],
            }
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "A2A-Version": "1.0",
            "Idempotency-Key": "outreach-recipient-limited-a2a",
            "X-Forwarded-For": "198.51.100.21",
        },
    )
    assert a2a_recipient_limited.status_code == 200
    recipient_task = a2a_recipient_limited.json()["task"]
    assert recipient_task["status"]["state"] == "TASK_STATE_REJECTED"
    assert recipient_task["artifacts"][0]["parts"][0]["data"]["error"] == {
        "code": "rate_limited",
        "message": "the action request was rate limited",
    }
    async with app.state.session_factory() as session:
        sender_bucket = await session.scalar(select(ContactRateBucket))
        recipient_bucket = await session.scalar(select(AgentOutreachRecipientRateBucket))
        direct_peer_buckets = (
            await session.scalars(select(AgentOutreachDirectPeerRateBucket))
        ).all()
        assert sender_bucket is not None and sender_bucket.request_count == 1
        assert recipient_bucket is not None and recipient_bucket.request_count == 5
        assert len(direct_peer_buckets) == 1 and direct_peer_buckets[0].request_count == 1
        recipient_bucket.request_count = 1
        direct_peer_buckets[
            0
        ].request_count = app.state.settings.agent_outreach_direct_peer_daily_limit
        await session.commit()

    a2a_limited = await client.post(
        "/a2a/message:send",
        json={
            "message": {
                "messageId": "outreach-direct-peer-limited-a2a",
                "role": "ROLE_USER",
                "parts": [{"data": {"action": "agent_outreach", **body}}],
            }
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "A2A-Version": "1.0",
            "Idempotency-Key": "outreach-direct-peer-limited-a2a",
            "X-Forwarded-For": "192.0.2.30",
        },
    )
    assert a2a_limited.status_code == 200
    task = a2a_limited.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_REJECTED"
    assert task["artifacts"][0]["parts"][0]["data"]["error"] == {
        "code": "rate_limited",
        "message": "the action request was rate limited",
    }
    direct_peer_limited = await client.post(
        "/v1/agent-outreach",
        json=body,
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "outreach-direct-peer-limited-http",
            "X-Forwarded-For": "192.0.2.31",
        },
    )
    assert direct_peer_limited.status_code == 429
    assert direct_peer_limited.json()["detail"] == "agent outreach rate limit reached"
    async with app.state.session_factory() as session:
        assert len((await session.scalars(select(ContactRequest))).all()) == 1
        sender_bucket = await session.scalar(select(ContactRateBucket))
        recipient_bucket = await session.scalar(select(AgentOutreachRecipientRateBucket))
        direct_peer_buckets = (
            await session.scalars(select(AgentOutreachDirectPeerRateBucket))
        ).all()
        assert sender_bucket is not None and sender_bucket.request_count == 1
        assert recipient_bucket is not None and recipient_bucket.request_count == 1
        assert len(direct_peer_buckets) == 1
        assert (
            direct_peer_buckets[0].request_count
            == app.state.settings.agent_outreach_direct_peer_daily_limit
        )


async def test_revoked_or_withdrawn_identity_cannot_send_agent_outreach(api_client) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="mandate-boundary-0001")
    raw_key = issued.json()["grant"]["key"]
    revoked = await client.delete(
        f"/v1/agent-identities/sender-agent/mandates/{issued.json()['id']}"
    )
    assert revoked.status_code == 204
    app.dependency_overrides.clear()
    denied = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Interview",
            "message": "This must not be sent after revocation.",
        },
        headers={"Authorization": f"Bearer {raw_key}", "Idempotency-Key": "revoked-send-0001"},
    )
    assert denied.status_code == 401

    as_principal(app, human("sender"))
    reissued = await issue_mandate(client, key="mandate-boundary-0002")
    assert reissued.status_code == 201
    withdrawn = await client.delete(
        "/v1/agent-identities/sender-agent",
        headers={"Idempotency-Key": "sender-agent-withdraw-0001"},
    )
    assert withdrawn.status_code == 204
    owner_inventory = await client.get("/v1/agent-identities")
    assert owner_inventory.status_code == 200
    assert owner_inventory.json()[0]["status"] == "withdrawn"
    assert (await client.get("/v1/agent-identities/sender-agent")).status_code == 404
    app.dependency_overrides.clear()
    denied_withdrawn = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Interview",
            "message": "This must not be sent after withdrawal.",
        },
        headers={
            "Authorization": f"Bearer {reissued.json()['grant']['key']}",
            "Idempotency-Key": "withdrawn-send-0001",
        },
    )
    assert denied_withdrawn.status_code == 403


async def test_target_withdrawal_blocks_outreach(api_client) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="mandate-target-withdraw-0001")
    raw_key = issued.json()["grant"]["key"]
    as_principal(app, human("recipient"))
    withdrawn = await client.delete(
        "/v1/agent-identities/recipient-agent",
        headers={"Idempotency-Key": "recipient-agent-withdraw-0001"},
    )
    assert withdrawn.status_code == 204
    app.dependency_overrides.clear()
    denied_withdrawn_target = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Interview",
            "message": "A withdrawn target cannot receive outreach.",
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "target-withdraw-send-0001",
        },
    )
    assert denied_withdrawn_target.status_code == 404


async def test_target_private_profile_blocks_outreach(api_client) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="mandate-target-private-0001")
    raw_key = issued.json()["grant"]["key"]
    as_principal(app, human("recipient"))
    current = await client.get("/v1/profiles/recipient-profile")
    assert current.status_code == 200
    private = await client.put(
        "/v1/profiles/recipient-profile",
        json={
            "markdown": profile_for("recipient-profile").replace(
                "visibility: public", "visibility: private"
            )
        },
        headers={
            "If-Match": current.headers["etag"],
            "Idempotency-Key": "recipient-profile-private-0001",
        },
    )
    assert private.status_code == 200
    app.dependency_overrides.clear()
    denied_private_target = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Interview",
            "message": "A private target profile cannot receive outreach.",
        },
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "target-private-send-0001",
        },
    )
    assert denied_private_target.status_code == 404


async def test_completed_contact_policy_opt_out_blocks_agent_outreach(api_client) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="mandate-opt-out-0001")
    as_principal(app, human("recipient"))
    current_policy = await client.get("/v1/contact-policy")
    assert current_policy.status_code == 200
    opted_out = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": False, "daily_request_limit": 5},
        headers={
            "Idempotency-Key": "mandate-opt-out-policy-0001",
            "If-Match": current_policy.headers["etag"],
        },
    )
    assert opted_out.status_code == 200
    app.dependency_overrides.clear()
    denied = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Interview",
            "message": "A completed opt-out must be observed before admission.",
        },
        headers={
            "Authorization": f"Bearer {issued.json()['grant']['key']}",
            "Idempotency-Key": "opt-out-send-0001",
        },
    )
    assert denied.status_code == 403


async def test_completed_block_or_report_blocks_later_agent_outreach(api_client) -> None:
    app, client, _ = await setup_identities(api_client)
    issued = await issue_mandate(client, key="mandate-block-0001")
    key = issued.json()["grant"]["key"]
    app.dependency_overrides.clear()
    first = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Interview",
            "message": "The recipient will block this request.",
        },
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": "block-first-send-0001"},
    )
    assert first.status_code == 201
    as_principal(app, human("recipient"))
    blocked = await client.post(
        f"/v1/contact-requests/{first.json()['id']}/block",
        headers={"Idempotency-Key": "mandate-contact-block-0001"},
    )
    assert blocked.status_code == 200
    app.dependency_overrides.clear()
    denied_block = await client.post(
        "/v1/agent-outreach",
        json={
            "target_agent_handle": "recipient-agent",
            "purpose": "Interview",
            "message": "A completed block must prevent a later admission.",
        },
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": "block-second-send-0001"},
    )
    assert denied_block.status_code == 404


async def test_a2a_outreach_and_status_rejections_use_stable_privacy_minimal_errors(
    api_client,
) -> None:
    app, client, _ = await setup_identities(api_client)
    outreach_body = {
        "target_agent_handle": "recipient-agent",
        "purpose": "D2 error mapping",
        "message": "The response must not echo this private request text.",
    }

    invalid_outreach = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-outreach-invalid-0001",
            "agent_outreach",
            **outreach_body,
            private_note="not allowed",
        ),
        headers={"A2A-Version": "1.0"},
    )
    assert_action_error(
        invalid_outreach,
        state="TASK_STATE_REJECTED",
        code="invalid_params",
        message="the action parameters are invalid",
    )

    invalid_status = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-status-invalid-0001",
            "get_agent_outreach_status",
            request_id="00000000-0000-0000-0000-000000000000",
            private_note="not allowed",
        ),
        headers={"A2A-Version": "1.0"},
    )
    assert_action_error(
        invalid_status,
        state="TASK_STATE_REJECTED",
        code="invalid_params",
        message="the action parameters are invalid",
    )
    for index, invalid_request_id in enumerate(
        (
            "00000000-0000-0000-0000-00000000000g",
            "00000000-0000-0000-0000-00000000000A",
        ),
        start=1,
    ):
        invalid_uuid_status = await client.post(
            "/a2a/message:send",
            json=a2a_action_payload(
                f"a2a-d2-status-invalid-uuid-{index:04d}",
                "get_agent_outreach_status",
                request_id=invalid_request_id,
            ),
            headers={"A2A-Version": "1.0"},
        )
        assert_action_error(
            invalid_uuid_status,
            state="TASK_STATE_REJECTED",
            code="invalid_params",
            message="the action parameters are invalid",
        )

    app.dependency_overrides.clear()
    anonymous_outreach = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-outreach-auth-0001",
            "agent_outreach",
            **outreach_body,
        ),
        headers={"A2A-Version": "1.0"},
    )
    assert_action_error(
        anonymous_outreach,
        state="TASK_STATE_AUTH_REQUIRED",
        code="auth_required",
        message="authentication is required for this action",
    )
    anonymous_status = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-status-auth-0001",
            "get_agent_outreach_status",
            request_id="00000000-0000-0000-0000-000000000000",
        ),
        headers={"A2A-Version": "1.0"},
    )
    assert_action_error(
        anonymous_status,
        state="TASK_STATE_AUTH_REQUIRED",
        code="auth_required",
        message="authentication is required for this action",
    )

    as_principal(app, human("sender"))
    human_denied = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-outreach-forbidden-0001",
            "agent_outreach",
            **outreach_body,
        ),
        headers={"A2A-Version": "1.0"},
    )
    assert_action_error(
        human_denied,
        state="TASK_STATE_REJECTED",
        code="request_rejected",
        message="the action request was not accepted",
    )

    issued = await issue_mandate(client, key="mandate-d2-errors-0001")
    raw_key = issued.json()["grant"]["key"]
    app.dependency_overrides.clear()
    missing_target = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-outreach-not-found-0001",
            "agent_outreach",
            target_agent_handle="missing-agent",
            purpose=outreach_body["purpose"],
            message=outreach_body["message"],
        ),
        headers={
            "Authorization": f"Bearer {raw_key}",
            "A2A-Version": "1.0",
            "Idempotency-Key": "a2a-d2-outreach-not-found-0001",
        },
    )
    assert_action_error(
        missing_target,
        state="TASK_STATE_REJECTED",
        code="request_rejected",
        message="the action request was not accepted",
    )
    assert (
        human_denied.json()["task"]["artifacts"][0]["parts"][0]["data"]
        == missing_target.json()["task"]["artifacts"][0]["parts"][0]["data"]
    )

    first = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-outreach-success-0001",
            "agent_outreach",
            **outreach_body,
        ),
        headers={
            "Authorization": f"Bearer {raw_key}",
            "A2A-Version": "1.0",
            "Idempotency-Key": "a2a-d2-outreach-success-0001",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    duplicate = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-outreach-conflict-0001",
            "agent_outreach",
            **outreach_body,
        ),
        headers={
            "Authorization": f"Bearer {raw_key}",
            "A2A-Version": "1.0",
            "Idempotency-Key": "a2a-d2-outreach-conflict-0001",
        },
    )
    assert_action_error(
        duplicate,
        state="TASK_STATE_REJECTED",
        code="conflict",
        message="the action request conflicted with current state",
    )

    request_id = first.json()["task"]["artifacts"][0]["parts"][0]["data"]["contact_request"]["id"]
    async with app.state.session_factory() as session:
        await session.execute(text("PRAGMA ignore_check_constraints = ON"))
        row = await session.get(ContactRequest, request_id)
        assert row is not None
        row.status = "corrupt"
        await session.commit()
    unavailable = await client.post(
        "/a2a/message:send",
        json=a2a_action_payload(
            "a2a-d2-status-unavailable-0001",
            "get_agent_outreach_status",
            request_id=request_id,
        ),
        headers={"Authorization": f"Bearer {raw_key}", "A2A-Version": "1.0"},
    )
    assert_action_error(
        unavailable,
        state="TASK_STATE_FAILED",
        code="service_unavailable",
        message="the action service is temporarily unavailable",
    )


async def test_agent_outreach_replay_requires_the_exact_originating_mandate_and_grant(
    api_client,
) -> None:
    app, client, _ = await setup_identities(api_client)
    first_mandate = await issue_mandate(client, key="origin-bound-mandate-0001")
    assert first_mandate.status_code == 201, first_mandate.text
    first_grant_key = first_mandate.json()["grant"]["key"]
    body = {
        "target_agent_handle": "recipient-agent",
        "purpose": "Exact originating authority",
        "message": "A different mandate must not replay this receipt.",
    }
    app.dependency_overrides.clear()
    first = await client.post(
        "/v1/agent-outreach",
        json=body,
        headers={
            "Authorization": f"Bearer {first_grant_key}",
            "Idempotency-Key": "origin-bound-outreach-0001",
        },
    )
    assert first.status_code == 201, first.text

    as_principal(app, human("sender"))
    alternate_identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "alternate-origin-agent",
            "display_name": "Alternate Origin Agent",
            "description": "A distinct source identity for origin binding.",
            "profile_handle": "sender-profile",
        },
        headers={"Idempotency-Key": "origin-bound-alternate-0001"},
    )
    assert alternate_identity.status_code == 201, alternate_identity.text
    alternate_mandate = await issue_mandate(
        client,
        key="origin-bound-mandate-0002",
        agent_handle="alternate-origin-agent",
    )
    assert alternate_mandate.status_code == 201, alternate_mandate.text
    alternate_grant_key = alternate_mandate.json()["grant"]["key"]
    app.dependency_overrides.clear()

    wrong_origin = await client.post(
        "/v1/agent-outreach",
        json=body,
        headers={
            "Authorization": f"Bearer {alternate_grant_key}",
            "Idempotency-Key": "origin-bound-outreach-0001",
        },
    )
    assert wrong_origin.status_code == 503, wrong_origin.text
    assert "sender-agent" not in wrong_origin.text
    assert "alternate-origin-agent" not in wrong_origin.text
    assert "recipient-agent" not in wrong_origin.text
    assert first_mandate.json()["id"] not in wrong_origin.text
    assert alternate_mandate.json()["id"] not in wrong_origin.text

    async with app.state.session_factory() as session:
        requests = (
            await session.scalars(
                select(ContactRequest).where(ContactRequest.origin == "agent_outreach")
            )
        ).all()
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "origin-bound-outreach-0001"
                )
            )
        ).all()
        assert len(requests) == 1
        assert len(receipts) == 1
        events = (
            await session.scalars(
                select(ChangeEvent).where(ChangeEvent.resource_id == requests[0].id)
            )
        ).all()
        assert len(events) == 2
        receipt = receipts[0]
        assert receipt.resource_id is not None
        assert first_mandate.json()["id"] not in receipt.resource_id
        assert alternate_mandate.json()["id"] not in receipt.resource_id
        assert first_grant_key not in receipt.resource_id
        assert alternate_grant_key not in receipt.resource_id
        assert "sender-agent" not in receipt.resource_id
        assert "alternate-origin-agent" not in receipt.resource_id
