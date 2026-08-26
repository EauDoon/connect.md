from __future__ import annotations

import asyncio
import json
import os
from base64 import b64encode
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import HttpUrl
from sqlalchemy import delete, exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, optional_principal, require_principal
from app.config import Settings
from app.main import create_app
from app.markdown import client_template, validate_canonical
from app.models import (
    AgentGrant,
    ApiKey,
    Application,
    ApplicationRateBucket,
    ChangeEvent,
    Document,
    DocumentVersion,
    IdempotencyRecord,
    Job,
    Notification,
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
    PublicTaxonomyDocumentSnapshot,
    PublicTaxonomyMembership,
    PublicTaxonomyTerm,
    SearchProjectionTask,
)
from app.services.artifact_durability import (
    acquire_artifact_intent_lock,
    derive_artifact_intent_uuid,
)
from app.services.database_roles import SEARCH_PROJECTION_DATABASE_ROLE, require_database_role
from app.services.deletion_journal import DeletionCommitmentJournal
from app.services.search_projection import SearchProjectionExecutor

from .helpers import profile_markdown
from .live_integration_support import (
    DATABASE_URL_ENV,
    LIVE_INDEX_PREFIX,
    LIVE_INTEGRATION_FLAG,
    MEILISEARCH_KEY_ENV,
    MEILISEARCH_URL_ENV,
    SEARCH_PROJECTION_DATABASE_URL_ENV,
    build_search_projection_session_factory,
    delete_meilisearch_index,
    new_unique_index_name,
    require_live_database_environment,
    require_live_integration_environment,
)

live_integration = pytest.mark.skipif(
    os.environ.get(LIVE_INTEGRATION_FLAG) != "1",
    reason="live PostgreSQL/Meilisearch integration is opt-in",
)


async def _owner() -> Principal:
    return _human("ci_owner")


def _human(subject: str, *, impersonated: bool = False) -> Principal:
    return Principal(
        subject=subject,
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        is_impersonated=impersonated,
    )


def _set_principal(app, subject: str, *, impersonated: bool = False) -> None:
    async def current() -> Principal:
        return _human(subject, impersonated=impersonated)

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


def _production_live_settings(live, tmp_path, suffix: str) -> Settings:
    """Build explicit production settings for the lifespan/role gate test."""

    settings = Settings(
        environment="production",
        database_url=live.database_url,
        storage_path=tmp_path / f"exact-search-storage-{suffix}",
        ingest_jobs_path=tmp_path / f"ingest-jobs-{suffix}",
        api_key_pepper="ci-only-pepper-is-at-least-thirty-two-characters",
        meilisearch_url=live.meilisearch_url,
        meilisearch_api_key=live.meilisearch_api_key,
        meilisearch_index=live.meilisearch_index,
        exact_search_cursor_keyring='[{"kid":"ci-v1","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAw"}]',
        clerk_jwks_url=HttpUrl("https://api.clerk.com"),
        clerk_issuer="https://clerk.example.test",
        clerk_authorized_parties=["https://connect.md"],
        public_base_url=HttpUrl("https://connect.md"),
        verification_reviewer_id="ci-verification-reviewer",
        verification_reviewer_role="recruiting_verifier",
        post_moderator_id="ci-post-moderator",
        post_moderator_role="content_moderator",
        appeal_reviewer_id="ci-appeal-reviewer",
        appeal_reviewer_role="appeal_reviewer",
        lifecycle_hmac_key="ci-lifecycle-hmac-key-is-at-least-thirty-two-bytes",
        lifecycle_aead_key="ci-lifecycle-aead-key-is-at-least-thirty-two-bytes",
        deletion_journal_path=tmp_path / f"deletion-journal-{suffix}",
        deletion_witness_path=tmp_path / f"deletion-witness-{suffix}",
        deletion_witness_hmac_key="ci-deletion-witness-key-is-at-least-thirty-two-bytes",
    )
    DeletionCommitmentJournal(settings).initialize()
    return settings


async def _post_after_barrier(
    app,
    barrier: asyncio.Barrier,
    path: str,
    *,
    body: Mapping[str, object],
    headers: dict[str, str],
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://integration"
    ) as client:
        await barrier.wait()
        return await client.post(path, json=body, headers=headers)


async def _post_without_body_after_barrier(
    app,
    barrier: asyncio.Barrier,
    path: str,
    *,
    headers: dict[str, str],
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://integration"
    ) as client:
        await barrier.wait()
        return await client.post(path, headers=headers)


async def _wait_for_application_transition_lock_waiters(
    session: AsyncSession,
    *,
    gate_started_at: datetime,
    minimum: int = 2,
) -> None:
    """Prove both HTTP transactions reached the PostgreSQL organization lock."""

    deadline = asyncio.get_running_loop().time() + 10
    statement = text(
        """
        SELECT count(*)
        FROM pg_stat_activity
        WHERE pid <> pg_backend_pid()
          AND datname = current_database()
          AND usename = current_user
          AND state = 'active'
          AND wait_event_type = 'Lock'
          AND query_start >= :gate_started_at
          AND lower(query) LIKE '%from organizations%'
          AND lower(query) LIKE '%for update%'
        """
    )
    while asyncio.get_running_loop().time() < deadline:
        waiters = await session.scalar(
            statement,
            {"gate_started_at": gate_started_at},
        )
        if int(waiters or 0) >= minimum:
            return
        await asyncio.sleep(0.05)
    pytest.fail(
        "both application transitions did not reach the PostgreSQL lock gate",
        pytrace=False,
    )


async def _run_scoped_projection_task(
    executor: SearchProjectionExecutor,
    *,
    document_id: str,
    version: int,
    timeout_seconds: float = 30.0,
) -> None:
    """Consume exactly one task created for this test's document version."""

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    observed = False
    indexed = False
    while True:
        async with executor.session_factory() as session:
            tasks = list(
                (
                    await session.scalars(
                        select(SearchProjectionTask).order_by(
                            SearchProjectionTask.document_id.asc(),
                            SearchProjectionTask.version.asc(),
                        )
                    )
                ).all()
            )
        if not tasks:
            if not observed or not indexed:
                pytest.fail(
                    "the expected run-scoped projection task was not consumed",
                    pytrace=False,
                )
            return
        if len(tasks) != 1:
            pytest.fail(
                "live projection work was not isolated to one run-scoped task",
                pytrace=False,
            )
        task = tasks[0]
        if task.document_id != document_id or task.version != version:
            pytest.fail(
                "live projection work included a pre-existing or foreign task",
                pytrace=False,
            )
        if not observed:
            assert task.state == "pending"
            assert task.attempts == 0
            assert task.claimed_by is None
            assert task.claim_token is None
            observed = True
        if task.state == "dead_letter":
            pytest.fail("live search projection entered dead letter", pytrace=False)
        if asyncio.get_running_loop().time() >= deadline:
            pytest.fail("live search projection did not settle before timeout", pytrace=False)
        result = await executor.run_once()
        if result.document_id not in {None, document_id}:
            pytest.fail("live projection consumed a foreign task", pytrace=False)
        if result.action == "indexed" and result.document_id == document_id:
            assert result.version == version
            indexed = True
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining > 0:
            await asyncio.sleep(min(0.1, remaining))


def _assert_status(response: Any, expected: int) -> None:
    if response.status_code != expected:
        pytest.fail(f"expected HTTP {expected}, got {response.status_code}", pytrace=False)


def _assert_body_contains(response: Any, marker: str) -> None:
    if marker not in response.text:
        pytest.fail("response omitted the expected bounded error marker", pytrace=False)


def _assert_body_excludes(response: Any, marker: str) -> None:
    if marker in response.text:
        pytest.fail("response leaked a protected fixture marker", pytrace=False)


def _assert_text_excludes(value: str | None, marker: str) -> None:
    if value is not None and marker in value:
        pytest.fail("persisted state leaked a protected fixture marker", pytrace=False)


