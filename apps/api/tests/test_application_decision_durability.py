from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    Application,
    ChangeEvent,
    IdempotencyRecord,
    Job,
    Notification,
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


async def prepare_workspace(app, client) -> tuple[dict[str, str], dict[str, str]]:
    as_principal(app, human("employer_owner"))
    organization_response = await client.post(
        "/v1/organizations",
        json={
            "slug": "acme",
            "name": "Acme, Inc.",
            "description": "A bounded employer test organization.",
            "visibility": "private",
        },
        headers={"Idempotency-Key": "application-durability-org-0001"},
    )
    assert organization_response.status_code == 201, organization_response.text
    job_response = await client.post(
        "/v1/organizations/acme/jobs",
        json={
            "slug": "backend-engineer",
            "title": "Backend Engineer",
            "description": "Build bounded application services.",
            "location": "Singapore",
            "work_mode": "hybrid",
            "employment_type": "full_time",
        },
        headers={"Idempotency-Key": "application-durability-job-0001"},
    )
    assert job_response.status_code == 201, job_response.text
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        organization = await session.get(Organization, organization_response.json()["id"])
        job = await session.get(Job, job_response.json()["id"])
        assert organization is not None and job is not None
        evidence_bytes = b"a"
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
        verification_id = str(uuid4())
        evidence_path = canonical_evidence_path(organization.id, verification_id, evidence_sha256)
        app.state.store.write_immutable_bytes(evidence_path, evidence_bytes)
        session.add_all(
            (
                OrganizationVerification(
                    id=verification_id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    submitted_by_owner_id="employer_owner",
                    material_claim_digest=claim_digest,
                    created_at=now - timedelta(seconds=3),
                ),
                OrganizationVerificationEvidence(
                    id=str(uuid4()),
                    verification_id=verification_id,
                    evidence_kind="other",
                    metadata_json="{}",
                    artifact_content_type="text/plain",
                    artifact_sha256=evidence_sha256,
                    artifact_size_bytes=len(evidence_bytes),
                    storage_path=evidence_path,
                    created_at=now - timedelta(seconds=3),
                    retention_expires_at=now + timedelta(days=365),
                ),
                OrganizationVerificationEvent(
                    id=str(uuid4()),
                    verification_id=verification_id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    to_state="submitted",
                    actor_id="employer_owner",
                    actor_role="submitter",
                    policy_version=None,
                    material_claim_digest=claim_digest,
                    expires_at=None,
                    occurred_at=now - timedelta(seconds=3),
                ),
                OrganizationVerificationEvent(
                    id=str(uuid4()),
                    verification_id=verification_id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    to_state="under_review",
                    actor_id="reviewer:preprovisioned",
                    actor_role="recruiting_verifier",
                    policy_version=None,
                    material_claim_digest=claim_digest,
                    expires_at=None,
                    occurred_at=now - timedelta(seconds=2),
                ),
                OrganizationVerificationEvent(
                    id=str(uuid4()),
                    verification_id=verification_id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    to_state="active",
                    actor_id="reviewer:preprovisioned",
                    actor_role="recruiting_verifier",
                    policy_version="recruiting-control-v1",
                    material_claim_digest=claim_digest,
                    expires_at=now + timedelta(days=30),
                    occurred_at=now - timedelta(seconds=1),
                ),
            )
        )
        await session.commit()
    publicized = await client.put(
        "/v1/organizations/acme",
        json={"visibility": "public"},
        headers={
            "If-Match": organization_response.headers["etag"],
            "Idempotency-Key": "application-durability-org-public-0001",
        },
    )
    assert publicized.status_code == 200, publicized.text
    published = await client.post(
        "/v1/organizations/acme/jobs/backend-engineer/lifecycle/publish",
        headers={
            "If-Match": job_response.headers["etag"],
            "Idempotency-Key": "application-durability-job-publish-0001",
        },
    )
    assert published.status_code == 200, published.text
    return organization_response.json(), published.json()


