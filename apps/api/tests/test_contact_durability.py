from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.main as main_module
from app.auth import Principal, lifecycle_hmac, optional_principal, require_principal
from app.config import Settings
from app.models import (
    AccountAccessDeny,
    AccountLifecycle,
    AgentGrant,
    ApiKey,
    Base,
    ChangeEvent,
    ContactBlock,
    ContactPolicy,
    ContactRequest,
    IdempotencyRecord,
)
from app.services.deletion_journal import DeletionCommitmentJournal

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def create_public_profile(app, client, subject: str, handle: str) -> None:
    as_principal(app, human(subject))
    response = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", handle)},
        headers={"Idempotency-Key": f"contact-durability-profile-{handle}"},
    )
    assert response.status_code == 201, response.text


async def create_pending_contact(
    app,
    client,
    *,
    sender: str,
    recipient_handle: str,
    key: str,
    purpose: str,
    message: str,
) -> str:
    as_principal(app, human(sender))
    response = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": recipient_handle,
            "purpose": purpose,
            "message": message,
        },
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_profile_contact_replay_after_retention_expiry_fails_closed(api_client) -> None:
    app, client = api_client
    sender = "contact-expired-replay-sender"
    recipient = "contact-expired-replay-recipient"
    handle = "contact-expired-replay-recipient"
    await create_public_profile(app, client, recipient, handle)
    as_principal(app, human(recipient))
    policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 20},
        headers={
            "Idempotency-Key": "contact-expired-replay-policy-0001",
            "If-Match": '"policy-0"',
        },
    )
    assert policy.status_code == 200, policy.text
    request_id = await create_pending_contact(
        app,
        client,
        sender=sender,
        recipient_handle=handle,
        key="contact-expired-replay-create-0001",
        purpose="Expired private purpose",
        message="Expired private message",
    )

    async with app.state.session_factory() as session:
        contact = await session.get(ContactRequest, request_id)
        assert contact is not None
        contact.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    as_principal(app, human(sender))
    replay = await client.post(
        "/v1/contact-requests",
        json={
            "target_profile_handle": handle,
            "purpose": "Expired private purpose",
            "message": "Expired private message",
        },
        headers={"Idempotency-Key": "contact-expired-replay-create-0001"},
    )
    assert replay.status_code == 503
    assert "Expired private purpose" not in replay.text
    assert "Expired private message" not in replay.text

    async with app.state.session_factory() as session:
        stored_contact = await session.get(ContactRequest, request_id)
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == sender,
                    IdempotencyRecord.idempotency_key == "contact-expired-replay-create-0001",
                )
            )
        ).all()
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_type == "contact_request",
                    ChangeEvent.resource_id == request_id,
                )
            )
        ).all()
    assert stored_contact is not None
    assert len(receipts) == 1
    assert len(events) == 2


