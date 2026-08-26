from __future__ import annotations

import asyncio
from argparse import Namespace
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli
from app.auth import Principal, optional_principal, require_principal
from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountBackupAuthority,
    AccountBackupManifest,
    AgentOutreachDirectPeerRateBucket,
    Application,
    ChangeEvent,
    Connection,
    ConnectionRequest,
    ContactRequest,
    Conversation,
    IdempotencyRecord,
    Job,
    LifecycleTask,
    Message,
    ModerationAppeal,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    Notification,
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
    Post,
    PostReport,
    RetentionHold,
    RetentionTombstone,
)
from app.services.retention import RetentionExecutor, RetentionFailure, RetentionRunResult
from app.services.storage import StorageIntegrityError


@pytest_asyncio.fixture(autouse=True)
async def retention_hold_guard(api_client) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        session.add(
            AccountBackupManifest(
                generation_id="retention-test-generation",
                created_at=now,
                expires_at=now + timedelta(days=30),
                state="active",
                db_manifest_digest="a" * 64,
                markdown_manifest_digest="b" * 64,
            )
        )
        session.add(
            AccountBackupAuthority(
                id=ACCOUNT_BACKUP_AUTHORITY_ID,
                current_generation_id="retention-test-generation",
                registered_at=now,
                updated_at=now,
            )
        )
        await session.commit()


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def _insert_expired_records(app, now: datetime) -> dict[str, str]:
    expires_at = now - timedelta(seconds=1)
    ids = {
        "application": "10000000-0000-4000-8000-000000000001",
        "contact_request": "10000000-0000-4000-8000-000000000002",
        "connection_request": "10000000-0000-4000-8000-000000000003",
        "conversation": "10000000-0000-4000-8000-000000000004",
        "message": "10000000-0000-4000-8000-000000000005",
        "notification": "10000000-0000-4000-8000-000000000006",
        "organization_verification_evidence": "10000000-0000-4000-8000-000000000007",
    }
    verification_id = "10000000-0000-4000-8000-000000000008"
    organization_id = "10000000-0000-4000-8000-000000000009"
    artifact_payload = b"retained-private-artifact"
    artifact_sha256 = sha256(artifact_payload).hexdigest()
    artifact_path = (
        f"verification-evidence/{organization_id}/{verification_id}/{artifact_sha256}.bin"
    )
    app.state.store.write_immutable_bytes(artifact_path, artifact_payload)
    async with app.state.session_factory() as session:
        session.add_all(
            (
                Application(
                    id=ids["application"],
                    job_id="job-expired",
                    applicant_owner_id="applicant",
                    applicant_actor_id="applicant",
                    applicant_actor_method="clerk_jwt",
                    applicant_grant_id=None,
                    snapshot_document_id="document-expired",
                    snapshot_document_kind="profile",
                    snapshot_document_identifier="expired-profile",
                    snapshot_document_version=1,
                    snapshot_sha256="b" * 64,
                    message="private application body",
                    status="submitted",
                    confirmed_by_owner_id="applicant",
                    confirmed_at=expires_at,
                    retention_policy_version="application-retention-v1",
                    retention_expires_at=expires_at,
                    created_at=expires_at,
                    updated_at=expires_at,
                ),
                ContactRequest(
                    id=ids["contact_request"],
                    sender_owner_id="sender",
                    recipient_owner_id="recipient",
                    sender_actor_id="sender",
                    sender_actor_method="clerk_jwt",
                    sender_grant_id=None,
                    target_document_id="document-expired",
                    purpose="Private purpose",
                    message="private contact request body",
                    status="rejected",
                    decision_actor_id="recipient",
                    report_reason=None,
                    created_at=expires_at,
                    decided_at=expires_at,
                    retention_expires_at=expires_at,
                ),
                ConnectionRequest(
                    id=ids["connection_request"],
                    pair_owner_low="a",
                    pair_owner_high="b",
                    requester_owner_id="a",
                    recipient_owner_id="b",
                    requester_profile_handle="a-profile",
                    recipient_profile_handle="b-profile",
                    requested_messaging=False,
                    recipient_messaging_consent=None,
                    status="rejected",
                    requester_actor_id="a",
                    requester_actor_method="clerk_jwt",
                    decision_actor_id="b",
                    created_at=expires_at,
                    updated_at=expires_at,
                    decided_at=expires_at,
                    retention_expires_at=expires_at,
                ),
                Conversation(
                    id=ids["conversation"],
                    connection_id="connection-expired",
                    pair_owner_low="a",
                    pair_owner_high="b",
                    status="closed",
                    created_by_owner_id="a",
                    created_at=expires_at,
                    closed_at=expires_at,
                    retention_expires_at=expires_at,
                ),
                Message(
                    id=ids["message"],
                    conversation_id="conversation-message-expired",
                    sender_owner_id="a",
                    sender_actor_id="a",
                    sender_actor_method="clerk_jwt",
                    markdown="private message body",
                    content_sha256="c" * 64,
                    status="active",
                    created_at=expires_at,
                    retention_expires_at=expires_at,
                ),
                Notification(
                    id=ids["notification"],
                    recipient_owner_id="recipient",
                    type="private_notification",
                    actor_owner_id="sender",
                    resource_type="message",
                    resource_id=ids["message"],
                    created_at=expires_at,
                    read_at=None,
                    retention_expires_at=expires_at,
                ),
                Organization(
                    id=organization_id,
                    owner_id="organization-owner",
                    slug="retention-org",
                    name="Retention Organization",
                    description=None,
                    website_url=None,
                    visibility="private",
                    verification_status="unverified",
                    verification_material_version=1,
                    version=1,
                    created_at=expires_at,
                    updated_at=expires_at,
                ),
                OrganizationVerification(
                    id=verification_id,
                    organization_id=organization_id,
                    purpose="recruiting_control",
                    submitted_by_owner_id="organization-owner",
                    material_claim_digest="d" * 64,
                    created_at=expires_at,
                ),
                OrganizationVerificationEvidence(
                    id=ids["organization_verification_evidence"],
                    verification_id=verification_id,
                    evidence_kind="other",
                    metadata_json='{"private":"metadata"}',
                    artifact_content_type="text/plain",
                    artifact_sha256=artifact_sha256,
                    artifact_size_bytes=len(artifact_payload),
                    storage_path=artifact_path,
                    created_at=expires_at,
                    retention_expires_at=expires_at,
                ),
                OrganizationVerificationEvent(
                    id="10000000-0000-4000-8000-000000000010",
                    verification_id=verification_id,
                    organization_id=organization_id,
                    purpose="recruiting_control",
                    to_state="rejected",
                    actor_id="reviewer",
                    actor_role="recruiting_verifier",
                    policy_version=None,
                    material_claim_digest="d" * 64,
                    expires_at=None,
                    occurred_at=expires_at,
                ),
            )
        )
        for resource_type, resource_id in ids.items():
            session.add(
                IdempotencyRecord(
                    id=f"20000000-0000-4000-8000-{int(resource_id[-3:]):012d}",
                    owner_id="owner",
                    idempotency_key=f"retention-{resource_type}",
                    operation="retention-test",
                    request_hash="e" * 64,
                    response_status=201,
                    response_body="private replay residue",
                    response_headers="{}",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    created_at=expires_at,
                )
            )
            session.add(
                ChangeEvent(
                    owner_id="owner",
                    event_type="retention.test",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    actor_id="owner",
                    actor_method="clerk_jwt",
                    payload="private event residue",
                    occurred_at=expires_at,
                )
            )
        await session.commit()
    ids["artifact_path"] = artifact_path
    return ids


