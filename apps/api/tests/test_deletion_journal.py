from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import lifecycle_hmac
from app.config import Settings
from app.main import create_app
from app.models import AccountAccessDeny, AccountLifecycle, Base
from app.services.deletion_journal import (
    DELETION_AUTHORITY_CONTRACT_VERSION,
    DeletionCommitmentJournal,
    DeletionJournalError,
    verify_live_deletion_mirror,
)


def test_deletion_authority_contract_version_is_witness_aware() -> None:
    assert DELETION_AUTHORITY_CONTRACT_VERSION >= 1


def _settings(tmp_path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'journal.db'}",
        "storage_path": tmp_path / "storage",
        "api_key_pepper": "test-only-pepper-is-long-enough",
        "account_lifecycle_enabled": True,
        "lifecycle_hmac_key": "h" * 32,
        "lifecycle_aead_key": "a" * 32,
        "deletion_journal_path": tmp_path / "deletion-journal",
        "deletion_witness_path": tmp_path / "deletion-witness",
        "deletion_witness_hmac_key": "w" * 32,
        "clerk_backend_secret": "b" * 32,
        "clerk_backend_base_url": "https://clerk.example.test",
    }
    values.update(overrides)
    return Settings(**values)


def _append(journal: DeletionCommitmentJournal, *, deletion_id: str = "deletion-one"):
    now = datetime.now(UTC)
    subject = "user-secret-subject"
    return journal.append(
        deletion_id=deletion_id,
        subject=subject,
        subject_hmac=lifecycle_hmac(journal.settings, "subject", subject),
        backup_generation_id="connectmd-20260804T000000Z",
        backup_generation_created_at=now - timedelta(hours=1),
        committed_at=now,
        policy_version="account-lifecycle-v1",
    )


def test_journal_requires_explicit_initialization_and_pins_all_authority_keys(tmp_path) -> None:
    settings = _settings(tmp_path)
    journal = DeletionCommitmentJournal(settings)
    with pytest.raises(DeletionJournalError, match="missing or unsafe"):
        journal.verify()
    journal.initialize(created_at=datetime(2026, 8, 4, tzinfo=UTC))
    assert journal.verify() == []
    assert journal.checkpoint() == (0, "0" * 64)
    witness_zero = journal.witness_entries_root / "00000000000000000000.json"
    assert witness_zero.is_file()

    rotated_hmac = DeletionCommitmentJournal(_settings(tmp_path, lifecycle_hmac_key="r" * 32))
    with pytest.raises(DeletionJournalError, match="HMAC key"):
        rotated_hmac.verify()
    rotated_aead = DeletionCommitmentJournal(_settings(tmp_path, lifecycle_aead_key="r" * 32))
    with pytest.raises(DeletionJournalError, match="AEAD key"):
        rotated_aead.verify()
    rotated_witness = DeletionCommitmentJournal(
        _settings(tmp_path, deletion_witness_hmac_key="r" * 32)
    )
    with pytest.raises(DeletionJournalError, match="witness HMAC key"):
        rotated_witness.verify()


def test_journal_is_non_plaintext_chained_idempotent_and_restore_checkpoint_exact(tmp_path) -> None:
    journal = DeletionCommitmentJournal(_settings(tmp_path))
    journal.initialize()
    commitment = _append(journal)
    assert (
        journal.append(
            deletion_id=commitment.deletion_id,
            subject="user-secret-subject",
            subject_hmac=commitment.subject_hmac,
            backup_generation_id=commitment.backup_generation_id,
            backup_generation_created_at=commitment.backup_generation_created_at,
            committed_at=commitment.committed_at + timedelta(minutes=1),
            policy_version=commitment.policy_version,
        )
        == commitment
    )
    head_sequence, head_digest = journal.checkpoint()
    assert head_sequence == 1 and len(head_digest) == 64
    journal.assert_checkpoint(head_sequence=head_sequence, head_digest=head_digest)
    with pytest.raises(DeletionJournalError, match="does not cover"):
        journal.assert_checkpoint(head_sequence=0, head_digest="0" * 64)
    entry_bytes = next((journal.root / "entries").iterdir()).read_bytes()
    witness_bytes = (journal.witness_entries_root / "00000000000000000001.json").read_bytes()
    assert b"user-secret-subject" not in entry_bytes
    assert b"user-secret-subject" not in witness_bytes
    assert b"subject_ciphertext" in entry_bytes