async def _cleanup_live_rows(
    app,
    *,
    owner_id: str,
    idempotency_keys: set[str],
    api_key_ids: set[str] | None = None,
    agent_grant_ids: set[str] | None = None,
    organization_id: str | None = None,
    verification_id: str | None = None,
    applicant_owner_id: str | None = None,
) -> None:
    """Delete only this test's unique rows and exact promoted private artifacts."""

    evidence_paths: list[str] = []
    application_artifacts: list[tuple[str, str, int]] = []
    async with app.state.session_factory() as session:
        organization_ids = {organization_id} if organization_id is not None else set()
        organization_ids.update(
            (
                await session.scalars(
                    select(Organization.id).where(Organization.owner_id == owner_id)
                )
            ).all()
        )
        job_ids = set(
            (
                await session.scalars(
                    select(Job.id).where(Job.organization_id.in_(organization_ids))
                )
            ).all()
        )
        application_ids: set[str] = set()
        if job_ids:
            applications = (
                await session.scalars(select(Application).where(Application.job_id.in_(job_ids)))
            ).all()
            application_ids.update(application.id for application in applications)
            application_artifacts.extend(
                (
                    application.snapshot_storage_path,
                    application.snapshot_sha256,
                    application.snapshot_size_bytes,
                )
                for application in applications
                if application.snapshot_storage_path is not None
                and application.snapshot_size_bytes is not None
            )
        verification_ids = {verification_id} if verification_id is not None else set()
        verification_ids.update(
            (
                await session.scalars(
                    select(OrganizationVerification.id).where(
                        OrganizationVerification.organization_id.in_(organization_ids)
                    )
                )
            ).all()
        )
        if verification_ids:
            evidence_paths.extend(
                (
                    await session.scalars(
                        select(OrganizationVerificationEvidence.storage_path).where(
                            OrganizationVerificationEvidence.verification_id.in_(verification_ids)
                        )
                    )
                ).all()
            )
            await session.execute(
                delete(OrganizationVerificationEvent).where(
                    OrganizationVerificationEvent.verification_id.in_(verification_ids)
                )
            )
            await session.execute(
                delete(OrganizationVerificationEvidence).where(
                    OrganizationVerificationEvidence.verification_id.in_(verification_ids)
                )
            )
            await session.execute(
                delete(OrganizationVerification).where(
                    OrganizationVerification.id.in_(verification_ids)
                )
            )
        if application_ids:
            await session.execute(
                delete(Notification).where(
                    Notification.resource_type == "application",
                    Notification.resource_id.in_(application_ids),
                )
            )
            await session.execute(
                delete(ChangeEvent).where(
                    ChangeEvent.resource_type == "application",
                    ChangeEvent.resource_id.in_(application_ids),
                )
            )
            await session.execute(delete(Application).where(Application.id.in_(application_ids)))
        if applicant_owner_id is not None:
            await session.execute(
                delete(ApplicationRateBucket).where(
                    ApplicationRateBucket.applicant_owner_id == applicant_owner_id
                )
            )
        credential_ids = (api_key_ids or set()) | (agent_grant_ids or set())
        if credential_ids:
            await session.execute(
                delete(ChangeEvent).where(
                    ChangeEvent.owner_id == owner_id,
                    ChangeEvent.resource_id.in_(credential_ids),
                    ChangeEvent.resource_type.in_({"api_key", "agent_grant"}),
                )
            )
        if api_key_ids:
            await session.execute(
                delete(ApiKey).where(
                    ApiKey.owner_id == owner_id,
                    ApiKey.id.in_(api_key_ids),
                )
            )
        if agent_grant_ids:
            await session.execute(
                delete(AgentGrant).where(
                    AgentGrant.owner_id == owner_id,
                    AgentGrant.id.in_(agent_grant_ids),
                )
            )
        resource_ids = organization_ids | job_ids | verification_ids | application_ids
        if resource_ids:
            await session.execute(
                delete(ChangeEvent).where(
                    ChangeEvent.owner_id == owner_id,
                    ChangeEvent.resource_id.in_(resource_ids),
                )
            )
        if idempotency_keys:
            await session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key.in_(idempotency_keys)
                )
            )
        if job_ids:
            await session.execute(delete(Job).where(Job.id.in_(job_ids)))
        if organization_ids:
            await session.execute(delete(Organization).where(Organization.id.in_(organization_ids)))
        await session.commit()
    for relative_path in evidence_paths:
        app.state.store.delete_exact(relative_path)
    for relative_path, sha256, size_bytes in application_artifacts:
        app.state.store.delete_verified_exact(
            relative_path,
            sha256,
            expected_size_bytes=size_bytes,
            max_size_bytes=131_072,
        )


async def _cleanup_live_document(
    app,
    *,
    owner_id: str,
    identifier: str,
    idempotency_keys: set[str],
) -> None:
    """Delete only this test's unique document graph and canonical Markdown files."""

    storage_paths: list[str] = []
    async with app.state.session_factory() as session:
        document_ids = set(
            (
                await session.scalars(
                    select(Document.id).where(
                        Document.owner_id == owner_id,
                        Document.kind == "profile",
                        Document.public_identifier == identifier,
                    )
                )
            ).all()
        )
        taxonomy_term_ids: set[str] = set()
        if document_ids:
            storage_paths.extend(
                (
                    await session.scalars(
                        select(DocumentVersion.storage_path).where(
                            DocumentVersion.document_id.in_(document_ids)
                        )
                    )
                ).all()
            )
            taxonomy_term_ids.update(
                (
                    await session.scalars(
                        select(PublicTaxonomyMembership.term_id).where(
                            PublicTaxonomyMembership.document_id.in_(document_ids)
                        )
                    )
                ).all()
            )
            await session.execute(
                delete(SearchProjectionTask).where(
                    SearchProjectionTask.document_id.in_(document_ids)
                )
            )
            await session.execute(
                delete(ChangeEvent).where(
                    ChangeEvent.owner_id == owner_id,
                    ChangeEvent.resource_id.in_(document_ids),
                )
            )
            await session.execute(delete(Document).where(Document.id.in_(document_ids)))
            await session.flush()
            if taxonomy_term_ids:
                await session.execute(
                    delete(PublicTaxonomyTerm).where(
                        PublicTaxonomyTerm.id.in_(taxonomy_term_ids),
                        ~exists().where(PublicTaxonomyMembership.term_id == PublicTaxonomyTerm.id),
                    )
                )
        if idempotency_keys:
            await session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner_id,
                    IdempotencyRecord.idempotency_key.in_(idempotency_keys),
                )
            )
        await session.commit()
    for relative_path in storage_paths:
        app.state.store.delete_exact(relative_path)