async def submit_application(
    app, client, job: dict[str, str], subject: str, handle: str
) -> dict[str, str]:
    as_principal(app, human(subject))
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", handle)},
        headers={"Idempotency-Key": f"application-durability-profile-{handle}"},
    )
    assert profile.status_code == 201, profile.text
    application = await client.post(
        f"/v1/organizations/acme/jobs/{job['slug']}/applications",
        json={
            "message": "A private application message that must not enter receipts.",
            "snapshot_kind": "profile",
            "snapshot_identifier": handle,
            "human_confirmed": True,
        },
        headers={"Idempotency-Key": f"application-durability-submit-{handle}"},
    )
    assert application.status_code == 201, application.text
    return application.json()


async def test_application_transition_key_openapi_and_protocol_exclusion(api_client) -> None:
    app, client = api_client
    as_principal(app, human("employer_owner"))
    missing_decision_key = await client.post(
        "/v1/organizations/missing/jobs/missing/applications/missing/review"
    )
    assert missing_decision_key.status_code == 428
    assert missing_decision_key.json()["detail"] == "Idempotency-Key is required for this operation"
    missing_withdraw_key = await client.post("/v1/applications/missing/withdraw")
    assert missing_withdraw_key.status_code == 428
    assert missing_withdraw_key.json()["detail"] == "Idempotency-Key is required for this operation"

    openapi = app.openapi()
    for path in (
        "/v1/applications/{application_id}/withdraw",
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/{action}",
    ):
        parameters = openapi["paths"][path]["post"]["parameters"]
        key_parameter = next(item for item in parameters if item["name"] == "Idempotency-Key")
        assert key_parameter["required"] is True
        assert key_parameter["schema"] == {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[\x21-\x7E]{1,128}$",
        }

    tools = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": "application-tools", "method": "tools/list", "params": {}},
    )
    assert tools.status_code == 200, tools.text
    tool_names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert "application_transition" not in tool_names
    card = await client.get("/.well-known/agent-card.json")
    assert card.status_code == 200, card.text
    card_text = card.text.lower()
    assert "application_transition" not in card_text
    assert "application decision" not in card_text


async def test_all_application_http_surfaces_reject_non_clerk_before_route_state(
    api_client,
) -> None:
    app, client = api_client
    application_body = {
        "message": "Private application body",
        "snapshot_kind": "profile",
        "snapshot_identifier": "candidate",
        "human_confirmed": True,
    }
    operations = (
        ("GET", "/v1/applications?cursor=malformed", None),
        ("GET", "/v1/applications/missing", None),
        ("POST", "/v1/applications/missing/withdraw", None),
        ("GET", "/v1/organizations/missing/jobs/missing/applications", None),
        ("POST", "/v1/organizations/missing/jobs/missing/applications", application_body),
        ("GET", "/v1/organizations/missing/jobs/missing/applications/missing", None),
        ("GET", "/v1/organizations/missing/jobs/missing/applications/missing/snapshot", None),
        ("GET", "/v1/organizations/missing/jobs/missing/applications/missing/snapshot.md", None),
        ("POST", "/v1/organizations/missing/jobs/missing/applications/missing/not-an-action", None),
    )
    legacy_credentials = (
        Principal(
            subject="legacy-application-owner",
            method="agent_api_key",
            scopes=frozenset({"applications:read", "applications:write"}),
        ),
        Principal(
            subject="legacy-application-owner",
            method="agent_grant",
            scopes=frozenset({"applications:read", "applications:write"}),
            grant_mode="direct",
            resource_type="owner",
        ),
    )

    for credential in legacy_credentials:
        as_principal(app, credential)
        for method, path, body in operations:
            response = await client.request(method, path, json=body)
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["detail"] == "application access requires a signed-in human"

    source = inspect.getsource(__import__("app.main", fromlist=["create_app"]).create_app)
    replay_start = source.index("async def application_transition_replay")
    replay_end = source.index("async def agent_grant_recovery_replay", replay_start)
    replay = source[replay_start:replay_end]
    assert replay.index("require_application_human(principal)") < replay.index(
        'record.resource_type != "application_transition"'
    )


