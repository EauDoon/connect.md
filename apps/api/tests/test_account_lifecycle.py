from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    LifecycleConfirmationClaims,
    Principal,
    assert_account_access,
    lifecycle_hmac,
    require_lifecycle_confirmation_claims,
)
from app.config import Settings
from app.main import create_app
from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountAccessDeny,
    AccountBackupAuthority,
    AccountBackupManifest,
    AccountBackupObligation,
    AccountErasureItem,
    AccountLifecycle,
    AccountLifecycleReceiptRateLimit,
    AccountLifecycleTombstone,
    AccountReverificationUse,
    AgentGrant,
    AgentIdentity,
    AgentProposal,
    ApiKey,
    Application,
    Base,
    Connection,
    ConnectionRequest,
    ContactRequest,
    Conversation,
    Document,
    Job,
    Message,
    ModerationAppeal,
    ModerationCase,
    ModerationDecision,
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
    Post,
    PostVersion,
    RetentionHold,
)
from app.services.deletion_journal import DeletionCommitmentJournal, DeletionJournalError

from .helpers import profile_markdown


def lifecycle_principal(
    *,
    reverification_id: str = "reverification-0001",
    factor_verification_age: tuple[int, int] | None = (1, -1),
    is_impersonated: bool = False,
) -> Principal:
    return Principal(
        subject="user_lifecycle",
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        factor_verification_age=factor_verification_age,
        reverification_id=reverification_id,
        session_id="session-lifecycle-0001",
        token_id="token-lifecycle-0001",
        is_impersonated=is_impersonated,
    )


def lifecycle_post_markdown() -> str:
    return """---
schema: connect.md/post
schema_version: 1
title: Lifecycle public post
topics: [reliability]
visibility: public
---
# Lifecycle public post

An account-lifecycle regression fixture.
"""


