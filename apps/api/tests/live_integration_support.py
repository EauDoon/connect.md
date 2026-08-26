"""Strict, bounded support for the opt-in PostgreSQL/Meilisearch CI lane."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import SearchProjectionTask
from app.services.database_roles import SEARCH_PROJECTION_DATABASE_ROLE
from app.services.search_projection import SearchProjectionExecutor

LIVE_INTEGRATION_FLAG = "CONNECTMD_RUN_LIVE_INTEGRATION"
DATABASE_URL_ENV = "CONNECTMD_DATABASE_URL"
SEARCH_PROJECTION_DATABASE_URL_ENV = "CONNECTMD_SEARCH_PROJECTION_DATABASE_URL"
MEILISEARCH_URL_ENV = "CONNECTMD_MEILISEARCH_URL"
MEILISEARCH_KEY_ENV = "CONNECTMD_MEILISEARCH_API_KEY"
INTEGRATION_DATABASE = "connectmd_integration"
LIVE_INDEX_PREFIX = "connectmd-integration-"
MAX_LIVE_INDEX_LENGTH = 64
_LIVE_INDEX_PATTERN = re.compile(r"connectmd-integration-[0-9a-f]{32}")
_TASK_POLL_SECONDS = 0.1
_TASK_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class LiveIntegrationConfig:
    """Validated live-test inputs; the generated index is unique to one run."""

    database_url: str
    search_projection_database_url: str
    meilisearch_url: str
    meilisearch_api_key: str
    meilisearch_index: str

    def __post_init__(self) -> None:
        _validate_index_name(self.meilisearch_index)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} must be configured for live integration")
    return value


def _is_numeric_loopback(host: str | None) -> bool:
    if host is None:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_database_url(value: str, *, expected_role: str | None = None) -> None:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("live database URL is invalid") from None
    if parsed.scheme != "postgresql+asyncpg":
        raise ValueError("live database URL must use postgresql+asyncpg")
    if not _is_numeric_loopback(host):
        raise ValueError("live database URL must use a numeric loopback host")
    if port is None or not 1 <= port <= 65535:
        raise ValueError("live database URL must include an explicit port")
    if parsed.path != f"/{INTEGRATION_DATABASE}":
        raise ValueError("live database URL must target the integration database")
    if expected_role is not None and parsed.username != expected_role:
        raise ValueError("live database URL must use the dedicated search projection role")
    if parsed.query or parsed.fragment:
        raise ValueError("live database URL must not contain query or fragment data")


def _validate_meilisearch_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("live Meilisearch URL is invalid") from None
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("live Meilisearch URL must use http or https")
    if not _is_numeric_loopback(host):
        raise ValueError("live Meilisearch URL must use a numeric loopback host")
    if port is None or not 1 <= port <= 65535:
        raise ValueError("live Meilisearch URL must include an explicit port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("live Meilisearch URL must be a base URL without query data")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("live Meilisearch URL must not contain user info")


def _validate_meilisearch_key(value: str) -> None:
    if len(value) < 16 or not value.strip() or any(character.isspace() for character in value):
        raise ValueError("live Meilisearch API key must be a configured non-whitespace value")


def _validate_index_name(value: str) -> None:
    if len(value) > MAX_LIVE_INDEX_LENGTH or _LIVE_INDEX_PATTERN.fullmatch(value) is None:
        raise ValueError("live Meilisearch index must be a unique bounded integration index")


def new_unique_index_name() -> str:
    """Return a bounded, run-specific index name accepted by the cleanup gate."""

    index = f"{LIVE_INDEX_PREFIX}{uuid4().hex}"
    _validate_index_name(index)
    return index


def require_live_database_environment() -> str:
    """Validate the opt-in PostgreSQL lane without requiring Meilisearch."""

    if os.environ.get(LIVE_INTEGRATION_FLAG) != "1":
        raise RuntimeError(f"set {LIVE_INTEGRATION_FLAG}=1 to run live integration")
    database_url = _required_environment(DATABASE_URL_ENV)
    _validate_database_url(database_url)
    return database_url


def require_live_integration_environment() -> LiveIntegrationConfig:
    """Validate all live-service gates without connecting or exposing their values."""

    database_url = require_live_database_environment()
    meilisearch_url = _required_environment(MEILISEARCH_URL_ENV)
    meilisearch_api_key = _required_environment(MEILISEARCH_KEY_ENV)
    _validate_meilisearch_url(meilisearch_url)
    _validate_meilisearch_key(meilisearch_api_key)
    search_projection_database_url = _required_environment(SEARCH_PROJECTION_DATABASE_URL_ENV)
    _validate_database_url(
        search_projection_database_url,
        expected_role=SEARCH_PROJECTION_DATABASE_ROLE,
    )
    return LiveIntegrationConfig(
        database_url=database_url,
        search_projection_database_url=search_projection_database_url,
        meilisearch_url=meilisearch_url,
        meilisearch_api_key=meilisearch_api_key,
        meilisearch_index=new_unique_index_name(),
    )


def build_search_projection_session_factory(
    config: LiveIntegrationConfig,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Build the worker session from the dedicated search-projection DSN only."""

    engine = create_async_engine(config.search_projection_database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def _projection_states(
    executor: SearchProjectionExecutor, document_id: str
) -> tuple[str, ...]:
    async with executor.session_factory() as session:
        tasks = list(
            (
                await session.scalars(
                    select(SearchProjectionTask)
                    .where(SearchProjectionTask.document_id == document_id)
                    .order_by(SearchProjectionTask.version.asc())
                )
            ).all()
        )
    return tuple(task.state for task in tasks)


async def _projection_document_ids(
    executor: SearchProjectionExecutor,
) -> tuple[str, ...]:
    async with executor.session_factory() as session:
        document_ids = await session.scalars(select(SearchProjectionTask.document_id).distinct())
        return tuple(document_ids.all())


async def wait_for_projection_tasks(
    executor: SearchProjectionExecutor,
    document_id: str,
    *,
    timeout_seconds: float = 30.0,
) -> None:
    """Run the version-keyed executor until one document has no pending work."""

    if not document_id:
        raise ValueError("document_id must be configured")
    if timeout_seconds <= 0:
        raise ValueError("projection timeout must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        document_ids = await _projection_document_ids(executor)
        if any(candidate != document_id for candidate in document_ids):
            raise AssertionError(
                "live search projection backlog is not isolated to the requested document"
            )
        states = await _projection_states(executor, document_id)
        if not states:
            return
        if "dead_letter" in states:
            raise AssertionError("live search projection entered dead letter")
        if loop.time() >= deadline:
            raise AssertionError("live search projection did not settle before timeout")
        result = await executor.run_once()
        if result.document_id not in {None, document_id}:
            raise AssertionError(
                "live search projection processed a document outside the requested scope"
            )
        remaining = deadline - loop.time()
        if remaining > 0:
            await asyncio.sleep(min(_TASK_POLL_SECONDS, remaining))


async def _wait_for_meilisearch_task(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    task_uid: int,
) -> None:
    deadline = asyncio.get_running_loop().time() + _TASK_TIMEOUT_SECONDS
    headers = {"Authorization": f"Bearer {api_key}"}
    while True:
        try:
            response = await client.get(f"{base_url}/tasks/{task_uid}", headers=headers)
        except httpx.HTTPError:
            raise RuntimeError("live Meilisearch cleanup task could not be read") from None
        if response.status_code != 200:
            raise RuntimeError("live Meilisearch cleanup task returned an unexpected status")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("live Meilisearch cleanup task returned invalid JSON") from None
        state = payload.get("status") if isinstance(payload, dict) else None
        if state == "succeeded":
            return
        if state in {"failed", "canceled"}:
            raise RuntimeError("live Meilisearch index cleanup failed")
        if state not in {"enqueued", "processing"}:
            raise RuntimeError("live Meilisearch cleanup task returned an invalid state")
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RuntimeError("live Meilisearch index cleanup timed out")
        await asyncio.sleep(min(_TASK_POLL_SECONDS, remaining))


async def delete_meilisearch_index(config: LiveIntegrationConfig) -> None:
    """Delete only the run-specific index and wait for Meilisearch confirmation."""

    _validate_meilisearch_url(config.meilisearch_url)
    _validate_meilisearch_key(config.meilisearch_api_key)
    _validate_index_name(config.meilisearch_index)
    base_url = config.meilisearch_url.rstrip("/")
    headers = {"Authorization": f"Bearer {config.meilisearch_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.delete(
                f"{base_url}/indexes/{config.meilisearch_index}", headers=headers
            )
            if response.status_code == 404:
                return
            if response.status_code == 204:
                return
            if response.status_code not in {200, 202}:
                raise RuntimeError("live Meilisearch index cleanup returned an unexpected status")
            try:
                payload = response.json()
            except ValueError:
                raise RuntimeError("live Meilisearch cleanup returned invalid JSON") from None
            task_uid = payload.get("taskUid") if isinstance(payload, dict) else None
            if not isinstance(task_uid, int) or task_uid < 0:
                raise RuntimeError("live Meilisearch cleanup did not return a task")
            await _wait_for_meilisearch_task(client, base_url, config.meilisearch_api_key, task_uid)
    except httpx.HTTPError:
        raise RuntimeError("live Meilisearch index cleanup was unavailable") from None