async def test_retention_prunes_only_prior_utc_direct_peer_buckets_once(api_client) -> None:
    app, _ = api_client
    now = datetime(2026, 8, 12, 0, 15, tzinfo=timezone(timedelta(hours=14)))
    utc_day = now.astimezone(UTC).date()
    prior_hmac = "1" * 64
    current_hmac = "2" * 64
    future_hmac = "3" * 64
    async with app.state.session_factory() as session:
        session.add_all(
            (
                AgentOutreachDirectPeerRateBucket(
                    direct_peer_hmac=prior_hmac,
                    bucket_date=utc_day - timedelta(days=1),
                    request_count=91,
                    updated_at=now - timedelta(days=1),
                ),
                AgentOutreachDirectPeerRateBucket(
                    direct_peer_hmac=current_hmac,
                    bucket_date=utc_day,
                    request_count=7,
                    updated_at=now,
                ),
                AgentOutreachDirectPeerRateBucket(
                    direct_peer_hmac=future_hmac,
                    bucket_date=utc_day + timedelta(days=1),
                    request_count=5,
                    updated_at=now,
                ),
            )
        )
        await session.commit()

    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="direct-peer-prune-test"
    )
    first = await executor.run_once(limit=10, now=now)
    second = await executor.run_once(limit=10, now=now + timedelta(minutes=1))
    assert first == RetentionRunResult()
    assert second == RetentionRunResult()

    async with app.state.session_factory() as session:
        buckets = (
            await session.scalars(
                select(AgentOutreachDirectPeerRateBucket).order_by(
                    AgentOutreachDirectPeerRateBucket.bucket_date,
                    AgentOutreachDirectPeerRateBucket.direct_peer_hmac,
                )
            )
        ).all()
        assert [(row.direct_peer_hmac, row.bucket_date, row.request_count) for row in buckets] == [
            (current_hmac, utc_day, 7),
            (future_hmac, utc_day + timedelta(days=1), 5),
        ]
        assert (await session.scalars(select(LifecycleTask))).all() == []
        assert (await session.scalars(select(RetentionTombstone))).all() == []