async def _prepare_live_launch_workflow(app, client, suffix: str) -> dict[str, object]:
    owner_id = f"live-launch-owner-{suffix}"
    reviewer_id = f"live-launch-reviewer-{suffix}"
    organization_slug = f"live-launch-org-{suffix[:16]}"
    job_slug = f"live-launch-job-{suffix[:16]}"
    _set_principal(app, owner_id)
    organization = await client.post(
        "/v1/organizations",
        json={
            "slug": organization_slug,
            "name": "Live launch fixture",
            "website_url": "https://live-launch.example.test/careers",
            "visibility": "private",
        },
        headers={"Idempotency-Key": f"live-launch-org-{suffix}"},
    )
    _assert_status(organization, 201)
    organization_payload = organization.json()
    artifact = f"live-private-evidence-{suffix}".encode("ascii")
    submission = await client.post(
        f"/v1/organizations/{organization_slug}/verification-submissions",
        json={
            "evidence_kind": "corporate_registration",
            "metadata": {"fixture": f"live-private-marker-{suffix}"},
            "artifact_content_type": "text/plain",
            "artifact_base64": b64encode(artifact).decode("ascii"),
        },
        headers={"Idempotency-Key": f"live-launch-submit-{suffix}"},
    )
    _assert_status(submission, 201)
    verification_id = submission.json()["verification_id"]

    _set_principal(app, reviewer_id)
    detail = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    _assert_status(detail, 200)
    review_etag = detail.headers["etag"]
    reviewed = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "Idempotency-Key": f"live-launch-review-{suffix}",
            "If-Match": review_etag,
        },
    )
    _assert_status(reviewed, 200)
    detail_after_review = await client.get(
        f"/v1/internal/recruiting-verifications/{verification_id}"
    )
    _assert_status(detail_after_review, 200)
    activated = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/activate",
        json={
            "expected_state": "under_review",
            "policy_version": "recruiting-control-v1",
            "expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
        headers={
            "Idempotency-Key": f"live-launch-activate-{suffix}",
            "If-Match": detail_after_review.headers["etag"],
        },
    )
    _assert_status(activated, 200)

    _set_principal(app, owner_id)
    publicized = await client.put(
        f"/v1/organizations/{organization_slug}",
        json={"visibility": "public"},
        headers={
            "If-Match": organization.headers["etag"],
            "Idempotency-Key": f"live-launch-public-{suffix}",
        },
    )
    _assert_status(publicized, 200)
    job = await client.post(
        f"/v1/organizations/{organization_slug}/jobs",
        json={
            "slug": job_slug,
            "title": "Live launch role",
            "description": "A deterministic PostgreSQL launch fixture.",
            "location": "Singapore",
            "work_mode": "hybrid",
            "employment_type": "full_time",
        },
        headers={"Idempotency-Key": f"live-launch-job-{suffix}"},
    )
    _assert_status(job, 201)
    published = await client.post(
        f"/v1/organizations/{organization_slug}/jobs/{job_slug}/lifecycle/publish",
        headers={
            "If-Match": job.headers["etag"],
            "Idempotency-Key": f"live-launch-publish-{suffix}",
        },
    )
    _assert_status(published, 200)
    return {
        "owner_id": owner_id,
        "reviewer_id": reviewer_id,
        "organization_id": organization_payload["id"],
        "organization_slug": organization_slug,
        "verification_id": verification_id,
        "job_id": job.json()["id"],
        "job_slug": job_slug,
        "artifact": artifact.decode("ascii"),
        "keys": {
            f"live-launch-org-{suffix}",
            f"live-launch-submit-{suffix}",
            f"live-launch-review-{suffix}",
            f"live-launch-activate-{suffix}",
            f"live-launch-public-{suffix}",
            f"live-launch-job-{suffix}",
            f"live-launch-publish-{suffix}",
            f"live-launch-suspend-{suffix}",
            f"live-launch-revoke-{suffix}",
        },
    }


def test_live_index_name_is_unique_and_bounded() -> None:
    first = new_unique_index_name()
    second = new_unique_index_name()

    assert first != second
    assert first.startswith(LIVE_INDEX_PREFIX)
    assert second.startswith(LIVE_INDEX_PREFIX)
    assert len(first) <= 64
    assert len(second) <= 64
    assert len(first.removeprefix(LIVE_INDEX_PREFIX)) == 32
    assert len(second.removeprefix(LIVE_INDEX_PREFIX)) == 32


def test_live_environment_requires_opt_in_and_loopback_contract(monkeypatch) -> None:
    for name in (
        LIVE_INTEGRATION_FLAG,
        DATABASE_URL_ENV,
        SEARCH_PROJECTION_DATABASE_URL_ENV,
        MEILISEARCH_URL_ENV,
        MEILISEARCH_KEY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="=1"):
        require_live_integration_environment()

    monkeypatch.setenv(LIVE_INTEGRATION_FLAG, "1")
    database_url = "postgresql+asyncpg://ci:ci@127.0.0.1:5432/connectmd_integration"
    monkeypatch.setenv(DATABASE_URL_ENV, database_url)
    assert require_live_database_environment() == database_url
    monkeypatch.setenv(
        DATABASE_URL_ENV,
        "postgresql://ci:ci@127.0.0.1:5432/connectmd_integration",
    )
    monkeypatch.setenv(MEILISEARCH_URL_ENV, "http://127.0.0.1:7700")
    monkeypatch.setenv(MEILISEARCH_KEY_ENV, "a" * 16)
    with pytest.raises(ValueError, match="postgresql\\+asyncpg"):
        require_live_integration_environment()

    monkeypatch.setenv(
        DATABASE_URL_ENV,
        "postgresql+asyncpg://ci:ci@192.0.2.10:5432/connectmd_integration",
    )
    with pytest.raises(ValueError, match="loopback"):
        require_live_integration_environment()

    monkeypatch.setenv(
        DATABASE_URL_ENV,
        "postgresql+asyncpg://ci:ci@127.0.0.1:5432/connectmd_integration",
    )
    monkeypatch.setenv(
        SEARCH_PROJECTION_DATABASE_URL_ENV,
        "postgresql+asyncpg://connectmd_search_projection:ci@127.0.0.1:5432/connectmd_integration",
    )
    monkeypatch.setenv(MEILISEARCH_URL_ENV, "http://192.0.2.10:7700")
    with pytest.raises(ValueError, match="loopback"):
        require_live_integration_environment()

    monkeypatch.setenv(MEILISEARCH_URL_ENV, "http://127.0.0.1:7700")
    monkeypatch.setenv(
        SEARCH_PROJECTION_DATABASE_URL_ENV,
        "postgresql+asyncpg://connectmd_api:ci@127.0.0.1:5432/connectmd_integration",
    )
    with pytest.raises(ValueError, match="dedicated search projection role"):
        require_live_integration_environment()
    monkeypatch.setenv(
        SEARCH_PROJECTION_DATABASE_URL_ENV,
        "postgresql+asyncpg://connectmd_search_projection:ci@127.0.0.1:5432/connectmd_integration",
    )
    config = require_live_integration_environment()
    assert config.search_projection_database_url.startswith(
        "postgresql+asyncpg://connectmd_search_projection:"
    )
    assert config.meilisearch_index.startswith(LIVE_INDEX_PREFIX)


