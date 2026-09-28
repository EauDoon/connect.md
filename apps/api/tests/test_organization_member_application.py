from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    Application,
    ApplicationRateBucket,
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


async def _seed_published_job(app, *, owner_id: str, slug: str, job_slug: str) -> None:
    now = datetime.now(UTC)
    organization = Organization(
        id="32000000-0000-4000-8000-000000000001",
        owner_id=owner_id,
        slug=slug,
        name="Member Apply Org",
        description=None,
        website_url=None,
        visibility="public",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    evidence_bytes = b"member-apply-evidence"
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
        id="32000000-0000-4000-8000-000000000002",
        organization_id=organization.id,
        purpose="recruiting_control",
        submitted_by_owner_id=owner_id,
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
                    id="32000000-0000-4000-8000-000000000003",
                    organization_id=organization.id,
                    slug=job_slug,
                    title="Member Apply Role",
                    description="A published role used to test insider applications.",
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
                    id="32000000-0000-4000-8000-000000000004",
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
                    id="32000000-0000-4000-8000-000000000005",
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


async def _public_profile(app, client, subject: str, handle: str) -> None:
    as_principal(app, human(subject))
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", handle)},
        headers={"Idempotency-Key": f"member-apply-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def test_active_organization_member_cannot_apply_to_its_job(api_client) -> None:
    app, client = api_client
    owner_id = "member-apply-owner"
    await _seed_published_job(
        app, owner_id=owner_id, slug="member-apply-org", job_slug="member-apply-role"
    )
    await _public_profile(app, client, "member-apply-admin", "member-apply-admin")
    await _public_profile(app, client, "member-apply-outsider", "member-apply-outsider")

    as_principal(app, human(owner_id))
    owner_attempt = await client.post(
        "/v1/organizations/member-apply-org/jobs/member-apply-role/applications",
        json={
            "message": "Owner application that must be refused.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "member-apply-admin",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "member-apply-owner-0001"},
    )
    assert owner_attempt.status_code == 409, owner_attempt.text
    assert owner_attempt.json()["detail"] == "organization owner cannot apply to its own job"

    invite = await client.post(
        "/v1/organizations/member-apply-org/admins",
        json={"member_profile_handle": "member-apply-admin", "role": "admin"},
        headers={"Idempotency-Key": "member-apply-invite-0001"},
    )
    assert invite.status_code == 201, invite.text
    as_principal(app, human("member-apply-admin"))
    accepted = await client.post(
        f"/v1/organizations/member-apply-org/memberships/{invite.json()['id']}/accept",
        headers={"Idempotency-Key": "member-apply-accept-0001"},
    )
    assert accepted.status_code == 200, accepted.text

    insider = await client.post(
        "/v1/organizations/member-apply-org/jobs/member-apply-role/applications",
        json={
            "message": "Insider application that must be refused.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "member-apply-admin",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "member-apply-insider-0001"},
    )
    assert insider.status_code == 409, insider.text
    assert insider.json()["detail"] == "organization members cannot apply to their own job"

    as_principal(app, human("member-apply-outsider"))
    outsider = await client.post(
        "/v1/organizations/member-apply-org/jobs/member-apply-role/applications",
        json={
            "message": "Outsider application that must be accepted.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "member-apply-outsider",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "member-apply-outsider-0001"},
    )
    assert outsider.status_code == 201, outsider.text

    async with app.state.session_factory() as session:
        applications = (await session.scalars(select(Application))).all()
        insider_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "member-apply-insider-0001"
                )
            )
        ).all()
        insider_quota = (
            await session.scalars(
                select(ApplicationRateBucket).where(
                    ApplicationRateBucket.applicant_owner_id == "member-apply-admin"
                )
            )
        ).all()
    assert [row.applicant_owner_id for row in applications] == ["member-apply-outsider"]
    assert insider_receipts == []
    assert insider_quota == []


async def test_invited_and_non_admin_members_cannot_apply(api_client) -> None:
    """Employer authority is owner/admin only, but every membership bars applying."""

    app, client = api_client
    owner_id = "member-role-owner"
    await _seed_published_job(
        app, owner_id=owner_id, slug="member-role-org", job_slug="member-role-job"
    )
    await _public_profile(app, client, "member-role-member", "member-role-member")
    await _public_profile(app, client, "member-role-invited", "member-role-invited")
    await _public_profile(app, client, "member-role-outsider", "member-role-outsider")

    as_principal(app, human(owner_id))
    member_invite = await client.post(
        "/v1/organizations/member-role-org/admins",
        json={"member_profile_handle": "member-role-member", "role": "member"},
        headers={"Idempotency-Key": "member-role-invite-member"},
    )
    assert member_invite.status_code == 201, member_invite.text
    invited = await client.post(
        "/v1/organizations/member-role-org/admins",
        json={"member_profile_handle": "member-role-invited", "role": "admin"},
        headers={"Idempotency-Key": "member-role-invite-pending"},
    )
    assert invited.status_code == 201, invited.text

    as_principal(app, human("member-role-member"))
    accepted = await client.post(
        f"/v1/organizations/member-role-org/memberships/{member_invite.json()['id']}/accept",
        headers={"Idempotency-Key": "member-role-accept"},
    )
    assert accepted.status_code == 200, accepted.text
    member_application = await client.post(
        "/v1/organizations/member-role-org/jobs/member-role-job/applications",
        json={
            "message": "An active non-admin member must not apply.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "member-role-member",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "member-role-apply-member"},
    )
    assert member_application.status_code == 409, member_application.text
    assert (
        member_application.json()["detail"]
        == "organization members cannot apply to their own job"
    )

    as_principal(app, human("member-role-invited"))
    invited_application = await client.post(
        "/v1/organizations/member-role-org/jobs/member-role-job/applications",
        json={
            "message": "An invited member must not apply.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "member-role-invited",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "member-role-apply-invited"},
    )
    assert invited_application.status_code == 409, invited_application.text
    assert (
        invited_application.json()["detail"]
        == "organization members cannot apply to their own job"
    )

    as_principal(app, human("member-role-outsider"))
    outsider = await client.post(
        "/v1/organizations/member-role-org/jobs/member-role-job/applications",
        json={
            "message": "An outsider may apply.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "member-role-outsider",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "member-role-apply-outsider"},
    )
    assert outsider.status_code == 201, outsider.text

    async with app.state.session_factory() as session:
        applications = (await session.scalars(select(Application))).all()
        refused_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key.in_(
                        ("member-role-apply-member", "member-role-apply-invited")
                    )
                )
            )
        ).all()
        refused_quota = (
            await session.scalars(
                select(ApplicationRateBucket).where(
                    ApplicationRateBucket.applicant_owner_id.in_(
                        ("member-role-member", "member-role-invited")
                    )
                )
            )
        ).all()
    assert [row.applicant_owner_id for row in applications] == ["member-role-outsider"]
    assert refused_receipts == []
    assert refused_quota == []
