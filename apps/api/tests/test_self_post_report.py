from __future__ import annotations

from sqlalchemy import func, select

from app.auth import Principal, optional_principal, require_principal
from app.models import IdempotencyRecord, ModerationCase, PostReport, PostReportRateBucket

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


def post_markdown() -> str:
    return """---
schema: connect.md/post
schema_version: 1
title: A note I must not report
topics: [engineering, reliability]
visibility: public
---
# A note I must not report

This post belongs to its author.
"""


async def test_author_cannot_report_their_own_post(api_client) -> None:
    app, client = api_client
    as_principal(app, human("self-report-author"))
    profile = await client.post(
        "/v1/profiles",
        json={
            "markdown": profile_markdown(visibility="public").replace(
                "ada-lovelace", "self-report-author"
            )
        },
        headers={"Idempotency-Key": "self-report-profile"},
    )
    assert profile.status_code == 201, profile.text
    created = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown()},
        headers={"Idempotency-Key": "self-report-post"},
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]

    report = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam", "narrative": "Reporting my own post must not open a case."},
        headers={"Idempotency-Key": "self-report-own"},
    )
    assert report.status_code == 409, report.text
    assert report.json()["detail"] == "cannot report your own post"

    as_principal(app, human("self-report-reader"))
    reader_profile = await client.post(
        "/v1/profiles",
        json={
            "markdown": profile_markdown(visibility="public").replace(
                "ada-lovelace", "self-report-reader"
            )
        },
        headers={"Idempotency-Key": "self-report-reader-profile"},
    )
    assert reader_profile.status_code == 201, reader_profile.text
    reader_report = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam", "narrative": "A different person may report this post."},
        headers={"Idempotency-Key": "self-report-reader"},
    )
    assert reader_report.status_code == 201, reader_report.text

    async with app.state.session_factory() as session:
        reports = (await session.scalars(select(PostReport))).all()
        cases = await session.scalar(select(func.count()).select_from(ModerationCase))
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "self-report-own"
                )
            )
        ).all()
        quota = (
            await session.scalars(
                select(PostReportRateBucket).where(
                    PostReportRateBucket.owner_id == "self-report-author"
                )
            )
        ).all()
    assert [row.reporter_owner_id for row in reports] == ["self-report-reader"]
    assert cases == 1
    assert receipts == []
    assert quota == []
