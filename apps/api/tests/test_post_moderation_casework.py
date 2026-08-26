from __future__ import annotations

import asyncio
from argparse import Namespace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request
from sqlalchemy import select

from app import cli
from app.auth import Principal, optional_principal, require_principal
from app.markdown import prepare_client_document
from app.models import (
    ChangeEvent,
    IdempotencyRecord,
    ModerationAppeal,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    Post,
    PostReport,
)
from app.services.documents import public_owner_id
from app.services.post_moderation import (
    PostModerationPreconditionError,
    appeal_evidence_manifest,
    appeal_evidence_snapshot,
    case_evidence_manifest,
    case_evidence_snapshot,
    decide_case,
    evidence_manifest_sha256,
    lock_appeal_review_bundle,
    lock_case_review_bundle,
    review_appeal,
)
from app.services.retention import RetentionExecutor

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def agent(subject: str) -> Principal:
    return Principal(subject=subject, method="agent_api_key", scopes=frozenset({"documents:read"}))


def as_principal(app, value: Principal) -> None:
    async def current() -> Principal:
        return value

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


def post_markdown() -> str:
    return """---
schema: connect.md/post
schema_version: 1
title: Moderation casework regression
topics: [reliability]
visibility: public
---
# Moderation casework regression

A bounded professional post.
"""


def moderation_settings(app):
    return app.state.settings.model_copy(
        update={
            "post_moderator_id": "moderator:configured",
            "post_moderator_role": "content_moderator",
            "appeal_reviewer_id": "appeals:configured",
            "appeal_reviewer_role": "appeal_reviewer",
        }
    )


async def create_profile(client, app, owner: str, handle: str) -> None:
    as_principal(app, human(owner))
    markdown = profile_markdown(visibility="public").replace("ada-lovelace", handle)
    response = await client.post(
        "/v1/profiles",
        json={"markdown": markdown},
        headers={"Idempotency-Key": f"casework-profile-create-{handle}"},
    )
    assert response.status_code == 201, response.text