async def test_application_review_accept_reject_withdraw_replay_once_and_privacy(
    api_client,
) -> None:
    app, client = api_client
    _organization, job = await prepare_workspace(app, client)
    review_application = await submit_application(app, client, job, "applicant-review", "reviewer")
    accept_application = await submit_application(app, client, job, "applicant-accept", "acceptor")
    reject_application = await submit_application(app, client, job, "applicant-reject", "rejector")
    withdraw_application = await submit_application(
        app, client, job, "applicant-withdraw", "withdrawer"
    )

    as_principal(app, human("employer_owner"))
    review_path = (
        f"/v1/organizations/acme/jobs/{job['slug']}/applications/{review_application['id']}/review"
    )
    reviewed = await client.post(review_path, headers={"Idempotency-Key": "decision-review-0001"})
    assert reviewed.status_code == 200, reviewed.text
    reviewed_replay = await client.post(
        review_path, headers={"Idempotency-Key": "decision-review-0001"}
    )
    assert reviewed_replay.status_code == 200
    assert reviewed_replay.json() == reviewed.json()
    assert reviewed_replay.headers["idempotency-replayed"] == "true"

    accept_path = (
        f"/v1/organizations/acme/jobs/{job['slug']}/applications/{accept_application['id']}/accept"
    )
    accepted = await client.post(accept_path, headers={"Idempotency-Key": "decision-accept-0001"})
    assert accepted.status_code == 200, accepted.text
    accepted_replay = await client.post(
        accept_path, headers={"Idempotency-Key": "decision-accept-0001"}
    )
    assert accepted_replay.status_code == 200
    assert accepted_replay.json() == accepted.json()
    assert accepted_replay.headers["idempotency-replayed"] == "true"
    accepted_fresh_key = await client.post(
        accept_path, headers={"Idempotency-Key": "decision-accept-fresh-0001"}
    )
    assert accepted_fresh_key.status_code == 409

    reject_path = (
        f"/v1/organizations/acme/jobs/{job['slug']}/applications/{reject_application['id']}/reject"
    )
    rejected = await client.post(reject_path, headers={"Idempotency-Key": "decision-reject-0001"})
    assert rejected.status_code == 200, rejected.text
    rejected_replay = await client.post(
        reject_path, headers={"Idempotency-Key": "decision-reject-0001"}
    )
    assert rejected_replay.status_code == 200
    assert rejected_replay.json() == rejected.json()
    assert rejected_replay.headers["idempotency-replayed"] == "true"

    as_principal(app, human("applicant-withdraw"))
    withdraw_path = f"/v1/applications/{withdraw_application['id']}/withdraw"
    withdrawn = await client.post(
        withdraw_path, headers={"Idempotency-Key": "decision-withdraw-0001"}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    withdrawn_replay = await client.post(
        withdraw_path, headers={"Idempotency-Key": "decision-withdraw-0001"}
    )
    assert withdrawn_replay.status_code == 200
    assert withdrawn_replay.json() == withdrawn.json()
    assert withdrawn_replay.headers["idempotency-replayed"] == "true"

    async with app.state.session_factory() as session:
        transition_receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.resource_type == "application_transition"
                )
            )
        ).all()
        assert len(transition_receipts) == 4
        assert all(receipt.response_body == "" for receipt in transition_receipts)
        assert all(
            "private application message" not in receipt.resource_id
            for receipt in transition_receipts
        )
        assert all("applicant-review" not in receipt.resource_id for receipt in transition_receipts)
        events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_type == "application",
                    ChangeEvent.event_type.in_(
                        {
                            "application.under_review",
                            "application.accepted",
                            "application.rejected",
                            "application.withdrawn",
                        }
                    ),
                )
            )
        ).all()
        assert len(events) == 8
        assert all("private application message" not in event.payload for event in events)
        notifications = (
            await session.scalars(
                select(Notification).where(Notification.resource_type == "application")
            )
        ).all()
        assert len(notifications) == 3
        assert {
            (
                notification.resource_id,
                notification.recipient_owner_id,
                notification.type,
                notification.actor_owner_id,
                notification.resource_type,
            )
            for notification in notifications
        } == {
            (
                review_application["id"],
                "applicant-review",
                "application.under_review",
                None,
                "application",
            ),
            (
                accept_application["id"],
                "applicant-accept",
                "application.accepted",
                None,
                "application",
            ),
            (
                reject_application["id"],
                "applicant-reject",
                "application.rejected",
                None,
                "application",
            ),
        }
        assert all(
            "private application message" not in str(notification.__dict__)
            and "reviewer" not in str(notification.__dict__)
            for notification in notifications
        )

    for subject, application_id, expected_type in (
        ("applicant-review", review_application["id"], "application.under_review"),
        ("applicant-accept", accept_application["id"], "application.accepted"),
        ("applicant-reject", reject_application["id"], "application.rejected"),
    ):
        as_principal(app, human(subject))
        notification_response = await client.get("/v1/notifications")
        assert notification_response.status_code == 200, notification_response.text
        applicant_notifications = notification_response.json()["notifications"]
        matching = [
            item for item in applicant_notifications if item["resource_id"] == application_id
        ]
        assert len(matching) == 1
        assert matching[0]["type"] == expected_type
        assert matching[0]["actor_owner_id"] is None
        assert matching[0]["resource_type"] == "application"
        assert "private application message" not in notification_response.text
        assert "reviewer:preprovisioned" not in notification_response.text

    as_principal(app, human("employer_owner"))
    employer_notifications = await client.get("/v1/notifications")
    assert employer_notifications.status_code == 200
    assert all(
        item["resource_id"]
        not in {
            review_application["id"],
            accept_application["id"],
            reject_application["id"],
            withdraw_application["id"],
        }
        for item in employer_notifications.json()["notifications"]
    )
    as_principal(app, human("unrelated-viewer"))
    unrelated_notifications = await client.get("/v1/notifications")
    assert unrelated_notifications.status_code == 200
    assert unrelated_notifications.json()["notifications"] == []

    as_principal(app, human("employer_owner"))
    collision = await client.post(
        f"/v1/organizations/acme/jobs/{job['slug']}/applications/{review_application['id']}/accept",
        headers={"Idempotency-Key": "decision-review-0001"},
    )
    assert collision.status_code == 409
    second_job = await client.post(
        "/v1/organizations/acme/jobs",
        json={
            "slug": "second-role",
            "title": "Second Role",
            "description": "A second bounded role.",
            "location": "Singapore",
            "work_mode": "hybrid",
            "employment_type": "full_time",
        },
        headers={"Idempotency-Key": "application-durability-second-job-0001"},
    )
    assert second_job.status_code == 201, second_job.text
    cross_path_collision = await client.post(
        f"/v1/organizations/acme/jobs/second-role/applications/{review_application['id']}/review",
        headers={"Idempotency-Key": "decision-review-0001"},
    )
    assert cross_path_collision.status_code == 409


