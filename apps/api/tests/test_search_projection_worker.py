from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Document, SearchProjectionTask
from app.search_projection_worker import (
    SearchProjectionWorkerSettings,
    _refresh_health_heartbeat,
    run_worker,
)
from app.services.database_roles import (
    SEARCH_PROJECTION_DATABASE_ROLE,
    DatabaseRoleContractError,
)
from app.services.search import SearchDeleteAttestation, SearchUnavailable
from app.services.search_projection import (
    SearchProjectionExecutor,
    count_dead_letters,
    list_dead_letters,
    retry_dead_letter,
)

from .helpers import profile_markdown


class FakeSearch:
    enabled = True

    def __init__(self, *, failures: int = 0, ready: bool = True, configured: bool = True) -> None:
        self.failures = failures
        self.ready = ready
        self.configured = configured
        self.indexed: list[tuple[str, int, str]] = []
        self.deleted: list[str] = []

    async def index(self, document, markdown: str) -> None:
        if self.failures:
            self.failures -= 1
            raise SearchUnavailable("simulated projection outage")
        self.indexed.append((document.id, document.current_version, markdown))

    async def delete_document(self, document_id: str) -> SearchDeleteAttestation:
        if self.failures:
            self.failures -= 1
            raise SearchUnavailable("simulated projection outage")
        self.deleted.append(document_id)
        return SearchDeleteAttestation(
            configured=self.configured,
            state="deleted" if self.configured else "unconfigured",
        )

    async def check_ready(self) -> None:
        if not self.ready:
            raise SearchUnavailable("simulated invalid projection credential")


class BrokenDatabaseExecutor:
    async def health_snapshot(self, *, now=None):
        raise OSError("simulated invalid database credential")


def test_worker_poll_interval_cannot_exceed_heartbeat_freshness_window(tmp_path) -> None:
    with pytest.raises(ValueError):
        SearchProjectionWorkerSettings(
            database_url="sqlite+aiosqlite://",
            storage_path=tmp_path,
            search_projection_poll_seconds=31,
        )


def test_production_worker_requires_its_dedicated_database_login(tmp_path) -> None:
    settings = SearchProjectionWorkerSettings(
        environment="production",
        database_url=("postgresql+asyncpg://connectmd:do-not-render@postgres/connectmd"),
        storage_path=tmp_path,
    )

    with pytest.raises(ValueError, match="database configuration is invalid") as exc_info:
        settings.require_runtime_configuration()

    assert "do-not-render" not in str(exc_info.value)


async def test_run_worker_disposes_engine_when_initial_role_attestation_fails(
    tmp_path, monkeypatch, capsys
) -> None:
    settings = SearchProjectionWorkerSettings(
        database_url="sqlite+aiosqlite://",
        storage_path=tmp_path,
        meilisearch_url="http://meilisearch:7700",
        meilisearch_api_key="restricted-projection-key",
        search_projection_heartbeat_path=tmp_path / "projection-health.json",
    )

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

    async def reject_role(_session: object, _expected_role: str) -> None:
        raise DatabaseRoleContractError("database role contract is not satisfied")

    monkeypatch.setattr(
        "app.search_projection_worker._session_factory", lambda _settings: (sessions, engine)
    )
    monkeypatch.setattr("app.search_projection_worker.require_database_role", reject_role)

    with pytest.raises(DatabaseRoleContractError, match="role contract"):
        await run_worker(settings)

    assert engine.disposed is True
    assert not settings.search_projection_heartbeat_path.exists()
    assert capsys.readouterr().out == "event=search_projection_worker_starting\n"


def executor(app, search: FakeSearch, **overrides: int) -> SearchProjectionExecutor:
    return SearchProjectionExecutor(
        app.state.session_factory,
        app.state.store,
        search,  # type: ignore[arg-type]
        worker_id="projection-test-worker",
        lease_seconds=overrides.get("lease_seconds", 60),
        max_attempts=overrides.get("max_attempts", 8),
        max_backoff_seconds=overrides.get("max_backoff_seconds", 300),
    )


