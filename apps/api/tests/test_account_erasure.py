from __future__ import annotations

import json
import os
import stat
from argparse import Namespace
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app import cli
from app.account_erasure_worker import _refresh_health_heartbeat
from app.auth import LifecycleConfirmationClaims, Principal, require_lifecycle_confirmation_claims
from app.config import Settings
from app.main import create_app
from app.markdown import client_template
from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountAccessDeny,
    AccountBackupAuthority,
    AccountBackupManifest,
    AccountErasureFileProof,
    AccountErasureItem,
    AccountLifecycle,
    AccountLifecycleReceiptRateLimit,
    AccountLifecycleTombstone,
    Application,
    Connection,
    ConnectionRequest,
    Conversation,
    Document,
    DocumentVersion,
    IdentifierReservation,
    Job,
    Message,
    ModerationCase,
    Organization,
    Post,
    PostVersion,
    PublicTaxonomyDocumentSnapshot,
    PublicTaxonomyMembership,
    PublicTaxonomyProjectionState,
    PublicTaxonomyTerm,
    RetentionHold,
    SearchProjectionTask,
)
from app.services.account_erasure import AccountErasureExecutor, ClerkLifecycleProvider
from app.services.deletion_journal import DeletionCommitmentJournal
from app.services.search import SearchDeleteAttestation
from app.services.search_projection import SearchProjectionExecutor
from app.services.storage import StorageIntegrityError, VersionStore
from app.services.taxonomy import TAXONOMY_CONTRACT_DIGEST, TAXONOMY_TYPES

from .helpers import profile_markdown


class FakeProvider(ClerkLifecycleProvider):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.session_outcome = "deleted"
        self.user_outcome = "deleted"
        self.ready = True

    async def check_ready(self) -> None:
        if not self.ready:
            raise RuntimeError("simulated provider readiness failure")

    async def revoke_sessions(self, *, subject: str, current_session_id: str):
        self.calls.append(f"session:{subject}:{current_session_id}")
        return self.session_outcome

    async def delete_user(self, *, subject: str):
        self.calls.append(f"user:{subject}")
        return self.user_outcome


class FakeSearch:
    def __init__(self, *, configured: bool = True) -> None:
        self.deleted: list[str] = []
        self.configured = configured
        self.live_documents: set[str] = set()
        self.ready = True

    async def check_ready(self) -> None:
        if not self.ready:
            raise RuntimeError("simulated search readiness failure")

    async def delete_document(self, document_id: str) -> SearchDeleteAttestation:
        self.deleted.append(document_id)
        if self.configured:
            self.live_documents.discard(document_id)
        return SearchDeleteAttestation(
            configured=self.configured,
            state="deleted" if self.configured else "unconfigured",
        )


class UnexpectedFailureSearch(FakeSearch):
    def __init__(self, sentinel: str) -> None:
        super().__init__()
        self.sentinel = sentinel

    async def delete_document(self, document_id: str) -> SearchDeleteAttestation:
        raise RuntimeError(self.sentinel)


class UnexpectedFailureProvider(FakeProvider):
    def __init__(self, sentinel: str) -> None:
        super().__init__()
        self.sentinel = sentinel

    async def revoke_sessions(self, *, subject: str, current_session_id: str):
        raise RuntimeError(self.sentinel)


async def consume_erasure_projection_tombstone(app, search: FakeSearch) -> None:
    projection = SearchProjectionExecutor(
        app.state.session_factory,
        app.state.store,
        search,  # type: ignore[arg-type]
        worker_id="erasure-projection-test",
    )
    assert (
        await projection.run_once(now=datetime.now(UTC) + timedelta(seconds=10))
    ).action == "removed_missing"


def test_lifecycle_poll_interval_cannot_outlive_heartbeat_freshness() -> None:
    with pytest.raises(ValueError):
        Settings(account_lifecycle_poll_seconds=31)


def principal(reverification_id: str = "erasure-reverification") -> Principal:
    return Principal(
        subject="erasure-owner",
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        factor_verification_age=(1, -1),
        reverification_id=reverification_id,
        session_id="erasure-current-session",
        token_id="erasure-token",
    )


def erasure_profile_v2_markdown() -> str:
    return client_template(
        "profile", "Ada Lovelace\nErasure taxonomy profile\nSkills\nPython"
    ).replace("visibility: private", "visibility: public")


async def install_taxonomy_ready(app: object) -> None:
    async with app.state.session_factory() as session:
        session.add_all(
            PublicTaxonomyProjectionState(
                taxonomy=taxonomy,
                revision=1,
                status="ready",
                contract_digest=TAXONOMY_CONTRACT_DIGEST,
                updated_at=datetime.now(UTC),
            )
            for taxonomy in TAXONOMY_TYPES
        )
        await session.commit()


@pytest_asyncio.fixture
async def erasure_client(
    tmp_path,
) -> AsyncIterator[tuple[object, AsyncClient, dict[str, Principal]]]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'erasure.db'}",
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
    from app.models import Base

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
    state = {"principal": principal()}

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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield app, client, state
    await app.state.engine.dispose()


