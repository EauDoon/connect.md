from __future__ import annotations

import asyncio
import json
from base64 import urlsafe_b64encode
from datetime import UTC, datetime

from sqlalchemy import select

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    IdempotencyRecord,
    ModerationAppeal,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    Post,
    PostReport,
    new_id,
)

from .test_post_moderation_casework import as_principal, create_profile, human, post_markdown

MODERATOR = "moderator:configured"
APPEAL_REVIEWER = "appeals:configured"


def configured(app):
    app.state.settings = app.state.settings.model_copy(
        update={
            "post_moderator_id": MODERATOR,
            "post_moderator_role": "content_moderator",
            "appeal_reviewer_id": APPEAL_REVIEWER,
            "appeal_reviewer_role": "appeal_reviewer",
        }
    )


def staff(subject: str, *, impersonated: bool = False, method: str = "clerk_jwt") -> Principal:
    return Principal(
        subject=subject,
        method=method,
        scopes=frozenset({"*"}),
        is_impersonated=impersonated,
    )


async def seed_open_case(api_client) -> tuple[object, object, str, str]:
    app, client = api_client
    await create_profile(client, app, "subject-private", "author-profile")
    await create_profile(client, app, "reporter-private", "reader-profile")
    as_principal(app, human("subject-private"))
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "review-http-post-create"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    as_principal(app, human("reporter-private"))
    report = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam", "narrative": "private report narrative"},
        headers={"Idempotency-Key": "review-http-report-create"},
    )
    assert report.status_code == 201, report.text
    async with app.state.session_factory() as session:
        case = await session.scalar(select(ModerationCase).where(ModerationCase.post_id == post_id))
        assert case is not None
        return app, client, post_id, case.id


async def case_detail(client, case_id: str):
    response = await client.get(f"/v1/internal/post-moderation/cases/{case_id}")
    assert response.status_code == 200, response.text
    return response


async def decide_case(client, case_id: str, etag: str, key: str, *, action: str = "withhold"):
    return await client.post(
        f"/v1/internal/post-moderation/cases/{case_id}/decision",
        json={
            "action": action,
            "reason_code": "spam",
            "subject_explanation": "A bounded reviewer explanation.",
        },
        headers={"If-Match": etag, "Idempotency-Key": key},
    )


async def test_hidden_moderation_review_authority_privacy_and_discovery(api_client) -> None:
    app, client, _post_id, case_id = await seed_open_case(api_client)
    configured(app)
    path = "/v1/internal/post-moderation/cases"

    as_principal(app, staff("wrong-staff"))
    wrong_staff = await client.get(path)
    as_principal(app, staff(MODERATOR, method="agent_api_key"))
    agent_key = await client.get(path)
    as_principal(app, staff(MODERATOR, impersonated=True))
    impersonated = await client.get(path)
    assert {response.status_code for response in (wrong_staff, agent_key, impersonated)} == {403}
    assert {response.json()["detail"] for response in (wrong_staff, agent_key, impersonated)} == {
        "moderation review access is forbidden"
    }

    app.dependency_overrides.pop(require_principal)
    app.dependency_overrides.pop(optional_principal)
    anonymous = await client.get(path)
    assert anonymous.status_code == 401
    as_principal(app, staff(MODERATOR))

    queue = await client.get(path)
    assert queue.status_code == 200, queue.text
    assert queue.headers["cache-control"] == "no-store"
    assert set(queue.json()) == {"cases", "next_cursor"}
    summary = queue.json()["cases"][0]
    assert set(summary) == {
        "id",
        "post_id",
        "status",
        "author_profile_handle",
        "title",
        "report_count",
        "reason_codes",
        "created_at",
        "updated_at",
    }
    assert summary["id"] == case_id
    assert summary["report_count"] == 1
    assert summary["reason_codes"] == ["spam"]

    detail = await client.get(f"/v1/internal/post-moderation/cases/{case_id}")
    payload = detail.json()
    assert detail.headers["cache-control"] == "no-store"
    assert detail.headers["etag"] == payload["etag"]
    assert set(payload) == {"case", "post", "reports", "etag"}
    assert set(payload["post"]) == {
        "id",
        "author_profile_handle",
        "title",
        "topics",
        "version",
        "published_at",
        "status",
        "markdown",
    }
    assert set(payload["reports"][0]) == {"id", "reason_code", "narrative", "created_at"}
    assert payload["reports"][0]["narrative"] == "private report narrative"
    for forbidden in (
        "subject-private",
        "reporter-private",
        MODERATOR,
        APPEAL_REVIEWER,
        "storage_path",
    ):
        assert forbidden not in detail.text

    schema = app.openapi()
    assert all("/v1/internal/post-moderation" not in route for route in schema["paths"])
    llms = await client.get("/llms.txt")
    capabilities = await client.get("/v1/capabilities")
    agent_card = await client.get("/.well-known/agent-card.json")
    assert all(
        "/v1/internal/post-moderation" not in response.text
        for response in (llms, capabilities, agent_card)
    )


