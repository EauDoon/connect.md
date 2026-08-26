from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import ChangeEvent, Document, DocumentVersion, IdempotencyRecord
from app.services import storage as storage_module
from app.services.documents import DocumentService
from app.services.storage import StorageIntegrityError, VersionStore

from .helpers import profile_markdown


def test_immutable_write_reconciles_identical_unledgered_file(tmp_path) -> None:
    store = VersionStore(tmp_path)
    relative = store.relative_path("profile", "document-id", 2)
    digest = store.write_immutable(relative, "first\n")

    assert store.write_immutable(relative, "first\n") == digest
    assert store.read_verified(relative, digest) == "first\n"


def test_immutable_write_rejects_different_bytes_without_hidden_orphan(tmp_path) -> None:
    store = VersionStore(tmp_path)
    relative = store.relative_path("profile", "document-id", 2)
    original_digest = store.write_immutable(relative, "interrupted-attempt\n")

    with pytest.raises(
        StorageIntegrityError, match="immutable version path contains different bytes"
    ):
        store.write_immutable(relative, "retry\n")

    assert store.read_verified(relative, original_digest) == "interrupted-attempt\n"
    target = tmp_path / relative
    assert list(target.parent.glob(f".{target.name}.orphan-*")) == []
    assert list(target.parent.glob(f".{target.name}.pending-*")) == []


def _owned_pending_name(target_name: str, created_ns: int) -> str:
    return f".{target_name}.pending-{created_ns:019d}-{uuid4()}"


def test_initialization_removes_only_stale_owned_pending_files(tmp_path) -> None:
    target_directory = tmp_path / "profiles" / "document" / "versions"
    target_directory.mkdir(parents=True)
    stale = target_directory / _owned_pending_name(
        "000001.md", time.time_ns() - storage_module._PENDING_GRACE_NS - 1_000_000
    )
    fresh = target_directory / _owned_pending_name("000001.md", time.time_ns())
    unrelated = target_directory / ".000001.md.pending-not-owned"
    stale.write_bytes(b"stale")
    fresh.write_bytes(b"fresh")
    unrelated.write_bytes(b"unrelated")

    VersionStore(tmp_path)

    assert not stale.exists()
    assert fresh.read_bytes() == b"fresh"
    assert unrelated.read_bytes() == b"unrelated"


def test_write_removes_stale_owned_pending_in_target_directory(tmp_path) -> None:
    store = VersionStore(tmp_path)
    relative = store.relative_path("profile", "document-id", 1)
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    stale = target.parent / _owned_pending_name(
        target.name, time.time_ns() - storage_module._PENDING_GRACE_NS - 1_000_000
    )
    stale.write_bytes(b"stale")

    store.write_immutable(relative, "canonical\n")

    assert not stale.exists()
    assert list(target.parent.glob(f".{target.name}.pending-*")) == []


def _as_symlink_stat(result: os.stat_result) -> os.stat_result:
    values = list(result)
    values[0] = stat.S_IFLNK | 0o777
    return os.stat_result(values)


def test_pending_cleanup_leaves_symlink_entries_untouched(tmp_path, monkeypatch) -> None:
    target_directory = tmp_path / "profiles" / "document" / "versions"
    target_directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    symlink = target_directory / _owned_pending_name(
        "000001.md", time.time_ns() - storage_module._PENDING_GRACE_NS - 1_000_000
    )
    try:
        symlink.symlink_to(outside)
    except OSError:
        symlink.write_bytes(b"simulated symlink entry")
        original_lstat = Path.lstat

        def symlink_lstat(path: Path) -> os.stat_result:
            result = original_lstat(path)
            return _as_symlink_stat(result) if path == symlink else result

        monkeypatch.setattr(Path, "lstat", symlink_lstat)

    VersionStore(tmp_path)

    assert symlink.is_symlink()
    assert outside.read_bytes() == b"outside"


def test_pending_cleanup_bound_fails_closed_without_deleting_entries(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    monkeypatch.setattr(storage_module, "_PENDING_CLEANUP_MAX_ENTRIES", 1)

    with pytest.raises(StorageIntegrityError, match="storage pending cleanup is unavailable"):
        VersionStore(tmp_path)

    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_verified_bytes_returns_only_exact_bounded_regular_file(tmp_path) -> None:
    store = VersionStore(tmp_path)
    relative = "verification-evidence/artifact.bin"
    payload = b"private evidence"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)

    assert (
        store.read_verified_bytes(
            relative,
            hashlib.sha256(payload).hexdigest(),
            expected_size_bytes=len(payload),
            max_size_bytes=262_144,
        )
        == payload
    )


