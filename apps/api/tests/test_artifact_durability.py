from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import RollbackFileCleanup, _compensate_rollback_files
from app.main import create_app
from app.models import (
    IdempotencyRecord,
    OrganizationVerification,
    OrganizationVerificationEvidence,
)
from app.services.artifact_durability import (
    CANONICAL_DOCUMENT_CREATE_TARGET_IDS,
    PROFESSIONAL_POST_CREATE_TARGET_ID,
    ArtifactDurabilityUnavailable,
    ArtifactReconciler,
    derive_artifact_intent_uuid,
    parse_signed_descriptor,
    stage_artifact,
)
from app.services.storage import StorageIntegrityError, VersionStore

PEPPER = "test-only-pepper-is-long-enough"


def test_artifact_intent_fixed_vectors_and_length_framing() -> None:
    assert (
        derive_artifact_intent_uuid(
            PEPPER,
            flow="application_snapshot",
            owner_id="user_test",
            target_id="30000000-0000-4000-8000-000000000003",
            idempotency_key="application-create-0001",
        )
        == "6c7c0f62-880a-4d65-9980-38035bd6d430"
    )
    assert (
        derive_artifact_intent_uuid(
            PEPPER,
            flow="organization_verification_evidence",
            owner_id="owner:acme",
            target_id="40000000-0000-4000-8000-000000000001",
            idempotency_key="verification-submit-0001",
        )
        == "0cfee9f8-043b-438d-b4ac-f82176f8af51"
    )
    first = derive_artifact_intent_uuid(
        PEPPER,
        flow="application_snapshot",
        owner_id="a:b",
        target_id="30000000-0000-4000-8000-000000000003",
        idempotency_key="c",
    )
    second = derive_artifact_intent_uuid(
        PEPPER,
        flow="application_snapshot",
        owner_id="a",
        target_id="30000000-0000-4000-8000-000000000003",
        idempotency_key="b:c",
    )
    assert first != second
    assert (
        derive_artifact_intent_uuid(
            PEPPER,
            flow="canonical_document_version",
            owner_id="owner",
            target_id=CANONICAL_DOCUMENT_CREATE_TARGET_IDS["profile"],
            idempotency_key="profile-create-0001",
        )
        == "cca7b520-c204-4fae-af33-f8027d9d213e"
    )
    assert (
        derive_artifact_intent_uuid(
            PEPPER,
            flow="professional_post",
            owner_id="owner",
            target_id=PROFESSIONAL_POST_CREATE_TARGET_ID,
            idempotency_key="post-create-0001",
        )
        == "1c869379-3bc7-481f-ae61-5163d19ac5e9"
    )


