from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from app.models import (
    Job,
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import canonical_evidence_path


async def _seed_searchable_jobs(app) -> None:
    now = datetime.now(UTC)
    organization = Organization(
        id="30000000-0000-4000-8000-000000000001",
        owner_id="job-search-owner",
        slug="job-search-labs",
        name="Bounded Labs",
        description=None,
        website_url=None,
        visibility="public",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    evidence_bytes = b"job-search-evidence"
    evidence_sha256 = sha256(evidence_bytes).hexdigest()
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
        id="30000000-0000-4000-8000-000000000002",
        organization_id=organization.id,
        purpose="recruiting_control",
        submitted_by_owner_id=organization.owner_id,
        material_claim_digest=claim_digest,
        created_at=now,
    )
    evidence_path = canonical_evidence_path(organization.id, verification.id, evidence_sha256)
    app.state.store.write_immutable_bytes(evidence_path, evidence_bytes)
    jobs = (
        Job(
            id="30000000-0000-4000-8000-000000000011",
            organization_id=organization.id,
            slug="percent-role",
            title="100% onsite",
            description="Build services.",
            location="100% remote",
            work_mode="onsite",
            employment_type="full_time",
            status="published",
            version=1,
            published_at=now,
            created_at=now,
            updated_at=now,
        ),
        Job(
            id="30000000-0000-4000-8000-000000000012",
            organization_id=organization.id,
            slug="underscore-role",
            title="role_alpha",
            description="Build services.",
            location="Singapore",
            work_mode="hybrid",
            employment_type="full_time",
            status="published",
            version=1,
            published_at=now,
            created_at=now,
            updated_at=now - timedelta(seconds=1),
        ),
        Job(
            id="30000000-0000-4000-8000-000000000013",
            organization_id=organization.id,
            slug="plain-role",
            title="plain role",
            description="Build services.",
            location="London",
            work_mode="remote",
            employment_type="full_time",
            status="published",
            version=1,
            published_at=now,
            created_at=now,
            updated_at=now - timedelta(seconds=2),
        ),
    )
    async with app.state.session_factory() as session:
        session.add_all(
            (
                organization,
                verification,
                *jobs,
                OrganizationVerificationEvidence(
                    id="30000000-0000-4000-8000-000000000003",
                    verification_id=verification.id,
                    evidence_kind="other",
                    metadata_json="{}",
                    artifact_content_type="text/plain",
                    artifact_sha256=evidence_sha256,
                    artifact_size_bytes=len(evidence_bytes),
                    storage_path=evidence_path,
                    created_at=now,
                    retention_expires_at=now + timedelta(days=30),
                ),
                OrganizationVerificationEvent(
                    id="30000000-0000-4000-8000-000000000004",
                    verification_id=verification.id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    to_state="active",
                    actor_id="reviewer:preprovisioned",
                    actor_role="recruiting_verifier",
                    policy_version="recruiting-control-v1",
                    material_claim_digest=claim_digest,
                    expires_at=now + timedelta(days=30),
                    occurred_at=now,
                ),
            )
        )
        await session.commit()


def _titles(response) -> set[str]:
    return {job["title"] for job in response.json()["jobs"]}


async def test_job_search_treats_like_metacharacters_and_blank_location_literally(
    api_client,
) -> None:
    app, client = api_client
    await _seed_searchable_jobs(app)

    percent = await client.get("/v1/jobs", params={"q": "%"})
    assert percent.status_code == 200, percent.text
    assert _titles(percent) == {"100% onsite"}

    underscore = await client.get("/v1/jobs", params={"q": "_"})
    assert underscore.status_code == 200, underscore.text
    assert _titles(underscore) == {"role_alpha"}

    literal = await client.get("/v1/jobs", params={"q": "PLAIN"})
    assert literal.status_code == 200, literal.text
    assert _titles(literal) == {"plain role"}

    percent_location = await client.get("/v1/jobs", params={"location": "%"})
    assert percent_location.status_code == 200, percent_location.text
    assert _titles(percent_location) == {"100% onsite"}

    city = await client.get("/v1/jobs", params={"location": "singapore"})
    assert city.status_code == 200, city.text
    assert _titles(city) == {"role_alpha"}

    blank = await client.get("/v1/jobs", params={"location": " "})
    assert blank.status_code == 400, blank.text
    assert blank.json()["detail"] == "job location must not be blank"