@pytest.mark.parametrize(
    "relative_path",
    ["../artifact.bin", "/artifact.bin", "dir\\artifact.bin", "./artifact.bin", "dir//x"],
)
def test_verified_bytes_rejects_noncanonical_or_escaping_path(tmp_path, relative_path: str) -> None:
    store = VersionStore(tmp_path)

    with pytest.raises(StorageIntegrityError, match="stored artifact is unavailable"):
        store.read_verified_bytes(
            relative_path,
            hashlib.sha256(b"").hexdigest(),
            expected_size_bytes=0,
            max_size_bytes=1,
        )


def test_verified_bytes_rejects_missing_nonregular_size_oversize_and_hash(tmp_path) -> None:
    store = VersionStore(tmp_path)
    directory = tmp_path / "evidence"
    directory.mkdir()
    target = directory / "artifact.bin"
    payload = b"private evidence"
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    cases = (
        ("evidence/missing.bin", digest, len(payload), 262_144),
        ("evidence", digest, len(payload), 262_144),
        ("evidence/artifact.bin", digest, len(payload) - 1, 262_144),
        ("evidence/artifact.bin", digest, len(payload), len(payload) - 1),
        ("evidence/artifact.bin", "0" * 64, len(payload), 262_144),
    )

    for relative, expected_digest, expected_size, maximum in cases:
        with pytest.raises(StorageIntegrityError, match="^stored artifact is unavailable$"):
            store.read_verified_bytes(
                relative,
                expected_digest,
                expected_size_bytes=expected_size,
                max_size_bytes=maximum,
            )


def test_verified_bytes_rejects_symlink_target_and_component(tmp_path, monkeypatch) -> None:
    store = VersionStore(tmp_path)
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    payload = b"private evidence"
    real_target = real_directory / "artifact.bin"
    real_target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    target_symlink = tmp_path / "artifact-link.bin"
    component_symlink = tmp_path / "directory-link"
    simulated_symlinks: set[Path] = set()
    try:
        target_symlink.symlink_to(real_target)
    except OSError:
        target_symlink.write_bytes(payload)
        simulated_symlinks.add(target_symlink)
    try:
        component_symlink.symlink_to(real_directory, target_is_directory=True)
    except OSError:
        component_symlink.mkdir()
        (component_symlink / "artifact.bin").write_bytes(payload)
        simulated_symlinks.add(component_symlink)
    if simulated_symlinks:
        original_lstat = Path.lstat

        def symlink_lstat(path: Path) -> os.stat_result:
            result = original_lstat(path)
            return _as_symlink_stat(result) if path in simulated_symlinks else result

        monkeypatch.setattr(Path, "lstat", symlink_lstat)

    for relative in ("artifact-link.bin", "directory-link/artifact.bin"):
        with pytest.raises(StorageIntegrityError, match="stored artifact is unavailable"):
            store.read_verified_bytes(
                relative,
                digest,
                expected_size_bytes=len(payload),
                max_size_bytes=262_144,
            )


@pytest.mark.parametrize(
    ("directory", "version"),
    (("profiles", "000001.md"), ("resumes", "000042.md"), ("posts", "000001.md")),
)
def test_staged_promotion_accepts_only_bounded_canonical_content_paths(
    tmp_path: Path, directory: str, version: str
) -> None:
    store = VersionStore(tmp_path)
    resource_id = str(uuid4())
    payload = b"canonical\n"
    digest = hashlib.sha256(payload).hexdigest()
    staged = store.stage_artifact(
        str(uuid4()), payload, b"{}", created_ns=time.time_ns(), nonce=str(uuid4())
    )
    relative_path = f"{directory}/{resource_id}/versions/{version}"
    store.promote_staged_artifact(
        staged.payload_path,
        relative_path,
        expected_sha256=digest,
        expected_size_bytes=len(payload),
        max_size_bytes=131_072,
    )
    assert (
        store.read_verified_bytes(
            relative_path,
            digest,
            expected_size_bytes=len(payload),
            max_size_bytes=131_072,
        )
        == payload
    )


