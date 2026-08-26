import json
from base64 import b64encode
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    IdempotencyRecord,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.schemas import OrganizationVerificationDecisionRequest
from tests.helpers import profile_markdown

REVIEWER_ID = "reviewer:preprovisioned"


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


def configure_reviewer(app) -> None:
    app.state.settings = app.state.settings.model_copy(
        update={
            "verification_reviewer_id": REVIEWER_ID,
            "verification_reviewer_role": "recruiting_verifier",
        }
    )


def assert_private_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Authorization"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


async def create_submission(app, client, *, slug: str, artifact: bytes):
    as_principal(app, human(f"owner:{slug}"))
    organization = await client.post(
        "/v1/organizations",
        json={
            "slug": slug,
            "name": f"{slug.title()} Incorporated",
            "website_url": f"https://{slug}.example/careers",
            "visibility": "private",
        },
        headers={"Idempotency-Key": f"evidence-org-{slug}"},
    )
    assert organization.status_code == 201, organization.text
    submission = await client.post(
        f"/v1/organizations/{slug}/verification-submissions",
        json={
            "evidence_kind": "corporate_registration",
            "metadata": {
                "registration": f"PRIVATE-{slug.upper()}-REGISTRATION",
                "review_note": "private reviewer evidence",
            },
            "artifact_content_type": "text/plain",
            "artifact_base64": b64encode(artifact).decode("ascii"),
        },
        headers={"Idempotency-Key": f"evidence-submit-{slug}"},
    )
    assert submission.status_code == 201, submission.text
    return organization, submission


async def artifact_path(app, verification_id: str):
    async with app.state.session_factory() as session:
        evidence = await session.scalar(
            select(OrganizationVerificationEvidence).where(
                OrganizationVerificationEvidence.verification_id == verification_id
            )
        )
    assert evidence is not None
    return app.state.store._absolute(evidence.storage_path)


async def event_count(app, verification_id: str) -> int:
    async with app.state.session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(OrganizationVerificationEvent)
            .where(OrganizationVerificationEvent.verification_id == verification_id)
        )
    assert count is not None
    return count


async def review_and_activate(app, client, *, slug: str, verification_id: str) -> str:
    as_principal(app, human(REVIEWER_ID))
    detail = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    assert detail.status_code == 200, detail.text
    review_etag = detail.headers["etag"]
    reviewed = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "Idempotency-Key": f"evidence-review-{slug}",
            "If-Match": review_etag,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    activated = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/activate",
        json={
            "expected_state": "under_review",
            "policy_version": "recruiting-control-v1",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        headers={
            "Idempotency-Key": f"evidence-activate-{slug}",
            "If-Match": review_etag,
        },
    )
    assert activated.status_code == 200, activated.text
    return review_etag


