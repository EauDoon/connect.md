from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    ChangeEvent,
    Connection,
    ConnectionBlock,
    ConnectionRequest,
    ConnectionRequestRateBucket,
    Conversation,
    IdempotencyRecord,
    Message,
    Notification,
)

from .helpers import profile_markdown


def human(subject: str, *, impersonated: bool = False) -> Principal:
    return Principal(
        subject=subject,
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        is_impersonated=impersonated,
    )


def as_principal(app, value: Principal) -> None:
    async def current() -> Principal:
        return value

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def test_impersonated_clerk_principal_cannot_use_private_social_surfaces(
    api_client,
) -> None:
    app, client = api_client
    as_principal(app, human("impersonated-human", impersonated=True))

    private_read = await client.get("/v1/conversations")
    private_write = await client.post(
        "/v1/connection-requests",
        json={
            "recipient_profile_handle": "target-profile",
            "messaging_requested": False,
        },
        headers={"Idempotency-Key": "impersonated-social-write-0001"},
    )
    private_conversation_write = await client.post(
        "/v1/conversations",
        json={"connection_id": "00000000-0000-0000-0000-000000000000"},
        headers={"Idempotency-Key": "impersonated-conversation-write-0001"},
    )

    assert {
        private_read.status_code,
        private_write.status_code,
        private_conversation_write.status_code,
    } == {403}
    assert {private_read.json()["detail"], private_write.json()["detail"]} == {
        "this private social operation requires a signed-in human"
    }
    assert private_conversation_write.json()["detail"] == (
        "this private social operation requires a signed-in human"
    )

    as_principal(app, human("ordinary-human"))
    allowed = await client.get("/v1/conversations")
    assert allowed.status_code == 200
    assert allowed.json() == {"conversations": [], "next_cursor": None}


