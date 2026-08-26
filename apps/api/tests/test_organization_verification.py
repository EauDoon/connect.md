from __future__ import annotations

import asyncio
from argparse import Namespace
from base64 import b64encode
from datetime import UTC, datetime, timedelta

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli as verification_cli
from app.auth import (
    IMPERSONATION_READ_ONLY_CODE,
    Principal,
    optional_principal,
    require_principal,
)
from app.models import (
    ChangeEvent,
    IdempotencyRecord,
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import (
    claims_from_rows,
    verify_recruiting_evidence,
)

from .helpers import profile_markdown


def human(subject: str, *, impersonated: bool = False) -> Principal:
    return Principal(
        subject=subject,
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        is_impersonated=impersonated,
    )


def as_principal(app, principal: Principal | None) -> None:
    async def current() -> Principal:
        assert principal is not None
        return principal

    if principal is None:
        app.dependency_overrides.pop(require_principal, None)
        app.dependency_overrides.pop(optional_principal, None)
        return
    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def _transition(
    app,
    verification_id: str,
    action: str,
    *,
    expected_review_snapshot_sha256: str | None = None,
    policy_version: str | None = None,
    material_claim_digest: str | None = None,
    expires_at: str | None = None,
) -> int:
    return await verification_cli.apply_verification_transition(
        Namespace(
            verification_id=verification_id,
            action=action,
            policy_version=policy_version,
            material_claim_digest=material_claim_digest,
            expires_at=expires_at,
            expected_review_snapshot_sha256=expected_review_snapshot_sha256,
        )
    )


async def _current_review_snapshot_sha256(app, verification_id: str) -> str:
    async with app.state.session_factory() as session:
        verification = await session.get(OrganizationVerification, verification_id)
        assert verification is not None
        organization = await session.get(Organization, verification.organization_id)
        assert organization is not None
        evidence = await session.scalar(
            select(OrganizationVerificationEvidence).where(
                OrganizationVerificationEvidence.verification_id == verification_id
            )
        )
        assert evidence is not None
        return verify_recruiting_evidence(
            app.state.store,
            claims_from_rows(organization, verification, evidence),
            now=datetime.now(UTC),
        ).review_snapshot_sha256


async def test_recruiting_verification_is_private_immutable_and_fails_closed_on_material_drift(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    as_principal(app, human("employer"))
    created = await client.post(
        "/v1/organizations",
        json={
            "slug": "acme",
            "name": "Acme",
            "website_url": "https://acme.example/careers",
            "visibility": "private",
        },
        headers={"Idempotency-Key": "verification-org-0001"},
    )
    assert created.status_code == 201, created.text

    artifact = b"private registration artifact: do not expose"
    secret_metadata = "SG-PRIVATE-REGISTRATION-123"
    submission_body = {
        "evidence_kind": "corporate_registration",
        "metadata": {"registration": secret_metadata},
        "artifact_content_type": "text/plain",
        "artifact_base64": b64encode(artifact).decode(),
    }
    as_principal(app, human("employer", impersonated=True))
    impersonated_status = await client.get("/v1/organizations/acme/verification-status")
    assert impersonated_status.status_code == 200, impersonated_status.text
    assert impersonated_status.json()["state"] == "unverified"
    impersonated_submission_key = "verification-submit-impersonated-0001"
    impersonated_denied = await client.post(
        "/v1/organizations/acme/verification-submissions",
        json=submission_body,
        headers={"Idempotency-Key": impersonated_submission_key},
    )
    assert impersonated_denied.status_code == 403, impersonated_denied.text
    assert impersonated_denied.json()["detail"] == IMPERSONATION_READ_ONLY_CODE
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(
                select(OrganizationVerification).where(
                    OrganizationVerification.organization_id == created.json()["id"]
                )
            )
            is None
        )
        assert (
            await session.scalars(
                select(OrganizationVerificationEvidence).join(
                    OrganizationVerification,
                    OrganizationVerification.id == OrganizationVerificationEvidence.verification_id,
                )
            )
        ).all() == []
        assert (
            await session.scalars(
                select(OrganizationVerificationEvent).where(
                    OrganizationVerificationEvent.organization_id == created.json()["id"]
                )
            )
        ).all() == []
        assert (
            await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == impersonated_submission_key
                )
            )
            is None
        )
        assert (
            await session.scalars(
                select(ChangeEvent.sequence).where(
                    ChangeEvent.resource_type == "organization_verification"
                )
            )
        ).all() == []
    assert not list((app.state.store.root / "verification-evidence").rglob("*.bin"))

    as_principal(app, human("employer"))
    submitted = await client.post(
        "/v1/organizations/acme/verification-submissions",
        json=submission_body,
        headers={"Idempotency-Key": "verification-submit-0001"},
    )
    assert submitted.status_code == 201, submitted.text
    first_verification_id = submitted.json()["verification_id"]
    assert secret_metadata not in submitted.text
    assert submission_body["artifact_base64"] not in submitted.text

    agent = Principal(
        subject="employer",
        method="agent_grant",
        scopes=frozenset({"organizations:write"}),
        grant_mode="direct",
        resource_type="organization",
        resource_id=created.json()["id"],
    )
    as_principal(app, agent)
    assert (
        await client.post("/v1/organizations/acme/verification-submissions", json=submission_body)
    ).status_code == 403
    as_principal(app, human("unrelated"))
    assert (
        await client.post("/v1/organizations/acme/verification-submissions", json=submission_body)
    ).status_code == 404
    as_principal(app, human("employer"))

    async with app.state.session_factory() as session:
        verification = await session.get(OrganizationVerification, first_verification_id)
        assert verification is not None
        evidence = await session.scalar(
            select(OrganizationVerificationEvidence).where(
                OrganizationVerificationEvidence.verification_id == first_verification_id
            )
        )
        assert evidence is not None
        assert evidence.metadata_json == '{"registration":"SG-PRIVATE-REGISTRATION-123"}'
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "verification-submit-0001"
            )
        )
        events = (
            await session.scalars(
                select(ChangeEvent).where(ChangeEvent.resource_type == "organization_verification")
            )
        ).all()
        assert receipt is not None
        assert secret_metadata not in receipt.response_body
        assert submission_body["artifact_base64"] not in receipt.response_body
        assert events and all(secret_metadata not in event.payload for event in events)
        assert all(event.payload == '{"state": "submitted"}' for event in events)
        assert all(evidence.artifact_sha256 not in event.payload for event in events)
        submitted_event = await session.execute(
            select(OrganizationVerificationEvent).where(
                OrganizationVerificationEvent.verification_id == first_verification_id
            )
        )
        assert submitted_event.first() is not None
    assert (
        app.state.store._absolute(
            f"verification-evidence/{created.json()['id']}/{first_verification_id}/"
            f"{submitted.json()['evidence_sha256']}.bin"
        ).read_bytes()
        == artifact
    )

    schema = app.openapi()
    submission_operation = schema["paths"][
        "/v1/organizations/{organization_slug}/verification-submissions"
    ]["post"]
    assert submission_operation["security"] == [{"ClerkBearerAuth": []}]
    assert submission_operation["x-connectmd-human-only"] is True
    assert "/v1/organizations/{organization_slug}/verification-status" in schema["paths"]
    assert not any(
        path.startswith("/v1/internal/recruiting-verifications") for path in schema["paths"]
    )
    owner_status_schema = schema["components"]["schemas"][
        "OrganizationVerificationOwnerStatusResponse"
    ]
    assert "artifact_base64" not in str(owner_status_schema)
    assert "metadata" not in str(owner_status_schema)

    reviewer_settings = app.state.settings.model_copy(
        update={
            "verification_reviewer_id": "reviewer:preprovisioned",
            "verification_reviewer_role": "recruiting_verifier",
        }
    )
    monkeypatch.setattr(verification_cli, "get_settings", lambda: reviewer_settings)
    first_review_snapshot = await _current_review_snapshot_sha256(app, first_verification_id)
    assert (
        await _transition(
            app,
            first_verification_id,
            "review",
            expected_review_snapshot_sha256=first_review_snapshot,
        )
        == 0
    )
    assert (
        await _transition(
            app,
            first_verification_id,
            "reject",
            expected_review_snapshot_sha256=first_review_snapshot,
        )
        == 0
    )

    changed = await client.put(
        "/v1/organizations/acme",
        json={"name": "Acme Renamed"},
        headers={
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "verification-name-change-0001",
        },
    )
    assert changed.status_code == 200, changed.text
    async with app.state.session_factory() as session:
        first = await session.get(OrganizationVerification, first_verification_id)
        assert first is not None
        digest = first.material_claim_digest
    assert (
        await _transition(
            app,
            first_verification_id,
            "activate",
            expected_review_snapshot_sha256=first_review_snapshot,
            policy_version="recruiting-control-v1",
            material_claim_digest=digest,
            expires_at=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        )
        == 1
    )
    resubmitted = await client.post(
        "/v1/organizations/acme/verification-submissions",
        json=submission_body,
        headers={"Idempotency-Key": "verification-submit-0002"},
    )
    assert resubmitted.status_code == 201, resubmitted.text
    second_verification_id = resubmitted.json()["verification_id"]
    assert second_verification_id != first_verification_id
    async with app.state.session_factory() as session:
        second = await session.get(OrganizationVerification, second_verification_id)
        assert second is not None
        digest = second.material_claim_digest
    second_review_snapshot = await _current_review_snapshot_sha256(app, second_verification_id)
    assert (
        await _transition(
            app,
            second_verification_id,
            "review",
            expected_review_snapshot_sha256=second_review_snapshot,
        )
        == 0
    )
    assert (
        await _transition(
            app,
            second_verification_id,
            "activate",
            expected_review_snapshot_sha256=second_review_snapshot,
            policy_version="recruiting-control-v1",
            material_claim_digest=digest,
            expires_at=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        )
        == 0
    )

    publicized = await client.put(
        "/v1/organizations/acme",
        json={"visibility": "public"},
        headers={
            "If-Match": changed.headers["etag"],
            "Idempotency-Key": "verification-public-0001",
        },
    )
    assert publicized.status_code == 200, publicized.text
    assert publicized.json()["recruiting_verification_active"] is True
    assert secret_metadata not in publicized.text
    assert "material_claim_digest" not in publicized.text

    job = await client.post(
        "/v1/organizations/acme/jobs",
        json={"slug": "engineer", "title": "Engineer", "description": "Build systems."},
        headers={"Idempotency-Key": "verification-job-0001"},
    )
    assert job.status_code == 201, job.text
    published = await client.post(
        "/v1/organizations/acme/jobs/engineer/lifecycle/publish",
        headers={
            "If-Match": job.headers["etag"],
            "Idempotency-Key": "verification-publish-0001",
        },
    )
    assert published.status_code == 200, published.text

    drifted = await client.put(
        "/v1/organizations/acme",
        json={"website_url": "https://acme.example/new-careers"},
        headers={
            "If-Match": publicized.headers["etag"],
            "Idempotency-Key": "verification-website-change-0001",
        },
    )
    assert drifted.status_code == 200, drifted.text
    assert drifted.json()["recruiting_verification_active"] is False
    owner_organization = await client.get("/v1/organizations/acme")
    assert owner_organization.status_code == 200, owner_organization.text
    assert owner_organization.json()["slug"] == "acme"
    as_principal(app, None)
    assert (await client.get("/v1/organizations/acme")).status_code == 404
    assert (await client.get("/v1/jobs")).json()["jobs"] == []

    as_principal(app, human("employer"))
    reverted = await client.put(
        "/v1/organizations/acme",
        json={"website_url": "https://acme.example/careers"},
        headers={
            "If-Match": drifted.headers["etag"],
            "Idempotency-Key": "verification-website-revert-0001",
        },
    )
    assert reverted.status_code == 200
    assert reverted.json()["recruiting_verification_active"] is False
    third_submission = await client.post(
        "/v1/organizations/acme/verification-submissions",
        json=submission_body,
        headers={"Idempotency-Key": "verification-submit-0003"},
    )
    assert third_submission.status_code == 201, third_submission.text
    third_verification_id = third_submission.json()["verification_id"]
    async with app.state.session_factory() as session:
        third = await session.get(OrganizationVerification, third_verification_id)
        assert third is not None
        digest = third.material_claim_digest
    third_review_snapshot = await _current_review_snapshot_sha256(app, third_verification_id)
    assert (
        await _transition(
            app,
            third_verification_id,
            "review",
            expected_review_snapshot_sha256=third_review_snapshot,
        )
        == 0
    )
    assert (
        await _transition(
            app,
            third_verification_id,
            "activate",
            expected_review_snapshot_sha256=third_review_snapshot,
            policy_version="recruiting-control-v1",
            material_claim_digest=digest,
            expires_at=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        )
        == 0
    )

    late_job = await client.post(
        "/v1/organizations/acme/jobs",
        json={"slug": "late", "title": "Late", "description": "Publish only while trusted."},
        headers={"Idempotency-Key": "verification-job-0002"},
    )
    assert late_job.status_code == 201, late_job.text
    as_principal(app, human("applicant"))
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "verification-applicant-profile-create"},
    )
    assert profile.status_code == 201, profile.text

    assert await _transition(app, third_verification_id, "revoke") == 0
    as_principal(app, human("employer"))
    revoked_publish = await client.post(
        "/v1/organizations/acme/jobs/late/lifecycle/publish",
        headers={
            "If-Match": late_job.headers["etag"],
            "Idempotency-Key": "verification-revoked-publish-0001",
        },
    )
    assert revoked_publish.status_code == 409
    as_principal(app, human("applicant"))
    revoked_apply = await client.post(
        "/v1/organizations/acme/jobs/engineer/applications",
        json={
            "message": "Please consider my application.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "ada-lovelace",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "verification-revoked-apply-0001"},
    )
    assert revoked_apply.status_code == 404

    as_principal(app, human("employer"))
    fourth_submission = await client.post(
        "/v1/organizations/acme/verification-submissions",
        json=submission_body,
        headers={"Idempotency-Key": "verification-submit-0004"},
    )
    assert fourth_submission.status_code == 201, fourth_submission.text
    fourth_verification_id = fourth_submission.json()["verification_id"]
    async with app.state.session_factory() as session:
        fourth = await session.get(OrganizationVerification, fourth_verification_id)
        assert fourth is not None
        digest = fourth.material_claim_digest
    fourth_review_snapshot = await _current_review_snapshot_sha256(app, fourth_verification_id)
    assert (
        await _transition(
            app,
            fourth_verification_id,
            "review",
            expected_review_snapshot_sha256=fourth_review_snapshot,
        )
        == 0
    )
    assert (
        await _transition(
            app,
            fourth_verification_id,
            "activate",
            expected_review_snapshot_sha256=fourth_review_snapshot,
            policy_version="recruiting-control-v1",
            material_claim_digest=digest,
            expires_at=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        )
        == 0
    )
    await anyio.sleep(1.1)
    expired_publish = await client.post(
        "/v1/organizations/acme/jobs/late/lifecycle/publish",
        headers={
            "If-Match": late_job.headers["etag"],
            "Idempotency-Key": "verification-expired-publish-0001",
        },
    )
    assert expired_publish.status_code == 409
    as_principal(app, human("applicant"))
    expired_apply = await client.post(
        "/v1/organizations/acme/jobs/engineer/applications",
        json={
            "message": "Please consider my application.",
            "snapshot_kind": "profile",
            "snapshot_identifier": "ada-lovelace",
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": "verification-expired-apply-0001"},
    )
    assert expired_apply.status_code == 404


async def test_recruiting_verification_reviewer_api_is_private_idempotent_and_fails_closed(
    api_client,
) -> None:
    app, client = api_client
    app.state.settings = app.state.settings.model_copy(
        update={
            "verification_reviewer_id": "reviewer:preprovisioned",
            "verification_reviewer_role": "recruiting_verifier",
        }
    )
    as_principal(app, human("employer"))
    organization = await client.post(
        "/v1/organizations",
        json={
            "slug": "review-queue",
            "name": "Review Queue",
            "website_url": "https://review-queue.example/careers",
            "visibility": "private",
        },
        headers={"Idempotency-Key": "reviewer-api-organization-0001"},
    )
    assert organization.status_code == 201, organization.text
    unverified = await client.get("/v1/organizations/review-queue/verification-status")
    assert unverified.status_code == 200, unverified.text
    assert unverified.json()["state"] == "unverified"

    secret_metadata = "SG-REVIEWER-PRIVATE-REGISTRATION"
    artifact = b"reviewer private evidence"
    submission = await client.post(
        "/v1/organizations/review-queue/verification-submissions",
        json={
            "evidence_kind": "corporate_registration",
            "metadata": {"registration": secret_metadata},
            "artifact_content_type": "text/plain",
            "artifact_base64": b64encode(artifact).decode(),
        },
        headers={"Idempotency-Key": "reviewer-api-submission-0001"},
    )
    assert submission.status_code == 201, submission.text
    verification_id = submission.json()["verification_id"]
    owner_submitted = await client.get("/v1/organizations/review-queue/verification-status")
    assert owner_submitted.status_code == 200, owner_submitted.text
    assert owner_submitted.json()["state"] == "submitted"
    assert secret_metadata not in owner_submitted.text
    assert b64encode(artifact).decode() not in owner_submitted.text

    as_principal(app, None)
    anonymous = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    assert anonymous.status_code == 401
    as_principal(
        app,
        Principal(
            subject="reviewer:preprovisioned",
            method="agent_grant",
            scopes=frozenset({"organizations:read"}),
            grant_mode="direct",
            resource_type="owner",
        ),
    )
    agent_denied = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    assert agent_denied.status_code == 403
    assert secret_metadata not in agent_denied.text
    as_principal(
        app,
        Principal(
            subject="reviewer:preprovisioned",
            method="agent_api_key",
            scopes=frozenset({"*"}),
        ),
    )
    api_key_denied = await client.get("/v1/internal/recruiting-verifications")
    assert api_key_denied.status_code == 403
    api_key_decision_denied = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={"Idempotency-Key": "reviewer-api-key-decision-0001"},
    )
    assert api_key_decision_denied.status_code == 403
    as_principal(
        app,
        Principal(
            subject="reviewer:preprovisioned",
            method="clerk_jwt",
            scopes=frozenset({"*"}),
            is_impersonated=True,
        ),
    )
    impersonated_denied = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={"Idempotency-Key": "reviewer-impersonated-decision-0001"},
    )
    assert impersonated_denied.status_code == 403
    assert {
        response.json()["detail"]
        for response in (
            agent_denied,
            api_key_denied,
            api_key_decision_denied,
            impersonated_denied,
        )
    } == {"configured recruiting-verifier authority is required"}
    async with app.state.session_factory() as session:
        denied_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == "reviewer:preprovisioned",
                    IdempotencyRecord.idempotency_key.in_(
                        {
                            "reviewer-api-key-decision-0001",
                            "reviewer-impersonated-decision-0001",
                        }
                    ),
                )
            )
        ).all()
        impersonated_transition = await session.scalar(
            select(OrganizationVerificationEvent).where(
                OrganizationVerificationEvent.verification_id == verification_id,
                OrganizationVerificationEvent.to_state == "under_review",
            )
        )
    assert denied_receipts == []
    assert impersonated_transition is None
    as_principal(app, human("unrelated"))
    unrelated_status = await client.get("/v1/organizations/review-queue/verification-status")
    assert unrelated_status.status_code == 404
    unrelated_reviewer = await client.get("/v1/internal/recruiting-verifications")
    assert unrelated_reviewer.status_code == 403

    as_principal(app, human("reviewer:preprovisioned"))
    queue = await client.get("/v1/internal/recruiting-verifications", params={"state": "submitted"})
    assert queue.status_code == 200, queue.text
    assert queue.json()["verifications"][0]["verification_id"] == verification_id
    assert secret_metadata not in queue.text
    assert b64encode(artifact).decode() not in queue.text
    assert "metadata" not in queue.text
    inspected = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    assert inspected.status_code == 200, inspected.text
    inspected_detail = inspected.json()
    assert inspected_detail["state"] == "submitted"
    assert set(inspected_detail) == {
        "verification_id",
        "organization_slug",
        "organization_name",
        "state",
        "evidence_kind",
        "evidence_sha256",
        "artifact_content_type",
        "artifact_size_bytes",
        "material_claim_digest",
        "submitted_at",
        "updated_at",
        "policy_version",
        "expires_at",
        "organization_website_url",
        "organization_material_version",
        "evidence_metadata",
        "evidence_retention_expires_at",
        "evidence_url",
        "review_etag",
    }
    assert inspected_detail["evidence_metadata"] == {"registration": secret_metadata}
    assert inspected.headers["etag"] == inspected_detail["review_etag"]
    assert "submitted_by_owner_id" not in inspected.text
    assert b64encode(artifact).decode() not in inspected.text
    review_etag = inspected_detail["review_etag"]

    reviewed = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "If-Match": review_etag,
            "Idempotency-Key": "reviewer-api-review-0001",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["state"] == "under_review"
    review_replay = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "If-Match": review_etag,
            "Idempotency-Key": "reviewer-api-review-0001",
        },
    )
    assert review_replay.status_code == 200, review_replay.text
    assert review_replay.headers["idempotency-replayed"] == "true"
    stale = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/activate",
        json={"expected_state": "submitted"},
        headers={"If-Match": review_etag, "Idempotency-Key": "reviewer-api-stale-0001"},
    )
    assert stale.status_code == 412

    stale_review_snapshot = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/activate",
        json={
            "expected_state": "under_review",
            "policy_version": "recruiting-control-v1",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        headers={
            "If-Match": f'"sha256-{"0" * 64}"',
            "Idempotency-Key": "reviewer-api-stale-digest-0001",
        },
    )
    assert stale_review_snapshot.status_code == 412

    activated = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/activate",
        json={
            "expected_state": "under_review",
            "policy_version": "recruiting-control-v1",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        headers={
            "If-Match": review_etag,
            "Idempotency-Key": "reviewer-api-activate-0001",
        },
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["state"] == "active"

    as_principal(app, human("employer"))
    owner_active = await client.get("/v1/organizations/review-queue/verification-status")
    assert owner_active.status_code == 200, owner_active.text
    assert owner_active.json()["state"] == "active"
    assert secret_metadata not in owner_active.text
    publicized = await client.put(
        "/v1/organizations/review-queue",
        json={"visibility": "public"},
        headers={
            "If-Match": organization.headers["etag"],
            "Idempotency-Key": "reviewer-api-publicize-0001",
        },
    )
    assert publicized.status_code == 200, publicized.text
    unlocked_job = await client.post(
        "/v1/organizations/review-queue/jobs",
        json={"slug": "unlocked", "title": "Unlocked", "description": "Trusted opening."},
        headers={"Idempotency-Key": "reviewer-api-job-unlocked-0001"},
    )
    assert unlocked_job.status_code == 201, unlocked_job.text
    unlocked_publish = await client.post(
        "/v1/organizations/review-queue/jobs/unlocked/lifecycle/publish",
        headers={
            "If-Match": unlocked_job.headers["etag"],
            "Idempotency-Key": "reviewer-api-publish-unlocked-0001",
        },
    )
    assert unlocked_publish.status_code == 200, unlocked_publish.text

    as_principal(app, human("reviewer:preprovisioned"))
    suspended = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/suspend",
        json={"expected_state": "active"},
        headers={"Idempotency-Key": "reviewer-api-suspend-0001"},
    )
    assert suspended.status_code == 200, suspended.text
    as_principal(app, human("employer"))
    suspended_job = await client.post(
        "/v1/organizations/review-queue/jobs",
        json={"slug": "suspended", "title": "Suspended", "description": "Blocked opening."},
        headers={"Idempotency-Key": "reviewer-api-job-suspended-0001"},
    )
    assert suspended_job.status_code == 201, suspended_job.text
    suspended_publish = await client.post(
        "/v1/organizations/review-queue/jobs/suspended/lifecycle/publish",
        headers={
            "If-Match": suspended_job.headers["etag"],
            "Idempotency-Key": "reviewer-api-publish-suspended-0001",
        },
    )
    assert suspended_publish.status_code == 409

    as_principal(app, human("reviewer:preprovisioned"))
    restored = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/restore",
        json={
            "expected_state": "suspended",
            "policy_version": "recruiting-control-v1",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        headers={
            "If-Match": review_etag,
            "Idempotency-Key": "reviewer-api-restore-0001",
        },
    )
    assert restored.status_code == 200, restored.text
    expired = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/expire",
        json={"expected_state": "active"},
        headers={"Idempotency-Key": "reviewer-api-expire-0001"},
    )
    assert expired.status_code == 200, expired.text
    assert expired.json()["state"] == "expired"

    as_principal(app, human("employer"))
    expired_job = await client.post(
        "/v1/organizations/review-queue/jobs",
        json={"slug": "expired", "title": "Expired", "description": "Expired opening."},
        headers={"Idempotency-Key": "reviewer-api-job-expired-0001"},
    )
    assert expired_job.status_code == 201, expired_job.text
    expired_publish = await client.post(
        "/v1/organizations/review-queue/jobs/expired/lifecycle/publish",
        headers={
            "If-Match": expired_job.headers["etag"],
            "Idempotency-Key": "reviewer-api-publish-expired-0001",
        },
    )
    assert expired_publish.status_code == 409

    rejected_submission = await client.post(
        "/v1/organizations/review-queue/verification-submissions",
        json={
            "evidence_kind": "other",
            "metadata": {"case": "reject"},
            "artifact_content_type": "text/plain",
            "artifact_base64": b64encode(b"new private evidence").decode(),
        },
        headers={"Idempotency-Key": "reviewer-api-submission-0002"},
    )
    assert rejected_submission.status_code == 201, rejected_submission.text
    rejected_id = rejected_submission.json()["verification_id"]
    as_principal(app, human("reviewer:preprovisioned"))
    rejected_detail = await client.get(f"/v1/internal/recruiting-verifications/{rejected_id}")
    assert rejected_detail.status_code == 200, rejected_detail.text
    rejected_review_etag = rejected_detail.json()["review_etag"]
    assert (
        await client.post(
            f"/v1/internal/recruiting-verifications/{rejected_id}/review",
            json={"expected_state": "submitted"},
            headers={
                "If-Match": rejected_review_etag,
                "Idempotency-Key": "reviewer-api-review-0002",
            },
        )
    ).status_code == 200
    rejected = await client.post(
        f"/v1/internal/recruiting-verifications/{rejected_id}/reject",
        json={"expected_state": "under_review"},
        headers={
            "If-Match": rejected_review_etag,
            "Idempotency-Key": "reviewer-api-reject-0001",
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    as_principal(app, human("employer"))
    rejected_job = await client.post(
        "/v1/organizations/review-queue/jobs",
        json={"slug": "rejected", "title": "Rejected", "description": "Rejected opening."},
        headers={"Idempotency-Key": "reviewer-api-job-rejected-0001"},
    )
    assert rejected_job.status_code == 201, rejected_job.text
    rejected_publish = await client.post(
        "/v1/organizations/review-queue/jobs/rejected/lifecycle/publish",
        headers={
            "If-Match": rejected_job.headers["etag"],
            "Idempotency-Key": "reviewer-api-publish-rejected-0001",
        },
    )
    assert rejected_publish.status_code == 409

    async with app.state.session_factory() as session:
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.resource_type == "organization_verification"
                )
            )
        ).all()
        events = (
            await session.scalars(
                select(ChangeEvent).where(ChangeEvent.resource_type == "organization_verification")
            )
        ).all()
    assert all(secret_metadata not in receipt.response_body for receipt in receipts)
    assert all(secret_metadata not in event.payload for event in events)