async def test_application_transition_lock_order_is_explicit_but_sqlite_is_not_a_race_proof(
    api_client,
) -> None:
    """Source order is deterministic; SQLite does not provide PostgreSQL lock evidence."""
    _app, _client = api_client
    source = inspect.getsource(__import__("app.main", fromlist=["create_app"]).create_app)
    decision_start = source.index("async def decide_application")
    decision_end = source.index("async def create_connection_request", decision_start)
    decision = source[decision_start:decision_end]
    assert decision.index(
        "organization_by_slug(session, organization_slug, for_update=True)"
    ) < decision.index("job_by_slug(session, organization, job_slug, for_update=True)")
    assert decision.index(
        "job_by_slug(session, organization, job_slug, for_update=True)"
    ) < decision.index("assert_active_employer_application_authority")
    assert decision.index("assert_active_employer_application_authority") < decision.index(
        "replay = await idempotency_replay"
    )
    assert decision.index("replay = await idempotency_replay") < decision.index(
        "select(Application)"
    )
    second_replay = decision.rindex("replay = await idempotency_replay")
    retention_gate = decision.index("retention_expired(row.retention_expires_at)")
    status_gate = decision.index('if action == "review" and row.status != "submitted"')
    assert second_replay < retention_gate < status_gate
    withdrawal_start = source.index("async def withdraw_application")
    withdrawal_end = source.index("async def decide_application", withdrawal_start)
    withdrawal = source[withdrawal_start:withdrawal_end]
    assert withdrawal.index("idempotency_key(request, required=True)") < withdrawal.index(
        "select(Application)"
    )
    assert withdrawal.index("with_for_update()") < withdrawal.rindex("with_for_update()")
    assert withdrawal.index("organization.id") < withdrawal.index("transition_context")