async def _confirmed_profile(app, client, state) -> tuple[str, str]:
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "erasure-confirmed-profile-create"},
    )
    assert created.status_code == 201, created.text
    requested = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "erasure-request"}
    )
    assert requested.status_code == 202, requested.text
    state["principal"] = principal("erasure-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{requested.json()['deletion_id']}/confirm",
        headers={"Idempotency-Key": "erasure-confirmed-profile-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text
    return created.json()["id"], requested.json()["deletion_id"]


async def test_terminal_lifecycle_cleanup_scrubs_expired_markers_but_retains_authorities(
    erasure_client,
) -> None:
    app, client, state = erasure_client
    _, deletion_id = await _confirmed_profile(app, client, state)
    terminal_at = datetime.now(UTC) - timedelta(days=31)
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.receipt_hmac is not None
        lifecycle.state = "fully_erased"
        lifecycle.backup_state = "verified"
        lifecycle.live_erased_at = terminal_at
        lifecycle.terminal_at = terminal_at
        session.add(
            AccountLifecycleTombstone(
                deletion_id=deletion_id,
                policy_version=lifecycle.policy_version,
                phase="fully_erased",
                result_digest=sha256(
                    f"connect.md:lifecycle:terminal:v1:{deletion_id}:{lifecycle.policy_version}:"
                    f"{terminal_at.isoformat()}".encode()
                ).hexdigest(),
                occurred_at=terminal_at,
            )
        )
        session.add(
            AccountLifecycleReceiptRateLimit(
                deletion_id=deletion_id,
                receipt_hmac=lifecycle.receipt_hmac,
                ip_hmac="b" * 64,
                window_started_at=terminal_at.replace(second=0, microsecond=0),
                request_count=1,
                updated_at=terminal_at,
            )
        )
        await session.commit()
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        FakeSearch(),  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="terminal-marker-cleanup",
    )
    await executor._reconcile(deletion_id, datetime.now(UTC))
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "fully_erased"
        assert lifecycle.confirmation_idempotency_hmac is None
        assert lifecycle.request_idempotency_hmac is None
        assert lifecycle.receipt_hmac is None
        assert lifecycle.receipt_recovery_idempotency_hmac is None
        assert await session.scalar(
            select(AccountAccessDeny.id).where(AccountAccessDeny.deletion_id == deletion_id)
        )
        assert (
            await session.scalar(
                select(AccountLifecycleTombstone).where(
                    AccountLifecycleTombstone.deletion_id == deletion_id
                )
            )
            is not None
        )
        assert (
            await session.scalar(
                select(AccountLifecycleReceiptRateLimit.id).where(
                    AccountLifecycleReceiptRateLimit.deletion_id == deletion_id
                )
            )
            is None
        )


async def test_terminal_lifecycle_cleanup_keeps_markers_when_live_mirror_is_corrupt(
    erasure_client,
) -> None:
    app, client, state = erasure_client
    _, deletion_id = await _confirmed_profile(app, client, state)
    terminal_at = datetime.now(UTC) - timedelta(days=31)
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        lifecycle.state = "fully_erased"
        lifecycle.backup_state = "verified"
        lifecycle.terminal_at = terminal_at
        session.add(
            AccountLifecycleTombstone(
                deletion_id=deletion_id,
                policy_version=lifecycle.policy_version,
                phase="fully_erased",
                result_digest=sha256(
                    f"connect.md:lifecycle:terminal:v1:{deletion_id}:{lifecycle.policy_version}:"
                    f"{terminal_at.isoformat()}".encode()
                ).hexdigest(),
                occurred_at=terminal_at,
            )
        )
        deny = await session.scalar(
            select(AccountAccessDeny).where(AccountAccessDeny.deletion_id == deletion_id)
        )
        assert deny is not None
        await session.delete(deny)
        await session.commit()
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        FakeSearch(),  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="terminal-marker-corruption",
    )
    await executor._reconcile(deletion_id, datetime.now(UTC))
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None
        assert lifecycle.confirmation_idempotency_hmac is not None
        assert lifecycle.receipt_hmac is not None