async def test_direct_peer_prune_failure_is_sanitized_and_fails_closed(
    api_client, monkeypatch, caplog
) -> None:
    app, _ = api_client
    now = datetime(2026, 8, 12, 0, 15, tzinfo=UTC)
    private_hmac = "a" * 64
    private_failure = "PRIVATE_DIRECT_PEER_PRUNE_FAILURE_7f36b9"
    async with app.state.session_factory() as session:
        session.add(
            AgentOutreachDirectPeerRateBucket(
                direct_peer_hmac=private_hmac,
                bucket_date=now.date() - timedelta(days=1),
                request_count=47,
                updated_at=now - timedelta(days=1),
            )
        )
        await session.commit()

    original_execute = AsyncSession.execute

    async def fail_peer_prune(self, statement, *args, **kwargs):
        if (
            getattr(getattr(statement, "table", None), "name", None)
            == AgentOutreachDirectPeerRateBucket.__tablename__
        ):
            raise RuntimeError(private_failure)
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", fail_peer_prune)
    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="direct-peer-prune-failure-test"
    )
    with pytest.raises(RetentionFailure) as captured:
        await executor.run_once(limit=10, now=now)
    monkeypatch.setattr(AsyncSession, "execute", original_execute)

    assert str(captured.value) == "direct_peer_rate_bucket_prune_failed"
    assert private_hmac not in str(captured.value)
    assert "47" not in str(captured.value)
    assert private_failure not in str(captured.value)
    assert private_hmac not in caplog.text
    assert private_failure not in caplog.text
    async with app.state.session_factory() as session:
        bucket = await session.get(
            AgentOutreachDirectPeerRateBucket,
            (private_hmac, now.date() - timedelta(days=1)),
        )
        assert bucket is not None and bucket.request_count == 47
        assert (await session.scalars(select(LifecycleTask))).all() == []
        assert (await session.scalars(select(RetentionTombstone))).all() == []


async def test_retention_executor_purges_expired_records_once_without_content_residue(
    api_client,
) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    ids = await _insert_expired_records(app, now)
    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="test-worker"
    )

    first = await executor.run_once(limit=20, now=now)
    second = await executor.run_once(limit=20, now=now + timedelta(seconds=2))
    assert first.disposed == 7
    assert second.disposed == 0
    assert not app.state.store._absolute(ids["artifact_path"]).exists()

    async with app.state.session_factory() as session:
        tombstones = (await session.scalars(select(RetentionTombstone))).all()
        tasks = (await session.scalars(select(LifecycleTask))).all()
        assert len(tombstones) == 7
        assert len(tasks) == 7 and {task.state for task in tasks} == {"completed"}
        assert await session.get(Application, ids["application"]) is None
        assert await session.get(ContactRequest, ids["contact_request"]) is None
        assert await session.get(ConnectionRequest, ids["connection_request"]) is None
        assert await session.get(Conversation, ids["conversation"]) is None
        assert await session.get(Message, ids["message"]) is None
        assert await session.get(Notification, ids["notification"]) is None
        assert (
            await session.get(
                OrganizationVerificationEvidence, ids["organization_verification_evidence"]
            )
            is None
        )
        assert (await session.scalars(select(IdempotencyRecord))).all() == []
        assert (await session.scalars(select(ChangeEvent))).all() == []
        serialized = "\n".join(
            f"{task.resource_type}:{task.resource_id}:{task.last_error_code}" for task in tasks
        ) + "\n".join(
            f"{tombstone.resource_type}:{tombstone.resource_id}:{tombstone.policy_version}"
            for tombstone in tombstones
        )
        for secret in (
            "private application body",
            "private message body",
            "private replay residue",
        ):
            assert secret not in serialized


async def test_retention_disposes_application_owned_snapshot_before_its_row(api_client) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    application_id = "30000000-0000-4000-8000-000000000001"
    snapshot_path = app.state.store.application_snapshot_relative_path(application_id)
    snapshot_payload = b"# expired application\n"
    snapshot_sha256 = app.state.store.write_immutable_bytes(snapshot_path, snapshot_payload)
    async with app.state.session_factory() as session:
        session.add(
            Application(
                id=application_id,
                job_id="expired-application-job",
                applicant_owner_id="expired-applicant",
                applicant_actor_id="expired-applicant",
                applicant_actor_method="clerk_jwt",
                applicant_grant_id=None,
                snapshot_document_id="expired-application-document",
                snapshot_document_kind="profile",
                snapshot_document_identifier="expired-application-profile",
                snapshot_document_version=1,
                snapshot_sha256=snapshot_sha256,
                snapshot_size_bytes=len(snapshot_payload),
                snapshot_storage_path=snapshot_path,
                message="expired application body",
                status="submitted",
                confirmed_by_owner_id="expired-applicant",
                confirmed_at=now - timedelta(days=1),
                retention_policy_version="application-retention-v1",
                retention_expires_at=now - timedelta(seconds=1),
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
        )
        await session.commit()
    result = await RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="application-snapshot-retention"
    ).run_once(limit=10, now=now)
    assert result.disposed == 1
    assert not app.state.store._absolute(snapshot_path).exists()
    async with app.state.session_factory() as session:
        assert await session.get(Application, application_id) is None


