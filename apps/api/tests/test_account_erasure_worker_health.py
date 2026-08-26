from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import account_erasure_worker, cli
from app.account_erasure_worker import _refresh_health_heartbeat, run
from app.auth import AuthenticationUnavailable
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.models import AccountErasureItem, AccountLifecycle, Base
from app.services.account_erasure import HttpClerkLifecycleProvider
from app.services.database_roles import (
    ACCOUNT_ERASURE_DATABASE_ROLE,
    DatabaseRoleContractError,
)
from app.services.deletion_journal import DeletionCommitmentJournal, DeletionJournalError


class ReadinessProbe:
    def __init__(self) -> None:
        self.ready = True
        self.calls = 0

    async def check_ready(self) -> None:
        self.calls += 1
        if not self.ready:
            raise RuntimeError("simulated readiness failure")


class ProviderClient:
    def __init__(self, *, authorized: bool) -> None:
        self.authorized = authorized
        self.calls: list[tuple[str, dict[str, str], dict[str, int]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str], params: dict[str, int]):
        self.calls.append((url, headers, params))
        request = httpx.Request("GET", url)
        response = httpx.Response(200 if self.authorized else 401, request=request)
        response.raise_for_status()
        return response


@pytest.mark.asyncio
async def test_worker_disposes_engine_when_initial_role_attestation_fails(
    monkeypatch, tmp_path
) -> None:
    heartbeat = tmp_path / "lifecycle-health.json"
    configured: list[str] = []
    attested: list[str] = []

    class _Settings:
        account_lifecycle_enabled = True
        account_lifecycle_heartbeat_path = heartbeat

        def require_clerk_backend_configuration(self) -> None:
            return None

        def require_database_role_configuration(self, expected_role: str) -> None:
            configured.append(expected_role)

    class _SessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Engine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = _Engine()

    def sessions() -> _SessionContext:
        return _SessionContext()

    async def reject_role(_session: object, expected_role: str) -> None:
        attested.append(expected_role)
        raise DatabaseRoleContractError("database role contract is not satisfied")

    monkeypatch.setattr("app.account_erasure_worker.get_settings", _Settings)
    monkeypatch.setattr("app.account_erasure_worker.build_engine", lambda _settings: engine)
    monkeypatch.setattr(
        "app.account_erasure_worker.build_session_factory",
        lambda _settings, _engine: sessions,
    )
    monkeypatch.setattr("app.account_erasure_worker.require_database_role", reject_role)

    with pytest.raises(DatabaseRoleContractError, match="role contract"):
        await run()

    assert configured == [ACCOUNT_ERASURE_DATABASE_ROLE]
    assert attested == [ACCOUNT_ERASURE_DATABASE_ROLE]
    assert engine.disposed is True
    assert not heartbeat.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entrypoint", "args"),
    [
        (account_erasure_worker.run, ()),
        (cli.run_account_erasure, (argparse.Namespace(limit=1),)),
    ],
)
async def test_erasure_entrypoints_require_provider_configuration_before_engine_or_work(
    monkeypatch, entrypoint, args: tuple[object, ...]
) -> None:
    attempted: list[str] = []

    class _Settings:
        account_lifecycle_enabled = True

        def require_clerk_backend_configuration(self) -> None:
            attempted.append("provider-configuration")
            raise ValueError("provider credentials are unavailable")

    def unexpected_engine(_settings: object) -> None:
        attempted.append("engine")
        raise AssertionError("engine must not be created before provider configuration")

    def unexpected_provider(*_args: object) -> None:
        attempted.append("provider")
        raise AssertionError("provider must not be created before provider configuration")

    monkeypatch.setattr(account_erasure_worker, "get_settings", _Settings)
    monkeypatch.setattr(cli, "get_settings", _Settings)
    monkeypatch.setattr(account_erasure_worker, "build_engine", unexpected_engine)
    monkeypatch.setattr(cli, "build_engine", unexpected_engine)
    monkeypatch.setattr(account_erasure_worker, "HttpClerkLifecycleProvider", unexpected_provider)
    monkeypatch.setattr(cli, "HttpClerkLifecycleProvider", unexpected_provider)

    with pytest.raises(ValueError, match="provider credentials"):
        await entrypoint(*args)

    assert attempted == ["provider-configuration"]


def test_lifecycle_poll_interval_cannot_outlive_heartbeat_freshness() -> None:
    with pytest.raises(ValueError):
        Settings(account_lifecycle_poll_seconds=31)


@pytest.mark.asyncio
async def test_provider_readiness_authenticates_without_retaining_content(
    monkeypatch, tmp_path
) -> None:
    from app.services import account_erasure as account_erasure_module

    settings = Settings(
        storage_path=tmp_path,
        api_key_pepper="test-only-pepper-is-long-enough",
        clerk_backend_secret="p" * 32,
        clerk_backend_base_url="https://clerk.example.test",
    )
    client = ProviderClient(authorized=True)
    monkeypatch.setattr(account_erasure_module.httpx, "AsyncClient", lambda **_: client)
    await HttpClerkLifecycleProvider(settings).check_ready()
    assert client.calls == [
        (
            "https://clerk.example.test/v1/users",
            {"Authorization": f"Bearer {'p' * 32}"},
            {"limit": 1},
        )
    ]

    denied = ProviderClient(authorized=False)
    monkeypatch.setattr(account_erasure_module.httpx, "AsyncClient", lambda **_: denied)
    with pytest.raises(AuthenticationUnavailable, match="readiness"):
        await HttpClerkLifecycleProvider(settings).check_ready()