def test_same_document_intent_reuses_one_exact_staged_artifact(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    target_id = CANONICAL_DOCUMENT_CREATE_TARGET_IDS["profile"]
    intent_id = derive_artifact_intent_uuid(
        PEPPER,
        flow="canonical_document_version",
        owner_id="owner",
        target_id=target_id,
        idempotency_key="stable-profile-intent",
    )
    canonical_path = f"profiles/{intent_id}/versions/000001.md"
    first = stage_artifact(
        store,
        PEPPER,
        flow="canonical_document_version",
        owner_id="owner",
        target_id=target_id,
        idempotency_key="stable-profile-intent",
        request_hash="a" * 64,
        canonical_path=canonical_path,
        payload=b"first server timestamp\n",
        max_size_bytes=131_072,
        resource_id=intent_id,
    )
    retried = stage_artifact(
        store,
        PEPPER,
        flow="canonical_document_version",
        owner_id="owner",
        target_id=target_id,
        idempotency_key="stable-profile-intent",
        request_hash="a" * 64,
        canonical_path=canonical_path,
        payload=b"later server timestamp\n",
        max_size_bytes=131_072,
        resource_id=intent_id,
    )
    assert retried == first
    assert (tmp_path / canonical_path).read_bytes() == b"first server timestamp\n"
    scan = store.scan_staged_artifacts()
    assert scan.descriptors == (first.staged_descriptor_path,)
    assert scan.incomplete_payloads == ()
    with pytest.raises(ArtifactDurabilityUnavailable, match="staging is unavailable"):
        stage_artifact(
            store,
            PEPPER,
            flow="canonical_document_version",
            owner_id="owner",
            target_id=target_id,
            idempotency_key="stable-profile-intent",
            request_hash="b" * 64,
            canonical_path=canonical_path,
            payload=b"collision\n",
            max_size_bytes=131_072,
            resource_id=intent_id,
        )
    assert (tmp_path / canonical_path).read_bytes() == b"first server timestamp\n"
    assert store.scan_staged_artifacts().descriptors == (first.staged_descriptor_path,)


def _staged(store: VersionStore):
    intent_id = derive_artifact_intent_uuid(
        PEPPER,
        flow="application_snapshot",
        owner_id="owner",
        target_id="30000000-0000-4000-8000-000000000003",
        idempotency_key="fixture-key",
    )
    return stage_artifact(
        store,
        PEPPER,
        flow="application_snapshot",
        owner_id="owner",
        target_id="30000000-0000-4000-8000-000000000003",
        idempotency_key="fixture-key",
        request_hash="a" * 64,
        canonical_path=f"applications/{intent_id}/snapshot.md",
        payload=b"# exact\n",
        max_size_bytes=131_072,
    )


def test_signed_stage_tamper_and_unknown_entries_are_preserved(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    descriptor = _staged(store)
    descriptor_target = tmp_path / descriptor.staged_descriptor_path
    raw = descriptor_target.read_bytes()
    descriptor_target.write_bytes(raw.replace(b'"request_hash":"', b'"request_hash":"f', 1))
    with pytest.raises(Exception, match="descriptor"):
        parse_signed_descriptor(
            store.read_staged_descriptor(descriptor.staged_descriptor_path),
            PEPPER,
            expected_descriptor_path=descriptor.staged_descriptor_path,
        )
    unknown = tmp_path / ".connectmd-artifact-staging" / "v1" / "unknown"
    unknown.mkdir()
    (unknown / "do-not-delete").write_bytes(b"private")
    scan = store.scan_staged_artifacts()
    assert scan.invalid_entry is True
    assert (unknown / "do-not-delete").read_bytes() == b"private"
    assert (tmp_path / descriptor.canonical_path).read_bytes() == b"# exact\n"


async def test_invalid_scan_never_reconciles_other_valid_entries(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    descriptor = _staged(store)
    invalid = tmp_path / ".connectmd-artifact-staging" / "v1" / "invalid"
    invalid.mkdir()
    (invalid / "unknown").write_bytes(b"private")
    calls = 0

    async def absent(_descriptor):
        nonlocal calls
        calls += 1
        return "absent"

    reconciler = ArtifactReconciler(store, PEPPER, absent, enabled=True)
    await reconciler.run_once()
    assert calls == 0
    assert reconciler.status == "unavailable"
    assert (tmp_path / descriptor.canonical_path).read_bytes() == b"# exact\n"
    assert (tmp_path / descriptor.staged_payload_path).exists()
    assert (tmp_path / descriptor.staged_descriptor_path).exists()


async def test_pending_shaped_unknown_stage_is_preserved_and_blocks_readiness(api_client) -> None:
    app, client = api_client
    intent_id = str(uuid4())
    pending_name = f".unknown.pending-{1:019d}-{uuid4()}"
    unknown = app.state.store.root / ".connectmd-artifact-staging" / "v1" / intent_id / pending_name
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(b"unverified-private-stage")

    # Reinitialization performs generic stale-pending cleanup.  The artifact
    # namespace remains exclusively owned by its signed reconciler.
    rebuilt = VersionStore(app.state.store.root)
    assert unknown.read_bytes() == b"unverified-private-stage"
    assert rebuilt.scan_staged_artifacts().invalid_entry is True

    await app.state.artifact_reconciler.run_once()
    assert app.state.artifact_reconciler.status == "unavailable"
    assert unknown.read_bytes() == b"unverified-private-stage"
    readiness = await client.get("/readyz")
    assert readiness.status_code == 503
    assert readiness.json()["storage"] == "reconciliation_unavailable"


async def test_absent_authority_deletes_only_exact_bytes_and_retires_stage(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    descriptor = _staged(store)

    async def absent(_descriptor):
        return "absent"

    reconciler = ArtifactReconciler(store, PEPPER, absent, enabled=True)
    assert await reconciler.reconcile_descriptor(descriptor, respect_grace=False) == "absent"
    assert not (tmp_path / descriptor.canonical_path).exists()
    assert not (tmp_path / descriptor.staged_payload_path).exists()
    assert not (tmp_path / descriptor.staged_descriptor_path).exists()


async def test_uncertain_or_cancelled_authority_preserves_all_bytes(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    descriptor = _staged(store)

    async def uncertain(_descriptor):
        return "uncertain"

    reconciler = ArtifactReconciler(store, PEPPER, uncertain, enabled=True)
    assert await reconciler.reconcile_descriptor(descriptor, respect_grace=False) == "uncertain"
    assert reconciler.status == "unavailable"
    assert (tmp_path / descriptor.canonical_path).read_bytes() == b"# exact\n"
    assert (tmp_path / descriptor.staged_payload_path).exists()
    assert (tmp_path / descriptor.staged_descriptor_path).exists()


async def test_cancellation_preserves_canonical_and_staging_bytes(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    descriptor = _staged(store)

    async def cancelled(_descriptor):
        raise asyncio.CancelledError

    reconciler = ArtifactReconciler(store, PEPPER, cancelled, enabled=True)
    with pytest.raises(asyncio.CancelledError):
        await reconciler.reconcile_descriptor(descriptor, respect_grace=False)
    assert (tmp_path / descriptor.canonical_path).read_bytes() == b"# exact\n"
    assert (tmp_path / descriptor.staged_payload_path).exists()
    assert (tmp_path / descriptor.staged_descriptor_path).exists()


def test_exact_rollback_registry_rejects_legacy_strings_and_mismatch(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    path = "applications/fixture/snapshot.md"
    digest = store.write_immutable_bytes(path, b"exact")
    session = SimpleNamespace(info={"connectmd_rollback_file_cleanup": {path}})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(store=store)))
    _compensate_rollback_files(request, session)
    assert (tmp_path / path).read_bytes() == b"exact"

    session.info["connectmd_rollback_file_cleanup"] = {RollbackFileCleanup(path, "0" * 64, 5, 10)}
    _compensate_rollback_files(request, session)
    assert (tmp_path / path).read_bytes() == b"exact"

    session.info["connectmd_rollback_file_cleanup"] = {RollbackFileCleanup(path, digest, 5, 10)}
    _compensate_rollback_files(request, session)
    assert not (tmp_path / path).exists()


def test_delete_verified_exact_never_follows_a_symlink(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "store")
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    link = store.root / "applications"
    store.root.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(StorageIntegrityError):
        store.delete_verified_exact(
            "applications/outside",
            "0" * 64,
            expected_size_bytes=7,
            max_size_bytes=10,
        )
    assert outside.read_bytes() == b"private"


def test_grace_is_based_on_signed_creation_time_not_mtime(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    descriptor = _staged(store)
    Path(tmp_path / descriptor.staged_descriptor_path).touch()
    parsed = parse_signed_descriptor(
        store.read_staged_descriptor(descriptor.staged_descriptor_path),
        PEPPER,
        expected_descriptor_path=descriptor.staged_descriptor_path,
    )
    assert parsed.created_ns == descriptor.created_ns


async def test_grace_aged_incomplete_payload_is_deleted_only_after_absence_proof(
    tmp_path: Path,
) -> None:
    store = VersionStore(tmp_path)
    intent_id = str(uuid4())
    nonce = str(uuid4())
    created_ns = 1
    staged = store.stage_artifact(
        intent_id,
        b"private",
        b"{}",
        created_ns=created_ns,
        nonce=nonce,
    )
    (tmp_path / staged.descriptor_path).unlink()
    calls: list[tuple[str, str]] = []

    async def classify(_descriptor):
        return "uncertain"

    async def absent(locked_intent_id: str, payload_path: str):
        calls.append((locked_intent_id, payload_path))
        return "absent"

    reconciler = ArtifactReconciler(
        store, PEPPER, classify, enabled=True, classify_incomplete=absent
    )
    await reconciler.run_once()
    assert calls == [(intent_id, staged.payload_path)]
    assert not (tmp_path / staged.payload_path).exists()
    assert reconciler.status == "ready"


async def test_fresh_incomplete_payload_is_preserved_without_authority_query(
    tmp_path: Path,
) -> None:
    store = VersionStore(tmp_path)
    intent_id = str(uuid4())
    staged = store.stage_artifact(
        intent_id,
        b"private",
        b"{}",
        created_ns=time.time_ns(),
        nonce=str(uuid4()),
    )
    (tmp_path / staged.descriptor_path).unlink()

    async def classify(_descriptor):
        return "uncertain"

    async def forbidden(_intent_id: str, _payload_path: str):
        raise AssertionError("fresh stage must not query disposal authority")

    reconciler = ArtifactReconciler(
        store, PEPPER, classify, enabled=True, classify_incomplete=forbidden
    )
    await reconciler.run_once()
    assert (tmp_path / staged.payload_path).read_bytes() == b"private"
    assert reconciler.status == "ready"


async def test_precommit_verification_failure_leaves_no_file_or_graph(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/organizations",
        json={"slug": "precommit-artifact", "name": "Precommit", "visibility": "private"},
        headers={"Idempotency-Key": "precommit-organization"},
    )
    assert created.status_code == 201, created.text
    original_flush = AsyncSession.flush

    async def fail_registered_flush(session: AsyncSession, objects=None) -> None:
        if session.info.get("connectmd_rollback_file_cleanup"):
            raise RuntimeError("precommit failure")
        await original_flush(session, objects)

    monkeypatch.setattr(AsyncSession, "flush", fail_registered_flush)
    failed = await client.post(
        "/v1/organizations/precommit-artifact/verification-submissions",
        json={
            "evidence_kind": "other",
            "metadata": {},
            "artifact_content_type": "text/plain",
            "artifact_base64": "cHJpdmF0ZQ==",
        },
        headers={"Idempotency-Key": "precommit-verification"},
    )
    monkeypatch.setattr(AsyncSession, "flush", original_flush)
    assert failed.status_code == 503
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(OrganizationVerification))).all() == []
        assert (await session.scalars(select(OrganizationVerificationEvidence))).all() == []
        assert (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "precommit-verification"
                )
            )
        ).all() == []
    assert not list((app.state.store.root / "verification-evidence").rglob("*.bin"))
    stage_root = app.state.store.root / ".connectmd-artifact-staging" / "v1"
    assert not list(stage_root.rglob("*.bin"))
    assert not list(stage_root.rglob("*.json"))


async def test_reconciler_enablement_and_readiness_follow_attempted_scan(
    api_client, tmp_path: Path, monkeypatch
) -> None:
    app, client = api_client
    reconciler = app.state.artifact_reconciler
    assert reconciler.enabled is True
    assert reconciler.status == "not_attempted"
    await reconciler.run_once()
    assert reconciler.status == "ready"
    invalid = app.state.store.root / ".connectmd-artifact-staging" / "v1" / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "unknown").write_bytes(b"private")
    await reconciler.run_once()
    assert reconciler.status == "unavailable"
    readiness = await client.get("/readyz")
    assert readiness.status_code == 503
    assert readiness.json()["storage"] == "reconciliation_unavailable"

    monkeypatch.delenv("CONNECTMD_DATABASE_URL", raising=False)
    settings = Settings(
        storage_path=tmp_path / "local-storage",
        api_key_pepper=PEPPER,
    )
    local_app = create_app(settings)
    assert local_app.state.artifact_reconciler.enabled is False
    assert local_app.state.artifact_reconciler.status == "disabled"
    await local_app.state.engine.dispose()


async def test_unexpected_reconciler_failure_fails_closed_and_retries(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    reconciler = app.state.artifact_reconciler
    calls = 0

    def scan(*, limit: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic scan failure")
        raise asyncio.CancelledError

    monkeypatch.setattr(app.state.store, "scan_staged_artifacts", scan)
    monkeypatch.setattr("app.services.artifact_durability.ARTIFACT_RECONCILE_INTERVAL_SECONDS", 0)

    task = asyncio.create_task(reconciler.run_forever())
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == 2
    assert reconciler.status == "unavailable"
    readiness = await client.get("/readyz")
    assert readiness.status_code == 503
    assert readiness.json()["storage"] == "reconciliation_unavailable"
