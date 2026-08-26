from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth import Principal, optional_principal, require_principal
from app.config import Settings
from app.main import create_app
from app.models import (
    Base,
    IdempotencyRecord,
    Job,
    Organization,
    OrganizationMembership,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import canonical_evidence_path


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


@pytest_asyncio.fixture
async def disabled_recruiting_client(
    tmp_path,
) -> AsyncIterator[tuple[Any, AsyncClient, dict[str, Principal | None]]]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'connectmd.db'}",
        storage_path=tmp_path / "storage",
        api_key_pepper="test-only-pepper-is-long-enough",
        verification_reviewer_id="reviewer:preprovisioned",
        verification_reviewer_role="recruiting_verifier",
    )
    assert settings.recruiting_enabled is False
    app = create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    current: dict[str, Principal | None] = {"principal": human("owner")}

    async def required() -> Principal:
        principal = current["principal"]
        assert principal is not None
        return principal

    async def optional() -> Principal | None:
        return current["principal"]

    app.dependency_overrides[require_principal] = required
    app.dependency_overrides[optional_principal] = optional
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield app, client, current
    await app.state.engine.dispose()


async def seed_active_public_recruiting(app: Any, organization_id: str, job_id: str) -> str:
    now = datetime.now(UTC)
    payload = b"bounded recruiting release-gate evidence"
    artifact_digest = sha256(payload).hexdigest()
    metadata = {"registry": "verified"}
    verification_id = str(uuid4())
    evidence_path = canonical_evidence_path(organization_id, verification_id, artifact_digest)
    app.state.store.write_immutable_bytes(evidence_path, payload)

    async with app.state.session_factory() as session:
        organization = await session.get(Organization, organization_id)
        job = await session.get(Job, job_id)
        assert organization is not None
        assert job is not None
        claim_digest = material_claim_digest(
            organization_id=organization.id,
            organization_name=organization.name,
            organization_website_url=organization.website_url,
            organization_material_version=organization.verification_material_version,
            evidence_kind="other",
            metadata=metadata,
            artifact_content_type="text/plain",
            artifact_sha256=artifact_digest,
            artifact_size_bytes=len(payload),
        )
        organization.visibility = "public"
        organization.verification_status = "verified"
        job.status = "published"
        job.published_at = now
        verification = OrganizationVerification(
            id=verification_id,
            organization_id=organization.id,
            purpose="recruiting_control",
            submitted_by_owner_id=organization.owner_id,
            material_claim_digest=claim_digest,
            created_at=now,
        )
        evidence = OrganizationVerificationEvidence(
            id=str(uuid4()),
            verification_id=verification.id,
            evidence_kind="other",
            metadata_json=json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            artifact_content_type="text/plain",
            artifact_sha256=artifact_digest,
            artifact_size_bytes=len(payload),
            storage_path=evidence_path,
            created_at=now,
            retention_expires_at=now + timedelta(days=30),
        )
        event = OrganizationVerificationEvent(
            id=str(uuid4()),
            verification_id=verification.id,
            organization_id=organization.id,
            purpose="recruiting_control",
            to_state="active",
            actor_id="reviewer:preprovisioned",
            actor_role="recruiting_verifier",
            policy_version="recruiting-control-v1",
            material_claim_digest=claim_digest,
            expires_at=now + timedelta(days=7),
            occurred_at=now,
        )
        session.add_all(
            (
                verification,
                evidence,
                event,
                OrganizationMembership(
                    id=str(uuid4()),
                    organization_id=organization.id,
                    member_owner_id="member",
                    member_profile_handle="member-profile",
                    role="admin",
                    status="active",
                    invited_by_owner_id=organization.owner_id,
                    created_at=now,
                ),
            )
        )
        await session.commit()
    return verification_id


async def idempotency_count(app: Any) -> int:
    async with app.state.session_factory() as session:
        return int(await session.scalar(select(func.count(IdempotencyRecord.id))) or 0)