async def test_private_connections_conversations_messages_and_notifications(api_client) -> None:
    app, client = api_client
    as_principal(app, human("recipient"))
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "private-social-recipient-profile-create"},
    )
    assert profile.status_code == 201, profile.text

    as_principal(app, human("unprofiled"))
    missing_profile = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": False},
        headers={"Idempotency-Key": "unprofiled-connection-request-0001"},
    )
    assert missing_profile.status_code == 409

    as_principal(app, human("sender"))
    sender_profile = await client.post(
        "/v1/profiles",
        json={
            "markdown": profile_markdown(visibility="public").replace(
                "ada-lovelace", "sender-profile"
            )
        },
        headers={"Idempotency-Key": "private-social-sender-profile-create"},
    )
    assert sender_profile.status_code == 201, sender_profile.text
    request_headers = {"Idempotency-Key": "connection-request-0001"}
    connection_request = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": True},
        headers=request_headers,
    )
    assert connection_request.status_code == 201, connection_request.text
    assert connection_request.json()["direction"] == "outbound"
    assert connection_request.json()["counterparty_profile_handle"] == "ada-lovelace"
    assert connection_request.json()["messaging_consent"] is None
    replay = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": True},
        headers=request_headers,
    )
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"
    duplicate = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": True},
        headers={"Idempotency-Key": "connection-request-duplicate-0001"},
    )
    assert duplicate.status_code == 409

    as_principal(app, human("recipient"))
    self_request = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": False},
        headers={"Idempotency-Key": "connection-request-self-0001"},
    )
    assert self_request.status_code == 409
    inbox = await client.get("/v1/connection-requests/inbox?limit=1")
    assert inbox.status_code == 200
    assert inbox.json()["requests"][0]["id"] == connection_request.json()["id"]
    accepted = await client.post(
        f"/v1/connection-requests/{connection_request.json()['id']}/accept",
        json={"messaging_consent": True},
        headers={"Idempotency-Key": "connection-accept-0001"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["counterparty_profile_handle"] == "sender-profile"

    as_principal(app, human("sender"))
    sender_changes = await client.get("/v1/changes")
    assert sender_changes.status_code == 200
    sender_social_events = [
        event
        for event in sender_changes.json()["events"]
        if event["resource_type"]
        in {"connection_request", "connection", "conversation", "message", "notification"}
    ]
    assert sender_social_events
    assert all(event["actor_id"] not in {"sender", "recipient"} for event in sender_social_events)
    assert all(
        all(value not in {"sender", "recipient"} for value in event["data"].values())
        for event in sender_social_events
    )
    as_principal(app, human("recipient"))
    recipient_changes = await client.get("/v1/changes")
    assert recipient_changes.status_code == 200
    recipient_social_events = [
        event
        for event in recipient_changes.json()["events"]
        if event["resource_type"]
        in {"connection_request", "connection", "conversation", "message", "notification"}
    ]
    assert recipient_social_events
    assert all(
        event["actor_id"] not in {"sender", "recipient"} for event in recipient_social_events
    )
    assert all(
        all(value not in {"sender", "recipient"} for value in event["data"].values())
        for event in recipient_social_events
    )

    as_principal(app, human("sender"))
    api_key = await client.post(
        "/v1/api-keys",
        json={"scopes": ["changes:read"]},
        headers={"Idempotency-Key": "private-social-api-key-0001"},
    )
    assert api_key.status_code == 201, api_key.text
    app.dependency_overrides.clear()
    key_changes = await client.get(
        "/v1/changes", headers={"Authorization": f"Bearer {api_key.json()['key']}"}
    )
    assert key_changes.status_code == 200
    assert all(
        event["resource_type"]
        not in {"connection_request", "connection", "conversation", "message", "notification"}
        for event in key_changes.json()["events"]
    )

    as_principal(app, human("sender"))
    unavailable_scope = await client.post(
        "/v1/agent-grants",
        json={
            "name": "Unavailable social scope",
            "mode": "direct",
            "resource": {"type": "owner"},
            "scopes": ["connections:read"],
        },
        headers={"Idempotency-Key": "private-social-invalid-grant-0001"},
    )
    assert unavailable_scope.status_code == 422
    grant = await client.post(
        "/v1/agent-grants",
        json={
            "name": "Changes reader",
            "mode": "direct",
            "resource": {"type": "owner"},
            "scopes": ["changes:read"],
        },
        headers={"Idempotency-Key": "private-social-changes-grant-0001"},
    )
    assert grant.status_code == 201, grant.text
    app.dependency_overrides.clear()
    grant_changes = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {grant.json()['key']}"},
        json={
            "jsonrpc": "2.0",
            "id": "private-social-filter",
            "method": "tools/call",
            "params": {"name": "get_changes", "arguments": {}},
        },
    )
    assert grant_changes.status_code == 200, grant_changes.text
    grant_events = grant_changes.json()["result"]["structuredContent"]
    assert all(
        event["resource_type"]
        not in {
            "connection_request",
            "connection",
            "conversation",
            "message",
            "notification",
        }
        for event in grant_events
    )

    as_principal(app, human("recipient"))
    connections = await client.get("/v1/connections")
    assert connections.status_code == 200
    connection_id = connections.json()["connections"][0]["id"]
    assert connections.json()["connections"][0]["counterparty_profile_handle"] == "sender-profile"

    as_principal(app, human("sender"))
    sender_connections = await client.get("/v1/connections")
    assert sender_connections.status_code == 200
    assert sender_connections.json()["connections"][0]["id"] == connection_id
    conversation = await client.post(
        "/v1/conversations",
        json={"connection_id": connection_id},
        headers={"Idempotency-Key": "conversation-0001"},
    )
    assert conversation.status_code == 201, conversation.text
    assert conversation.headers.get("idempotency-replayed") is None
    assert conversation.headers["location"].endswith("/messages")
    conversation_id = conversation.json()["id"]
    assert conversation.json()["counterparty_profile_handle"] == "ada-lovelace"
    conversation_retry = await client.post(
        "/v1/conversations",
        json={"connection_id": connection_id},
        headers={"Idempotency-Key": "conversation-0001"},
    )
    assert conversation_retry.status_code == 201, conversation_retry.text
    assert conversation_retry.content == conversation.content
    assert conversation_retry.headers["location"] == conversation.headers["location"]
    assert conversation_retry.headers["idempotency-replayed"] == "true"
    duplicate_conversation = await client.post(
        "/v1/conversations",
        json={"connection_id": connection_id},
        headers={"Idempotency-Key": "conversation-duplicate-0001"},
    )
    assert duplicate_conversation.status_code == 409

    private_markdown = "A private note with [a link](https://example.test/private)."
    message = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"markdown": private_markdown},
        headers={"Idempotency-Key": "message-0001"},
    )
    assert message.status_code == 201, message.text
    assert private_markdown not in message.text
    second = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"markdown": "Second private Markdown message."},
        headers={"Idempotency-Key": "message-0002"},
    )
    assert second.status_code == 201
    third = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"markdown": "Third private Markdown message."},
        headers={"Idempotency-Key": "message-0003"},
    )
    assert third.status_code == 201
    closed_retry_markdown = "Message sent immediately before the conversation closed."
    closed_retry_first = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"markdown": closed_retry_markdown},
        headers={"Idempotency-Key": "message-closed-retry-0001"},
    )
    assert closed_retry_first.status_code == 201, closed_retry_first.text
    assert closed_retry_first.headers.get("idempotency-replayed") is None
    active_retry = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"markdown": closed_retry_markdown},
        headers={"Idempotency-Key": "message-closed-retry-0001"},
    )
    assert active_retry.status_code == 201, active_retry.text
    assert active_retry.content == closed_retry_first.content
    assert active_retry.headers["idempotency-replayed"] == "true"

    as_principal(app, human("recipient"))
    received = await client.get(f"/v1/conversations/{conversation_id}/messages?limit=1")
    assert received.status_code == 200
    assert received.json()["messages"][0]["markdown"] == private_markdown
    assert received.json()["messages"][0]["direction"] == "received"
    assert '"sender"' not in received.text
    assert received.json()["next_cursor"]
    malformed_cursor = await client.get(
        f"/v1/conversations/{conversation_id}/messages", params={"cursor": "not-a-cursor"}
    )
    assert malformed_cursor.status_code == 400
    bounded_cursor = await client.get(f"/v1/conversations/{conversation_id}/messages?limit=101")
    assert bounded_cursor.status_code == 422
    notifications = await client.get("/v1/notifications?limit=1")
    assert notifications.status_code == 200
    assert private_markdown not in notifications.text
    assert '"sender"' not in notifications.text
    assert '"recipient"' not in notifications.text
    first_notification = notifications.json()["notifications"][0]
    assert first_notification["actor_owner_id"] != "sender"
    marked = await client.post(
        f"/v1/notifications/{first_notification['id']}/read",
        headers={"Idempotency-Key": "notification-read-0001"},
    )
    assert marked.status_code == 200
    assert marked.json()["read_at"]

    as_principal(app, human("unrelated"))
    cross_user_conversation = await client.post(
        "/v1/conversations",
        json={"connection_id": connection_id},
        headers={"Idempotency-Key": "conversation-cross-user-0001"},
    )
    assert cross_user_conversation.status_code == 404
    cross_user_messages = await client.get(f"/v1/conversations/{conversation_id}/messages")
    assert cross_user_messages.status_code == 404
    assert (await client.get("/v1/connections")).json()["connections"] == []

    agent = Principal(
        subject="sender",
        method="agent_grant",
        scopes=frozenset({"connections:write", "conversations:write"}),
        grant_mode="direct",
        resource_type="owner",
    )
    as_principal(app, agent)
    agent_request = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": True},
        headers={"Idempotency-Key": "agent-connection-0001"},
    )
    assert agent_request.status_code == 403
    agent_message = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"markdown": "An agent must not send this."},
        headers={"Idempotency-Key": "agent-message-0001"},
    )
    assert agent_message.status_code == 403

    as_principal(app, human("recipient"))
    blocked = await client.post(
        f"/v1/connections/{connection_id}/block",
        headers={"Idempotency-Key": "connection-block-0001"},
    )
    assert blocked.status_code == 204
    assert blocked.content == b""
    as_principal(app, human("sender"))
    blocked_conversation_retry = await client.post(
        "/v1/conversations",
        json={"connection_id": connection_id},
        headers={"Idempotency-Key": "conversation-0001"},
    )
    assert blocked_conversation_retry.status_code == 201, blocked_conversation_retry.text
    assert blocked_conversation_retry.content == conversation.content
    assert blocked_conversation_retry.headers["location"] == conversation.headers["location"]
    assert blocked_conversation_retry.headers["idempotency-replayed"] == "true"
    async with app.state.session_factory() as session:
        stored_conversations = (
            await session.scalars(
                select(Conversation).where(Conversation.connection_id == connection_id)
            )
        ).all()
        conversation_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "conversation-0001"
                )
            )
        ).all()
    assert len(stored_conversations) == 1
    assert len(conversation_receipts) == 1
    closed_retry = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"markdown": closed_retry_markdown},
        headers={"Idempotency-Key": "message-closed-retry-0001"},
    )
    assert closed_retry.status_code == 404, closed_retry.text
    assert closed_retry.json()["detail"] == "conversation was not found"
    assert closed_retry.headers.get("idempotency-replayed") is None
    async with app.state.session_factory() as session:
        stored_messages = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.id == closed_retry_first.json()["id"],
                )
            )
        ).all()
        message_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "message-closed-retry-0001"
                )
            )
        ).all()
    assert len(stored_messages) == 1
    assert len(message_receipts) == 1
    blocked_connections = await client.get("/v1/connections")
    assert blocked_connections.status_code == 200
    assert blocked_connections.json()["connections"] == []
    blocked_target = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": True},
        headers={"Idempotency-Key": "blocked-enumeration-0001"},
    )
    assert blocked_target.status_code == 404

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "message-0001")
        )
        assert receipt is not None and private_markdown not in receipt.response_body
        events = (await session.scalars(select(ChangeEvent))).all()
        assert all(private_markdown not in event.payload for event in events)
        stored_notifications = (await session.scalars(select(Notification))).all()
        assert stored_notifications
        assert all(private_markdown not in str(row.__dict__) for row in stored_notifications)