async def test_create_update_and_idempotent_replay_leave_version_keyed_tasks(api_client) -> None:
    app, client = api_client
    api_search = FakeSearch(failures=1)
    app.state.search = api_search
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-create-0001"},
    )
    assert created.status_code == 201, created.text
    assert "Idempotency-Replayed" not in created.headers
    assert created.headers["X-Connectmd-Search"] == "queued"
    replay = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-create-0001"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.headers["X-Connectmd-Search"] == "queued"

    updated = await client.put(
        "/v1/profiles/ada-lovelace",
        json={
            "markdown": created.json()["markdown"].replace(
                "headline: Backend engineer", "headline: Projection engineer"
            )
        },
        headers={
            "Idempotency-Key": "projection-update-0001",
            "If-Match": created.headers["ETag"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.headers["X-Connectmd-Search"] == "queued"
    assert api_search.failures == 1
    assert api_search.indexed == []
    assert api_search.deleted == []
    async with app.state.session_factory() as session:
        tasks = (
            await session.scalars(
                select(SearchProjectionTask).order_by(SearchProjectionTask.version)
            )
        ).all()
    assert [(task.document_id, task.version) for task in tasks] == [
        (created.json()["id"], 1),
        (created.json()["id"], 2),
    ]
    assert all(task.state == "pending" and task.attempts == 0 for task in tasks)


async def test_mcp_update_queues_projection_without_api_process_indexing(api_client) -> None:
    app, client = api_client
    api_search = FakeSearch()
    app.state.search = api_search
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-mcp-profile-create"},
    )
    assert created.status_code == 201, created.text

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "update_document",
            "arguments": {
                "kind": "profile",
                "identifier": "ada-lovelace",
                "markdown": created.json()["markdown"].replace(
                    "headline: Backend engineer", "headline: MCP projection engineer"
                ),
                "if_match": created.headers["ETag"],
                "idempotency_key": "projection-mcp-update-0001",
            },
        },
    }
    updated = await client.post("/mcp", json=payload)
    assert updated.status_code == 200, updated.text
    assert "Idempotency-Replayed" not in updated.headers
    assert updated.headers["X-Connectmd-Search"] == "queued"
    replay = await client.post("/mcp", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.headers["X-Connectmd-Search"] == "queued"
    assert api_search.indexed == []
    assert api_search.deleted == []


async def test_stale_task_is_superseded_and_only_current_public_version_is_projected(
    api_client,
) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-stale-profile-create"},
    )
    assert created.status_code == 201, created.text
    updated = await client.put(
        "/v1/profiles/ada-lovelace",
        json={
            "markdown": created.json()["markdown"].replace(
                "headline: Backend engineer", "headline: Current projection"
            )
        },
        headers={
            "If-Match": created.headers["ETag"],
            "Idempotency-Key": "projection-stale-profile-update",
        },
    )
    assert updated.status_code == 200, updated.text
    search = FakeSearch()
    worker = executor(app, search)

    stale = await worker.run_once()
    current = await worker.run_once()

    assert stale.action == "superseded"
    assert current.action == "indexed"
    assert [
        (version, "Current projection" in markdown) for _, version, markdown in search.indexed
    ] == [(2, True)]


async def test_public_to_private_transition_removes_projection_without_indexing_private_bytes(
    api_client,
) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-private-profile-create"},
    )
    assert created.status_code == 201, created.text
    search = FakeSearch()
    worker = executor(app, search)
    assert (await worker.run_once()).action == "indexed"

    private_markdown = created.json()["markdown"].replace(
        "visibility: public", "visibility: private"
    )
    updated = await client.put(
        "/v1/profiles/ada-lovelace",
        json={"markdown": private_markdown},
        headers={
            "If-Match": created.headers["ETag"],
            "Idempotency-Key": "projection-private-profile-update",
        },
    )
    assert updated.status_code == 200, updated.text
    assert (await worker.run_once()).action == "removed"
    assert search.deleted == [created.json()["id"]]
    assert all("visibility: private" not in markdown for _, _, markdown in search.indexed)