async def test_default_off_discovery_hides_every_recruiting_contract(
    disabled_recruiting_client,
    api_client,
) -> None:
    _, client, _ = disabled_recruiting_client
    schema = (await client.get("/openapi.json")).json()
    recruiting_paths = {
        path
        for path in schema["paths"]
        if path.startswith(("/v1/organizations", "/v1/jobs", "/v1/applications"))
        or path
        in {
            "/v1/employer/organizations",
            "/v1/employer/jobs",
            "/v1/organization-membership-invitations",
        }
    }
    assert recruiting_paths == set()
    assert schema["info"]["description"] == (
        "Markdown-native profiles, resumes, and human-only professional posts."
    )
    assert {tag["name"] for tag in schema["tags"]}.isdisjoint(
        {"organizations", "jobs", "applications"}
    )

    capabilities = (await client.get("/v1/capabilities")).json()
    assert capabilities["release_gates"] == {"verified_recruitment": False}
    for hidden in ("employer_inventory", "organizations", "jobs", "applications"):
        assert hidden not in capabilities
    assert "organization" not in capabilities["agent_grants"]["resource_scope_matrix"]
    assert not any(
        operation.startswith(("organization.", "job.", "application."))
        for operation in capabilities["idempotency"]["operations"]
    )

    concise = await client.get("/llms.txt")
    complete = await client.get("/llms-full.txt")
    assert concise.status_code == complete.status_code == 200
    assert "disabled by the deployment release gate" in concise.text
    assert "disabled by default in this deployment" in complete.text
    for marker in (
        "/v1/organizations",
        "/v1/jobs",
        "/v1/applications",
        "/v1/employer/organizations",
        "/v1/employer/jobs",
    ):
        assert marker not in concise.text
        assert marker not in complete.text
    assert '"organization":' not in complete.text
    assert "exact `organization`" not in complete.text

    for metadata_path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        scopes = (await client.get(metadata_path)).json()["scopes_supported"]
        assert not set(scopes).intersection(
            {"organizations:read", "organizations:write", "jobs:read", "jobs:write"}
        )

    _, enabled_client = api_client
    enabled_schema = (await enabled_client.get("/openapi.json")).json()
    assert enabled_schema["info"]["description"].endswith("organization-owned JSON jobs.")
    assert {"organizations", "jobs", "applications"}.issubset(
        {tag["name"] for tag in enabled_schema["tags"]}
    )
    assert "/v1/organizations" in enabled_schema["paths"]
    assert "/v1/jobs" in enabled_schema["paths"]
    assert "/v1/applications" in enabled_schema["paths"]
    enabled_capabilities = (await enabled_client.get("/v1/capabilities")).json()
    assert enabled_capabilities["release_gates"] == {"verified_recruitment": True}
    assert enabled_capabilities["agent_grants"]["resource_scope_matrix"]["organization"] == [
        "jobs:read",
        "jobs:write",
        "organizations:read",
        "organizations:write",
    ]
    for metadata_path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        enabled_scopes = (await enabled_client.get(metadata_path)).json()["scopes_supported"]
        assert {
            "organizations:read",
            "organizations:write",
            "jobs:read",
            "jobs:write",
        }.issubset(set(enabled_scopes))
    enabled_complete = await enabled_client.get("/llms-full.txt")
    assert '"organization":' in enabled_complete.text
    assert "exact `organization`" in enabled_complete.text