async def test_case_linked_reports_private_appeal_and_withdrawn_overturn(
    api_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    await create_profile(client, app, "reader", "reader-profile")
    as_principal(app, human("author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "casework-post-0001"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]

    narrative = "reporter-private narrative must not reach the post subject"
    as_principal(app, human("reader"))
    first_report = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam", "narrative": narrative},
        headers={"Idempotency-Key": "casework-report-0001"},
    )
    assert first_report.status_code == 201, first_report.text
    as_principal(app, human("another-reader"))
    second_report = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "harassment", "narrative": "another private narrative"},
        headers={"Idempotency-Key": "casework-report-0002"},
    )
    assert second_report.status_code == 201, second_report.text
    async with app.state.session_factory() as session:
        reports = (await session.scalars(select(PostReport).order_by(PostReport.id))).all()
        case = await session.scalar(select(ModerationCase).where(ModerationCase.post_id == post_id))
        audit_events = (await session.scalars(select(ModerationAuditEvent))).all()
        assert case is not None and case.status == "open"
        assert len(reports) == 2 and {report.case_id for report in reports} == {case.id}
        assert {event.event_type for event in audit_events} == {"case_opened", "report_linked"}

    settings = moderation_settings(app)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert (
        await cli.moderate_post(
            Namespace(
                case_id=case.id,
                post_id=post_id,
                post_moderation_action="withhold",
                reason_code="spam",
                subject_explanation="The post was withheld after a review.",
            )
        )
        == 0
    )

    as_principal(app, human("author"))
    cases = await client.get("/v1/moderation/cases")
    assert cases.status_code == 200, cases.text
    assert cases.json()["cases"][0]["reason_code"] == "spam"
    assert cases.json()["cases"][0]["appeal_deadline"] is not None
    assert narrative not in cases.text
    assert "reader" not in cases.text
    assert (await client.get(f"/v1/posts/{post_id}")).status_code == 404

    withdrawn = await client.delete(
        f"/v1/posts/{post_id}",
        headers={"Idempotency-Key": "casework-withdraw-0001", "If-Match": created.headers["etag"]},
    )
    assert withdrawn.status_code == 204, withdrawn.text
    appeal_text = "I request independent review of this adverse decision."
    appeal = await client.post(
        f"/v1/moderation/cases/{case.id}/appeals",
        json={"rationale": appeal_text},
        headers={"Idempotency-Key": "casework-appeal-0001"},
    )
    assert appeal.status_code == 201, appeal.text
    assert appeal_text not in appeal.text
    replay = await client.post(
        f"/v1/moderation/cases/{case.id}/appeals",
        json={"rationale": appeal_text},
        headers={"Idempotency-Key": "casework-appeal-0001"},
    )
    assert replay.status_code == 201 and replay.headers["idempotency-replayed"] == "true"
    assert appeal_text not in replay.text
    async with app.state.session_factory() as session:
        shared_appeal_bundle = await lock_appeal_review_bundle(
            session, appeal_id=appeal.json()["id"], read=True
        )
        shared_appeal_digest = appeal_evidence_snapshot(
            app.state.store, shared_appeal_bundle
        ).sha256
        assert len(shared_appeal_digest) == 64
        await session.rollback()
    async with app.state.session_factory() as session:
        with pytest.raises(
            PostModerationPreconditionError,
            match="moderation evidence snapshot precondition failed",
        ):
            await review_appeal(
                session,
                app.state.store,
                settings,
                appeal_id=appeal.json()["id"],
                action="overturn",
                subject_explanation="The appeal received independent review.",
                actor_method="direct_service_test",
                expected_snapshot_sha256="0" * 64,
            )
        stale_appeal = await session.get(ModerationAppeal, appeal.json()["id"])
        stale_case = await session.get(ModerationCase, case.id)
        stale_post = await session.get(Post, post_id)
        assert stale_appeal is not None and stale_appeal.status == "submitted"
        assert stale_case is not None and stale_case.status == "appealed"
        assert stale_post is not None and stale_post.status == "withdrawn"
        assert not session.new
        await session.rollback()
    original_moderator_as_reviewer = settings.model_copy(
        update={
            "post_moderator_id": "moderator:replacement",
            "appeal_reviewer_id": "moderator:configured",
        }
    )
    monkeypatch.setattr(cli, "get_settings", lambda: original_moderator_as_reviewer)
    assert (
        await cli.review_post_appeal(
            Namespace(
                appeal_id=appeal.json()["id"],
                appeal_action="overturn",
                subject_explanation="This reviewer is not independent.",
            )
        )
        == 1
    )
    subject_as_reviewer = settings.model_copy(update={"appeal_reviewer_id": "author"})
    monkeypatch.setattr(cli, "get_settings", lambda: subject_as_reviewer)
    assert (
        await cli.review_post_appeal(
            Namespace(
                appeal_id=appeal.json()["id"],
                appeal_action="overturn",
                subject_explanation="This reviewer is also not independent.",
            )
        )
        == 1
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert (
        await cli.review_post_appeal(
            Namespace(
                appeal_id=appeal.json()["id"],
                appeal_action="overturn",
                subject_explanation="The appeal was independently overturned.",
            )
        )
        == 0
    )
    delayed_appeal_replay = await client.post(
        f"/v1/moderation/cases/{case.id}/appeals",
        json={"rationale": appeal_text},
        headers={"Idempotency-Key": "casework-appeal-0001"},
    )
    assert delayed_appeal_replay.status_code == 201, delayed_appeal_replay.text
    assert delayed_appeal_replay.json() == appeal.json()
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        case = await session.get(ModerationCase, case.id)
        decision = await session.scalar(
            select(ModerationDecision).where(ModerationDecision.case_id == case.id)
        )
        appeal_row = await session.scalar(
            select(ModerationAppeal).where(ModerationAppeal.case_id == case.id)
        )
        moderation_changes = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_id == post_id,
                    ChangeEvent.event_type.in_({"post.withheld", "post.restored"}),
                )
            )
        ).all()
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "casework-appeal-0001"
            )
        )
        assert post is not None and post.status == "withdrawn"
        assert case is not None and case.status == "appeal_overturned"
        assert decision is not None and decision.moderator_id == "moderator:configured"
        assert decision.evidence_snapshot_sha256 is not None
        assert appeal_row is not None and appeal_row.status == "overturned"
        assert appeal_row.review_snapshot_sha256 is not None
        decision_digest = decision.evidence_snapshot_sha256
        appeal_digest = appeal_row.review_snapshot_sha256
        assert all(change.actor_id == "system:post-moderation" for change in moderation_changes)
        assert all(change.actor_method == "system" for change in moderation_changes)
        assert "moderator:configured" not in str([change.__dict__ for change in moderation_changes])
        assert "appeals:configured" not in str([change.__dict__ for change in moderation_changes])
        assert receipt is not None and receipt.response_body == ""
        assert appeal_text not in receipt.response_body

    executor = RetentionExecutor(
        app.state.session_factory,
        app.state.store,
        worker_id="moderation-evidence-retention-test",
    )
    async with app.state.session_factory() as session:
        await executor._dispose_moderation_case(session, case.id)
        await session.commit()
    async with app.state.session_factory() as session:
        reports = (
            await session.scalars(select(PostReport).where(PostReport.case_id == case.id))
        ).all()
        decision = await session.scalar(
            select(ModerationDecision).where(ModerationDecision.case_id == case.id)
        )
        appeal_row = await session.scalar(
            select(ModerationAppeal).where(ModerationAppeal.case_id == case.id)
        )
        assert all(report.narrative is None for report in reports)
        assert decision is not None and decision.evidence_snapshot_sha256 == decision_digest
        assert appeal_row is not None and appeal_row.rationale is None
        assert appeal_row.review_snapshot_sha256 == appeal_digest


