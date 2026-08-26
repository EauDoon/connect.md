from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select

import app.main as main_module
from app.auth import (
    IMPERSONATION_READ_ONLY_CODE,
    Principal,
    optional_principal,
    require_principal,
)
from app.db import get_session
from app.models import (
    Application,
    ChangeEvent,
    DocumentVersion,
    IdempotencyRecord,
    Job,
    JobVersion,
    Organization,
    OrganizationMembership,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import canonical_evidence_path

from .helpers import profile_markdown


def human(subject: str, *, impersonated: bool = False) -> Principal:
    return Principal(
        subject=subject,
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        is_impersonated=impersonated,
    )


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def create_profile(
    app, client, subject: str, handle: str, *, visibility: str = "public"
) -> None:
    as_principal(app, human(subject))
    markdown = profile_markdown(visibility=visibility).replace("ada-lovelace", handle)
    created = await client.post(
        "/v1/profiles",
        json={"markdown": markdown},
        headers={"Idempotency-Key": f"social-profile-create-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_impersonated_clerk_principal_cannot_use_private_application_surfaces(
    api_client,
) -> None:
    app, client = api_client
    as_principal(app, human("impersonated-applicant", impersonated=True))

    private_read = await client.get("/v1/applications")
    private_write = await client.post(
        "/v1/organizations/example/jobs/example/applications",
        json={
            "message": "Please consider my application.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "applicant-profile",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "impersonated-application-write-0001"},
    )

    assert {private_read.status_code, private_write.status_code} == {403}
    assert {private_read.json()["detail"], private_write.json()["detail"]} == {
        "application access requires a signed-in human"
    }

    as_principal(app, human("ordinary-applicant"))
    allowed = await client.get("/v1/applications")
    assert allowed.status_code == 200
    assert allowed.json() == {"applications": [], "next_cursor": None}


async def test_organization_job_and_application_are_gated_and_private(api_client) -> None:
    app, client = api_client
    as_principal(app, human("employer_owner"))

    unverified_public = await client.post(
        "/v1/organizations",
        json={
            "slug": "acme",
            "name": "Acme, Inc.",
            "description": "Owner-attested employer profile.",
            "visibility": "public",
        },
        headers={"Idempotency-Key": "organization-acme-0001"},
    )
    assert unverified_public.status_code == 422
    organization = await client.post(
        "/v1/organizations",
        json={
            "slug": "acme",
            "name": "Acme, Inc.",
            "description": "Owner-attested employer profile.",
            "visibility": "private",
        },
        headers={"Idempotency-Key": "organization-acme-private-0001"},
    )
    assert organization.status_code == 201, organization.text
    assert organization.json()["recruiting_verification_active"] is False

    job = await client.post(
        "/v1/organizations/acme/jobs",
        json={
            "slug": "backend-engineer",
            "title": "Backend Engineer",
            "description": "Build reliable application services.",
            "location": "Singapore",
            "work_mode": "hybrid",
            "employment_type": "full_time",
        },
        headers={"Idempotency-Key": "job-backend-0001"},
    )
    assert job.status_code == 201, job.text
    blocked_publish = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/publish",
        headers={
            "If-Match": job.headers["etag"],
            "Idempotency-Key": "job-publish-unverified-0001",
        },
    )
    assert blocked_publish.status_code == 409

    blocked_visibility = await client.put(
        "/v1/organizations/acme",
        json={"visibility": "public"},
        headers={
            "If-Match": organization.headers["etag"],
            "Idempotency-Key": "organization-acme-public-unverified-0001",
        },
    )
    assert blocked_visibility.status_code == 409

    async with app.state.session_factory() as session:
        row = await session.scalar(select(Organization).where(Organization.slug == "acme"))
        assert row is not None
        unverified_job = await session.get(Job, job.json()["id"])
        assert unverified_job is not None
        row.visibility = "public"
        # The legacy mutable flag has no authority once 0006 is installed.
        row.verification_status = "verified"
        unverified_job.status = "published"
        await session.commit()

    unverified_public_jobs = await client.get("/v1/jobs")
    assert unverified_public_jobs.status_code == 200
    assert unverified_public_jobs.json()["jobs"] == []

    async with app.state.session_factory() as session:
        row = await session.scalar(select(Organization).where(Organization.slug == "acme"))
        assert row is not None
        verified_job = await session.get(Job, job.json()["id"])
        assert verified_job is not None
        now = datetime.now(UTC)
        evidence_bytes = b"a"
        evidence_sha256 = sha256(evidence_bytes).hexdigest()
        claim_digest = material_claim_digest(
            organization_id=row.id,
            organization_name=row.name,
            organization_website_url=row.website_url,
            organization_material_version=row.verification_material_version,
            evidence_kind="other",
            metadata={},
            artifact_content_type="text/plain",
            artifact_sha256=evidence_sha256,
            artifact_size_bytes=len(evidence_bytes),
        )
        verification = OrganizationVerification(
            id="11111111-1111-4111-8111-111111111111",
            organization_id=row.id,
            purpose="recruiting_control",
            submitted_by_owner_id="employer_owner",
            material_claim_digest=claim_digest,
            created_at=now - timedelta(seconds=3),
        )
        evidence_path = canonical_evidence_path(row.id, verification.id, evidence_sha256)
        app.state.store.write_immutable_bytes(evidence_path, evidence_bytes)
        session.add_all(
            (
                verification,
                OrganizationVerificationEvidence(
                    id="11111111-1111-4111-8111-111111111115",
                    verification_id=verification.id,
                    evidence_kind="other",
                    metadata_json="{}",
                    artifact_content_type="text/plain",
                    artifact_sha256=evidence_sha256,
                    artifact_size_bytes=len(evidence_bytes),
                    storage_path=evidence_path,
                    created_at=now - timedelta(seconds=3),
                    retention_expires_at=now + timedelta(days=365),
                ),
                OrganizationVerificationEvent(
                    id="11111111-1111-4111-8111-111111111112",
                    verification_id=verification.id,
                    organization_id=row.id,
                    purpose="recruiting_control",
                    to_state="submitted",
                    actor_id="employer_owner",
                    actor_role="submitter",
                    policy_version=None,
                    material_claim_digest=claim_digest,
                    expires_at=None,
                    occurred_at=now - timedelta(seconds=3),
                ),
                OrganizationVerificationEvent(
                    id="11111111-1111-4111-8111-111111111113",
                    verification_id=verification.id,
                    organization_id=row.id,
                    purpose="recruiting_control",
                    to_state="under_review",
                    actor_id="reviewer:preprovisioned",
                    actor_role="recruiting_verifier",
                    policy_version=None,
                    material_claim_digest=claim_digest,
                    expires_at=None,
                    occurred_at=now - timedelta(seconds=2),
                ),
                OrganizationVerificationEvent(
                    id="11111111-1111-4111-8111-111111111114",
                    verification_id=verification.id,
                    organization_id=row.id,
                    purpose="recruiting_control",
                    to_state="active",
                    actor_id="reviewer:preprovisioned",
                    actor_role="recruiting_verifier",
                    policy_version="recruiting-control-v1",
                    material_claim_digest=claim_digest,
                    expires_at=now + timedelta(days=30),
                    occurred_at=now - timedelta(seconds=1),
                ),
            )
        )
        verified_job.status = "draft"
        await session.commit()

    publicized = await client.put(
        "/v1/organizations/acme",
        json={"visibility": "public"},
        headers={
            "If-Match": organization.headers["etag"],
            "Idempotency-Key": "organization-acme-public-0001",
        },
    )
    assert publicized.status_code == 200, publicized.text

    published = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/publish",
        headers={
            "If-Match": job.headers["etag"],
            "Idempotency-Key": "job-publish-verified-0001",
        },
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    public_jobs = await client.get("/v1/jobs", params={"work_mode": "hybrid"})
    assert public_jobs.status_code == 200
    assert public_jobs.json()["jobs"][0]["id"] == job.json()["id"]

    published_etag = published.headers["etag"]
    published_title = published.json()["title"]
    organization_agent = Principal(
        subject="employer_owner",
        method="agent_grant",
        scopes=frozenset({"jobs:write"}),
        grant_mode="direct",
        resource_type="organization",
        resource_id=organization.json()["id"],
    )
    as_principal(app, organization_agent)
    denied_agent_update = await client.put(
        "/v1/organizations/acme/jobs/backend-engineer",
        json={"title": "Agent-rewritten role"},
        headers={
            "If-Match": published_etag,
            "Idempotency-Key": "job-published-agent-update-0001",
        },
    )
    assert denied_agent_update.status_code == 403

    organization_api_key = Principal(
        subject="employer_owner",
        method="agent_api_key",
        scopes=frozenset({"jobs:write"}),
    )
    as_principal(app, organization_api_key)
    denied_api_key_update = await client.put(
        "/v1/organizations/acme/jobs/backend-engineer",
        json={"title": "API-key-rewritten role"},
        headers={
            "If-Match": published_etag,
            "Idempotency-Key": "job-published-api-key-update-0001",
        },
    )
    assert denied_api_key_update.status_code == 403
    unchanged_public_jobs = await client.get("/v1/jobs", params={"work_mode": "hybrid"})
    assert unchanged_public_jobs.status_code == 200
    unchanged_public_job = unchanged_public_jobs.json()["jobs"][0]
    assert unchanged_public_job["title"] == published_title
    assert unchanged_public_job["etag"] == published_etag

    as_principal(app, human("employer_owner"))
    human_update = await client.put(
        "/v1/organizations/acme/jobs/backend-engineer",
        json={"title": "Senior Backend Engineer"},
        headers={
            "If-Match": published_etag,
            "Idempotency-Key": "job-published-human-update-0001",
        },
    )
    assert human_update.status_code == 200, human_update.text
    assert human_update.json()["status"] == "published"
    assert human_update.json()["title"] == "Senior Backend Engineer"

    later_update = await client.put(
        "/v1/organizations/acme/jobs/backend-engineer",
        json={"description": "Build reliable distributed application services."},
        headers={
            "If-Match": human_update.headers["etag"],
            "Idempotency-Key": "job-published-human-update-0002",
        },
    )
    assert later_update.status_code == 200, later_update.text

    delayed_update_replay = await client.put(
        "/v1/organizations/acme/jobs/backend-engineer",
        json={"title": "Senior Backend Engineer"},
        headers={
            "If-Match": published_etag,
            "Idempotency-Key": "job-published-human-update-0001",
        },
    )
    assert delayed_update_replay.status_code == 200, delayed_update_replay.text
    assert delayed_update_replay.json() == human_update.json()
    assert delayed_update_replay.headers["etag"] == human_update.headers["etag"]
    assert delayed_update_replay.headers["idempotency-replayed"] == "true"

    delayed_publish_replay = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/publish",
        headers={
            "If-Match": job.headers["etag"],
            "Idempotency-Key": "job-publish-verified-0001",
        },
    )
    assert delayed_publish_replay.status_code == 200, delayed_publish_replay.text
    assert delayed_publish_replay.json() == published.json()
    assert delayed_publish_replay.headers["etag"] == published.headers["etag"]
    assert delayed_publish_replay.headers["idempotency-replayed"] == "true"

    delayed_create_replay = await client.post(
        "/v1/organizations/acme/jobs",
        json={
            "slug": "backend-engineer",
            "title": "Backend Engineer",
            "description": "Build reliable application services.",
            "location": "Singapore",
            "work_mode": "hybrid",
            "employment_type": "full_time",
        },
        headers={"Idempotency-Key": "job-backend-0001"},
    )
    assert delayed_create_replay.status_code == 201, delayed_create_replay.text
    assert delayed_create_replay.json() == job.json()
    assert delayed_create_replay.headers["etag"] == job.headers["etag"]
    assert delayed_create_replay.headers["idempotency-replayed"] == "true"
    async with app.state.session_factory() as session:
        job_receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == "employer_owner",
                IdempotencyRecord.idempotency_key == "job-backend-0001",
            )
        )
        assert job_receipt is not None
        original_job_receipt_headers = job_receipt.response_headers
        job_receipt.response_headers = '{"ETag":'
        await session.commit()
    malformed_job_receipt_replay = await client.post(
        "/v1/organizations/acme/jobs",
        json={
            "slug": "backend-engineer",
            "title": "Backend Engineer",
            "description": "Build reliable application services.",
            "location": "Singapore",
            "work_mode": "hybrid",
            "employment_type": "full_time",
        },
        headers={"Idempotency-Key": "job-backend-0001"},
    )
    assert malformed_job_receipt_replay.status_code == 503
    assert malformed_job_receipt_replay.json()["detail"] == (
        "idempotent job operation receipt failed its integrity check"
    )
    assert '{"ETag":' not in malformed_job_receipt_replay.text
    async with app.state.session_factory() as session:
        job_receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == "employer_owner",
                IdempotencyRecord.idempotency_key == "job-backend-0001",
            )
        )
        assert job_receipt is not None
        job_receipt.response_headers = original_job_receipt_headers
        await session.commit()

    as_principal(app, human("applicant"))
    submitted_snapshot_markdown = profile_markdown(visibility="public")
    snapshot = await client.post(
        "/v1/profiles",
        json={"markdown": submitted_snapshot_markdown},
        headers={"Idempotency-Key": "application-profile-create-0001"},
    )
    assert snapshot.status_code == 201, snapshot.text
    submitted_snapshot_markdown = snapshot.json()["markdown"]
    note = "I would like to discuss this role privately."
    application_headers = {"Idempotency-Key": "application-acme-0001"}
    application = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/applications",
        json={
            "message": note,
            "snapshot_kind": "profile",
            "snapshot_identifier": "ada-lovelace",
            "human_confirmed": True,
        },
        headers=application_headers,
    )
    assert application.status_code == 201, application.text
    assert note not in application.text
    assert application.json()["snapshot_version"] == snapshot.json()["version"]
    assert application.json()["snapshot_sha256"]
    replay = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/applications",
        json={
            "message": note,
            "snapshot_kind": "profile",
            "snapshot_identifier": "ada-lovelace",
            "human_confirmed": True,
        },
        headers=application_headers,
    )
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"

    own_detail = await client.get(f"/v1/applications/{application.json()['id']}")
    assert own_detail.status_code == 200
    assert own_detail.json()["message"] == note
    own_list = await client.get("/v1/applications")
    assert own_list.status_code == 200
    assert note not in own_list.text

    # A later source version must never change an already-submitted application.
    source_update = await client.put(
        "/v1/profiles/ada-lovelace",
        json={"markdown": profile_markdown(visibility="public", headline="Changed source")},
        headers={
            "If-Match": snapshot.headers["etag"],
            "Idempotency-Key": "application-profile-source-update-0001",
        },
    )
    assert source_update.status_code == 200, source_update.text
    async with app.state.session_factory() as session:
        changed_source = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == snapshot.json()["id"],
                DocumentVersion.version == source_update.json()["version"],
            )
        )
        assert changed_source is not None
        app.state.store.delete_exact(changed_source.storage_path)

    as_principal(app, human("unrelated_user"))
    forged_job_write = await client.post(
        "/v1/organizations/acme/jobs",
        json={"slug": "forged", "title": "Forged", "description": "Must never be created."},
        headers={"Idempotency-Key": "forged-job-0001"},
    )
    assert forged_job_write.status_code == 404
    cross_org_read = await client.get("/v1/organizations/acme/jobs/backend-engineer/applications")
    assert cross_org_read.status_code == 404

    as_principal(app, human("employer_owner"))
    denied_list = await client.get("/v1/organizations/acme/jobs/backend-engineer/applications")
    assert denied_list.status_code == 403
    private_list = await client.get(
        "/v1/organizations/acme/jobs/backend-engineer/applications",
        headers={"X-Connectmd-Purpose": "job_application_review"},
    )
    assert private_list.status_code == 200
    assert note not in private_list.text
    assert "applicant_owner_id" not in private_list.text
    denied_detail = await client.get(
        f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}"
    )
    assert denied_detail.status_code == 403
    purpose_limited_detail = await client.get(
        f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}",
        headers={"X-Connectmd-Purpose": "job_application_review"},
    )
    assert purpose_limited_detail.status_code == 200
    assert purpose_limited_detail.json()["message"] == note

    snapshot_path = f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}/snapshot"
    denied_snapshot = await client.get(snapshot_path)
    assert denied_snapshot.status_code == 403
    snapshot_json = await client.get(
        snapshot_path, headers={"X-Connectmd-Purpose": "job_application_review"}
    )
    assert snapshot_json.status_code == 200, snapshot_json.text
    assert snapshot_json.json() == {
        "application_id": application.json()["id"],
        "snapshot_kind": "profile",
        "snapshot_identifier": "ada-lovelace",
        "snapshot_version": 1,
        "snapshot_sha256": application.json()["snapshot_sha256"],
        "markdown": submitted_snapshot_markdown,
        "markdown_url": f"{snapshot_path}.md",
    }
    assert snapshot_json.headers["etag"] == f'"sha256-{application.json()["snapshot_sha256"]}"'
    assert snapshot_json.headers["cache-control"] == "no-store"
    assert snapshot_json.headers["content-digest"].startswith("sha-256=:")
    assert "Accept" in snapshot_json.headers["vary"].split(", ")
    snapshot_markdown = await client.get(
        snapshot_path,
        headers={
            "Accept": "text/markdown",
            "X-Connectmd-Purpose": "job_application_review",
        },
    )
    assert snapshot_markdown.status_code == 200
    assert snapshot_markdown.text == submitted_snapshot_markdown
    assert snapshot_markdown.headers["etag"] == snapshot_json.headers["etag"]
    explicit_snapshot_markdown = await client.get(
        f"{snapshot_path}.md", headers={"X-Connectmd-Purpose": "job_application_review"}
    )
    assert explicit_snapshot_markdown.status_code == 200
    assert explicit_snapshot_markdown.text == submitted_snapshot_markdown
    assert "Accept" not in explicit_snapshot_markdown.headers.get("vary", "").split(", ")

    # Loss of active recruiting control closes every employer-side private
    # application surface without suppressing the applicant's retained record.
    async with app.state.session_factory() as session:
        verification_event = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(OrganizationVerificationEvent.organization_id == organization.json()["id"])
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
        assert verification_event is not None
        verification_event.to_state = "suspended"
        await session.commit()
    assert (
        await client.get("/v1/organizations/acme/jobs/backend-engineer/applications")
    ).status_code == 404
    assert (
        await client.get(
            f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}",
            headers={"X-Connectmd-Purpose": "job_application_review"},
        )
    ).status_code == 404
    assert (
        await client.get(snapshot_path, headers={"X-Connectmd-Purpose": "job_application_review"})
    ).status_code == 404
    assert (
        await client.post(
            f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}/review",
            headers={"Idempotency-Key": "application-review-suspended-0001"},
        )
    ).status_code == 404
    as_principal(app, human("applicant"))
    assert (await client.get(f"/v1/applications/{application.json()['id']}")).status_code == 200
    async with app.state.session_factory() as session:
        verification_event = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(OrganizationVerificationEvent.organization_id == organization.json()["id"])
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
        assert verification_event is not None
        verification_event.to_state = "active"
        await session.commit()

    as_principal(app, human("employer_owner"))
    async with app.state.session_factory() as session:
        stored_application = await session.get(Application, application.json()["id"])
        assert (
            stored_application is not None and stored_application.snapshot_storage_path is not None
        )
        app.state.store._absolute(stored_application.snapshot_storage_path).write_text(
            "tampered", encoding="utf-8"
        )
    assert (
        await client.get(snapshot_path, headers={"X-Connectmd-Purpose": "job_application_review"})
    ).status_code == 404

    organization_agent = Principal(
        subject="employer_owner",
        method="agent_grant",
        scopes=frozenset({"applications:read", "applications:write"}),
        grant_mode="direct",
        resource_type="organization",
        resource_id=organization.json()["id"],
    )
    as_principal(app, organization_agent)
    agent_list = await client.get(
        "/v1/organizations/acme/jobs/backend-engineer/applications",
        headers={"X-Connectmd-Purpose": "job_application_review"},
    )
    assert agent_list.status_code == 403
    agent_detail = await client.get(
        f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}",
        headers={"X-Connectmd-Purpose": "job_application_review"},
    )
    assert agent_detail.status_code == 403
    agent_decision = await client.post(
        f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}/review",
        headers={"Idempotency-Key": "agent-application-review-0001"},
    )
    assert agent_decision.status_code == 403

    as_principal(app, human("applicant"))
    withdrawn = await client.post(
        f"/v1/applications/{application.json()['id']}/withdraw",
        headers={"Idempotency-Key": "application-withdraw-0001"},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    retained_applicant_detail = await client.get(f"/v1/applications/{application.json()['id']}")
    assert retained_applicant_detail.status_code == 200
    assert retained_applicant_detail.json()["message"] == note
    as_principal(app, human("employer_owner"))
    withdrawn_employer_detail = await client.get(
        f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}",
        headers={"X-Connectmd-Purpose": "job_application_review"},
    )
    assert withdrawn_employer_detail.status_code == 404
    withdrawn_employer_snapshot = await client.get(
        f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}/snapshot",
        headers={"X-Connectmd-Purpose": "job_application_review"},
    )
    assert withdrawn_employer_snapshot.status_code == 404

    async with app.state.session_factory() as session:
        expired = await session.scalar(
            select(Application).where(Application.id == application.json()["id"])
        )
        assert expired is not None
        expired.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        expired.status = "submitted"
        await session.commit()
    as_principal(app, human("applicant"))
    assert (await client.get(f"/v1/applications/{application.json()['id']}")).status_code == 404
    assert (await client.get("/v1/applications")).json()["applications"] == []
    as_principal(app, human("employer_owner"))
    assert (
        await client.get(
            f"/v1/organizations/acme/jobs/backend-engineer/applications/{application.json()['id']}/snapshot",
            headers={"X-Connectmd-Purpose": "job_application_review"},
        )
    ).status_code == 404

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "application-acme-0001"
            )
        )
        assert receipt is not None and note not in receipt.response_body
        events = (
            await session.scalars(
                select(ChangeEvent).where(ChangeEvent.resource_type == "application")
            )
        ).all()
        assert events and all(note not in event.payload for event in events)
        stored = await session.scalar(
            select(Application).where(Application.id == application.json()["id"])
        )
        assert stored is not None
        assert stored.confirmed_by_owner_id == "applicant"
        assert stored.retention_policy_version == "application-retention-v1"

    current_before_rename = await client.get("/v1/organizations/acme/jobs/backend-engineer")
    assert current_before_rename.status_code == 200, current_before_rename.text
    renamed = await client.put(
        "/v1/organizations/acme",
        json={"name": "Acme Hiring"},
        headers={
            "If-Match": publicized.headers["etag"],
            "Idempotency-Key": "organization-acme-rename-0001",
        },
    )
    assert renamed.status_code == 200, renamed.text
    current_after_rename = await client.get("/v1/organizations/acme/jobs/backend-engineer")
    assert current_after_rename.status_code == 200, current_after_rename.text
    assert current_after_rename.json()["version"] == current_before_rename.json()["version"]
    assert current_after_rename.json()["organization_name"] == "Acme Hiring"
    assert current_after_rename.headers["etag"] != current_before_rename.headers["etag"]

    delayed_pre_rename_replay = await client.put(
        "/v1/organizations/acme/jobs/backend-engineer",
        json={"description": "Build reliable distributed application services."},
        headers={
            "If-Match": human_update.headers["etag"],
            "Idempotency-Key": "job-published-human-update-0002",
        },
    )
    assert delayed_pre_rename_replay.status_code == 200, delayed_pre_rename_replay.text
    assert delayed_pre_rename_replay.content == later_update.content
    assert delayed_pre_rename_replay.headers["etag"] == later_update.headers["etag"]
    assert delayed_pre_rename_replay.headers["idempotency-replayed"] == "true"

    lifecycle_agent = Principal(
        subject="employer_owner",
        method="agent_grant",
        scopes=frozenset({"jobs:write"}),
        grant_mode="direct",
        resource_type="organization",
        resource_id=organization.json()["id"],
    )
    close_headers = {
        "If-Match": current_after_rename.headers["etag"],
        "Idempotency-Key": "job-agent-close-0001",
    }
    as_principal(app, lifecycle_agent)
    denied_agent_close = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/close",
        headers=close_headers,
    )
    assert denied_agent_close.status_code == 403
    as_principal(app, human("employer_owner"))
    after_agent_denial = await client.get("/v1/organizations/acme/jobs/backend-engineer")
    assert after_agent_denial.json()["status"] == "published"
    assert after_agent_denial.json()["version"] == current_after_rename.json()["version"]
    assert after_agent_denial.headers["etag"] == current_after_rename.headers["etag"]

    lifecycle_api_key = Principal(
        subject="employer_owner",
        method="agent_api_key",
        scopes=frozenset({"jobs:write"}),
    )
    as_principal(app, lifecycle_api_key)
    denied_api_key_close = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/close",
        headers={
            "If-Match": current_after_rename.headers["etag"],
            "Idempotency-Key": "job-api-key-close-0001",
        },
    )
    assert denied_api_key_close.status_code == 403
    as_principal(app, human("employer_owner"))
    after_api_key_denial = await client.get("/v1/organizations/acme/jobs/backend-engineer")
    assert after_api_key_denial.json()["status"] == "published"
    assert after_api_key_denial.json()["version"] == current_after_rename.json()["version"]
    assert after_api_key_denial.headers["etag"] == current_after_rename.headers["etag"]

    human_close_headers = {
        "If-Match": current_after_rename.headers["etag"],
        "Idempotency-Key": "job-human-close-0001",
    }
    human_close = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/close",
        headers=human_close_headers,
    )
    assert human_close.status_code == 200, human_close.text
    assert human_close.json()["status"] == "closed"
    assert human_close.json()["version"] == current_after_rename.json()["version"] + 1
    delayed_close_replay = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/close",
        headers=human_close_headers,
    )
    assert delayed_close_replay.status_code == 200, delayed_close_replay.text
    assert delayed_close_replay.content == human_close.content
    assert delayed_close_replay.headers["etag"] == human_close.headers["etag"]
    assert delayed_close_replay.headers["idempotency-replayed"] == "true"

    async with app.state.session_factory() as session:
        closed_receipt = await session.get(
            JobVersion, (job.json()["id"], human_close.json()["version"])
        )
        assert closed_receipt is not None
        closed_receipt.response_body = closed_receipt.response_body.replace(
            '"status":"closed"', '"status":"published"', 1
        )
        await session.commit()
    tampered_close_replay = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/close",
        headers=human_close_headers,
    )
    assert tampered_close_replay.status_code == 503

    async with app.state.session_factory() as session:
        legacy_receipt = await session.get(JobVersion, (job.json()["id"], job.json()["version"]))
        assert legacy_receipt is not None
        legacy_receipt.response_body = ""
        legacy_receipt.response_sha256 = ""
        await session.commit()
    legacy_create_replay = await client.post(
        "/v1/organizations/acme/jobs",
        json={
            "slug": "backend-engineer",
            "title": "Backend Engineer",
            "description": "Build reliable application services.",
            "location": "Singapore",
            "work_mode": "hybrid",
            "employment_type": "full_time",
        },
        headers={"Idempotency-Key": "job-backend-0001"},
    )
    assert legacy_create_replay.status_code == 503


