from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import ConnectionBlock, ContactRequest, IdempotencyRecord

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
        headers={"Idempotency-Key": f"inflight-contact-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_connection_block_hides_an_inflight_contact_request(api_client) -> None:
    app, client = api_client
    await _profile(app, client, "inflight-contact-recipient", "inflight-contact-recipient")
    await _profile(app, client, "inflight-contact-sender", "inflight-contact-sender")
    as_principal(app, human("inflight-contact-recipient"))
    policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 5},
        headers={
            "Idempotency-Key": "inflight-contact-policy",
            "If-Match": '"policy-0"',
        },
    )
    assert policy.status_code == 200, policy.text

    as_principal(app, human("inflight-contact-sender"))
    created = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": "inflight-contact-recipient",
            "purpose": "Introduce before the block",
            "message": "This private note must disappear from the inbox after a block.",
        },
        headers={"Idempotency-Key": "inflight-contact-create"},
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    as_principal(app, human("inflight-contact-recipient"))
    visible = await client.get("/v1/contact-requests/inbox")
    assert [row["id"] for row in visible.json()["requests"]] == [request_id]

    async with app.state.session_factory() as session:
        session.add(
            ConnectionBlock(
                id="38000000-0000-4000-8000-000000000001",
                blocker_owner_id="inflight-contact-recipient",
                blocked_owner_id="inflight-contact-sender",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    hidden = await client.get("/v1/contact-requests/inbox")
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["requests"] == []
    assert "This private note must disappear" not in hidden.text
    decision = await client.post(
        f"/v1/contact-requests/{request_id}/accept",
        headers={"Idempotency-Key": "inflight-contact-accept"},
    )
    assert decision.status_code == 404, decision.text
    assert decision.json()["detail"] == "contact request was not found"

    async with app.state.session_factory() as session:
        row = await session.get(ContactRequest, request_id)
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "inflight-contact-accept"
                )
            )
        ).all()
    assert row is not None and row.status == "pending"
    assert receipts == []
