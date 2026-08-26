from __future__ import annotations

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db import (
    EXPECTED_ALEMBIC_HEAD,
    DatabaseSchemaNotCurrent,
    require_current_database_schema,
)
from app.main import create_app
from app.services.database_roles import API_DATABASE_ROLE
from app.services.deletion_journal import DeletionJournalError


class SearchReadiness:
    def __init__(self, *, enabled: bool, healthy: bool) -> None:
        self.enabled = enabled
        self.healthy = healthy
        self.health_calls = 0

    async def health(self) -> bool:
        self.health_calls += 1
        return self.healthy


class TaxonomyReadiness:
    async def check_ready(self, _session) -> None:
        return None


class ExactSearchNotInstalled:
    async def is_installed(self, _session) -> bool:
        return False


class JournalReadiness:
    def __init__(self) -> None:
        self.verify_calls = 0

    def verify(self) -> list[object]:
        self.verify_calls += 1
        return []


class DisposalProbe:
    def __init__(self) -> None:
        self.dispose_calls = 0

    async def dispose(self) -> None:
        self.dispose_calls += 1


async def _install_alembic_versions(app, *versions: str) -> None:
    async with app.state.engine.begin() as connection:
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL PRIMARY KEY)")
        )
        for version in versions:
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
                {"version": version},
            )


async def test_readyz_fails_closed_when_configured_search_is_unavailable(api_client) -> None:
    app, client = api_client
    search = SearchReadiness(enabled=True, healthy=False)
    app.state.search = search

    response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "database": "ok",
        "storage": "ok",
        "search": "unavailable",
    }
    assert search.health_calls == 1


async def test_readyz_allows_intentionally_unconfigured_local_search(api_client) -> None:
    app, client = api_client
    search = SearchReadiness(enabled=False, healthy=False)
    app.state.search = search

    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "storage": "ok",
        "search": "not_configured",
    }
    assert search.health_calls == 0


@pytest.mark.parametrize(
    "versions",
    [
        None,
        (),
        ("0026_moderation_evidence_snapshots",),
        ("9999_unknown_future",),
        (EXPECTED_ALEMBIC_HEAD, "branch_head"),
    ],
)
async def test_production_readyz_requires_one_exact_database_head(
    api_client, versions: tuple[str, ...] | None
) -> None:
    app, client = api_client
    if versions is not None:
        await _install_alembic_versions(app, *versions)
    app.state.settings.environment = "production"
    search = SearchReadiness(enabled=True, healthy=True)
    app.state.search = search

    response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "database": "unavailable",
        "storage": "ok",
        "search": "unknown",
    }
    assert search.health_calls == 0
    assert not any(version and version in response.text for version in versions or ())


async def test_production_readyz_accepts_the_one_exact_database_head(api_client) -> None:
    app, client = api_client
    await _install_alembic_versions(app, EXPECTED_ALEMBIC_HEAD)
    app.state.settings.environment = "production"
    app.state.taxonomy = TaxonomyReadiness()
    app.state.exact_search = ExactSearchNotInstalled()
    search = SearchReadiness(enabled=False, healthy=False)
    app.state.search = search

    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "storage": "ok",
        "search": "not_configured",
    }
    assert search.health_calls == 0


async def test_schema_readiness_attests_the_api_database_role(api_client, monkeypatch) -> None:
    app, _client = api_client
    await _install_alembic_versions(app, EXPECTED_ALEMBIC_HEAD)
    seen: list[str] = []

    async def attest(_session, expected_role: str) -> None:
        seen.append(expected_role)

    monkeypatch.setattr("app.db.require_database_role", attest)
    async with app.state.session_factory() as session:
        await require_current_database_schema(session)

    assert seen == [API_DATABASE_ROLE]


async def test_production_startup_checks_database_head_before_other_reconciliation(
    api_client,
) -> None:
    app, _client = api_client
    await _install_alembic_versions(app, "0026_moderation_evidence_snapshots")
    app.state.settings.environment = "production"
    journal = JournalReadiness()
    app.state.deletion_journal = journal
    original_engine = app.state.engine
    disposal = DisposalProbe()
    app.state.engine = disposal

    try:
        with pytest.raises(DatabaseSchemaNotCurrent, match="database schema is not current"):
            async with app.router.lifespan_context(app):
                pass
    finally:
        app.state.engine = original_engine

    assert journal.verify_calls == 0
    assert disposal.dispose_calls == 1


def test_create_app_does_not_build_engine_when_deletion_journal_setup_is_invalid(
    tmp_path, monkeypatch
) -> None:
    def fail_build_engine(*_args, **_kwargs):
        pytest.fail("engine construction must wait for fallible app construction")

    monkeypatch.setattr("app.main.build_engine", fail_build_engine)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'connectmd.db'}",
        storage_path=tmp_path / "storage",
        api_key_pepper="test-only-pepper-is-long-enough",
        deletion_journal_path=tmp_path / "deletion-journal",
    )

    with pytest.raises(DeletionJournalError, match="witness path is not configured"):
        create_app(settings)


def test_create_app_does_not_build_engine_when_exact_search_keyring_is_invalid(
    tmp_path, monkeypatch
) -> None:
    def fail_build_engine(*_args, **_kwargs):
        pytest.fail("engine construction must wait for fallible app construction")

    monkeypatch.setattr("app.main.build_engine", fail_build_engine)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'connectmd.db'}",
        storage_path=tmp_path / "storage",
        api_key_pepper="test-only-pepper-is-long-enough",
        exact_search_cursor_keyring="not-json",
    )

    with pytest.raises(ValueError, match="exact search cursor keyring is invalid"):
        create_app(settings)