async def test_contact_policy_receipt_replay_concurrency_and_integrity(api_client) -> None:
    app, client = api_client
    owner = "contact-policy-durability-owner"
    await create_public_profile(app, client, owner, "contact-policy-owner")
    as_principal(app, human(owner))
    missing_both = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 10},
    )
    missing_if_match = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 10},
        headers={"Idempotency-Key": "contact-policy-missing-precondition-0001"},
    )
    malformed = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 10},
        headers={"Idempotency-Key": "\x7f"},
    )
    assert missing_both.status_code == 428
    assert missing_if_match.status_code == 428
    assert missing_if_match.json()["detail"] == "If-Match is required to update contact policy"
    assert malformed.status_code == 400

    schema = app.openapi()
    operation = schema["paths"]["/v1/contact-policy"]["put"]
    parameter = next(item for item in operation["parameters"] if item["name"] == "Idempotency-Key")
    assert parameter["required"] is True
    assert parameter["schema"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": main_module._IDEMPOTENCY_KEY_PATTERN,
    }
    if_match_parameter = next(
        item for item in operation["parameters"] if item["name"] == "If-Match"
    )
    assert if_match_parameter["required"] is True
    assert if_match_parameter["schema"] == {
        "type": "string",
        "pattern": r'^"policy-(0|[1-9][0-9]*)"$',
    }
    assert operation["responses"]["200"]["headers"]["ETag"]["schema"] == {"type": "string"}

    initial = await client.get("/v1/contact-policy")
    assert initial.status_code == 200
    assert initial.headers["etag"] == '"policy-0"'
    assert initial.json()["etag"] == '"policy-0"'
    for index, supplied in enumerate(("*", 'W/"policy-0"', '"policy-0", "policy-1"'), start=1):
        rejected = await client.put(
            "/v1/contact-policy",
            json={"allow_agent_requests": True, "daily_request_limit": 10},
            headers={
                "Idempotency-Key": f"contact-policy-invalid-precondition-{index:04d}",
                "If-Match": supplied,
            },
        )
        assert rejected.status_code == 412

    policy_body = {"allow_agent_requests": True, "daily_request_limit": 10}
    first, second = await asyncio.gather(
        client.put(
            "/v1/contact-policy",
            json=policy_body,
            headers={
                "Idempotency-Key": "contact-policy-durable-0001",
                "If-Match": initial.headers["etag"],
            },
        ),
        client.put(
            "/v1/contact-policy",
            json=policy_body,
            headers={
                "Idempotency-Key": "contact-policy-durable-0001",
                "If-Match": initial.headers["etag"],
            },
        ),
    )
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert sorted(
        response.headers.get("idempotency-replayed", "false") for response in (first, second)
    ) == ["false", "true"]
    assert first.headers["etag"] == first.json()["etag"]

    async with app.state.session_factory() as session:
        policy = await session.get(ContactPolicy, owner)
        assert policy is not None and policy.version == 1
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.owner_id == owner,
                    ChangeEvent.event_type == "contact_policy.updated",
                )
            )
        ).all()
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == owner,
                IdempotencyRecord.idempotency_key == "contact-policy-durable-0001",
            )
        )
        assert len(events) == 1
        assert receipt is not None
        assert receipt.resource_type == "contact_policy"
        assert receipt.response_body == first.text
        assert receipt.resource_id is not None
        owner_part, digest_part = receipt.resource_id.split(":")
        assert owner_part == main_module.public_owner_id(owner)
        assert len(digest_part) == 64 and digest_part == digest_part.lower()

    stale_distinct_key = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": False, "daily_request_limit": 5},
        headers={
            "Idempotency-Key": "contact-policy-stale-distinct-0001",
            "If-Match": initial.headers["etag"],
        },
    )
    assert stale_distinct_key.status_code == 412

    later = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": False, "daily_request_limit": 5},
        headers={
            "Idempotency-Key": "contact-policy-durable-later-0001",
            "If-Match": first.headers["etag"],
        },
    )
    assert later.status_code == 200
    assert later.headers["etag"] != first.headers["etag"]
    old_replay = await client.put(
        "/v1/contact-policy",
        json=policy_body,
        headers={
            "Idempotency-Key": "contact-policy-durable-0001",
            "If-Match": initial.headers["etag"],
        },
    )
    assert old_replay.status_code == 200
    assert old_replay.content == first.content
    assert old_replay.headers["etag"] == first.headers["etag"]
    assert old_replay.headers["idempotency-replayed"] == "true"

    body_collision = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 9},
        headers={
            "Idempotency-Key": "contact-policy-durable-0001",
            "If-Match": initial.headers["etag"],
        },
    )
    if_match_collision = await client.put(
        "/v1/contact-policy",
        json=policy_body,
        headers={
            "Idempotency-Key": "contact-policy-durable-0001",
            "If-Match": first.headers["etag"],
        },
    )
    assert body_collision.status_code == if_match_collision.status_code == 409

    other_owner = "contact-policy-cross-owner"
    await create_public_profile(app, client, other_owner, "contact-policy-cross-owner")
    as_principal(app, human(other_owner))
    other_initial = await client.get("/v1/contact-policy")
    assert other_initial.status_code == 200
    cross_owner = await client.put(
        "/v1/contact-policy",
        json=policy_body,
        headers={
            "Idempotency-Key": "contact-policy-durable-0001",
            "If-Match": other_initial.headers["etag"],
        },
    )
    assert cross_owner.status_code == 200
    assert cross_owner.headers.get("idempotency-replayed") is None
    async with app.state.session_factory() as session:
        cross_owner_receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == other_owner,
                IdempotencyRecord.idempotency_key == "contact-policy-durable-0001",
            )
        )
    assert cross_owner_receipt is not None
    as_principal(app, human(owner))

    async def corrupt_policy_receipt(field: str, value: object) -> None:
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "contact-policy-durable-0001",
                )
            )
            assert receipt is not None
            original = getattr(receipt, field)
            setattr(receipt, field, value)
            await session.commit()
        replay = await client.put(
            "/v1/contact-policy",
            json=policy_body,
            headers={
                "Idempotency-Key": "contact-policy-durable-0001",
                "If-Match": initial.headers["etag"],
            },
        )
        assert replay.status_code == 503
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "contact-policy-durable-0001",
                )
            )
            assert receipt is not None
            setattr(receipt, field, original)
            await session.commit()

    await corrupt_policy_receipt("response_status", 201)
    await corrupt_policy_receipt("resource_type", "other")
    await corrupt_policy_receipt("response_body", "{")
    await corrupt_policy_receipt("response_headers", "[")
    await corrupt_policy_receipt("resource_id", f"{main_module.public_owner_id(owner)}:{'0' * 64}")
    await corrupt_policy_receipt("resource_id", "wrong-owner:bad-digest")


