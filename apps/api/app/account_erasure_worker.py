"""Signal-aware local account-erasure daemon; disabled unless lifecycle is enabled."""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import build_engine, build_session_factory
from app.models import AccountErasureItem, AccountLifecycle
from app.services.account_erasure import AccountErasureExecutor, HttpClerkLifecycleProvider
from app.services.database_roles import (
    ACCOUNT_ERASURE_DATABASE_ROLE,
    require_database_role,
)
from app.services.deletion_journal import (
    DeletionCommitmentJournal,
    verify_live_deletion_mirror,
)
from app.services.search import MeiliSearchProjection
from app.services.storage import VersionStore

ACCOUNT_LIFECYCLE_HEALTH_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class AccountLifecycleHealth:
    deletion_commitment_count: int
    backlog_count: int
    eligible_count: int
    dead_letter_count: int
    failed_lifecycle_count: int
    oldest_eligible_age_seconds: int


class ReadinessProbe(Protocol):
    async def check_ready(self) -> None: ...


async def _database_health_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    journal: DeletionCommitmentJournal,
    *,
    now: datetime | None = None,
) -> AccountLifecycleHealth:
    current = now or datetime.now(UTC)
    eligible = or_(
        and_(
            AccountErasureItem.state == "queued",
            AccountErasureItem.available_at.is_not(None),
            AccountErasureItem.available_at <= current,
        ),
        and_(
            AccountErasureItem.state == "leased",
            AccountErasureItem.lease_expires_at.is_not(None),
            AccountErasureItem.lease_expires_at <= current,
        ),
    )
    async with session_factory() as session:
        deletion_commitment_count = await verify_live_deletion_mirror(session, journal)
        backlog_count = int(
            await session.scalar(
                select(func.count())
                .select_from(AccountErasureItem)
                .where(AccountErasureItem.state.in_(("queued", "leased")))
            )
            or 0
        )
        eligible_count = int(
            await session.scalar(
                select(func.count()).select_from(AccountErasureItem).where(eligible)
            )
            or 0
        )
        dead_letter_count = int(
            await session.scalar(
                select(func.count())
                .select_from(AccountErasureItem)
                .where(AccountErasureItem.state == "dead_letter")
            )
            or 0
        )
        failed_lifecycle_count = int(
            await session.scalar(
                select(func.count())
                .select_from(AccountLifecycle)
                .where(AccountLifecycle.state == "failed")
            )
            or 0
        )
        oldest_queued = await session.scalar(
            select(func.min(AccountErasureItem.available_at)).where(
                AccountErasureItem.state == "queued",
                AccountErasureItem.available_at.is_not(None),
                AccountErasureItem.available_at <= current,
            )
        )
        oldest_expired_lease = await session.scalar(
            select(func.min(AccountErasureItem.lease_expires_at)).where(
                AccountErasureItem.state == "leased",
                AccountErasureItem.lease_expires_at.is_not(None),
                AccountErasureItem.lease_expires_at <= current,
            )
        )
    eligible_times = [
        candidate.replace(tzinfo=UTC) if candidate.tzinfo is None else candidate
        for candidate in (oldest_queued, oldest_expired_lease)
        if candidate is not None
    ]
    if not eligible_times:
        oldest_eligible_age = 0
    else:
        oldest_eligible_age = max(0, int((current - min(eligible_times)).total_seconds()))
    return AccountLifecycleHealth(
        deletion_commitment_count=deletion_commitment_count,
        backlog_count=backlog_count,
        eligible_count=eligible_count,
        dead_letter_count=dead_letter_count,
        failed_lifecycle_count=failed_lifecycle_count,
        oldest_eligible_age_seconds=oldest_eligible_age,
    )