@pytest_asyncio.fixture
async def lifecycle_client(
    tmp_path,
) -> AsyncIterator[tuple[object, AsyncClient, dict[str, Principal]]]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}",
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
    app = create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        session.add_all(
            [
                AccountBackupManifest(
                    generation_id="fixture-backup-generation",
                    created_at=now,
                    expires_at=now + timedelta(days=1),
                    state="active",
                    db_manifest_digest="c" * 64,
                    markdown_manifest_digest="d" * 64,
                ),
                AccountBackupAuthority(
                    id=ACCOUNT_BACKUP_AUTHORITY_ID,
                    current_generation_id="fixture-backup-generation",
                    registered_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.commit()
    state = {"principal": lifecycle_principal()}

    async def current() -> Principal:
        return state["principal"]

    async def current_confirmation() -> LifecycleConfirmationClaims:
        principal = state["principal"]
        return LifecycleConfirmationClaims(
            subject=principal.subject,
            factor_verification_age=principal.factor_verification_age,
            reverification_id=principal.reverification_id,
            session_id=principal.session_id,
            token_id=principal.token_id,
            is_impersonated=principal.is_impersonated,
        )

    from app.auth import optional_principal, require_principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current
    app.dependency_overrides[require_lifecycle_confirmation_claims] = current_confirmation
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield app, client, state
    await app.state.engine.dispose()


async def test_disabled_lifecycle_hides_export_before_any_authentication(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    app.dependency_overrides.clear()

    async def unexpected_authentication(*_args, **_kwargs):
        raise AssertionError("disabled lifecycle route must not authenticate")

    monkeypatch.setattr(app.state.api_keys, "verify", unexpected_authentication)
    monkeypatch.setattr(app.state.agent_grants, "verify", unexpected_authentication)
    monkeypatch.setattr(app.state.clerk, "verify", unexpected_authentication)
    responses = [
        await client.post("/v1/account/export"),
        await client.post("/v1/account/export", headers={"Authorization": "Bearer cnd_disabled"}),
        await client.post("/v1/account/export", headers={"Authorization": "Bearer clerk-disabled"}),
    ]
    assert [response.status_code for response in responses] == [404, 404, 404]
    assert all(
        response.json()["detail"] == "account lifecycle is unavailable" for response in responses
    )


async def test_disabled_lifecycle_status_returns_404_before_request_parsing_or_database(
    api_client, monkeypatch
) -> None:
    app, client = api_client

    def unexpected_session():
        raise AssertionError("disabled receipt status must not open a database session")

    monkeypatch.setattr(app.state, "session_factory", unexpected_session)
    response = await client.post(
        "/v1/account/lifecycle-status?unexpected=1",
        headers={"Authorization": "Bearer malformed"},
        json={"unexpected": True},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "account lifecycle status was not found"
    assert response.headers["cache-control"] == "no-store, private"


async def test_lifecycle_requires_exact_clerk_step_up_and_rejects_impersonation(
    lifecycle_client,
) -> None:
    _, client, state = lifecycle_client
    state["principal"] = lifecycle_principal(factor_verification_age=None)
    missing = await client.post("/v1/account/export")
    assert missing.status_code == 403
    assert missing.json() == {
        "clerk_error": {
            "type": "forbidden",
            "reason": "reverification-error",
            "metadata": {"reverification": {"level": "second_factor", "afterMinutes": 10}},
        }
    }
    state["principal"] = Principal(
        subject="user_lifecycle",
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        factor_verification_age=(True, -1),  # type: ignore[arg-type]
        reverification_id="reverification-malformed",
        session_id="session-lifecycle-0001",
        token_id="token-lifecycle-0001",
    )
    malformed = await client.post("/v1/account/export")
    assert malformed.status_code == 403
    assert malformed.json() == missing.json()
    state["principal"] = lifecycle_principal(
        reverification_id="reverification-fresh", factor_verification_age=(9, -1)
    )
    assert (await client.post("/v1/account/export")).status_code == 200
    state["principal"] = lifecycle_principal(
        reverification_id="reverification-stale", factor_verification_age=(1, 10)
    )
    assert (await client.post("/v1/account/export")).json() == missing.json()
    state["principal"] = lifecycle_principal(
        reverification_id="reverification-impersonated", is_impersonated=True
    )
    impersonated = await client.post("/v1/account/export")
    assert impersonated.status_code == 403
    assert impersonated.json()["detail"] == "account_lifecycle_impersonation_forbidden"


async def test_direct_bounded_export_consumes_one_step_up_and_excludes_raw_account_identity(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "lifecycle-export-profile-create"},
    )
    assert created.status_code == 201, created.text
    exported = await client.post("/v1/account/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("application/x-ndjson")
    assert exported.headers["content-disposition"].startswith("attachment;")
    assert "user_lifecycle" not in exported.text
    assert "storage_path" not in exported.text
    lines = [line for line in exported.text.splitlines() if line]
    assert len(lines) == 2
    assert '"record_type":"account_export"' in lines[0]
    assert '"record_type":"document"' in lines[1]
    canonical_markdown = json.loads(lines[1])["versions"][0]["canonical_markdown"]
    assert "Designed an API." in canonical_markdown
    async with app.state.session_factory() as session:
        uses = (await session.scalars(select(AccountReverificationUse))).all()
        assert len(uses) == 1
        assert uses[0].purpose == "export"
        assert state["principal"].reverification_id not in str(uses[0].__dict__)
    replay = await client.post("/v1/account/export")
    assert replay.status_code == 409
    assert replay.json()["detail"] == "reverification_already_used"


async def test_export_cap_and_request_validation_do_not_consume_step_up(lifecycle_client) -> None:
    app, client, state = lifecycle_client
    invalid = await client.post("/v1/account/export", content=b"{}")
    assert invalid.status_code == 422
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(AccountReverificationUse))).all() == []
    state["principal"] = lifecycle_principal(reverification_id="reverification-cap")
    app.state.settings.account_export_max_bytes = 1024
    oversized = profile_markdown().replace("## Skills\n\n- Python", "## Skills\n\n" + "x" * 2_000)
    created = await client.post(
        "/v1/profiles",
        json={"markdown": oversized},
        headers={"Idempotency-Key": "lifecycle-capped-profile-create"},
    )
    assert created.status_code == 201, created.text
    capped = await client.post("/v1/account/export")
    assert capped.status_code == 413
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(AccountReverificationUse))).all() == []


async def test_export_includes_only_each_subject_safe_record_shape(lifecycle_client) -> None:
    app, client, _ = lifecycle_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "lifecycle-safe-shape-profile-create"},
    )
    assert created.status_code == 201, created.text
    now = datetime.now(UTC)
    later = now + timedelta(days=365)
    subject = "user_lifecycle"
    counterparty = "counterparty-raw-identifier"
    private_markers = {
        counterparty,
        "counterparty-message-secret",
        "moderator-secret",
        "decision-rationale-secret",
        "decision-evidence-secret",
        "appeal-rationale-secret",
        "appeal-reviewer-secret",
        "appeal-internal-secret",
        "verification-submitter-secret",
        "verification-reviewer-secret",
        "evidence-metadata-secret",
        "verification-artifact-secret",
        "organization-description-secret",
        "employer-raw-identifier",
        "agent-raw-identifier",
        "grant-raw-identifier",
    }
    async with app.state.session_factory() as session:
        profile = await session.scalar(
            select(Document).where(Document.owner_id == subject, Document.kind == "profile")
        )
        assert profile is not None
        post_path = app.state.store.relative_path("post", "export-post", 1)
        post_markdown = "# Exported post\n"
        post_digest = app.state.store.write_immutable(post_path, post_markdown)
        post = Post(
            id="export-post",
            owner_id=subject,
            author_profile_document_id=profile.id,
            author_profile_handle=profile.public_identifier,
            status="published",
            current_version=1,
            sha256=post_digest,
            storage_path=post_path,
            published_at=now,
            created_at=now,
            updated_at=now,
        )
        post.versions.append(
            PostVersion(version=1, sha256=post_digest, storage_path=post_path, created_at=now)
        )
        organization = Organization(
            id="export-org",
            owner_id=subject,
            slug="export-org",
            name="Export Organization",
            description="organization-description-secret",
            website_url="https://organization.example.test",
            visibility="private",
            verification_status="active",
            verification_material_version=1,
            version=1,
            created_at=now,
            updated_at=now,
        )
        job = Job(
            id="export-job",
            organization_id=organization.id,
            slug="export-job",
            title="Export job",
            description="job-description-secret",
            status="draft",
            version=1,
            created_at=now,
            updated_at=now,
        )
        request = ConnectionRequest(
            id="export-connection-request",
            pair_owner_low=counterparty,
            pair_owner_high=subject,
            requester_owner_id=counterparty,
            recipient_owner_id=subject,
            requester_profile_handle="counterparty-handle-secret",
            recipient_profile_handle="subject-handle",
            requested_messaging=True,
            recipient_messaging_consent=True,
            status="accepted",
            requester_actor_id=counterparty,
            requester_actor_method="clerk_jwt",
            decision_actor_id=subject,
            created_at=now,
            updated_at=now,
            decided_at=now,
            retention_expires_at=later,
        )
        connection = Connection(
            id="export-connection",
            connection_request_id=request.id,
            pair_owner_low=counterparty,
            pair_owner_high=subject,
            requester_owner_id=counterparty,
            recipient_owner_id=subject,
            requester_profile_handle="counterparty-handle-secret",
            recipient_profile_handle="subject-handle",
            requested_messaging=True,
            recipient_messaging_consent=True,
            messaging_enabled=True,
            status="active",
            created_at=now,
            updated_at=now,
            retention_expires_at=later,
        )
        conversation = Conversation(
            id="export-conversation",
            connection_id=connection.id,
            pair_owner_low=counterparty,
            pair_owner_high=subject,
            status="active",
            created_by_owner_id=counterparty,
            created_at=now,
            retention_expires_at=later,
        )
        case = ModerationCase(
            id="export-case",
            post_id=post.id,
            subject_owner_id=subject,
            status="appealed",
            created_at=now,
            updated_at=now,
            retention_expires_at=later,
        )
        decision = ModerationDecision(
            id="export-decision",
            case_id=case.id,
            post_id=post.id,
            moderator_id="moderator-secret",
            moderator_role="content_moderator",
            action="withhold",
            reason_code="private_reason",
            subject_explanation="subject-decision-explanation",
            internal_rationale="decision-rationale-secret",
            evidence="decision-evidence-secret",
            decided_at=now,
        )
        verification = OrganizationVerification(
            id="export-verification",
            organization_id=organization.id,
            purpose="recruiting_control",
            submitted_by_owner_id="verification-submitter-secret",
            material_claim_digest="d" * 64,
            created_at=now,
        )
        session.add_all(
            [
                post,
                organization,
                job,
                request,
                connection,
                conversation,
                Message(
                    id="export-message-owned",
                    conversation_id=conversation.id,
                    sender_owner_id=subject,
                    sender_actor_id=subject,
                    sender_actor_method="clerk_jwt",
                    markdown="sender-owned-message",
                    content_sha256="a" * 64,
                    status="active",
                    created_at=now,
                    retention_expires_at=later,
                ),
                Message(
                    id="export-message-counterparty",
                    conversation_id=conversation.id,
                    sender_owner_id=counterparty,
                    sender_actor_id=counterparty,
                    sender_actor_method="clerk_jwt",
                    markdown="counterparty-message-secret",
                    content_sha256="b" * 64,
                    status="active",
                    created_at=now,
                    retention_expires_at=later,
                ),
                Application(
                    id="export-application",
                    job_id=job.id,
                    applicant_owner_id=subject,
                    applicant_actor_id=subject,
                    applicant_actor_method="clerk_jwt",
                    snapshot_document_id=profile.id,
                    snapshot_document_kind="profile",
                    snapshot_document_identifier=profile.public_identifier,
                    snapshot_document_version=profile.current_version,
                    snapshot_sha256="c" * 64,
                    message="application-message",
                    status="submitted",
                    confirmed_by_owner_id="employer-raw-identifier",
                    confirmed_at=now,
                    retention_policy_version="application-v1",
                    retention_expires_at=later,
                    created_at=now,
                    updated_at=now,
                ),
                AgentProposal(
                    id="export-proposal",
                    owner_id=subject,
                    submitter_actor_id="agent-raw-identifier",
                    submitter_grant_id="grant-raw-identifier",
                    document_id=profile.id,
                    document_kind="profile",
                    document_identifier=profile.public_identifier,
                    markdown="proposal-markdown",
                    if_match='"proposal-etag"',
                    status="pending",
                    created_at=now,
                ),
                ContactRequest(
                    id="export-contact",
                    sender_owner_id=subject,
                    recipient_owner_id=counterparty,
                    sender_actor_id=subject,
                    sender_actor_method="clerk_jwt",
                    target_document_id="counterparty-document-id",
                    purpose="outbound contact purpose",
                    message="outbound-contact-message",
                    status="pending",
                    origin="profile_contact",
                    created_at=now,
                    retention_expires_at=later,
                ),
                case,
                decision,
                ModerationAppeal(
                    id="export-appeal",
                    case_id=case.id,
                    decision_id=decision.id,
                    subject_owner_id=subject,
                    rationale="appeal-rationale-secret",
                    status="upheld",
                    submitted_at=now,
                    reviewed_at=now,
                    appeal_reviewer_id="appeal-reviewer-secret",
                    appeal_reviewer_role="appeal_reviewer",
                    subject_explanation="appeal-subject-explanation",
                    internal_rationale="appeal-internal-secret",
                ),
                verification,
                OrganizationVerificationEvent(
                    id="export-verification-event",
                    verification_id=verification.id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    to_state="active",
                    actor_id="verification-reviewer-secret",
                    actor_role="recruiting_verifier",
                    policy_version="verification-policy-v1",
                    material_claim_digest="d" * 64,
                    expires_at=later,
                    occurred_at=now,
                ),
                OrganizationVerificationEvidence(
                    id="export-evidence",
                    verification_id=verification.id,
                    evidence_kind="other",
                    metadata_json='{"secret":"evidence-metadata-secret"}',
                    artifact_content_type="text/plain",
                    artifact_sha256="e" * 64,
                    artifact_size_bytes=32,
                    storage_path="verification-artifact-secret",
                    created_at=now,
                    retention_expires_at=later,
                ),
            ]
        )
        await session.commit()
    exported = await client.post("/v1/account/export")
    assert exported.status_code == 200, exported.text
    content = exported.text
    for record_type in {
        '"record_type":"document"',
        '"record_type":"post"',
        '"record_type":"message"',
        '"record_type":"application"',
        '"record_type":"agent_proposal"',
        '"record_type":"outbound_contact_request"',
        '"record_type":"connection_request"',
        '"record_type":"connection"',
        '"record_type":"conversation"',
        '"record_type":"moderation_case"',
        '"record_type":"moderation_appeal"',
        '"record_type":"organization_verification"',
    }:
        assert record_type in content
    for included in {
        post_markdown.strip(),
        "sender-owned-message",
        "application-message",
        "proposal-markdown",
        "outbound-contact-message",
        "subject-decision-explanation",
        "appeal-subject-explanation",
        "verification-policy-v1",
        "d" * 64,
    }:
        assert included in content
    for excluded in private_markers:
        assert excluded not in content