async def test_retention_preserves_tampered_verification_evidence(api_client) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    ids = await _insert_expired_records(app, now)
    evidence_id = ids["organization_verification_evidence"]
    async with app.state.session_factory() as session:
        evidence = await session.get(OrganizationVerificationEvidence, evidence_id)
        assert evidence is not None
        evidence_path = evidence.storage_path
    tampered_payload = b"tampered-private-artifact"
    app.state.store._absolute(evidence_path).write_bytes(tampered_payload)

    result = await RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="tampered-evidence-retention"
    ).run_once(limit=20, now=now)

    assert result.retried >= 1
    assert app.state.store._absolute(evidence_path).read_bytes() == tampered_payload
    async with app.state.session_factory() as session:
        evidence = await session.get(OrganizationVerificationEvidence, evidence_id)
        task = await session.scalar(
            select(LifecycleTask).where(
                LifecycleTask.resource_type == "organization_verification_evidence",
                LifecycleTask.resource_id == evidence_id,
            )
        )
        tombstone = await session.scalar(
            select(RetentionTombstone).where(
                RetentionTombstone.resource_type == "organization_verification_evidence",
                RetentionTombstone.resource_id == evidence_id,
            )
        )
    assert evidence is not None
    assert task is not None and task.state == "queued"
    assert task.last_error_code == "storage_cleanup_failed"
    assert tombstone is None


async def test_expiry_denies_contact_reads_before_retention_purge(api_client) -> None:
    app, client = api_client
    as_principal(app, human("recipient"))
    now = datetime.now(UTC)
    contact = ContactRequest(
        id="30000000-0000-4000-8000-000000000001",
        sender_owner_id="sender",
        recipient_owner_id="recipient",
        sender_actor_id="sender",
        sender_actor_method="clerk_jwt",
        sender_grant_id=None,
        target_document_id="document",
        purpose="Private purpose",
        message="must be hidden before purge",
        status="pending",
        decision_actor_id=None,
        report_reason=None,
        created_at=now - timedelta(days=366),
        decided_at=None,
        retention_expires_at=now - timedelta(seconds=1),
    )
    async with app.state.session_factory() as session:
        session.add(contact)
        await session.commit()
    inbox = await client.get("/v1/contact-requests/inbox")
    assert inbox.status_code == 200 and inbox.json()["requests"] == []
    decision = await client.post(
        f"/v1/contact-requests/{contact.id}/accept", headers={"Idempotency-Key": "expired-contact"}
    )
    assert decision.status_code == 404
    result = await RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="test-worker"
    ).run_once(limit=10, now=now)
    assert result.disposed == 1


async def test_retention_hold_release_and_bounded_dead_letter(api_client, monkeypatch) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    notification_id = "40000000-0000-4000-8000-000000000001"
    async with app.state.session_factory() as session:
        session.add(
            Notification(
                id=notification_id,
                recipient_owner_id="recipient",
                type="private_notification",
                actor_owner_id=None,
                resource_type="none",
                resource_id="none",
                created_at=now - timedelta(days=1),
                read_at=None,
                retention_expires_at=now - timedelta(seconds=1),
            )
        )
        session.add(
            RetentionHold(
                id="40000000-0000-4000-8000-000000000002",
                resource_type="notification",
                resource_id=notification_id,
                purpose="legal preservation",
                authority="case-owner",
                expires_at=now - timedelta(seconds=1),
                review_at=now - timedelta(hours=1),
                created_at=now - timedelta(days=1),
                released_at=None,
                released_by_authority=None,
            )
        )
        await session.commit()
    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="test-worker"
    )
    held = await executor.run_once(limit=10, now=now)
    assert held.held == 1
    async with app.state.session_factory() as session:
        assert await session.get(Notification, notification_id) is not None
        hold = await session.get(RetentionHold, "40000000-0000-4000-8000-000000000002")
        assert hold is not None
        hold.released_at = now
        hold.released_by_authority = "case-owner"
        await session.commit()
    released = await executor.run_once(limit=10, now=now + timedelta(days=2))
    assert released.disposed == 1

    ids = await _insert_expired_records(app, now + timedelta(days=3))
    original_delete = app.state.store.delete_verified_exact

    def fail_delete(*_args: object, **_kwargs: object) -> None:
        raise StorageIntegrityError("private artifact must not enter task errors")

    monkeypatch.setattr(app.state.store, "delete_verified_exact", fail_delete)
    failing = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="failing-worker", max_attempts=2
    )
    first = await failing.run_once(limit=20, now=now + timedelta(days=3))
    second = await failing.run_once(limit=20, now=now + timedelta(days=3, seconds=3))
    assert first.retried >= 1
    assert second.dead_lettered == 1
    async with app.state.session_factory() as session:
        task = await session.scalar(
            select(LifecycleTask).where(
                LifecycleTask.resource_type == "organization_verification_evidence",
                LifecycleTask.resource_id == ids["organization_verification_evidence"],
            )
        )
        assert task is not None and task.state == "dead_letter"
        assert task.last_error_code == "storage_cleanup_failed"
        assert "private artifact" not in (task.last_error_code or "")
    monkeypatch.setattr(app.state.store, "delete_verified_exact", original_delete)


