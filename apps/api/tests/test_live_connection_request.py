from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import Connection, ConnectionRequest, IdempotencyRecord

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
        headers={"Idempotency-Key": f"live-connection-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_live_connection_blocks_a_new_request_after_its_request_expires(api_client) -> None:
    app, client = api_client
    await _profile(app, client, "live-connection-recipient", "live-connection-recipient")
    await _profile(app, client, "live-connection-sender", "live-connection-sender")

    as_principal(app, human("live-connection-sender"))
    requested = await client.post(
        "/v1/connection-requests",
        json={
            "recipient_profile_handle": "live-connection-recipient",
            "messaging_requested": False,
        },
        headers={"Idempotency-Key": "live-connection-request-0001"},
    )
    assert requested.status_code == 201, requested.text
    request_id = requested.json()["id"]
    as_principal(app, human("live-connection-recipient"))
    accepted = await client.post(
        f"/v1/connection-requests/{request_id}/accept",
        json={"messaging_consent": False},
        headers={"Idempotency-Key": "live-connection-accept-0001"},
    )
    assert accepted.status_code == 200, accepted.text
    connection_id = (await client.get("/v1/connections")).json()["connections"][0]["id"]

    async with app.state.session_factory() as session:
        request_row = await session.get(ConnectionRequest, request_id)
        assert request_row is not None
        request_row.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    as_principal(app, human("live-connection-sender"))
    replacement = await client.post(
        "/v1/connection-requests",
        json={
            "recipient_profile_handle": "live-connection-recipient",
            "messaging_requested": True,
        },
        headers={"Idempotency-Key": "live-connection-replacement-0001"},
    )
    assert replacement.status_code == 409, replacement.text
    assert replacement.json()["detail"] == "an active connection already exists"

    async with app.state.session_factory() as session:
        request_row = await session.get(ConnectionRequest, request_id)
        connection = await session.get(Connection, connection_id)
        requests = (await session.scalars(select(ConnectionRequest))).all()
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "live-connection-replacement-0001"
                )
            )
        ).all()
    assert request_row is not None and request_row.status == "accepted"
    assert connection is not None and connection.status == "active"
    assert [row.id for row in requests] == [request_id]
    assert receipts == []