async def test_remove_connection_is_durably_idempotent(api_client) -> None:
    app, client = api_client
    as_principal(app, human("remove-recipient"))
    recipient_profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "remove-recipient-profile-create"},
    )
    assert recipient_profile.status_code == 201, recipient_profile.text

    as_principal(app, human("remove-sender"))
    sender_profile = await client.post(
        "/v1/profiles",
        json={
            "markdown": profile_markdown(visibility="public").replace(
                "ada-lovelace", "remove-sender-profile"
            )
        },
        headers={"Idempotency-Key": "remove-sender-profile-create"},
    )
    assert sender_profile.status_code == 201, sender_profile.text
    connection_request = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": False},
        headers={"Idempotency-Key": "remove-connection-request"},
    )
    assert connection_request.status_code == 201, connection_request.text

    as_principal(app, human("remove-recipient"))
    accepted = await client.post(
        f"/v1/connection-requests/{connection_request.json()['id']}/accept",
        json={"messaging_consent": False},
        headers={"Idempotency-Key": "remove-connection-accept"},
    )
    assert accepted.status_code == 200, accepted.text

    as_principal(app, human("remove-sender"))
    connections = await client.get("/v1/connections")
    assert connections.status_code == 200
    connection_id = connections.json()["connections"][0]["id"]
    path = f"/v1/connections/{connection_id}"

    missing_key = await client.delete(path)
    assert missing_key.status_code == 428

    as_principal(app, human("remove-outsider"))
    nonparticipant = await client.delete(path, headers={"Idempotency-Key": "remove-nonparticipant"})
    assert nonparticipant.status_code == 404

    agent = Principal(
        subject="remove-sender",
        method="agent_grant",
        scopes=frozenset({"connections:write"}),
        grant_mode="direct",
        resource_type="owner",
    )
    as_principal(app, agent)
    agent_attempt = await client.delete(path, headers={"Idempotency-Key": "remove-agent-attempt"})
    assert agent_attempt.status_code == 403

    as_principal(app, human("remove-sender"))
    key = "remove-connection-0001"
    first = await client.delete(path, headers={"Idempotency-Key": key})
    assert first.status_code == 204
    assert first.content == b""
    assert "content-type" not in first.headers

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)
        )
        assert receipt is not None
        assert receipt.response_status == 204
        assert receipt.response_body == ""
        assert receipt.resource_type == "connection"
        assert receipt.resource_id == connection_id
        row = await session.get(Connection, connection_id)
        assert row is not None
        await session.delete(row)
        await session.commit()

    replay = await client.delete(path, headers={"Idempotency-Key": key})
    assert replay.status_code == 204
    assert replay.content == b""
    assert "content-type" not in replay.headers
    assert replay.headers["idempotency-replayed"] == "true"

    collision = await client.delete(
        "/v1/connections/different-connection",
        headers={"Idempotency-Key": key},
    )
    assert collision.status_code == 409


