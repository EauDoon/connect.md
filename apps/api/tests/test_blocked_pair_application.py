from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    Application,
    IdempotencyRecord,
    Job,
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import canonical_evidence_path

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def _seed_published_job(app) -> None:
    now = datetime.now(UTC)
    organization = Organization(
        id="34000000-0000-4000-8000-000000000001",
        owner_id="block-apply-owner",
        slug="block-apply-org",
        name="Block Apply Org",
        description=None,
        website_url=None,
        visibility="public",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    evidence_bytes = b"block-apply-evidence"
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
        id="34000000-0000-4000-8000-000000000002",
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
                Job(
                    id="34000000-0000-4000-8000-000000000003",
                    organization_id=organization.id,
                    slug="block-apply-role",
                    title="Block Apply Role",
                    description="A published role used to test blocked applicants.",
                    location="Singapore",
                    work_mode="hybrid",
                    employment_type="full_time",
                    status="published",
                    version=1,
                    published_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                OrganizationVerificationEvidence(
                    id="34000000-0000-4000-8000-000000000004",
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
                    id="34000000-0000-4000-8000-000000000005",
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


async def _profile(app, client, subject: str, handle: str) -> None:
    as_principal(app, human(subject))
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", handle)},
        headers={"Idempotency-Key": f"block-apply-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_connection_block_rejects_a_job_application(api_client) -> None:
    app, client = api_client
    await _seed_published_job(app)
    await _profile(app, client, "block-apply-owner", "block-apply-owner")
    await _profile(app, client, "block-apply-candidate", "block-apply-candidate")
    await _profile(app, client, "block-apply-outsider", "block-apply-outsider")

    as_principal(app, human("block-apply-candidate"))
    requested = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "block-apply-owner", "messaging_requested": False},
        headers={"Idempotency-Key": "block-apply-connection-0001"},
    )
    assert requested.status_code == 201, requested.text
    as_principal(app, human("block-apply-owner"))
    accepted = await client.post(
        f"/v1/connection-requests/{requested.json()['id']}/accept",
        json={"messaging_consent": False},
        headers={"Idempotency-Key": "block-apply-accept-0001"},
    )
    assert accepted.status_code == 200, accepted.text
    connection_id = (await client.get("/v1/connections")).json()["connections"][0]["id"]
    blocked = await client.post(
        f"/v1/connections/{connection_id}/block",
        headers={"Idempotency-Key": "block-apply-block-0001"},
    )
    assert blocked.status_code == 204, blocked.text

    as_principal(app, human("block-apply-candidate"))
    denied = await client.post(
        "/v1/organizations/block-apply-org/jobs/block-apply-role/applications",
        json={
            "message": "A blocked candidate must not apply.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "block-apply-candidate",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "block-apply-denied-0001"},
    )
    assert denied.status_code == 404, denied.text
    assert denied.json()["detail"] == "job was not found"

    as_principal(app, human("block-apply-outsider"))
    allowed = await client.post(
        "/v1/organizations/block-apply-org/jobs/block-apply-role/applications",
        json={
            "message": "An unblocked candidate may apply.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "block-apply-outsider",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "block-apply-allowed-0001"},
    )
    assert allowed.status_code == 201, allowed.text

    async with app.state.session_factory() as session:
        applications = (await session.scalars(select(Application))).all()
        denied_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "block-apply-denied-0001"
                )
            )
        ).all()
    assert [row.applicant_owner_id for row in applications] == ["block-apply-outsider"]
    assert denied_receipts == []