async def test_organization_receipts_snapshot_state_and_legacy_empty_fail_closed(
    api_client,
) -> None:
    app, client = api_client
    owner = "organization-receipt-owner"
    as_principal(app, human(owner))
    payload = {"slug": "receipt-org", "name": "Receipt Org", "visibility": "private"}
    key_headers = {"Idempotency-Key": "organization-receipt-create-0001"}
    created = await client.post("/v1/organizations", json=payload, headers=key_headers)
    assert created.status_code == 201, created.text
    assert created.headers.get("idempotency-replayed") is None
    exact_replay = await client.post("/v1/organizations", json=payload, headers=key_headers)
    assert exact_replay.status_code == 201
    assert exact_replay.content == created.content
    assert exact_replay.headers["idempotency-replayed"] == "true"
    assert exact_replay.headers["etag"] == created.headers["etag"]

    async with app.state.session_factory() as session:
        organization = await session.scalar(
            select(Organization).where(Organization.slug == "receipt-org")
        )
        assert organization is not None
        now = datetime.now(UTC)
        evidence_bytes = b"receipt evidence"
        evidence_sha256 = sha256(evidence_bytes).hexdigest()
        verification_id = "22222222-2222-4222-8222-222222222221"
        claim_digest = material_claim_digest(
            organization_id=organization.id,
            organization_name=organization.name,
            organization_website_url=organization.website_url,
            organization_material_version=organization.verification_material_version,
            evidence_kind="other",
            metadata={},
            artifact_content_type="text/plain",
            artifact_sha256=evidence_sha256,
            artifact_size_bytes=len(evidence_bytes),
        )
        evidence_path = canonical_evidence_path(organization.id, verification_id, evidence_sha256)
        app.state.store.write_immutable_bytes(evidence_path, evidence_bytes)
        session.add_all(
            (
                OrganizationVerification(
                    id=verification_id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    submitted_by_owner_id=owner,
                    material_claim_digest=claim_digest,
                    created_at=now - timedelta(seconds=2),
                ),
                OrganizationVerificationEvidence(
                    id="22222222-2222-4222-8222-222222222222",
                    verification_id=verification_id,
                    evidence_kind="other",
                    metadata_json="{}",
                    artifact_content_type="text/plain",
                    artifact_sha256=evidence_sha256,
                    artifact_size_bytes=len(evidence_bytes),
                    storage_path=evidence_path,
                    created_at=now - timedelta(seconds=2),
                    retention_expires_at=now + timedelta(days=365),
                ),
                OrganizationVerificationEvent(
                    id="22222222-2222-4222-8222-222222222223",
                    verification_id=verification_id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    to_state="active",
                    actor_id="reviewer:preprovisioned",
                    actor_role="recruiting_verifier",
                    policy_version="recruiting-control-v1",
                    material_claim_digest=claim_digest,
                    expires_at=now + timedelta(days=30),
                    occurred_at=now - timedelta(seconds=1),
                ),
            )
        )
        await session.commit()

    drifted = await client.post("/v1/organizations", json=payload, headers=key_headers)
    assert drifted.status_code == 201
    assert drifted.content == created.content
    assert drifted.json()["recruiting_verification_active"] is False
    assert drifted.headers["etag"] == created.headers["etag"]

    update_key = "organization-receipt-update-0001"
    updated = await client.put(
        "/v1/organizations/receipt-org",
        json={"name": "Receipt Org Updated"},
        headers={
            "Idempotency-Key": update_key,
            "If-Match": created.headers["etag"],
        },
    )
    assert updated.status_code == 200, updated.text
    updated_replay = await client.put(
        "/v1/organizations/receipt-org",
        json={"name": "Receipt Org Updated"},
        headers={
            "Idempotency-Key": update_key,
            "If-Match": created.headers["etag"],
        },
    )
    assert updated_replay.status_code == 200
    assert updated_replay.content == updated.content
    assert updated_replay.headers["idempotency-replayed"] == "true"
    assert updated_replay.headers["etag"] == updated.headers["etag"]

    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "organization-receipt-create-0001"
            )
        )
        assert receipt is not None
        receipt.response_body = ""
        await session.commit()
    legacy = await client.post("/v1/organizations", json=payload, headers=key_headers)
    assert legacy.status_code == 503