@pytest.mark.parametrize("credential_kind", ["clerk_jwt", "agent_api_key", "agent_grant"])
async def test_contact_policy_sqlite_lock_rechecks_lifecycle_and_preserves_usage(
    tmp_path, monkeypatch, credential_kind: str
) -> None:
    owner = f"contact-policy-lock-{credential_kind}-owner"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'contact-policy-lock.db'}",
        storage_path=tmp_path / "storage",
        api_key_pepper="test-only-pepper-is-long-enough",
        account_lifecycle_enabled=True,
        lifecycle_hmac_key="h" * 32,
        lifecycle_aead_key="a" * 32,
        deletion_journal_path=tmp_path / "deletion-journal",
        deletion_witness_path=tmp_path / "deletion-witness",
        deletion_witness_hmac_key="w" * 32,
        clerk_backend_secret="b" * 32,
        clerk_backend_base_url="https://clerk.example.test",
    )
    DeletionCommitmentJournal(settings).initialize()
    app = main_module.create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    credential_id: str | None = None
    raw_credential = "clerk-contact-policy-lock-token"
    async with app.state.session_factory() as session:
        if credential_kind == "agent_api_key":
            api_key, raw_credential = await app.state.api_keys.create(
                session, owner, ["contacts:write"]
            )
            credential_id = api_key.id
        elif credential_kind == "agent_grant":
            grant, raw_credential = await app.state.agent_grants.create(
                session,
                owner_id=owner,
                actor_id=owner,
                name="contact policy lifecycle lock",
                scopes=["contacts:write"],
                mode="direct",
                resource_type="owner",
                resource_id=None,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            credential_id = grant.id
        lifecycle = AccountLifecycle(
            subject_hmac=lifecycle_hmac(settings, "subject", owner),
            request_idempotency_hmac=lifecycle_hmac(
                settings, "delete-request-key", f"contact-policy-lock-{credential_kind}"
            ),
            state="confirmation_pending",
            provider_state="pending",
            backup_state="expiry_pending",
            policy_version="account-lifecycle-v1",
            requested_at=datetime.now(UTC),
        )
        session.add(lifecycle)
        await session.commit()
        deletion_id = lifecycle.id

    if credential_kind == "clerk_jwt":

        async def verify_clerk(token: str) -> Principal:
            assert token == raw_credential
            return human(owner)

        monkeypatch.setattr(app.state.clerk, "verify", verify_clerk)

    original_commit = AsyncSession.commit
    denial_injected = False

    async def commit_then_deny(session: AsyncSession) -> None:
        nonlocal denial_injected
        await original_commit(session)
        if denial_injected:
            return
        denial_injected = True
        async with app.state.session_factory() as denial_session:
            denial_session.add(
                AccountAccessDeny(
                    subject_hmac=lifecycle_hmac(settings, "subject", owner),
                    deletion_id=deletion_id,
                    denied_at=datetime.now(UTC),
                )
            )
            await original_commit(denial_session)

    monkeypatch.setattr(AsyncSession, "commit", commit_then_deny)
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.put(
                "/v1/contact-policy",
                json={"allow_agent_requests": True, "daily_request_limit": 10},
                headers={
                    "Authorization": f"Bearer {raw_credential}",
                    "Idempotency-Key": f"contact-policy-lock-{credential_kind}-0001",
                    "If-Match": '"policy-0"',
                },
            )
        assert denial_injected is True
        assert response.status_code == 403
        assert response.json()["detail"] == "account_access_denied"
        async with app.state.session_factory() as session:
            if credential_kind == "agent_api_key":
                assert credential_id is not None
                stored_credential = await session.get(ApiKey, credential_id)
                assert stored_credential is not None
                assert stored_credential.last_used_at is not None
            elif credential_kind == "agent_grant":
                assert credential_id is not None
                stored_credential = await session.get(AgentGrant, credential_id)
                assert stored_credential is not None
                assert stored_credential.last_used_at is not None
            policy = await session.get(ContactPolicy, owner)
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key
                    == f"contact-policy-lock-{credential_kind}-0001",
                )
            )
            events = (
                await session.scalars(
                    select(ChangeEvent).where(
                        ChangeEvent.owner_id == owner,
                        ChangeEvent.event_type == "contact_policy.updated",
                    )
                )
            ).all()
        assert policy is None
        assert receipt is None
        assert events == []
    finally:
        await app.state.engine.dispose()