async def test_case_review_preconditions_idempotency_and_replay(api_client) -> None:
    app, client, _post_id, case_id = await seed_open_case(api_client)
    configured(app)
    as_principal(app, staff(MODERATOR))
    detail = await case_detail(client, case_id)
    etag = detail.headers["etag"]
    path = f"/v1/internal/post-moderation/cases/{case_id}/decision"
    body = {
        "action": "withhold",
        "reason_code": "spam",
        "subject_explanation": "A bounded reviewer explanation.",
    }

    missing_if_match = await client.post(
        f"/v1/internal/post-moderation/cases/{case_id}/decision",
        json=body,
    )
    assert missing_if_match.status_code == 428
    assert missing_if_match.json()["detail"]
    for malformed in (f"W/{etag}", "*", f"{etag}, {etag}", '"sha256-' + "0" * 64 + '"'):
        response = await client.post(
            path,
            json=body,
            headers={"If-Match": malformed, "Idempotency-Key": "review-precondition-key"},
        )
        assert response.status_code == 412, response.text
    extra = await client.post(
        path,
        json={**body, "unexpected": True},
        headers={"If-Match": etag, "Idempotency-Key": "review-extra-key"},
    )
    assert extra.status_code == 422

    async with app.state.session_factory() as session:
        report = await session.scalar(select(PostReport).where(PostReport.case_id == case_id))
        assert report is not None
        report.narrative = "The evidence changed after the reviewer loaded it."
        await session.commit()
    drifted = await decide_case(client, case_id, etag, "review-etag-drift")
    assert drifted.status_code == 412
    etag = (await case_detail(client, case_id)).headers["etag"]

    first = await decide_case(client, case_id, etag, "review-decision-key")
    assert first.status_code == 204 and first.content == b""
    assert first.headers["cache-control"] == "no-store"
    assert "idempotency-replayed" not in first.headers
    replay = await decide_case(client, case_id, etag, "review-decision-key")
    assert replay.status_code == 204 and replay.content == b""
    assert replay.headers["idempotency-replayed"] == "true"
    collision = await client.post(
        path,
        json={**body, "subject_explanation": "A different explanation."},
        headers={"If-Match": etag, "Idempotency-Key": "review-decision-key"},
    )
    assert collision.status_code == 409
    terminal = await decide_case(client, case_id, etag, "review-terminal-key")
    assert terminal.status_code == 409

    async with app.state.session_factory() as session:
        decision = await session.scalar(
            select(ModerationDecision).where(ModerationDecision.case_id == case_id)
        )
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "review-decision-key"
            )
        )
        assert decision is not None and decision.action == "withhold"
        assert receipt is not None
        assert receipt.resource_type == "moderation_decision"
        assert receipt.resource_id is not None
        assert receipt.resource_id.startswith("moderation_decision:v2:")
        assert receipt.response_status == 204
        assert receipt.response_body == ""
        assert receipt.response_headers == "{}"


async def test_completed_post_create_receipt_replays_byte_identically_after_withhold(
    api_client,
) -> None:
    app, client, post_id, case_id = await seed_open_case(api_client)
    create_key = "review-http-post-create"
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == "subject-private",
                IdempotencyRecord.idempotency_key == create_key,
            )
        )
        assert receipt is not None and receipt.response_status == 201
        assert receipt.response_body
        completed_body = receipt.response_body.encode("utf-8")

    configured(app)
    as_principal(app, staff(MODERATOR))
    detail = await case_detail(client, case_id)
    withheld = await decide_case(client, case_id, detail.headers["etag"], "replay-withhold-key")
    assert withheld.status_code == 204, withheld.text
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        assert post is not None and post.status == "withheld"

    as_principal(app, human("subject-private"))
    replay = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": create_key},
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.content == completed_body
    assert "private report narrative" not in replay.text


