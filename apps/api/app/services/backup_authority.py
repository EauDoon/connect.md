"""Transactional backup-generation authority operations."""

from __future__ import annotations

import re
import sys
from argparse import Namespace
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountBackupAuthority,
    AccountBackupManifest,
    AccountBackupObligation,
    AccountErasureItem,
    AccountLifecycle,
)
from app.services.database_roles import API_DATABASE_ROLE, require_database_role

_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

TimestampParser = Callable[[str, str], datetime | None]
SettingsFactory = Callable[[], Settings]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _backup_digest(value: str, field: str) -> str | None:
    if not _HEX_DIGEST.fullmatch(value):
        print(f"{field} must be a lowercase SHA-256 hex digest", file=sys.stderr)
        return None
    return value


async def register_backup_generation(
    settings_factory: SettingsFactory,
    args: Namespace,
    *,
    parse_timestamp: TimestampParser,
) -> int:
    """Register immutable local backup evidence and attach open erasures atomically."""
    created_at = parse_timestamp(args.created_at, "created-at")
    expires_at = parse_timestamp(args.expires_at, "expires-at")
    db_digest = _backup_digest(args.db_manifest_digest, "db-manifest-digest")
    markdown_digest = _backup_digest(args.markdown_manifest_digest, "markdown-manifest-digest")
    if None in {created_at, expires_at, db_digest, markdown_digest}:
        return 2
    assert created_at is not None and expires_at is not None
    assert db_digest is not None and markdown_digest is not None
    if not _GENERATION_ID.fullmatch(args.generation_id) or expires_at <= created_at:
        print(
            "generation id must be a printable identifier and timestamps must increase",
            file=sys.stderr,
        )
        return 2
    settings = settings_factory()
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    now = datetime.now(UTC)
    attached = 0
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            await session.execute(
                update(AccountBackupAuthority)
                .where(AccountBackupAuthority.id == ACCOUNT_BACKUP_AUTHORITY_ID)
                .values(updated_at=AccountBackupAuthority.updated_at)
            )
            authority = await session.get(
                AccountBackupAuthority, ACCOUNT_BACKUP_AUTHORITY_ID, with_for_update=True
            )
            existing = await session.scalar(
                select(AccountBackupManifest)
                .where(AccountBackupManifest.generation_id == args.generation_id)
                .with_for_update()
            )
            if existing is not None:
                immutable_match = (
                    _utc(existing.created_at) == created_at
                    and _utc(existing.expires_at) == expires_at
                    and existing.db_manifest_digest == db_digest
                    and existing.markdown_manifest_digest == markdown_digest
                )
                if not immutable_match:
                    print(
                        "backup generation is already registered with different immutable evidence",
                        file=sys.stderr,
                    )
                    return 1
                if authority is None:
                    print(
                        "backup generation authority is missing; register a new generation",
                        file=sys.stderr,
                    )
                    return 1
                print(f"backup generation {args.generation_id} is already registered")
                return 0
            manifest = AccountBackupManifest(
                generation_id=args.generation_id,
                created_at=created_at,
                expires_at=expires_at,
                state="active",
                db_manifest_digest=db_digest,
                markdown_manifest_digest=markdown_digest,
            )
            session.add(manifest)
            if authority is None:
                authority = AccountBackupAuthority(
                    id=ACCOUNT_BACKUP_AUTHORITY_ID,
                    current_generation_id=args.generation_id,
                    registered_at=now,
                    updated_at=now,
                )
                session.add(authority)
            else:
                authority.current_generation_id = args.generation_id
                authority.updated_at = now
            await session.flush()
            live_erasure_after_capture = await session.scalar(
                select(AccountLifecycle.id)
                .where(
                    AccountLifecycle.state == "fully_erased",
                    AccountLifecycle.live_erased_at.is_not(None),
                    AccountLifecycle.live_erased_at >= manifest.created_at,
                )
                .limit(1)
            )
            if live_erasure_after_capture is not None:
                await session.rollback()
                print(
                    "backup registration violates a live-erasure capture invariant", file=sys.stderr
                )
                return 1
            open_lifecycles = (
                await session.scalars(
                    select(AccountLifecycle)
                    .where(
                        AccountLifecycle.confirmed_at.is_not(None),
                        AccountLifecycle.state != "fully_erased",
                    )
                    .with_for_update()
                )
            ).all()
            for lifecycle in open_lifecycles:
                session.add(
                    AccountBackupObligation(
                        deletion_id=lifecycle.id,
                        generation_id=manifest.generation_id,
                        generation_created_at=manifest.created_at,
                        generation_expires_at=manifest.expires_at,
                        db_manifest_digest=manifest.db_manifest_digest,
                        markdown_manifest_digest=manifest.markdown_manifest_digest,
                        state="pending",
                    )
                )
                session.add(
                    AccountErasureItem(
                        deletion_id=lifecycle.id,
                        resource_type="backup_manifest",
                        resource_id=manifest.id,
                        phase="backup",
                        disposition="hold",
                        state="queued",
                        attempts=0,
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                attached += 1
            await session.commit()
    finally:
        await engine.dispose()
    print(f"backup generation {args.generation_id} registered; attached={attached}")
    return 0


async def transition_backup_generation(settings_factory: SettingsFactory, args: Namespace) -> int:
    proof_digest = _backup_digest(args.proof_digest, "proof-digest")
    if proof_digest is None:
        return 2
    settings = settings_factory()
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            await session.execute(
                update(AccountBackupAuthority)
                .where(AccountBackupAuthority.id == ACCOUNT_BACKUP_AUTHORITY_ID)
                .values(updated_at=AccountBackupAuthority.updated_at)
            )
            authority = await session.get(
                AccountBackupAuthority, ACCOUNT_BACKUP_AUTHORITY_ID, with_for_update=True
            )
            manifest = await session.scalar(
                select(AccountBackupManifest)
                .where(AccountBackupManifest.generation_id == args.generation_id)
                .with_for_update()
            )
            if authority is None or manifest is None or manifest.state != "active":
                print("active backup generation was not found", file=sys.stderr)
                return 1
            if authority.current_generation_id == manifest.generation_id:
                print(
                    "register a replacement backup generation before retiring the current generation",
                    file=sys.stderr,
                )
                return 1
            now = datetime.now(UTC)
            expires_at = _utc(manifest.expires_at)
            if now < expires_at:
                print("backup generation has not reached its registered expiry", file=sys.stderr)
                return 1
            if args.backup_action == "expire":
                manifest.state = "expired"
                manifest.expired_proof_digest = proof_digest
                manifest.expired_at = now
            else:
                manifest.state = "crypto_destroyed"
                manifest.crypto_destroyed_proof_digest = proof_digest
                manifest.crypto_destroyed_at = now
            await session.commit()
    finally:
        await engine.dispose()
    print(f"backup generation {args.generation_id} marked {args.backup_action}")
    return 0