async def test_concurrent_same_key_job_create_replays_the_exact_winner(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    as_principal(app, human("concurrent-owner"))
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "concurrent-org", "name": "Concurrent Org"},
        headers={"Idempotency-Key": "concurrent-org-create-0001"},
    )
    assert organization.status_code == 201, organization.text
    payload = {
        "slug": "concurrent-role",
        "title": "Concurrent Role",
        "description": "One canonical result.",
    }
    headers = {"Idempotency-Key": "concurrent-job-create-0001"}
    original_identifier_is_reserved = main_module.identifier_is_reserved
    both_prechecks_complete = asyncio.Event()
    precheck_arrivals = 0

    async def synchronized_identifier_check(*args, **kwargs):
        nonlocal precheck_arrivals
        result = await original_identifier_is_reserved(*args, **kwargs)
        if kwargs.get("identifier") == "concurrent-role" and precheck_arrivals < 2:
            precheck_arrivals += 1
            if precheck_arrivals == 2:
                both_prechecks_complete.set()
            await asyncio.wait_for(both_prechecks_complete.wait(), timeout=2)
        return result

    monkeypatch.setattr(main_module, "identifier_is_reserved", synchronized_identifier_check)

    first, second = await asyncio.gather(
        client.post("/v1/organizations/concurrent-org/jobs", json=payload, headers=headers),
        client.post("/v1/organizations/concurrent-org/jobs", json=payload, headers=headers),
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert precheck_arrivals == 2
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert first.headers["location"] == second.headers["location"]
    assert sorted(
        response.headers.get("idempotency-replayed", "false") for response in (first, second)
    ) == ["false", "true"]
    async with app.state.session_factory() as session:
        jobs = (
            await session.scalars(
                select(Job).where(Job.organization_id == organization.json()["id"])
            )
        ).all()
        versions = (
            await session.scalars(select(JobVersion).where(JobVersion.job_id == first.json()["id"]))
        ).all()
    assert len(jobs) == 1
    assert len(versions) == 1


async def test_concurrent_same_key_application_submission_replays_one_snapshot(api_client) -> None:
    app, client = api_client
    as_principal(app, human("application-race-applicant"))
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "application-race-profile-create-0001"},
    )
    assert profile.status_code == 201, profile.text
    now = datetime.now(UTC)
    evidence_bytes = b"b"
    evidence_sha256 = sha256(evidence_bytes).hexdigest()
    organization = Organization(
        id="20000000-0000-4000-8000-000000000001",
        owner_id="application-race-employer",
        slug="application-race-org",
        name="Application Race Org",
        description=None,
        website_url=None,
        visibility="public",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    claim_digest = material_claim_digest(
        organization_id=organization.id,
        organization_name=organization.name,
        organization_website_url=organization.website_url,
        organization_material_version=organization.verification_material_version,
        evidence_kind="other",
        metadata={},
        artifact_content_type="text/plain",
        artifact_sha256=evidence_sha256,
        artifact_size_bytes=len(evidence_bytes),
    )
    verification = OrganizationVerification(
        id="20000000-0000-4000-8000-000000000002",
        organization_id=organization.id,
        purpose="recruiting_control",
        submitted_by_owner_id=organization.owner_id,
        material_claim_digest=claim_digest,
        created_at=now,
    )
    job = Job(
        id="20000000-0000-4000-8000-000000000003",
        organization_id=organization.id,
        slug="application-race-role",
        title="Application Race Role",
        description="Only one application may be submitted per applicant.",
        status="published",
        version=1,
        published_at=now,
        created_at=now,
        updated_at=now,
    )
    evidence_path = canonical_evidence_path(organization.id, verification.id, evidence_sha256)
    app.state.store.write_immutable_bytes(evidence_path, evidence_bytes)
    async with app.state.session_factory() as session:
        session.add_all(
            (
                organization,
                verification,
                job,
                OrganizationVerificationEvidence(
                    id="20000000-0000-4000-8000-000000000004",
                    verification_id=verification.id,
                    evidence_kind="other",
                    metadata_json="{}",
                    artifact_content_type="text/plain",
                    artifact_sha256=evidence_sha256,
                    artifact_size_bytes=len(evidence_bytes),
                    storage_path=evidence_path,
                    created_at=now,
                    retention_expires_at=now + timedelta(days=1),
                ),
                OrganizationVerificationEvent(
                    id="20000000-0000-4000-8000-000000000005",
                    verification_id=verification.id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    to_state="active",
                    actor_id="reviewer:preprovisioned",
                    actor_role="recruiting_verifier",
                    policy_version="recruiting-control-v1",
                    material_claim_digest=claim_digest,
                    expires_at=now + timedelta(days=1),
                    occurred_at=now,
                ),
            )
        )
        await session.commit()

    payload = {
        "message": "The durable idempotency receipt is the source of truth.",
        "snapshot_kind": "profile",
        "snapshot_identifier": "ada-lovelace",
        "human_confirmed": True,
    }
    headers = {"Idempotency-Key": "application-race-submit-0001"}
    first, second = await asyncio.gather(
        client.post(
            "/v1/organizations/application-race-org/jobs/application-race-role/applications",
            json=payload,
            headers=headers,
        ),
        client.post(
            "/v1/organizations/application-race-org/jobs/application-race-role/applications",
            json=payload,
            headers=headers,
        ),
    )
    assert first.status_code == second.status_code == 201
    assert first.content == second.content
    assert sorted(
        response.headers.get("idempotency-replayed", "false") for response in (first, second)
    ) == ["false", "true"]

    changed_body = await client.post(
        "/v1/organizations/application-race-org/jobs/application-race-role/applications",
        json={**payload, "message": "A different body must not reuse the receipt."},
        headers=headers,
    )
    assert changed_body.status_code == 409
    async with app.state.session_factory() as session:
        applications = (
            await session.scalars(select(Application).where(Application.job_id == job.id))
        ).all()
    assert len(applications) == 1
    snapshot_files = list((app.state.store.root / "applications").glob("*/snapshot.md"))
    assert snapshot_files == [app.state.store._absolute(applications[0].snapshot_storage_path)]


async def test_member_invitation_and_agents_cannot_establish_or_publish(api_client) -> None:
    app, client = api_client
    await create_profile(app, client, "admin", "admin-profile")
    await create_profile(app, client, "admin-two", "admin-two-profile")
    as_principal(app, human("owner"))
    created = await client.post(
        "/v1/organizations",
        json={"slug": "northstar", "name": "Northstar", "visibility": "private"},
        headers={"Idempotency-Key": "organization-northstar-0001"},
    )
    assert created.status_code == 201
    invite = await client.post(
        "/v1/organizations/northstar/admins",
        json={"member_profile_handle": "admin-profile", "role": "admin"},
        headers={"Idempotency-Key": "membership-northstar-0001"},
    )
    assert invite.status_code == 201, invite.text
    assert invite.json()["status"] == "invited"
    replay = await client.post(
        "/v1/organizations/northstar/admins",
        json={"member_profile_handle": "admin-profile", "role": "admin"},
        headers={"Idempotency-Key": "membership-northstar-0001"},
    )
    assert replay.status_code == 201
    assert replay.json()["member_profile_handle"] == "admin-profile"
    assert "member_owner_id" not in replay.json()

    membership_id = invite.json()["id"]
    async with app.state.session_factory() as session:
        memberships_before_impersonation = [
            (row.id, row.member_owner_id, row.role, row.status)
            for row in (
                await session.scalars(
                    select(OrganizationMembership).where(
                        OrganizationMembership.organization_id == created.json()["id"]
                    )
                )
            ).all()
        ]
        organization_event_ids_before_impersonation = (
            await session.scalars(
                select(ChangeEvent.sequence)
                .where(ChangeEvent.resource_type == "organization")
                .order_by(ChangeEvent.sequence)
            )
        ).all()

    as_principal(app, human("owner", impersonated=True))
    owner_members_read = await client.get("/v1/organizations/northstar/members")
    assert owner_members_read.status_code == 200, owner_members_read.text
    impersonated_invite = await client.post(
        "/v1/organizations/northstar/admins",
        json={"member_profile_handle": "admin-two-profile", "role": "member"},
        headers={"Idempotency-Key": "impersonated-membership-invite-0001"},
    )
    assert impersonated_invite.status_code == 403, impersonated_invite.text
    assert impersonated_invite.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    async with app.state.session_factory() as session:
        memberships_after_invite = [
            (row.id, row.member_owner_id, row.role, row.status)
            for row in (
                await session.scalars(
                    select(OrganizationMembership).where(
                        OrganizationMembership.organization_id == created.json()["id"]
                    )
                )
            ).all()
        ]
        assert memberships_after_invite == memberships_before_impersonation
        assert (
            await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "impersonated-membership-invite-0001"
                )
            )
            is None
        )
        assert (
            await session.scalars(
                select(ChangeEvent.sequence)
                .where(ChangeEvent.resource_type == "organization")
                .order_by(ChangeEvent.sequence)
            )
        ).all() == organization_event_ids_before_impersonation

    as_principal(app, human("admin", impersonated=True))
    inbox_read = await client.get("/v1/organization-membership-invitations")
    assert inbox_read.status_code == 200, inbox_read.text
    assert any(item["id"] == membership_id for item in inbox_read.json()["invitations"])
    impersonated_accept = await client.post(
        f"/v1/organizations/northstar/memberships/{membership_id}/accept",
        headers={"Idempotency-Key": "impersonated-membership-accept-0001"},
    )
    assert impersonated_accept.status_code == 403, impersonated_accept.text
    assert impersonated_accept.json()["detail"] == IMPERSONATION_READ_ONLY_CODE
    async with app.state.session_factory() as session:
        membership_after_accept_denial = await session.get(OrganizationMembership, membership_id)
        assert membership_after_accept_denial is not None
        assert membership_after_accept_denial.status == "invited"
        assert (
            await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "impersonated-membership-accept-0001"
                )
            )
            is None
        )

    as_principal(app, human("admin"))
    accepted = await client.post(
        f"/v1/organizations/northstar/memberships/{membership_id}/accept",
        headers={"Idempotency-Key": "membership-northstar-accept-0001"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "active"

    async with app.state.session_factory() as session:
        organization_event_ids_after_ordinary_accept = (
            await session.scalars(
                select(ChangeEvent.sequence)
                .where(ChangeEvent.resource_type == "organization")
                .order_by(ChangeEvent.sequence)
            )
        ).all()

    as_principal(app, human("owner", impersonated=True))
    impersonated_remove = await client.delete(
        f"/v1/organizations/northstar/memberships/{membership_id}",
        headers={"Idempotency-Key": "impersonated-membership-remove-0001"},
    )
    assert impersonated_remove.status_code == 403, impersonated_remove.text
    assert impersonated_remove.json()["detail"] == IMPERSONATION_READ_ONLY_CODE
    async with app.state.session_factory() as session:
        membership_after_remove_denial = await session.get(OrganizationMembership, membership_id)
        assert membership_after_remove_denial is not None
        assert membership_after_remove_denial.status == "active"
        assert (
            await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "impersonated-membership-remove-0001"
                )
            )
            is None
        )
        assert (
            await session.scalars(
                select(ChangeEvent.sequence)
                .where(ChangeEvent.resource_type == "organization")
                .order_by(ChangeEvent.sequence)
            )
        ).all() == organization_event_ids_after_ordinary_accept

    agent = Principal(
        subject="admin",
        method="agent_grant",
        scopes=frozenset({"organizations:write", "jobs:write"}),
        grant_mode="direct",
        resource_type="organization",
        resource_id=created.json()["id"],
    )
    as_principal(app, agent)
    agent_org = await client.post(
        "/v1/organizations",
        json={"slug": "agent-forged", "name": "Agent Forged"},
        headers={"Idempotency-Key": "agent-org-0001"},
    )
    assert agent_org.status_code == 403
    draft = await client.post(
        "/v1/organizations/northstar/jobs",
        json={"slug": "operator", "title": "Operator", "description": "Run operations."},
        headers={"Idempotency-Key": "agent-job-0001"},
    )
    assert draft.status_code == 201, draft.text
    agent_draft_update = await client.put(
        "/v1/organizations/northstar/jobs/operator",
        json={"title": "Senior Operator"},
        headers={
            "If-Match": draft.headers["etag"],
            "Idempotency-Key": "agent-job-update-0001",
        },
    )
    assert agent_draft_update.status_code == 200, agent_draft_update.text
    assert agent_draft_update.json()["title"] == "Senior Operator"
    agent_publish = await client.post(
        "/v1/organizations/northstar/jobs/operator/lifecycle/publish",
        headers={
            "If-Match": agent_draft_update.headers["etag"],
            "Idempotency-Key": "agent-publish-0001",
        },
    )
    assert agent_publish.status_code == 403

    async with app.state.session_factory() as session:
        current_job = await session.scalar(select(Job).where(Job.id == draft.json()["id"]))
        assert current_job is not None
        current_job.status = "published"
        current_job.version += 1
        current_job.updated_at = datetime.now(UTC)
        await session.commit()

    delayed_agent_replay = await client.put(
        "/v1/organizations/northstar/jobs/operator",
        json={"title": "Senior Operator"},
        headers={
            "If-Match": draft.headers["etag"],
            "Idempotency-Key": "agent-job-update-0001",
        },
    )
    assert delayed_agent_replay.status_code == 200, delayed_agent_replay.text
    assert delayed_agent_replay.json() == agent_draft_update.json()
    assert delayed_agent_replay.headers["etag"] == agent_draft_update.headers["etag"]
    assert delayed_agent_replay.headers["idempotency-replayed"] == "true"