async def _health_payload(
    session_factory: async_sessionmaker[AsyncSession],
    journal: DeletionCommitmentJournal,
    provider: ReadinessProbe,
    search: ReadinessProbe,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    if settings.is_production:
        async with session_factory() as session:
            await require_database_role(session, ACCOUNT_ERASURE_DATABASE_ROLE)
    snapshot = await _database_health_snapshot(session_factory, journal, now=current)
    await provider.check_ready()
    await search.check_ready()
    state = (
        "healthy"
        if snapshot.backlog_count <= settings.account_lifecycle_max_healthy_backlog
        and snapshot.dead_letter_count <= settings.account_lifecycle_max_healthy_dead_letters
        and snapshot.failed_lifecycle_count == 0
        and snapshot.oldest_eligible_age_seconds
        <= settings.account_lifecycle_max_healthy_eligible_age_seconds
        else "degraded"
    )
    return {
        "state": state,
        "checked_at": current.isoformat(),
        "database_ready": True,
        "deletion_journal_ready": True,
        "provider_ready": True,
        "search_ready": True,
        "deletion_commitment_count": snapshot.deletion_commitment_count,
        "backlog_count": snapshot.backlog_count,
        "eligible_count": snapshot.eligible_count,
        "dead_letter_count": snapshot.dead_letter_count,
        "failed_lifecycle_count": snapshot.failed_lifecycle_count,
        "oldest_eligible_age_seconds": snapshot.oldest_eligible_age_seconds,
    }


async def _refresh_health_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    journal: DeletionCommitmentJournal,
    provider: ReadinessProbe,
    search: ReadinessProbe,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    heartbeat = settings.account_lifecycle_heartbeat_path
    try:
        payload = await _health_payload(
            session_factory, journal, provider, search, settings, now=now
        )
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        temporary = heartbeat.with_name(f".{heartbeat.name}.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(heartbeat)
    except Exception:
        heartbeat.unlink(missing_ok=True)
        raise
    return payload


async def run() -> int:
    settings = get_settings()
    if not settings.account_lifecycle_enabled:
        print("account lifecycle executor is disabled", file=sys.stderr)
        return 2
    settings.require_clerk_backend_configuration()
    settings.require_database_role_configuration(ACCOUNT_ERASURE_DATABASE_ROLE)
    engine = build_engine(settings)
    heartbeat = settings.account_lifecycle_heartbeat_path
    try:
        session_factory = build_session_factory(settings, engine)
        async with session_factory() as session:
            await require_database_role(session, ACCOUNT_ERASURE_DATABASE_ROLE)
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        for candidate in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(candidate, stopping.set)
            except (NotImplementedError, RuntimeError):  # Windows or non-main test loop.
                pass
        journal = DeletionCommitmentJournal(settings)
        search = MeiliSearchProjection(settings)
        provider = HttpClerkLifecycleProvider(settings)
        executor = AccountErasureExecutor(
            session_factory,
            VersionStore(settings.storage_path),
            search,
            provider,
            settings,
            worker_id=settings.account_lifecycle_worker_id,
        )
        last_health_state: str | None = None
        last_cycle_failed = False
        while not stopping.is_set():
            health_verified = False
            try:
                health = await _refresh_health_heartbeat(
                    session_factory, journal, provider, search, settings
                )
                health_verified = True
                if health["state"] == "degraded" and last_health_state != "degraded":
                    print(
                        " ".join(
                            [
                                "event=account_lifecycle_health_degraded",
                                f"backlog_count={health['backlog_count']}",
                                f"eligible_count={health['eligible_count']}",
                                f"dead_letter_count={health['dead_letter_count']}",
                                f"failed_lifecycle_count={health['failed_lifecycle_count']}",
                                "oldest_eligible_age_seconds="
                                f"{health['oldest_eligible_age_seconds']}",
                            ]
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                last_health_state = str(health["state"])
            except Exception:
                heartbeat.unlink(missing_ok=True)
                if last_health_state != "unavailable":
                    print(
                        "event=account_lifecycle_health_unavailable",
                        file=sys.stderr,
                        flush=True,
                    )
                last_health_state = "unavailable"
            processed = False
            if health_verified:
                try:
                    result = await executor.run_once(limit=1)
                    processed = result.claimed > 0 or result.held > 0
                    last_cycle_failed = False
                except Exception:  # Never emit provider, subject, or content details.
                    if not last_cycle_failed:
                        print("account erasure worker cycle failed", file=sys.stderr, flush=True)
                    last_cycle_failed = True
            if processed:
                continue
            try:
                await asyncio.wait_for(
                    stopping.wait(), timeout=settings.account_lifecycle_poll_seconds
                )
            except TimeoutError:
                pass
    finally:
        heartbeat.unlink(missing_ok=True)
        await engine.dispose()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