@live_integration
async def test_live_postgres_authority_matrix_uses_real_credentials(tmp_path) -> None:
    """Exercise database-backed credential paths without changing the unit-test lane."""

    database_url = require_live_database_environment()
    suffix = uuid4().hex
    owner_id = f"live-auth-owner-{suffix}"
    other_owner_id = f"live-auth-other-{suffix}"
    owner_identifier = f"live-auth-owner-{suffix[:16]}"
    other_identifier = f"live-auth-other-{suffix[:16]}"
    owner_profile_key = f"live-auth-owner-profile-{suffix}"
    other_profile_key = f"live-auth-other-profile-{suffix}"
    api_key_create_key = f"live-auth-api-key-{suffix}"
    grant_create_key = f"live-auth-grant-{suffix}"
    impersonated_write_key = f"live-auth-impersonated-{suffix}"
    idempotency_keys = {
        owner_profile_key,
        other_profile_key,
        api_key_create_key,
        grant_create_key,
        impersonated_write_key,
    }
    api_key_ids: set[str] = set()
    agent_grant_ids: set[str] = set()
    api_key_value = ""
    grant_value = ""
    settings = Settings(
        database_url=database_url,
        storage_path=tmp_path / "authority-matrix-storage",
        api_key_pepper="ci-only-pepper-is-at-least-thirty-two-characters",
        recruiting_enabled=True,
    )
    app = create_app(settings)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://integration"
        ) as client:
            _set_principal(app, owner_id)
            clerk_me = await client.get("/v1/me")
            _assert_status(clerk_me, 200)
            assert clerk_me.json()["authentication_method"] == "clerk_jwt"
            owner_profile = await client.post(
                "/v1/profiles",
                json={
                    "markdown": profile_markdown(visibility="private").replace(
                        "ada-lovelace", owner_identifier
                    )
                },
                headers={"Idempotency-Key": owner_profile_key},
            )
            _assert_status(owner_profile, 201)

            _set_principal(app, other_owner_id)
            other_profile = await client.post(
                "/v1/profiles",
                json={
                    "markdown": profile_markdown(visibility="private").replace(
                        "ada-lovelace", other_identifier
                    )
                },
                headers={"Idempotency-Key": other_profile_key},
            )
            _assert_status(other_profile, 201)

            _set_principal(app, owner_id)
            api_key_response = await client.post(
                "/v1/api-keys",
                json={"scopes": ["documents:read"]},
                headers={"Idempotency-Key": api_key_create_key},
            )
            _assert_status(api_key_response, 201)
            api_key_payload = api_key_response.json()
            api_key_value = api_key_payload["key"]
            api_key_ids.add(api_key_payload["id"])
            assert api_key_value.startswith("cnd_")

            grant_response = await client.post(
                "/v1/agent-grants",
                json={
                    "name": "Live PostgreSQL authority matrix",
                    "mode": "direct",
                    "resource": {"type": "owner"},
                    "scopes": ["documents:read"],
                },
                headers={"Idempotency-Key": grant_create_key},
            )
            _assert_status(grant_response, 201)
            grant_payload = grant_response.json()
            grant_value = grant_payload["key"]
            agent_grant_ids.add(grant_payload["id"])
            assert grant_value.startswith("cng_")

            app.dependency_overrides.clear()
            anonymous_me = await client.get("/v1/me")
            _assert_status(anonymous_me, 401)
            anonymous_documents = await client.get("/v1/documents")
            _assert_status(anonymous_documents, 401)

            api_headers = {"Authorization": f"Bearer {api_key_value}"}
            api_me = await client.get("/v1/me", headers=api_headers)
            _assert_status(api_me, 200)
            assert api_me.json()["authentication_method"] == "agent_api_key"
            assert api_me.json()["scopes"] == ["documents:read"]
            api_documents = await client.get("/v1/documents", headers=api_headers)
            _assert_status(api_documents, 200)
            assert {document["identifier"] for document in api_documents.json()["documents"]} == {
                owner_identifier
            }

            grant_headers = {"Authorization": f"Bearer {grant_value}"}
            grant_me = await client.get("/v1/me", headers=grant_headers)
            _assert_status(grant_me, 200)
            grant_me_payload = grant_me.json()
            assert grant_me_payload["authentication_method"] == "agent_grant"
            assert grant_me_payload["grant_id"] in agent_grant_ids
            assert grant_me_payload["grant_mode"] == "direct"
            assert grant_me_payload["resource"] == {"type": "owner", "id": None}
            grant_documents = await client.get("/v1/documents", headers=grant_headers)
            _assert_status(grant_documents, 200)
            assert {document["identifier"] for document in grant_documents.json()["documents"]} == {
                owner_identifier
            }

            async with app.state.session_factory() as session:
                api_key_row = await session.get(ApiKey, next(iter(api_key_ids)))
                grant_row = await session.get(AgentGrant, next(iter(agent_grant_ids)))
                assert api_key_row is not None and api_key_row.last_used_at is not None
                assert grant_row is not None and grant_row.last_used_at is not None

            _set_principal(app, owner_id, impersonated=True)
            impersonated_read = await client.get("/v1/applications")
            impersonated_write = await client.post(
                "/v1/organizations/example/jobs/example/applications",
                json={
                    "message": "This impersonated fixture must not write.",
                    "snapshot_kind": "profile",
                    "snapshot_identifier": owner_identifier,
                    "human_confirmed": True,
                },
                headers={"Idempotency-Key": impersonated_write_key},
            )
            assert {impersonated_read.status_code, impersonated_write.status_code} == {403}
            assert {
                impersonated_read.json()["detail"],
                impersonated_write.json()["detail"],
            } == {"application access requires a signed-in human"}
            app.dependency_overrides.clear()

            async with app.state.session_factory() as session:
                receipt = await session.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.idempotency_key == impersonated_write_key
                    )
                )
                assert receipt is None
    finally:
        app.dependency_overrides.clear()
        try:
            await _cleanup_live_document(
                app,
                owner_id=owner_id,
                identifier=owner_identifier,
                idempotency_keys={owner_profile_key},
            )
            await _cleanup_live_document(
                app,
                owner_id=other_owner_id,
                identifier=other_identifier,
                idempotency_keys={other_profile_key},
            )
            await _cleanup_live_rows(
                app,
                owner_id=owner_id,
                idempotency_keys=idempotency_keys,
                api_key_ids=api_key_ids,
                agent_grant_ids=agent_grant_ids,
            )
        finally:
            await app.state.engine.dispose()


@live_integration
async def test_live_postgres_api_key_same_key_different_body_race_rolls_back_loser(
    tmp_path,
) -> None:
    """PostgreSQL must serialize real API-key authentication and document idempotency."""

    database_url = require_live_database_environment()
    suffix = uuid4().hex
    owner_id = f"live-api-key-race-owner-{suffix}"
    api_key_create_key = f"live-api-key-race-create-{suffix}"
    race_key = f"live-api-key-race-write-{suffix}"
    first_identifier = f"live-api-key-race-a-{suffix[:16]}"
    second_identifier = f"live-api-key-race-b-{suffix[:16]}"
    idempotency_keys = {api_key_create_key, race_key}
    api_key_ids: set[str] = set()
    api_key_value = ""
    first_body = {
        "markdown": profile_markdown(visibility="private").replace("ada-lovelace", first_identifier)
    }
    second_body = {
        "markdown": profile_markdown(visibility="private").replace(
            "ada-lovelace", second_identifier
        )
    }
    settings = Settings(
        database_url=database_url,
        storage_path=tmp_path / "api-key-race-storage",
        api_key_pepper="ci-only-pepper-is-at-least-thirty-two-characters",
    )
    apps = []
    try:
        setup_app = create_app(settings)
        apps.append(setup_app)
        async with AsyncClient(
            transport=ASGITransport(app=setup_app), base_url="http://integration"
        ) as client:
            _set_principal(setup_app, owner_id)
            issued = await client.post(
                "/v1/api-keys",
                json={"scopes": ["documents:write"]},
                headers={"Idempotency-Key": api_key_create_key},
            )
            _assert_status(issued, 201)
            issued_payload = issued.json()
            api_key_value = issued_payload["key"]
            api_key_ids.add(issued_payload["id"])
            assert api_key_value.startswith("cnd_")
            setup_app.dependency_overrides.clear()

        first_app = create_app(settings)
        second_app = create_app(settings)
        apps.extend((first_app, second_app))
        barrier = asyncio.Barrier(2)
        headers = {
            "Authorization": f"Bearer {api_key_value}",
            "Idempotency-Key": race_key,
        }
        first, second = await asyncio.wait_for(
            asyncio.gather(
                _post_after_barrier(
                    first_app,
                    barrier,
                    "/v1/profiles",
                    body=first_body,
                    headers=headers,
                ),
                _post_after_barrier(
                    second_app,
                    barrier,
                    "/v1/profiles",
                    body=second_body,
                    headers=headers,
                ),
            ),
            timeout=15,
        )
        assert {first.status_code, second.status_code} == {201, 409}
        winner = first if first.status_code == 201 else second
        loser = second if winner is first else first
        loser_identifier = second_identifier if winner is first else first_identifier
        _assert_body_contains(loser, "Idempotency-Key")
        _assert_body_excludes(loser, first_identifier)
        _assert_body_excludes(loser, second_identifier)

        async with setup_app.state.session_factory() as session:
            documents = (
                await session.scalars(
                    select(Document).where(
                        Document.owner_id == owner_id,
                        Document.public_identifier.in_({first_identifier, second_identifier}),
                    )
                )
            ).all()
            assert len(documents) == 1
            assert documents[0].public_identifier == winner.json()["identifier"]
            receipts = (
                await session.scalars(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.owner_id == owner_id,
                        IdempotencyRecord.idempotency_key == race_key,
                    )
                )
            ).all()
            assert len(receipts) == 1
            assert receipts[0].resource_type == "document"
            assert receipts[0].resource_id == documents[0].id
            _assert_text_excludes(receipts[0].response_body, loser_identifier)
            events = (
                await session.scalars(
                    select(ChangeEvent).where(
                        ChangeEvent.owner_id == owner_id,
                        ChangeEvent.resource_type == "document",
                        ChangeEvent.resource_id == documents[0].id,
                    )
                )
            ).all()
            assert len(events) == 1
            _assert_text_excludes(events[0].payload, loser_identifier)
    finally:
        if apps:
            try:
                await _cleanup_live_document(
                    apps[0],
                    owner_id=owner_id,
                    identifier=first_identifier,
                    idempotency_keys={race_key},
                )
                await _cleanup_live_document(
                    apps[0],
                    owner_id=owner_id,
                    identifier=second_identifier,
                    idempotency_keys={race_key},
                )
                await _cleanup_live_rows(
                    apps[0],
                    owner_id=owner_id,
                    idempotency_keys=idempotency_keys,
                    api_key_ids=api_key_ids,
                )
            finally:
                await asyncio.gather(*(app.state.engine.dispose() for app in apps))