async def test_nonhuman_agent_outreach_decisions_are_opaque_404(api_client) -> None:
    app, client = api_client
    recipient = "contact-outreach-decision-recipient"
    request_id = "contact-outreach-decision-row"
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        session.add(
            ContactRequest(
                id=request_id,
                sender_owner_id="contact-outreach-decision-sender",
                recipient_owner_id=recipient,
                sender_actor_id="contact-outreach-agent",
                sender_actor_method="agent_grant",
                sender_grant_id="contact-outreach-grant",
                sender_mandate_id=None,
                origin="agent_outreach",
                sender_identity_handle="source-agent",
                sender_identity_display_name="Source Agent",
                target_identity_handle="target-agent",
                target_identity_display_name="Target Agent",
                target_document_id="target-document",
                purpose="outreach purpose",
                message="outreach message",
                status="pending",
                decision_actor_id=None,
                report_reason=None,
                created_at=now,
                decided_at=None,
                retention_expires_at=now + timedelta(days=1),
            )
        )
        await session.commit()

    as_principal(app, human(recipient))
    missing_contact = await client.post(
        "/v1/contact-requests/does-not-exist/accept",
        headers={"Idempotency-Key": "contact-outreach-decision-missing-0001"},
    )
    assert missing_contact.status_code == 404
    assert missing_contact.json()["detail"] == "contact request was not found"

    principals = (
        Principal(
            subject=recipient,
            method="agent_grant",
            scopes=frozenset({"contacts:write"}),
            grant_mode="direct",
            resource_type="owner",
        ),
        Principal(
            subject=recipient,
            method="agent_api_key",
            scopes=frozenset({"contacts:write"}),
        ),
    )
    for index, principal in enumerate(principals):
        as_principal(app, principal)
        response = await client.post(
            f"/v1/contact-requests/{request_id}/accept",
            headers={"Idempotency-Key": f"contact-outreach-decision-{index}-0001"},
        )
        nonexistent = await client.post(
            "/v1/contact-requests/does-not-exist/accept",
            headers={"Idempotency-Key": f"contact-outreach-decision-{index}-0002"},
        )
        assert response.status_code == nonexistent.status_code == 404
        assert nonexistent.json()["detail"] == "contact request was not found"

    async with app.state.session_factory() as session:
        row = await session.get(ContactRequest, request_id)
        assert row is not None and row.status == "pending"
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == recipient,
                    IdempotencyRecord.operation.like("POST:/v1/contact-requests/%"),
                )
            )
        ).all()
        assert receipts == []