async def test_emptied_post_create_receipt_after_withhold_fails_closed_without_private_leakage(
    api_client,
) -> None:
    app, client, post_id, case_id = await seed_open_case(api_client)
    create_key = "review-http-post-create"
    configured(app)
    as_principal(app, staff(MODERATOR))
    detail = await case_detail(client, case_id)
    withheld = await decide_case(
        client, case_id, detail.headers["etag"], "empty-receipt-withhold-key"
    )
    assert withheld.status_code == 204, withheld.text
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == "subject-private",
                IdempotencyRecord.idempotency_key == create_key,
            )
        )
        assert post is not None and post.status == "withheld"
        assert receipt is not None and receipt.response_body
        receipt.response_body = ""
        await session.commit()

    as_principal(app, human("subject-private"))
    replay = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": create_key},
    )
    assert replay.status_code == 503
    assert replay.json()["detail"] == "idempotent post publication receipt cannot be reconstructed"
    assert "idempotency-replayed" not in replay.headers
    for private_value in (
        "private report narrative",
        "subject-private",
        "reporter-private",
        MODERATOR,
    ):
        assert private_value not in replay.text


async def test_case_review_concurrent_same_key_replays_after_bundle_lock(api_client) -> None:
    """SQLite gather is useful coverage, but PostgreSQL provides the production row locks."""

    app, client, _post_id, case_id = await seed_open_case(api_client)
    configured(app)
    as_principal(app, staff(MODERATOR))
    etag = (await case_detail(client, case_id)).headers["etag"]

    first, second = await asyncio.gather(
        decide_case(client, case_id, etag, "review-concurrent-same-key"),
        decide_case(client, case_id, etag, "review-concurrent-same-key"),
    )
    responses = (first, second)
    assert [response.status_code for response in responses] == [204, 204], [
        response.text for response in responses
    ]
    assert all(response.content == b"" for response in responses)
    assert (
        sum(response.headers.get("idempotency-replayed") == "true" for response in responses) == 1
    )

    async with app.state.session_factory() as session:
        decisions = (
            await session.scalars(
                select(ModerationDecision).where(ModerationDecision.case_id == case_id)
            )
        ).all()
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "review-concurrent-same-key"
                )
            )
        ).all()
        audit_events = (
            await session.scalars(
                select(ModerationAuditEvent).where(
                    ModerationAuditEvent.case_id == case_id,
                    ModerationAuditEvent.event_type == "decision_withheld",
                )
            )
        ).all()
        assert len(decisions) == len(receipts) == len(audit_events) == 1


async def test_case_review_replay_fails_closed_on_current_fact_or_storage_tamper(
    api_client,
) -> None:
    app, client, post_id, case_id = await seed_open_case(api_client)
    configured(app)
    as_principal(app, staff(MODERATOR))
    etag = (await case_detail(client, case_id)).headers["etag"]
    key = "review-current-fact-tamper"
    first = await decide_case(client, case_id, etag, key)
    assert first.status_code == 204

    async def assert_unavailable() -> None:
        replay = await decide_case(client, case_id, etag, key)
        assert replay.status_code == 503
        assert (
            replay.json()["detail"]
            == "idempotent moderation decision receipt cannot be reconstructed"
        )
        assert "private report narrative" not in replay.text

    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        assert post is not None and post.status == "withheld"
        post.status = "withdrawn"
        await session.commit()
    await assert_unavailable()
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        assert post is not None
        post.status = "withheld"
        await session.commit()

    async with app.state.session_factory() as session:
        report = await session.scalar(select(PostReport).where(PostReport.case_id == case_id))
        assert report is not None
        report.narrative = "receipt digest tamper"
        await session.commit()
    await assert_unavailable()
    async with app.state.session_factory() as session:
        report = await session.scalar(select(PostReport).where(PostReport.case_id == case_id))
        assert report is not None
        report.narrative = "private report narrative"
        await session.commit()

    async with app.state.session_factory() as session:
        decision = await session.scalar(
            select(ModerationDecision).where(ModerationDecision.case_id == case_id)
        )
        assert decision is not None
        decision.subject_explanation = "receipt decision tamper"
        await session.commit()
    await assert_unavailable()
    async with app.state.session_factory() as session:
        decision = await session.scalar(
            select(ModerationDecision).where(ModerationDecision.case_id == case_id)
        )
        assert decision is not None
        decision.subject_explanation = "A bounded reviewer explanation."
        await session.commit()

    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        assert post is not None
        original_sha256 = post.sha256
        post.sha256 = "0" * 64
        await session.commit()
    await assert_unavailable()
    async with app.state.session_factory() as session:
        post = await session.get(Post, post_id)
        assert post is not None
        post.sha256 = original_sha256
        await session.commit()

    restored = await decide_case(client, case_id, etag, key)
    assert restored.status_code == 204
    assert restored.headers["idempotency-replayed"] == "true"