async def test_connection_request_race_and_durable_quota(api_client) -> None:
    app, client = api_client
    as_principal(app, human("race_recipient"))
    first_profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "connection-race-recipient-profile-create"},
    )
    assert first_profile.status_code == 201
    as_principal(app, human("quota_recipient"))
    second_profile = await client.post(
        "/v1/profiles",
        json={
            "markdown": profile_markdown(visibility="public").replace(
                "ada-lovelace", "grace-hopper"
            )
        },
        headers={"Idempotency-Key": "connection-quota-recipient-profile-create"},
    )
    assert second_profile.status_code == 201

    as_principal(app, human("race_sender"))
    sender_profile = await client.post(
        "/v1/profiles",
        json={
            "markdown": profile_markdown(visibility="public").replace("ada-lovelace", "race-sender")
        },
        headers={"Idempotency-Key": "connection-race-sender-profile-create"},
    )
    assert sender_profile.status_code == 201
    body = {"recipient_profile_handle": "ada-lovelace", "messaging_requested": False}
    first, second = await asyncio.gather(
        client.post(
            "/v1/connection-requests",
            json=body,
            headers={"Idempotency-Key": "connection-race-0001"},
        ),
        client.post(
            "/v1/connection-requests",
            json=body,
            headers={"Idempotency-Key": "connection-race-0002"},
        ),
    )
    assert sorted((first.status_code, second.status_code)) == [201, 409]

    async with app.state.session_factory() as session:
        bucket = await session.get(
            ConnectionRequestRateBucket,
            ("race_sender", datetime.now(UTC).date()),
        )
        assert bucket is not None
        bucket.request_count = 20
        await session.commit()
    limited = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "grace-hopper", "messaging_requested": False},
        headers={"Idempotency-Key": "connection-rate-limit-0001"},
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "86400"