@pytest.mark.parametrize(
    "relative_path",
    (
        "profiles/{id}/versions/000000.md",
        "profiles/{id}/versions/1.md",
        "profiles/{id}/versions/000001.md/extra",
        "posts/{id}/versions/000002.md",
        "documents/{id}/versions/000001.md",
        "profiles/{upper_id}/versions/000001.md",
    ),
)
def test_staged_promotion_rejects_broadened_content_targets(
    tmp_path: Path, relative_path: str
) -> None:
    store = VersionStore(tmp_path)
    resource_id = str(uuid4())
    payload = b"canonical\n"
    digest = hashlib.sha256(payload).hexdigest()
    staged = store.stage_artifact(
        str(uuid4()), payload, b"{}", created_ns=time.time_ns(), nonce=str(uuid4())
    )
    candidate = relative_path.format(id=resource_id, upper_id=resource_id.upper())
    with pytest.raises(StorageIntegrityError, match="artifact promotion is unavailable"):
        store.promote_staged_artifact(
            staged.payload_path,
            candidate,
            expected_sha256=digest,
            expected_size_bytes=len(payload),
            max_size_bytes=131_072,
        )
    assert not (tmp_path / candidate).exists()


async def test_ambiguous_commit_ack_never_deletes_committed_canonical_file(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    async with app.state.session_factory() as session:
        actual_commit = session.commit

        async def commit_then_lose_acknowledgement() -> None:
            await actual_commit()
            raise ConnectionError("simulated lost commit acknowledgement")

        monkeypatch.setattr(session, "commit", commit_then_lose_acknowledgement)
        with pytest.raises(ConnectionError, match="lost commit"):
            await DocumentService(
                session,
                app.state.store,
                app.state.settings,
                app.state.artifact_reconciler,
            ).create(
                "profile",
                profile_markdown(),
                "user_test",
                idempotency_record=IdempotencyRecord(
                    owner_id="user_test",
                    idempotency_key="profile-lost-commit-ack",
                    operation="POST:/v1/profiles",
                    request_hash="a" * 64,
                    response_status=201,
                    response_body="",
                    response_headers="{}",
                    resource_type="profile",
                ),
            )

    async with app.state.session_factory() as verification_session:
        document = await verification_session.scalar(
            select(Document).options(selectinload(Document.versions))
        )
        assert document is not None
        version = document.versions[0]
        assert app.state.store.read_verified(version.storage_path, version.sha256).startswith("---")
        receipt = await verification_session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "profile-lost-commit-ack"
            )
        )
        events = (
            await verification_session.scalars(
                select(ChangeEvent).where(ChangeEvent.resource_id == document.id)
            )
        ).all()
        assert receipt is not None
        assert receipt.resource_id == f"{document.id}@1"
        assert receipt.response_body == ""
        assert receipt.response_headers == "{}"
        assert len(events) == 1
    scan = app.state.store.scan_staged_artifacts()
    assert scan.descriptors == ()
    assert scan.incomplete_payloads == ()


async def test_precommit_document_update_total_absence_deletes_exact_stage(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    async with app.state.session_factory() as session:
        service = DocumentService(
            session,
            app.state.store,
            app.state.settings,
            app.state.artifact_reconciler,
        )
        document = await service.create(
            "profile",
            profile_markdown(),
            "user_test",
            idempotency_record=IdempotencyRecord(
                owner_id="user_test",
                idempotency_key="profile-update-base",
                operation="POST:/v1/profiles",
                request_hash="b" * 64,
                response_status=201,
                response_body="",
                response_headers="{}",
                resource_type="profile",
            ),
        )
        current = document.versions[0]

        async def fail_before_commit() -> None:
            raise ConnectionError("simulated precommit failure")

        monkeypatch.setattr(session, "commit", fail_before_commit)
        with pytest.raises(ConnectionError, match="precommit failure"):
            await service.update(
                "profile",
                document.public_identifier,
                profile_markdown().replace("Computing pioneer", "Durability pioneer"),
                "user_test",
                if_match=f'"sha256-{current.sha256}"',
                idempotency_record=IdempotencyRecord(
                    owner_id="user_test",
                    idempotency_key="profile-update-precommit",
                    operation=f"PUT:/v1/profiles/{document.public_identifier}",
                    request_hash="c" * 64,
                    response_status=200,
                    response_body="",
                    response_headers="{}",
                    resource_type="profile",
                ),
            )

    async with app.state.session_factory() as verification_session:
        versions = (await verification_session.scalars(select(DocumentVersion))).all()
        receipts = (await verification_session.scalars(select(IdempotencyRecord))).all()
        assert len(versions) == 1
        assert {record.idempotency_key for record in receipts} == {"profile-update-base"}
        assert app.state.store.read_verified(
            versions[0].storage_path, versions[0].sha256
        ).startswith("---")
    assert not list((app.state.store.root / "profiles").rglob("000002.md"))
    scan = app.state.store.scan_staged_artifacts()
    assert scan.descriptors == ()
    assert scan.incomplete_payloads == ()
