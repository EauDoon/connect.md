from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import cli
from app.config import Settings
from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountBackupAuthority,
    AccountBackupManifest,
    AccountBackupObligation,
    AccountErasureItem,
    AccountLifecycle,
    Base,
)


def _register_args(
    generation_id: str, created_at: datetime, expires_at: datetime, digest: str
) -> Namespace:
    return Namespace(
        generation_id=generation_id,
        created_at=created_at.isoformat(),
        expires_at=expires_at.isoformat(),
        db_manifest_digest=digest,
        markdown_manifest_digest="b" * 64,
    )


@pytest.mark.asyncio
async def test_backup_register_is_immutable_attaches_open_lifecycle_and_requires_proof_transitions(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'backup-cli.db'}",
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
    engine = cli.build_engine(settings)
    session_factory = cli.build_session_factory(settings, engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            AccountLifecycle(
                subject_hmac="s" * 64,
                request_idempotency_hmac="r" * 64,
                state="concealed",
                provider_state="pending",
                backup_state="expiry_pending",
                policy_version="test",
                requested_at=now,
                confirmed_at=now,
            )
        )
        await session.commit()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    created_at = now - timedelta(days=2)
    expired_at = now - timedelta(days=1)
    assert (
        await cli.register_backup_generation(
            _register_args("generation-one", created_at, expired_at, "a" * 64)
        )
        == 0
    )
    assert (
        await cli.register_backup_generation(
            _register_args("generation-one", created_at, expired_at, "a" * 64)
        )
        == 0
    )
    assert (
        await cli.register_backup_generation(
            _register_args("generation-one", created_at, expired_at, "c" * 64)
        )
        == 1
    )
    assert (
        await cli.register_backup_generation(
            _register_args("generation-two", created_at, expired_at, "c" * 64)
        )
        == 0
    )
    assert (
        await cli.transition_backup_generation(
            Namespace(backup_action="expire", generation_id="generation-one", proof_digest="d" * 64)
        )
        == 0
    )
    assert (
        await cli.register_backup_generation(
            _register_args("generation-three", created_at, expired_at, "e" * 64)
        )
        == 0
    )
    assert (
        await cli.register_backup_generation(
            _register_args("generation-four", created_at, expired_at, "f" * 64)
        )
        == 0
    )
    assert (
        await cli.transition_backup_generation(
            Namespace(
                backup_action="crypto-destroyed",
                generation_id="generation-three",
                proof_digest="e" * 64,
            )
        )
        == 0
    )
    async with session_factory() as session:
        authority = await session.get(AccountBackupAuthority, ACCOUNT_BACKUP_AUTHORITY_ID)
        assert authority is not None and authority.current_generation_id == "generation-four"
        manifests = {
            row.generation_id: row
            for row in (await session.scalars(select(AccountBackupManifest))).all()
        }
        assert manifests["generation-one"].state == "expired"
        assert manifests["generation-one"].expired_proof_digest == "d" * 64
        assert manifests["generation-three"].state == "crypto_destroyed"
        assert manifests["generation-three"].crypto_destroyed_proof_digest == "e" * 64
        assert (await session.scalars(select(AccountBackupObligation))).all()
        assert (await session.scalars(select(AccountErasureItem))).all()
    await engine.dispose()