def test_evidence_manifests_are_deterministic_deidentified_and_fact_bound() -> None:
    now = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    post = Post(
        id="post-evidence",
        owner_id="subject-private",
        author_profile_document_id="profile-evidence",
        author_profile_handle="subject-profile",
        status="withheld",
        current_version=1,
        sha256="a" * 64,
        storage_path="posts/post-evidence/versions/000001.md",
        published_at=now,
        created_at=now,
        updated_at=now,
        withheld_at=now,
    )
    case = ModerationCase(
        id="case-evidence",
        post_id=post.id,
        subject_owner_id="subject-private",
        status="appealed",
        created_at=now,
        updated_at=now,
    )
    report = PostReport(
        id="report-evidence",
        post_id=post.id,
        case_id=case.id,
        reporter_owner_id="reporter-must-not-appear",
        reason_code="privacy",
        narrative="private narrative",
        created_at=now,
    )
    reports = (report,)
    first = evidence_manifest_sha256(case_evidence_manifest(case, post, reports))
    assert first == evidence_manifest_sha256(case_evidence_manifest(case, post, reports))
    serialized = str(case_evidence_manifest(case, post, reports))
    assert "reporter-must-not-appear" not in serialized
    assert "subject-private" not in serialized

    report.narrative = "changed narrative"
    assert evidence_manifest_sha256(case_evidence_manifest(case, post, reports)) != first
    report.narrative = "private narrative"
    report.reason_code = "spam"
    assert evidence_manifest_sha256(case_evidence_manifest(case, post, reports)) != first
    report.reason_code = "privacy"
    post.sha256 = "b" * 64
    assert evidence_manifest_sha256(case_evidence_manifest(case, post, reports)) != first
    post.sha256 = "a" * 64
    case.updated_at = now + timedelta(seconds=1)
    assert evidence_manifest_sha256(case_evidence_manifest(case, post, reports)) != first
    case.updated_at = now

    decision = ModerationDecision(
        id="decision-evidence",
        case_id=case.id,
        post_id=post.id,
        moderator_id="moderator-must-not-appear",
        moderator_role="content_moderator",
        action="withhold",
        reason_code="privacy",
        subject_explanation="A bounded explanation.",
        evidence_snapshot_sha256="c" * 64,
        decided_at=now,
    )
    appeal = ModerationAppeal(
        id="appeal-evidence",
        case_id=case.id,
        decision_id=decision.id,
        subject_owner_id=case.subject_owner_id,
        rationale="Appeal rationale",
        status="submitted",
        submitted_at=now,
    )
    appeal_manifest = appeal_evidence_manifest(case, post, reports, decision, appeal)
    appeal_digest = evidence_manifest_sha256(appeal_manifest)
    assert appeal_digest == evidence_manifest_sha256(
        appeal_evidence_manifest(case, post, reports, decision, appeal)
    )
    assert "moderator-must-not-appear" not in str(appeal_manifest)
    decision.subject_explanation = "A changed explanation."
    assert (
        evidence_manifest_sha256(appeal_evidence_manifest(case, post, reports, decision, appeal))
        != appeal_digest
    )
    decision.subject_explanation = "A bounded explanation."
    appeal.rationale = "Changed appeal rationale"
    assert (
        evidence_manifest_sha256(appeal_evidence_manifest(case, post, reports, decision, appeal))
        != appeal_digest
    )


