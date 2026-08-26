from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    Application,
    ChangeEvent,
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


async def test_application_snapshot_commit_failure_compensates_before_response(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    applicant = human("snapshot-atomicity-applicant")
    as_principal(app, applicant)
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "snapshot-atomicity-profile-create-0001"},
    )
    assert profile.status_code == 201, profile.text

    now = datetime.now(UTC)
    evidence_bytes = b"a"
    evidence_sha256 = sha256(evidence_bytes).hexdigest()
    organization = Organization(
        id="30000000-0000-4000-8000-000000000001",
        owner_id="snapshot-atomicity-employer",
        slug="snapshot-atomicity-org",
        name="Snapshot Atomicity Org",
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
        id="30000000-0000-4000-8000-000000000002",
        organization_id=organization.id,
        purpose="recruiting_control",
        submitted_by_owner_id=organization.owner_id,
        material_claim_digest=claim_digest,
        created_at=now,
    )
    job = Job(
        id="30000000-0000-4000-8000-000000000003",
        organization_id=organization.id,
        slug="snapshot-atomicity-role",
        title="Snapshot Atomicity Role",
        description="A verified role used only for commit-failure coverage.",
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
                    id="30000000-0000-4000-8000-000000000004",
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
                    id="30000000-0000-4000-8000-000000000005",
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

    original_commit = AsyncSession.commit

    commit_calls = 0

    async def commit_then_lose_acknowledgement(session: AsyncSession) -> None:
        nonlocal commit_calls
        await original_commit(session)
        commit_calls += 1
        if commit_calls == 1:
            raise RuntimeError("application commit acknowledgement lost")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_acknowledgement)
    key = "snapshot-atomicity-application-submit-0001"
    request_body = {
        "message": "This must leave one durable application state.",
        "snapshot_kind": "profile",
        "snapshot_identifier": "ada-lovelace",
        "human_confirmed": True,
    }
    recovered = await client.post(
        "/v1/organizations/snapshot-atomicity-org/jobs/snapshot-atomicity-role/applications",
        json=request_body,
        headers={"Idempotency-Key": key},
    )
    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    assert recovered.status_code == 201, recovered.text
    assert recovered.headers["idempotency-replayed"] == "true"

    async with app.state.session_factory() as session:
        applications = (
            await session.scalars(select(Application).where(Application.job_id == job.id))
        ).all()
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)
            )
        ).all()
        changes = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_type == "application",
                    ChangeEvent.resource_id == applications[0].id,
                )
            )
        ).all()
        assert len(applications) == 1
        assert len(receipts) == 1
        assert {change.event_type for change in changes} == {
            "application.received",
            "application.submitted",
        }
        application = applications[0]
        assert application.snapshot_size_bytes is not None
        assert application.snapshot_storage_path is not None
        snapshot = app.state.store.read_verified_bytes(
            application.snapshot_storage_path,
            application.snapshot_sha256,
            expected_size_bytes=application.snapshot_size_bytes,
            max_size_bytes=131_072,
        )
        assert sha256(snapshot).hexdigest() == profile.json()["etag"].removeprefix(
            '"sha256-'
        ).removesuffix('"')
        application.status = "under_review"
        application.updated_at = now + timedelta(seconds=1)
        await session.commit()

    snapshot_path = (
        "/v1/organizations/snapshot-atomicity-org/jobs/snapshot-atomicity-role/"
        f"applications/{recovered.json()['id']}/snapshot"
    )
    applicant_denied = await client.get(f"{snapshot_path}.md")
    assert applicant_denied.status_code == 404, applicant_denied.text

    as_principal(app, human("snapshot-atomicity-employer"))
    missing_purpose = await client.get(f"{snapshot_path}.md")
    assert missing_purpose.status_code == 403, missing_purpose.text

    authorized_snapshot = await client.get(
        f"{snapshot_path}.md",
        headers={"X-Connectmd-Purpose": "job_application_review"},
    )
    assert authorized_snapshot.status_code == 200, authorized_snapshot.text
    snapshot_sha256 = recovered.json()["snapshot_sha256"]
    assert authorized_snapshot.content == profile.json()["markdown"].encode("utf-8")
    assert authorized_snapshot.headers["content-type"].startswith("text/markdown")
    assert authorized_snapshot.headers["cache-control"] == "no-store"
    assert authorized_snapshot.headers["etag"] == f'"sha256-{snapshot_sha256}"'
    assert authorized_snapshot.headers["content-digest"] == (
        f"sha-256=:{b64encode(bytes.fromhex(snapshot_sha256)).decode('ascii')}:"
    )
    assert {item.strip() for item in authorized_snapshot.headers["vary"].split(",")} == {
        "Authorization"
    }
    assert "snapshot-atomicity-applicant" not in authorized_snapshot.text

    as_principal(app, applicant)
    replay = await client.post(
        "/v1/organizations/snapshot-atomicity-org/jobs/snapshot-atomicity-role/applications",
        json=request_body,
        headers={"Idempotency-Key": key},
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json() == recovered.json()
    stage_root = app.state.store.root / ".connectmd-artifact-staging" / "v1"
    assert not list(stage_root.rglob("*.bin"))
    assert not list(stage_root.rglob("*.json"))