async def test_reviewer_decision_same_key_is_concurrently_replayed_once(api_client) -> None:
    app, client = api_client
    app.state.settings = app.state.settings.model_copy(
        update={
            "verification_reviewer_id": "reviewer:preprovisioned",
            "verification_reviewer_role": "recruiting_verifier",
        }
    )
    as_principal(app, human("employer"))
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "reviewer-race", "name": "Reviewer Race", "visibility": "private"},
        headers={"Idempotency-Key": "reviewer-race-organization-0001"},
    )
    assert organization.status_code == 201, organization.text
    submission = await client.post(
        "/v1/organizations/reviewer-race/verification-submissions",
        json={
            "evidence_kind": "other",
            "metadata": {"case": "concurrent-review"},
            "artifact_content_type": "text/plain",
            "artifact_base64": b64encode(b"private reviewer race evidence").decode(),
        },
        headers={"Idempotency-Key": "reviewer-race-submission-0001"},
    )
    assert submission.status_code == 201, submission.text
    verification_id = submission.json()["verification_id"]

    as_principal(app, human("reviewer:preprovisioned"))
    detail = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    assert detail.status_code == 200, detail.text
    url = f"/v1/internal/recruiting-verifications/{verification_id}/review"
    body = {"expected_state": "submitted"}
    headers = {
        "If-Match": detail.json()["review_etag"],
        "Idempotency-Key": "reviewer-race-decision-0001",
    }
    first, second = await asyncio.gather(
        client.post(url, json=body, headers=headers),
        client.post(url, json=body, headers=headers),
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert {
        first.headers.get("idempotency-replayed"),
        second.headers.get("idempotency-replayed"),
    } == {
        None,
        "true",
    }

    collision = await client.post(
        url,
        json={"expected_state": "under_review"},
        headers=headers,
    )
    assert collision.status_code == 409

    async with app.state.session_factory() as session:
        review_events = (
            await session.scalars(
                select(OrganizationVerificationEvent).where(
                    OrganizationVerificationEvent.verification_id == verification_id,
                    OrganizationVerificationEvent.to_state == "under_review",
                )
            )
        ).all()
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == "reviewer:preprovisioned",
                    IdempotencyRecord.idempotency_key == "reviewer-race-decision-0001",
                )
            )
        ).all()
    assert len(review_events) == 1
    assert len(receipts) == 1


