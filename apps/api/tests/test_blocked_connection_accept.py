from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.auth import Principal, optional_principal, require_principal
from app.models import Connection, ConnectionBlock, ConnectionRequest, IdempotencyRecord

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
        headers={"Idempotency-Key": f"blocked-accept-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_blocked_pair_cannot_accept_a_pending_connection_request(api_client) -> None:
    app, client = api_client
    await _profile(app, client, "blocked-accept-recipient", "blocked-accept-recipient")
    await _profile(app, client, "blocked-accept-sender", "blocked-accept-sender")

    as_principal(app, human("blocked-accept-sender"))
    requested = await client.post(
        "/v1/connection-requests",
        json={
            "recipient_profile_handle": "blocked-accept-recipient",
            "messaging_requested": True,
        },
        headers={"Idempotency-Key": "blocked-accept-request"},
    )
    assert requested.status_code == 201, requested.text
    request_id = requested.json()["id"]
    as_principal(app, human("blocked-accept-recipient"))
    visible = await client.get("/v1/connection-requests/inbox")
    assert visible.status_code == 200, visible.text
    assert [row["id"] for row in visible.json()["requests"]] == [request_id]

    async with app.state.session_factory() as session:
        session.add(
            ConnectionBlock(
                id="37000000-0000-4000-8000-000000000001",
                blocker_owner_id="blocked-accept-sender",
                blocked_owner_id="blocked-accept-recipient",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    hidden = await client.get("/v1/connection-requests/inbox")
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["requests"] == []
    accepted = await client.post(
        f"/v1/connection-requests/{request_id}/accept",
        json={"messaging_consent": True},
        headers={"Idempotency-Key": "blocked-accept-decision"},
    )
    assert accepted.status_code == 404, accepted.text
    assert accepted.json()["detail"] == "connection request was not found"

    async with app.state.session_factory() as session:
        row = await session.get(ConnectionRequest, request_id)
        connections = await session.scalar(select(func.count()).select_from(Connection))
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "blocked-accept-decision"
                )
            )
        ).all()
    assert row is not None and row.status == "pending"
    assert connections == 0
    assert receipts == []