@live_integration
async def test_live_postgres_meili_document_contract(tmp_path) -> None:
    live = require_live_integration_environment()
    suffix = uuid4().hex[:16]
    identifier = f"ci-{suffix}"
    create_key = f"live-profile-create-{identifier}"
    update_key = f"live-profile-update-{identifier}"
    previous_headline = f"Legacy projection {suffix}"
    current_headline = f"Current projection {suffix}"
    previous_skill_label = f"LegacySkill{suffix[:8]}"
    current_skill_label = f"CurrentSkill{suffix[:8]}"
    source = client_template(
        "profile",
        "\n".join(
            (
                "Ada Lovelace",
                previous_headline,
                "Skills",
                previous_skill_label,
            )
        ),
    )
    source = source.replace("handle: ada-lovelace", f"handle: {identifier}")
    source = source.replace("visibility: private", "visibility: public")
    settings = Settings(
        database_url=live.database_url,
        storage_path=tmp_path / "storage",
        api_key_pepper="ci-only-pepper-is-at-least-thirty-two-characters",
        meilisearch_url=cast(Any, live.meilisearch_url),
        meilisearch_api_key=live.meilisearch_api_key,
        meilisearch_index=live.meilisearch_index,
    )
    app = create_app(settings)
    projection_session_factory, projection_engine = build_search_projection_session_factory(live)
    try:
        app.dependency_overrides[require_principal] = _owner
        app.dependency_overrides[optional_principal] = _owner
        await app.state.search.configure_index()
        executor = SearchProjectionExecutor(
            projection_session_factory,
            app.state.store,
            app.state.search,
            worker_id=f"live-{identifier}",
        )
        async with projection_session_factory() as projection_session:
            session_identity = (
                await projection_session.execute(text("SELECT session_user, current_user"))
            ).one()
            assert session_identity[0] == SEARCH_PROJECTION_DATABASE_ROLE
            assert session_identity[1] == SEARCH_PROJECTION_DATABASE_ROLE
            await require_database_role(projection_session, SEARCH_PROJECTION_DATABASE_ROLE)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://integration"
        ) as client:
            created = await client.post(
                "/v1/profiles",
                json={"markdown": source},
                headers={"Idempotency-Key": create_key},
            )
            assert created.status_code == 201
            created_payload = created.json()
            document_id = created_payload["id"]
            created_frontmatter, _ = validate_canonical("profile", created_payload["markdown"])
            assert created_frontmatter["schema_version"] == 2
            previous_skill = created_frontmatter["skills"][0]
            previous_skill_id = f"{previous_skill['scheme']}:{previous_skill['id']}"
            current_skill_external_id = f"live-current-{suffix}"
            current_skill_id = f"{previous_skill['scheme']}:{current_skill_external_id}"
            await _run_scoped_projection_task(
                executor,
                document_id=document_id,
                version=1,
            )
            markdown = await client.get(
                f"/v1/profiles/{identifier}", headers={"Accept": "text/markdown"}
            )
            assert markdown.status_code == 200
            canonical = created_payload["markdown"].replace(previous_headline, current_headline)
            canonical = canonical.replace(
                f"id: {previous_skill['id']}", f"id: {current_skill_external_id}"
            )
            canonical = canonical.replace(
                f"label: {previous_skill_label}", f"label: {current_skill_label}"
            )
            canonical = canonical.replace(f"- {previous_skill_label}", f"- {current_skill_label}")
            assert previous_headline not in canonical
            assert previous_skill_label not in canonical
            assert previous_skill["id"] not in canonical
            updated = await client.put(
                f"/v1/profiles/{identifier}",
                json={"markdown": canonical},
                headers={
                    "If-Match": created.headers["etag"],
                    "Idempotency-Key": update_key,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["version"] == 2
            updated_frontmatter, _ = validate_canonical("profile", updated.json()["markdown"])
            assert updated_frontmatter["schema_version"] == 2
            assert updated_frontmatter["skills"] == [
                {
                    "scheme": previous_skill["scheme"],
                    "id": current_skill_external_id,
                    "label": current_skill_label,
                }
            ]
            await _run_scoped_projection_task(
                executor,
                document_id=document_id,
                version=2,
            )
            versions = await client.get(f"/v1/profiles/{identifier}/versions")
            assert [item["version"] for item in versions.json()["versions"]] == [1, 2]
            search = await client.get("/v1/search", params={"q": current_headline})
            assert search.status_code == 200
            matching_hits = [
                hit for hit in search.json()["hits"] if hit["identifier"] == identifier
            ]
            assert len(matching_hits) == 1
            assert matching_hits[0]["version"] == 2
            assert matching_hits[0]["schema_version"] == 2
            assert matching_hits[0]["headline"] == current_headline
            assert matching_hits[0]["skills"] == [current_skill_label]

            stale_content = await client.get("/v1/search", params={"q": previous_headline})
            assert stale_content.status_code == 200
            assert identifier not in {hit["identifier"] for hit in stale_content.json()["hits"]}
            current_typed = await client.get("/v1/search", params={"skill_ids": current_skill_id})
            assert current_typed.status_code == 200
            current_typed_hits = [
                hit for hit in current_typed.json()["hits"] if hit["identifier"] == identifier
            ]
            assert len(current_typed_hits) == 1
            assert current_typed_hits[0]["version"] == 2
            stale_typed = await client.get("/v1/search", params={"skill_ids": previous_skill_id})
            assert stale_typed.status_code == 200
            assert identifier not in {hit["identifier"] for hit in stale_typed.json()["hits"]}

            current_terms = await client.get("/v1/taxonomies/skill", params={"q": current_skill_id})
            assert current_terms.status_code == 200
            assert [term["canonical_id"] for term in current_terms.json()["terms"]] == [
                current_skill_id
            ]
            stale_terms = await client.get("/v1/taxonomies/skill", params={"q": previous_skill_id})
            assert stale_terms.status_code == 200
            assert stale_terms.json()["terms"] == []

            async with app.state.session_factory() as session:
                snapshot = await session.get(PublicTaxonomyDocumentSnapshot, document_id)
                assert snapshot is not None
                assert snapshot.schema_version == 2
                assert snapshot.document_version == 2
                memberships = (
                    await session.execute(
                        select(PublicTaxonomyTerm, PublicTaxonomyMembership)
                        .join(
                            PublicTaxonomyMembership,
                            PublicTaxonomyMembership.term_id == PublicTaxonomyTerm.id,
                        )
                        .where(
                            PublicTaxonomyMembership.document_id == document_id,
                            PublicTaxonomyTerm.taxonomy == "skill",
                        )
                    )
                ).all()
                assert [
                    (term.canonical_id, membership.label_assertion)
                    for term, membership in memberships
                ] == [(current_skill_id, current_skill_label)]
                assert (
                    await session.scalar(
                        select(PublicTaxonomyTerm.id).where(
                            PublicTaxonomyTerm.canonical_id == previous_skill_id
                        )
                    )
                    is None
                )
    finally:
        try:
            await delete_meilisearch_index(live)
        finally:
            try:
                await _cleanup_live_document(
                    app,
                    owner_id="ci_owner",
                    identifier=identifier,
                    idempotency_keys={create_key, update_key},
                )
            finally:
                try:
                    await projection_engine.dispose()
                finally:
                    await app.state.engine.dispose()


@live_integration
async def test_live_postgres_production_lifespan_exact_and_projection_role(tmp_path) -> None:
    """Prove production startup, exact current/stale reads, and dedicated projection authority."""

    live = require_live_integration_environment()
    suffix = uuid4().hex[:16]
    identifier = f"ci-exact-{suffix}"
    create_key = f"live-exact-create-{identifier}"
    update_key = f"live-exact-update-{identifier}"
    legacy_headline = f"Live exact legacy {suffix}"
    current_headline = f"Live exact current {suffix}"
    settings = _production_live_settings(live, tmp_path, suffix)
    app = create_app(settings)
    projection_session_factory, projection_engine = build_search_projection_session_factory(live)
    lifespan_entered = False
    cleanup_complete = False
    try:
        await app.state.search.configure_index()
        async with app.router.lifespan_context(app):
            lifespan_entered = True
            _set_principal(app, "ci-exact-owner")
            executor = SearchProjectionExecutor(
                projection_session_factory,
                app.state.store,
                app.state.search,
                worker_id=f"live-{identifier}",
            )
            async with projection_session_factory() as projection_session:
                session_identity = (
                    await projection_session.execute(text("SELECT session_user, current_user"))
                ).one()
                assert session_identity[0] == SEARCH_PROJECTION_DATABASE_ROLE
                assert session_identity[1] == SEARCH_PROJECTION_DATABASE_ROLE
                await require_database_role(projection_session, SEARCH_PROJECTION_DATABASE_ROLE)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://integration"
            ) as client:
                ready = await client.get("/readyz")
                _assert_status(ready, 200)
                ready_payload = ready.json()
                assert ready_payload["database"] == "ok"
                assert ready_payload["taxonomy"] == "ok"
                assert ready_payload["exact_search"] == "ok"
                assert ready_payload["search"] == "ok"

                source = client_template(
                    "profile",
                    "\n".join(("Ada Lovelace", legacy_headline, "Skills", f"LiveSkill{suffix}")),
                ).replace("handle: ada-lovelace", f"handle: {identifier}")
                source = source.replace("visibility: private", "visibility: public")
                created = await client.post(
                    "/v1/profiles",
                    json={"markdown": source},
                    headers={"Idempotency-Key": create_key},
                )
                _assert_status(created, 201)
                document_id = created.json()["id"]
                await _run_scoped_projection_task(
                    executor,
                    document_id=document_id,
                    version=1,
                )

                canonical = created.json()["markdown"].replace(legacy_headline, current_headline)
                updated = await client.put(
                    f"/v1/profiles/{identifier}",
                    json={"markdown": canonical},
                    headers={
                        "If-Match": created.headers["etag"],
                        "Idempotency-Key": update_key,
                    },
                )
                _assert_status(updated, 200)
                assert updated.json()["version"] == 2
                await _run_scoped_projection_task(
                    executor,
                    document_id=document_id,
                    version=2,
                )

                current_exact = await client.get(
                    "/v1/search", params={"mode": "exact", "q": current_headline}
                )
                _assert_status(current_exact, 200)
                current_exact_payload = current_exact.json()
                assert current_exact_payload["mode"] == "exact"
                assert current_exact_payload["complete"] is True
                current_exact_hits = [
                    hit for hit in current_exact_payload["hits"] if hit["identifier"] == identifier
                ]
                assert len(current_exact_hits) == 1
                assert current_exact_hits[0]["version"] == 2

                stale_exact = await client.get(
                    "/v1/search", params={"mode": "exact", "q": legacy_headline}
                )
                _assert_status(stale_exact, 200)
                assert identifier not in {hit["identifier"] for hit in stale_exact.json()["hits"]}

                current_projection = await client.get("/v1/search", params={"q": current_headline})
                _assert_status(current_projection, 200)
                current_projection_hits = [
                    hit
                    for hit in current_projection.json()["hits"]
                    if hit["identifier"] == identifier
                ]
                assert len(current_projection_hits) == 1
                assert current_projection_hits[0]["version"] == 2
                assert current_projection_hits[0]["headline"] == current_headline

                stale_projection = await client.get("/v1/search", params={"q": legacy_headline})
                _assert_status(stale_projection, 200)
                assert identifier not in {
                    hit["identifier"] for hit in stale_projection.json()["hits"]
                }

                await _cleanup_live_document(
                    app,
                    owner_id="ci-exact-owner",
                    identifier=identifier,
                    idempotency_keys={create_key, update_key},
                )
                cleanup_complete = True
                async with app.state.session_factory() as session:
                    assert (
                        await session.scalar(
                            select(Document.id).where(
                                Document.owner_id == "ci-exact-owner",
                                Document.public_identifier == identifier,
                            )
                        )
                        is None
                    )
                    assert (
                        await session.scalar(
                            select(SearchProjectionTask.document_id).where(
                                SearchProjectionTask.document_id == document_id,
                            )
                        )
                        is None
                    )
    finally:
        try:
            await _cleanup_live_document(
                app,
                owner_id="ci-exact-owner",
                identifier=identifier,
                idempotency_keys={create_key, update_key},
            )
        finally:
            try:
                await delete_meilisearch_index(live)
            finally:
                await projection_engine.dispose()
                await app.state.engine.dispose()
    if lifespan_entered:
        assert app.state.engine.sync_engine.pool.checkedout() == 0
    assert cast(Any, projection_engine.sync_engine.pool).checkedout() == 0
    assert cleanup_complete


@live_integration
async def test_live_postgres_same_key_different_body_race_rolls_back_loser(tmp_path) -> None:
    """PostgreSQL must commit one organization and one receipt for a colliding key."""

    database_url = require_live_database_environment()
    suffix = uuid4().hex
    owner_id = f"live-idempotency-owner-{suffix}"
    key = f"live-idempotency-race-{suffix}"
    first_body = {
        "slug": f"live-idempotency-a-{suffix[:16]}",
        "name": "Live idempotency winner candidate A",
        "description": "First body in the live same-key race.",
        "visibility": "private",
    }
    second_body = {
        "slug": f"live-idempotency-b-{suffix[:16]}",
        "name": "Live idempotency loser candidate B",
        "description": "Second body in the live same-key race.",
        "visibility": "private",
    }
    settings = Settings(
        database_url=database_url,
        storage_path=tmp_path / "idempotency-race-storage",
        api_key_pepper="ci-only-pepper-is-at-least-thirty-two-characters",
    )
    apps = []
    try:
        first_app = create_app(settings)
        apps.append(first_app)
        second_app = create_app(settings)
        apps.append(second_app)
        _set_principal(first_app, owner_id)
        _set_principal(second_app, owner_id)
        barrier = asyncio.Barrier(2)
        first, second = await asyncio.wait_for(
            asyncio.gather(
                _post_after_barrier(
                    first_app,
                    barrier,
                    "/v1/organizations",
                    body=first_body,
                    headers={"Idempotency-Key": key},
                ),
                _post_after_barrier(
                    second_app,
                    barrier,
                    "/v1/organizations",
                    body=second_body,
                    headers={"Idempotency-Key": key},
                ),
            ),
            timeout=15,
        )
        assert {first.status_code, second.status_code} == {201, 409}
        winner = first if first.status_code == 201 else second
        loser = second if winner is first else first
        _assert_body_contains(loser, "Idempotency-Key")
        _assert_body_excludes(loser, str(second_body["name"]))

        async with first_app.state.session_factory() as session:
            organizations = (
                await session.scalars(select(Organization).where(Organization.owner_id == owner_id))
            ).all()
            assert len(organizations) == 1
            assert organizations[0].slug == winner.json()["slug"]
            receipts = (
                await session.scalars(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.owner_id == owner_id,
                        IdempotencyRecord.idempotency_key == key,
                    )
                )
            ).all()
            assert len(receipts) == 1
            assert receipts[0].resource_type == "organization"
            assert receipts[0].resource_id == organizations[0].id
            _assert_text_excludes(receipts[0].response_body, str(second_body["name"]))
            events = (
                await session.scalars(
                    select(ChangeEvent).where(
                        ChangeEvent.owner_id == owner_id,
                        ChangeEvent.resource_type == "organization",
                    )
                )
            ).all()
            assert len(events) == 1
            assert events[0].resource_id == organizations[0].id
            _assert_text_excludes(events[0].payload, str(second_body["slug"]))
    finally:
        if apps:
            try:
                await _cleanup_live_rows(apps[0], owner_id=owner_id, idempotency_keys={key})
            finally:
                await asyncio.gather(*(app.state.engine.dispose() for app in apps))


