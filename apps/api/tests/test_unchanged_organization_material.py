from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import canonical_evidence_path


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def _seed_verified_organization(app) -> None:
    now = datetime.now(UTC)
    organization = Organization(
        id="36000000-0000-4000-8000-000000000001",
        owner_id="material-owner",
        slug="material-labs",
        name="Bounded Labs",
        description="Original description",
        website_url="https://bounded.example/careers",
        visibility="public",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    evidence_bytes = b"material-evidence"
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
        id="36000000-0000-4000-8000-000000000002",
        organization_id=organization.id,
        purpose="recruiting_control",
        submitted_by_owner_id=organization.owner_id,
        material_claim_digest=claim_digest,
        created_at=now,
    )
    evidence_path = canonical_evidence_path(organization.id, verification.id, evidence_sha256)
    app.state.store.write_immutable_bytes(evidence_path, evidence_bytes)
    async with app.state.session_factory() as session:
        session.add_all(
            (
                organization,
                verification,
                OrganizationVerificationEvidence(
                    id="36000000-0000-4000-8000-000000000003",
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
                    id="36000000-0000-4000-8000-000000000004",
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


async def test_unchanged_organization_name_keeps_recruiting_verification(api_client) -> None:
    app, client = api_client
    await _seed_verified_organization(app)
    as_principal(app, human("material-owner"))
    current = await client.get("/v1/organizations/material-labs")
    assert current.status_code == 200, current.text
    assert current.json()["recruiting_verification_active"] is True

    same_name = await client.put(
        "/v1/organizations/material-labs",
        json={"name": "Bounded Labs", "description": "Updated description"},
        headers={
            "If-Match": current.headers["etag"],
            "Idempotency-Key": "material-same-name",
        },
    )
    assert same_name.status_code == 200, same_name.text
    assert same_name.json()["description"] == "Updated description"
    assert same_name.json()["name"] == "Bounded Labs"
    assert same_name.json()["recruiting_verification_active"] is True

    renamed = await client.put(
        "/v1/organizations/material-labs",
        json={"name": "Renamed Labs"},
        headers={
            "If-Match": same_name.headers["etag"],
            "Idempotency-Key": "material-renamed",
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed Labs"
    assert renamed.json()["recruiting_verification_active"] is False