async def test_account_erasure_removes_final_taxonomy_membership_and_term_atomically(
    erasure_client,
) -> None:
    app, client, state = erasure_client
    await install_taxonomy_ready(app)
    created = await client.post(
        "/v1/profiles",
        json={"markdown": erasure_profile_v2_markdown()},
        headers={"Idempotency-Key": "erasure-taxonomy-profile-create"},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    async with app.state.session_factory() as session:
        memberships = (
            await session.scalars(
                select(PublicTaxonomyMembership).where(
                    PublicTaxonomyMembership.document_id == document_id
                )
            )
        ).all()
        assert memberships
        term_ids = {membership.term_id for membership in memberships}
        assert (await session.get(PublicTaxonomyDocumentSnapshot, document_id)) is not None

    requested = await client.post(
        "/v1/account-deletion-requests",
        headers={"Idempotency-Key": "erasure-taxonomy-request"},
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = principal("erasure-taxonomy-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "erasure-taxonomy-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text

    async with app.state.session_factory() as session:
        assert (
            await session.scalar(
                select(PublicTaxonomyMembership.id).where(
                    PublicTaxonomyMembership.document_id == document_id
                )
            )
            is None
        )
        assert await session.get(PublicTaxonomyDocumentSnapshot, document_id) is None
        assert (
            await session.scalar(
                select(PublicTaxonomyTerm.id).where(PublicTaxonomyTerm.id.in_(term_ids))
            )
            is None
        )
        manifest = await session.scalar(
            select(AccountBackupManifest).where(
                AccountBackupManifest.generation_id == "fixture-backup-generation"
            )
        )
        assert manifest is not None
        manifest.state = "crypto_destroyed"
        manifest.crypto_destroyed_proof_digest = "e" * 64
        manifest.crypto_destroyed_at = datetime.now(UTC)
        await session.commit()

    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        FakeSearch(),  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="taxonomy-erasure",
    )
    await executor.run_once(limit=50)
    async with app.state.session_factory() as session:
        assert await session.get(Document, document_id) is None
        assert (
            await session.scalar(
                select(PublicTaxonomyMembership.id).where(
                    PublicTaxonomyMembership.document_id == document_id
                )
            )
            is None
        )
        assert (
            await session.scalar(
                select(PublicTaxonomyTerm.id).where(PublicTaxonomyTerm.id.in_(term_ids))
            )
            is None
        )


async def test_local_erasure_dag_uses_fake_provider_and_reaches_opaque_terminal_state(
    erasure_client,
) -> None:
    app, client, state = erasure_client
    document_id, deletion_id = await _confirmed_profile(app, client, state)
    async with app.state.session_factory() as session:
        manifest = await session.scalar(
            select(AccountBackupManifest).where(
                AccountBackupManifest.generation_id == "fixture-backup-generation"
            )
        )
        assert manifest is not None
        manifest.state = "crypto_destroyed"
        manifest.crypto_destroyed_proof_digest = "e" * 64
        manifest.crypto_destroyed_at = datetime.now(UTC)
        # Simulate a projection worker that already consumed the create task
        # after erasure unindexing but before canonical deletion. The delete
        # transaction must produce a fresh durable absence tombstone.
        await session.execute(
            delete(SearchProjectionTask).where(SearchProjectionTask.document_id == document_id)
        )
        await session.commit()
    provider = FakeProvider()
    search = FakeSearch()
    search.live_documents.add(document_id)
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        search,  # type: ignore[arg-type]
        provider,
        app.state.settings,
        worker_id="test-erasure",
    )
    result = await executor.run_once(limit=50)
    assert result.completed >= 7
    assert provider.calls == [
        "session:erasure-owner:erasure-current-session",
        "user:erasure-owner",
    ]
    assert search.deleted == [document_id]
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "erasing"
        assert lifecycle.live_erased_at is None
        assert lifecycle.provider_subject_ciphertext is None
        assert lifecycle.provider_session_ciphertext is None
        assert await session.get(Document, document_id) is None
        assert await session.get(SearchProjectionTask, (document_id, 1)) is not None
        assert (await session.scalars(select(AccountErasureFileProof))).all()
        assert (await session.scalars(select(IdentifierReservation))).all()
        items = (await session.scalars(select(AccountErasureItem))).all()
        assert {item.state for item in items} == {"completed"}

    # Worst-case interleaving: a projection write claimed before the direct
    # unindex finishes afterward and reintroduces the public document. Terminal
    # lifecycle state is still prohibited until the durable missing-row task
    # deletes and attests remote absence.
    search.live_documents.add(document_id)
    await consume_erasure_projection_tombstone(app, search)
    assert document_id not in search.live_documents
    assert search.deleted == [document_id, document_id]

    await executor.run_once(limit=50)
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "fully_erased"
        assert lifecycle.live_erased_at is not None
        assert await session.get(SearchProjectionTask, (document_id, 1)) is None


async def test_counterparty_history_detaches_and_erasure_reaches_terminal_state(
    erasure_client,
) -> None:
    app, client, state = erasure_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "erasure-shared-history-profile"},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    async with app.state.session_factory() as session:
        subject_document = await session.get(Document, document_id)
        assert subject_document is not None
        subject_handle = subject_document.public_identifier
    now = datetime.now(UTC)
    later = now + timedelta(days=30)
    counterparty = "counterparty-owner"
    request_id = "72000000-0000-4000-8000-000000000001"
    connection_id = "72000000-0000-4000-8000-000000000002"
    conversation_id = "72000000-0000-4000-8000-000000000003"
    retained_message_id = "72000000-0000-4000-8000-000000000004"
    erased_message_id = "72000000-0000-4000-8000-000000000005"
    async with app.state.session_factory() as session:
        session.add_all(
            [
                ConnectionRequest(
                    id=request_id,
                    pair_owner_low=counterparty,
                    pair_owner_high="erasure-owner",
                    requester_owner_id=counterparty,
                    recipient_owner_id="erasure-owner",
                    requester_profile_handle="counterparty-profile",
                    recipient_profile_handle=subject_handle,
                    requested_messaging=True,
                    recipient_messaging_consent=True,
                    status="accepted",
                    requester_actor_id=counterparty,
                    requester_actor_method="clerk_jwt",
                    decision_actor_id="erasure-owner",
                    created_at=now,
                    updated_at=now,
                    decided_at=now,
                    retention_expires_at=later,
                ),
                Connection(
                    id=connection_id,
                    connection_request_id=request_id,
                    pair_owner_low=counterparty,
                    pair_owner_high="erasure-owner",
                    requester_owner_id=counterparty,
                    recipient_owner_id="erasure-owner",
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
                    pair_owner_high="erasure-owner",
                    status="active",
                    created_by_owner_id="erasure-owner",
                    created_at=now,
                    retention_expires_at=later,
                ),
                Message(
                    id=retained_message_id,
                    conversation_id=conversation_id,
                    sender_owner_id=counterparty,
                    sender_actor_id=counterparty,
                    sender_actor_method="clerk_jwt",
                    markdown=f"Counterparty history mentions erasure-owner and @{subject_handle}.",
                    content_sha256="a" * 64,
                    status="active",
                    created_at=now,
                    retention_expires_at=later,
                ),
                Message(
                    id=erased_message_id,
                    conversation_id=conversation_id,
                    sender_owner_id="erasure-owner",
                    sender_actor_id="erasure-owner",
                    sender_actor_method="clerk_jwt",
                    markdown="Subject-authored private content.",
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
        headers={"Idempotency-Key": "erasure-shared-history-request"},
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = principal("erasure-shared-history-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "erasure-shared-history-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text

    async with app.state.session_factory() as session:
        manifest = await session.scalar(
            select(AccountBackupManifest).where(
                AccountBackupManifest.generation_id == "fixture-backup-generation"
            )
        )
        assert manifest is not None
        manifest.state = "crypto_destroyed"
        manifest.crypto_destroyed_proof_digest = "e" * 64
        manifest.crypto_destroyed_at = datetime.now(UTC)
        await session.execute(
            delete(SearchProjectionTask).where(SearchProjectionTask.document_id == document_id)
        )
        await session.commit()

    provider = FakeProvider()
    search = FakeSearch()
    search.live_documents.add(document_id)
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        search,  # type: ignore[arg-type]
        provider,
        app.state.settings,
        worker_id="shared-history-erasure",
    )
    await executor.run_once(limit=100)
    await consume_erasure_projection_tombstone(app, search)
    await executor.run_once(limit=100)

    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        request_row = await session.get(ConnectionRequest, request_id)
        connection_row = await session.get(Connection, connection_id)
        conversation_row = await session.get(Conversation, conversation_id)
        retained_message = await session.get(Message, retained_message_id)
        assert lifecycle is not None and lifecycle.state == "fully_erased"
        assert provider.calls == [
            "session:erasure-owner:erasure-current-session",
            "user:erasure-owner",
        ]
        assert request_row is not None and connection_row is not None
        assert conversation_row is not None and retained_message is not None
        detached_rows = (request_row, connection_row, conversation_row, retained_message)
        assert all("erasure-owner" not in str(row.__dict__) for row in detached_rows)
        assert subject_handle not in str(request_row.__dict__)
        assert subject_handle not in str(connection_row.__dict__)
        assert subject_handle not in retained_message.markdown
        assert (
            retained_message.content_sha256
            == sha256(retained_message.markdown.encode("utf-8")).hexdigest()
        )
        assert await session.get(Message, erased_message_id) is None
        shared_items = (
            await session.scalars(
                select(AccountErasureItem).where(
                    AccountErasureItem.deletion_id == deletion_id,
                    AccountErasureItem.resource_type.in_(
                        ["connection_request", "connection", "conversation", "message"]
                    ),
                )
            )
        ).all()
        assert shared_items
        assert all(item.state == "completed" and item.hold_kind is None for item in shared_items)


async def test_applicant_erasure_removes_application_snapshot_before_its_row(
    erasure_client,
) -> None:
    app, client, state = erasure_client
    submitted_markdown = profile_markdown(visibility="public")
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": submitted_markdown},
        headers={"Idempotency-Key": "erasure-application-profile-create"},
    )
    assert profile.status_code == 201, profile.text
    now = datetime.now(UTC)
    application_id = "10000000-0000-4000-8000-000000000099"
    snapshot_path = app.state.store.application_snapshot_relative_path(application_id)
    snapshot_payload = submitted_markdown.encode("utf-8")
    snapshot_digest = app.state.store.write_immutable_bytes(snapshot_path, snapshot_payload)
    async with app.state.session_factory() as session:
        session.add(
            Application(
                id=application_id,
                job_id="job-owned-by-another-employer",
                applicant_owner_id="erasure-owner",
                applicant_actor_id="erasure-owner",
                applicant_actor_method="clerk_jwt",
                snapshot_document_id=profile.json()["id"],
                snapshot_document_kind="profile",
                snapshot_document_identifier="ada-lovelace",
                snapshot_document_version=1,
                snapshot_sha256=snapshot_digest,
                snapshot_size_bytes=len(snapshot_payload),
                snapshot_storage_path=snapshot_path,
                message="application body",
                status="submitted",
                confirmed_by_owner_id="erasure-owner",
                confirmed_at=now,
                retention_policy_version="application-retention-v1",
                retention_expires_at=now + timedelta(days=365),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    requested = await client.post(
        "/v1/account-deletion-requests",
        headers={"Idempotency-Key": "erasure-application-request"},
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = principal("erasure-application-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "erasure-application-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text

    async with app.state.session_factory() as session:
        planned = {
            (item.resource_type, item.resource_id, item.phase)
            for item in await session.scalars(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
            )
        }
    assert ("application", application_id, "delete_file") in planned
    assert ("application", application_id, "delete_row") in planned

    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        FakeSearch(),  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="application-erasure",
    )
    await executor.run_once(limit=50)
    assert not app.state.store._absolute(snapshot_path).exists()
    async with app.state.session_factory() as session:
        assert await session.get(Application, application_id) is None
        proof = await session.scalar(
            select(AccountErasureFileProof).where(
                AccountErasureFileProof.deletion_id == deletion_id,
                AccountErasureFileProof.resource_type == "application",
                AccountErasureFileProof.resource_id == application_id,
            )
        )
        assert proof is not None and proof.relative_path == snapshot_path


@pytest.mark.parametrize("integrity_case", ["tampered", "missing_digest", "missing_size"])
async def test_account_erasure_preserves_unverified_application_snapshot(
    erasure_client, integrity_case: str
) -> None:
    app, client, state = erasure_client
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": f"erasure-{integrity_case}-application-profile"},
    )
    assert profile.status_code == 201, profile.text
    now = datetime.now(UTC)
    application_id = "10000000-0000-4000-8000-000000000109"
    snapshot_path = app.state.store.application_snapshot_relative_path(application_id)
    snapshot_payload = b"# expected snapshot\n"
    snapshot_digest = app.state.store.write_immutable_bytes(snapshot_path, snapshot_payload)
    async with app.state.session_factory() as session:
        session.add(
            Application(
                id=application_id,
                job_id="job-owned-by-another-employer",
                applicant_owner_id="erasure-owner",
                applicant_actor_id="erasure-owner",
                applicant_actor_method="clerk_jwt",
                snapshot_document_id=profile.json()["id"],
                snapshot_document_kind="profile",
                snapshot_document_identifier="ada-lovelace",
                snapshot_document_version=1,
                snapshot_sha256="" if integrity_case == "missing_digest" else snapshot_digest,
                snapshot_size_bytes=(
                    None if integrity_case == "missing_size" else len(snapshot_payload)
                ),
                snapshot_storage_path=snapshot_path,
                message="application body",
                status="submitted",
                confirmed_by_owner_id="erasure-owner",
                confirmed_at=now,
                retention_policy_version="application-retention-v1",
                retention_expires_at=now + timedelta(days=365),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    requested = await client.post(
        "/v1/account-deletion-requests",
        headers={"Idempotency-Key": f"erasure-{integrity_case}-application-request"},
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = principal(f"erasure-{integrity_case}-application-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": f"erasure-{integrity_case}-application-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text

    retained_payload = snapshot_payload
    if integrity_case == "tampered":
        retained_payload = b"# altered snapshot\n"
        app.state.store._absolute(snapshot_path).write_bytes(retained_payload)
    await AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        FakeSearch(),  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id=f"{integrity_case}-application-erasure",
    ).run_once(limit=50)

    assert app.state.store._absolute(snapshot_path).read_bytes() == retained_payload
    async with app.state.session_factory() as session:
        application = await session.get(Application, application_id)
        file_item = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "application",
                AccountErasureItem.resource_id == application_id,
                AccountErasureItem.phase == "delete_file",
            )
        )
        proof = await session.scalar(
            select(AccountErasureFileProof).where(
                AccountErasureFileProof.deletion_id == deletion_id,
                AccountErasureFileProof.resource_type == "application",
                AccountErasureFileProof.resource_id == application_id,
            )
        )
    assert application is not None
    assert file_item is not None
    if integrity_case == "tampered":
        assert file_item.state == "queued"
        assert file_item.last_error_code == "erasure_dependency_unavailable"
    else:
        assert file_item.state == "dead_letter"
        assert file_item.last_error_code == "storage_metadata_invalid"
    assert proof is None


async def test_employer_erasure_retains_other_applicants_snapshot_for_normal_retention(
    erasure_client,
) -> None:
    app, client, state = erasure_client
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "erasure-employer-profile-create"},
    )
    assert profile.status_code == 201, profile.text
    now = datetime.now(UTC)
    application_id = "10000000-0000-4000-8000-000000000100"
    snapshot_path = app.state.store.application_snapshot_relative_path(application_id)
    snapshot_payload = b"# applicant snapshot\n"
    snapshot_digest = app.state.store.write_immutable_bytes(snapshot_path, snapshot_payload)
    organization = Organization(
        id="10000000-0000-4000-8000-000000000101",
        owner_id="erasure-owner",
        slug="erasure-employer",
        name="Erasure Employer",
        visibility="private",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    job = Job(
        id="10000000-0000-4000-8000-000000000102",
        organization_id=organization.id,
        slug="erasure-role",
        title="Erasure role",
        description="A retained applicant record.",
        status="closed",
        version=1,
        created_at=now,
        updated_at=now,
    )
    async with app.state.session_factory() as session:
        session.add_all(
            (
                organization,
                job,
                Application(
                    id=application_id,
                    job_id=job.id,
                    applicant_owner_id="other-applicant",
                    applicant_actor_id="other-applicant",
                    applicant_actor_method="clerk_jwt",
                    snapshot_document_id=profile.json()["id"],
                    snapshot_document_kind="profile",
                    snapshot_document_identifier="other-applicant-profile",
                    snapshot_document_version=1,
                    snapshot_sha256=snapshot_digest,
                    snapshot_size_bytes=len(snapshot_payload),
                    snapshot_storage_path=snapshot_path,
                    message="application body",
                    status="submitted",
                    confirmed_by_owner_id="other-applicant",
                    confirmed_at=now,
                    retention_policy_version="application-retention-v1",
                    retention_expires_at=now + timedelta(days=365),
                    created_at=now,
                    updated_at=now,
                ),
            )
        )
        await session.commit()

    requested = await client.post(
        "/v1/account-deletion-requests",
        headers={"Idempotency-Key": "erasure-employer-request"},
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = principal("erasure-employer-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "erasure-retention-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text

    async with app.state.session_factory() as session:
        application_items = (
            await session.scalars(
                select(AccountErasureItem).where(
                    AccountErasureItem.deletion_id == deletion_id,
                    AccountErasureItem.resource_type == "application",
                    AccountErasureItem.resource_id == application_id,
                )
            )
        ).all()
    assert {(item.phase, item.state) for item in application_items} == {("detach", "held")}
    assert app.state.store._absolute(snapshot_path).exists()


async def test_projection_dead_letter_fails_account_erasure_closed(erasure_client) -> None:
    app, client, state = erasure_client
    document_id, deletion_id = await _confirmed_profile(app, client, state)
    async with app.state.session_factory() as session:
        manifest = await session.scalar(
            select(AccountBackupManifest).where(
                AccountBackupManifest.generation_id == "fixture-backup-generation"
            )
        )
        assert manifest is not None
        manifest.state = "crypto_destroyed"
        manifest.crypto_destroyed_proof_digest = "e" * 64
        manifest.crypto_destroyed_at = datetime.now(UTC)
        await session.commit()
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        FakeSearch(),  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="test-erasure",
    )
    await executor.run_once(limit=50)
    async with app.state.session_factory() as session:
        task = await session.get(SearchProjectionTask, (document_id, 1))
        assert task is not None
        task.state = "dead_letter"
        task.dead_lettered_at = datetime.now(UTC)
        await session.commit()

    await executor.run_once(limit=50)
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "failed"
        assert lifecycle.safe_failure_code == "search_projection_dead_letter"
        assert lifecycle.live_erased_at is None


async def test_unconfigured_search_cannot_attest_or_complete_account_erasure(
    erasure_client,
) -> None:
    app, client, state = erasure_client
    document_id, deletion_id = await _confirmed_profile(app, client, state)
    async with app.state.session_factory() as session:
        manifest = await session.scalar(
            select(AccountBackupManifest).where(
                AccountBackupManifest.generation_id == "fixture-backup-generation"
            )
        )
        assert manifest is not None
        manifest.state = "crypto_destroyed"
        manifest.crypto_destroyed_proof_digest = "e" * 64
        manifest.crypto_destroyed_at = datetime.now(UTC)
        await session.commit()
    search = FakeSearch(configured=False)
    search.live_documents.add(document_id)
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        search,  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="test-erasure",
    )

    await executor.run_once(limit=50)
    assert document_id in search.live_documents
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        unindex = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "document",
                AccountErasureItem.phase == "unindex",
            )
        )
        assert lifecycle is not None and lifecycle.state == "erasing"
        assert lifecycle.live_erased_at is None
        assert unindex is not None and unindex.state == "queued"
        assert unindex.last_error_code == "erasure_dependency_unavailable"
        assert await session.get(Document, document_id) is not None
        assert await session.get(SearchProjectionTask, (document_id, 1)) is not None

    search.configured = True
    await executor.run_once(limit=50, now=datetime.now(UTC) + timedelta(seconds=3))
    assert document_id not in search.live_documents
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "erasing"
        assert lifecycle.live_erased_at is None
        assert await session.get(Document, document_id) is None
        assert await session.get(SearchProjectionTask, (document_id, 1)) is not None

    # Even if stale projection bytes appear after direct unindex, the durable
    # missing-document task is the final proof boundary.
    search.live_documents.add(document_id)
    await consume_erasure_projection_tombstone(app, search)
    assert document_id not in search.live_documents
    await executor.run_once(limit=50)
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "fully_erased"
        assert lifecycle.live_erased_at is not None


async def test_unexpected_search_error_retries_dead_letters_and_does_not_starve_queue(
    erasure_client, capsys
) -> None:
    app, client, state = erasure_client
    document_id, deletion_id = await _confirmed_profile(app, client, state)
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        items = (
            await session.scalars(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
            )
        ).all()
        for item in items:
            item.available_at = now + timedelta(hours=1)
        unindex = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "document",
                AccountErasureItem.resource_id == document_id,
                AccountErasureItem.phase == "unindex",
            )
        )
        provider_session = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "provider_session",
                AccountErasureItem.phase == "provider",
            )
        )
        assert unindex is not None and provider_session is not None
        unindex.available_at = now
        provider_session.available_at = now + timedelta(seconds=1)
        await session.commit()

    sentinel = "unexpected-search-sentinel"
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        UnexpectedFailureSearch(sentinel),  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="unexpected-search-failure",
    )
    first = await executor.run_once(limit=2, now=now + timedelta(seconds=1))
    assert first.claimed == 2
    assert first.completed == 1
    assert first.retried == 1
    assert first.dead_lettered == 0
    assert sentinel not in repr(first)

    async with app.state.session_factory() as session:
        unindex = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "document",
                AccountErasureItem.resource_id == document_id,
                AccountErasureItem.phase == "unindex",
            )
        )
        provider_session = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "provider_session",
                AccountErasureItem.phase == "provider",
            )
        )
        assert unindex is not None
        assert unindex.state == "queued"
        assert unindex.attempts == 1
        assert unindex.last_error_code == "erasure_execution_failed"
        assert provider_session is not None and provider_session.state == "completed"

    second = await executor.run_once(limit=1, now=now + timedelta(seconds=4))
    assert second.retried == 1
    third = await executor.run_once(limit=1, now=now + timedelta(seconds=9))
    assert third.dead_lettered == 1

    async with app.state.session_factory() as session:
        unindex = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "document",
                AccountErasureItem.resource_id == document_id,
                AccountErasureItem.phase == "unindex",
            )
        )
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert unindex is not None and unindex.state == "dead_letter"
        assert unindex.last_error_code == "erasure_execution_failed"
        assert lifecycle is not None and lifecycle.safe_failure_code == "erasure_dead_letter"
        persisted_codes = (
            await session.scalars(
                select(AccountErasureItem.last_error_code).where(
                    AccountErasureItem.deletion_id == deletion_id
                )
            )
        ).all()
        assert all(sentinel not in (code or "") for code in persisted_codes)
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err


