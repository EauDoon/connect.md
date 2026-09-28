from __future__ import annotations

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import ContactRequest, IdempotencyRecord

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def _profile(app, client, subject: str, handle: str) -> None:
    as_principal(app, human(subject))
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", handle)},
        headers={"Idempotency-Key": f"block-contact-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_connection_block_rejects_a_later_contact_request(api_client) -> None:
    app, client = api_client
    await _profile(app, client, "block-contact-recipient", "block-contact-recipient")
    await _profile(app, client, "block-contact-sender", "block-contact-sender")
    await _profile(app, client, "block-contact-outsider", "block-contact-outsider")

    as_principal(app, human("block-contact-recipient"))
    policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 5},
        headers={
            "Idempotency-Key": "block-contact-policy-0001",
            "If-Match": '"policy-0"',
        },
    )
    assert policy.status_code == 200, policy.text

    as_principal(app, human("block-contact-sender"))
    requested = await client.post(
        "/v1/connection-requests",
        json={
            "recipient_profile_handle": "block-contact-recipient",
            "messaging_requested": False,
        },
        headers={"Idempotency-Key": "block-contact-connection-0001"},
    )
    assert requested.status_code == 201, requested.text
    as_principal(app, human("block-contact-recipient"))
    accepted = await client.post(
        f"/v1/connection-requests/{requested.json()['id']}/accept",
        json={"messaging_consent": False},
        headers={"Idempotency-Key": "block-contact-accept-0001"},
    )
    assert accepted.status_code == 200, accepted.text
    connection_id = (await client.get("/v1/connections")).json()["connections"][0]["id"]
    blocked = await client.post(
        f"/v1/connections/{connection_id}/block",
        headers={"Idempotency-Key": "block-contact-block-0001"},
    )
    assert blocked.status_code == 204, blocked.text

    as_principal(app, human("block-contact-sender"))
    denied = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": "block-contact-recipient",
            "purpose": "Introduce a blocked sender",
            "message": "This outreach must not be stored.",
        },
        headers={"Idempotency-Key": "block-contact-denied-0001"},
    )
    assert denied.status_code == 404, denied.text
    assert denied.json()["detail"] == "contact target was not found"
    assert "This outreach must not be stored." not in denied.text

    as_principal(app, human("block-contact-outsider"))
    allowed = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": "block-contact-recipient",
            "purpose": "Introduce an unblocked sender",
            "message": "This outreach is allowed.",
        },
        headers={"Idempotency-Key": "block-contact-allowed-0001"},
    )
    assert allowed.status_code == 201, allowed.text

    async with app.state.session_factory() as session:
        contacts = (await session.scalars(select(ContactRequest))).all()
        denied_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "block-contact-denied-0001"
                )
            )
        ).all()
    assert [row.sender_owner_id for row in contacts] == ["block-contact-outsider"]
    assert denied_receipts == []