async def test_ancestor_hold_preserves_application_snapshot_until_explicit_release(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    organization_id = "41000000-0000-4000-8000-000000000001"
    job_id = "41000000-0000-4000-8000-000000000002"
    application_id = "41000000-0000-4000-8000-000000000003"
    hold_id = "41000000-0000-4000-8000-000000000004"
    snapshot_path = app.state.store.application_snapshot_relative_path(application_id)
    snapshot_payload = b"# held application\n"
    snapshot_digest = app.state.store.write_immutable_bytes(snapshot_path, snapshot_payload)
    async with app.state.session_factory() as session:
        session.add_all(
            (
                Organization(
                    id=organization_id,
                    owner_id="held-organization-owner",
                    slug="held-organization",
                    name="Held Organization",
                    visibility="private",
                    verification_status="unverified",
                    verification_material_version=1,
                    version=1,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                ),
                Job(
                    id=job_id,
                    organization_id=organization_id,
                    slug="held-role",
                    title="Held role",
                    description="Application evidence preserved by a parent hold.",
                    status="closed",
                    version=1,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                ),
                Application(
                    id=application_id,
                    job_id=job_id,
                    applicant_owner_id="held-applicant",
                    applicant_actor_id="held-applicant",
                    applicant_actor_method="clerk_jwt",
                    applicant_grant_id=None,
                    snapshot_document_id="held-document",
                    snapshot_document_kind="profile",
                    snapshot_document_identifier="held-profile",
                    snapshot_document_version=1,
                    snapshot_sha256=snapshot_digest,
                    snapshot_size_bytes=len(snapshot_payload),
                    snapshot_storage_path=snapshot_path,
                    message="held application body",
                    status="submitted",
                    confirmed_by_owner_id="held-applicant",
                    confirmed_at=now - timedelta(days=2),
                    retention_policy_version="application-retention-v1",
                    retention_expires_at=now - timedelta(seconds=1),
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                ),
                RetentionHold(
                    id=hold_id,
                    resource_type="job",
                    resource_id=job_id,
                    purpose="legal preservation",
                    authority="legal",
                    expires_at=now - timedelta(seconds=1),
                    review_at=now - timedelta(hours=1),
                    created_at=now - timedelta(days=1),
                ),
            )
        )
        await session.commit()

    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="ancestor-hold-worker"
    )
    held = await executor.run_once(limit=10, now=now)
    assert held.held == 1
    async with app.state.session_factory() as session:
        assert await session.get(Application, application_id) is not None
        task = await session.scalar(
            select(LifecycleTask).where(
                LifecycleTask.resource_type == "application",
                LifecycleTask.resource_id == application_id,
            )
        )
        assert task is not None and task.state == "queued" and task.attempts == 0
        assert task.available_at.replace(tzinfo=task.available_at.tzinfo or UTC) > now
    assert app.state.store.read_verified(snapshot_path, snapshot_digest) == "# held application\n"

    monkeypatch.setattr(cli, "get_settings", lambda: app.state.settings)
    assert await cli.release_retention_hold(Namespace(hold_id=hold_id, authority="legal")) == 0
    release_time = datetime.now(UTC)
    async with app.state.session_factory() as session:
        task = await session.scalar(
            select(LifecycleTask).where(
                LifecycleTask.resource_type == "application",
                LifecycleTask.resource_id == application_id,
            )
        )
        assert task is not None
        assert task.available_at.replace(tzinfo=task.available_at.tzinfo or UTC) <= release_time
    released = await executor.run_once(limit=10, now=release_time)
    assert released.disposed == 1
    assert not app.state.store._absolute(snapshot_path).exists()