async def test_out_of_order_public_task_cannot_reintroduce_after_private_transition(
    api_client,
) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-out-of-order-profile-create"},
    )
    assert created.status_code == 201, created.text
    private_markdown = created.json()["markdown"].replace(
        "visibility: public", "visibility: private"
    )
    updated = await client.put(
        "/v1/profiles/ada-lovelace",
        json={"markdown": private_markdown},
        headers={
            "If-Match": created.headers["ETag"],
            "Idempotency-Key": "projection-out-of-order-profile-update",
        },
    )
    assert updated.status_code == 200, updated.text
    search = FakeSearch()
    worker = executor(app, search)

    assert (await worker.run_once()).action == "superseded"
    assert (await worker.run_once()).action == "removed"
    assert search.indexed == []
    assert search.deleted == [created.json()["id"]]


async def test_canonical_delete_leaves_tombstone_task_that_removes_projection(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-delete-profile-create"},
    )
    assert created.status_code == 201, created.text
    async with app.state.session_factory() as session:
        document = await session.get(Document, created.json()["id"])
        assert document is not None
        await session.delete(document)
        await session.commit()

    search = FakeSearch()
    result = await executor(app, search).run_once()
    assert result.action == "removed_missing"
    assert search.deleted == [created.json()["id"]]
    async with app.state.session_factory() as session:
        assert await session.get(SearchProjectionTask, (created.json()["id"], 1)) is None


async def test_missing_document_tombstone_survives_unconfigured_search(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-unconfigured-profile-create"},
    )
    assert created.status_code == 201, created.text
    async with app.state.session_factory() as session:
        document = await session.get(Document, created.json()["id"])
        assert document is not None
        await session.delete(document)
        await session.commit()

    search = FakeSearch(configured=False)
    worker = executor(app, search)
    first = await worker.run_once()
    assert first.action == "retry_scheduled"
    assert first.error_code == "search_unconfigured"
    async with app.state.session_factory() as session:
        assert await session.get(SearchProjectionTask, (created.json()["id"], 1)) is not None

    search.configured = True
    second = await worker.run_once(now=datetime.now(UTC) + timedelta(seconds=3))
    assert second.action == "removed_missing"
    assert search.deleted == [created.json()["id"], created.json()["id"]]
    async with app.state.session_factory() as session:
        assert await session.get(SearchProjectionTask, (created.json()["id"], 1)) is None


async def test_failure_retries_with_bounded_metadata_and_recovers_expired_lease(
    api_client,
) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-retry-profile-create"},
    )
    assert created.status_code == 201, created.text
    search = FakeSearch(failures=1)
    worker = executor(app, search, lease_seconds=15, max_backoff_seconds=4)
    failed = await worker.run_once()
    assert failed.action == "retry_scheduled"
    assert failed.error_code == "search_unavailable"
    async with app.state.session_factory() as session:
        task = await session.get(SearchProjectionTask, (created.json()["id"], 1))
        assert task is not None
        task.state = "leased"
        task.claimed_by = "crashed-worker"
        task.claim_token = "expired-claim"
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        task.available_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    recovered = await worker.run_once(now=datetime.now(UTC))
    assert recovered.action == "indexed"
    assert recovered.attempts == 2
    async with app.state.session_factory() as session:
        assert await session.get(SearchProjectionTask, (created.json()["id"], 1)) is None


async def test_dead_letter_is_content_free_and_operator_can_requeue_exact_version(
    api_client,
) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-dead-letter-profile-create"},
    )
    assert created.status_code == 201, created.text
    search = FakeSearch(failures=1)
    worker = executor(app, search, max_attempts=1)
    failed = await worker.run_once()
    assert failed.action == "dead_lettered"
    assert failed.error_code == "search_unavailable"

    dead = await list_dead_letters(app.state.session_factory)
    assert len(dead) == 1
    assert await count_dead_letters(app.state.session_factory) == 1
    assert dead[0].last_error_code == "search_unavailable"
    assert not hasattr(dead[0], "markdown")
    assert await retry_dead_letter(
        app.state.session_factory,
        document_id=created.json()["id"],
        version=1,
    )
    assert (await worker.run_once()).action == "indexed"