async def test_reviewer_queue_cursor_pages_tied_timestamps_without_cross_filter_replay(
    api_client,
) -> None:
    app, client = api_client
    app.state.settings = app.state.settings.model_copy(
        update={
            "verification_reviewer_id": "reviewer:preprovisioned",
            "verification_reviewer_role": "recruiting_verifier",
        }
    )
    as_principal(app, human("employer"))
    verification_ids: list[str] = []
    for index in range(3):
        slug = f"queue-page-{index}"
        organization = await client.post(
            "/v1/organizations",
            json={"slug": slug, "name": f"Queue Page {index}", "visibility": "private"},
            headers={"Idempotency-Key": f"queue-page-organization-{index}"},
        )
        assert organization.status_code == 201, organization.text
        submission = await client.post(
            f"/v1/organizations/{slug}/verification-submissions",
            json={
                "evidence_kind": "other",
                "metadata": {"case": f"queue-page-{index}"},
                "artifact_content_type": "text/plain",
                "artifact_base64": b64encode(f"private queue evidence {index}".encode()).decode(),
            },
            headers={"Idempotency-Key": f"queue-page-submission-{index}"},
        )
        assert submission.status_code == 201, submission.text
        verification_ids.append(submission.json()["verification_id"])

    latest = datetime.now(UTC).replace(microsecond=0)
    async with app.state.session_factory() as session:
        events = (
            await session.scalars(
                select(OrganizationVerificationEvent).where(
                    OrganizationVerificationEvent.verification_id.in_(verification_ids)
                )
            )
        ).all()
        for event in events:
            event.occurred_at = (
                latest
                if event.verification_id in verification_ids[:2]
                else latest - timedelta(seconds=1)
            )
        await session.commit()

    as_principal(app, human("reviewer:preprovisioned"))
    params = {"state": "submitted", "limit": 1}
    first = await client.get("/v1/internal/recruiting-verifications", params=params)
    assert first.status_code == 200, first.text
    first_cursor = first.json()["next_cursor"]
    assert first_cursor
    second = await client.get(
        "/v1/internal/recruiting-verifications",
        params={**params, "cursor": first_cursor},
    )
    assert second.status_code == 200, second.text
    second_cursor = second.json()["next_cursor"]
    assert second_cursor
    third = await client.get(
        "/v1/internal/recruiting-verifications",
        params={**params, "cursor": second_cursor},
    )
    assert third.status_code == 200, third.text
    assert third.json()["next_cursor"] is None
    seen = [
        first.json()["verifications"][0]["verification_id"],
        second.json()["verifications"][0]["verification_id"],
        third.json()["verifications"][0]["verification_id"],
    ]
    assert seen == sorted(verification_ids[:2], reverse=True) + [verification_ids[2]]
    assert len(set(seen)) == len(verification_ids)
    cross_filter = await client.get(
        "/v1/internal/recruiting-verifications",
        params={"state": "under_review", "limit": 1, "cursor": first_cursor},
    )
    assert cross_filter.status_code == 400