async def test_deletion_request_is_pending_only_and_cancellation_retains_only_step_up_use(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    created = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "delete-request-0001"}
    )
    assert created.status_code == 202, created.text
    payload = created.json()
    assert set(payload) == {"deletion_id", "status_receipt"}
    assert re.fullmatch(r"lr1_[A-Za-z0-9_-]{43}", payload["status_receipt"])
    assert "user_lifecycle" not in created.text
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, payload["deletion_id"])
        assert lifecycle is not None and lifecycle.provider_subject_ciphertext is None
        assert lifecycle.request_idempotency_hmac != "delete-request-0001"
    replay = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "delete-request-0001"}
    )
    assert replay.status_code == 202
    assert replay.json() == payload
    async with app.state.session_factory() as session:
        assert len((await session.scalars(select(AccountLifecycle))).all()) == 1
        assert len((await session.scalars(select(AccountReverificationUse))).all()) == 1
    state["principal"] = lifecycle_principal(reverification_id="reverification-different-key")
    different_key = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "delete-request-0002"}
    )
    assert different_key.status_code == 409
    assert different_key.json()["detail"] == "account_deletion_request_exists"
    cancelled = await client.post(f"/v1/account-deletion-requests/{payload['deletion_id']}/cancel")
    assert cancelled.status_code == 204
    assert cancelled.content == b""
    async with app.state.session_factory() as session:
        assert await session.get(AccountLifecycle, payload["deletion_id"]) is None
        uses = (await session.scalars(select(AccountReverificationUse))).all()
        assert len(uses) == 1 and uses[0].purpose == "delete_request"
    state["principal"] = lifecycle_principal(reverification_id="reverification-next")
    assert (
        await client.post(f"/v1/account-deletion-requests/{payload['deletion_id']}/cancel")
    ).status_code == 404


async def test_concurrent_same_key_deletion_requests_return_the_same_opaque_id(
    lifecycle_client,
) -> None:
    app, client, _ = lifecycle_client
    requests = [
        client.post(
            "/v1/account-deletion-requests", headers={"Idempotency-Key": "delete-request-race"}
        )
        for _ in range(2)
    ]
    first, second = await asyncio.gather(*requests)
    assert [first.status_code, second.status_code] == [202, 202]
    assert first.json() == second.json()
    assert set(first.json()) == {"deletion_id", "status_receipt"}
    async with app.state.session_factory() as session:
        assert len((await session.scalars(select(AccountLifecycle))).all()) == 1
        assert len((await session.scalars(select(AccountReverificationUse))).all()) == 1


async def test_lifecycle_receipt_status_is_local_sanitized_and_credential_indistinguishable(
    lifecycle_client, monkeypatch
) -> None:
    app, client, _ = lifecycle_client
    created = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "receipt-status-create"}
    )
    assert created.status_code == 202, created.text
    deletion_id = created.json()["deletion_id"]
    receipt = created.json()["status_receipt"]
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        assert lifecycle.receipt_hmac == lifecycle_hmac(
            app.state.settings, "status-receipt", receipt
        )
        assert lifecycle.receipt_hmac != receipt
        assert lifecycle.receipt_ciphertext is not None
        assert receipt not in lifecycle.receipt_ciphertext

    async def unexpected_authentication(*_args, **_kwargs):
        raise AssertionError("receipt status must not invoke an account authenticator")

    app.dependency_overrides.clear()
    monkeypatch.setattr(app.state.api_keys, "verify", unexpected_authentication)
    monkeypatch.setattr(app.state.agent_grants, "verify", unexpected_authentication)
    monkeypatch.setattr(app.state.clerk, "verify", unexpected_authentication)
    status = await client.post(
        "/v1/account/lifecycle-status",
        headers={"Authorization": f"LifecycleReceipt {receipt}"},
    )
    assert status.status_code == 200, status.text
    assert set(status.json()) == {
        "contract",
        "state",
        "observed_at",
        "requested_at",
        "confirmed_at",
        "live_erased_at",
        "terminal_at",
        "policy_version",
        "condition",
        "next_check_after_seconds",
        "receipt_expires_at",
    }
    assert status.json()["contract"] == "account_lifecycle_status.v1"
    assert status.json()["state"] == "confirmation_pending"
    assert status.json()["confirmed_at"] is None
    assert status.json()["condition"] is None
    assert status.headers["cache-control"] == "no-store, private"
    assert status.headers["pragma"] == "no-cache"
    assert status.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    for forbidden in (deletion_id, receipt, "user_lifecycle", "receipt_hmac", "ciphertext"):
        assert forbidden not in status.text

    invalid = [
        await client.post("/v1/account/lifecycle-status"),
        await client.post(
            "/v1/account/lifecycle-status", headers={"Authorization": f"Bearer {receipt}"}
        ),
        await client.post(
            "/v1/account/lifecycle-status",
            headers={"Authorization": f"LifecycleReceipt {deletion_id}"},
        ),
        await client.post(
            "/v1/account/lifecycle-status",
            headers={"Authorization": "LifecycleReceipt lr1_" + "x" * 43},
        ),
        await client.post(
            "/v1/account/lifecycle-status",
            headers={"Authorization": f"LifecycleReceipt {receipt} trailing"},
        ),
    ]
    assert {response.status_code for response in invalid} == {404}
    assert {
        (
            response.json()["type"],
            response.json()["title"],
            response.json()["status"],
            response.json()["detail"],
            response.json()["instance"],
        )
        for response in invalid
    } == {
        (
            "https://connect.md/problems/not-found",
            "Not Found",
            404,
            "account lifecycle status was not found",
            "/v1/account/lifecycle-status",
        )
    }
    assert all(response.headers["cache-control"] == "no-store, private" for response in invalid)
    assert (
        await client.post(
            "/v1/account/lifecycle-status?unexpected=1",
            headers={"Authorization": f"LifecycleReceipt {receipt}"},
        )
    ).status_code == 422
    assert (
        await client.post(
            "/v1/account/lifecycle-status",
            headers={"Authorization": f"LifecycleReceipt {receipt}"},
            json={},
        )
    ).status_code == 422


async def test_lifecycle_receipt_recovery_replays_rotates_and_closes_on_confirmation(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    creation_key = "receipt-recovery-create"
    created = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": creation_key}
    )
    assert created.status_code == 202, created.text
    deletion_id = created.json()["deletion_id"]
    original = created.json()["status_receipt"]

    state["principal"] = lifecycle_principal(reverification_id="receipt-recovery-step-up-1")
    recovered = await client.post(
        "/v1/account-deletion-receipts/recover",
        headers={"Idempotency-Key": "receipt-recovery-key-1"},
    )
    assert recovered.status_code == 200, recovered.text
    rotated_once = recovered.json()["status_receipt"]
    assert rotated_once != original
    assert recovered.json()["deletion_id"] == deletion_id
    assert (
        await client.post(
            "/v1/account/lifecycle-status",
            headers={"Authorization": f"LifecycleReceipt {original}"},
        )
    ).status_code == 404

    state["principal"] = lifecycle_principal(reverification_id="receipt-recovery-replay")
    replay = await client.post(
        "/v1/account-deletion-receipts/recover",
        headers={"Idempotency-Key": "receipt-recovery-key-1"},
    )
    assert replay.status_code == 200
    assert replay.json() == recovered.json()
    creation_replay = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": creation_key}
    )
    assert creation_replay.status_code == 409

    state["principal"] = lifecycle_principal(reverification_id="receipt-recovery-step-up-1")
    reused_step_up = await client.post(
        "/v1/account-deletion-receipts/recover",
        headers={"Idempotency-Key": "receipt-recovery-key-2"},
    )
    assert reused_step_up.status_code == 409
    assert reused_step_up.json()["detail"] == "reverification_already_used"
    assert (
        await client.post(
            "/v1/account/lifecycle-status",
            headers={"Authorization": f"LifecycleReceipt {rotated_once}"},
        )
    ).status_code == 200

    state["principal"] = lifecycle_principal(reverification_id="receipt-recovery-step-up-2")
    second_recovery = await client.post(
        "/v1/account-deletion-receipts/recover",
        headers={"Idempotency-Key": "receipt-recovery-key-2"},
    )
    assert second_recovery.status_code == 200
    rotated_twice = second_recovery.json()["status_receipt"]
    assert rotated_twice not in {original, rotated_once}
    assert (
        await client.post(
            "/v1/account/lifecycle-status",
            headers={"Authorization": f"LifecycleReceipt {rotated_once}"},
        )
    ).status_code == 404

    state["principal"] = lifecycle_principal(reverification_id="receipt-confirm-step-up")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "receipt-confirm-key"},
    )
    assert confirmed.status_code == 202, confirmed.text
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        assert lifecycle.state == "concealed"
        assert lifecycle.receipt_ciphertext is None
        assert lifecycle.receipt_recovery_idempotency_hmac is None
        assert lifecycle.receipt_hmac == lifecycle_hmac(
            app.state.settings, "status-receipt", rotated_twice
        )
    confirmed_status = await client.post(
        "/v1/account/lifecycle-status",
        headers={"Authorization": f"LifecycleReceipt {rotated_twice}"},
    )
    assert confirmed_status.status_code == 200
    assert confirmed_status.json()["state"] == "confirmed"
    state["principal"] = lifecycle_principal(reverification_id="receipt-recovery-too-late")
    too_late = await client.post(
        "/v1/account-deletion-receipts/recover",
        headers={"Idempotency-Key": "receipt-recovery-key-too-late"},
    )
    assert too_late.status_code == 404