async def test_appeal_review_queue_detail_and_replay(api_client) -> None:
    app, client, _post_id, case_id = await seed_open_case(api_client)
    configured(app)
    as_principal(app, staff(MODERATOR))
    detail = await case_detail(client, case_id)
    decided = await decide_case(client, case_id, detail.headers["etag"], "appeal-seed-decision")
    assert decided.status_code == 204

    as_principal(app, human("subject-private"))
    appeal = await client.post(
        f"/v1/moderation/cases/{case_id}/appeals",
        json={"rationale": "Please independently review this decision."},
        headers={"Idempotency-Key": "appeal-seed-create"},
    )
    assert appeal.status_code == 201, appeal.text
    appeal_id = appeal.json()["id"]

    as_principal(app, staff(APPEAL_REVIEWER))
    queue = await client.get("/v1/internal/post-moderation/appeals")
    assert queue.status_code == 200, queue.text
    assert set(queue.json()) == {"appeals", "next_cursor"}
    assert queue.json()["appeals"][0]["id"] == appeal_id
    assert set(queue.json()["appeals"][0]) == {
        "id",
        "case_id",
        "post_id",
        "status",
        "author_profile_handle",
        "title",
        "submitted_at",
    }
    detail = await client.get(f"/v1/internal/post-moderation/appeals/{appeal_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert detail.headers["etag"] == payload["etag"]
    assert set(payload) == {"appeal", "post", "reports", "decision", "etag"}
    assert set(payload["appeal"]) == {
        "id",
        "case_id",
        "post_id",
        "status",
        "rationale",
        "submitted_at",
    }
    assert set(payload["decision"]) == {
        "action",
        "reason_code",
        "subject_explanation",
        "decided_at",
    }
    assert MODERATOR not in detail.text and APPEAL_REVIEWER not in detail.text

    path = f"/v1/internal/post-moderation/appeals/{appeal_id}/decision"
    headers = {"If-Match": detail.headers["etag"], "Idempotency-Key": "appeal-review-key"}
    body = {"action": "overturn", "subject_explanation": "The appeal was independently upheld."}
    # SQLite does not prove PostgreSQL row-lock scheduling, but this exercises the same-key loser.
    first, second = await asyncio.gather(
        client.post(path, json=body, headers=headers),
        client.post(path, json=body, headers=headers),
    )
    responses = (first, second)
    assert [response.status_code for response in responses] == [204, 204], [
        response.text for response in responses
    ]
    assert all(response.content == b"" for response in responses)
    assert (
        sum(response.headers.get("idempotency-replayed") == "true" for response in responses) == 1
    )

    async with app.state.session_factory() as session:
        appeal_row = await session.get(ModerationAppeal, appeal_id)
        post = await session.scalar(select(Post).where(Post.id == payload["post"]["id"]))
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "appeal-review-key"
            )
        )
        assert appeal_row is not None and appeal_row.status == "overturned"
        assert post is not None and post.status == "published"
        assert receipt is not None and receipt.resource_type == "moderation_appeal_review"
        assert receipt.resource_id is not None
        assert receipt.resource_id.startswith("moderation_appeal_review:v2:")

    async with app.state.session_factory() as session:
        appeal_row = await session.get(ModerationAppeal, appeal_id)
        assert appeal_row is not None
        appeal_row.subject_explanation = "appeal receipt tamper"
        await session.commit()
    tampered_appeal = await client.post(path, json=body, headers=headers)
    assert tampered_appeal.status_code == 503
    assert (
        tampered_appeal.json()["detail"]
        == "idempotent moderation decision receipt cannot be reconstructed"
    )
    async with app.state.session_factory() as session:
        appeal_row = await session.get(ModerationAppeal, appeal_id)
        assert appeal_row is not None
        appeal_row.subject_explanation = "The appeal was independently upheld."
        await session.commit()

    async with app.state.session_factory() as session:
        post = await session.get(Post, payload["post"]["id"])
        assert post is not None
        post.status = "withdrawn"
        await session.commit()
    tampered_post = await client.post(path, json=body, headers=headers)
    assert tampered_post.status_code == 503
    async with app.state.session_factory() as session:
        post = await session.get(Post, payload["post"]["id"])
        assert post is not None
        post.status = "published"
        await session.commit()

    restored = await client.post(path, json=body, headers=headers)
    assert restored.status_code == 204
    assert restored.headers["idempotency-replayed"] == "true"


