from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
        headers={"Idempotency-Key": f"expired-contact-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_expired_pending_contact_request_releases_the_pair(api_client) -> None:
    app, client = api_client
    await _profile(app, client, "expired-contact-recipient", "expired-contact-recipient")
    await _profile(app, client, "expired-contact-sender", "expired-contact-sender")

    as_principal(app, human("expired-contact-recipient"))
    policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 5},
        headers={
            "Idempotency-Key": "expired-contact-policy-0001",
            "If-Match": '"policy-0"',
        },
    )
    assert policy.status_code == 200, policy.text

    as_principal(app, human("expired-contact-sender"))
    first = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": "expired-contact-recipient",
            "purpose": "Original introduction",
            "message": "This request has expired.",
        },
        headers={"Idempotency-Key": "expired-contact-first-0001"},
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    async with app.state.session_factory() as session:
        row = await session.get(ContactRequest, first_id)
        assert row is not None
        row.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    as_principal(app, human("expired-contact-recipient"))
    inbox = await client.get("/v1/contact-requests/inbox")
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["requests"] == []

    as_principal(app, human("expired-contact-sender"))
    replacement = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": "expired-contact-recipient",
            "purpose": "Replacement introduction",
            "message": "The expired request must not keep this pair pending.",
        },
        headers={"Idempotency-Key": "expired-contact-replacement-0001"},
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["id"] != first_id
    assert replacement.json()["status"] == "pending"

    blocked = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": "expired-contact-recipient",
            "purpose": "Still pending",
            "message": "A live pending request must still be refused.",
        },
        headers={"Idempotency-Key": "expired-contact-live-0001"},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == "a contact request to this recipient is already pending"

    async with app.state.session_factory() as session:
        expired = await session.get(ContactRequest, first_id)
        live_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "expired-contact-live-0001"
                )
            )
        ).all()
    assert expired is not None
    assert expired.status == "rejected"
    assert expired.decision_actor_id == "system:retention"
    assert expired.message == "This request has expired."
    assert live_receipts == []