@pytest.mark.asyncio
async def test_provider_never_sends_secret_to_unapproved_backend(monkeypatch, tmp_path) -> None:
    from app.services import account_erasure as account_erasure_module

    settings = Settings(
        storage_path=tmp_path,
        clerk_backend_secret="p" * 32,
        clerk_backend_base_url="https://attacker.example.com",
    )

    def unexpected_client(**_kwargs):
        raise AssertionError("unapproved backend must be rejected before an HTTP client is opened")

    monkeypatch.setattr(account_erasure_module.httpx, "AsyncClient", unexpected_client)

    with pytest.raises(AuthenticationUnavailable, match="not configured"):
        await HttpClerkLifecycleProvider(settings).check_ready()
    assert (
        await HttpClerkLifecycleProvider(settings).delete_user(subject="user_test")
        == "permanent_unsupported"
    )


@pytest.mark.asyncio
async def test_provider_percent_encodes_user_identifier_path_segment(monkeypatch, tmp_path) -> None:
    from app.services import account_erasure as account_erasure_module

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204 if request.method == "DELETE" else 404)

    real_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(account_erasure_module.httpx, "AsyncClient", client_factory)
    settings = Settings(
        storage_path=tmp_path,
        clerk_backend_secret="p" * 32,
        clerk_backend_base_url="https://clerk.example.test",
    )

    outcome = await HttpClerkLifecycleProvider(settings).delete_user(subject="user/unsafe?#segment")

    assert outcome == "deleted"
    assert [str(request.url) for request in requests] == [
        "https://clerk.example.test/v1/users/user%2Funsafe%3F%23segment",
        "https://clerk.example.test/v1/users/user%2Funsafe%3F%23segment",
    ]


@pytest.mark.asyncio
async def test_heartbeat_is_content_free_bounded_and_dependency_gated(
    tmp_path, monkeypatch
) -> None:
    now = datetime.now(UTC)
    heartbeat = tmp_path / "lifecycle-health.json"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'health.db'}",
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
        account_lifecycle_heartbeat_path=heartbeat,
    )
    journal = DeletionCommitmentJournal(settings)
    journal.initialize(created_at=now)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add(
            AccountLifecycle(
                id="health-lifecycle",
                subject_hmac="a" * 64,
                request_idempotency_hmac="b" * 64,
                state="confirmation_pending",
                provider_state="pending",
                backup_state="expiry_pending",
                policy_version="account-lifecycle-v1",
                requested_at=now,
            )
        )
        session.add(
            AccountErasureItem(
                id="health-item",
                deletion_id="health-lifecycle",
                resource_type="document",
                resource_id="opaque-resource",
                phase="delete_row",
                disposition="delete",
                state="queued",
                attempts=0,
                available_at=now - timedelta(seconds=5),
                created_at=now - timedelta(seconds=5),
                updated_at=now,
            )
        )
        await session.commit()

    provider = ReadinessProbe()
    search = ReadinessProbe()
    attested: list[str] = []

    async def attest(_session, expected_role: str) -> None:
        attested.append(expected_role)

    monkeypatch.setattr("app.account_erasure_worker.require_database_role", attest)
    settings.environment = "production"
    try:
        payload = await _refresh_health_heartbeat(
            session_factory, journal, provider, search, settings, now=now
        )
        assert attested == [ACCOUNT_ERASURE_DATABASE_ROLE]
        settings.environment = "development"
        assert payload == {
            "state": "healthy",
            "checked_at": now.isoformat(),
            "database_ready": True,
            "deletion_journal_ready": True,
            "provider_ready": True,
            "search_ready": True,
            "deletion_commitment_count": 0,
            "backlog_count": 1,
            "eligible_count": 1,
            "dead_letter_count": 0,
            "failed_lifecycle_count": 0,
            "oldest_eligible_age_seconds": 5,
        }
        assert provider.calls == search.calls == 1
        if os.name == "posix":
            assert stat.S_IMODE(heartbeat.stat().st_mode) == 0o600
        serialized = json.loads(heartbeat.read_text(encoding="utf-8"))
        assert serialized == payload
        assert not {"subject_hmac", "resource_id", "deletion_id", "receipt_hmac"} & set(serialized)

        async with session_factory() as session:
            item = await session.get(AccountErasureItem, "health-item")
            lifecycle = await session.get(AccountLifecycle, "health-lifecycle")
            assert item is not None and lifecycle is not None
            item.state = "dead_letter"
            item.available_at = None
            await session.commit()
        degraded = await _refresh_health_heartbeat(
            session_factory, journal, provider, search, settings, now=now
        )
        assert degraded["state"] == "degraded"
        assert degraded["dead_letter_count"] == 1
        assert degraded["failed_lifecycle_count"] == 0

        async with session_factory() as session:
            lifecycle = await session.get(AccountLifecycle, "health-lifecycle")
            assert lifecycle is not None
            lifecycle.state = "erasing"
            await session.commit()
        with pytest.raises(DeletionJournalError, match="commitment sets"):
            await _refresh_health_heartbeat(
                session_factory, journal, provider, search, settings, now=now
            )
        assert not heartbeat.exists()
        async with session_factory() as session:
            lifecycle = await session.get(AccountLifecycle, "health-lifecycle")
            assert lifecycle is not None
            lifecycle.state = "confirmation_pending"
            await session.commit()

        provider.ready = False
        with pytest.raises(RuntimeError, match="readiness"):
            await _refresh_health_heartbeat(
                session_factory, journal, provider, search, settings, now=now
            )
        assert not heartbeat.exists()
        provider.ready = True
        search.ready = False
        with pytest.raises(RuntimeError, match="readiness"):
            await _refresh_health_heartbeat(
                session_factory, journal, provider, search, settings, now=now
            )
        assert not heartbeat.exists()
    finally:
        await engine.dispose()