async def test_membership_invitation_replay_is_exact_and_fail_closed(api_client) -> None:
    app, client = api_client
    owner = "membership-invite-replay-owner"
    recipient = "membership-invite-replay-recipient"
    second_recipient = "membership-invite-replay-second"
    await create_profile(app, client, recipient, recipient)
    await create_profile(app, client, second_recipient, second_recipient)
    as_principal(app, human(owner))
    first_org = await client.post(
        "/v1/organizations",
        json={"slug": "membership-invite-replay", "name": "Invitation Replay"},
        headers={"Idempotency-Key": "membership-invite-replay-org-0001"},
    )
    second_org = await client.post(
        "/v1/organizations",
        json={"slug": "membership-invite-cross", "name": "Invitation Cross"},
        headers={"Idempotency-Key": "membership-invite-cross-org-0001"},
    )
    assert first_org.status_code == second_org.status_code == 201
    invite_path = "/v1/organizations/membership-invite-replay/admins"
    invite_headers = {"Idempotency-Key": "membership-invite-replay-0001"}
    first, second = await asyncio.gather(
        client.post(
            invite_path,
            json={"member_profile_handle": recipient, "role": "member"},
            headers=invite_headers,
        ),
        client.post(
            invite_path,
            json={"member_profile_handle": recipient, "role": "member"},
            headers=invite_headers,
        ),
    )
    assert first.status_code == second.status_code == 201
    assert first.content == second.content
    assert sorted(
        response.headers.get("idempotency-replayed", "false") for response in (first, second)
    ) == ["false", "true"]
    assert first.json()["status"] == "invited"
    assert first.json()["created_at"].endswith("Z")
    omitted_default_role = await client.post(
        invite_path,
        json={"member_profile_handle": recipient},
        headers=invite_headers,
    )
    assert omitted_default_role.status_code == 201
    assert omitted_default_role.content == first.content
    same_org_invite = await client.post(
        invite_path,
        json={"member_profile_handle": second_recipient, "role": "member"},
        headers={"Idempotency-Key": "membership-invite-replay-second-0001"},
    )
    assert same_org_invite.status_code == 201
    cross_invite = await client.post(
        "/v1/organizations/membership-invite-cross/admins",
        json={"member_profile_handle": recipient, "role": "member"},
        headers={"Idempotency-Key": "membership-invite-cross-0001"},
    )
    assert cross_invite.status_code == 201
    async with app.state.session_factory() as session:
        same_org_row = await session.get(OrganizationMembership, same_org_invite.json()["id"])
        assert same_org_row is not None
        swapped_resource_id = f"{same_org_row.id}:{main_module._organization_membership_generation_digest(same_org_row)}"

    async def corrupt_invite_receipt(field: str, value: object) -> None:
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "membership-invite-replay-0001",
                )
            )
            assert receipt is not None
            original = getattr(receipt, field)
            setattr(receipt, field, value)
            await session.commit()
        replay = await client.post(
            invite_path,
            json={"member_profile_handle": recipient, "role": "member"},
            headers=invite_headers,
        )
        assert replay.status_code == 503
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "membership-invite-replay-0001",
                )
            )
            assert receipt is not None
            setattr(receipt, field, original)
            await session.commit()

    await corrupt_invite_receipt("response_status", 200)
    await corrupt_invite_receipt("response_body", "unexpected")
    await corrupt_invite_receipt("response_headers", '{"X-Unexpected":"1"}')
    await corrupt_invite_receipt("resource_type", "organization")
    await corrupt_invite_receipt("resource_id", swapped_resource_id)

    membership_id = first.json()["id"]
    async with app.state.session_factory() as session:
        membership = await session.get(OrganizationMembership, membership_id)
        assert membership is not None
        original_role = membership.role
        original_created_at = membership.created_at
        membership.role = "admin"
        await session.commit()
    changed_role = await client.post(
        invite_path,
        json={"member_profile_handle": recipient, "role": "member"},
        headers=invite_headers,
    )
    assert changed_role.status_code == 503
    async with app.state.session_factory() as session:
        membership = await session.get(OrganizationMembership, membership_id)
        assert membership is not None
        membership.role = original_role
        membership.created_at = original_created_at + timedelta(seconds=1)
        await session.commit()
    changed_created_at = await client.post(
        invite_path,
        json={"member_profile_handle": recipient, "role": "member"},
        headers=invite_headers,
    )
    assert changed_created_at.status_code == 503
    async with app.state.session_factory() as session:
        membership = await session.get(OrganizationMembership, membership_id)
        assert membership is not None
        membership.created_at = original_created_at
        membership.status = "active"
        await session.commit()
    changed_state = await client.post(
        invite_path,
        json={"member_profile_handle": recipient, "role": "member"},
        headers=invite_headers,
    )
    assert changed_state.status_code == 503
    async with app.state.session_factory() as session:
        membership = await session.get(OrganizationMembership, membership_id)
        assert membership is not None
        await session.delete(membership)
        await session.commit()
    deleted_state = await client.post(
        invite_path,
        json={"member_profile_handle": recipient, "role": "member"},
        headers=invite_headers,
    )
    assert deleted_state.status_code == 503

    async with app.state.session_factory() as session:
        memberships = (
            await session.scalars(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == first_org.json()["id"]
                )
            )
        ).all()
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == first_org.json()["id"],
                    ChangeEvent.event_type == "organization.member_invited",
                )
            )
        ).all()
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "membership-invite-replay-0001",
                )
            )
        ).all()
    assert [membership.id for membership in memberships] == [same_org_invite.json()["id"]]
    assert len(events) == 4
    assert len(receipts) == 1


async def test_membership_remove_replay_requires_current_owner(api_client) -> None:
    app, client = api_client
    owner = "membership-former-owner"
    recipient = "membership-former-owner-recipient"
    await create_profile(app, client, recipient, recipient)
    as_principal(app, human(owner))
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "membership-former-owner", "name": "Former Owner"},
        headers={"Idempotency-Key": "membership-former-owner-org-0001"},
    )
    assert organization.status_code == 201
    invite = await client.post(
        "/v1/organizations/membership-former-owner/admins",
        json={"member_profile_handle": recipient, "role": "member"},
        headers={"Idempotency-Key": "membership-former-owner-invite-0001"},
    )
    assert invite.status_code == 201
    as_principal(app, human(recipient))
    accepted = await client.post(
        f"/v1/organizations/membership-former-owner/memberships/{invite.json()['id']}/accept",
        headers={"Idempotency-Key": "membership-former-owner-accept-0001"},
    )
    assert accepted.status_code == 200
    as_principal(app, human(owner))
    remove_path = f"/v1/organizations/membership-former-owner/memberships/{invite.json()['id']}"
    removed = await client.delete(
        remove_path,
        headers={"Idempotency-Key": "membership-former-owner-remove-0001"},
    )
    assert removed.status_code == 204
    async with app.state.session_factory() as session:
        row = await session.get(Organization, organization.json()["id"])
        assert row is not None
        row.owner_id = "membership-new-owner"
        await session.commit()
    as_principal(app, human(owner))
    former_owner_replay = await client.delete(
        remove_path,
        headers={"Idempotency-Key": "membership-former-owner-remove-0001"},
    )
    assert former_owner_replay.status_code == 404
    assert "idempotency-replayed" not in former_owner_replay.headers


