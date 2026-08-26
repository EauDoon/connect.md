"""Least-privilege daemon and recovery CLI for durable search projection work."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.services.database_roles import (
    SEARCH_PROJECTION_DATABASE_ROLE,
    DatabaseRoleContractError,
    require_database_role,
    require_database_url_role,
)
from app.services.search import MeiliSearchProjection
from app.services.search_projection import (
    ProjectionResult,
    SearchProjectionExecutor,
    count_dead_letters,
    list_dead_letters,
    retry_dead_letter,
)
from app.services.storage import VersionStore

SEARCH_PROJECTION_CONTRACT_VERSION = 2


class SearchProjectionWorkerSettings(BaseSettings):
    """Only the database, canonical store, and disposable projection are available."""

    model_config = SettingsConfigDict(
        env_prefix="CONNECTMD_", extra="ignore", hide_input_in_errors=True
    )

    environment: str = "development"
    database_url: str
    storage_path: Path
    meilisearch_url: HttpUrl | None = None
    meilisearch_api_key: str | None = None
    meilisearch_index: str = "documents"
    search_projection_worker_id: str = Field(
        default="search-projection-worker", min_length=1, max_length=128
    )
    search_projection_poll_seconds: int = Field(default=2, ge=1, le=30)
    search_projection_lease_seconds: int = Field(default=60, ge=15, le=300)
    search_projection_max_attempts: int = Field(default=8, ge=1, le=32)
    search_projection_max_backoff_seconds: int = Field(default=300, ge=1, le=3600)
    search_projection_max_healthy_dead_letters: int = Field(default=0, ge=0, le=10_000)
    search_projection_max_healthy_backlog_age_seconds: int = Field(default=600, ge=30, le=86_400)
    search_projection_heartbeat_path: Path = Path("/tmp/connectmd-search-projection-worker-ready")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def require_runtime_configuration(self) -> None:
        try:
            if (
                self.is_production
                and make_url(self.database_url).drivername != "postgresql+asyncpg"
            ):
                raise ValueError("search projection database configuration is invalid")
            require_database_url_role(self.database_url, SEARCH_PROJECTION_DATABASE_ROLE)
        except (ArgumentError, DatabaseRoleContractError):
            raise ValueError("search projection database configuration is invalid") from None


def _session_factory(
    settings: SearchProjectionWorkerSettings,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


def _observe(result: ProjectionResult) -> None:
    fields = [f"event=search_projection_{result.action}"]
    if result.document_id is not None:
        fields.append(f"document_id={result.document_id}")
    if result.version is not None:
        fields.append(f"version={result.version}")
    if result.attempts is not None:
        fields.append(f"attempts={result.attempts}")
    if result.error_code is not None:
        fields.append(f"error_code={result.error_code}")
    print(" ".join(fields), flush=True)


async def _health_payload(
    executor: SearchProjectionExecutor,
    projection: MeiliSearchProjection,
    settings: SearchProjectionWorkerSettings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    if settings.is_production:
        async with executor.session_factory() as session:
            await require_database_role(session, SEARCH_PROJECTION_DATABASE_ROLE)
    snapshot = await executor.health_snapshot(now=current)
    await projection.check_ready()
    state = (
        "healthy"
        if snapshot.dead_letter_count <= settings.search_projection_max_healthy_dead_letters
        and snapshot.oldest_backlog_age_seconds
        <= settings.search_projection_max_healthy_backlog_age_seconds
        else "degraded"
    )
    return {
        "state": state,
        "checked_at": current.isoformat(),
        "backlog_count": snapshot.backlog_count,
        "eligible_count": snapshot.eligible_count,
        "dead_letter_count": snapshot.dead_letter_count,
        "oldest_backlog_age_seconds": snapshot.oldest_backlog_age_seconds,
    }


async def _refresh_health_heartbeat(
    executor: SearchProjectionExecutor,
    projection: MeiliSearchProjection,
    settings: SearchProjectionWorkerSettings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    heartbeat = settings.search_projection_heartbeat_path
    try:
        payload = await _health_payload(executor, projection, settings, now=now)
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        temporary = heartbeat.with_name(f".{heartbeat.name}.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(heartbeat)
    except Exception:
        heartbeat.unlink(missing_ok=True)
        raise
    return payload


async def run_worker(settings: SearchProjectionWorkerSettings) -> int:
    settings.require_runtime_configuration()
    projection = MeiliSearchProjection(settings)
    if not projection.enabled:
        print("search projection worker is not configured", file=sys.stderr)
        return 2
    session_factory, engine = _session_factory(settings)
    heartbeat = settings.search_projection_heartbeat_path
    try:
        async with session_factory() as session:
            await require_database_role(session, SEARCH_PROJECTION_DATABASE_ROLE)
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        for candidate in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(candidate, stopping.set)
            except (NotImplementedError, RuntimeError):
                pass
        executor = SearchProjectionExecutor(
            session_factory,
            VersionStore(settings.storage_path),
            projection,
            worker_id=settings.search_projection_worker_id,
            lease_seconds=settings.search_projection_lease_seconds,
            max_attempts=settings.search_projection_max_attempts,
            max_backoff_seconds=settings.search_projection_max_backoff_seconds,
        )
        last_health_state: str | None = None
        last_cycle_failed = False
        while not stopping.is_set():
            processed = False
            try:
                result = await executor.run_once()
                if result.action != "idle":
                    _observe(result)
                    processed = True
                last_cycle_failed = False
            except Exception:
                # The durable task remains pending/leased. Never log content,
                # credentials, database URLs, or raw exception text.
                if not last_cycle_failed:
                    print(
                        "event=search_projection_worker_cycle_failed",
                        file=sys.stderr,
                        flush=True,
                    )
                last_cycle_failed = True
            try:
                health = await _refresh_health_heartbeat(executor, projection, settings)
                if health["state"] == "degraded" and last_health_state != "degraded":
                    print(
                        " ".join(
                            [
                                "event=search_projection_health_degraded",
                                f"backlog_count={health['backlog_count']}",
                                f"eligible_count={health['eligible_count']}",
                                f"dead_letter_count={health['dead_letter_count']}",
                                "oldest_backlog_age_seconds="
                                f"{health['oldest_backlog_age_seconds']}",
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
                        "event=search_projection_health_unavailable",
                        file=sys.stderr,
                        flush=True,
                    )
                last_health_state = "unavailable"
            if processed:
                continue
            try:
                await asyncio.wait_for(
                    stopping.wait(), timeout=settings.search_projection_poll_seconds
                )
            except TimeoutError:
                pass
    finally:
        heartbeat.unlink(missing_ok=True)
        await engine.dispose()
    return 0


async def list_dead(settings: SearchProjectionWorkerSettings, limit: int) -> int:
    settings.require_runtime_configuration()
    session_factory, engine = _session_factory(settings)
    try:
        async with session_factory() as session:
            await require_database_role(session, SEARCH_PROJECTION_DATABASE_ROLE)
        rows = await list_dead_letters(session_factory, limit=limit)
        total = await count_dead_letters(session_factory)
        for row in rows:
            print(
                " ".join(
                    [
                        "event=search_projection_dead_letter",
                        f"document_id={row.document_id}",
                        f"version={row.version}",
                        f"attempts={row.attempts}",
                        f"error_code={row.last_error_code or 'unknown'}",
                    ]
                )
            )
        print(f"dead_letter_page_count={len(rows)} dead_letter_total_count={total}")
    finally:
        await engine.dispose()
    return 0


async def retry_dead(
    settings: SearchProjectionWorkerSettings, document_id: str, version: int
) -> int:
    settings.require_runtime_configuration()
    session_factory, engine = _session_factory(settings)
    try:
        async with session_factory() as session:
            await require_database_role(session, SEARCH_PROJECTION_DATABASE_ROLE)
        recovered = await retry_dead_letter(
            session_factory, document_id=document_id, version=version
        )
    finally:
        await engine.dispose()
    if not recovered:
        print("search projection dead letter was not found", file=sys.stderr)
        return 1
    print(
        f"event=search_projection_requeued document_id={document_id} version={version}",
        flush=True,
    )
    return 0


async def projection_status(settings: SearchProjectionWorkerSettings) -> int:
    settings.require_runtime_configuration()
    projection = MeiliSearchProjection(settings)
    session_factory, engine = _session_factory(settings)
    executor = SearchProjectionExecutor(
        session_factory,
        VersionStore(settings.storage_path),
        projection,
        worker_id=f"{settings.search_projection_worker_id}-status",
        lease_seconds=settings.search_projection_lease_seconds,
        max_attempts=settings.search_projection_max_attempts,
        max_backoff_seconds=settings.search_projection_max_backoff_seconds,
    )
    try:
        async with session_factory() as session:
            await require_database_role(session, SEARCH_PROJECTION_DATABASE_ROLE)
        payload = await _health_payload(executor, projection, settings)
    except Exception:
        print("state=unavailable", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    print(" ".join(f"{key}={value}" for key, value in payload.items()))
    return 0 if payload["state"] == "healthy" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.search_projection_worker")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run")
    commands.add_parser("status")
    dead = commands.add_parser("list-dead")
    dead.add_argument("--limit", type=int, choices=range(1, 1001), default=100)
    retry = commands.add_parser("retry-dead")
    retry.add_argument("--document-id", required=True)
    retry.add_argument("--version", type=int, required=True, choices=range(1, 2_147_483_648))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        settings = SearchProjectionWorkerSettings()  # type: ignore[call-arg]
        if args.command in {None, "run"}:
            result = asyncio.run(run_worker(settings))
        elif args.command == "list-dead":
            result = asyncio.run(list_dead(settings, args.limit))
        elif args.command == "status":
            result = asyncio.run(projection_status(settings))
        else:
            result = asyncio.run(retry_dead(settings, args.document_id, args.version))
    except Exception:
        # Configuration and dependency exceptions may contain credentials or
        # connection strings. The operator gets only a bounded failure signal.
        print("search projection command failed", file=sys.stderr)
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