async def test_private_reviewer_reads_are_authorized_bounded_and_hidden(api_client) -> None:
    app, client = api_client
    configure_reviewer(app)
    artifact = b"exact private recruiting evidence\n"
    _, submission = await create_submission(app, client, slug="evidence-read", artifact=artifact)
    verification_id = submission.json()["verification_id"]

    as_principal(app, human(REVIEWER_ID))
    missing_evidence = await client.get(
        "/v1/internal/recruiting-verifications/missing-verification/evidence"
    )
    assert missing_evidence.status_code == 404
    assert missing_evidence.json()["detail"] == "verification was not found"

    as_principal(app, human("owner:evidence-read"))
    owner_denied = await client.get(
        f"/v1/internal/recruiting-verifications/{verification_id}/evidence"
    )
    assert owner_denied.status_code == 403
    assert_private_headers(owner_denied)

    for denied_principal in (
        human("unrelated"),
        human(REVIEWER_ID, impersonated=True),
        Principal(
            subject=REVIEWER_ID,
            method="agent_api_key",
            scopes=frozenset({"*"}),
        ),
        Principal(
            subject=REVIEWER_ID,
            method="agent_grant",
            scopes=frozenset({"organizations:read"}),
            grant_mode="direct",
            resource_type="owner",
        ),
    ):
        as_principal(app, denied_principal)
        denied = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
        assert denied.status_code == 403
        assert_private_headers(denied)

    as_principal(app, None)
    anonymous = await client.get(
        f"/v1/internal/recruiting-verifications/{verification_id}/evidence"
    )
    assert anonymous.status_code == 401

    as_principal(app, human(REVIEWER_ID))
    queue = await client.get("/v1/internal/recruiting-verifications")
    assert queue.status_code == 200, queue.text
    assert_private_headers(queue)
    summary = queue.json()["verifications"][0]
    assert set(summary) == {
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
    }
    assert "PRIVATE-EVIDENCE-READ-REGISTRATION" not in queue.text
    assert "private reviewer evidence" not in queue.text

    detail = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    assert detail.status_code == 200, detail.text
    assert_private_headers(detail)
    body = detail.json()
    assert detail.headers["etag"] == body["review_etag"]
    assert body["organization_website_url"] == "https://evidence-read.example/careers"
    assert body["organization_material_version"] == 1
    assert body["evidence_metadata"] == {
        "registration": "PRIVATE-EVIDENCE-READ-REGISTRATION",
        "review_note": "private reviewer evidence",
    }
    assert body["evidence_url"] == (
        f"/v1/internal/recruiting-verifications/{verification_id}/evidence"
    )
    assert not {
        "actor_id",
        "artifact_base64",
        "owner_id",
        "storage_path",
        "submitted_by_owner_id",
    }.intersection(body)

    evidence = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}/evidence")
    assert evidence.status_code == 200, evidence.text
    assert evidence.content == artifact
    assert_private_headers(evidence)
    digest = body["evidence_sha256"]
    assert evidence.headers["content-type"] == "text/plain"
    assert evidence.headers["content-length"] == str(len(artifact))
    assert evidence.headers["content-disposition"] == (
        'attachment; filename="connectmd-verification-evidence.txt"'
    )
    assert evidence.headers["etag"] == f'"sha256-{digest}"'
    assert evidence.headers["content-digest"] == (
        f"sha-256=:{b64encode(bytes.fromhex(digest)).decode('ascii')}:"
    )
    assert evidence.headers["x-content-type-options"] == "nosniff"
    assert evidence.headers["content-security-policy"] == "sandbox"

    schema = app.openapi()
    assert not any(
        path.startswith("/v1/internal/recruiting-verifications") for path in schema["paths"]
    )
    for discovery_path in (
        "/llms.txt",
        "/llms-full.txt",
        "/v1/capabilities",
        "/.well-known/agent-card.json",
    ):
        discovery = await client.get(discovery_path)
        assert discovery.status_code == 200, discovery.text
        assert "/v1/internal/recruiting-verifications" not in discovery.text