async def test_unexpected_provider_error_uses_sanitized_retry(erasure_client, capsys) -> None:
    app, client, state = erasure_client
    _, deletion_id = await _confirmed_profile(app, client, state)
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        items = (
            await session.scalars(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
            )
        ).all()
        for item in items:
            item.available_at = now + timedelta(hours=1)
        provider_session = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "provider_session",
                AccountErasureItem.phase == "provider",
            )
        )
        assert provider_session is not None
        provider_session.available_at = now
        await session.commit()

    sentinel = "unexpected-provider-sentinel"
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        FakeSearch(),  # type: ignore[arg-type]
        UnexpectedFailureProvider(sentinel),
        app.state.settings,
        worker_id="unexpected-provider-failure",
    )
    result = await executor.run_once(limit=1, now=now)
    assert result.claimed == 1
    assert result.retried == 1
    assert result.dead_lettered == 0
    assert sentinel not in repr(result)

    async with app.state.session_factory() as session:
        provider_session = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "provider_session",
                AccountErasureItem.phase == "provider",
            )
        )
        assert provider_session is not None
        assert provider_session.state == "queued"
        assert provider_session.last_error_code == "erasure_execution_failed"
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err


async def test_lifecycle_worker_heartbeat_is_content_free_and_dependency_gated(
    erasure_client, tmp_path
) -> None:
    app, client, state = erasure_client
    await _confirmed_profile(app, client, state)
    provider = FakeProvider()
    search = FakeSearch()
    heartbeat = tmp_path / "lifecycle-health.json"
    settings = app.state.settings.model_copy(update={"account_lifecycle_heartbeat_path": heartbeat})

    payload = await _refresh_health_heartbeat(
        app.state.session_factory,
        app.state.deletion_journal,
        provider,
        search,
        settings,
    )
    assert payload["state"] == "healthy"
    assert payload["database_ready"] is True
    assert payload["provider_ready"] is True
    assert payload["search_ready"] is True
    assert payload["backlog_count"] >= payload["eligible_count"] >= 1
    assert payload["dead_letter_count"] == 0
    assert payload["failed_lifecycle_count"] == 0
    assert set(payload) == {
        "state",
        "checked_at",
        "database_ready",
        "deletion_journal_ready",
        "provider_ready",
        "search_ready",
        "deletion_commitment_count",
        "backlog_count",
        "eligible_count",
        "dead_letter_count",
        "failed_lifecycle_count",
        "oldest_eligible_age_seconds",
    }
    if os.name == "posix":
        assert stat.S_IMODE(heartbeat.stat().st_mode) == 0o600
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["state"] == "healthy"

    async with app.state.session_factory() as session:
        item = await session.scalar(
            select(AccountErasureItem).where(AccountErasureItem.state == "queued").limit(1)
        )
        assert item is not None
        item.state = "dead_letter"
        item.available_at = None
        await session.commit()
    degraded = await _refresh_health_heartbeat(
        app.state.session_factory,
        app.state.deletion_journal,
        provider,
        search,
        settings,
    )
    assert degraded["state"] == "degraded"
    assert degraded["dead_letter_count"] == 1

    provider.ready = False
    with pytest.raises(RuntimeError, match="provider readiness"):
        await _refresh_health_heartbeat(
            app.state.session_factory,
            app.state.deletion_journal,
            provider,
            search,
            settings,
        )
    assert not heartbeat.exists()
    provider.ready = True
    search.ready = False
    with pytest.raises(RuntimeError, match="search readiness"):
        await _refresh_health_heartbeat(
            app.state.session_factory,
            app.state.deletion_journal,
            provider,
            search,
            settings,
        )
    assert not heartbeat.exists()