async def test_hold_admission_linearizes_before_claim_or_rejects_after_claim(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    hold_first_id = "42000000-0000-4000-8000-000000000001"
    claim_first_id = "42000000-0000-4000-8000-000000000002"
    attempted_id = "42000000-0000-4000-8000-000000000003"
    async with app.state.session_factory() as session:
        session.add_all(
            Notification(
                id=resource_id,
                recipient_owner_id="retention-recipient",
                type="private_notification",
                actor_owner_id=None,
                resource_type="none",
                resource_id="none",
                created_at=now - timedelta(days=1),
                read_at=None,
                retention_expires_at=now - timedelta(seconds=1),
            )
            for resource_id in (hold_first_id, claim_first_id, attempted_id)
        )
        session.add(
            LifecycleTask(
                id="42000000-0000-4000-8000-000000000004",
                resource_type="notification",
                resource_id=attempted_id,
                policy_version="social-retention-v1",
                state="queued",
                attempts=1,
                available_at=now + timedelta(days=1),
                created_at=now,
                last_error_code="disposition_failed",
            )
        )
        await session.commit()

    monkeypatch.setattr(cli, "get_settings", lambda: app.state.settings)

    def hold_args(resource_id: str) -> Namespace:
        return Namespace(
            resource_type="notification",
            resource_id=resource_id,
            purpose="legal preservation",
            authority="legal",
            expires_at=(now + timedelta(days=1)).isoformat(),
            review_at=(now + timedelta(hours=1)).isoformat(),
        )

    assert await cli.create_retention_hold(hold_args(hold_first_id)) == 0
    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="hold-linearization-worker"
    )
    held = await executor.run_once(limit=1, now=now)
    assert held.held == 1
    async with app.state.session_factory() as session:
        assert await session.get(Notification, hold_first_id) is not None

    assert await executor.discover(limit=10, now=now) >= 1
    claim = await executor._claim_one(now=now)
    assert claim is not None and claim.resource_id == claim_first_id
    assert await cli.create_retention_hold(hold_args(claim_first_id)) == 1
    assert await cli.create_retention_hold(hold_args(attempted_id)) == 1
    async with app.state.session_factory() as session:
        accepted = (
            await session.scalars(select(RetentionHold).where(RetentionHold.authority == "legal"))
        ).all()
        assert [(hold.resource_type, hold.resource_id) for hold in accepted] == [
            ("notification", hold_first_id)
        ]


async def test_retention_workers_converge_and_are_not_discoverable(api_client) -> None:
    app, client = api_client
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        session.add(
            Notification(
                id="50000000-0000-4000-8000-000000000001",
                recipient_owner_id="recipient",
                type="private_notification",
                actor_owner_id=None,
                resource_type="none",
                resource_id="none",
                created_at=now - timedelta(days=1),
                read_at=None,
                retention_expires_at=now - timedelta(seconds=1),
            )
        )
        await session.commit()
    first = RetentionExecutor(app.state.session_factory, app.state.store, worker_id="worker-a")
    second = RetentionExecutor(app.state.session_factory, app.state.store, worker_id="worker-b")
    await asyncio.gather(first.run_once(limit=10, now=now), second.run_once(limit=10, now=now))
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(RetentionTombstone))).all()
        assert len((await session.scalars(select(LifecycleTask))).all()) == 1
    schema = app.openapi()
    assert not any("retention" in path for path in schema["paths"])
    assert "retention" not in (await client.get("/llms.txt")).text.lower()


async def test_retention_discovery_skips_existing_tasks_without_starving_later_types(
    api_client,
) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=1)
    async with app.state.session_factory() as session:
        for index in range(12):
            resource_id = f"70000000-0000-4000-8000-{index:012d}"
            session.add(
                Application(
                    id=resource_id,
                    job_id=f"job-{index}",
                    applicant_owner_id=f"applicant-{index}",
                    applicant_actor_id=f"applicant-{index}",
                    applicant_actor_method="clerk_jwt",
                    applicant_grant_id=None,
                    snapshot_document_id=f"document-{index}",
                    snapshot_document_kind="profile",
                    snapshot_document_identifier=f"profile-{index}",
                    snapshot_document_version=1,
                    snapshot_sha256="a" * 64,
                    message="private application",
                    status="submitted",
                    confirmed_by_owner_id=f"applicant-{index}",
                    confirmed_at=expired,
                    retention_policy_version="application-retention-v1",
                    retention_expires_at=expired,
                    created_at=expired,
                    updated_at=expired,
                )
            )
            session.add(
                LifecycleTask(
                    id=f"71000000-0000-4000-8000-{index:012d}",
                    resource_type="application",
                    resource_id=resource_id,
                    policy_version="application-retention-v1",
                    state="queued",
                    attempts=0,
                    available_at=now + timedelta(days=1),
                    lease_expires_at=None,
                    claimed_by=None,
                    claim_token=None,
                    last_error_code=None,
                    created_at=now,
                    completed_at=None,
                )
            )
        session.add(
            Message(
                id="70000000-0000-4000-8000-000000000099",
                conversation_id="later-type-conversation",
                sender_owner_id="sender",
                sender_actor_id="sender",
                sender_actor_method="clerk_jwt",
                markdown="private later message",
                content_sha256="b" * 64,
                status="active",
                created_at=expired,
                retention_expires_at=expired,
            )
        )
        await session.commit()
    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="test-worker"
    )
    assert await executor.discover(limit=5, now=now) == 1
    async with app.state.session_factory() as session:
        task = await session.scalar(
            select(LifecycleTask).where(
                LifecycleTask.resource_type == "message",
                LifecycleTask.resource_id == "70000000-0000-4000-8000-000000000099",
            )
        )
        assert task is not None