@live_integration
async def test_live_postgres_launch_authority_loss_race_has_one_decision_receipt(
    tmp_path,
) -> None:
    """Concurrent launch-gate decisions use PostgreSQL locks and roll back the loser.

    SQLite can check source order, but it cannot prove this PostgreSQL scheduling contract.
    """

    database_url = require_live_database_environment()
    suffix = uuid4().hex
    launch_owner_id = f"live-launch-owner-{suffix}"
    launch_keys = {
        f"live-launch-org-{suffix}",
        f"live-launch-submit-{suffix}",
        f"live-launch-review-{suffix}",
        f"live-launch-activate-{suffix}",
        f"live-launch-public-{suffix}",
        f"live-launch-job-{suffix}",
        f"live-launch-publish-{suffix}",
        f"live-launch-suspend-{suffix}",
        f"live-launch-revoke-{suffix}",
    }
    settings = Settings(
        database_url=database_url,
        storage_path=tmp_path / "launch-race-storage",
        api_key_pepper="ci-only-pepper-is-at-least-thirty-two-characters",
        recruiting_enabled=True,
        verification_reviewer_id=f"live-launch-reviewer-{suffix}",
        verification_reviewer_role="recruiting_verifier",
    )
    apps = []
    workflow: dict[str, object] | None = None
    try:
        setup_app = create_app(settings)
        apps.append(setup_app)
        async with AsyncClient(
            transport=ASGITransport(app=setup_app), base_url="http://integration"
        ) as client:
            workflow = await _prepare_live_launch_workflow(setup_app, client, suffix)
            organization_slug = workflow["organization_slug"]
            assert isinstance(organization_slug, str)
            before_loss = await client.get(
                "/v1/jobs", params={"organization_slug": organization_slug}
            )
            _assert_status(before_loss, 200)
            assert len(before_loss.json()["jobs"]) == 1

        race_apps = [create_app(settings), create_app(settings)]
        apps.extend(race_apps)
        reviewer_id = workflow["reviewer_id"]
        verification_id = workflow["verification_id"]
        assert isinstance(reviewer_id, str) and isinstance(verification_id, str)
        for app in race_apps:
            _set_principal(app, reviewer_id)
        barrier = asyncio.Barrier(2)
        race_path = "/v1/internal/recruiting-verifications/{}/{}"
        suspend_key = f"live-launch-suspend-{suffix}"
        revoke_key = f"live-launch-revoke-{suffix}"
        suspend, revoke = await asyncio.wait_for(
            asyncio.gather(
                _post_after_barrier(
                    race_apps[0],
                    barrier,
                    race_path.format(verification_id, "suspend"),
                    body={"expected_state": "active"},
                    headers={"Idempotency-Key": suspend_key},
                ),
                _post_after_barrier(
                    race_apps[1],
                    barrier,
                    race_path.format(verification_id, "revoke"),
                    body={"expected_state": "active"},
                    headers={"Idempotency-Key": revoke_key},
                ),
            ),
            timeout=15,
        )
        assert {suspend.status_code, revoke.status_code} == {200, 412}
        winner = suspend if suspend.status_code == 200 else revoke
        loser = revoke if winner is suspend else suspend
        assert winner.json()["state"] in {"suspended", "revoked"}
        _assert_body_contains(loser, "verification state is stale")
        artifact = workflow["artifact"]
        assert isinstance(artifact, str)
        _assert_body_excludes(loser, artifact)

        async with setup_app.state.session_factory() as session:
            verification_events = (
                await session.scalars(
                    select(OrganizationVerificationEvent)
                    .where(
                        OrganizationVerificationEvent.verification_id == verification_id,
                    )
                    .order_by(
                        OrganizationVerificationEvent.occurred_at.asc(),
                        OrganizationVerificationEvent.id.asc(),
                    )
                )
            ).all()
            assert len(verification_events) == 4
            assert verification_events[-1].to_state == winner.json()["state"]
            decision_receipts = (
                await session.scalars(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.idempotency_key.in_({suspend_key, revoke_key})
                    )
                )
            ).all()
            assert len(decision_receipts) == 1
            assert decision_receipts[0].response_status == 200
            assert decision_receipts[0].resource_type == "recruiting_verification_decision"
            if not decision_receipts[0].response_body:
                pytest.fail("winning decision receipt has no response body", pytrace=False)
            _assert_text_excludes(decision_receipts[0].response_body, artifact)
            decision_events = (
                await session.scalars(
                    select(ChangeEvent).where(
                        ChangeEvent.owner_id == workflow["owner_id"],
                        ChangeEvent.resource_type == "organization_verification",
                        ChangeEvent.resource_id == verification_id,
                        ChangeEvent.event_type.in_(
                            {
                                "organization_verification.suspended",
                                "organization_verification.revoked",
                            }
                        ),
                    )
                )
            ).all()
            assert len(decision_events) == 1
            json_state = winner.json()["state"]
            assert json_state
            assert decision_events[0].event_type.endswith(json_state)
            _assert_text_excludes(decision_events[0].payload, artifact)

        async with AsyncClient(
            transport=ASGITransport(app=setup_app), base_url="http://integration"
        ) as client:
            after_loss = await client.get(
                "/v1/jobs", params={"organization_slug": organization_slug}
            )
        _assert_status(after_loss, 200)
        assert after_loss.json()["jobs"] == []
    finally:
        if apps:
            try:
                if workflow is not None:
                    owner_id = workflow["owner_id"]
                    organization_id = workflow["organization_id"]
                    verification_id = workflow["verification_id"]
                    keys = workflow["keys"]
                    assert (
                        isinstance(owner_id, str)
                        and isinstance(organization_id, str)
                        and isinstance(verification_id, str)
                        and isinstance(keys, set)
                    )
                else:
                    owner_id = launch_owner_id
                    organization_id = None
                    verification_id = None
                    keys = launch_keys
                await _cleanup_live_rows(
                    apps[0],
                    owner_id=owner_id,
                    idempotency_keys=keys,
                    organization_id=organization_id,
                    verification_id=verification_id,
                )
            finally:
                await asyncio.gather(*(app.state.engine.dispose() for app in apps))


