from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from app.models import (
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import canonical_evidence_path


def _seed_organization(
    *,
    organization_id: str,
    verification_id: str,
    evidence_id: str,
    event_id: str,
    slug: str,
    name: str,
    owner_id: str,
    now: datetime,
) -> tuple[Organization, OrganizationVerification, OrganizationVerificationEvidence, OrganizationVerificationEvent, bytes, str]:
    organization = Organization(
        id=organization_id,
        owner_id=owner_id,
        slug=slug,
        name=name,
        description=None,
        website_url=None,
        visibility="public",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    evidence_bytes = f"org-search-{slug}".encode()
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
        id=verification_id,
        organization_id=organization.id,
        purpose="recruiting_control",
        submitted_by_owner_id=owner_id,
        material_claim_digest=claim_digest,
        created_at=now,
    )
    evidence = OrganizationVerificationEvidence(
        id=evidence_id,
        verification_id=verification.id,
        evidence_kind="other",
        metadata_json="{}",
        artifact_content_type="text/plain",
        artifact_sha256=evidence_sha256,
        artifact_size_bytes=len(evidence_bytes),
        storage_path="",
        created_at=now,
        retention_expires_at=now + timedelta(days=30),
    )
    event = OrganizationVerificationEvent(
        id=event_id,
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
    )
    return organization, verification, evidence, event, evidence_bytes, evidence_sha256


async def test_organization_search_treats_like_metacharacters_literally(api_client) -> None:
    app, client = api_client
    now = datetime.now(UTC)
    percent = _seed_organization(
        organization_id="31000000-0000-4000-8000-000000000001",
        verification_id="31000000-0000-4000-8000-000000000002",
        evidence_id="31000000-0000-4000-8000-000000000003",
        event_id="31000000-0000-4000-8000-000000000004",
        slug="percent-labs",
        name="100% Labs",
        owner_id="org-search-percent-owner",
        now=now,
    )
    underscore = _seed_organization(
        organization_id="31000000-0000-4000-8000-000000000011",
        verification_id="31000000-0000-4000-8000-000000000012",
        evidence_id="31000000-0000-4000-8000-000000000013",
        event_id="31000000-0000-4000-8000-000000000014",
        slug="north_labs",
        name="North Labs",
        owner_id="org-search-underscore-owner",
        now=now - timedelta(seconds=1),
    )
    plain = _seed_organization(
        organization_id="31000000-0000-4000-8000-000000000021",
        verification_id="31000000-0000-4000-8000-000000000022",
        evidence_id="31000000-0000-4000-8000-000000000023",
        event_id="31000000-0000-4000-8000-000000000024",
        slug="plain-labs",
        name="Plain Labs",
        owner_id="org-search-plain-owner",
        now=now - timedelta(seconds=2),
    )
    async with app.state.session_factory() as session:
        for organization, verification, evidence, event, evidence_bytes, evidence_sha256 in (
            percent,
            underscore,
            plain,
        ):
            evidence.storage_path = canonical_evidence_path(
                organization.id, verification.id, evidence_sha256
            )
            app.state.store.write_immutable_bytes(evidence.storage_path, evidence_bytes)
            session.add_all((organization, verification, evidence, event))
        await session.commit()

    percent_query = await client.get("/v1/organizations", params={"q": "%"})
    assert percent_query.status_code == 200, percent_query.text
    assert {row["name"] for row in percent_query.json()["organizations"]} == {"100% Labs"}

    underscore_query = await client.get("/v1/organizations", params={"q": "_"})
    assert underscore_query.status_code == 200, underscore_query.text
    assert {row["slug"] for row in underscore_query.json()["organizations"]} == {"north_labs"}

    literal = await client.get("/v1/organizations", params={"q": "PLAIN"})
    assert literal.status_code == 200, literal.text
    assert {row["name"] for row in literal.json()["organizations"]} == {"Plain Labs"}
