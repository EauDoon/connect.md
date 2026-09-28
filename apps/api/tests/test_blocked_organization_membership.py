from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import ConnectionBlock, IdempotencyRecord, OrganizationMembership

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
        headers={"Idempotency-Key": f"blocked-member-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_connection_block_prevents_organization_membership(api_client) -> None:
    app, client = api_client
    await _profile(app, client, "blocked-member-owner", "blocked-member-owner")
    await _profile(app, client, "blocked-member-person", "blocked-member-person")
    await _profile(app, client, "blocked-member-clear", "blocked-member-clear")

    as_principal(app, human("blocked-member-owner"))
    created = await client.post(
        "/v1/organizations",
        json={"slug": "blocked-member-org", "name": "Blocked Member Org", "visibility": "private"},
        headers={"Idempotency-Key": "blocked-member-org"},
    )
    assert created.status_code == 201, created.text

    async with app.state.session_factory() as session:
        session.add(
            ConnectionBlock(
                id="40000000-0000-4000-8000-000000000001",
                blocker_owner_id="blocked-member-owner",
                blocked_owner_id="blocked-member-person",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    denied = await client.post(
        "/v1/organizations/blocked-member-org/admins",
        json={"member_profile_handle": "blocked-member-person", "role": "admin"},
        headers={"Idempotency-Key": "blocked-member-invite-denied"},
    )
    assert denied.status_code == 404, denied.text
    assert denied.json()["detail"] == "profile was not found"

    invited = await client.post(
        "/v1/organizations/blocked-member-org/admins",
        json={"member_profile_handle": "blocked-member-clear", "role": "member"},
        headers={"Idempotency-Key": "blocked-member-invite-clear"},
    )
    assert invited.status_code == 201, invited.text
    membership_id = invited.json()["id"]

    async with app.state.session_factory() as session:
        session.add(
            ConnectionBlock(
                id="40000000-0000-4000-8000-000000000002",
                blocker_owner_id="blocked-member-clear",
                blocked_owner_id="blocked-member-owner",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    as_principal(app, human("blocked-member-clear"))
    inbox = await client.get("/v1/organization-membership-invitations")
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["invitations"] == []
    accepted = await client.post(
        f"/v1/organizations/blocked-member-org/memberships/{membership_id}/accept",
        headers={"Idempotency-Key": "blocked-member-accept"},
    )
    assert accepted.status_code == 404, accepted.text
    assert accepted.json()["detail"] == "organization invitation was not found"

    async with app.state.session_factory() as session:
        rows = (await session.scalars(select(OrganizationMembership))).all()
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key.in_(
                        ("blocked-member-invite-denied", "blocked-member-accept")
                    )
                )
            )
        ).all()
    assert [(row.member_owner_id, row.status) for row in rows] == [
        ("blocked-member-clear", "invited")
    ]
    assert receipts == []