@live_integration
async def test_live_postgres_application_withdraw_accept_race_has_one_terminal_effect(
    tmp_path,
) -> None:
    """Applicant withdrawal and employer acceptance serialize on PostgreSQL locks."""

    database_url = require_live_database_environment()
    suffix = uuid4().hex
    owner_id = f"live-launch-owner-{suffix}"
    reviewer_id = f"live-launch-reviewer-{suffix}"
    applicant_owner_id = f"live-application-applicant-{suffix}"
    profile_identifier = f"live-applicant-{suffix[:16]}"
    profile_key = f"live-application-profile-{suffix}"
    submission_key = f"live-application-submit-{suffix}"
    withdrawal_key = f"live-application-withdraw-{suffix}"
    acceptance_key = f"live-application-accept-{suffix}"
    private_marker = f"live-private-application-{suffix}"
    launch_keys = {
        f"live-launch-org-{suffix}",
        f"live-launch-submit-{suffix}",
        f"live-launch-review-{suffix}",
        f"live-launch-activate-{suffix}",
        f"live-launch-public-{suffix}",
        f"live-launch-job-{suffix}",
        f"live-launch-publish-{suffix}",
        f"live-launch-suspend-{suffix}",
        f"live-launch-revoke-{suffix}",
    }
    application_keys = {submission_key, withdrawal_key, acceptance_key}
    settings = Settings(
        database_url=database_url,
        storage_path=tmp_path / "application-race-storage",
        api_key_pepper="ci-only-pepper-is-at-least-thirty-two-characters",
        recruiting_enabled=True,
        verification_reviewer_id=reviewer_id,
        verification_reviewer_role="recruiting_verifier",
    )
    apps = []
    workflow: dict[str, object] | None = None
    try:
        setup_app = create_app(settings)
        apps.append(setup_app)
        async with AsyncClient(
            transport=ASGITransport(app=setup_app), base_url="http://integration"
        ) as client:
            workflow = await _prepare_live_launch_workflow(setup_app, client, suffix)
            organization_slug = workflow["organization_slug"]
            job_slug = workflow["job_slug"]
            organization_id = workflow["organization_id"]
            assert (
                isinstance(organization_slug, str)
                and isinstance(job_slug, str)
                and isinstance(organization_id, str)
            )

            _set_principal(setup_app, applicant_owner_id)
            profile = await client.post(
                "/v1/profiles",
                json={
                    "markdown": profile_markdown(visibility="public").replace(
                        "ada-lovelace", profile_identifier
                    )
                },
                headers={"Idempotency-Key": profile_key},
            )
            _assert_status(profile, 201)
            submitted = await client.post(
                f"/v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
                json={
                    "message": private_marker,
                    "snapshot_kind": "profile",
                    "snapshot_identifier": profile_identifier,
                    "human_confirmed": True,
                },
                headers={"Idempotency-Key": submission_key},
            )
            _assert_status(submitted, 201)
            application_id = submitted.json()["id"]
            assert isinstance(application_id, str)

        withdraw_app = create_app(settings)
        accept_app = create_app(settings)
        apps.extend((withdraw_app, accept_app))
        _set_principal(withdraw_app, applicant_owner_id)
        _set_principal(accept_app, owner_id)
        barrier = asyncio.Barrier(2)
        withdraw_path = f"/v1/applications/{application_id}/withdraw"
        accept_path = (
            f"/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/"
            f"{application_id}/accept"
        )

        async with setup_app.state.session_factory() as gate:
            await gate.begin()
            locked_organization = await gate.scalar(
                select(Organization).where(Organization.id == organization_id).with_for_update()
            )
            assert locked_organization is not None
            gate_started_at = await gate.scalar(text("SELECT clock_timestamp()"))
            assert isinstance(gate_started_at, datetime)
            tasks = [
                asyncio.create_task(
                    _post_without_body_after_barrier(
                        withdraw_app,
                        barrier,
                        withdraw_path,
                        headers={"Idempotency-Key": withdrawal_key},
                    )
                ),
                asyncio.create_task(
                    _post_without_body_after_barrier(
                        accept_app,
                        barrier,
                        accept_path,
                        headers={"Idempotency-Key": acceptance_key},
                    )
                ),
            ]
            try:
                await _wait_for_application_transition_lock_waiters(
                    gate,
                    gate_started_at=gate_started_at,
                )
                await gate.commit()
                withdrawn, accepted = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
            except BaseException:
                if gate.in_transaction():
                    await gate.rollback()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        assert {withdrawn.status_code, accepted.status_code} == {200, 409}
        winner = withdrawn if withdrawn.status_code == 200 else accepted
        loser = accepted if winner is withdrawn else withdrawn
        winning_status = winner.json()["status"]
        assert winning_status in {"withdrawn", "accepted"}
        _assert_body_excludes(loser, private_marker)

        async with setup_app.state.session_factory() as session:
            final_application = await session.get(Application, application_id)
            assert final_application is not None
            assert final_application.status == winning_status
            receipts = (
                await session.scalars(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.idempotency_key.in_({withdrawal_key, acceptance_key})
                    )
                )
            ).all()
            assert len(receipts) == 1
            receipt = receipts[0]
            assert receipt.resource_type == "application_transition"
            assert receipt.response_status == 200
            assert receipt.response_body == ""
            assert receipt.response_headers == "{}"
            assert receipt.owner_id == (
                applicant_owner_id if winning_status == "withdrawn" else owner_id
            )
            assert receipt.idempotency_key == (
                withdrawal_key if winning_status == "withdrawn" else acceptance_key
            )
            _assert_text_excludes(receipt.resource_id, private_marker)

            winning_event_type = f"application.{winning_status}"
            events = (
                await session.scalars(
                    select(ChangeEvent).where(
                        ChangeEvent.resource_type == "application",
                        ChangeEvent.resource_id == application_id,
                        ChangeEvent.event_type.in_(
                            {"application.withdrawn", "application.accepted"}
                        ),
                    )
                )
            ).all()
            assert len(events) == 2
            assert {event.owner_id for event in events} == {
                owner_id,
                applicant_owner_id,
            }
            assert {event.event_type for event in events} == {winning_event_type}
            assert all(json.loads(event.payload) == {"status": winning_status} for event in events)
            assert all(private_marker not in event.payload for event in events)

            notifications = (
                await session.scalars(
                    select(Notification).where(
                        Notification.resource_type == "application",
                        Notification.resource_id == application_id,
                    )
                )
            ).all()
            if winning_status == "accepted":
                assert len(notifications) == 1
                assert notifications[0].recipient_owner_id == applicant_owner_id
                assert notifications[0].type == "application.accepted"
            else:
                assert notifications == []

        _set_principal(setup_app, owner_id)
        async with AsyncClient(
            transport=ASGITransport(app=setup_app), base_url="http://integration"
        ) as client:
            employer_detail = await client.get(
                f"/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/"
                f"{application_id}",
                headers={"X-Connectmd-Purpose": "job_application_review"},
            )
        if winning_status == "withdrawn":
            _assert_status(employer_detail, 404)
            _assert_body_excludes(employer_detail, private_marker)
        else:
            _assert_status(employer_detail, 200)
            assert employer_detail.json()["id"] == application_id
    finally:
        if apps:
            try:
                if workflow is not None:
                    organization_id = workflow["organization_id"]
                    verification_id = workflow["verification_id"]
                    workflow_keys = workflow["keys"]
                    assert (
                        isinstance(organization_id, str)
                        and isinstance(verification_id, str)
                        and isinstance(workflow_keys, set)
                    )
                    row_keys = workflow_keys | application_keys
                else:
                    organization_id = None
                    verification_id = None
                    row_keys = launch_keys | application_keys
                await _cleanup_live_rows(
                    apps[0],
                    owner_id=owner_id,
                    idempotency_keys=row_keys,
                    organization_id=organization_id,
                    verification_id=verification_id,
                    applicant_owner_id=applicant_owner_id,
                )
                await _cleanup_live_document(
                    apps[0],
                    owner_id=applicant_owner_id,
                    identifier=profile_identifier,
                    idempotency_keys={profile_key},
                )
            finally:
                await asyncio.gather(*(app.state.engine.dispose() for app in apps))