async def test_blocked_rows_do_not_break_connection_or_conversation_pagination(api_client) -> None:
    app, client = api_client
    now = datetime.now(UTC)
    expiry = now + timedelta(days=1)
    async with app.state.session_factory() as session:
        blocked_request = ConnectionRequest(
            id="blocked-request",
            pair_owner_low="blocked",
            pair_owner_high="owner",
            requester_owner_id="owner",
            recipient_owner_id="blocked",
            requester_profile_handle="owner-profile",
            recipient_profile_handle="blocked-profile",
            requested_messaging=True,
            recipient_messaging_consent=True,
            status="accepted",
            requester_actor_id="owner",
            requester_actor_method="clerk_jwt",
            created_at=now,
            updated_at=now,
            retention_expires_at=expiry,
        )
        visible_request = ConnectionRequest(
            id="visible-request",
            pair_owner_low="owner",
            pair_owner_high="visible",
            requester_owner_id="owner",
            recipient_owner_id="visible",
            requester_profile_handle="owner-profile",
            recipient_profile_handle="visible-profile",
            requested_messaging=True,
            recipient_messaging_consent=True,
            status="accepted",
            requester_actor_id="owner",
            requester_actor_method="clerk_jwt",
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
            retention_expires_at=expiry,
        )
        blocked_connection = Connection(
            id="blocked-connection",
            connection_request_id=blocked_request.id,
            pair_owner_low="blocked",
            pair_owner_high="owner",
            requester_owner_id="owner",
            recipient_owner_id="blocked",
            requester_profile_handle="owner-profile",
            recipient_profile_handle="blocked-profile",
            requested_messaging=True,
            recipient_messaging_consent=True,
            messaging_enabled=True,
            status="active",
            created_at=now,
            updated_at=now,
            retention_expires_at=expiry,
        )
        visible_connection = Connection(
            id="visible-connection",
            connection_request_id=visible_request.id,
            pair_owner_low="owner",
            pair_owner_high="visible",
            requester_owner_id="owner",
            recipient_owner_id="visible",
            requester_profile_handle="owner-profile",
            recipient_profile_handle="visible-profile",
            requested_messaging=True,
            recipient_messaging_consent=True,
            messaging_enabled=True,
            status="active",
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
            retention_expires_at=expiry,
        )
        session.add_all(
            [
                blocked_request,
                visible_request,
                blocked_connection,
                visible_connection,
                ConnectionBlock(
                    id="owner-blocks-blocked",
                    blocker_owner_id="owner",
                    blocked_owner_id="blocked",
                    created_at=now,
                ),
                Conversation(
                    id="blocked-conversation",
                    connection_id=blocked_connection.id,
                    pair_owner_low="blocked",
                    pair_owner_high="owner",
                    status="active",
                    created_by_owner_id="owner",
                    created_at=now,
                    retention_expires_at=expiry,
                ),
                Conversation(
                    id="visible-conversation",
                    connection_id=visible_connection.id,
                    pair_owner_low="owner",
                    pair_owner_high="visible",
                    status="active",
                    created_by_owner_id="owner",
                    created_at=now - timedelta(minutes=1),
                    retention_expires_at=expiry,
                ),
            ]
        )
        await session.commit()

    as_principal(app, human("owner"))
    connections = await client.get("/v1/connections?limit=1")
    assert connections.status_code == 200, connections.text
    assert [row["id"] for row in connections.json()["connections"]] == ["visible-connection"]
    assert connections.json()["next_cursor"] is None
    conversations = await client.get("/v1/conversations?limit=1")
    assert conversations.status_code == 200, conversations.text
    assert [row["id"] for row in conversations.json()["conversations"]] == ["visible-conversation"]
    assert conversations.json()["next_cursor"] is None