async def test_membership_inbox_and_owner_inventory_are_private_human_workflows(api_client) -> None:
    app, client = api_client
    await create_profile(app, client, "admin-one", "admin-one")
    await create_profile(app, client, "admin-two", "admin-two")
    await create_profile(
        app, client, "private-recipient", "private-recipient", visibility="private"
    )
    as_principal(app, human("owner"))
    for slug in ("membership-one", "membership-two"):
        created = await client.post(
            "/v1/organizations",
            json={"slug": slug, "name": slug.replace("-", " ").title(), "visibility": "private"},
            headers={"Idempotency-Key": f"create-{slug}-0001"},
        )
        assert created.status_code == 201, created.text

    invite_one = await client.post(
        "/v1/organizations/membership-one/admins",
        json={"member_profile_handle": "admin-one", "role": "admin"},
        headers={"Idempotency-Key": "membership-one-admin-one-0001"},
    )
    invite_two = await client.post(
        "/v1/organizations/membership-two/admins",
        json={"member_profile_handle": "admin-one", "role": "member"},
        headers={"Idempotency-Key": "membership-two-admin-one-0001"},
    )
    invite_other = await client.post(
        "/v1/organizations/membership-one/admins",
        json={"member_profile_handle": "admin-two", "role": "member"},
        headers={"Idempotency-Key": "membership-one-admin-two-0001"},
    )
    assert invite_one.status_code == invite_two.status_code == invite_other.status_code == 201
    assert "member_owner_id" not in invite_one.json()
    assert invite_one.json()["member_profile_handle"] == "admin-one"
    legacy_raw_identifier = await client.post(
        "/v1/organizations/membership-one/admins",
        json={"member_owner_id": "admin-one", "role": "member"},
        headers={"Idempotency-Key": "membership-legacy-raw-id-0001"},
    )
    assert legacy_raw_identifier.status_code == 422
    private_invite = await client.post(
        "/v1/organizations/membership-one/admins",
        json={"member_profile_handle": "private-recipient", "role": "member"},
        headers={"Idempotency-Key": "membership-private-recipient-0001"},
    )
    assert private_invite.status_code == 404

    owner_page_one = await client.get("/v1/organizations/membership-one/members?limit=1")
    assert owner_page_one.status_code == 200, owner_page_one.text
    assert len(owner_page_one.json()["members"]) == 1
    assert owner_page_one.json()["next_cursor"]
    owner_page_two = await client.get(
        "/v1/organizations/membership-one/members",
        params={"limit": 1, "cursor": owner_page_one.json()["next_cursor"]},
    )
    member_handles = {
        owner_page_one.json()["members"][0]["member_profile_handle"],
        owner_page_two.json()["members"][0]["member_profile_handle"],
    }
    assert member_handles == {"admin-one", "admin-two"}
    assert "member_owner_id" not in owner_page_one.text
    cross_organization_cursor = await client.get(
        "/v1/organizations/membership-two/members",
        params={"cursor": owner_page_one.json()["next_cursor"]},
    )
    assert cross_organization_cursor.status_code == 400

    as_principal(app, human("admin-one"))
    inbox_one = await client.get("/v1/organization-membership-invitations?limit=1")
    assert inbox_one.status_code == 200, inbox_one.text
    assert inbox_one.json()["next_cursor"]
    first = inbox_one.json()["invitations"][0]
    assert set(first) == {
        "id",
        "organization_id",
        "organization_slug",
        "organization_name",
        "role",
        "status",
        "created_at",
    }
    inbox_two = await client.get(
        "/v1/organization-membership-invitations",
        params={"limit": 1, "cursor": inbox_one.json()["next_cursor"]},
    )
    assert {
        first["organization_slug"],
        inbox_two.json()["invitations"][0]["organization_slug"],
    } == {"membership-one", "membership-two"}

    cross_recipient = await client.post(
        f"/v1/organizations/membership-one/memberships/{invite_other.json()['id']}/accept",
        headers={"Idempotency-Key": "membership-cross-recipient-0001"},
    )
    assert cross_recipient.status_code == 404
    own_invite_id = next(
        item["id"]
        for item in (first, inbox_two.json()["invitations"][0])
        if item["organization_slug"] == "membership-one"
    )
    accepted = await client.post(
        f"/v1/organizations/membership-one/memberships/{own_invite_id}/accept",
        headers={"Idempotency-Key": "membership-one-accept-0001"},
    )
    assert accepted.status_code == 200, accepted.text
    remaining = await client.get("/v1/organization-membership-invitations")
    assert all(
        item["organization_slug"] != "membership-one" for item in remaining.json()["invitations"]
    )
    assert (await client.get("/v1/organizations/membership-one/members")).status_code == 404

    as_principal(app, human("admin-two"))
    isolated = await client.get(
        "/v1/organization-membership-invitations",
        params={"cursor": inbox_one.json()["next_cursor"]},
    )
    assert isolated.status_code == 400

    agent = Principal(
        subject="admin-two",
        method="agent_grant",
        scopes=frozenset({"organizations:read"}),
        grant_mode="direct",
        resource_type="owner",
    )
    as_principal(app, agent)
    assert (await client.get("/v1/organization-membership-invitations")).status_code == 403
    assert (await client.get("/v1/organizations/membership-one/members")).status_code == 403

    as_principal(app, human("owner"))
    active_members = await client.get("/v1/organizations/membership-one/members")
    assert any(
        member["member_profile_handle"] == "admin-one" and member["status"] == "active"
        for member in active_members.json()["members"]
    )
    active_member_id = next(
        member["id"]
        for member in active_members.json()["members"]
        if member["member_profile_handle"] == "admin-one"
    )
    removed = await client.delete(
        f"/v1/organizations/membership-one/memberships/{active_member_id}",
        headers={"Idempotency-Key": "membership-one-remove-0001"},
    )
    assert removed.status_code == 204
    as_principal(app, human("admin-one"))
    assert (await client.get("/v1/organizations/membership-one")).status_code == 404


async def test_membership_reads_are_human_only_in_openapi(api_client) -> None:
    app, _ = api_client
    schema = app.openapi()
    operations = {
        "/v1/organization-membership-invitations": {"get"},
        "/v1/organizations/{organization_slug}/members": {"get"},
        "/v1/organizations/{organization_slug}/admins": {"post"},
        "/v1/organizations/{organization_slug}/memberships/{membership_id}/accept": {"post"},
        "/v1/organizations/{organization_slug}/memberships/{membership_id}": {"delete"},
    }
    for path, methods in operations.items():
        for method in methods:
            operation = schema["paths"][path][method]
            assert operation["security"] == [{"ClerkBearerAuth": []}]
            assert operation["x-connectmd-human-only"] is True
            assert "401" in operation["responses"]

    for path, method in (
        (
            "/v1/organizations/{organization_slug}/memberships/{membership_id}/accept",
            "post",
        ),
        ("/v1/organizations/{organization_slug}/memberships/{membership_id}", "delete"),
    ):
        operation = schema["paths"][path][method]
        key_parameters = [
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        ]
        assert len(key_parameters) == 1
        key_schema = key_parameters[0]["schema"]
        assert key_parameters[0]["in"] == "header"
        assert key_parameters[0]["required"] is True
        assert key_schema["minLength"] == 1
        assert key_schema["maxLength"] == 128
        assert key_schema["pattern"] == main_module._IDEMPOTENCY_KEY_PATTERN
        assert {"400", "403", "404", "409", "428", "503"}.issubset(operation["responses"])

    job_update = schema["paths"]["/v1/organizations/{organization_slug}/jobs/{job_slug}"]["put"]
    assert job_update["security"] == [{"BearerAuth": []}]
    assert "x-connectmd-human-only" not in job_update
    description = " ".join(job_update["description"].split())
    assert "Agent Grants may update drafts" in description
    assert "published or closed job requires an authenticated Clerk human" in description


async def test_membership_authority_and_lock_order_are_fail_closed(api_client) -> None:
    app, client = api_client
    owner = "membership-order-owner"
    recipient = "membership-order-recipient"
    await create_profile(app, client, recipient, recipient)
    as_principal(app, human(owner))
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "membership-order", "name": "Membership Order"},
        headers={"Idempotency-Key": "membership-order-org-0001"},
    )
    assert organization.status_code == 201, organization.text

    as_principal(
        app,
        Principal(
            subject=owner,
            method="agent_grant",
            scopes=frozenset({"organizations:write"}),
            resource_type="owner",
        ),
    )
    agent_unknown = await client.post(
        "/v1/organizations/does-not-exist/admins",
        json={"member_profile_handle": recipient, "role": "member"},
    )
    assert agent_unknown.status_code == 403
    agent_accept = await client.post(
        "/v1/organizations/membership-order/memberships/unknown/accept"
    )
    assert agent_accept.status_code == 403
    agent_remove = await client.delete("/v1/organizations/membership-order/memberships/unknown")
    assert agent_remove.status_code == 403

    as_principal(app, human(owner))
    missing_key_before_profile_lookup = await client.post(
        "/v1/organizations/membership-order/admins",
        json={"member_profile_handle": "not-a-public-profile", "role": "member"},
    )
    assert missing_key_before_profile_lookup.status_code == 428

    source = inspect.getsource(main_module.create_app)
    add_start = source.index("async def add_organization_admin")
    accept_start = source.index("async def accept_organization_membership")
    remove_start = source.index("async def remove_organization_admin")
    add_source = source[add_start:accept_start]
    accept_source = source[accept_start:remove_start]
    remove_source = source[remove_start:]
    assert add_source.index('if principal.method != "clerk_jwt"') < add_source.index(
        "organization_by_slug(session, organization_slug, for_update=True)"
    )
    assert add_source.index("key = idempotency_key") < add_source.index("public_profile_by_handle")
    assert accept_source.index(
        "organization_by_slug(session, organization_slug, for_update=True)"
    ) < accept_source.index("OrganizationMembership")
    assert "OrganizationMembership" in accept_source and ".with_for_update()" in accept_source
    assert remove_source.index(
        "organization_by_slug(session, organization_slug, for_update=True)"
    ) < remove_source.index("OrganizationMembership")
    assert remove_source.index("await assert_organization_authority(") < remove_source.index(
        "replay = await idempotency_replay"
    )
    assert "OrganizationMembership" in remove_source and ".with_for_update()" in remove_source