async def test_retention_hold_requires_explicit_release_and_policy_holds_never_auto_release(
    erasure_client, monkeypatch
) -> None:
    app, client, state = erasure_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "erasure-stale-profile-create"},
    )
    assert created.status_code == 201
    document_id = created.json()["id"]
    now = datetime.now(UTC)
    monkeypatch.setattr(cli, "get_settings", lambda: app.state.settings)
    assert (
        await cli.create_retention_hold(
            Namespace(
                resource_type="document",
                resource_id=document_id,
                purpose="preservation",
                authority="legal",
                expires_at=(now + timedelta(days=1)).isoformat(),
                review_at=(now + timedelta(hours=1)).isoformat(),
            )
        )
        == 0
    )
    async with app.state.session_factory() as session:
        hold = await session.scalar(
            select(RetentionHold).where(
                RetentionHold.resource_type == "document",
                RetentionHold.resource_id == document_id,
            )
        )
        assert hold is not None
        hold_id = hold.id
    requested = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "erasure-hold-request"}
    )
    assert requested.status_code == 202, requested.text
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = principal("erasure-hold-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "erasure-search-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text
    async with app.state.session_factory() as session:
        manifest = await session.scalar(
            select(AccountBackupManifest).where(
                AccountBackupManifest.generation_id == "fixture-backup-generation"
            )
        )
        assert manifest is not None
        manifest.state = "crypto_destroyed"
        manifest.crypto_destroyed_proof_digest = "e" * 64
        manifest.crypto_destroyed_at = datetime.now(UTC)
        await session.commit()
    provider = FakeProvider()
    search = FakeSearch()
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        search,  # type: ignore[arg-type]
        provider,
        app.state.settings,
        worker_id="test-erasure",
    )
    await executor.run_once(limit=50)
    assert search.deleted == [document_id]
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        assert lifecycle is not None and lifecycle.state == "held"
        item = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "document",
                AccountErasureItem.phase == "delete_row",
            )
        )
        assert item is not None and item.state == "held" and item.hold_kind == "retention"
        version = await session.scalar(
            select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        )
        assert version is not None
        assert app.state.store._absolute(version.storage_path).is_file()
        assert await session.get(Document, document_id) is not None
        persisted_hold = await session.get(RetentionHold, hold_id)
        assert persisted_hold is not None
        persisted_hold.released_at = datetime.now(UTC)
        persisted_hold.released_by_authority = "legal"
        await session.commit()
    await executor.run_once(limit=50)
    await consume_erasure_projection_tombstone(app, search)
    await executor.run_once(limit=50)
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        states = (
            await session.execute(
                select(
                    AccountErasureItem.resource_type,
                    AccountErasureItem.phase,
                    AccountErasureItem.state,
                    AccountErasureItem.last_error_code,
                ).where(AccountErasureItem.deletion_id == deletion_id)
            )
        ).all()
        assert lifecycle is not None and lifecycle.state == "fully_erased", states
        assert await session.get(Document, document_id) is None