async def test_lifecycle_receipt_cancellation_invalidates_and_cascades_rate_state(
    lifecycle_client,
) -> None:
    app, client, _ = lifecycle_client
    created = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "receipt-cancel-create"}
    )
    deletion_id = created.json()["deletion_id"]
    receipt = created.json()["status_receipt"]
    status_headers = {"Authorization": f"LifecycleReceipt {receipt}"}
    assert (
        await client.post("/v1/account/lifecycle-status", headers=status_headers)
    ).status_code == 200
    cancelled = await client.post(f"/v1/account-deletion-requests/{deletion_id}/cancel")
    assert cancelled.status_code == 204
    assert (
        await client.post("/v1/account/lifecycle-status", headers=status_headers)
    ).status_code == 404
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(AccountLifecycleReceiptRateLimit))).all() == []


async def _install_full_terminal_receipt(
    app, client: AsyncClient, state: dict[str, Principal], suffix: str
) -> tuple[str, str, datetime]:
    state["principal"] = Principal(
        subject=f"user_lifecycle_{suffix}",
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        factor_verification_age=(1, -1),
        reverification_id=f"terminal-proof-step-{suffix}",
        session_id="session-lifecycle-0001",
        token_id="token-lifecycle-0001",
    )
    created = await client.post(
        "/v1/account-deletion-requests",
        headers={"Idempotency-Key": f"terminal-proof-create-{suffix}"},
    )
    assert created.status_code == 202, created.text
    deletion_id = created.json()["deletion_id"]
    receipt = created.json()["status_receipt"]
    state["principal"] = Principal(
        subject=f"user_lifecycle_{suffix}",
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        factor_verification_age=(1, -1),
        reverification_id=f"terminal-proof-confirm-step-{suffix}",
        session_id="session-lifecycle-0001",
        token_id="token-lifecycle-0001",
    )
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": f"terminal-proof-confirm-{suffix}"},
    )
    assert confirmed.status_code == 202, confirmed.text
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        assert lifecycle.confirmed_at is not None
        confirmed_at = (
            lifecycle.confirmed_at
            if lifecycle.confirmed_at.tzinfo is not None
            else lifecycle.confirmed_at.replace(tzinfo=UTC)
        ).astimezone(UTC)
        lifecycle.state = "fully_erased"
        lifecycle.provider_state = "verified"
        lifecycle.backup_state = "verified"
        lifecycle.safe_failure_code = None
        lifecycle.provider_subject_ciphertext = None
        lifecycle.provider_session_ciphertext = None
        lifecycle.live_erased_at = confirmed_at
        lifecycle.terminal_at = confirmed_at
        items = (
            await session.scalars(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
            )
        ).all()
        assert items
        for item in items:
            item.state = "completed"
            item.completed_at = confirmed_at
            item.available_at = None
            item.lease_expires_at = None
            item.claimed_by = None
            item.claim_token = None
            item.hold_kind = None
            item.hold_id = None
            item.hold_review_at = None
        obligations = (
            await session.scalars(
                select(AccountBackupObligation).where(
                    AccountBackupObligation.deletion_id == deletion_id
                )
            )
        ).all()
        assert obligations
        for obligation in obligations:
            obligation.state = "verified"
            obligation.db_manifest_digest = obligation.db_manifest_digest or "c" * 64
            obligation.markdown_manifest_digest = obligation.markdown_manifest_digest or "d" * 64
            obligation.proof_digest = "e" * 64
            obligation.verified_at = confirmed_at
        lifecycle_digest = sha256(
            f"connect.md:lifecycle:terminal:v1:{lifecycle.id}:{lifecycle.policy_version}:"
            f"{confirmed_at.isoformat()}".encode()
        ).hexdigest()
        session.add(
            AccountLifecycleTombstone(
                deletion_id=lifecycle.id,
                policy_version=lifecycle.policy_version,
                phase="fully_erased",
                result_digest=lifecycle_digest,
                occurred_at=confirmed_at,
            )
        )
        await session.commit()
    return deletion_id, receipt, confirmed_at


async def test_lifecycle_receipt_terminal_proof_expiration_conditions_and_rate_limit(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    created = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "receipt-terminal-create"}
    )
    deletion_id = created.json()["deletion_id"]
    receipt = created.json()["status_receipt"]
    headers = {"Authorization": f"LifecycleReceipt {receipt}"}
    terminal_at = datetime.now(UTC) - timedelta(days=29)
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        lifecycle.state = "fully_erased"
        lifecycle.terminal_at = terminal_at
        await session.commit()
    assert (await client.post("/v1/account/lifecycle-status", headers=headers)).status_code == 404
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(AccountLifecycleReceiptRateLimit))).all() == []

    async with app.state.session_factory() as session:
        session.add(
            AccountLifecycleTombstone(
                deletion_id=deletion_id,
                policy_version=app.state.settings.account_lifecycle_policy_version,
                phase="fully_erased",
                result_digest="e" * 64,
                occurred_at=terminal_at + timedelta(seconds=1),
            )
        )
        await session.commit()
    assert (await client.post("/v1/account/lifecycle-status", headers=headers)).status_code == 404
    # Restore the deliberately incomplete fixture to its original pending state
    # so the independent full-proof fixture can pass the global mirror check.
    async with app.state.session_factory() as session:
        incomplete = await session.get(AccountLifecycle, deletion_id)
        tombstone = await session.scalar(
            select(AccountLifecycleTombstone).where(
                AccountLifecycleTombstone.deletion_id == deletion_id
            )
        )
        assert incomplete is not None and tombstone is not None
        incomplete.state = "confirmation_pending"
        incomplete.terminal_at = None
        await session.delete(tombstone)
        await session.commit()
    _, valid_receipt, _ = await _install_full_terminal_receipt(app, client, state, "valid")
    valid_headers = {"Authorization": f"LifecycleReceipt {valid_receipt}"}
    terminal = await client.post("/v1/account/lifecycle-status", headers=valid_headers)
    assert terminal.status_code == 200, terminal.text
    assert terminal.json()["state"] == "fully_erased"
    assert terminal.json()["next_check_after_seconds"] == 0
    assert terminal.json()["receipt_expires_at"] is not None

    async with app.state.session_factory() as session:
        lifecycle = await session.scalar(
            select(AccountLifecycle).where(
                AccountLifecycle.receipt_hmac
                == lifecycle_hmac(app.state.settings, "status-receipt", valid_receipt)
            )
        )
        assert lifecycle is not None
        tombstone = await session.scalar(
            select(AccountLifecycleTombstone).where(
                AccountLifecycleTombstone.deletion_id == lifecycle.id
            )
        )
        assert tombstone is not None
        expired_at = datetime.now(UTC) - timedelta(days=31)
        lifecycle.terminal_at = tombstone.occurred_at = expired_at
        await session.commit()
    assert (
        await client.post("/v1/account/lifecycle-status", headers=valid_headers)
    ).status_code == 404

    async with app.state.session_factory() as session:
        lifecycle = await session.scalar(
            select(AccountLifecycle).where(
                AccountLifecycle.receipt_hmac
                == lifecycle_hmac(app.state.settings, "status-receipt", valid_receipt)
            )
        )
        assert lifecycle is not None
        tombstone = await session.scalar(
            select(AccountLifecycleTombstone).where(
                AccountLifecycleTombstone.deletion_id == lifecycle.id
            )
        )
        assert tombstone is not None
        assert lifecycle.confirmed_at is not None
        current_terminal = (
            lifecycle.confirmed_at
            if lifecycle.confirmed_at.tzinfo is not None
            else lifecycle.confirmed_at.replace(tzinfo=UTC)
        ).astimezone(UTC)
        lifecycle.live_erased_at = current_terminal
        lifecycle.terminal_at = tombstone.occurred_at = current_terminal
        tombstone.result_digest = sha256(
            f"connect.md:lifecycle:terminal:v1:{lifecycle.id}:{lifecycle.policy_version}:"
            f"{current_terminal.isoformat()}".encode()
        ).hexdigest()
        rate = await session.scalar(
            select(AccountLifecycleReceiptRateLimit).where(
                AccountLifecycleReceiptRateLimit.receipt_hmac == lifecycle.receipt_hmac,
                AccountLifecycleReceiptRateLimit.ip_hmac
                == lifecycle_hmac(app.state.settings, "status-receipt-ip", "127.0.0.1"),
                AccountLifecycleReceiptRateLimit.window_started_at
                == datetime.now(UTC).replace(second=0, microsecond=0),
            )
        )
        assert rate is not None
        rate.request_count = 20
        rate.updated_at = datetime.now(UTC)
        await session.commit()
    limited = await client.post("/v1/account/lifecycle-status", headers=valid_headers)
    assert limited.status_code == 429
    assert limited.json()["detail"] == "rate limit exceeded"
    assert limited.headers["retry-after"] == "60"
    assert limited.headers["cache-control"] == "no-store, private"
    async with app.state.session_factory() as session:
        rate = await session.scalar(select(AccountLifecycleReceiptRateLimit))
        assert rate is not None
        assert rate.receipt_hmac == lifecycle_hmac(
            app.state.settings, "status-receipt", valid_receipt
        )
        assert rate.ip_hmac == lifecycle_hmac(app.state.settings, "status-receipt-ip", "127.0.0.1")
        assert valid_receipt not in str(rate.__dict__)
        assert "127.0.0.1" not in str(rate.__dict__)