async def test_application_competing_withdrawal_and_employer_decision_serialize_semantics(
    api_client,
) -> None:
    app, client = api_client
    _organization, job = await prepare_workspace(app, client)
    application = await submit_application(app, client, job, "competing-applicant", "competing")
    as_principal(app, human("competing-applicant"))
    withdraw_path = f"/v1/applications/{application['id']}/withdraw"
    withdrawn = await client.post(
        withdraw_path, headers={"Idempotency-Key": "competing-withdraw-0001"}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    as_principal(app, human("employer_owner"))
    accept_path = (
        f"/v1/organizations/acme/jobs/{job['slug']}/applications/{application['id']}/accept"
    )
    employer_loser = await client.post(
        accept_path, headers={"Idempotency-Key": "competing-accept-0001"}
    )
    assert employer_loser.status_code == 409


async def test_fresh_employer_decisions_fail_closed_after_application_retention_expiry(
    api_client,
) -> None:
    app, client = api_client
    _organization, job = await prepare_workspace(app, client)
    applications = {
        action: await submit_application(
            app, client, job, f"expired-{action}-applicant", f"expired-{action}"
        )
        for action in ("review", "accept", "reject")
    }

    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    async with app.state.session_factory() as session:
        for application in applications.values():
            row = await session.get(Application, application["id"])
            assert row is not None
            row.retention_expires_at = expired_at
        await session.commit()

    as_principal(app, human("employer_owner"))
    for action, application in applications.items():
        path = (
            f"/v1/organizations/acme/jobs/{job['slug']}/applications/{application['id']}/{action}"
        )
        response = await client.post(
            path, headers={"Idempotency-Key": f"fresh-expired-{action}-0001"}
        )
        assert response.status_code == 404, (action, response.text)
        assert response.json()["detail"] == "application was not found"
        assert response.json()["status"] == 404
        assert "Private application body" not in response.text

    async with app.state.session_factory() as session:
        rows = [
            await session.get(Application, application["id"])
            for application in applications.values()
        ]
        assert all(row is not None for row in rows)
        assert all(row.status == "submitted" for row in rows if row is not None)
        assert all(row.decision_actor_id is None for row in rows if row is not None)
        decision_events = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_type == "application",
                    ChangeEvent.resource_id.in_(
                        application["id"] for application in applications.values()
                    ),
                    ChangeEvent.event_type.in_(
                        {
                            "application.under_review",
                            "application.accepted",
                            "application.rejected",
                        }
                    ),
                )
            )
        ).all()
        assert decision_events == []
        assert (
            await session.scalars(
                select(Notification).where(
                    Notification.resource_type == "application",
                    Notification.resource_id.in_(
                        application["id"] for application in applications.values()
                    ),
                )
            )
        ).all() == []
        assert (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key.in_(
                        f"fresh-expired-{action}-0001" for action in applications
                    )
                )
            )
        ).all() == []


async def test_application_transition_replays_fail_closed_for_row_parent_status_retention_and_snapshot_corruption(
    api_client,
) -> None:
    app, client = api_client
    _organization, job = await prepare_workspace(app, client)
    deleted = await submit_application(app, client, job, "deleted-applicant", "deleted")
    substituted = await submit_application(app, client, job, "substituted-applicant", "substituted")
    status_tampered = await submit_application(app, client, job, "status-applicant", "status")
    expired = await submit_application(app, client, job, "expired-applicant", "expired")
    snapshot_tampered = await submit_application(app, client, job, "snapshot-applicant", "snapshot")
    as_principal(app, human("employer_owner"))

    cases = (
        (deleted, "corrupt-deleted-0001"),
        (substituted, "corrupt-substituted-0001"),
        (status_tampered, "corrupt-status-0001"),
        (expired, "corrupt-expired-0001"),
        (snapshot_tampered, "corrupt-snapshot-0001"),
    )
    paths: dict[str, str] = {}
    for row, key in cases:
        path = f"/v1/organizations/acme/jobs/{job['slug']}/applications/{row['id']}/review"
        paths[key] = path
        first = await client.post(path, headers={"Idempotency-Key": key})
        assert first.status_code == 200, first.text

    replacement_job = await client.post(
        "/v1/organizations/acme/jobs",
        json={
            "slug": "replacement-role",
            "title": "Replacement Role",
            "description": "A relationship substitution target.",
            "location": "Singapore",
            "work_mode": "hybrid",
            "employment_type": "full_time",
        },
        headers={"Idempotency-Key": "application-durability-replacement-job-0001"},
    )
    assert replacement_job.status_code == 201
    async with app.state.session_factory() as session:
        deleted_row = await session.get(Application, deleted["id"])
        assert deleted_row is not None
        await session.delete(deleted_row)
        substituted_row = await session.get(Application, substituted["id"])
        status_row = await session.get(Application, status_tampered["id"])
        expired_row = await session.get(Application, expired["id"])
        snapshot_row = await session.get(Application, snapshot_tampered["id"])
        assert substituted_row is not None
        assert status_row is not None
        assert expired_row is not None
        assert snapshot_row is not None
        substituted_row.job_id = replacement_job.json()["id"]
        status_row.status = "accepted"
        expired_row.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        snapshot_row.snapshot_sha256 = "0" * 64
        await session.commit()
        assert snapshot_row.snapshot_storage_path is not None
        app.state.store.delete_exact(snapshot_row.snapshot_storage_path)

    for key, path in paths.items():
        failed_replay = await client.post(path, headers={"Idempotency-Key": key})
        assert failed_replay.status_code == 503, (key, failed_replay.text)
        assert "private application message" not in failed_replay.text