async def test_reviewer_decision_recovers_after_lost_commit_acknowledgement(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    app.state.settings = app.state.settings.model_copy(
        update={
            "verification_reviewer_id": "reviewer:preprovisioned",
            "verification_reviewer_role": "recruiting_verifier",
        }
    )
    as_principal(app, human("employer"))
    organization = await client.post(
        "/v1/organizations",
        json={"slug": "decision-ack-loss", "name": "Decision Ack Loss", "visibility": "private"},
        headers={"Idempotency-Key": "decision-ack-loss-organization"},
    )
    assert organization.status_code == 201, organization.text
    submission = await client.post(
        "/v1/organizations/decision-ack-loss/verification-submissions",
        json={
            "evidence_kind": "other",
            "metadata": {"case": "decision-ack-loss"},
            "artifact_content_type": "text/plain",
            "artifact_base64": b64encode(b"private decision acknowledgement evidence").decode(),
        },
        headers={"Idempotency-Key": "decision-ack-loss-submission"},
    )
    assert submission.status_code == 201, submission.text
    verification_id = submission.json()["verification_id"]

    as_principal(app, human("reviewer:preprovisioned"))
    detail = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    assert detail.status_code == 200, detail.text
    original_commit = AsyncSession.commit
    commit_calls = 0

    async def commit_then_lose_acknowledgement(session: AsyncSession) -> None:
        nonlocal commit_calls
        await original_commit(session)
        commit_calls += 1
        if commit_calls == 1:
            raise RuntimeError("decision commit acknowledgement lost")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_acknowledgement)
    url = f"/v1/internal/recruiting-verifications/{verification_id}/review"
    body = {"expected_state": "submitted"}
    headers = {
        "If-Match": detail.json()["review_etag"],
        "Idempotency-Key": "decision-ack-loss-review",
    }
    lost_acknowledgement = await client.post(url, json=body, headers=headers)
    assert lost_acknowledgement.status_code == 500
    assert lost_acknowledgement.headers["content-type"].startswith("application/problem+json")
    assert lost_acknowledgement.json()["detail"] == "an unexpected server error occurred"
    assert "acknowledgement" not in lost_acknowledgement.text
    monkeypatch.setattr(AsyncSession, "commit", original_commit)

    replay = await client.post(url, json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["state"] == "under_review"
    assert replay.headers["idempotency-replayed"] == "true"
    collision = await client.post(
        url,
        json={"expected_state": "under_review"},
        headers=headers,
    )
    assert collision.status_code == 409

    async with app.state.session_factory() as session:
        review_events = (
            await session.scalars(
                select(OrganizationVerificationEvent).where(
                    OrganizationVerificationEvent.verification_id == verification_id,
                    OrganizationVerificationEvent.to_state == "under_review",
                )
            )
        ).all()
        change_events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == verification_id,
                    ChangeEvent.event_type == "organization_verification.under_review",
                )
            )
        ).all()
    assert len(review_events) == 1
    assert len(change_events) == 1