async def test_policy_held_item_never_auto_requeues(erasure_client) -> None:
    app, client, state = erasure_client
    document_id, deletion_id = await _confirmed_profile(app, client, state)
    async with app.state.session_factory() as session:
        item = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "document",
                AccountErasureItem.resource_id == document_id,
                AccountErasureItem.phase == "delete_row",
            )
        )
        assert item is not None
        item.state = "held"
        item.disposition = "hold"
        item.hold_kind = "policy"
        item.available_at = None
        await session.commit()
    search = FakeSearch()
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        search,  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="test-erasure",
    )
    await executor.run_once(limit=50)
    async with app.state.session_factory() as session:
        item = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "document_version",
                AccountErasureItem.phase == "delete_file",
            )
        )
        assert item is not None and item.state == "held" and item.hold_kind == "policy"


async def test_cli_hold_rejects_a_resource_with_an_in_progress_erasure_lease(
    erasure_client, monkeypatch
) -> None:
    app, client, state = erasure_client
    document_id, deletion_id = await _confirmed_profile(app, client, state)
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        item = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.resource_type == "document",
                AccountErasureItem.resource_id == document_id,
                AccountErasureItem.phase == "delete_row",
            )
        )
        assert item is not None
        item.state = "leased"
        item.claim_token = "active-lease"
        item.claimed_by = "test-erasure"
        item.lease_expires_at = now + timedelta(minutes=1)
        await session.commit()
    monkeypatch.setattr(cli, "get_settings", lambda: app.state.settings)
    assert (
        await cli.create_retention_hold(
            Namespace(
                resource_type="document",
                resource_id=document_id,
                purpose="preservation",
                authority="legal",
                expires_at=(now + timedelta(days=1)).isoformat(),
                review_at=(now + timedelta(hours=1)).isoformat(),
            )
        )
        == 1
    )
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(
                select(RetentionHold).where(
                    RetentionHold.resource_type == "document",
                    RetentionHold.resource_id == document_id,
                )
            )
            is None
        )