@pytest.mark.parametrize("tamper", ["entry", "truncate"])
def test_journal_integrity_fails_closed_on_edit_or_truncation(tmp_path, tamper: str) -> None:
    journal = DeletionCommitmentJournal(_settings(tmp_path))
    journal.initialize()
    _append(journal)
    entry_path = next((journal.root / "entries").iterdir())
    if tamper == "entry":
        payload = json.loads(entry_path.read_text(encoding="utf-8"))
        payload["policy_version"] = "tampered"
        entry_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    else:
        entry_path.unlink()
    with pytest.raises(DeletionJournalError):
        journal.verify()


@pytest.mark.parametrize("tamper", ["entry", "truncate"])
def test_witness_integrity_fails_closed_on_edit_or_truncation(tmp_path, tamper: str) -> None:
    journal = DeletionCommitmentJournal(_settings(tmp_path))
    journal.initialize()
    _append(journal)
    witness_path = journal.witness_entries_root / "00000000000000000001.json"
    if tamper == "entry":
        payload = json.loads(witness_path.read_text(encoding="utf-8"))
        payload["journal_head_digest"] = "f" * 64
        witness_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    else:
        witness_path.unlink()
    with pytest.raises(DeletionJournalError):
        journal.verify()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_journal_rejects_weakened_directory_permissions_and_lock_symlink(tmp_path) -> None:
    journal = DeletionCommitmentJournal(_settings(tmp_path))
    journal.initialize()
    journal.root.chmod(0o755)
    with pytest.raises(DeletionJournalError, match="root permissions"):
        journal.verify()
    journal.root.chmod(0o700)
    journal.lock_path.unlink()
    journal.lock_path.symlink_to(journal.state_path)
    with pytest.raises(DeletionJournalError, match="lock path"):
        journal.verify()


def test_append_filesystem_fault_after_entry_creation_is_bounded_and_fail_closed(
    tmp_path, monkeypatch
) -> None:
    journal = DeletionCommitmentJournal(_settings(tmp_path))
    journal.initialize()

    def fail_state_replace(_payload: bytes) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(journal, "_replace_state", fail_state_replace)
    with pytest.raises(DeletionJournalError, match="filesystem operation failed"):
        _append(journal)
    assert len(list((journal.root / "entries").iterdir())) == 1
    with pytest.raises(DeletionJournalError, match="entry count"):
        journal.verify()


def test_append_filesystem_fault_after_journal_head_update_leaves_witness_mismatch_fail_closed(
    tmp_path, monkeypatch
) -> None:
    journal = DeletionCommitmentJournal(_settings(tmp_path))
    journal.initialize()
    original_write_new = journal._write_new

    def fail_witness_write(path, payload: bytes) -> None:
        if path.parent == journal.witness_entries_root and path.name.endswith("01.json"):
            raise OSError("simulated witness failure")
        original_write_new(path, payload)

    monkeypatch.setattr(journal, "_write_new", fail_witness_write)
    with pytest.raises(DeletionJournalError, match="filesystem operation failed"):
        _append(journal)
    with pytest.raises(DeletionJournalError, match="head sequences do not match"):
        journal.verify()


@pytest.mark.parametrize("rolled_back_authority", ["journal", "witness"])
def test_rollback_of_either_authority_fails_exact_cross_authority_continuity(
    tmp_path, rolled_back_authority: str
) -> None:
    settings = _settings(tmp_path)
    journal = DeletionCommitmentJournal(settings)
    journal.initialize(created_at=datetime(2026, 8, 4, tzinfo=UTC))
    authority = journal.root if rolled_back_authority == "journal" else journal.witness_root
    preserved = tmp_path / f"preserved-{rolled_back_authority}"
    shutil.copytree(authority, preserved)
    _append(journal)

    shutil.rmtree(authority)
    shutil.copytree(preserved, authority)
    rolled_back = DeletionCommitmentJournal(settings)
    with pytest.raises(DeletionJournalError, match="head sequences do not match"):
        rolled_back.verify()