async def test_membership_accept_remove_receipts_are_atomic_and_collision_safe(api_client) -> None:
    app, client = api_client
    owner = "membership-receipt-owner"
    accept_recipient = "membership-receipt-accept"
    accept_other = "membership-receipt-other"
    remove_recipient = "membership-receipt-remove"
    remove_other = "membership-receipt-remove-other"
    for subject in (accept_recipient, accept_other, remove_recipient, remove_other):
        await create_profile(app, client, subject, subject)

    as_principal(app, human(owner))
    accept_org = await client.post(
        "/v1/organizations",
        json={"slug": "membership-receipt-accept", "name": "Accept Receipts"},
        headers={"Idempotency-Key": "membership-receipt-accept-org-0001"},
    )
    remove_org = await client.post(
        "/v1/organizations",
        json={"slug": "membership-receipt-remove", "name": "Remove Receipts"},
        headers={"Idempotency-Key": "membership-receipt-remove-org-0001"},
    )
    assert accept_org.status_code == remove_org.status_code == 201

    accept_invite = await client.post(
        "/v1/organizations/membership-receipt-accept/admins",
        json={"member_profile_handle": accept_recipient, "role": "admin"},
        headers={"Idempotency-Key": "membership-receipt-accept-invite-0001"},
    )
    accept_other_invite = await client.post(
        "/v1/organizations/membership-receipt-accept/admins",
        json={"member_profile_handle": accept_other, "role": "member"},
        headers={"Idempotency-Key": "membership-receipt-accept-other-0001"},
    )
    remove_invite = await client.post(
        "/v1/organizations/membership-receipt-remove/admins",
        json={"member_profile_handle": remove_recipient, "role": "member"},
        headers={"Idempotency-Key": "membership-receipt-remove-invite-0001"},
    )
    remove_other_invite = await client.post(
        "/v1/organizations/membership-receipt-remove/admins",
        json={"member_profile_handle": remove_other, "role": "member"},
        headers={"Idempotency-Key": "membership-receipt-remove-other-0001"},
    )
    assert (
        accept_invite.status_code
        == accept_other_invite.status_code
        == remove_invite.status_code
        == remove_other_invite.status_code
        == 201
    )

    as_principal(app, human(accept_recipient))
    accept_path = f"/v1/organizations/membership-receipt-accept/memberships/{accept_invite.json()['id']}/accept"
    missing_accept_key = await client.post(accept_path)
    malformed_accept_key = await client.post(accept_path, headers={"Idempotency-Key": "\n"})
    assert missing_accept_key.status_code == 428
    assert malformed_accept_key.status_code == 400
    accepted = await client.post(
        accept_path,
        headers={"Idempotency-Key": "membership-receipt-accept-0001"},
    )
    accepted_replay = await client.post(
        accept_path,
        headers={"Idempotency-Key": "membership-receipt-accept-0001"},
    )
    assert accepted.status_code == accepted_replay.status_code == 200
    assert accepted.json() == accepted_replay.json()
    assert accepted_replay.headers["idempotency-replayed"] == "true"
    collision_accept = await client.post(
        f"/v1/organizations/membership-receipt-accept/memberships/{accept_other_invite.json()['id']}/accept",
        headers={"Idempotency-Key": "membership-receipt-accept-0001"},
    )
    assert collision_accept.status_code == 409

    async with app.state.session_factory() as session:
        accepted_row = await session.get(OrganizationMembership, accept_invite.json()["id"])
        assert accepted_row is not None
        await session.delete(accepted_row)
        await session.commit()
        acceptance_events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == accept_org.json()["id"],
                    ChangeEvent.event_type == "organization.membership_accepted",
                )
            )
        ).all()
        acceptance_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == accept_recipient,
                    IdempotencyRecord.idempotency_key == "membership-receipt-accept-0001",
                )
            )
        ).all()
        assert len(acceptance_events) == 2
        assert len(acceptance_receipts) == 1

    missing_row_replay = await client.post(
        accept_path,
        headers={"Idempotency-Key": "membership-receipt-accept-0001"},
    )
    assert missing_row_replay.status_code == 503

    as_principal(app, human(remove_recipient))
    remove_accept_path = f"/v1/organizations/membership-receipt-remove/memberships/{remove_invite.json()['id']}/accept"
    remove_accept = await client.post(
        remove_accept_path,
        headers={"Idempotency-Key": "membership-receipt-remove-accept-0001"},
    )
    as_principal(app, human(remove_other))
    remove_other_accept = await client.post(
        f"/v1/organizations/membership-receipt-remove/memberships/{remove_other_invite.json()['id']}/accept",
        headers={"Idempotency-Key": "membership-receipt-remove-other-accept-0001"},
    )
    assert remove_accept.status_code == remove_other_accept.status_code == 200

    as_principal(app, human(owner))
    remove_path = (
        f"/v1/organizations/membership-receipt-remove/memberships/{remove_invite.json()['id']}"
    )
    missing_remove_key = await client.delete(remove_path)
    malformed_remove_key = await client.delete(remove_path, headers={"Idempotency-Key": "\x7f"})
    assert missing_remove_key.status_code == 428
    assert malformed_remove_key.status_code == 400
    removed = await client.delete(
        remove_path,
        headers={"Idempotency-Key": "membership-receipt-remove-0001"},
    )
    removed_replay = await client.delete(
        remove_path,
        headers={"Idempotency-Key": "membership-receipt-remove-0001"},
    )
    assert removed.status_code == removed_replay.status_code == 204
    assert removed.content == removed_replay.content == b""
    assert "content-type" not in removed.headers
    assert "content-type" not in removed_replay.headers
    assert removed_replay.headers["idempotency-replayed"] == "true"
    collision_remove = await client.delete(
        f"/v1/organizations/membership-receipt-remove/memberships/{remove_other_invite.json()['id']}",
        headers={"Idempotency-Key": "membership-receipt-remove-0001"},
    )
    assert collision_remove.status_code == 409
    unknown_remove = await client.delete(
        "/v1/organizations/membership-receipt-remove/memberships/unknown-membership",
        headers={"Idempotency-Key": "membership-receipt-remove-unknown-0001"},
    )
    assert unknown_remove.status_code == 404

    async with app.state.session_factory() as session:
        removal_events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == remove_org.json()["id"],
                    ChangeEvent.event_type == "organization.member_removed",
                )
            )
        ).all()
        removal_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "membership-receipt-remove-0001",
                )
            )
        ).all()
        assert len(removal_events) == 2
        assert len(removal_receipts) == 1


async def test_membership_replay_corruption_and_generation_reincarnation_fail_closed(
    api_client,
) -> None:
    app, client = api_client
    owner = "membership-corrupt-owner"
    recipient = "membership-corrupt-recipient"
    await create_profile(app, client, recipient, recipient)
    as_principal(app, human(owner))
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "membership-corrupt", "name": "Membership Corrupt"},
        headers={"Idempotency-Key": "membership-corrupt-org-0001"},
    )
    assert organization.status_code == 201, organization.text
    invite = await client.post(
        "/v1/organizations/membership-corrupt/admins",
        json={"member_profile_handle": recipient, "role": "member"},
        headers={"Idempotency-Key": "membership-corrupt-invite-0001"},
    )
    assert invite.status_code == 201, invite.text
    membership_id = invite.json()["id"]
    accept_path = f"/v1/organizations/membership-corrupt/memberships/{membership_id}/accept"
    as_principal(app, human(recipient))
    accepted = await client.post(
        accept_path,
        headers={"Idempotency-Key": "membership-corrupt-accept-0001"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["created_at"].endswith("Z")

    async with app.state.session_factory() as session:
        membership = await session.get(OrganizationMembership, membership_id)
        assert membership is not None
        original_created_at = membership.created_at
        await session.delete(membership)
        await session.commit()
    async with app.state.session_factory() as session:
        session.add(
            OrganizationMembership(
                id=membership_id,
                organization_id=organization.json()["id"],
                member_owner_id=recipient,
                member_profile_handle=recipient,
                role="member",
                status="active",
                invited_by_owner_id=owner,
                created_at=original_created_at + timedelta(seconds=1),
            )
        )
        await session.commit()
    reincarnated = await client.post(
        accept_path,
        headers={"Idempotency-Key": "membership-corrupt-accept-0001"},
    )
    assert reincarnated.status_code == 503

    async def corrupt_accept_receipt(field: str, value: object, expected_status: int) -> None:
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == recipient,
                    IdempotencyRecord.idempotency_key == "membership-corrupt-accept-0001",
                )
            )
            assert receipt is not None
            original = getattr(receipt, field)
            setattr(receipt, field, value)
            await session.commit()
        replay = await client.post(
            accept_path,
            headers={"Idempotency-Key": "membership-corrupt-accept-0001"},
        )
        assert replay.status_code == expected_status
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == recipient,
                    IdempotencyRecord.idempotency_key == "membership-corrupt-accept-0001",
                )
            )
            assert receipt is not None
            setattr(receipt, field, original)
            await session.commit()

    await corrupt_accept_receipt("response_status", 201, 503)
    await corrupt_accept_receipt("response_body", "unexpected", 503)
    await corrupt_accept_receipt("response_headers", '{"X-Unexpected":"1"}', 503)
    await corrupt_accept_receipt("resource_id", "wrong-membership", 503)
    await corrupt_accept_receipt(
        "operation",
        "POST:/v1/organizations/membership-corrupt/memberships/other/accept",
        409,
    )

    async with app.state.session_factory() as session:
        membership = await session.get(OrganizationMembership, membership_id)
        assert membership is not None
        membership.member_owner_id = "membership-corrupt-foreign-owner"
        await session.commit()
    owner_mismatch = await client.post(
        accept_path,
        headers={"Idempotency-Key": "membership-corrupt-accept-0001"},
    )
    assert owner_mismatch.status_code == 503
    async with app.state.session_factory() as session:
        membership = await session.get(OrganizationMembership, membership_id)
        assert membership is not None
        membership.member_owner_id = recipient
        await session.commit()
        membership.status = "invited"
        await session.commit()
    non_active = await client.post(
        accept_path,
        headers={"Idempotency-Key": "membership-corrupt-accept-0001"},
    )
    assert non_active.status_code == 503
    async with app.state.session_factory() as session:
        membership = await session.get(OrganizationMembership, membership_id)
        assert membership is not None
        membership.status = "active"
        await session.commit()

    as_principal(app, human(owner))
    remove_path = f"/v1/organizations/membership-corrupt/memberships/{membership_id}"
    removed = await client.delete(
        remove_path,
        headers={"Idempotency-Key": "membership-corrupt-remove-0001"},
    )
    assert removed.status_code == 204

    async def corrupt_remove_receipt(field: str, value: object, expected_status: int) -> None:
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "membership-corrupt-remove-0001",
                )
            )
            assert receipt is not None
            original = getattr(receipt, field)
            setattr(receipt, field, value)
            await session.commit()
        replay = await client.delete(
            remove_path,
            headers={"Idempotency-Key": "membership-corrupt-remove-0001"},
        )
        assert replay.status_code == expected_status
        async with app.state.session_factory() as session:
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "membership-corrupt-remove-0001",
                )
            )
            assert receipt is not None
            setattr(receipt, field, original)
            await session.commit()

    await corrupt_remove_receipt("response_status", 200, 503)
    await corrupt_remove_receipt("response_body", "unexpected", 503)
    await corrupt_remove_receipt("response_headers", '{"X-Unexpected":"1"}', 503)
    await corrupt_remove_receipt("resource_id", "wrong-membership", 503)
    await corrupt_remove_receipt(
        "operation",
        "DELETE:/v1/organizations/membership-corrupt/memberships/other",
        409,
    )
    async with app.state.session_factory() as session:
        session.add(
            OrganizationMembership(
                id=membership_id,
                organization_id=organization.json()["id"],
                member_owner_id=recipient,
                member_profile_handle=recipient,
                role="member",
                status="active",
                invited_by_owner_id=owner,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    reappeared_remove = await client.delete(
        remove_path,
        headers={"Idempotency-Key": "membership-corrupt-remove-0001"},
    )
    assert reappeared_remove.status_code == 503


async def test_membership_same_key_accept_and_remove_concurrency_replays_once(api_client) -> None:
    app, client = api_client
    owner = "membership-concurrency-owner"
    recipient = "membership-concurrency-recipient"
    await create_profile(app, client, recipient, recipient)
    as_principal(app, human(owner))
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "membership-concurrency", "name": "Membership Concurrency"},
        headers={"Idempotency-Key": "membership-concurrency-org-0001"},
    )
    assert organization.status_code == 201, organization.text
    invite = await client.post(
        "/v1/organizations/membership-concurrency/admins",
        json={"member_profile_handle": recipient, "role": "member"},
        headers={"Idempotency-Key": "membership-concurrency-invite-0001"},
    )
    assert invite.status_code == 201, invite.text

    as_principal(app, human(recipient))
    accept_path = (
        f"/v1/organizations/membership-concurrency/memberships/{invite.json()['id']}/accept"
    )
    accept_responses = await asyncio.gather(
        client.post(accept_path, headers={"Idempotency-Key": "membership-concurrency-accept-0001"}),
        client.post(accept_path, headers={"Idempotency-Key": "membership-concurrency-accept-0001"}),
    )
    assert [response.status_code for response in accept_responses] == [200, 200]
    assert sorted(
        response.headers.get("idempotency-replayed", "false") for response in accept_responses
    ) == ["false", "true"]
    assert accept_responses[0].content == accept_responses[1].content

    as_principal(app, human(owner))
    remove_path = f"/v1/organizations/membership-concurrency/memberships/{invite.json()['id']}"
    remove_responses = await asyncio.gather(
        client.delete(
            remove_path, headers={"Idempotency-Key": "membership-concurrency-remove-0001"}
        ),
        client.delete(
            remove_path, headers={"Idempotency-Key": "membership-concurrency-remove-0001"}
        ),
    )
    assert [response.status_code for response in remove_responses] == [204, 204]
    assert sorted(
        response.headers.get("idempotency-replayed", "false") for response in remove_responses
    ) == ["false", "true"]
    assert remove_responses[0].content == remove_responses[1].content == b""

    async with app.state.session_factory() as session:
        accepted_events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == organization.json()["id"],
                    ChangeEvent.event_type == "organization.membership_accepted",
                )
            )
        ).all()
        removed_events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == organization.json()["id"],
                    ChangeEvent.event_type == "organization.member_removed",
                )
            )
        ).all()
        accept_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == recipient,
                    IdempotencyRecord.idempotency_key == "membership-concurrency-accept-0001",
                )
            )
        ).all()
        remove_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner,
                    IdempotencyRecord.idempotency_key == "membership-concurrency-remove-0001",
                )
            )
        ).all()
        assert len(accepted_events) == 2
        assert len(removed_events) == 2
        assert len(accept_receipts) == len(remove_receipts) == 1