async def test_report_cap_and_review_bundle_fail_closed_at_one_thousand_one(api_client) -> None:
    app, client, post_id, case_id = await seed_open_case(api_client)
    configured(app)
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        for index in range(999):
            session.add(
                PostReport(
                    id=new_id(),
                    post_id=post_id,
                    case_id=case_id,
                    reporter_owner_id=f"cap-reporter-{index}",
                    reason_code="spam",
                    narrative=None,
                    created_at=now,
                )
            )
        await session.commit()

    as_principal(app, human("cap-reporter-next"))
    capped = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam"},
        headers={"Idempotency-Key": "report-cap-bound"},
    )
    assert capped.status_code == 503

    async with app.state.session_factory() as session:
        session.add(
            PostReport(
                id=new_id(),
                post_id=post_id,
                case_id=case_id,
                reporter_owner_id="cap-reporter-over",
                reason_code="spam",
                narrative=None,
                created_at=now,
            )
        )
        await session.commit()
    as_principal(app, staff(MODERATOR))
    queue = await client.get("/v1/internal/post-moderation/cases")
    detail = await client.get(f"/v1/internal/post-moderation/cases/{case_id}")
    assert queue.status_code == 503
    assert detail.status_code == 503


async def test_reviewer_independence_cursor_bounds_and_blank_narrative(api_client) -> None:
    app, client, _post_id, case_id = await seed_open_case(api_client)
    configured(app)
    as_principal(app, staff(MODERATOR))
    path = "/v1/internal/post-moderation/cases"
    malformed = await client.get(f"{path}?cursor=not-a-moderation-cursor")
    wrong_scope_cursor = (
        urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "scope": "moderation_review_appeals",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "id": case_id,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    wrong_scope = await client.get(f"{path}?cursor={wrong_scope_cursor}")
    assert malformed.status_code == wrong_scope.status_code == 400
    assert (await client.get(f"{path}?limit=0")).status_code == 422
    assert (await client.get(f"{path}?limit=51")).status_code == 422
    assert (await client.get(f"{path}?cursor={'a' * 501}")).status_code == 422

    async with app.state.session_factory() as session:
        report = await session.scalar(select(PostReport).where(PostReport.case_id == case_id))
        assert report is not None
        report.narrative = " \t "
        await session.commit()
    blank = await case_detail(client, case_id)
    assert blank.json()["reports"][0]["narrative"] is None

    app.state.settings = app.state.settings.model_copy(
        update={"post_moderator_id": "subject-private", "appeal_reviewer_id": APPEAL_REVIEWER}
    )
    as_principal(app, staff("subject-private"))
    excluded_queue = await client.get(path)
    excluded_detail = await client.get(f"{path}/{case_id}")
    assert excluded_queue.status_code == 200 and excluded_queue.json()["cases"] == []
    assert excluded_detail.status_code == 403