async def test_contact_decision_receipts_authority_integrity_and_redaction(api_client) -> None:
    app, client = api_client
    recipient = "contact-decision-recipient"
    sender = "contact-decision-sender"
    await create_public_profile(app, client, recipient, "contact-decision-recipient-profile")
    as_principal(app, human(recipient))
    policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 20},
        headers={
            "Idempotency-Key": "contact-decision-policy-0001",
            "If-Match": '"policy-0"',
        },
    )
    assert policy.status_code == 200
    request_id = await create_pending_contact(
        app,
        client,
        sender=sender,
        recipient_handle="contact-decision-recipient-profile",
        key="contact-decision-create-0001",
        purpose="Decision purpose",
        message="Private decision message",
    )
    as_principal(app, human(recipient))
    path = f"/v1/contact-requests/{request_id}/accept"
    missing = await client.post(path)
    malformed = await client.post(path, headers={"Idempotency-Key": "\x7f"})
    assert missing.status_code == 428
    assert malformed.status_code == 400

    schema = app.openapi()
    operation = schema["paths"]["/v1/contact-requests/{contact_request_id}/{action}"]["post"]
    parameter = next(item for item in operation["parameters"] if item["name"] == "Idempotency-Key")
    assert parameter["required"] is True
    assert parameter["schema"]["pattern"] == main_module._IDEMPOTENCY_KEY_PATTERN

    decided = await client.post(path, headers={"Idempotency-Key": "contact-decision-0001"})
    replay = await client.post(path, headers={"Idempotency-Key": "contact-decision-0001"})
    assert decided.status_code == replay.status_code == 200
    assert decided.content == replay.content
    assert replay.headers["idempotency-replayed"] == "true"
    collision = await client.post(
        f"/v1/contact-requests/{request_id}/reject",
        headers={"Idempotency-Key": "contact-decision-0001"},
    )
    assert collision.status_code == 409

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == recipient,
                IdempotencyRecord.idempotency_key == "contact-decision-0001",
            )
        )
        row = await session.get(ContactRequest, request_id)
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == request_id,
                    ChangeEvent.event_type == "contact_request.accepted",
                )
            )
        ).all()
        assert receipt is not None and receipt.resource_type == "contact_request_decision"
        assert receipt.response_body == ""
        assert row is not None and row.status == "accepted"
        assert len(events) == 2
        assert "Private decision message" not in receipt.response_body
        assert all("Private decision message" not in event.payload for event in events)

    async def corrupt_decision_receipt(field: str, value: object) -> None:
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == recipient,
                    IdempotencyRecord.idempotency_key == "contact-decision-0001",
                )
            )
            assert receipt is not None
            original = getattr(receipt, field)
            setattr(receipt, field, value)
            await session.commit()
        response = await client.post(path, headers={"Idempotency-Key": "contact-decision-0001"})
        assert response.status_code == 503
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == recipient,
                    IdempotencyRecord.idempotency_key == "contact-decision-0001",
                )
            )
            assert receipt is not None
            setattr(receipt, field, original)
            await session.commit()

    await corrupt_decision_receipt("resource_type", "other")
    await corrupt_decision_receipt("response_status", 201)
    await corrupt_decision_receipt("response_body", "private")
    await corrupt_decision_receipt("response_headers", '{"ETag":"bad"}')
    await corrupt_decision_receipt("resource_id", "wrong")
    await corrupt_decision_receipt("resource_id", f"{request_id}:accept:profile_contact:{'0' * 64}")

    async with app.state.session_factory() as session:
        row = await session.get(ContactRequest, request_id)
        assert row is not None
        original_message = row.message
        row.message = "redacted private message"
        await session.commit()
    redacted = await client.post(path, headers={"Idempotency-Key": "contact-decision-0001"})
    assert redacted.status_code == 503
    async with app.state.session_factory() as session:
        row = await session.get(ContactRequest, request_id)
        assert row is not None
        row.message = original_message
        row.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    expired = await client.post(path, headers={"Idempotency-Key": "contact-decision-0001"})
    assert expired.status_code == 503
    async with app.state.session_factory() as session:
        row = await session.get(ContactRequest, request_id)
        assert row is not None
        row.retention_expires_at = datetime.now(UTC) + timedelta(days=365)
        await session.delete(row)
        await session.commit()
    deleted = await client.post(path, headers={"Idempotency-Key": "contact-decision-0001"})
    assert deleted.status_code == 503

    report_id = await create_pending_contact(
        app,
        client,
        sender=sender,
        recipient_handle="contact-decision-recipient-profile",
        key="contact-report-create-0001",
        purpose="Report purpose",
        message="Report message",
    )
    as_principal(app, human(recipient))
    report_path = f"/v1/contact-requests/{report_id}/report"
    report_body = {"reason": "private report reason"}
    report = await client.post(
        report_path,
        json=report_body,
        headers={"Idempotency-Key": "contact-report-0001"},
    )
    report_replay = await client.post(
        report_path,
        json=report_body,
        headers={"Idempotency-Key": "contact-report-0001"},
    )
    assert report.status_code == report_replay.status_code == 200, report.text
    assert report.content == report_replay.content
    reason_collision = await client.post(
        report_path,
        json={"reason": "a different valid reason"},
        headers={"Idempotency-Key": "contact-report-0001"},
    )
    assert reason_collision.status_code == 409
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == recipient,
                IdempotencyRecord.idempotency_key == "contact-report-0001",
            )
        )
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == report_id,
                    ChangeEvent.event_type == "contact_request.reported",
                )
            )
        ).all()
        row = await session.get(ContactRequest, report_id)
        assert receipt is not None and receipt.response_body == ""
        assert receipt.response_headers == "{}"
        assert receipt.resource_type == "contact_request_decision"
        assert receipt.resource_id is not None
        receipt_fields = (
            receipt.response_body,
            receipt.response_headers,
            receipt.resource_type,
            receipt.resource_id,
        )
        assert all("private report reason" not in field for field in receipt_fields)
        assert len(events) == 2
        assert all("private report reason" not in event.payload for event in events)
        assert row is not None
        row.report_reason = "changed report reason"
        await session.commit()
    changed_reason = await client.post(
        report_path,
        json=report_body,
        headers={"Idempotency-Key": "contact-report-0001"},
    )
    assert changed_reason.status_code == 503

    reject_id = await create_pending_contact(
        app,
        client,
        sender="contact-decision-reject-sender",
        recipient_handle="contact-decision-recipient-profile",
        key="contact-reject-create-0001",
        purpose="Reject purpose",
        message="Reject message",
    )
    as_principal(app, human(recipient))
    reject_path = f"/v1/contact-requests/{reject_id}/reject"
    rejected = await client.post(reject_path, headers={"Idempotency-Key": "contact-reject-0001"})
    reject_replay = await client.post(
        reject_path, headers={"Idempotency-Key": "contact-reject-0001"}
    )
    assert rejected.status_code == reject_replay.status_code == 200
    assert rejected.content == reject_replay.content
    assert reject_replay.headers["idempotency-replayed"] == "true"

    as_principal(
        app,
        Principal(
            subject=recipient,
            method="agent_grant",
            scopes=frozenset({"contacts:write"}),
            grant_mode="direct",
            resource_type="owner",
        ),
    )
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == recipient,
                IdempotencyRecord.idempotency_key == "contact-decision-0001",
            )
        )
        assert receipt is not None and receipt.resource_id is not None
        parts = receipt.resource_id.split(":")
        assert len(parts) == 4
        original_resource_id = receipt.resource_id
        receipt.resource_id = ":".join([parts[0], parts[1], "agent_outreach", parts[3]])
        await session.commit()
    outreach_replay = await client.post(
        path,
        headers={"Idempotency-Key": "contact-decision-0001"},
    )
    assert outreach_replay.status_code == 403
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == recipient,
                IdempotencyRecord.idempotency_key == "contact-decision-0001",
            )
        )
        assert receipt is not None
        receipt.resource_id = original_resource_id
        await session.commit()


