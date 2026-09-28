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
        id="33000000-0000-4000-8000-000000000001",
        owner_id="decide-owner",
        slug="decide-org",
        name="Decide Org",
        description=None,
        website_url=None,
        visibility="public",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    evidence_bytes = b"decide-evidence"
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
        id="33000000-0000-4000-8000-000000000002",
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
                    id="33000000-0000-4000-8000-000000000003",
                    organization_id=organization.id,
                    slug="decide-role",
                    title="Decide Role",
                    description="A published role used to test self-decision.",
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
                    id="33000000-0000-4000-8000-000000000004",
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
                    id="33000000-0000-4000-8000-000000000005",
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


async def _apply(app, client, subject: str, handle: str) -> str:
    as_principal(app, human(subject))
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", handle)},
        headers={"Idempotency-Key": f"decide-profile-{handle}"},
    )
    assert profile.status_code == 201, profile.text
    application = await client.post(
        "/v1/organizations/decide-org/jobs/decide-role/applications",
        json={
            "message": "A private application that the applicant must not decide.",
            "snapshot_kind": "profile",
            "snapshot_identifier": handle,
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": f"decide-submit-{handle}"},
    )
    assert application.status_code == 201, application.text
    return application.json()["id"]


async def test_employer_cannot_decide_their_own_application(api_client) -> None:
    app, client = api_client
    await _seed_published_job(app)
    insider_id = await _apply(app, client, "decide-insider", "decide-insider")
    outsider_id = await _apply(app, client, "decide-outsider", "decide-outsider")

    as_principal(app, human("decide-owner"))
    invite = await client.post(
        "/v1/organizations/decide-org/admins",
        json={"member_profile_handle": "decide-insider", "role": "admin"},
        headers={"Idempotency-Key": "decide-invite-0001"},
    )
    assert invite.status_code == 201, invite.text
    as_principal(app, human("decide-insider"))
    accepted = await client.post(
        f"/v1/organizations/decide-org/memberships/{invite.json()['id']}/accept",
        headers={"Idempotency-Key": "decide-accept-membership-0001"},
    )
    assert accepted.status_code == 200, accepted.text

    self_decision = await client.post(
        f"/v1/organizations/decide-org/jobs/decide-role/applications/{insider_id}/accept",
        headers={"Idempotency-Key": "decide-self-accept-0001"},
    )
    assert self_decision.status_code == 409, self_decision.text
    assert self_decision.json()["detail"] == "you cannot decide your own application"

    as_principal(app, human("decide-owner"))
    employer_decision = await client.post(
        f"/v1/organizations/decide-org/jobs/decide-role/applications/{outsider_id}/accept",
        headers={"Idempotency-Key": "decide-employer-accept-0001"},
    )
    assert employer_decision.status_code == 200, employer_decision.text
    assert employer_decision.json()["status"] == "accepted"

    async with app.state.session_factory() as session:
        insider = await session.get(Application, insider_id)
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "decide-self-accept-0001"
                )
            )
        ).all()
    assert insider is not None
    assert insider.status == "submitted"
    assert insider.decided_at is None
    assert receipts == []