async def test_employer_inventory_is_private_tenant_scoped_and_lifecycle_complete(
    api_client,
) -> None:
    app, client = api_client
    owner = "inventory-owner"

    async def create_organization(slug: str) -> dict:
        as_principal(app, human(owner))
        response = await client.post(
            "/v1/organizations",
            json={"slug": slug, "name": slug.replace("-", " ").title()},
            headers={"Idempotency-Key": f"inventory-org-{slug}-0001"},
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def create_job(organization_slug: str, slug: str) -> dict:
        response = await client.post(
            f"/v1/organizations/{organization_slug}/jobs",
            json={
                "slug": slug,
                "title": slug.replace("-", " ").title(),
                "description": "Private inventory test job.",
                "location": "Singapore",
                "work_mode": "hybrid",
                "employment_type": "full_time",
            },
            headers={"Idempotency-Key": f"inventory-job-{organization_slug}-{slug}-0001"},
        )
        assert response.status_code == 201, response.text
        return response.json()

    owned = await create_organization("inventory-owned")
    admin_org = await create_organization("inventory-admin-org")
    public_unverified = await create_organization("inventory-public-unverified")
    jobs = {
        "draft": await create_job("inventory-owned", "draft-role"),
        "published": await create_job("inventory-owned", "published-role"),
        "closed": await create_job("inventory-owned", "closed-role"),
        "admin": await create_job("inventory-admin-org", "admin-role"),
        "public": await create_job("inventory-public-unverified", "public-role"),
    }

    async with app.state.session_factory() as session:
        owned_row = await session.get(Organization, owned["id"])
        admin_org_row = await session.get(Organization, admin_org["id"])
        public_row = await session.get(Organization, public_unverified["id"])
        assert owned_row is not None
        assert admin_org_row is not None
        assert public_row is not None
        public_row.visibility = "public"
        public_row.verification_status = "verified"
        now = datetime.now(UTC)
        for offset, row in enumerate((owned_row, admin_org_row, public_row)):
            row.updated_at = now - timedelta(minutes=offset)

        session.add_all(
            [
                OrganizationMembership(
                    organization_id=owned["id"],
                    member_owner_id=owner,
                    member_profile_handle=None,
                    role="admin",
                    status="active",
                    invited_by_owner_id=owner,
                ),
                OrganizationMembership(
                    organization_id=owned["id"],
                    member_owner_id="inventory-admin",
                    member_profile_handle="inventory-admin-profile",
                    role="admin",
                    status="active",
                    invited_by_owner_id=owner,
                ),
                OrganizationMembership(
                    organization_id=owned["id"],
                    member_owner_id="inventory-member",
                    member_profile_handle="inventory-member-profile",
                    role="member",
                    status="active",
                    invited_by_owner_id=owner,
                ),
                OrganizationMembership(
                    organization_id=owned["id"],
                    member_owner_id="inventory-invited-admin",
                    member_profile_handle="inventory-invited-profile",
                    role="admin",
                    status="invited",
                    invited_by_owner_id=owner,
                ),
                OrganizationMembership(
                    organization_id=owned["id"],
                    member_owner_id="inventory-removed-admin",
                    member_profile_handle="inventory-removed-profile",
                    role="admin",
                    status="removed",
                    invited_by_owner_id=owner,
                ),
            ]
        )
        status_by_key = {
            "draft": "draft",
            "published": "published",
            "closed": "closed",
            "admin": "draft",
            "public": "published",
        }
        for offset, (key, status) in enumerate(status_by_key.items()):
            job = await session.get(Job, jobs[key]["id"])
            assert job is not None
            job.status = status
            job.updated_at = now - timedelta(minutes=offset)
        await session.commit()

    async with app.state.session_factory() as session:
        before_changes = len((await session.scalars(select(ChangeEvent))).all())
        before_idempotency = len((await session.scalars(select(IdempotencyRecord))).all())

    as_principal(app, human(owner))
    organizations = await client.get("/v1/employer/organizations", params={"limit": 100})
    assert organizations.status_code == 200, organizations.text
    organization_items = organizations.json()["organizations"]
    assert {item["id"] for item in organization_items} == {
        owned["id"],
        admin_org["id"],
        public_unverified["id"],
    }
    organization_keys = {
        "id",
        "slug",
        "name",
        "management_role",
        "visibility",
        "recruiting_verification_active",
        "recruiting_verification_purpose",
        "recruiting_verification_expires_at",
        "updated_at",
    }
    assert all(set(item) == organization_keys for item in organization_items)
    assert {item["management_role"] for item in organization_items} == {"owner"}
    public_summary = next(
        item for item in organization_items if item["id"] == public_unverified["id"]
    )
    assert public_summary["visibility"] == "public"
    assert public_summary["recruiting_verification_active"] is False
    assert public_summary["recruiting_verification_purpose"] is None
    assert public_summary["recruiting_verification_expires_at"] is None

    jobs_response = await client.get("/v1/employer/jobs", params={"limit": 100})
    assert jobs_response.status_code == 200, jobs_response.text
    job_items = jobs_response.json()["jobs"]
    assert {item["id"] for item in job_items} == {item["id"] for item in jobs.values()}
    job_keys = {
        "id",
        "organization_id",
        "organization_slug",
        "organization_name",
        "management_role",
        "slug",
        "title",
        "status",
        "location",
        "work_mode",
        "employment_type",
        "updated_at",
    }
    assert all(set(item) == job_keys for item in job_items)
    assert {item["status"] for item in job_items} == {"draft", "published", "closed"}
    assert {item["management_role"] for item in job_items} == {"owner"}
    for forbidden in (
        "owner_id",
        "member_owner_id",
        "member_profile_handle",
        "description",
        "website_url",
        "version",
        "etag",
        "evidence",
        "digest",
        "application",
        "snapshot",
        "grant",
        "mandate",
        "credential",
    ):
        assert forbidden not in organizations.text
        assert forbidden not in jobs_response.text

    public_organizations = await client.get("/v1/organizations")
    assert public_organizations.status_code == 200
    assert all(
        item["slug"] != public_unverified["slug"]
        for item in public_organizations.json()["organizations"]
    )
    public_jobs = await client.get("/v1/jobs")
    assert public_jobs.status_code == 200
    assert all(item["slug"] != jobs["public"]["slug"] for item in public_jobs.json()["jobs"])

    as_principal(app, human("inventory-admin"))
    admin_organizations = await client.get("/v1/employer/organizations")
    assert admin_organizations.status_code == 200
    assert [item["id"] for item in admin_organizations.json()["organizations"]] == [owned["id"]]
    assert admin_organizations.json()["organizations"][0]["management_role"] == "admin"
    admin_jobs = await client.get("/v1/employer/jobs")
    assert admin_jobs.status_code == 200
    assert {item["id"] for item in admin_jobs.json()["jobs"]} == {
        jobs["draft"]["id"],
        jobs["published"]["id"],
        jobs["closed"]["id"],
    }
    assert {item["management_role"] for item in admin_jobs.json()["jobs"]} == {"admin"}

    for subject in (
        "inventory-member",
        "inventory-invited-admin",
        "inventory-removed-admin",
        "inventory-foreign",
    ):
        as_principal(app, human(subject))
        denied_organizations = await client.get("/v1/employer/organizations")
        denied_jobs = await client.get("/v1/employer/jobs")
        assert denied_organizations.status_code == 200
        assert denied_jobs.status_code == 200
        assert denied_organizations.json() == {"organizations": [], "next_cursor": None}
        assert denied_jobs.json() == {"jobs": [], "next_cursor": None}

    denied_principals = [
        Principal(
            subject=owner,
            method="agent_api_key",
            scopes=frozenset({"organizations:read", "jobs:read"}),
        ),
        Principal(
            subject=owner,
            method="agent_grant",
            scopes=frozenset({"inventory:read"}),
            grant_mode="direct",
            resource_type="owner",
        ),
        Principal(
            subject=owner,
            method="agent_grant",
            scopes=frozenset({"jobs:read"}),
            grant_mode="direct",
            resource_type="organization",
            resource_id=owned["id"],
        ),
        Principal(
            subject=owner,
            method="agent_grant",
            scopes=frozenset({"jobs:read"}),
            grant_mode="proposal_only",
            resource_type="organization",
            resource_id=owned["id"],
        ),
        Principal(
            subject=owner,
            method="agent_grant",
            scopes=frozenset({"contacts:write"}),
            grant_mode="direct",
            mandate_id="inventory-mandate",
            resource_type="owner",
        ),
    ]
    for principal in denied_principals:
        as_principal(app, principal)
        for path in ("/v1/employer/organizations", "/v1/employer/jobs"):
            denied = await client.get(path, params={"cursor": "not-a-cursor"})
            assert denied.status_code == 403
            assert "organizations" not in denied.json()
            assert "jobs" not in denied.json()

    async with app.state.session_factory() as session:
        after_changes = len((await session.scalars(select(ChangeEvent))).all())
        after_idempotency = len((await session.scalars(select(IdempotencyRecord))).all())
    assert after_changes == before_changes
    assert after_idempotency == before_idempotency


async def test_employer_inventory_cursors_are_signed_bound_and_deterministic(api_client) -> None:
    app, client = api_client
    owner = "inventory-page-owner"
    as_principal(app, human(owner))
    organizations: list[dict] = []
    for index in range(3):
        slug = f"inventory-page-{index}"
        created = await client.post(
            "/v1/organizations",
            json={"slug": slug, "name": f"Inventory Page {index}"},
            headers={"Idempotency-Key": f"inventory-page-org-{index}-0001"},
        )
        assert created.status_code == 201, created.text
        organizations.append(created.json())
    jobs: list[dict] = []
    for index in range(3):
        created = await client.post(
            f"/v1/organizations/{organizations[0]['slug']}/jobs",
            json={
                "slug": f"inventory-page-job-{index}",
                "title": f"Inventory Page Job {index}",
                "description": "Cursor test job.",
            },
            headers={"Idempotency-Key": f"inventory-page-job-{index}-0001"},
        )
        assert created.status_code == 201, created.text
        jobs.append(created.json())

    async with app.state.session_factory() as session:
        now = datetime.now(UTC)
        for index, organization_data in enumerate(organizations):
            organization = await session.get(Organization, organization_data["id"])
            assert organization is not None
            organization.updated_at = now - timedelta(minutes=index)
        for index, job_data in enumerate(jobs):
            job = await session.get(Job, job_data["id"])
            assert job is not None
            job.updated_at = now - timedelta(minutes=index)
        await session.commit()

    first_organizations = await client.get("/v1/employer/organizations", params={"limit": 1})
    assert first_organizations.status_code == 200
    organization_cursor = first_organizations.json()["next_cursor"]
    assert organization_cursor
    second_organizations = await client.get(
        "/v1/employer/organizations",
        params={"limit": 1, "cursor": organization_cursor},
    )
    assert second_organizations.status_code == 200
    assert second_organizations.json()["organizations"]
    assert (
        second_organizations.json()["organizations"][0]["id"]
        != (first_organizations.json()["organizations"][0]["id"])
    )
    seen_organizations: list[str] = []
    cursor = None
    for _ in range(4):
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = await client.get("/v1/employer/organizations", params=params)
        assert page.status_code == 200
        seen_organizations.extend(item["id"] for item in page.json()["organizations"])
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break
    assert seen_organizations == [item["id"] for item in organizations]

    first_jobs = await client.get("/v1/employer/jobs", params={"limit": 1})
    assert first_jobs.status_code == 200
    job_cursor = first_jobs.json()["next_cursor"]
    assert job_cursor
    seen_jobs: list[str] = []
    cursor = None
    for _ in range(4):
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = await client.get("/v1/employer/jobs", params=params)
        assert page.status_code == 200
        seen_jobs.extend(item["id"] for item in page.json()["jobs"])
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break
    assert seen_jobs == [item["id"] for item in jobs]

    cross_endpoint = await client.get("/v1/employer/jobs", params={"cursor": organization_cursor})
    assert cross_endpoint.status_code == 400
    reverse_cross_endpoint = await client.get(
        "/v1/employer/organizations", params={"cursor": job_cursor}
    )
    assert reverse_cross_endpoint.status_code == 400
    tampered = organization_cursor[:-1] + ("A" if organization_cursor[-1] != "A" else "B")
    assert (
        await client.get("/v1/employer/organizations", params={"cursor": tampered})
    ).status_code == 400
    assert (
        await client.get("/v1/employer/organizations", params={"cursor": "malformed"})
    ).status_code == 400
    as_principal(app, human("inventory-page-foreign"))
    assert (
        await client.get("/v1/employer/organizations", params={"cursor": organization_cursor})
    ).status_code == 400

    as_principal(app, human(owner))
    async with app.state.session_factory() as session:
        boundary = await session.get(
            Organization, first_organizations.json()["organizations"][0]["id"]
        )
        assert boundary is not None
        boundary.updated_at = datetime.now(UTC) + timedelta(days=1)
        await session.commit()
    stale = await client.get("/v1/employer/organizations", params={"cursor": organization_cursor})
    assert stale.status_code == 409
    assert (await client.get("/v1/employer/organizations", params={"limit": 0})).status_code == 422
    assert (
        await client.get("/v1/employer/organizations", params={"cursor": "x" * 501})
    ).status_code == 422


async def test_employer_inventory_discovery_is_human_only_and_not_agent_surface(api_client) -> None:
    app, client = api_client
    schema = app.openapi()
    expected = {
        "/v1/employer/organizations": "EmployerOrganizationInventoryResponse",
        "/v1/employer/jobs": "EmployerJobInventoryResponse",
    }
    for path, response_name in expected.items():
        operation = schema["paths"][path]["get"]
        assert operation["security"] == [{"ClerkBearerAuth": []}]
        assert operation["x-connectmd-human-only"] is True
        assert response_name in str(operation["responses"]["200"])
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert parameters["limit"]["schema"]["type"] == "integer"
        assert parameters["limit"]["schema"]["minimum"] == 1
        assert parameters["limit"]["schema"]["maximum"] == 100
        assert parameters["limit"]["schema"]["default"] == 25
        assert parameters["cursor"]["schema"]["anyOf"][0]["maxLength"] == 500

    capabilities = await client.get("/v1/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["employer_inventory"] == {
        "human_only": True,
        "authentication": "clerk_jwt",
        "organizations_endpoint": "/v1/employer/organizations",
        "jobs_endpoint": "/v1/employer/jobs",
        "includes": ["manageable_organizations", "draft_published_closed_jobs"],
        "cursor": {"signed": True, "subject_bound": True, "endpoint_bound": True},
        "agent_access": False,
        "mcp": False,
        "a2a": False,
        "public_search": False,
        "sitemap": False,
    }
    full = await client.get("/llms-full.txt")
    concise = await client.get("/llms.txt")
    assert full.status_code == 200
    assert concise.status_code == 200
    for endpoint in expected:
        assert endpoint in full.text
        assert endpoint not in concise.text
    assert "Clerk-human-only HTTP" in full.text
    assert "not available to API keys, Agent Grants, MCP, A2A" in full.text

    tools = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert tools.status_code == 200
    assert all(endpoint not in tools.text for endpoint in expected)
    card = await client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert all(endpoint not in card.text for endpoint in expected)

    as_principal(app, human("inventory-discovery-owner"))
    assert (
        await client.get("/v1/employer/organizations", params={"limit": 101})
    ).status_code == 422
    assert (await client.get("/v1/employer/jobs", params={"cursor": "x" * 501})).status_code == 422


async def test_public_inventory_uses_set_based_verification_and_limit_plus_one(api_client) -> None:
    app, client = api_client
    now = datetime.now(UTC)

    def organization(organization_id: str, slug: str, updated_at: datetime) -> Organization:
        return Organization(
            id=organization_id,
            owner_id=f"owner-{slug}",
            slug=slug,
            name=slug.replace("-", " ").title(),
            description=None,
            website_url=None,
            visibility="public",
            verification_status="verified",
            verification_material_version=1,
            version=1,
            created_at=updated_at,
            updated_at=updated_at,
        )

    def verified_records(
        row: Organization, suffix: str, occurred_at: datetime
    ) -> tuple[
        OrganizationVerification, OrganizationVerificationEvidence, OrganizationVerificationEvent
    ]:
        evidence_bytes = b"c"
        evidence_sha256 = sha256(evidence_bytes).hexdigest()
        digest = material_claim_digest(
            organization_id=row.id,
            organization_name=row.name,
            organization_website_url=row.website_url,
            organization_material_version=row.verification_material_version,
            evidence_kind="other",
            metadata={},
            artifact_content_type="text/plain",
            artifact_sha256=evidence_sha256,
            artifact_size_bytes=len(evidence_bytes),
        )
        record_number = int(suffix) * 1_000
        verification_id = f"30000000-0000-4000-8000-{record_number + 1:012d}"
        verification = OrganizationVerification(
            id=verification_id,
            organization_id=row.id,
            purpose="recruiting_control",
            submitted_by_owner_id=row.owner_id,
            material_claim_digest=digest,
            created_at=occurred_at,
        )
        evidence_path = canonical_evidence_path(row.id, verification_id, evidence_sha256)
        app.state.store.write_immutable_bytes(evidence_path, evidence_bytes)
        evidence = OrganizationVerificationEvidence(
            id=f"30000000-0000-4000-8000-{record_number + 2:012d}",
            verification_id=verification_id,
            evidence_kind="other",
            metadata_json="{}",
            artifact_content_type="text/plain",
            artifact_sha256=evidence_sha256,
            artifact_size_bytes=len(evidence_bytes),
            storage_path=evidence_path,
            created_at=occurred_at,
            retention_expires_at=now + timedelta(days=30),
        )
        event = OrganizationVerificationEvent(
            id=f"30000000-0000-4000-8000-{record_number + 3:012d}",
            verification_id=verification_id,
            organization_id=row.id,
            purpose="recruiting_control",
            to_state="active",
            actor_id="reviewer:preprovisioned",
            actor_role="recruiting_verifier",
            policy_version="recruiting-control-v1",
            material_claim_digest=digest,
            expires_at=now + timedelta(days=30),
            occurred_at=occurred_at,
        )
        return verification, evidence, event

    ineligible = organization("30000000-0000-4000-8000-000000000001", "public-ineligible", now)
    stale_one = organization(
        "30000000-0000-4000-8000-000000000004",
        "public-stale-one",
        now,
    )
    stale_two = organization(
        "30000000-0000-4000-8000-000000000005",
        "public-stale-two",
        now - timedelta(minutes=2),
    )
    eligible_one = organization(
        "30000000-0000-4000-8000-000000000002",
        "public-eligible-one",
        now - timedelta(minutes=4),
    )
    eligible_two = organization(
        "30000000-0000-4000-8000-000000000003",
        "public-eligible-two",
        now - timedelta(minutes=6),
    )
    verification_rows = [
        *verified_records(stale_one, "003", now),
        *verified_records(stale_two, "004", now - timedelta(minutes=2)),
        *verified_records(eligible_one, "001", now - timedelta(minutes=1)),
        *verified_records(eligible_two, "002", now - timedelta(minutes=2)),
    ]
    stale_one.name = "Changed Stale One"
    stale_two.name = "Changed Stale Two"
    jobs = [
        Job(
            id="30000000-0000-4000-8000-000000000011",
            organization_id=ineligible.id,
            slug="ineligible-role",
            title="Ineligible Role",
            description="Should not occupy the public page.",
            location="Singapore",
            work_mode="hybrid",
            employment_type="full_time",
            status="published",
            version=1,
            published_at=now,
            created_at=now,
            updated_at=now,
        ),
        Job(
            id="30000000-0000-4000-8000-000000000014",
            organization_id=stale_one.id,
            slug="stale-role-one",
            title="Stale Role One",
            description="Stale material must not occupy the page.",
            location="Singapore",
            work_mode="hybrid",
            employment_type="full_time",
            status="published",
            version=1,
            published_at=stale_one.updated_at,
            created_at=stale_one.updated_at,
            updated_at=stale_one.updated_at,
        ),
        Job(
            id="30000000-0000-4000-8000-000000000015",
            organization_id=stale_two.id,
            slug="stale-role-two",
            title="Stale Role Two",
            description="Stale material must not occupy the page.",
            location="Singapore",
            work_mode="hybrid",
            employment_type="full_time",
            status="published",
            version=1,
            published_at=stale_two.updated_at,
            created_at=stale_two.updated_at,
            updated_at=stale_two.updated_at,
        ),
        Job(
            id="30000000-0000-4000-8000-000000000012",
            organization_id=eligible_one.id,
            slug="eligible-role-one",
            title="Eligible Role One",
            description="First eligible public role.",
            location="Singapore",
            work_mode="hybrid",
            employment_type="full_time",
            status="published",
            version=1,
            published_at=eligible_one.updated_at,
            created_at=eligible_one.updated_at,
            updated_at=eligible_one.updated_at,
        ),
        Job(
            id="30000000-0000-4000-8000-000000000013",
            organization_id=eligible_two.id,
            slug="eligible-role-two",
            title="Eligible Role Two",
            description="Second eligible public role.",
            location="Singapore",
            work_mode="remote",
            employment_type="full_time",
            status="published",
            version=1,
            published_at=eligible_two.updated_at,
            created_at=eligible_two.updated_at,
            updated_at=eligible_two.updated_at,
        ),
    ]
    async with app.state.session_factory() as session:
        session.add_all(
            [
                ineligible,
                stale_one,
                stale_two,
                eligible_one,
                eligible_two,
                *verification_rows,
                *jobs,
            ]
        )
        await session.commit()

    organization_query_count = 0

    def count_organization_queries(*_args: object, **_kwargs: object) -> None:
        nonlocal organization_query_count
        organization_query_count += 1

    sqlalchemy_event.listen(
        app.state.engine.sync_engine,
        "before_cursor_execute",
        count_organization_queries,
    )
    try:
        organizations = await client.get("/v1/organizations", params={"limit": 1})
    finally:
        sqlalchemy_event.remove(
            app.state.engine.sync_engine,
            "before_cursor_execute",
            count_organization_queries,
        )
    assert organizations.status_code == 200, organizations.text
    assert organization_query_count == 1
    assert organizations.json()["organizations"] == []
    assert organizations.json()["next_cursor"]
    assert main_module._cursor_decode(organizations.json()["next_cursor"])["mode"] == "raw"
    organization_page_two = await client.get(
        "/v1/organizations",
        params={"limit": 1, "cursor": organizations.json()["next_cursor"]},
    )
    assert [item["slug"] for item in organization_page_two.json()["organizations"]] == [
        "public-eligible-one"
    ]
    assert organization_page_two.json()["next_cursor"]
    organization_page_three = await client.get(
        "/v1/organizations",
        params={"limit": 1, "cursor": organization_page_two.json()["next_cursor"]},
    )
    assert [item["slug"] for item in organization_page_three.json()["organizations"]] == [
        "public-eligible-two"
    ]

    job_query_count = 0

    def count_job_queries(*_args: object, **_kwargs: object) -> None:
        nonlocal job_query_count
        job_query_count += 1

    sqlalchemy_event.listen(
        app.state.engine.sync_engine, "before_cursor_execute", count_job_queries
    )
    try:
        jobs_response = await client.get("/v1/jobs", params={"limit": 1})
    finally:
        sqlalchemy_event.remove(
            app.state.engine.sync_engine, "before_cursor_execute", count_job_queries
        )
    assert jobs_response.status_code == 200, jobs_response.text
    assert job_query_count == 1
    assert jobs_response.json()["jobs"] == []
    assert jobs_response.json()["next_cursor"]
    assert main_module._cursor_decode(jobs_response.json()["next_cursor"])["mode"] == "raw"
    jobs_page_two = await client.get(
        "/v1/jobs", params={"limit": 1, "cursor": jobs_response.json()["next_cursor"]}
    )
    assert [item["slug"] for item in jobs_page_two.json()["jobs"]] == ["eligible-role-one"]
    assert jobs_page_two.json()["next_cursor"]
    jobs_page_three = await client.get(
        "/v1/jobs", params={"limit": 1, "cursor": jobs_page_two.json()["next_cursor"]}
    )
    assert [item["slug"] for item in jobs_page_three.json()["jobs"]] == ["eligible-role-two"]


async def test_public_inventory_deduplicates_duplicate_evidence_join_rows(api_client) -> None:
    app, client = api_client
    now = datetime.now(UTC)
    organization = Organization(
        id="31000000-0000-4000-8000-000000000001",
        owner_id="duplicate-evidence-owner",
        slug="duplicate-evidence-org",
        name="Duplicate Evidence Org",
        description=None,
        website_url=None,
        visibility="public",
        verification_status="verified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    evidence_bytes = b"d"
    evidence_sha256 = sha256(evidence_bytes).hexdigest()
    digest = material_claim_digest(
        organization_id=organization.id,
        organization_name=organization.name,
        organization_website_url=organization.website_url,
        organization_material_version=organization.verification_material_version,
        evidence_kind="other",
        metadata={},
        artifact_content_type="text/plain",
        artifact_sha256=evidence_sha256,
        artifact_size_bytes=len(evidence_bytes),
    )
    verification = OrganizationVerification(
        id="31000000-0000-4000-8000-000000000002",
        organization_id=organization.id,
        purpose="recruiting_control",
        submitted_by_owner_id=organization.owner_id,
        material_claim_digest=digest,
        created_at=now,
    )
    evidence_path = canonical_evidence_path(organization.id, verification.id, evidence_sha256)
    app.state.store.write_immutable_bytes(evidence_path, evidence_bytes)
    evidence = OrganizationVerificationEvidence(
        id="31000000-0000-4000-8000-000000000003",
        verification_id=verification.id,
        evidence_kind="other",
        metadata_json="{}",
        artifact_content_type="text/plain",
        artifact_sha256=evidence_sha256,
        artifact_size_bytes=len(evidence_bytes),
        storage_path=evidence_path,
        created_at=now,
        retention_expires_at=now + timedelta(days=30),
    )
    duplicate_evidence = OrganizationVerificationEvidence(
        id="31000000-0000-4000-8000-000000000004",
        verification_id=verification.id,
        evidence_kind=evidence.evidence_kind,
        metadata_json=evidence.metadata_json,
        artifact_content_type=evidence.artifact_content_type,
        artifact_sha256=evidence.artifact_sha256,
        artifact_size_bytes=evidence.artifact_size_bytes,
        storage_path=evidence_path,
        created_at=evidence.created_at,
        retention_expires_at=evidence.retention_expires_at,
    )
    event = OrganizationVerificationEvent(
        id="31000000-0000-4000-8000-000000000005",
        verification_id=verification.id,
        organization_id=organization.id,
        purpose="recruiting_control",
        to_state="active",
        actor_id="reviewer:preprovisioned",
        actor_role="recruiting_verifier",
        policy_version="recruiting-control-v1",
        material_claim_digest=digest,
        expires_at=now + timedelta(days=30),
        occurred_at=now,
    )
    async with app.state.session_factory() as session:
        session.add_all([organization, verification, evidence, event])
        await session.commit()

    class DuplicateResult:
        def all(self):
            return [
                (organization, event, verification, evidence),
                (organization, event, verification, duplicate_evidence),
            ]

    class DuplicateSession:
        async def execute(self, _statement):
            return DuplicateResult()

    duplicate_session = DuplicateSession()
    app.dependency_overrides[get_session] = lambda: duplicate_session
    try:
        response = await client.get("/v1/organizations", params={"limit": 1})
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 200, response.text
    assert [item["slug"] for item in response.json()["organizations"]] == ["duplicate-evidence-org"]
    assert response.json()["next_cursor"]
    assert main_module._cursor_decode(response.json()["next_cursor"])["mode"] == "raw"