async def test_application_employer_replay_requires_current_recruiting_authority(
    api_client,
) -> None:
    app, client = api_client
    organization, job = await prepare_workspace(app, client)
    application = await submit_application(app, client, job, "authority-applicant", "authority")
    as_principal(app, human("employer_owner"))
    path = f"/v1/organizations/acme/jobs/{job['slug']}/applications/{application['id']}/review"
    first = await client.post(path, headers={"Idempotency-Key": "authority-review-0001"})
    assert first.status_code == 200, first.text

    async with app.state.session_factory() as session:
        event = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(OrganizationVerificationEvent.organization_id == organization["id"])
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
        assert event is not None
        event.to_state = "suspended"
        await session.commit()
    replay_after_authority_loss = await client.post(
        path, headers={"Idempotency-Key": "authority-review-0001"}
    )
    assert replay_after_authority_loss.status_code == 404
    assert replay_after_authority_loss.json()["detail"] == "organization was not found"


async def test_application_transition_owner_snapshot_and_receipt_corruption_fail_closed(
    api_client,
) -> None:
    app, client = api_client
    _organization, job = await prepare_workspace(app, client)
    employer_application = await submit_application(
        app, client, job, "owner-employer", "ownercheck"
    )
    applicant_application = await submit_application(
        app, client, job, "owner-applicant", "withdrawcheck"
    )

    as_principal(app, human("employer_owner"))
    employer_path = f"/v1/organizations/acme/jobs/{job['slug']}/applications/{employer_application['id']}/review"
    employer_key = "corruption-employer-0001"
    employer_result = await client.post(employer_path, headers={"Idempotency-Key": employer_key})
    assert employer_result.status_code == 200, employer_result.text
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == employer_key)
        )
        row = await session.get(Application, employer_application["id"])
        assert receipt is not None and row is not None
        row.applicant_owner_id = "substituted-owner"
        await session.commit()
    employer_corruption = await client.post(
        employer_path, headers={"Idempotency-Key": employer_key}
    )
    assert employer_corruption.status_code == 503
    assert "substituted-owner" not in employer_corruption.text
    async with app.state.session_factory() as session:
        assert receipt is not None
        receipt.resource_id = receipt.resource_id[:-64] + ("0" * 64)
        await session.commit()
    employer_receipt_corruption = await client.post(
        employer_path, headers={"Idempotency-Key": employer_key}
    )
    assert employer_receipt_corruption.status_code == 503
    assert "private application message" not in employer_receipt_corruption.text

    as_principal(app, human("owner-applicant"))
    withdraw_path = f"/v1/applications/{applicant_application['id']}/withdraw"
    withdraw_key = "corruption-withdraw-0001"
    withdrawn = await client.post(withdraw_path, headers={"Idempotency-Key": withdraw_key})
    assert withdrawn.status_code == 200, withdrawn.text
    async with app.state.session_factory() as session:
        row = await session.get(Application, applicant_application["id"])
        assert row is not None
        row.applicant_owner_id = "substituted-withdraw-owner"
        await session.commit()
    applicant_corruption = await client.post(
        withdraw_path, headers={"Idempotency-Key": withdraw_key}
    )
    assert applicant_corruption.status_code == 503
    assert "substituted-withdraw-owner" not in applicant_corruption.text