async def test_expired_active_request_does_not_block_a_new_connection_request(api_client) -> None:
    app, client = api_client
    as_principal(app, human("recipient"))
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "expired-request-recipient-profile-create"},
    )
    assert profile.status_code == 201
    now = datetime.now(UTC)
    as_principal(app, human("sender"))
    sender_profile = await client.post(
        "/v1/profiles",
        json={
            "markdown": profile_markdown(visibility="public").replace(
                "ada-lovelace", "sender-profile"
            )
        },
        headers={"Idempotency-Key": "expired-request-sender-profile-create"},
    )
    assert sender_profile.status_code == 201
    async with app.state.session_factory() as session:
        session.add(
            ConnectionRequest(
                id="expired-request",
                pair_owner_low="recipient",
                pair_owner_high="sender",
                requester_owner_id="sender",
                recipient_owner_id="recipient",
                requester_profile_handle="sender-profile",
                recipient_profile_handle="ada-lovelace",
                requested_messaging=False,
                status="pending",
                requester_actor_id="sender",
                requester_actor_method="clerk_jwt",
                created_at=now - timedelta(days=366),
                updated_at=now - timedelta(days=366),
                retention_expires_at=now - timedelta(seconds=1),
            )
        )
        await session.commit()

    as_principal(app, human("sender"))
    replacement = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": False},
        headers={"Idempotency-Key": "expired-request-replacement-0001"},
    )
    assert replacement.status_code == 201, replacement.text
    async with app.state.session_factory() as session:
        expired = await session.get(ConnectionRequest, "expired-request")
        assert expired is not None and expired.status == "rejected"