async def test_new_version_enqueue_clears_only_superseded_dead_letters(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-new-version-profile-create"},
    )
    assert created.status_code == 201, created.text
    worker = executor(app, FakeSearch(failures=1), max_attempts=1)
    assert (await worker.run_once()).action == "dead_lettered"

    updated = await client.put(
        "/v1/profiles/ada-lovelace",
        json={
            "markdown": created.json()["markdown"].replace(
                "headline: Backend engineer", "headline: New canonical version"
            )
        },
        headers={
            "If-Match": created.headers["ETag"],
            "Idempotency-Key": "projection-new-version-profile-update",
        },
    )
    assert updated.status_code == 200, updated.text
    async with app.state.session_factory() as session:
        tasks = (
            await session.scalars(
                select(SearchProjectionTask).order_by(SearchProjectionTask.version)
            )
        ).all()
    assert [(task.version, task.state) for task in tasks] == [(2, "pending")]


async def test_failure_state_does_not_dead_letter_a_newly_superseded_claim(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-superseded-profile-create"},
    )
    assert created.status_code == 201, created.text
    worker = executor(app, FakeSearch(), max_attempts=1)
    claimed = await worker._claim(datetime.now(UTC))
    assert claimed is not None

    async with app.state.session_factory() as session:
        document = await session.get(Document, created.json()["id"])
        assert document is not None
        document.current_version = 2
        await session.commit()

    result = await worker._fail(claimed, "search_unavailable", True)
    assert result.action == "superseded"
    async with app.state.session_factory() as session:
        assert await session.get(SearchProjectionTask, (created.json()["id"], 1)) is None


async def test_health_heartbeat_requires_database_and_authenticated_meili(
    api_client, tmp_path, monkeypatch
) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "projection-health-profile-create"},
    )
    assert created.status_code == 201, created.text
    search = FakeSearch()
    worker = executor(app, search)
    heartbeat = tmp_path / "projection-health.json"
    settings = SearchProjectionWorkerSettings(
        database_url="sqlite+aiosqlite://",
        storage_path=tmp_path,
        meilisearch_url="http://meilisearch:7700",
        meilisearch_api_key="restricted-projection-key",
        search_projection_heartbeat_path=heartbeat,
    )
    attested: list[str] = []

    async def attest(_session, expected_role: str) -> None:
        attested.append(expected_role)

    monkeypatch.setattr("app.search_projection_worker.require_database_role", attest)
    settings.environment = "production"

    payload = await _refresh_health_heartbeat(worker, search, settings)
    assert attested == [SEARCH_PROJECTION_DATABASE_ROLE]
    settings.environment = "development"
    assert payload["state"] == "healthy"
    assert payload["backlog_count"] == 1
    assert payload["eligible_count"] == 1
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["dead_letter_count"] == 0

    search.failures = 1
    assert (await executor(app, search, max_attempts=1).run_once()).action == "dead_lettered"
    payload = await _refresh_health_heartbeat(worker, search, settings)
    assert payload["state"] == "degraded"
    assert payload["dead_letter_count"] == 1

    search.ready = False
    try:
        await _refresh_health_heartbeat(worker, search, settings)
    except SearchUnavailable:
        pass
    else:
        raise AssertionError("invalid Meilisearch credentials must fail health")
    assert not heartbeat.exists()

    heartbeat.write_text('{"state":"healthy"}', encoding="utf-8")
    search.ready = True
    with pytest.raises(OSError):
        await _refresh_health_heartbeat(
            BrokenDatabaseExecutor(),  # type: ignore[arg-type]
            search,
            settings,
        )
    assert not heartbeat.exists()