@pytest.mark.parametrize(
    "mutation",
    [
        "marker",
        "digest",
        "deny_missing",
        "deny_mismatch",
        "journal_unavailable",
        "mirror_unavailable",
        "provider_state",
        "backup_state",
        "live_erased",
        "ciphertext",
        "item",
        "obligation",
        "terminal",
    ],
)
async def test_lifecycle_status_terminal_proof_fail_closed_without_rate_mutation(
    lifecycle_client, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, state = lifecycle_client
    deletion_id, receipt, terminal_at = await _install_full_terminal_receipt(
        app, client, state, mutation
    )
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        if mutation == "marker":
            lifecycle.confirmation_idempotency_hmac = "bad"
        elif mutation == "digest":
            tombstone = await session.scalar(
                select(AccountLifecycleTombstone).where(
                    AccountLifecycleTombstone.deletion_id == deletion_id
                )
            )
            assert tombstone is not None
            tombstone.result_digest = "0" * 64
        elif mutation == "deny_missing":
            deny = await session.scalar(
                select(AccountAccessDeny).where(AccountAccessDeny.deletion_id == deletion_id)
            )
            assert deny is not None
            await session.delete(deny)
        elif mutation == "deny_mismatch":
            deny = await session.scalar(
                select(AccountAccessDeny).where(AccountAccessDeny.deletion_id == deletion_id)
            )
            assert deny is not None
            deny.denied_at = terminal_at - timedelta(seconds=1)
        elif mutation == "provider_state":
            lifecycle.provider_state = "pending"
        elif mutation == "backup_state":
            lifecycle.backup_state = "expiry_pending"
        elif mutation == "live_erased":
            lifecycle.live_erased_at = None
        elif mutation == "ciphertext":
            lifecycle.provider_subject_ciphertext = "v1.corrupt"
        elif mutation == "item":
            item = await session.scalar(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
            )
            assert item is not None
            item.state = "queued"
        elif mutation == "obligation":
            obligation = await session.scalar(
                select(AccountBackupObligation).where(
                    AccountBackupObligation.deletion_id == deletion_id
                )
            )
            assert obligation is not None
            obligation.state = "pending"
            obligation.proof_digest = None
        elif mutation == "terminal":
            lifecycle.terminal_at = terminal_at + timedelta(days=1)
        await session.commit()
    if mutation == "journal_unavailable":
        app.state.deletion_journal = None
    elif mutation == "mirror_unavailable":

        def unavailable_mirror() -> None:
            raise DeletionJournalError("mirror unavailable")

        monkeypatch.setattr(app.state.deletion_journal, "verify", unavailable_mirror)
    before = 0
    async with app.state.session_factory() as session:
        before = len((await session.scalars(select(AccountLifecycleReceiptRateLimit))).all())
    response = await client.post(
        "/v1/account/lifecycle-status",
        headers={"Authorization": f"LifecycleReceipt {receipt}"},
    )
    assert response.status_code == 404, response.text
    async with app.state.session_factory() as session:
        after = len((await session.scalars(select(AccountLifecycleReceiptRateLimit))).all())
    assert after == before == 0


async def test_lifecycle_receipt_routes_are_hidden_from_discovery_surfaces(
    lifecycle_client,
) -> None:
    _, client, _ = lifecycle_client
    responses = [
        await client.get("/openapi.json"),
        await client.get("/v1/capabilities"),
        await client.get("/llms.txt"),
        await client.get("/llms-full.txt"),
        await client.get("/.well-known/agent-card.json"),
        await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    ]
    assert all(response.status_code == 200 for response in responses)
    discovery = "\n".join(response.text for response in responses)
    assert "/v1/account/lifecycle-status" not in discovery
    assert "/v1/account-deletion-receipts/recover" not in discovery
    assert "LifecycleReceipt" not in discovery


async def test_lifecycle_confirmation_requires_key_and_advertises_openapi_header(
    lifecycle_client,
) -> None:
    _, client, state = lifecycle_client
    requested = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "confirm-key-request"}
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = lifecycle_principal(reverification_id="confirm-key-step-up")
    path = f"/v1/account-deletion-requests/{deletion_id}/confirm"
    missing = await client.post(path)
    assert missing.status_code == 428
    malformed = await client.post(path, headers={"Idempotency-Key": "bad\u007f"})
    assert malformed.status_code == 400
    openapi = await client.get("/openapi.json")
    assert openapi.status_code == 200
    operation = openapi.json()["paths"]["/v1/account-deletion-requests/{deletion_id}/confirm"][
        "post"
    ]
    key_parameters = {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "header"
    }
    assert key_parameters["Idempotency-Key"]["required"] is True
    assert key_parameters["Idempotency-Key"]["schema"]["minLength"] == 1
    assert key_parameters["Idempotency-Key"]["schema"]["maxLength"] == 128