async def test_retention_discovery_round_robins_fresh_types_before_limit(api_client) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=1)
    async with app.state.session_factory() as session:
        for index in range(5):
            session.add(
                Application(
                    id=f"72000000-0000-4000-8000-{index:012d}",
                    job_id=f"fresh-job-{index}",
                    applicant_owner_id=f"fresh-applicant-{index}",
                    applicant_actor_id=f"fresh-applicant-{index}",
                    applicant_actor_method="clerk_jwt",
                    applicant_grant_id=None,
                    snapshot_document_id=f"fresh-document-{index}",
                    snapshot_document_kind="profile",
                    snapshot_document_identifier=f"fresh-profile-{index}",
                    snapshot_document_version=1,
                    snapshot_sha256="a" * 64,
                    message="private application",
                    status="submitted",
                    confirmed_by_owner_id=f"fresh-applicant-{index}",
                    confirmed_at=expired,
                    retention_policy_version="application-retention-v1",
                    retention_expires_at=expired,
                    created_at=expired,
                    updated_at=expired,
                )
            )
        session.add(
            Message(
                id="72000000-0000-4000-8000-000000000099",
                conversation_id="fresh-later-type-conversation",
                sender_owner_id="sender",
                sender_actor_id="sender",
                sender_actor_method="clerk_jwt",
                markdown="private later message",
                content_sha256="b" * 64,
                status="active",
                created_at=expired,
                retention_expires_at=expired,
            )
        )
        await session.commit()
    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="test-worker"
    )
    assert await executor.discover(limit=5, now=now) == 5
    async with app.state.session_factory() as session:
        tasks = (await session.scalars(select(LifecycleTask))).all()
        assert len(tasks) == 5
        assert any(
            task.resource_type == "message"
            and task.resource_id == "72000000-0000-4000-8000-000000000099"
            for task in tasks
        )


async def test_retention_defers_then_disposes_a_valid_connection_chain(api_client) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=1)
    request_id = "80000000-0000-4000-8000-000000000001"
    connection_id = "80000000-0000-4000-8000-000000000002"
    conversation_id = "80000000-0000-4000-8000-000000000003"
    message_id = "80000000-0000-4000-8000-000000000004"
    async with app.state.session_factory() as session:
        session.add_all(
            (
                ConnectionRequest(
                    id=request_id,
                    pair_owner_low="a",
                    pair_owner_high="b",
                    requester_owner_id="a",
                    recipient_owner_id="b",
                    requester_profile_handle="a-profile",
                    recipient_profile_handle="b-profile",
                    requested_messaging=True,
                    recipient_messaging_consent=True,
                    status="accepted",
                    requester_actor_id="a",
                    requester_actor_method="clerk_jwt",
                    decision_actor_id="b",
                    created_at=expired,
                    updated_at=expired,
                    decided_at=expired,
                    retention_expires_at=expired,
                ),
                Connection(
                    id=connection_id,
                    connection_request_id=request_id,
                    pair_owner_low="a",
                    pair_owner_high="b",
                    requester_owner_id="a",
                    recipient_owner_id="b",
                    requester_profile_handle="a-profile",
                    recipient_profile_handle="b-profile",
                    requested_messaging=True,
                    recipient_messaging_consent=True,
                    messaging_enabled=True,
                    status="active",
                    created_at=expired,
                    updated_at=expired,
                    ended_at=None,
                    ended_by_owner_id=None,
                    retention_expires_at=expired,
                ),
                Conversation(
                    id=conversation_id,
                    connection_id=connection_id,
                    pair_owner_low="a",
                    pair_owner_high="b",
                    status="active",
                    created_by_owner_id="a",
                    created_at=expired,
                    closed_at=None,
                    retention_expires_at=expired,
                ),
                Message(
                    id=message_id,
                    conversation_id=conversation_id,
                    sender_owner_id="a",
                    sender_actor_id="a",
                    sender_actor_method="clerk_jwt",
                    markdown="private chain message",
                    content_sha256="c" * 64,
                    status="active",
                    created_at=expired,
                    retention_expires_at=expired,
                ),
                LifecycleTask(
                    id="80000000-0000-4000-8000-000000000005",
                    resource_type="connection_request",
                    resource_id=request_id,
                    policy_version="social-retention-v1",
                    state="queued",
                    attempts=0,
                    available_at=now - timedelta(seconds=1),
                    lease_expires_at=None,
                    claimed_by=None,
                    claim_token=None,
                    last_error_code=None,
                    created_at=now - timedelta(seconds=2),
                    completed_at=None,
                ),
            )
        )
        await session.commit()
    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="test-worker"
    )
    first = await executor.run_once(limit=10, now=now)
    assert first.retried >= 1
    for offset in range(1, 6):
        await executor.run_once(limit=10, now=now + timedelta(seconds=offset * 2))
    async with app.state.session_factory() as session:
        assert await session.get(ConnectionRequest, request_id) is None
        assert await session.get(Connection, connection_id) is None
        assert await session.get(Conversation, conversation_id) is None
        assert await session.get(Message, message_id) is None
        tombstones = (await session.scalars(select(RetentionTombstone))).all()
        assert {
            "connection_request",
            "connection",
            "conversation",
            "message",
        }.issubset({row.resource_type for row in tombstones})


