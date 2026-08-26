"""Transactional post-moderation operator authority commands."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections.abc import Callable

from sqlalchemy import select

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.models import ModerationAppeal, ModerationCase, ModerationDecision, PostReport
from app.services.database_roles import API_DATABASE_ROLE, require_database_role
from app.services.post_moderation import (
    PostModerationConfigurationError,
    PostModerationError,
    configured_moderation_authorities,
    decide_case,
    review_appeal,
)
from app.services.storage import VersionStore

SettingsFactory = Callable[[], Settings]


async def moderate_post(settings_factory: SettingsFactory, args: Namespace) -> int:
    """Record one case-linked initial decision under the configured moderator authority."""
    settings = settings_factory()
    try:
        configured_moderation_authorities(settings)
    except PostModerationConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not 1 <= len(args.subject_explanation) <= 500:
        print("subject explanation must be 1-500 characters", file=sys.stderr)
        return 2
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            try:
                await decide_case(
                    session,
                    VersionStore(settings.storage_path),
                    settings,
                    case_id=args.case_id,
                    expected_post_id=args.post_id,
                    action=args.post_moderation_action,
                    reason_code=args.reason_code,
                    subject_explanation=args.subject_explanation,
                    actor_method="internal_cli",
                    expected_snapshot_sha256=None,
                )
            except PostModerationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            await session.commit()
    finally:
        await engine.dispose()
    verb = "withheld" if args.post_moderation_action == "withhold" else "dismissed"
    print(f"moderation case {args.case_id} {verb}")
    return 0


async def review_post_appeal(settings_factory: SettingsFactory, args: Namespace) -> int:
    """Resolve one appeal using only the configured, independent appeal reviewer."""
    settings = settings_factory()
    try:
        configured_moderation_authorities(settings)
    except PostModerationConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not 1 <= len(args.subject_explanation) <= 500:
        print("subject explanation must be 1-500 characters", file=sys.stderr)
        return 2
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            try:
                await review_appeal(
                    session,
                    VersionStore(settings.storage_path),
                    settings,
                    appeal_id=args.appeal_id,
                    action=args.appeal_action,
                    subject_explanation=args.subject_explanation,
                    actor_method="internal_cli",
                    expected_snapshot_sha256=None,
                )
            except PostModerationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            await session.commit()
    finally:
        await engine.dispose()
    verb = "overturned" if args.appeal_action == "overturn" else "upheld"
    print(f"moderation appeal {args.appeal_id} {verb}")
    return 0


async def list_post_moderation_cases(settings_factory: SettingsFactory, args: Namespace) -> int:
    """Bounded operator-only case discovery with no reporter data or identities."""
    settings = settings_factory()
    if (
        not settings.post_moderator_id
        or settings.post_moderator_role is None
        or not settings.appeal_reviewer_id
        or settings.appeal_reviewer_role is None
        or settings.post_moderator_id == settings.appeal_reviewer_id
    ):
        print("independent post moderation authorities are not pre-provisioned", file=sys.stderr)
        return 2
    if not settings.post_moderation_operator_output_enabled:
        print("private post moderation operator output is disabled", file=sys.stderr)
        return 2
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            rows = (
                await session.scalars(
                    select(ModerationCase)
                    .order_by(ModerationCase.updated_at.desc(), ModerationCase.id.desc())
                    .limit(args.limit)
                )
            ).all()
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "classification": "private_operator_only",
                "cases": [
                    {
                        "case_id": row.id,
                        "post_id": row.post_id,
                        "status": row.status,
                        "created_at": row.created_at.isoformat(),
                        "updated_at": row.updated_at.isoformat(),
                    }
                    for row in rows
                ],
            },
            sort_keys=True,
        )
    )
    return 0


async def inspect_post_moderation_case(settings_factory: SettingsFactory, args: Namespace) -> int:
    """Operator-only minimal evidence view; reporter identities are intentionally absent."""
    settings = settings_factory()
    if (
        not settings.post_moderator_id
        or settings.post_moderator_role is None
        or not settings.appeal_reviewer_id
        or settings.appeal_reviewer_role is None
        or settings.post_moderator_id == settings.appeal_reviewer_id
    ):
        print("independent post moderation authorities are not pre-provisioned", file=sys.stderr)
        return 2
    if not settings.post_moderation_operator_output_enabled:
        print("private post moderation operator output is disabled", file=sys.stderr)
        return 2
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            case = await session.get(ModerationCase, args.case_id)
            if case is None:
                print("moderation case was not found", file=sys.stderr)
                return 1
            reports = (
                await session.scalars(
                    select(PostReport)
                    .where(PostReport.case_id == case.id)
                    .order_by(PostReport.created_at.asc(), PostReport.id.asc())
                    .limit(100)
                )
            ).all()
            decision = await session.scalar(
                select(ModerationDecision).where(ModerationDecision.case_id == case.id)
            )
            appeal = await session.scalar(
                select(ModerationAppeal).where(ModerationAppeal.case_id == case.id)
            )
    finally:
        await engine.dispose()
    payload: dict[str, object] = {
        "classification": "private_operator_only",
        "case": {
            "case_id": case.id,
            "post_id": case.post_id,
            "status": case.status,
            "created_at": case.created_at.isoformat(),
            "updated_at": case.updated_at.isoformat(),
        },
        "reports": [
            {
                "reason_code": report.reason_code,
                "narrative": report.narrative,
                "created_at": report.created_at.isoformat(),
            }
            for report in reports
        ],
        "decision": None
        if decision is None
        else {
            "action": decision.action,
            "reason_code": decision.reason_code,
            "subject_explanation": decision.subject_explanation,
            "internal_rationale": decision.internal_rationale,
            "evidence": decision.evidence,
            "decided_at": decision.decided_at.isoformat(),
        },
        "appeal": None
        if appeal is None
        else {
            "status": appeal.status,
            "rationale": appeal.rationale,
            "subject_explanation": appeal.subject_explanation,
            "internal_rationale": appeal.internal_rationale,
            "submitted_at": appeal.submitted_at.isoformat(),
            "reviewed_at": None if appeal.reviewed_at is None else appeal.reviewed_at.isoformat(),
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0