async def test_verification_evidence_survives_lost_commit_acknowledgement(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    as_principal(app, human("employer"))
    created = await client.post(
        "/v1/organizations",
        json={"slug": "ack-loss", "name": "Ack Loss", "visibility": "private"},
        headers={"Idempotency-Key": "ack-loss-organization"},
    )
    assert created.status_code == 201, created.text
    body = {
        "evidence_kind": "other",
        "metadata": {"case": "ack-loss"},
        "artifact_content_type": "text/plain",
        "artifact_base64": "cHJpdmF0ZS1hcnRpZmFjdA==",
    }
    original_commit = AsyncSession.commit
    commit_calls = 0

    async def commit_then_lose_acknowledgement(session: AsyncSession) -> None:
        nonlocal commit_calls
        await original_commit(session)
        commit_calls += 1
        if commit_calls == 1:
            raise RuntimeError("commit acknowledgement lost")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_acknowledgement)
    recovered = await client.post(
        "/v1/organizations/ack-loss/verification-submissions",
        json=body,
        headers={"Idempotency-Key": "ack-loss-verification"},
    )
    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    assert recovered.status_code == 201, recovered.text
    assert recovered.headers["idempotency-replayed"] == "true"

    async with app.state.session_factory() as session:
        verification_rows = (await session.scalars(select(OrganizationVerification))).all()
        evidence_rows = (await session.scalars(select(OrganizationVerificationEvidence))).all()
        event_rows = (await session.scalars(select(OrganizationVerificationEvent))).all()
        change_rows = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.event_type == "organization_verification.submitted"
                )
            )
        ).all()
        receipt_rows = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "ack-loss-verification"
                )
            )
        ).all()
        assert len(verification_rows) == 1
        assert len(evidence_rows) == 1
        assert len(event_rows) == 1
        assert len(change_rows) == 1
        assert len(receipt_rows) == 1
        evidence = evidence_rows[0]
        assert evidence is not None
        assert change_rows[0].payload == '{"state": "submitted"}'
        assert evidence.artifact_sha256 not in change_rows[0].payload
        verified_bytes = app.state.store.read_verified_bytes(
            evidence.storage_path,
            evidence.artifact_sha256,
            expected_size_bytes=evidence.artifact_size_bytes,
            max_size_bytes=262_144,
        )
        assert verified_bytes == b"private-artifact"
    replay = await client.post(
        "/v1/organizations/ack-loss/verification-submissions",
        json=body,
        headers={"Idempotency-Key": "ack-loss-verification"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json() == recovered.json()


async def test_expired_evidence_cannot_authorize_public_recruiting_before_purge(api_client) -> None:
    app, client = api_client
    now = datetime.now(UTC)
    organization_id = "60000000-0000-4000-8000-000000000001"
    verification_id = "60000000-0000-4000-8000-000000000002"
    claim_digest = material_claim_digest(
        organization_id=organization_id,
        organization_name="Expired Evidence",
        organization_website_url=None,
        organization_material_version=1,
        evidence_kind="other",
        metadata={},
        artifact_content_type="text/plain",
        artifact_sha256="f" * 64,
        artifact_size_bytes=1,
    )
    async with app.state.session_factory() as session:
        session.add_all(
            (
                Organization(
                    id=organization_id,
                    owner_id="owner",
                    slug="expired-evidence",
                    name="Expired Evidence",
                    description=None,
                    website_url=None,
                    visibility="public",
                    verification_status="verified",
                    verification_material_version=1,
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
                OrganizationVerification(
                    id=verification_id,
                    organization_id=organization_id,
                    purpose="recruiting_control",
                    submitted_by_owner_id="owner",
                    material_claim_digest=claim_digest,
                    created_at=now,
                ),
                OrganizationVerificationEvidence(
                    id="60000000-0000-4000-8000-000000000003",
                    verification_id=verification_id,
                    evidence_kind="other",
                    metadata_json="{}",
                    artifact_content_type="text/plain",
                    artifact_sha256="f" * 64,
                    artifact_size_bytes=1,
                    storage_path=(
                        f"verification-evidence/{organization_id}/{verification_id}/{'f' * 64}.bin"
                    ),
                    created_at=now - timedelta(days=365),
                    retention_expires_at=now - timedelta(seconds=1),
                ),
                OrganizationVerificationEvent(
                    id="60000000-0000-4000-8000-000000000004",
                    verification_id=verification_id,
                    organization_id=organization_id,
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
    assert (await client.get("/v1/organizations")).json()["organizations"] == []
    assert (await client.get("/v1/organizations/expired-evidence")).status_code == 404