async def test_moderation_case_retention_preserves_safe_tombstone_and_skips_open_cases(
    api_client,
) -> None:
    app, _ = api_client
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=1)
    async with app.state.session_factory() as session:
        session.add_all(
            [
                Post(
                    id="moderation-post-closed",
                    owner_id="author",
                    author_profile_document_id="missing-profile-is-not-read",
                    author_profile_handle="author-profile",
                    status="withheld",
                    current_version=1,
                    sha256="a" * 64,
                    storage_path="posts/moderation-post-closed/versions/000001.md",
                    published_at=expired,
                    created_at=expired,
                    updated_at=expired,
                ),
                Post(
                    id="moderation-post-open",
                    owner_id="author",
                    author_profile_document_id="missing-profile-is-not-read",
                    author_profile_handle="author-profile",
                    status="published",
                    current_version=1,
                    sha256="b" * 64,
                    storage_path="posts/moderation-post-open/versions/000001.md",
                    published_at=expired,
                    created_at=expired,
                    updated_at=expired,
                ),
                ModerationCase(
                    id="moderation-case-closed",
                    post_id="moderation-post-closed",
                    subject_owner_id="author",
                    status="appeal_overturned",
                    created_at=expired,
                    updated_at=expired,
                    closed_at=expired,
                    retention_expires_at=expired,
                ),
                ModerationCase(
                    id="moderation-case-open",
                    post_id="moderation-post-open",
                    subject_owner_id="author",
                    status="open",
                    created_at=expired,
                    updated_at=expired,
                    retention_expires_at=expired,
                ),
                PostReport(
                    id="moderation-report",
                    post_id="moderation-post-closed",
                    case_id="moderation-case-closed",
                    reporter_owner_id="private-reporter",
                    reason_code="spam",
                    narrative="private reporter narrative",
                    created_at=expired,
                ),
                ModerationDecision(
                    id="moderation-decision",
                    case_id="moderation-case-closed",
                    post_id="moderation-post-closed",
                    moderator_id="configured-moderator",
                    moderator_role="content_moderator",
                    action="withhold",
                    reason_code="spam",
                    subject_explanation="A bounded explanation",
                    internal_rationale="private rationale",
                    evidence="private evidence",
                    decided_at=expired,
                ),
                ModerationAppeal(
                    id="moderation-appeal",
                    case_id="moderation-case-closed",
                    decision_id="moderation-decision",
                    subject_owner_id="author",
                    rationale="private appeal rationale",
                    status="overturned",
                    submitted_at=expired,
                    reviewed_at=expired,
                    appeal_reviewer_id="configured-appeals",
                    appeal_reviewer_role="appeal_reviewer",
                    internal_rationale="private appeal review",
                ),
            ]
        )
        await session.commit()
    executor = RetentionExecutor(
        app.state.session_factory, app.state.store, worker_id="test-worker"
    )
    result = await executor.run_once(limit=10, now=now)
    assert result.disposed == 1
    async with app.state.session_factory() as session:
        closed_case = await session.get(ModerationCase, "moderation-case-closed")
        open_case = await session.get(ModerationCase, "moderation-case-open")
        report = await session.get(PostReport, "moderation-report")
        decision = await session.get(ModerationDecision, "moderation-decision")
        appeal = await session.get(ModerationAppeal, "moderation-appeal")
        tombstone = await session.scalar(
            select(RetentionTombstone).where(
                RetentionTombstone.resource_type == "moderation_case",
                RetentionTombstone.resource_id == "moderation-case-closed",
            )
        )
        purge_event = await session.scalar(
            select(ModerationAuditEvent).where(
                ModerationAuditEvent.case_id == "moderation-case-closed",
                ModerationAuditEvent.event_type == "sensitive_purged",
            )
        )
        assert closed_case is not None and closed_case.sensitive_purged_at is not None
        assert open_case is not None and open_case.sensitive_purged_at is None
        assert report is not None and report.narrative is None
        assert (
            decision is not None
            and decision.internal_rationale is None
            and decision.evidence is None
        )
        assert appeal is not None and appeal.rationale is None and appeal.internal_rationale is None
        assert tombstone is not None and purge_event is not None