async def test_cli_and_direct_service_share_the_same_locked_decision_semantics(
    api_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = api_client
    await create_profile(client, app, "parity-author", "parity-author-profile")
    as_principal(app, human("parity-author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "parity-post-0001"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    as_principal(app, human("parity-reporter"))
    reported = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "privacy", "narrative": "private parity evidence"},
        headers={"Idempotency-Key": "parity-report-0001"},
    )
    assert reported.status_code == 201, reported.text
    async with app.state.session_factory() as session:
        case = await session.scalar(select(ModerationCase).where(ModerationCase.post_id == post_id))
        assert case is not None
        case_id = case.id
    settings = moderation_settings(app)
    async with app.state.session_factory() as session:
        shared_bundle = await lock_case_review_bundle(
            session,
            case_id=case_id,
            expected_post_id=post_id,
            read=True,
        )
        shared_digest = case_evidence_snapshot(app.state.store, shared_bundle).sha256
        await session.rollback()

    async with app.state.session_factory() as session:
        with pytest.raises(
            PostModerationPreconditionError,
            match="moderation evidence snapshot precondition failed",
        ):
            await decide_case(
                session,
                app.state.store,
                settings,
                case_id=case_id,
                expected_post_id=post_id,
                action="withhold",
                reason_code="privacy",
                subject_explanation="The post was withheld after review.",
                actor_method="direct_service_test",
                expected_snapshot_sha256="0" * 64,
            )
        stale_post = await session.get(Post, post_id)
        stale_case = await session.get(ModerationCase, case_id)
        assert stale_post is not None and stale_post.status == "published"
        assert stale_case is not None and stale_case.status == "open"
        assert not session.new
        await session.rollback()

    async with app.state.session_factory() as session:
        direct = await decide_case(
            session,
            app.state.store,
            settings,
            case_id=case_id,
            expected_post_id=post_id,
            action="withhold",
            reason_code="privacy",
            subject_explanation="The post was withheld after review.",
            actor_method="direct_service_test",
            expected_snapshot_sha256=shared_digest,
            now=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        )
        direct_digest = direct.evidence.sha256
        assert direct.post.status == "withheld"
        assert direct.case.status == "withheld"
        assert direct.decision.action == "withhold"
        await session.rollback()

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert (
        await cli.moderate_post(
            Namespace(
                case_id=case_id,
                post_id=post_id,
                post_moderation_action="withhold",
                reason_code="privacy",
                subject_explanation="The post was withheld after review.",
            )
        )
        == 0
    )
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        case = await session.get(ModerationCase, case_id)
        decision = await session.scalar(
            select(ModerationDecision).where(ModerationDecision.case_id == case_id)
        )
        assert post is not None and post.status == "withheld"
        assert case is not None and case.status == "withheld"
        assert decision is not None and decision.action == "withhold"
        assert decision.evidence_snapshot_sha256 == direct_digest


async def test_moderation_decision_fails_closed_when_canonical_storage_is_corrupt(
    api_client,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, client = api_client
    await create_profile(client, app, "storage-author", "storage-author-profile")
    as_principal(app, human("storage-author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "storage-failure-post-0001"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    as_principal(app, human("storage-reporter"))
    reported = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "privacy", "narrative": "private evidence"},
        headers={"Idempotency-Key": "storage-failure-report-0001"},
    )
    assert reported.status_code == 201, reported.text
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        case = await session.scalar(select(ModerationCase).where(ModerationCase.post_id == post_id))
        assert post is not None and case is not None
        storage_path = post.storage_path
        case_id = case.id
    app.state.store.delete_exact(storage_path)
    monkeypatch.setattr(cli, "get_settings", lambda: moderation_settings(app))
    result = await cli.moderate_post(
        Namespace(
            case_id=case_id,
            post_id=post_id,
            post_moderation_action="withhold",
            reason_code="privacy",
            subject_explanation="The evidence was reviewed.",
        )
    )
    assert result == 1
    assert "canonical post storage failed verification" in capsys.readouterr().err
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        case = await session.get(ModerationCase, case_id)
        assert post is not None and post.status == "published"
        assert case is not None and case.status == "open"
        assert (
            await session.scalar(
                select(ModerationDecision).where(ModerationDecision.case_id == case_id)
            )
            is None
        )


async def test_moderation_decision_rejects_valid_canonical_bytes_for_a_different_post(
    api_client,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, client = api_client
    await create_profile(client, app, "binding-author", "binding-author-profile")
    as_principal(app, human("binding-author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "binding-post-0001"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    as_principal(app, human("binding-reporter"))
    reported = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "privacy"},
        headers={"Idempotency-Key": "binding-report-0001"},
    )
    assert reported.status_code == 201, reported.text
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        case = await session.scalar(select(ModerationCase).where(ModerationCase.post_id == post_id))
        assert post is not None and case is not None
        published_at = (
            post.published_at
            if post.published_at.tzinfo is not None
            else post.published_at.replace(tzinfo=UTC)
        )
        updated_at = (
            post.updated_at
            if post.updated_at.tzinfo is not None
            else post.updated_at.replace(tzinfo=UTC)
        )
        mismatched, _ = prepare_client_document(
            "post",
            post_markdown(),
            document_id="00000000-0000-4000-8000-000000000002",
            owner_id="",
            version=1,
            updated_at=updated_at,
            published_at=published_at,
            author_profile_handle=post.author_profile_handle,
        )
        storage_path = f"posts/{post_id}/versions/mismatched.md"
        post.sha256 = app.state.store.write_immutable(storage_path, mismatched)
        post.storage_path = storage_path
        case_id = case.id
        await session.commit()
    monkeypatch.setattr(cli, "get_settings", lambda: moderation_settings(app))
    assert (
        await cli.moderate_post(
            Namespace(
                case_id=case_id,
                post_id=post_id,
                post_moderation_action="withhold",
                reason_code="privacy",
                subject_explanation="The evidence was reviewed.",
            )
        )
        == 1
    )
    assert "canonical post storage failed verification" in capsys.readouterr().err
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        case = await session.get(ModerationCase, case_id)
        assert post is not None and post.status == "published"
        assert case is not None and case.status == "open"


async def test_moderation_decision_rejects_inconsistent_report_evidence(
    api_client,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, client = api_client
    await create_profile(client, app, "report-author", "report-author-profile")
    as_principal(app, human("report-author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "report-binding-post-0001"},
    )
    other = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "report-binding-post-0002"},
    )
    assert created.status_code == other.status_code == 201
    post_id = created.json()["id"]
    as_principal(app, human("report-binding-reporter"))
    reported = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam"},
        headers={"Idempotency-Key": "report-binding-report-0001"},
    )
    assert reported.status_code == 201, reported.text
    async with app.state.session_factory() as session:
        report = await session.get(PostReport, reported.json()["id"])
        case = await session.scalar(select(ModerationCase).where(ModerationCase.post_id == post_id))
        assert report is not None and case is not None
        report.reason_code = "invalid-reason"
        case_id = case.id
        await session.commit()
    monkeypatch.setattr(cli, "get_settings", lambda: moderation_settings(app))
    args = Namespace(
        case_id=case_id,
        post_id=post_id,
        post_moderation_action="withhold",
        reason_code="spam",
        subject_explanation="The evidence was reviewed.",
    )
    assert await cli.moderate_post(args) == 1
    assert "moderation report evidence is inconsistent" in capsys.readouterr().err

    async with app.state.session_factory() as session:
        report = await session.get(PostReport, reported.json()["id"])
        assert report is not None
        report.reason_code = "spam"
        report.post_id = other.json()["id"]
        await session.commit()
    assert await cli.moderate_post(args) == 1
    assert "moderation report evidence is inconsistent" in capsys.readouterr().err
    async with app.state.session_factory() as session:
        case = await session.get(ModerationCase, case_id)
        assert case is not None and case.status == "open"
        assert (
            await session.scalar(
                select(ModerationDecision).where(ModerationDecision.case_id == case_id)
            )
            is None
        )


async def test_appeal_review_rejects_published_post_for_uphold_and_overturn(
    api_client,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, client = api_client
    await create_profile(client, app, "appeal-state-author", "appeal-state-author-profile")
    as_principal(app, human("appeal-state-author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "appeal-state-post-0001"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    as_principal(app, human("appeal-state-reporter"))
    reported = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam"},
        headers={"Idempotency-Key": "appeal-state-report-0001"},
    )
    assert reported.status_code == 201, reported.text
    async with app.state.session_factory() as session:
        case = await session.scalar(select(ModerationCase).where(ModerationCase.post_id == post_id))
        assert case is not None
        case_id = case.id
    monkeypatch.setattr(cli, "get_settings", lambda: moderation_settings(app))
    assert (
        await cli.moderate_post(
            Namespace(
                case_id=case_id,
                post_id=post_id,
                post_moderation_action="withhold",
                reason_code="spam",
                subject_explanation="The post was withheld after review.",
            )
        )
        == 0
    )
    capsys.readouterr()
    as_principal(app, human("appeal-state-author"))
    appealed = await client.post(
        f"/v1/moderation/cases/{case_id}/appeals",
        json={"rationale": "I request independent review."},
        headers={"Idempotency-Key": "appeal-state-appeal-0001"},
    )
    assert appealed.status_code == 201, appealed.text
    appeal_id = appealed.json()["id"]
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        assert post is not None
        post.status = "published"
        post.withheld_at = None
        await session.commit()

    for action in ("uphold", "overturn"):
        assert (
            await cli.review_post_appeal(
                Namespace(
                    appeal_id=appeal_id,
                    appeal_action=action,
                    subject_explanation="The appeal received independent review.",
                )
            )
            == 1
        )
        assert "appeal authority records are inconsistent" in capsys.readouterr().err
    async with app.state.session_factory() as session:
        case = await session.get(ModerationCase, case_id)
        appeal = await session.get(ModerationAppeal, appeal_id)
        assert case is not None and case.status == "appealed"
        assert appeal is not None and appeal.status == "submitted"
        assert appeal.review_snapshot_sha256 is None


async def test_profile_post_controls_are_exact_and_human_only(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "target", "target-profile")
    as_principal(app, human("viewer"))
    initial = await client.get("/v1/profile-post-controls/target-profile")
    assert initial.status_code == 200
    assert initial.json() == {
        "following": False,
        "content_blocked": False,
    }
    assert (
        await client.post(
            "/v1/follows/target-profile",
            headers={"Idempotency-Key": "casework-follow-0001"},
        )
    ).status_code == 200
    following = await client.get("/v1/profile-post-controls/target-profile")
    assert following.json() == {"following": True, "content_blocked": False}
    assert (
        await client.post(
            "/v1/content-blocks/target-profile",
            headers={"Idempotency-Key": "casework-block-0001"},
        )
    ).status_code == 204
    blocked = await client.get("/v1/profile-post-controls/target-profile")
    assert blocked.json() == {"following": False, "content_blocked": True}
    controls_operation = app.openapi()["paths"]["/v1/profile-post-controls/{profile_handle}"]["get"]
    assert controls_operation["security"] == [{"ClerkBearerAuth": []}]
    assert controls_operation["responses"]["200"]["content"]["application/json"]["schema"]
    as_principal(app, agent("viewer"))
    assert (await client.get("/v1/profile-post-controls/target-profile")).status_code == 403


async def test_terminal_dismissal_reopens_case_lineage_only_for_a_new_reporter(
    api_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    as_principal(app, human("author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "lineage-post-0001"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    as_principal(app, human("first-reporter"))
    first = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam"},
        headers={"Idempotency-Key": "lineage-report-0001"},
    )
    assert first.status_code == 201, first.text
    async with app.state.session_factory() as session:
        first_case = await session.scalar(
            select(ModerationCase).where(ModerationCase.post_id == post_id)
        )
        assert first_case is not None
    monkeypatch.setattr(cli, "get_settings", lambda: moderation_settings(app))
    assert (
        await cli.moderate_post(
            Namespace(
                case_id=first_case.id,
                post_id=post_id,
                post_moderation_action="dismiss",
                reason_code="spam",
                subject_explanation="No action is being taken.",
            )
        )
        == 0
    )
    async with app.state.session_factory() as session:
        reports_before_duplicate = (
            await session.scalars(select(PostReport).where(PostReport.post_id == post_id))
        ).all()
        assert [(row.reporter_owner_id, row.case_id) for row in reports_before_duplicate] == [
            ("first-reporter", first_case.id)
        ]
    # A fresh idempotency key for the same reporter is a safe receipt, not fresh casework.
    assert (await client.get("/v1/me")).json()["owner_id"] == public_owner_id("first-reporter")
    duplicate = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam"},
        headers={"Idempotency-Key": "lineage-report-0002"},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["id"] == first.json()["id"]
    as_principal(app, human("new-reporter"))
    later = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "harassment"},
        headers={"Idempotency-Key": "lineage-report-0003"},
    )
    assert later.status_code == 201, later.text
    async with app.state.session_factory() as session:
        cases = (
            await session.scalars(
                select(ModerationCase)
                .where(ModerationCase.post_id == post_id)
                .order_by(ModerationCase.created_at.asc(), ModerationCase.id.asc())
            )
        ).all()
        later_report = await session.get(PostReport, later.json()["id"])
        first_decision = await session.scalar(
            select(ModerationDecision).where(ModerationDecision.case_id == first_case.id)
        )
        assert len(cases) == 2
        initial_case = next(case for case in cases if case.id == first_case.id)
        later_case = next(case for case in cases if case.id != first_case.id)
        assert initial_case.status == "dismissed"
        assert later_case.status == "open"
        assert later_report is not None and later_report.case_id == later_case.id
        assert first_decision is not None and first_decision.action == "no_action"
    assert (
        await cli.moderate_post(
            Namespace(
                case_id=later_case.id,
                post_id=post_id,
                post_moderation_action="withhold",
                reason_code="harassment",
                subject_explanation="The later report was independently reviewed.",
            )
        )
        == 0
    )


async def test_appeal_overturn_allows_a_later_report_to_open_a_new_case(
    api_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    as_principal(app, human("author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "overturn-lineage-post-0001"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    as_principal(app, human("first-reporter"))
    report = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam"},
        headers={"Idempotency-Key": "overturn-lineage-report-0001"},
    )
    assert report.status_code == 201, report.text
    async with app.state.session_factory() as session:
        first_case = await session.scalar(
            select(ModerationCase).where(ModerationCase.post_id == post_id)
        )
        assert first_case is not None
    monkeypatch.setattr(cli, "get_settings", lambda: moderation_settings(app))
    assert (
        await cli.moderate_post(
            Namespace(
                case_id=first_case.id,
                post_id=post_id,
                post_moderation_action="withhold",
                reason_code="spam",
                subject_explanation="The post was withheld after review.",
            )
        )
        == 0
    )
    as_principal(app, human("author"))
    appeal = await client.post(
        f"/v1/moderation/cases/{first_case.id}/appeals",
        json={"rationale": "I request independent review."},
        headers={"Idempotency-Key": "overturn-lineage-appeal-0001"},
    )
    assert appeal.status_code == 201, appeal.text
    assert (
        await cli.review_post_appeal(
            Namespace(
                appeal_id=appeal.json()["id"],
                appeal_action="overturn",
                subject_explanation="The appeal was overturned.",
            )
        )
        == 0
    )
    as_principal(app, human("new-reporter"))
    later = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "privacy"},
        headers={"Idempotency-Key": "overturn-lineage-report-0002"},
    )
    assert later.status_code == 201, later.text
    async with app.state.session_factory() as session:
        cases = (
            await session.scalars(select(ModerationCase).where(ModerationCase.post_id == post_id))
        ).all()
        post = await session.get(Post, post_id)
        later_report = await session.get(PostReport, later.json()["id"])
        assert {case.status for case in cases} == {"appeal_overturned", "open"}
        assert later_report is not None and later_report.case_id != first_case.id
        assert post is not None and post.status == "published"


async def test_concurrent_new_reporters_share_one_open_case(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    as_principal(app, human("author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "concurrent-case-post-0001"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]

    async def from_header(request: Request) -> Principal:
        return human(request.headers["X-Test-Owner"])

    app.dependency_overrides[require_principal] = from_header
    app.dependency_overrides[optional_principal] = from_header
    first, second = await asyncio.gather(
        client.post(
            f"/v1/posts/{post_id}/report",
            json={"reason_code": "spam"},
            headers={"Idempotency-Key": "concurrent-case-report-a", "X-Test-Owner": "reporter-a"},
        ),
        client.post(
            f"/v1/posts/{post_id}/report",
            json={"reason_code": "harassment"},
            headers={"Idempotency-Key": "concurrent-case-report-b", "X-Test-Owner": "reporter-b"},
        ),
    )
    assert first.status_code == second.status_code == 201
    async with app.state.session_factory() as session:
        reports = (
            await session.scalars(select(PostReport).where(PostReport.post_id == post_id))
        ).all()
        cases = (
            await session.scalars(select(ModerationCase).where(ModerationCase.post_id == post_id))
        ).all()
        case_opened = (
            await session.scalars(
                select(ModerationAuditEvent).where(
                    ModerationAuditEvent.post_id == post_id,
                    ModerationAuditEvent.event_type == "case_opened",
                )
            )
        ).all()
        assert len(cases) == 1 and cases[0].status == "open"
        assert len(reports) == 2 and {report.case_id for report in reports} == {cases[0].id}
        assert len(case_opened) == 1


async def test_legacy_case_disposition_is_visible_but_not_appealable(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    as_principal(app, human("author"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "legacy-disposition-post-0001"},
    )
    assert created.status_code == 201, created.text
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        post = await session.get(Post, created.json()["id"])
        assert post is not None
        post.status = "withheld"
        post.withheld_at = now
        session.add(
            ModerationCase(
                id="legacy-withheld-case",
                post_id=post.id,
                subject_owner_id="author",
                status="legacy_withheld",
                created_at=now,
                updated_at=now,
                closed_at=now,
                retention_expires_at=now + timedelta(days=90),
            )
        )
        session.add(
            PostReport(
                id="legacy-withheld-report",
                post_id=post.id,
                case_id="legacy-withheld-case",
                reporter_owner_id="legacy-reporter",
                reason_code="spam",
                narrative="legacy private narrative",
                created_at=now,
            )
        )
        await session.commit()
    cases = await client.get("/v1/moderation/cases")
    assert cases.status_code == 200, cases.text
    row = next(item for item in cases.json()["cases"] if item["id"] == "legacy-withheld-case")
    assert row["status"] == "legacy_withheld"
    assert row["appeal_deadline"] is None and row["appeal"] is None
    attempt = await client.post(
        "/v1/moderation/cases/legacy-withheld-case/appeals",
        json={"rationale": "This must not fabricate an appealable decision."},
        headers={"Idempotency-Key": "legacy-disposition-appeal-0001"},
    )
    assert attempt.status_code == 409
    assert attempt.json()["detail"] == "moderation case authority is inconsistent"