async def test_policy_held_post_keeps_its_immutable_version_rows(erasure_client) -> None:
    app, client, state = erasure_client
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "erasure-held-post-profile-create"},
    )
    assert profile.status_code == 201
    post = await client.post(
        "/v1/posts",
        headers={"Idempotency-Key": "held-post"},
        json={
            "markdown": """---
schema: connect.md/post
schema_version: 1
title: Held post
topics: [retention]
visibility: public
---
# Held post

Canonical post evidence.
"""
        },
    )
    assert post.status_code == 201, post.text
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        session.add(
            ModerationCase(
                post_id=post.json()["id"],
                subject_owner_id="erasure-owner",
                status="open",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    requested = await client.post(
        "/v1/account-deletion-requests", headers={"Idempotency-Key": "held-post-request"}
    )
    assert requested.status_code == 202
    deletion_id = requested.json()["deletion_id"]
    state["principal"] = principal("held-post-confirm")
    confirmed = await client.post(
        f"/v1/account-deletion-requests/{deletion_id}/confirm",
        headers={"Idempotency-Key": "erasure-held-post-confirm"},
    )
    assert confirmed.status_code == 202, confirmed.text
    async with app.state.session_factory() as session:
        post_row = await session.get(Post, post.json()["id"])
        assert post_row is not None
        version = await session.scalar(
            select(PostVersion).where(PostVersion.post_id == post_row.id)
        )
        assert version is not None
        items = (
            await session.scalars(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
            )
        ).all()
        states = {
            (item.resource_type, item.resource_id, item.phase): (item.state, item.hold_kind)
            for item in items
        }
        assert states[("post", post_row.id, "delete_row")] == ("held", "policy")
        assert states[("post_version", version.id, "delete_file")] == ("held", "policy")
        assert states[("post_version", version.id, "delete_row")] == ("held", "policy")
        assert states[("post", post_row.id, "unindex")][0] == "queued"


async def test_backup_obligation_stays_pending_without_expiry_proof(erasure_client) -> None:
    app, client, state = erasure_client
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        session.add(
            AccountBackupManifest(
                generation_id="backup-generation",
                created_at=now,
                expires_at=now + timedelta(days=1),
                state="active",
                db_manifest_digest="a" * 64,
                markdown_manifest_digest="b" * 64,
            )
        )
    await session.commit()
    _, deletion_id = await _confirmed_profile(app, client, state)
    search = FakeSearch()
    executor = AccountErasureExecutor(
        app.state.session_factory,
        app.state.store,
        search,  # type: ignore[arg-type]
        FakeProvider(),
        app.state.settings,
        worker_id="test-erasure",
    )
    await executor.run_once(limit=50, now=datetime.now(UTC))
    await consume_erasure_projection_tombstone(app, search)
    await executor.run_once(limit=50, now=datetime.now(UTC))
    async with app.state.session_factory() as session:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        backup = await session.scalar(
            select(AccountErasureItem).where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.phase == "backup",
            )
        )
        assert lifecycle is not None and lifecycle.state == "backup_expiry_pending"
        assert backup is not None and backup.state == "queued" and backup.attempts == 0


def test_delete_exact_rejects_path_escape_and_symlink(tmp_path) -> None:
    store = VersionStore(tmp_path / "store")
    path = store.relative_path("profile", "00000000-0000-0000-0000-000000000001", 1)
    store.write_immutable(path, "# canonical\n")
    with pytest.raises(StorageIntegrityError):
        store.delete_exact("profiles/../outside.md")
    target = store._absolute(path)
    target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        target.symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("symlink creation is not permitted for this Windows test account")
        raise
    with pytest.raises(StorageIntegrityError):
        store.delete_exact(path)
    assert outside.read_text(encoding="utf-8") == "outside"
