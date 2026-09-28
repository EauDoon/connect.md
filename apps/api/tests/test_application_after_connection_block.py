from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    Application,
    ConnectionBlock,
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

REVIEW = {"X-Connectmd-Purpose": "job_application_review"}


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
        id="39000000-0000-4000-8000-000000000001",
        owner_id="later-block-owner",
        slug="later-block-org",
        name="Later Block Org",
        description=None,
        website_url=None,
        visibility="public",
        verification_status="unverified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    evidence_bytes = b"later-block-evidence"
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
        id="39000000-0000-4000-8000-000000000002",
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
                    id="39000000-0000-4000-8000-000000000003",
                    organization_id=organization.id,
                    slug="later-block-role",
                    title="Later Block Role",
                    description="A published role.",
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
                    id="39000000-0000-4000-8000-000000000004",
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
                    id="39000000-0000-4000-8000-000000000005",
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
        headers={"Idempotency-Key": f"later-block-profile-{handle}"},
    )
    assert created.status_code == 201, created.text


async def _apply(client, handle: str, message: str) -> str:
    created = await client.post(
        "/v1/organizations/later-block-org/jobs/later-block-role/applications",
        json={
            "message": message,
            "snapshot_kind": "profile",
            "snapshot_identifier": handle,
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": f"later-block-apply-{handle}"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_later_connection_block_hides_an_application_from_the_employer(api_client) -> None:
    app, client = api_client
    await _seed_published_job(app)
    await _profile(app, client, "later-block-owner", "later-block-owner")
    await _profile(app, client, "later-block-candidate", "later-block-candidate")
    await _profile(app, client, "later-block-other", "later-block-other")
    as_principal(app, human("later-block-candidate"))
    blocked_id = await _apply(client, "later-block-candidate", "Private note from the blocked candidate.")
    as_principal(app, human("later-block-other"))
    other_id = await _apply(client, "later-block-other", "Private note from an unblocked candidate.")

    as_principal(app, human("later-block-owner"))
    listed = await client.get(
        "/v1/organizations/later-block-org/jobs/later-block-role/applications",
        headers=REVIEW,
    )
    assert listed.status_code == 200, listed.text
    assert {row["id"] for row in listed.json()["applications"]} == {blocked_id, other_id}

    async with app.state.session_factory() as session:
        session.add(
            ConnectionBlock(
                id="39000000-0000-4000-8000-000000000010",
                blocker_owner_id="later-block-owner",
                blocked_owner_id="later-block-candidate",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    hidden_list = await client.get(
        "/v1/organizations/later-block-org/jobs/later-block-role/applications",
        headers=REVIEW,
    )
    assert hidden_list.status_code == 200, hidden_list.text
    assert [row["id"] for row in hidden_list.json()["applications"]] == [other_id]
    assert "Private note from the blocked candidate." not in hidden_list.text
    detail = await client.get(
        f"/v1/organizations/later-block-org/jobs/later-block-role/applications/{blocked_id}",
        headers=REVIEW,
    )
    assert detail.status_code == 404, detail.text
    snapshot = await client.get(
        f"/v1/organizations/later-block-org/jobs/later-block-role/applications/{blocked_id}/snapshot",
        headers=REVIEW,
    )
    assert snapshot.status_code == 404, snapshot.text
    decision = await client.post(
        f"/v1/organizations/later-block-org/jobs/later-block-role/applications/{blocked_id}/accept",
        headers={"Idempotency-Key": "later-block-accept"},
    )
    assert decision.status_code == 404, decision.text
    assert decision.json()["detail"] == "application was not found"

    as_principal(app, human("later-block-candidate"))
    mine = await client.get(f"/v1/applications/{blocked_id}")
    assert mine.status_code == 200, mine.text
    assert mine.json()["status"] == "submitted"

    async with app.state.session_factory() as session:
        row = await session.get(Application, blocked_id)
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "later-block-accept"
                )
            )
        ).all()
    assert row is not None and row.status == "submitted"
    assert receipts == []