async def test_default_off_gate_hides_public_state_and_blocks_release_mutations_first(
    disabled_recruiting_client,
) -> None:
    app, client, current = disabled_recruiting_client
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "release-org", "name": "Release Org", "visibility": "private"},
        headers={"Idempotency-Key": "release-org-create"},
    )
    assert organization.status_code == 201, organization.text
    job = await client.post(
        "/v1/organizations/release-org/jobs",
        json={
            "slug": "existing-role",
            "title": "Existing role",
            "description": "A retained role used to prove the release gate.",
        },
        headers={"Idempotency-Key": "existing-role-create"},
    )
    assert job.status_code == 201, job.text
    draft = await client.post(
        "/v1/organizations/release-org/jobs",
        json={
            "slug": "draft-role",
            "title": "Draft role",
            "description": "Private drafting must remain available.",
        },
        headers={"Idempotency-Key": "draft-role-create"},
    )
    assert draft.status_code == 201, draft.text
    verification_id = await seed_active_public_recruiting(
        app, organization.json()["id"], job.json()["id"]
    )

    app.state.settings.recruiting_enabled = True
    current["principal"] = None
    assert (await client.get("/v1/organizations/release-org")).status_code == 200
    assert (await client.get("/v1/organizations/release-org/jobs/existing-role")).status_code == 200
    assert (await client.get("/v1/organizations")).json()["organizations"]
    assert (await client.get("/v1/jobs")).json()["jobs"]

    app.state.settings.recruiting_enabled = False
    assert (await client.get("/v1/organizations/release-org")).status_code == 404
    assert (await client.get("/v1/organizations/release-org/jobs/existing-role")).status_code == 404
    assert (await client.get("/v1/organizations", params={"q": " "})).json() == {
        "organizations": [],
        "next_cursor": None,
    }
    assert (await client.get("/v1/jobs", params={"work_mode": "unknown"})).json() == {
        "jobs": [],
        "next_cursor": None,
    }

    current["principal"] = human("owner")
    assert (await client.get("/v1/organizations/release-org")).status_code == 200
    assert (await client.get("/v1/organizations/release-org/jobs/existing-role")).status_code == 200
    assert (await client.get("/v1/employer/organizations")).json()["organizations"]
    assert (await client.get("/v1/employer/jobs")).json()["jobs"]
    assert (await client.get("/v1/applications")).json() == {
        "applications": [],
        "next_cursor": None,
    }

    current["principal"] = human("member")
    assert (await client.get("/v1/organizations/release-org")).status_code == 200
    current["principal"] = Principal(
        subject="member",
        method="agent_grant",
        scopes=frozenset({"organizations:read", "jobs:read"}),
        grant_id="70000000-0000-4000-8000-000000000001",
        grant_mode="direct",
        resource_type="organization",
        resource_id=organization.json()["id"],
    )
    assert (await client.get("/v1/organizations/release-org")).status_code == 200
    assert (await client.get("/v1/organizations/release-org/jobs/existing-role")).status_code == 200

    current["principal"] = human("owner")
    managed = await client.put(
        "/v1/organizations/release-org",
        json={"description": "Private management remains available."},
        headers={
            "If-Match": (await client.get("/v1/organizations/release-org")).headers["etag"],
            "Idempotency-Key": "release-org-private-update",
        },
    )
    assert managed.status_code == 200, managed.text
    managed_job = await client.put(
        "/v1/organizations/release-org/jobs/existing-role",
        json={"title": "Privately managed existing role"},
        headers={
            "If-Match": (
                await client.get("/v1/organizations/release-org/jobs/existing-role")
            ).headers["etag"],
            "Idempotency-Key": "existing-role-private-update",
        },
    )
    assert managed_job.status_code == 200, managed_job.text

    receipts_before = await idempotency_count(app)
    assert (
        await client.put(
            "/v1/organizations/release-org",
            json={"visibility": "public"},
        )
    ).status_code == 404
    assert (
        await client.post("/v1/organizations/release-org/jobs/draft-role/lifecycle/publish")
    ).status_code == 404
    assert (
        await client.post(
            "/v1/organizations/not-present/jobs/not-present/applications",
            json={
                "message": "This must stop before lookup.",
                "snapshot_kind": "profile",
                "snapshot_identifier": "not-present",
                "human_confirmed": True,
            },
        )
    ).status_code == 404
    assert (
        await client.post(
            "/v1/organizations/not-present/jobs/not-present/applications/not-present/accept"
        )
    ).status_code == 404

    current["principal"] = human("reviewer:preprovisioned")
    for action in ("activate", "restore"):
        blocked = await client.post(
            f"/v1/internal/recruiting-verifications/not-present/{action}",
            json={"expected_state": "under_review" if action == "activate" else "suspended"},
        )
        assert blocked.status_code == 404
        assert blocked.json()["detail"] == "recruiting is unavailable"
    for action, expected_state in (
        ("review", "submitted"),
        ("reject", "under_review"),
        ("expire", "active"),
        ("suspend", "active"),
        ("revoke", "active"),
    ):
        defensive = await client.post(
            f"/v1/internal/recruiting-verifications/not-present/{action}",
            json={"expected_state": expected_state},
        )
        assert defensive.status_code == 428
        assert defensive.json()["detail"] != "recruiting is unavailable"

    assert await idempotency_count(app) == receipts_before
    async with app.state.session_factory() as session:
        draft_row = await session.scalar(
            select(Job).where(
                Job.organization_id == organization.json()["id"], Job.slug == "draft-role"
            )
        )
    assert draft_row is not None
    assert draft_row.status == "draft"

    suspended = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/suspend",
        json={"expected_state": "active"},
        headers={"Idempotency-Key": "disabled-release-defensive-suspend"},
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["state"] == "suspended"
    assert await idempotency_count(app) == receipts_before + 1
    async with app.state.session_factory() as session:
        latest = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(OrganizationVerificationEvent.verification_id == verification_id)
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
    assert latest is not None
    assert latest.to_state == "suspended"