async def test_snapshot_preconditions_tamper_and_idempotency_fail_closed(api_client) -> None:
    app, client = api_client
    configure_reviewer(app)
    artifact = b"decision-bound private evidence\n"
    _, submission = await create_submission(
        app, client, slug="evidence-decision", artifact=artifact
    )
    verification_id = submission.json()["verification_id"]
    as_principal(app, human(REVIEWER_ID))
    detail = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    assert detail.status_code == 200, detail.text
    review_etag = detail.headers["etag"]
    assert (
        "material_claim_digest"
        not in (OrganizationVerificationDecisionRequest.model_json_schema()["properties"])
    )
    assert await event_count(app, verification_id) == 1

    for action in ("review", "activate", "reject", "restore"):
        missing = await client.post(
            f"/v1/internal/recruiting-verifications/{verification_id}/{action}",
            json={"expected_state": "submitted"},
            headers={"Idempotency-Key": f"evidence-missing-{action}"},
        )
        assert missing.status_code == 428
        assert_private_headers(missing)
    wildcard = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={"Idempotency-Key": "evidence-wildcard", "If-Match": "*"},
    )
    assert wildcard.status_code == 412
    wrong_snapshot = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "Idempotency-Key": "evidence-wrong-snapshot",
            "If-Match": f'"sha256-{"0" * 64}"',
        },
    )
    assert wrong_snapshot.status_code == 412
    assert await event_count(app, verification_id) == 1

    path = await artifact_path(app, verification_id)
    path.write_bytes(b"X" * len(artifact))
    tampered = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "Idempotency-Key": "evidence-tampered-review",
            "If-Match": review_etag,
        },
    )
    assert tampered.status_code == 503
    assert tampered.json()["detail"] == "verification evidence is unavailable"
    assert "hash" not in tampered.text.lower()
    assert "storage" not in tampered.text.lower()
    assert_private_headers(tampered)
    assert await event_count(app, verification_id) == 1

    path.write_bytes(artifact)
    reviewed = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "Idempotency-Key": "evidence-review-replay",
            "If-Match": review_etag,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert_private_headers(reviewed)
    replay = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "Idempotency-Key": "evidence-review-replay",
            "If-Match": review_etag,
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.headers["idempotency-replayed"] == "true"
    assert_private_headers(replay)
    collision = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "Idempotency-Key": "evidence-review-replay",
            "If-Match": f'"sha256-{"1" * 64}"',
        },
    )
    assert collision.status_code == 409
    assert_private_headers(collision)
    assert await event_count(app, verification_id) == 2

    activated = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/activate",
        json={
            "expected_state": "under_review",
            "policy_version": "recruiting-control-v1",
            "material_claim_digest": "0" * 64,
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        headers={
            "Idempotency-Key": "evidence-activate-without-client-authority",
            "If-Match": review_etag,
        },
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["state"] == "active"
    assert await event_count(app, verification_id) == 3

    path.unlink()
    historical_replay = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={
            "Idempotency-Key": "evidence-review-replay",
            "If-Match": review_etag,
        },
    )
    assert historical_replay.status_code == 200, historical_replay.text
    assert historical_replay.json() == reviewed.json()
    assert historical_replay.headers["idempotency-replayed"] == "true"
    assert_private_headers(historical_replay)
    assert await event_count(app, verification_id) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "action",
        "actor",
        "body",
        "digest",
        "event",
        "headers",
        "role",
        "status",
    ],
)
async def test_decision_receipt_corruption_is_private_and_fails_closed(
    api_client, fault: str
) -> None:
    app, client = api_client
    configure_reviewer(app)
    slug = f"receipt-{fault}"
    artifact = f"private receipt evidence {fault}\n".encode()
    _, submission = await create_submission(app, client, slug=slug, artifact=artifact)
    verification_id = submission.json()["verification_id"]
    as_principal(app, human(REVIEWER_ID))
    detail = await client.get(f"/v1/internal/recruiting-verifications/{verification_id}")
    review_etag = detail.headers["etag"]
    key = f"receipt-review-{fault}"
    reviewed = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={"Idempotency-Key": key, "If-Match": review_etag},
    )
    assert reviewed.status_code == 200, reviewed.text

    async with app.state.session_factory() as session:
        record = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)
        )
        event = await session.scalar(
            select(OrganizationVerificationEvent).where(
                OrganizationVerificationEvent.verification_id == verification_id,
                OrganizationVerificationEvent.to_state == "under_review",
            )
        )
        assert record is not None
        assert event is not None
        assert record.resource_type == "recruiting_verification_decision"
        assert record.resource_id is not None
        if fault == "action":
            record.resource_id = record.resource_id.replace(":review:", ":reject:")
        elif fault == "actor":
            event.actor_id = "reviewer:forged"
        elif fault == "body":
            body = json.loads(record.response_body)
            body["organization_name"] = "Forged Organization"
            record.response_body = json.dumps(body, separators=(",", ":"))
        elif fault == "digest":
            record.resource_id = (
                f"{record.resource_id[:-1]}{'0' if record.resource_id[-1] != '0' else '1'}"
            )
        elif fault == "event":
            await session.delete(event)
        elif fault == "headers":
            record.response_headers = json.dumps({"Cache-Control": "public"}, sort_keys=True)
        elif fault == "role":
            event.actor_role = "submitter"
        elif fault == "status":
            record.response_status = 201
        await session.commit()

    replay = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/review",
        json={"expected_state": "submitted"},
        headers={"Idempotency-Key": key, "If-Match": review_etag},
    )
    assert replay.status_code == 503
    assert replay.json()["detail"] == (
        "idempotent recruiting verification decision receipt cannot be reconstructed"
    )
    assert_private_headers(replay)
    assert REVIEWER_ID not in replay.text
    assert "Forged Organization" not in replay.text