async def test_expired_connection_can_reconnect_without_reviving_its_conversation(
    api_client,
) -> None:
    app, client = api_client
    as_principal(app, human("recipient"))
    assert (
        await client.post(
            "/v1/profiles",
            json={"markdown": profile_markdown(visibility="public")},
            headers={"Idempotency-Key": "reconnect-recipient-profile-create"},
        )
    ).status_code == 201
    as_principal(app, human("sender"))
    assert (
        await client.post(
            "/v1/profiles",
            json={
                "markdown": profile_markdown(visibility="public").replace(
                    "ada-lovelace", "sender-profile"
                )
            },
            headers={"Idempotency-Key": "reconnect-sender-profile-create"},
        )
    ).status_code == 201
    first_request = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": True},
        headers={"Idempotency-Key": "reconnect-first-request-0001"},
    )
    assert first_request.status_code == 201
    as_principal(app, human("recipient"))
    assert (
        await client.post(
            f"/v1/connection-requests/{first_request.json()['id']}/accept",
            json={"messaging_consent": True},
            headers={"Idempotency-Key": "reconnect-first-accept-0001"},
        )
    ).status_code == 200
    first_connection_id = (await client.get("/v1/connections")).json()["connections"][0]["id"]
    as_principal(app, human("sender"))
    first_conversation = await client.post(
        "/v1/conversations",
        json={"connection_id": first_connection_id},
        headers={"Idempotency-Key": "reconnect-first-conversation-0001"},
    )
    assert first_conversation.status_code == 201

    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        first_connection = await session.get(Connection, first_connection_id)
        first_request_row = await session.get(ConnectionRequest, first_request.json()["id"])
        assert first_connection is not None and first_request_row is not None
        first_connection.retention_expires_at = now - timedelta(seconds=1)
        first_request_row.retention_expires_at = now - timedelta(seconds=1)
        await session.commit()

    replacement_request = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "ada-lovelace", "messaging_requested": True},
        headers={"Idempotency-Key": "reconnect-replacement-request-0001"},
    )
    assert replacement_request.status_code == 201, replacement_request.text
    as_principal(app, human("recipient"))
    replacement_accept = await client.post(
        f"/v1/connection-requests/{replacement_request.json()['id']}/accept",
        json={"messaging_consent": True},
        headers={"Idempotency-Key": "reconnect-replacement-accept-0001"},
    )
    assert replacement_accept.status_code == 200, replacement_accept.text
    replacement_connection_id = (await client.get("/v1/connections")).json()["connections"][0]["id"]
    assert replacement_connection_id != first_connection_id
    as_principal(app, human("sender"))
    replacement_conversation = await client.post(
        "/v1/conversations",
        json={"connection_id": replacement_connection_id},
        headers={"Idempotency-Key": "reconnect-replacement-conversation-0001"},
    )
    assert replacement_conversation.status_code == 201, replacement_conversation.text
    assert replacement_conversation.json()["id"] != first_conversation.json()["id"]
    async with app.state.session_factory() as session:
        expired_connection = await session.get(Connection, first_connection_id)
        expired_conversation = await session.get(Conversation, first_conversation.json()["id"])
        assert expired_connection is not None and expired_connection.status == "removed"
        assert expired_conversation is not None and expired_conversation.status == "closed"