async def test_contact_decision_same_key_replay_and_transition_conflict_are_durable(
    api_client,
) -> None:
    app, client = api_client
    recipient = "contact-race-recipient"
    sender = "contact-race-sender"
    await create_public_profile(app, client, recipient, "contact-race-recipient-profile")
    as_principal(app, human(recipient))
    policy = await client.put(
        "/v1/contact-policy",
        json={"allow_agent_requests": True, "daily_request_limit": 20},
        headers={
            "Idempotency-Key": "contact-race-policy-0001",
            "If-Match": '"policy-0"',
        },
    )
    assert policy.status_code == 200

    block_id = await create_pending_contact(
        app,
        client,
        sender=sender,
        recipient_handle="contact-race-recipient-profile",
        key="contact-race-block-create-0001",
        purpose="Block race",
        message="Block race message",
    )
    as_principal(app, human(recipient))
    block_path = f"/v1/contact-requests/{block_id}/block"
    # SQLite cannot prove PostgreSQL FOR UPDATE behavior; this gather covers the
    # same-key receipt race, while the different-key conflict below is sequential.
    block_responses = await asyncio.gather(
        client.post(block_path, headers={"Idempotency-Key": "contact-race-block-0001"}),
        client.post(block_path, headers={"Idempotency-Key": "contact-race-block-0001"}),
    )
    assert [response.status_code for response in block_responses] == [200, 200]
    assert sorted(
        response.headers.get("idempotency-replayed", "false") for response in block_responses
    ) == ["false", "true"]
    assert block_responses[0].content == block_responses[1].content
    async with app.state.session_factory() as session:
        blocks = (
            await session.scalars(
                select(ContactBlock).where(
                    ContactBlock.blocker_owner_id == recipient,
                    ContactBlock.blocked_owner_id == sender,
                )
            )
        ).all()
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == block_id,
                    ChangeEvent.event_type == "contact_request.blocked",
                )
            )
        ).all()
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == recipient,
                IdempotencyRecord.idempotency_key == "contact-race-block-0001",
            )
        )
        assert len(blocks) == 1
        assert len(events) == 2
        assert receipt is not None

    different_key_id = await create_pending_contact(
        app,
        client,
        sender="contact-race-different-sender",
        recipient_handle="contact-race-recipient-profile",
        key="contact-race-different-create-0001",
        purpose="Different key race",
        message="Different key race message",
    )
    as_principal(app, human(recipient))
    accept_path = f"/v1/contact-requests/{different_key_id}/accept"
    reject_path = f"/v1/contact-requests/{different_key_id}/reject"
    # PostgreSQL serialization is source-designed but not proven by this SQLite test.
    first = await client.post(accept_path, headers={"Idempotency-Key": "contact-race-accept-0001"})
    second = await client.post(reject_path, headers={"Idempotency-Key": "contact-race-reject-0001"})
    assert sorted((first.status_code, second.status_code)) == [200, 409]
    async with app.state.session_factory() as session:
        row = await session.get(ContactRequest, different_key_id)
        assert row is not None and row.status in {"accepted", "rejected"}
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == recipient,
                    IdempotencyRecord.operation.in_(
                        {
                            accept_path.replace("/v1", "POST:/v1"),
                            reject_path.replace("/v1", "POST:/v1"),
                        }
                    ),
                )
            )
        ).all()
        assert len(receipts) == 1