@pytest.mark.parametrize("action", ["suspend", "revoke", "expire"])
async def test_fail_safe_active_removal_does_not_depend_on_evidence(
    api_client, action: str
) -> None:
    app, client = api_client
    configure_reviewer(app)
    artifact = f"private evidence for {action}\n".encode()
    slug = f"evidence-{action}"
    _, submission = await create_submission(app, client, slug=slug, artifact=artifact)
    verification_id = submission.json()["verification_id"]
    review_etag = await review_and_activate(app, client, slug=slug, verification_id=verification_id)
    path = await artifact_path(app, verification_id)
    path.write_bytes(b"X" * len(artifact))

    removed = await client.post(
        f"/v1/internal/recruiting-verifications/{verification_id}/{action}",
        json={"expected_state": "active"},
        headers={"Idempotency-Key": f"evidence-{action}-corrupt"},
    )
    assert removed.status_code == 200, removed.text
    expected_state = {
        "expire": "expired",
        "revoke": "revoked",
        "suspend": "suspended",
    }[action]
    assert removed.json()["state"] == expected_state
    assert_private_headers(removed)

    if action == "suspend":
        restore = await client.post(
            f"/v1/internal/recruiting-verifications/{verification_id}/restore",
            json={
                "expected_state": "suspended",
                "policy_version": "recruiting-control-v1",
                "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            },
            headers={
                "Idempotency-Key": "evidence-restore-corrupt",
                "If-Match": review_etag,
            },
        )
        assert restore.status_code == 503
        assert restore.json()["detail"] == "verification evidence is unavailable"
        assert "hash" not in restore.text.lower()
        assert "storage" not in restore.text.lower()


async def test_public_recruiting_surfaces_fail_closed_after_artifact_corruption(api_client) -> None:
    app, client = api_client
    configure_reviewer(app)
    artifact = b"public-gate private authority evidence\n"
    organization, submission = await create_submission(
        app, client, slug="evidence-public", artifact=artifact
    )
    verification_id = submission.json()["verification_id"]
    await review_and_activate(app, client, slug="evidence-public", verification_id=verification_id)

    as_principal(app, human("owner:evidence-public"))
    publicized = await client.put(
        "/v1/organizations/evidence-public",
        json={"visibility": "public"},
        headers={
            "Idempotency-Key": "evidence-publicize",
            "If-Match": organization.headers["etag"],
        },
    )
    assert publicized.status_code == 200, publicized.text
    job = await client.post(
        "/v1/organizations/evidence-public/jobs",
        json={
            "slug": "integrity-role",
            "title": "Integrity Role",
            "description": "Requires retained recruiting authority.",
        },
        headers={"Idempotency-Key": "evidence-public-job"},
    )
    assert job.status_code == 201, job.text
    published = await client.post(
        "/v1/organizations/evidence-public/jobs/integrity-role/lifecycle/publish",
        headers={
            "Idempotency-Key": "evidence-public-job-publish",
            "If-Match": job.headers["etag"],
        },
    )
    assert published.status_code == 200, published.text

    as_principal(app, None)
    assert (await client.get("/v1/organizations/evidence-public")).status_code == 200
    assert (
        await client.get("/v1/organizations/evidence-public/jobs/integrity-role")
    ).status_code == 200
    before = await client.get("/v1/organizations")
    assert [row["slug"] for row in before.json()["organizations"]] == ["evidence-public"]
    assert [row["slug"] for row in (await client.get("/v1/jobs")).json()["jobs"]] == [
        "integrity-role"
    ]

    path = await artifact_path(app, verification_id)
    path.write_bytes(b"X" * len(artifact))
    assert (await client.get("/v1/organizations/evidence-public")).status_code == 404
    assert (
        await client.get("/v1/organizations/evidence-public/jobs/integrity-role")
    ).status_code == 404
    assert (await client.get("/v1/organizations")).json()["organizations"] == []
    assert (await client.get("/v1/jobs")).json()["jobs"] == []


async def test_all_active_recruiting_gates_require_current_configured_reviewer_authority(
    api_client,
) -> None:
    app, client = api_client
    configure_reviewer(app)
    artifact = b"configured reviewer authority evidence\n"
    organization, submission = await create_submission(
        app, client, slug="reviewer-authority", artifact=artifact
    )
    verification_id = submission.json()["verification_id"]
    await review_and_activate(
        app,
        client,
        slug="reviewer-authority",
        verification_id=verification_id,
    )

    as_principal(app, human("owner:reviewer-authority"))
    publicized = await client.put(
        "/v1/organizations/reviewer-authority",
        json={"visibility": "public"},
        headers={
            "Idempotency-Key": "reviewer-authority-publicize",
            "If-Match": organization.headers["etag"],
        },
    )
    assert publicized.status_code == 200, publicized.text
    job = await client.post(
        "/v1/organizations/reviewer-authority/jobs",
        json={
            "slug": "authority-role",
            "title": "Authority Role",
            "description": "Requires exact configured reviewer authority.",
        },
        headers={"Idempotency-Key": "reviewer-authority-job"},
    )
    assert job.status_code == 201, job.text
    published = await client.post(
        "/v1/organizations/reviewer-authority/jobs/authority-role/lifecycle/publish",
        headers={
            "Idempotency-Key": "reviewer-authority-publish",
            "If-Match": job.headers["etag"],
        },
    )
    assert published.status_code == 200, published.text

    as_principal(app, human("applicant:reviewer-authority"))
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "reviewer-authority-profile"},
    )
    assert profile.status_code == 201, profile.text
    application_body = {
        "message": "Please consider this authority-gated application.",
        "snapshot_kind": "profile",
        "snapshot_identifier": "ada-lovelace",
        "human_confirmed": True,
    }
    application = await client.post(
        "/v1/organizations/reviewer-authority/jobs/authority-role/applications",
        json=application_body,
        headers={"Idempotency-Key": "reviewer-authority-application"},
    )
    assert application.status_code == 201, application.text

    as_principal(app, None)
    assert (await client.get("/v1/organizations/reviewer-authority")).status_code == 200
    assert (
        await client.get("/v1/organizations/reviewer-authority/jobs/authority-role")
    ).status_code == 200
    assert [
        row["slug"] for row in (await client.get("/v1/organizations")).json()["organizations"]
    ] == ["reviewer-authority"]
    assert [row["slug"] for row in (await client.get("/v1/jobs")).json()["jobs"]] == [
        "authority-role"
    ]
    as_principal(app, human("owner:reviewer-authority"))
    employer_applications = await client.get(
        "/v1/organizations/reviewer-authority/jobs/authority-role/applications",
        headers={"X-Connectmd-Purpose": "job_application_review"},
    )
    assert employer_applications.status_code == 200, employer_applications.text

    async def assert_all_gates_closed(label: str) -> None:
        as_principal(app, None)
        assert (await client.get("/v1/organizations/reviewer-authority")).status_code == 404
        assert (
            await client.get("/v1/organizations/reviewer-authority/jobs/authority-role")
        ).status_code == 404
        assert (await client.get("/v1/organizations")).json()["organizations"] == []
        assert (await client.get("/v1/jobs")).json()["jobs"] == []
        as_principal(app, human("applicant:reviewer-authority"))
        denied_application = await client.post(
            "/v1/organizations/reviewer-authority/jobs/authority-role/applications",
            json=application_body,
            headers={"Idempotency-Key": f"reviewer-authority-denied-{label}"},
        )
        assert denied_application.status_code == 404
        as_principal(app, human("owner:reviewer-authority"))
        denied_employer_list = await client.get(
            "/v1/organizations/reviewer-authority/jobs/authority-role/applications",
            headers={"X-Connectmd-Purpose": "job_application_review"},
        )
        assert denied_employer_list.status_code == 404

    configured = app.state.settings
    app.state.settings = configured.model_copy(
        update={"verification_reviewer_id": "reviewer:rotated"}
    )
    await assert_all_gates_closed("rotated")
    app.state.settings = configured.model_copy(update={"verification_reviewer_id": None})
    await assert_all_gates_closed("unset")
    app.state.settings = configured.model_copy(
        update={"verification_reviewer_role": "forged_reviewer_role"}
    )
    await assert_all_gates_closed("forged-config-role")

    app.state.settings = configured
    async with app.state.session_factory() as session:
        active_event = await session.scalar(
            select(OrganizationVerificationEvent).where(
                OrganizationVerificationEvent.verification_id == verification_id,
                OrganizationVerificationEvent.to_state == "active",
            )
        )
        assert active_event is not None
        active_event.actor_id = "reviewer:forged"
        await session.commit()
    await assert_all_gates_closed("forged-event-actor")

    async with app.state.session_factory() as session:
        active_event = await session.scalar(
            select(OrganizationVerificationEvent).where(
                OrganizationVerificationEvent.verification_id == verification_id,
                OrganizationVerificationEvent.to_state == "active",
            )
        )
        assert active_event is not None
        active_event.actor_id = REVIEWER_ID
        active_event.actor_role = "submitter"
        await session.commit()
    await assert_all_gates_closed("forged-event-role")
