from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import cli
from app.config import Settings
from app.db import build_engine, build_session_factory
from app.markdown import prepare_client_document
from app.models import (
    Base,
    ChangeEvent,
    Document,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    Post,
    PostReport,
)
from app.services.storage import VersionStore

_POST_MARKDOWN = """---
schema: connect.md/post
schema_version: 1
title: Moderation CLI evidence
topics: [reliability]
visibility: public
---
# Moderation CLI evidence

Canonical evidence.
"""


async def test_post_moderation_fails_closed_without_preprovisioned_authority(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    result = await cli.moderate_post(
        Namespace(
            case_id="missing-case",
            post_id="missing-post",
            post_moderation_action="withhold",
            reason_code="spam",
            subject_explanation="A bounded explanation",
            internal_rationale=None,
            evidence=None,
        )
    )
    assert result == 2
    assert "not pre-provisioned" in capsys.readouterr().err


def test_post_moderation_cli_rejects_forged_actor_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "app.cli",
            "post-moderation",
            "decide",
            "withhold",
            "--case-id",
            "case-id",
            "--post-id",
            "post-id",
            "--moderator",
            "forged",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.parse_args()
    assert exc.value.code == 2


def test_post_moderation_cli_rejects_private_content_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "app.cli",
            "post-moderation",
            "decide",
            "withhold",
            "--case-id",
            "case-id",
            "--post-id",
            "post-id",
            "--reason-code",
            "spam",
            "--subject-explanation",
            "A bounded explanation",
            "--internal-rationale",
            "must-not-accept-private-content",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.parse_args()
    assert exc.value.code == 2


async def test_private_operator_output_fails_closed_without_explicit_enablement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(
        post_moderator_id="moderator:preprovisioned",
        post_moderator_role="content_moderator",
        appeal_reviewer_id="appeals:preprovisioned",
        appeal_reviewer_role="appeal_reviewer",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert await cli.inspect_post_moderation_case(Namespace(case_id="case-id")) == 2
    assert "operator output is disabled" in capsys.readouterr().err


async def test_post_moderation_records_only_the_configured_authority(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'moderation.db').as_posix()}",
        storage_path=tmp_path / "storage",
        post_moderator_id="moderator:preprovisioned",
        post_moderator_role="content_moderator",
        appeal_reviewer_id="appeals:preprovisioned",
        appeal_reviewer_role="appeal_reviewer",
        post_moderation_operator_output_enabled=True,
    )
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    now = datetime.now(UTC)
    post_id = "00000000-0000-4000-8000-000000000001"
    storage_path = f"posts/{post_id}/versions/000001.md"
    canonical, _ = prepare_client_document(
        "post",
        _POST_MARKDOWN,
        document_id=post_id,
        owner_id="",
        version=1,
        updated_at=now,
        published_at=now,
        author_profile_handle="author-profile",
    )
    digest = VersionStore(settings.storage_path).write_immutable(storage_path, canonical)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(
                Document(
                    id="profile-id",
                    kind="profile",
                    owner_id="author",
                    public_identifier="author-profile",
                    visibility="public",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                Post(
                    id=post_id,
                    owner_id="author",
                    author_profile_document_id="profile-id",
                    author_profile_handle="author-profile",
                    status="published",
                    current_version=1,
                    sha256=digest,
                    storage_path=storage_path,
                    published_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                ModerationCase(
                    id="case-id",
                    post_id=post_id,
                    subject_owner_id="author",
                    status="open",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        result = await cli.moderate_post(
            Namespace(
                case_id="case-id",
                post_id=post_id,
                post_moderation_action="withhold",
                reason_code="spam",
                subject_explanation="A bounded explanation",
            )
        )
        assert result == 0
        async with session_factory() as session:
            decision = await session.scalar(select(ModerationDecision))
            event = await session.scalar(select(ModerationAuditEvent))
            post = await session.get(Post, post_id)
            case = await session.get(ModerationCase, "case-id")
            change = await session.scalar(select(ChangeEvent))
            assert event is not None
            assert event.actor_id == "moderator:preprovisioned"
            assert event.actor_role == "content_moderator"
            assert event.event_type == "decision_withheld"
            assert event.safe_metadata == '{"actor_method":"internal_cli"}'
            assert decision is not None and decision.action == "withhold"
            assert decision.moderator_id == "moderator:preprovisioned"
            assert decision.evidence_snapshot_sha256 is not None
            assert len(decision.evidence_snapshot_sha256) == 64
            assert case is not None and case.status == "withheld"
            assert post is not None and post.status == "withheld"
            assert change is not None
            assert change.actor_id == "system:post-moderation"
            assert change.actor_method == "system"
            assert "moderator:preprovisioned" not in str(change.__dict__)
    finally:
        await engine.dispose()


async def test_post_moderation_rejects_a_configured_subject_as_moderator(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'self-moderation.db').as_posix()}",
        storage_path=tmp_path / "storage",
        post_moderator_id="author",
        post_moderator_role="content_moderator",
        appeal_reviewer_id="appeals:preprovisioned",
        appeal_reviewer_role="appeal_reviewer",
        post_moderation_operator_output_enabled=True,
    )
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add_all(
                [
                    Document(
                        id="profile-id",
                        kind="profile",
                        owner_id="author",
                        public_identifier="author-profile",
                        visibility="public",
                        current_version=1,
                        created_at=now,
                        updated_at=now,
                    ),
                    Post(
                        id="post-id",
                        owner_id="author",
                        author_profile_document_id="profile-id",
                        author_profile_handle="author-profile",
                        status="published",
                        current_version=1,
                        sha256="0" * 64,
                        storage_path="posts/post-id/versions/000001.md",
                        published_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                    ModerationCase(
                        id="case-id",
                        post_id="post-id",
                        subject_owner_id="author",
                        status="open",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await session.commit()
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        result = await cli.moderate_post(
            Namespace(
                case_id="case-id",
                post_id="post-id",
                post_moderation_action="withhold",
                reason_code="spam",
                subject_explanation="A bounded explanation",
            )
        )
        assert result == 1
        async with session_factory() as session:
            post = await session.get(Post, "post-id")
            assert post is not None and post.status == "published"
            assert await session.scalar(select(ModerationDecision)) is None
    finally:
        await engine.dispose()


async def test_post_moderation_inspection_omits_reporter_identity(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'inspect.db').as_posix()}",
        storage_path=tmp_path / "storage",
        post_moderator_id="moderator:preprovisioned",
        post_moderator_role="content_moderator",
        appeal_reviewer_id="appeals:preprovisioned",
        appeal_reviewer_role="appeal_reviewer",
        post_moderation_operator_output_enabled=True,
    )
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add_all(
                [
                    Document(
                        id="profile-id",
                        kind="profile",
                        owner_id="author",
                        public_identifier="author-profile",
                        visibility="public",
                        current_version=1,
                        created_at=now,
                        updated_at=now,
                    ),
                    Post(
                        id="post-id",
                        owner_id="author",
                        author_profile_document_id="profile-id",
                        author_profile_handle="author-profile",
                        status="published",
                        current_version=1,
                        sha256="0" * 64,
                        storage_path="posts/post-id/versions/000001.md",
                        published_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                    ModerationCase(
                        id="case-id",
                        post_id="post-id",
                        subject_owner_id="author",
                        status="open",
                        created_at=now,
                        updated_at=now,
                    ),
                    PostReport(
                        id="report-id",
                        post_id="post-id",
                        case_id="case-id",
                        reporter_owner_id="reporter-private-subject",
                        reason_code="spam",
                        narrative="private report narrative",
                        created_at=now,
                    ),
                ]
            )
            await session.commit()
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        assert await cli.inspect_post_moderation_case(Namespace(case_id="case-id")) == 0
        output = capsys.readouterr().out
        assert "private report narrative" in output
        assert "reporter-private-subject" not in output
        assert '"classification": "private_operator_only"' in output
    finally:
        await engine.dispose()