@pytest.mark.asyncio
async def test_coordinated_database_and_journal_rollback_is_rejected_by_preserved_witness(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    journal = DeletionCommitmentJournal(settings)
    journal.initialize(created_at=datetime(2026, 8, 4, tzinfo=UTC))
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    database_path = tmp_path / "journal.db"
    early_database = tmp_path / "early-journal.db"
    early_journal = tmp_path / "early-deletion-journal"
    shutil.copy2(database_path, early_database)
    shutil.copytree(journal.root, early_journal)

    commitment = _append(journal)
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(
            AccountLifecycle(
                id=commitment.deletion_id,
                subject_hmac=commitment.subject_hmac,
                request_idempotency_hmac="i" * 64,
                state="concealed",
                provider_state="pending",
                backup_state="expiry_pending",
                policy_version=commitment.policy_version,
                requested_at=commitment.committed_at,
                confirmed_at=commitment.committed_at,
                concealed_at=commitment.committed_at,
            )
        )
        session.add(
            AccountAccessDeny(
                subject_hmac=commitment.subject_hmac,
                deletion_id=commitment.deletion_id,
                denied_at=commitment.committed_at,
            )
        )
        await session.commit()
        assert await verify_live_deletion_mirror(session, journal) == 1
    await engine.dispose()

    shutil.copy2(early_database, database_path)
    shutil.rmtree(journal.root)
    shutil.copytree(early_journal, journal.root)
    rolled_back = DeletionCommitmentJournal(settings)
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        with pytest.raises(DeletionJournalError, match="head sequences do not match"):
            await verify_live_deletion_mirror(session, rolled_back)
    await engine.dispose()


def test_append_replay_binds_subject_and_backup_generation_creation(tmp_path) -> None:
    journal = DeletionCommitmentJournal(_settings(tmp_path))
    journal.initialize()
    commitment = _append(journal)
    with pytest.raises(DeletionJournalError, match="subject digest"):
        journal.append(
            deletion_id=commitment.deletion_id,
            subject="different-subject",
            subject_hmac=commitment.subject_hmac,
            backup_generation_id=commitment.backup_generation_id,
            backup_generation_created_at=commitment.backup_generation_created_at,
            committed_at=commitment.committed_at,
            policy_version=commitment.policy_version,
        )
    with pytest.raises(DeletionJournalError, match="replay does not match"):
        journal.append(
            deletion_id=commitment.deletion_id,
            subject="user-secret-subject",
            subject_hmac=commitment.subject_hmac,
            backup_generation_id=commitment.backup_generation_id,
            backup_generation_created_at=commitment.backup_generation_created_at
            + timedelta(seconds=1),
            committed_at=commitment.committed_at,
            policy_version=commitment.policy_version,
        )


@pytest.mark.asyncio
async def test_live_mirror_parity_is_bidirectional_and_subject_bound(tmp_path) -> None:
    settings = _settings(tmp_path)
    journal = DeletionCommitmentJournal(settings)
    journal.initialize()
    commitment = _append(journal)
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        with pytest.raises(DeletionJournalError, match="sets do not match"):
            await verify_live_deletion_mirror(session, journal)
        now = commitment.committed_at
        session.add(
            AccountLifecycle(
                id=commitment.deletion_id,
                subject_hmac=commitment.subject_hmac,
                request_idempotency_hmac="i" * 64,
                state="concealed",
                provider_state="pending",
                backup_state="expiry_pending",
                policy_version=commitment.policy_version,
                requested_at=now,
                confirmed_at=now,
                concealed_at=now,
            )
        )
        session.add(
            AccountAccessDeny(
                subject_hmac=commitment.subject_hmac,
                deletion_id=commitment.deletion_id,
                denied_at=now,
            )
        )
        await session.commit()
        assert await verify_live_deletion_mirror(session, journal) == 1
        extra = AccountLifecycle(
            id="database-only-deletion",
            subject_hmac="x" * 64,
            request_idempotency_hmac="y" * 64,
            state="concealed",
            provider_state="pending",
            backup_state="expiry_pending",
            policy_version="account-lifecycle-v1",
            requested_at=now,
            confirmed_at=now,
            concealed_at=now,
        )
        session.add(extra)
        session.add(
            AccountAccessDeny(
                subject_hmac=extra.subject_hmac,
                deletion_id=extra.id,
                denied_at=now,
            )
        )
        await session.commit()
        with pytest.raises(DeletionJournalError, match="sets do not match"):
            await verify_live_deletion_mirror(session, journal)
    await engine.dispose()


def test_nonempty_journal_cannot_start_with_lifecycle_disabled(tmp_path) -> None:
    enabled = _settings(tmp_path)
    journal = DeletionCommitmentJournal(enabled)
    journal.initialize()
    _append(journal)
    disabled = _settings(tmp_path, account_lifecycle_enabled=False)
    with pytest.raises(DeletionJournalError, match="cannot be disabled"):
        create_app(disabled)