async def test_block_races_cannot_commit_post_block_messages_or_conversations(api_client) -> None:
    app, client = api_client

    async def establish(
        sender: str, recipient: str, *, conversation: bool
    ) -> tuple[str, str | None]:
        as_principal(app, human(recipient))
        assert (
            await client.post(
                "/v1/profiles",
                json={
                    "markdown": profile_markdown(visibility="public").replace(
                        "ada-lovelace", f"{recipient}-profile"
                    )
                },
                headers={"Idempotency-Key": f"block-race-{recipient}-profile-create"},
            )
        ).status_code == 201
        as_principal(app, human(sender))
        assert (
            await client.post(
                "/v1/profiles",
                json={
                    "markdown": profile_markdown(visibility="public").replace(
                        "ada-lovelace", f"{sender}-profile"
                    )
                },
                headers={"Idempotency-Key": f"block-race-{sender}-profile-create"},
            )
        ).status_code == 201
        requested = await client.post(
            "/v1/connection-requests",
            json={"recipient_profile_handle": f"{recipient}-profile", "messaging_requested": True},
            headers={"Idempotency-Key": f"{sender}-request-0001"},
        )
        assert requested.status_code == 201, requested.text
        as_principal(app, human(recipient))
        accepted = await client.post(
            f"/v1/connection-requests/{requested.json()['id']}/accept",
            json={"messaging_consent": True},
            headers={"Idempotency-Key": f"{sender}-accept-0001"},
        )
        assert accepted.status_code == 200, accepted.text
        connection_id = (await client.get("/v1/connections")).json()["connections"][0]["id"]
        if not conversation:
            return connection_id, None
        as_principal(app, human(sender))
        created = await client.post(
            "/v1/conversations",
            json={"connection_id": connection_id},
            headers={"Idempotency-Key": f"{sender}-conversation-0001"},
        )
        assert created.status_code == 201, created.text
        return connection_id, created.json()["id"]

    async def by_header(request: Request) -> Principal:
        return human(request.headers["X-Test-Owner"])

    message_connection_id, message_conversation_id = await establish(
        "message-sender", "message-recipient", conversation=True
    )
    assert message_conversation_id is not None
    app.dependency_overrides[require_principal] = by_header
    app.dependency_overrides[optional_principal] = by_header
    send, block = await asyncio.gather(
        client.post(
            f"/v1/conversations/{message_conversation_id}/messages",
            json={"markdown": "racing message"},
            headers={
                "X-Test-Owner": "message-sender",
                "Idempotency-Key": "racing-send-0001",
            },
        ),
        client.post(
            f"/v1/connections/{message_connection_id}/block",
            headers={
                "X-Test-Owner": "message-recipient",
                "Idempotency-Key": "racing-block-message-0001",
            },
        ),
    )
    assert block.status_code == 204, block.text
    assert send.status_code in {201, 404}, send.text
    async with app.state.session_factory() as session:
        connection = await session.get(Connection, message_connection_id)
        messages = (
            await session.scalars(
                select(Message).where(Message.conversation_id == message_conversation_id)
            )
        ).all()
        assert (
            connection is not None
            and connection.status == "blocked"
            and connection.ended_at is not None
        )
        if send.status_code == 201:
            assert len(messages) == 1 and messages[0].created_at <= connection.ended_at
        else:
            assert messages == []

    create_connection_id, _ = await establish(
        "create-sender", "create-recipient", conversation=False
    )
    app.dependency_overrides[require_principal] = by_header
    app.dependency_overrides[optional_principal] = by_header
    create, block = await asyncio.gather(
        client.post(
            "/v1/conversations",
            json={"connection_id": create_connection_id},
            headers={
                "X-Test-Owner": "create-sender",
                "Idempotency-Key": "racing-create-0001",
            },
        ),
        client.post(
            f"/v1/connections/{create_connection_id}/block",
            headers={
                "X-Test-Owner": "create-recipient",
                "Idempotency-Key": "racing-block-create-0001",
            },
        ),
    )
    assert block.status_code == 204, block.text
    assert create.status_code in {201, 404}, create.text
    async with app.state.session_factory() as session:
        connection = await session.get(Connection, create_connection_id)
        conversations = (
            await session.scalars(
                select(Conversation).where(Conversation.connection_id == create_connection_id)
            )
        ).all()
        assert connection is not None and connection.status == "blocked"
        assert all(row.status != "active" for row in conversations)


async def test_organization_website_requires_public_https(api_client) -> None:
    app, client = api_client
    as_principal(app, human("organization_owner"))
    for index, website_url in enumerate(
        ("javascript:alert(1)", "file:///etc/passwd", "https://localhost/internal")
    ):
        rejected = await client.post(
            "/v1/organizations",
            json={"slug": f"unsafe-{index}", "name": "Unsafe", "website_url": website_url},
            headers={"Idempotency-Key": f"unsafe-url-{index}-0001"},
        )
        assert rejected.status_code == 422
    accepted = await client.post(
        "/v1/organizations",
        json={
            "slug": "safe-site",
            "name": "Safe Site",
            "website_url": "https://example.com/careers",
        },
        headers={"Idempotency-Key": "safe-url-0001"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["website_url"] == "https://example.com/careers"


async def test_private_gets_and_social_operations_are_marked_human_only_in_openapi(
    api_client,
) -> None:
    app, client = api_client
    as_principal(app, human("owner"))
    schema = app.openapi()
    assert schema["paths"]["/v1/applications"]["get"]["security"]
    social_paths = {
        "/v1/connection-requests": {"post"},
        "/v1/connection-requests/inbox": {"get"},
        "/v1/connection-requests/{connection_request_id}/{action}": {"post"},
        "/v1/connections": {"get"},
        "/v1/connections/{connection_id}": {"delete"},
        "/v1/connections/{connection_id}/block": {"post"},
        "/v1/conversations": {"get", "post"},
        "/v1/conversations/{conversation_id}/messages": {"get", "post"},
        "/v1/notifications": {"get"},
        "/v1/notifications/{notification_id}/read": {"post"},
    }
    for path, methods in social_paths.items():
        for method in methods:
            operation = schema["paths"][path][method]
            assert operation["security"] == [{"ClerkBearerAuth": []}]
            assert operation["x-connectmd-human-only"] is True
            assert "401" in operation["responses"]