@live_integration
async def test_live_postgres_artifact_intent_lock_serializes_same_key(tmp_path) -> None:
    database_url = require_live_database_environment()
    settings = Settings(
        database_url=database_url,
        storage_path=tmp_path / "artifact-lock-storage",
        api_key_pepper="ci-only-pepper-is-at-least-thirty-two-characters",
    )
    app = create_app(settings)
    try:
        intent_id = derive_artifact_intent_uuid(
            settings.api_key_pepper or "",
            flow="application_snapshot",
            owner_id="ci_owner",
            target_id="30000000-0000-4000-8000-000000000003",
            idempotency_key="live-same-application-key",
        )
        acquired = asyncio.Event()
        release = asyncio.Event()

        async with app.state.session_factory() as first:
            await first.begin()
            await acquire_artifact_intent_lock(first, intent_id)

            async def contender() -> None:
                async with app.state.session_factory() as second:
                    await second.begin()
                    await acquire_artifact_intent_lock(second, intent_id)
                    acquired.set()
                    await release.wait()
                    await second.rollback()

            task = asyncio.create_task(contender())
            await asyncio.sleep(0.1)
            assert not acquired.is_set()
            await first.commit()
            await asyncio.wait_for(acquired.wait(), timeout=2)
            release.set()
            await task
    finally:
        await app.state.engine.dispose()