async def test_lifecycle_confirmation_lost_ack_replays_without_second_step_up_or_mutation(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    requested = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "confirm-replay-request"}
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    path = f"/v1/account-deletion-requests/{deletion_id}/confirm"
    key = "confirm-replay-key"
    state["principal"] = lifecycle_principal(reverification_id="confirm-replay-step-up-1")
    first = await client.post(path, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    assert first.json() == {"deletion_id": deletion_id}
    assert first.headers.get("idempotency-replayed") is None
    async with app.state.session_factory() as session:
        use_count_after_first = len((await session.scalars(select(AccountReverificationUse))).all())
    state["principal"] = lifecycle_principal(reverification_id="confirm-replay-step-up-2")
    replay = await client.post(path, headers={"Idempotency-Key": key})
    assert replay.status_code == 202, replay.text
    assert replay.json() == first.json()
    assert replay.headers["idempotency-replayed"] == "true"
    async with app.state.session_factory() as session:
        uses = (await session.scalars(select(AccountReverificationUse))).all()
        assert len(uses) == use_count_after_first
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        assert lifecycle.confirmation_idempotency_hmac is not None
        assert lifecycle.request_idempotency_hmac is None
    state["principal"] = lifecycle_principal(reverification_id="confirm-replay-collision")
    collision = await client.post(path, headers={"Idempotency-Key": "confirm-other-key"})
    assert collision.status_code == 409
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(
                select(AccountReverificationUse.id).where(
                    AccountReverificationUse.reverification_id_hmac
                    == lifecycle_hmac(
                        app.state.settings, "reverification", "confirm-replay-collision"
                    )
                )
            )
            is None
        )
    state["principal"] = Principal(
        subject="reused-clerk-subject",
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        factor_verification_age=(1, -1),
        reverification_id="confirm-replay-subject-reuse",
        session_id="session-lifecycle-0001",
        token_id="token-lifecycle-0001",
    )
    subject_reuse = await client.post(path, headers={"Idempotency-Key": key})
    assert subject_reuse.status_code == 404


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        ("marker", 503),
        ("deny", 503),
        ("provider", 503),
        ("tombstone", 503),
        ("terminal", 503),
        ("journal", 503),
        ("provider_completed", 404),
    ],
)
async def test_lifecycle_confirmation_replay_corruption_fails_closed(
    lifecycle_client, mutation: str, expected_status: int
) -> None:
    app, client, state = lifecycle_client
    requested = await client.post(
        "/v1/account-deletion-requests",
        headers={"Idempotency-Key": f"confirm-corruption-request-{mutation}"},
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    path = f"/v1/account-deletion-requests/{deletion_id}/confirm"
    key = f"confirm-corruption-key-{mutation}"
    state["principal"] = lifecycle_principal(
        reverification_id=f"confirm-corruption-step-{mutation}"
    )
    first = await client.post(path, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        if mutation == "marker":
            lifecycle.confirmation_idempotency_hmac = "bad"
        elif mutation == "deny":
            deny = await session.scalar(
                select(AccountAccessDeny).where(AccountAccessDeny.deletion_id == deletion_id)
            )
            assert deny is not None
            deny.subject_hmac = "f" * 64
        elif mutation == "provider":
            lifecycle.provider_session_ciphertext = "v1.invalid"
        elif mutation == "tombstone":
            session.add(
                AccountLifecycleTombstone(
                    deletion_id=deletion_id,
                    policy_version=lifecycle.policy_version,
                    phase="corrupt",
                    result_digest="0" * 64,
                    occurred_at=datetime.now(UTC),
                )
            )
        elif mutation == "terminal":
            lifecycle.terminal_at = datetime.now(UTC)
        elif mutation == "provider_completed":
            lifecycle.provider_state = "verified"
        await session.commit()
    if mutation == "journal":
        app.state.deletion_journal_consistent = False
    state["principal"] = lifecycle_principal(
        reverification_id=f"confirm-corruption-replay-{mutation}"
    )
    replay = await client.post(path, headers={"Idempotency-Key": key})
    assert replay.status_code == expected_status, replay.text


def test_lifecycle_confirmation_migration_upgrade_and_safe_downgrade(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "lifecycle-confirmation-migration.db"
    environment = os.environ.copy()
    environment["CONNECTMD_DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0024_lifecycle_confirmation_idempotency"],
        cwd=api_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row[3] for row in connection.execute("PRAGMA table_info('account_lifecycles')")
        }
        assert columns["request_idempotency_hmac"] == 0
        assert columns["confirmation_idempotency_hmac"] == 0
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0023_contact_request_status_constraint"],
        cwd=api_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        names = {row[1] for row in connection.execute("PRAGMA table_info('account_lifecycles')")}
        assert "confirmation_idempotency_hmac" not in names


def test_lifecycle_confirmation_migration_refuses_unsafe_downgrade(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "lifecycle-confirmation-unsafe.db"
    environment = os.environ.copy()
    environment["CONNECTMD_DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0024_lifecycle_confirmation_idempotency"],
        cwd=api_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    now = datetime.now(UTC).isoformat()
    row_sql = """
        INSERT INTO account_lifecycles (
            id, subject_hmac, request_idempotency_hmac, confirmation_idempotency_hmac,
            receipt_hmac, receipt_ciphertext, receipt_recovery_idempotency_hmac,
            provider_subject_ciphertext, provider_session_ciphertext, state, provider_state,
            backup_state, policy_version, requested_at, confirmed_at, concealed_at,
            live_erased_at, terminal_at, safe_failure_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = [
        (
            "unsafe-request-marker",
            "1" * 64,
            None,
            None,
            "2" * 64,
            None,
            None,
            None,
            None,
            "confirmation_pending",
            "pending",
            "expiry_pending",
            "v1",
            now,
            None,
            None,
            None,
            None,
            None,
        ),
        (
            "unsafe-confirmation-marker",
            "3" * 64,
            "4" * 64,
            "5" * 64,
            "6" * 64,
            None,
            None,
            None,
            None,
            "confirmation_pending",
            "pending",
            "expiry_pending",
            "v1",
            now,
            None,
            None,
            None,
            None,
            None,
        ),
    ]
    with sqlite3.connect(database) as connection:
        connection.executemany(row_sql, rows)
        connection.commit()
    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0023_contact_request_status_constraint"],
        cwd=api_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert downgrade.returncode != 0
    assert "cannot downgrade lifecycle confirmation idempotency" in (
        downgrade.stdout + downgrade.stderr
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0024_lifecycle_confirmation_idempotency",
        )
        names = {row[1] for row in connection.execute("PRAGMA table_info('account_lifecycles')")}
        assert "confirmation_idempotency_hmac" in names
        preserved = connection.execute(
            "SELECT id, request_idempotency_hmac, confirmation_idempotency_hmac "
            "FROM account_lifecycles ORDER BY id"
        ).fetchall()
        assert preserved == [
            ("unsafe-confirmation-marker", "4" * 64, "5" * 64),
            ("unsafe-request-marker", None, None),
        ]


async def test_reused_reverification_for_a_different_deletion_request_is_rejected(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    settings = app.state.settings
    principal = state["principal"]
    async with app.state.session_factory() as session:
        session.add(
            AccountReverificationUse(
                reverification_id_hmac=lifecycle_hmac(
                    settings, "reverification", principal.reverification_id or ""
                ),
                subject_hmac=lifecycle_hmac(settings, "subject", principal.subject),
                sid_hmac=lifecycle_hmac(settings, "sid", principal.session_id or ""),
                jti_hmac=lifecycle_hmac(settings, "jti", principal.token_id or ""),
                purpose="export",
                action_hmac=lifecycle_hmac(settings, "action", "prior-action"),
                used_at=datetime.now(UTC),
            )
        )
        await session.commit()
    response = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "different-request-key"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "reverification_already_used"
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(AccountLifecycle))).all() == []


async def test_concealed_account_denies_agent_credentials_before_last_used_mutation(
    lifecycle_client,
) -> None:
    app, _, _ = lifecycle_client
    settings = app.state.settings
    async with app.state.session_factory() as session:
        api_key, raw_api_key = await app.state.api_keys.create(
            session, "concealed-owner", ["documents:read"]
        )
        grant, raw_grant = await app.state.agent_grants.create(
            session,
            owner_id="concealed-owner",
            actor_id="concealed-owner",
            name="concealed grant",
            scopes=["documents:read"],
            mode="direct",
            resource_type="owner",
            resource_id=None,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        expired_grant, raw_expired_grant = await app.state.agent_grants.create(
            session,
            owner_id="concealed-owner",
            actor_id="concealed-owner",
            name="expired concealed grant",
            scopes=["documents:read"],
            mode="direct",
            resource_type="owner",
            resource_id=None,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        lifecycle = AccountLifecycle(
            subject_hmac=lifecycle_hmac(settings, "subject", "concealed-owner"),
            request_idempotency_hmac=lifecycle_hmac(settings, "delete-request-key", "concealed"),
            state="concealed",
            provider_state="pending",
            backup_state="expiry_pending",
            policy_version="account-lifecycle-v1",
            requested_at=datetime.now(UTC),
        )
        session.add(lifecycle)
        await session.flush()
        session.add(
            AccountAccessDeny(
                subject_hmac=lifecycle.subject_hmac,
                deletion_id=lifecycle.id,
                denied_at=datetime.now(UTC),
            )
        )
        await session.commit()
        with pytest.raises(HTTPException, match="account_access_denied"):
            await app.state.api_keys.verify(session, raw_api_key)
        with pytest.raises(HTTPException, match="account_access_denied"):
            await app.state.agent_grants.verify(session, raw_grant)
        with pytest.raises(HTTPException, match="account_access_denied"):
            await app.state.agent_grants.verify(session, raw_expired_grant)
        await session.refresh(api_key)
        await session.refresh(grant)
        await session.refresh(expired_grant)
        assert api_key.last_used_at is None
        assert grant.last_used_at is None
        assert expired_grant.last_used_at is None


async def test_deletion_confirmation_is_atomic_concealed_and_retains_only_safe_inventory(
    lifecycle_client, monkeypatch
) -> None:
    app, client, state = lifecycle_client
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "lifecycle-scrub-profile-create"},
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["id"]
    post = await client.post(
        "/v1/posts",
        json={"markdown": lifecycle_post_markdown()},
        headers={"Idempotency-Key": "lifecycle-confirm-post-0001"},
    )
    assert post.status_code == 201, post.text
    deletion = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "lifecycle-delete-0001"}
    )
    assert deletion.status_code == 202, deletion.text
    deletion_id = deletion.json()["deletion_id"]

    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        api_key, raw_api_key = await app.state.api_keys.create(
            session, "user_lifecycle", ["documents:read"]
        )
        grant, raw_grant = await app.state.agent_grants.create(
            session,
            owner_id="user_lifecycle",
            actor_id="user_lifecycle",
            name="confirmation grant",
            scopes=["documents:read"],
            mode="direct",
            resource_type="owner",
            resource_id=None,
            expires_at=now + timedelta(days=1),
        )
        session.add_all(
            [
                AgentIdentity(
                    owner_id="user_lifecycle",
                    handle="lifecycle-agent",
                    display_name="Lifecycle agent",
                    description="A public lifecycle fixture.",
                    profile_document_id=profile_id,
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                Organization(
                    owner_id="user_lifecycle",
                    slug="lifecycle-org",
                    name="Lifecycle organization",
                    visibility="public",
                    created_at=now,
                    updated_at=now,
                ),
                AccountBackupManifest(
                    generation_id="lifecycle-backup-generation",
                    created_at=now,
                    expires_at=now + timedelta(days=30),
                    state="active",
                    db_manifest_digest="a" * 64,
                    markdown_manifest_digest="b" * 64,
                ),
                RetentionHold(
                    resource_type="document",
                    resource_id=profile_id,
                    purpose="legal preservation",
                    authority="retention-authority",
                    expires_at=now + timedelta(days=30),
                    review_at=now + timedelta(days=7),
                    created_at=now,
                ),
                RetentionHold(
                    resource_type="api_key",
                    resource_id=api_key.id,
                    purpose="credential evidence",
                    authority="retention-authority",
                    expires_at=now + timedelta(days=30),
                    review_at=now + timedelta(days=7),
                    created_at=now,
                ),
                RetentionHold(
                    resource_type="agent_grant",
                    resource_id=grant.id,
                    purpose="grant evidence",
                    authority="retention-authority",
                    expires_at=now + timedelta(days=30),
                    review_at=now + timedelta(days=7),
                    created_at=now,
                ),
                ContactRequest(
                    id="00000000-0000-0000-0000-000000000123",
                    sender_owner_id="user_lifecycle",
                    recipient_owner_id="counterparty-owner",
                    sender_actor_id="user_lifecycle",
                    sender_actor_method="clerk_jwt",
                    origin="profile_contact",
                    target_document_id=profile_id,
                    purpose="held contact purpose",
                    message="held contact message",
                    status="pending",
                    created_at=now,
                    retention_expires_at=now + timedelta(days=30),
                ),
                RetentionHold(
                    resource_type="contact_request",
                    resource_id="00000000-0000-0000-0000-000000000123",
                    purpose="contact preservation",
                    authority="retention-authority",
                    expires_at=now + timedelta(days=30),
                    review_at=now + timedelta(days=7),
                    created_at=now,
                ),
            ]
        )
        await session.flush()
        organization = await session.scalar(
            select(Organization).where(Organization.slug == "lifecycle-org")
        )
        assert organization is not None
        session.add(
            Job(
                organization_id=organization.id,
                slug="lifecycle-role",
                title="Lifecycle role",
                description="A held organization job.",
                status="published",
                published_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    state["principal"] = lifecycle_principal(reverification_id="reverification-confirm-0001")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "lifecycle-confirm-key"},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json() == {"deletion_id": deletion_id}

    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        assert lifecycle.state == "concealed"
        assert lifecycle.confirmed_at is not None and lifecycle.concealed_at is not None
        assert lifecycle.provider_subject_ciphertext is not None
        assert lifecycle.provider_subject_ciphertext.startswith("v1.")
        assert "user_lifecycle" not in lifecycle.provider_subject_ciphertext
        assert await session.scalar(
            select(AccountAccessDeny.id).where(AccountAccessDeny.deletion_id == deletion_id)
        )
        assert (await session.get(Document, profile_id)).visibility == "private"  # type: ignore[union-attr]
        post_row = await session.get(Post, post.json()["id"])
        assert post_row is not None and post_row.status == "withdrawn"
        identity = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == "lifecycle-agent")
        )
        assert identity is not None and identity.status == "withdrawn"
        organization = await session.scalar(
            select(Organization).where(Organization.slug == "lifecycle-org")
        )
        assert organization is not None and organization.visibility == "private"
        job = await session.scalar(select(Job).where(Job.organization_id == organization.id))
        assert job is not None and job.status == "closed"
        api_key_row = await session.get(ApiKey, api_key.id)
        grant_row = await session.get(AgentGrant, grant.id)
        assert api_key_row is not None and api_key_row.revoked
        assert grant_row is not None and grant_row.revoked
        items = (
            await session.scalars(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
            )
        ).all()
        item_keys = {(item.resource_type, item.resource_id, item.phase) for item in items}
        assert len(item_keys) == len(items)
        assert "user_lifecycle" not in "".join(
            f"{item.resource_type}:{item.resource_id}" for item in items
        )
        document_items = [
            item
            for item in items
            if item.resource_type == "document" and item.resource_id == profile_id
        ]
        assert {(item.phase, item.state) for item in document_items} >= {
            ("conceal", "completed"),
            ("delete_row", "held"),
        }
        for resource_type, resource_id in (("api_key", api_key.id), ("agent_grant", grant.id)):
            resource_items = [
                item
                for item in items
                if item.resource_type == resource_type and item.resource_id == resource_id
            ]
            assert {(item.phase, item.state) for item in resource_items} >= {
                ("revoke", "completed"),
                ("delete_row", "held"),
            }
            assert any(
                item.resource_type == "backup_manifest"
                and item.disposition == "hold"
                and item.state == "queued"
                for item in items
            )
        assert {
            (item.resource_type, item.phase, item.state)
            for item in items
            if item.resource_type in {"organization", "job"}
        } >= {
            ("organization", "delete_row", "held"),
            ("job", "delete_row", "held"),
        }
        held_contact = await session.get(ContactRequest, "00000000-0000-0000-0000-000000000123")
        assert held_contact is not None
        assert held_contact.status == "blocked"
        assert held_contact.purpose == held_contact.message == "Deleted account"
        assert "user_lifecycle" not in str(held_contact.__dict__)
        with pytest.raises(HTTPException, match="account_access_denied"):
            await app.state.api_keys.verify(session, raw_api_key)
        with pytest.raises(HTTPException, match="account_access_denied"):
            await app.state.agent_grants.verify(session, raw_grant)
        with pytest.raises(HTTPException, match="account_access_denied"):
            await assert_account_access(
                session, app.state.settings, "user_lifecycle", mutation=False
            )
        assert api_key_row.last_used_at is None
        assert grant_row.last_used_at is None

    async def anonymous_principal() -> None:
        return None

    from app.auth import optional_principal

    app.dependency_overrides[optional_principal] = anonymous_principal

    async def stale_search(**_kwargs):
        return ([{"id": profile_id, "version": 1}], 1, {})

    monkeypatch.setattr(app.state.search, "search", stale_search)
    assert (await client.get("/v1/profiles/ada-lovelace")).status_code == 404
    assert (await client.get(f"/v1/posts/{post.json()['id']}")).status_code == 404
    assert (await client.get("/v1/agent-identities/lifecycle-agent")).status_code == 404
    assert (await client.get("/v1/organizations/lifecycle-org")).status_code == 404
    assert (await client.get("/v1/jobs")).json()["jobs"] == []
    search = await client.get("/v1/search", params={"q": "lifecycle"})
    assert search.status_code == 200
    assert search.json()["hits"] == []
    state["principal"] = Principal(
        subject="counterparty-owner", method="clerk_jwt", scopes=frozenset({"*"})
    )
    held_inbox = await client.get("/v1/contact-requests/inbox")
    assert held_inbox.status_code == 200
    assert held_inbox.json()["requests"] == []

    state["principal"] = lifecycle_principal(reverification_id="reverification-confirm-repeat")
    repeated = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "lifecycle-confirm-repeat-key"},
    )
    assert repeated.status_code == 409


async def test_confirmation_detaches_counterparty_history_without_policy_deadlock(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "shared-history-profile"},
    )
    assert profile.status_code == 201, profile.text
    now = datetime.now(UTC)
    later = now + timedelta(days=30)
    request_id = "71000000-0000-4000-8000-000000000001"
    connection_id = "71000000-0000-4000-8000-000000000002"
    conversation_id = "71000000-0000-4000-8000-000000000003"
    counterparty_message_id = "71000000-0000-4000-8000-000000000004"
    subject_message_id = "71000000-0000-4000-8000-000000000005"
    async with app.state.session_factory() as session:
        subject_document = await session.get(Document, profile.json()["id"])
        assert subject_document is not None
        subject_handle = subject_document.public_identifier
    counterparty = "counterparty-owner"
    async with app.state.session_factory() as session:
        session.add_all(
            [
                ConnectionRequest(
                    id=request_id,
                    pair_owner_low=counterparty,
                    pair_owner_high="user_lifecycle",
                    requester_owner_id=counterparty,
                    recipient_owner_id="user_lifecycle",
                    requester_profile_handle="counterparty-profile",
                    recipient_profile_handle=subject_handle,
                    requested_messaging=True,
                    recipient_messaging_consent=True,
                    status="accepted",
                    requester_actor_id=counterparty,
                    requester_actor_method="clerk_jwt",
                    decision_actor_id="user_lifecycle",
                    created_at=now,
                    updated_at=now,
                    decided_at=now,
                    retention_expires_at=later,
                ),
                Connection(
                    id=connection_id,
                    connection_request_id=request_id,
                    pair_owner_low=counterparty,
                    pair_owner_high="user_lifecycle",
                    requester_owner_id=counterparty,
                    recipient_owner_id="user_lifecycle",
                    requester_profile_handle="counterparty-profile",
                    recipient_profile_handle=subject_handle,
                    requested_messaging=True,
                    recipient_messaging_consent=True,
                    messaging_enabled=True,
                    status="active",
                    created_at=now,
                    updated_at=now,
                    retention_expires_at=later,
                ),
                Conversation(
                    id=conversation_id,
                    connection_id=connection_id,
                    pair_owner_low=counterparty,
                    pair_owner_high="user_lifecycle",
                    status="active",
                    created_by_owner_id="user_lifecycle",
                    created_at=now,
                    retention_expires_at=later,
                ),
                Message(
                    id=counterparty_message_id,
                    conversation_id=conversation_id,
                    sender_owner_id=counterparty,
                    sender_actor_id=counterparty,
                    sender_actor_method="clerk_jwt",
                    markdown=f"Prior note for user_lifecycle at @{subject_handle}.",
                    content_sha256="a" * 64,
                    status="active",
                    created_at=now,
                    retention_expires_at=later,
                ),
                Message(
                    id=subject_message_id,
                    conversation_id=conversation_id,
                    sender_owner_id="user_lifecycle",
                    sender_actor_id="user_lifecycle",
                    sender_actor_method="clerk_jwt",
                    markdown="Delete this subject-authored message.",
                    content_sha256="b" * 64,
                    status="active",
                    created_at=now,
                    retention_expires_at=later,
                ),
            ]
        )
        await session.commit()

    requested = await client.post(
        "/v1/account-deletion-requests",
        headers={"Idempotency-Key": "shared-history-request"},
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = lifecycle_principal(reverification_id="shared-history-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "shared-history-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text

    async with app.state.session_factory() as session:
        request_row = await session.get(ConnectionRequest, request_id)
        connection_row = await session.get(Connection, connection_id)
        conversation_row = await session.get(Conversation, conversation_id)
        counterparty_message = await session.get(Message, counterparty_message_id)
        subject_message = await session.get(Message, subject_message_id)
        assert request_row is not None and connection_row is not None
        assert conversation_row is not None and counterparty_message is not None
        assert subject_message is not None
        detached_rows = (request_row, connection_row, conversation_row, counterparty_message)
        assert all("user_lifecycle" not in str(row.__dict__) for row in detached_rows)
        assert subject_handle not in str(request_row.__dict__)
        assert subject_handle not in str(connection_row.__dict__)
        assert subject_handle not in counterparty_message.markdown
        assert (
            counterparty_message.content_sha256
            == sha256(counterparty_message.markdown.encode("utf-8")).hexdigest()
        )
        items = (
            await session.scalars(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
            )
        ).all()
        shared_items = [
            item
            for item in items
            if (item.resource_type, item.resource_id)
            in {
                ("connection_request", request_id),
                ("connection", connection_id),
                ("conversation", conversation_id),
                ("message", counterparty_message_id),
            }
        ]
        assert shared_items
        assert all(item.state == "completed" and item.hold_kind is None for item in shared_items)
        assert not any(item.phase == "delete_row" for item in shared_items)
        assert any(
            item.resource_type == "message"
            and item.resource_id == subject_message_id
            and item.phase == "delete_row"
            and item.state == "queued"
            for item in items
        )
        with pytest.raises(HTTPException, match="account_access_denied"):
            await assert_account_access(
                session, app.state.settings, "user_lifecycle", mutation=False
            )


async def test_deletion_confirmation_reused_step_up_rolls_back_all_mutations(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "lifecycle-rollback-profile-create"},
    )
    assert profile.status_code == 201, profile.text
    deletion = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "lifecycle-delete-rollback"}
    )
    assert deletion.status_code == 202, deletion.text
    deletion_id = deletion.json()["deletion_id"]
    state["principal"] = lifecycle_principal(reverification_id="reverification-confirm-reused")
    principal = state["principal"]
    async with app.state.session_factory() as session:
        session.add(
            AccountReverificationUse(
                reverification_id_hmac=lifecycle_hmac(
                    app.state.settings, "reverification", principal.reverification_id or ""
                ),
                subject_hmac=lifecycle_hmac(app.state.settings, "subject", principal.subject),
                sid_hmac=lifecycle_hmac(app.state.settings, "sid", principal.session_id or ""),
                jti_hmac=lifecycle_hmac(app.state.settings, "jti", principal.token_id or ""),
                purpose="export",
                action_hmac=lifecycle_hmac(app.state.settings, "action", "prior-confirmation"),
                used_at=datetime.now(UTC),
            )
        )
        await session.commit()
    rejected = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "lifecycle-confirm-reused-key"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "reverification_already_used"
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "confirmation_pending"
        assert lifecycle.confirmed_at is None and lifecycle.provider_subject_ciphertext is None
        document = await session.get(Document, profile.json()["id"])
        assert document is not None and document.visibility == "public"
        assert (await session.scalars(select(AccountErasureItem))).all() == []
        assert (await session.scalars(select(AccountAccessDeny))).all() == []


async def test_deletion_confirmation_and_cancellation_are_serialized_on_the_subject_lifecycle(
    lifecycle_client,
) -> None:
    app, client, state = lifecycle_client
    deletion = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "lifecycle-delete-race"}
    )
    assert deletion.status_code == 202, deletion.text
    deletion_id = deletion.json()["deletion_id"]
    state["principal"] = lifecycle_principal(reverification_id="reverification-confirm-race")
    confirmation, cancellation = await asyncio.gather(
        client.post(
            f"/v1/account-deletion-requests/{deletion_id}/confirm",
            headers={"Idempotency-Key": "lifecycle-confirm-race-key"},
        ),
        client.post(f"/v1/account-deletion-requests/{deletion_id}/cancel"),
    )
    assert sorted((confirmation.status_code, cancellation.status_code)) in ([202, 404], [204, 404])
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        denied = await session.scalar(
            select(AccountAccessDeny.id).where(AccountAccessDeny.deletion_id == deletion_id)
        )
        if confirmation.status_code == 202:
            assert lifecycle is not None and lifecycle.state == "concealed"
            assert denied is not None
        else:
            assert cancellation.status_code == 204
            assert lifecycle is None
            assert denied is None


async def test_commit_failure_after_external_append_stays_durable_and_fails_closed(
    lifecycle_client, monkeypatch
) -> None:
    app, client, state = lifecycle_client
    deletion = await client.post(
        "/v1/account-deletion-requests",
        headers={"Idempotency-Key": "lifecycle-delete-external-commit-failure"},
    )
    assert deletion.status_code == 202, deletion.text
    deletion_id = deletion.json()["deletion_id"]
    state["principal"] = lifecycle_principal(
        reverification_id="reverification-external-commit-failure"
    )
    original_commit = AsyncSession.commit

    async def fail_after_journal_append(session: AsyncSession) -> None:
        if app.state.deletion_journal_consistent is False:
            raise IntegrityError("forced post-journal failure", {}, RuntimeError("forced"))
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_after_journal_append)
    confirmation = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "lifecycle-confirm-external-failure-key"},
    )
    assert confirmation.status_code == 503
    assert confirmation.json()["detail"] == "deletion commitment journal requires recovery"
    commitments = app.state.deletion_journal.verify()
    assert [item.deletion_id for item in commitments] == [deletion_id]
    entry_bytes = next(
        (app.state.settings.deletion_journal_path / "entries").iterdir()
    ).read_bytes()
    assert state["principal"].subject.encode("utf-8") not in entry_bytes
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "confirmation_pending"
        assert (
            await session.scalar(
                select(AccountAccessDeny.id).where(AccountAccessDeny.deletion_id == deletion_id)
            )
            is None
        )
    assert (await client.get("/readyz")).status_code == 503

    restarted = create_app(app.state.settings)
    try:
        with pytest.raises(DeletionJournalError, match="sets do not match"):
            async with restarted.router.lifespan_context(restarted):
                pass
    finally:
        await restarted.state.engine.dispose()
