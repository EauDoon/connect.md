from __future__ import annotations

from app.auth import Principal, optional_principal, require_principal

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
        headers={"Idempotency-Key": f"blocked-inbox-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_recipient_can_list_contact_requests_they_blocked(api_client) -> None:
    app, client = api_client
    await _profile(app, client, "blocked-inbox-recipient", "blocked-inbox-recipient")
    await _profile(app, client, "blocked-inbox-sender", "blocked-inbox-sender")
    as_principal(app, human("blocked-inbox-recipient"))
    policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 5},
        headers={
            "Idempotency-Key": "blocked-inbox-policy",
            "If-Match": '"policy-0"',
        },
    )
    assert policy.status_code == 200, policy.text

    as_principal(app, human("blocked-inbox-sender"))
    created = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": "blocked-inbox-recipient",
            "purpose": "Introduction before a block",
            "message": "The recipient blocked this request.",
        },
        headers={"Idempotency-Key": "blocked-inbox-create"},
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    as_principal(app, human("blocked-inbox-recipient"))
    pending = await client.get("/v1/contact-requests/inbox", params={"status": "pending"})
    assert [row["id"] for row in pending.json()["requests"]] == [request_id]
    blocked = await client.post(
        f"/v1/contact-requests/{request_id}/block",
        headers={"Idempotency-Key": "blocked-inbox-block"},
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"

    default_inbox = await client.get("/v1/contact-requests/inbox")
    assert default_inbox.status_code == 200, default_inbox.text
    assert default_inbox.json()["requests"] == []
    listed = await client.get("/v1/contact-requests/inbox", params={"status": "blocked"})
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["requests"]] == [request_id]
    assert listed.json()["requests"][0]["status"] == "blocked"
