"""Shared, evidence-bound post-moderation transitions.

The service owns transition semantics and lock ordering but never commits. HTTP
and operational callers retain transaction and idempotency ownership.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.markdown import MarkdownValidationError, validate_canonical
from app.models import (
    ChangeEvent,
    ModerationAppeal,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    Post,
    PostReport,
    new_id,
)
from app.services.storage import StorageIntegrityError, VersionStore

ModerationAction = Literal["dismiss", "withhold"]
AppealAction = Literal["uphold", "overturn"]

_REASON_CODES = frozenset(
    {"spam", "harassment", "misinformation", "privacy", "illegal_content", "other"}
)
_SYSTEM_CHANGE_ACTOR = "system:post-moderation"


class PostModerationError(RuntimeError):
    """A sanitized, caller-displayable moderation failure."""


class PostModerationConfigurationError(PostModerationError):
    pass


class PostModerationNotFoundError(PostModerationError):
    pass


class PostModerationConflictError(PostModerationError):
    pass


class PostModerationInputError(PostModerationError):
    pass


class PostModerationStorageError(PostModerationError):
    pass


class PostModerationPreconditionError(PostModerationError):
    pass


@dataclass(frozen=True)
class ModerationAuthorities:
    moderator_id: str
    moderator_role: Literal["content_moderator"]
    appeal_reviewer_id: str
    appeal_reviewer_role: Literal["appeal_reviewer"]


@dataclass(frozen=True)
class ModerationEvidenceSnapshot:
    markdown: str
    manifest: dict[str, Any]
    sha256: str


@dataclass(frozen=True)
class ModerationCaseBundle:
    post: Post
    case: ModerationCase
    reports: tuple[PostReport, ...]
    decision: ModerationDecision | None = None
    appeal: ModerationAppeal | None = None


@dataclass(frozen=True)
class ModerationAppealBundle:
    post: Post
    case: ModerationCase
    decision: ModerationDecision
    appeal: ModerationAppeal
    reports: tuple[PostReport, ...]


@dataclass(frozen=True)
class ModerationDecisionResult:
    post: Post
    case: ModerationCase
    decision: ModerationDecision
    evidence: ModerationEvidenceSnapshot
    reports: tuple[PostReport, ...]


@dataclass(frozen=True)
class ModerationAppealResult:
    post: Post
    case: ModerationCase
    decision: ModerationDecision
    appeal: ModerationAppeal
    evidence: ModerationEvidenceSnapshot
    reports: tuple[PostReport, ...]


def configured_moderation_authorities(settings: Settings) -> ModerationAuthorities:
    if (
        not settings.post_moderator_id
        or settings.post_moderator_role != "content_moderator"
        or not settings.appeal_reviewer_id
        or settings.appeal_reviewer_role != "appeal_reviewer"
        or settings.post_moderator_id == settings.appeal_reviewer_id
    ):
        raise PostModerationConfigurationError(
            "independent post moderation authorities are not pre-provisioned"
        )
    return ModerationAuthorities(
        moderator_id=settings.post_moderator_id,
        moderator_role=cast(Literal["content_moderator"], settings.post_moderator_role),
        appeal_reviewer_id=settings.appeal_reviewer_id,
        appeal_reviewer_role=cast(Literal["appeal_reviewer"], settings.appeal_reviewer_role),
    )


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _text_digest(value: str | None) -> str | None:
    return None if value is None else hashlib.sha256(value.encode("utf-8")).hexdigest()


def _subject_binding(*values: str) -> str:
    return hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()


def _report_manifest(report: PostReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "reason_code": report.reason_code,
        "narrative_sha256": _text_digest(report.narrative),
        "created_at": _utc_text(report.created_at),
    }


def _base_evidence_manifest(
    case: ModerationCase, post: Post, reports: tuple[PostReport, ...]
) -> dict[str, Any]:
    return {
        "schema": "connect.md/post-moderation-evidence",
        "schema_version": 1,
        "case": {
            "id": case.id,
            "post_id": case.post_id,
            "status": case.status,
            "created_at": _utc_text(case.created_at),
            "updated_at": _utc_text(case.updated_at),
            "closed_at": _utc_text(case.closed_at),
            "retention_expires_at": _utc_text(case.retention_expires_at),
            "sensitive_purged_at": _utc_text(case.sensitive_purged_at),
            "subject_matches_post_owner": case.subject_owner_id == post.owner_id,
            "subject_binding_sha256": _subject_binding(case.subject_owner_id, post.owner_id),
        },
        "post": {
            "id": post.id,
            "status": post.status,
            "current_version": post.current_version,
            "sha256": post.sha256,
            "published_at": _utc_text(post.published_at),
            "updated_at": _utc_text(post.updated_at),
            "withdrawn_at": _utc_text(post.withdrawn_at),
            "withheld_at": _utc_text(post.withheld_at),
        },
        "reports": [_report_manifest(report) for report in reports],
    }


def case_evidence_manifest(
    case: ModerationCase, post: Post, reports: tuple[PostReport, ...]
) -> dict[str, Any]:
    manifest = _base_evidence_manifest(case, post, reports)
    manifest["purpose"] = "initial_decision"
    manifest["decision"] = None
    manifest["appeal"] = None
    return manifest


def appeal_evidence_manifest(
    case: ModerationCase,
    post: Post,
    reports: tuple[PostReport, ...],
    decision: ModerationDecision,
    appeal: ModerationAppeal,
) -> dict[str, Any]:
    manifest = _base_evidence_manifest(case, post, reports)
    manifest["purpose"] = "appeal_review"
    manifest["decision"] = {
        "id": decision.id,
        "case_matches": decision.case_id == case.id,
        "post_matches": decision.post_id == post.id,
        "moderator_role": decision.moderator_role,
        "action": decision.action,
        "reason_code": decision.reason_code,
        "subject_explanation_sha256": _text_digest(decision.subject_explanation),
        "internal_rationale_sha256": _text_digest(decision.internal_rationale),
        "evidence_sha256": _text_digest(decision.evidence),
        "evidence_snapshot_sha256": decision.evidence_snapshot_sha256,
        "decided_at": _utc_text(decision.decided_at),
    }
    manifest["appeal"] = {
        "id": appeal.id,
        "case_matches": appeal.case_id == case.id,
        "decision_matches": appeal.decision_id == decision.id,
        "subject_matches_case": appeal.subject_owner_id == case.subject_owner_id,
        "subject_binding_sha256": _subject_binding(
            appeal.subject_owner_id, case.subject_owner_id, post.owner_id
        ),
        "rationale_sha256": _text_digest(appeal.rationale),
        "status": appeal.status,
        "submitted_at": _utc_text(appeal.submitted_at),
        "reviewed_at": _utc_text(appeal.reviewed_at),
        "appeal_reviewer_role": appeal.appeal_reviewer_role,
        "subject_explanation_sha256": _text_digest(appeal.subject_explanation),
        "internal_rationale_sha256": _text_digest(appeal.internal_rationale),
        "review_snapshot_sha256": appeal.review_snapshot_sha256,
    }
    return manifest


def evidence_manifest_sha256(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verified_snapshot(
    store: VersionStore, post: Post, manifest: dict[str, Any]
) -> ModerationEvidenceSnapshot:
    try:
        markdown = store.read_verified(post.storage_path, post.sha256)
        frontmatter, _ = validate_canonical("post", markdown)
        published_at = frontmatter.get("published_at")
        if not isinstance(published_at, str):
            raise StorageIntegrityError("canonical post timestamp is malformed")
        try:
            canonical_published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StorageIntegrityError("canonical post timestamp is malformed") from exc
        if canonical_published_at.tzinfo is None or canonical_published_at.utcoffset() is None:
            raise StorageIntegrityError("canonical post timestamp lacks a timezone")
        row_published_at = (
            post.published_at
            if post.published_at.tzinfo is not None
            else post.published_at.replace(tzinfo=UTC)
        )
        if (
            post.current_version != 1
            or frontmatter.get("id") != post.id
            or frontmatter.get("author_profile_handle") != post.author_profile_handle
            or frontmatter.get("version") != 1
            or canonical_published_at.astimezone(UTC) != row_published_at.astimezone(UTC)
        ):
            raise StorageIntegrityError("canonical post Markdown does not match its ledger row")
    except (StorageIntegrityError, MarkdownValidationError) as exc:
        raise PostModerationStorageError("canonical post storage failed verification") from exc
    return ModerationEvidenceSnapshot(
        markdown=markdown,
        manifest=manifest,
        sha256=evidence_manifest_sha256(manifest),
    )


def case_evidence_snapshot(
    store: VersionStore, bundle: ModerationCaseBundle
) -> ModerationEvidenceSnapshot:
    return _verified_snapshot(
        store,
        bundle.post,
        case_evidence_manifest(bundle.case, bundle.post, bundle.reports),
    )


def appeal_evidence_snapshot(
    store: VersionStore, bundle: ModerationAppealBundle
) -> ModerationEvidenceSnapshot:
    return _verified_snapshot(
        store,
        bundle.post,
        appeal_evidence_manifest(
            bundle.case,
            bundle.post,
            bundle.reports,
            bundle.decision,
            bundle.appeal,
        ),
    )


def _require_expected_snapshot(expected: str | None, actual: str) -> None:
    if expected is None:
        return
    if (
        len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
        or not hmac.compare_digest(expected, actual)
    ):
        raise PostModerationPreconditionError("moderation evidence snapshot precondition failed")


def _validate_reports(case: ModerationCase, post: Post, reports: tuple[PostReport, ...]) -> None:
    if len(reports) > 1_000:
        raise PostModerationStorageError("moderation report evidence exceeds the review bound")
    if any(
        report.case_id != case.id
        or report.post_id != post.id
        or report.reason_code not in _REASON_CODES
        or not isinstance(report.created_at, datetime)
        or (report.narrative is not None and not isinstance(report.narrative, str))
        for report in reports
    ):
        raise PostModerationStorageError("moderation report evidence is inconsistent")


async def lock_case_review_bundle(
    session: AsyncSession,
    *,
    case_id: str,
    expected_post_id: str | None = None,
    read: bool = False,
    allow_existing_decision: bool = False,
) -> ModerationCaseBundle:
    probe = await session.get(ModerationCase, case_id)
    if probe is None:
        raise PostModerationNotFoundError("moderation case was not found")
    post_id = expected_post_id or probe.post_id
    post = await session.scalar(
        select(Post)
        .where(Post.id == post_id)
        .with_for_update(read=read)
        .execution_options(populate_existing=True)
    )
    case = await session.scalar(
        select(ModerationCase)
        .where(ModerationCase.id == case_id)
        .with_for_update(read=read)
        .execution_options(populate_existing=True)
    )
    if case is None:
        raise PostModerationNotFoundError("moderation case was not found")
    if post is None or case.post_id != post.id or case.subject_owner_id != post.owner_id:
        raise PostModerationStorageError("case does not match the post subject")
    existing_decision = await session.scalar(
        select(ModerationDecision)
        .where(ModerationDecision.case_id == case.id)
        .with_for_update(read=read)
        .execution_options(populate_existing=True)
    )
    appeal = None
    if existing_decision is not None:
        appeal = await session.scalar(
            select(ModerationAppeal)
            .where(ModerationAppeal.decision_id == existing_decision.id)
            .with_for_update(read=read)
            .execution_options(populate_existing=True)
        )
    if existing_decision is not None and not allow_existing_decision:
        raise PostModerationConflictError(
            "only an open moderation case can receive an initial decision"
        )
    if existing_decision is not None and (
        existing_decision.case_id != case.id
        or existing_decision.post_id != post.id
        or (
            appeal is not None
            and (
                appeal.case_id != case.id
                or appeal.decision_id != existing_decision.id
                or appeal.subject_owner_id != case.subject_owner_id
            )
        )
    ):
        raise PostModerationStorageError("moderation decision authority records are inconsistent")
    reports = tuple(
        (
            await session.scalars(
                select(PostReport)
                .where(PostReport.case_id == case.id)
                .order_by(PostReport.created_at.asc(), PostReport.id.asc())
                .limit(1_001)
                .with_for_update(read=read)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    _validate_reports(case, post, reports)
    return ModerationCaseBundle(
        post=post,
        case=case,
        reports=reports,
        decision=existing_decision,
        appeal=appeal,
    )


async def lock_appeal_review_bundle(
    session: AsyncSession, *, appeal_id: str, read: bool = False
) -> ModerationAppealBundle:
    appeal_probe = await session.get(ModerationAppeal, appeal_id)
    if appeal_probe is None:
        raise PostModerationNotFoundError("moderation appeal was not found")
    case_probe = await session.get(ModerationCase, appeal_probe.case_id)
    if case_probe is None:
        raise PostModerationStorageError("appeal authority records are inconsistent")

    post = await session.scalar(
        select(Post)
        .where(Post.id == case_probe.post_id)
        .with_for_update(read=read)
        .execution_options(populate_existing=True)
    )
    case = await session.scalar(
        select(ModerationCase)
        .where(ModerationCase.id == appeal_probe.case_id)
        .with_for_update(read=read)
        .execution_options(populate_existing=True)
    )
    decision = await session.scalar(
        select(ModerationDecision)
        .where(ModerationDecision.id == appeal_probe.decision_id)
        .with_for_update(read=read)
        .execution_options(populate_existing=True)
    )
    appeal = await session.scalar(
        select(ModerationAppeal)
        .where(ModerationAppeal.id == appeal_id)
        .with_for_update(read=read)
        .execution_options(populate_existing=True)
    )
    if post is None or case is None or decision is None or appeal is None:
        raise PostModerationStorageError("appeal authority records are inconsistent")
    if (
        case.post_id != post.id
        or case.subject_owner_id != post.owner_id
        or decision.case_id != case.id
        or decision.post_id != post.id
        or appeal.case_id != case.id
        or appeal.decision_id != decision.id
        or appeal.subject_owner_id != case.subject_owner_id
    ):
        raise PostModerationStorageError("appeal authority records are inconsistent")
    reports = tuple(
        (
            await session.scalars(
                select(PostReport)
                .where(PostReport.case_id == case.id)
                .order_by(PostReport.created_at.asc(), PostReport.id.asc())
                .limit(1_001)
                .with_for_update(read=read)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    _validate_reports(case, post, reports)
    return ModerationAppealBundle(
        post=post,
        case=case,
        decision=decision,
        appeal=appeal,
        reports=reports,
    )


async def decide_case(
    session: AsyncSession,
    store: VersionStore,
    settings: Settings,
    *,
    case_id: str,
    expected_post_id: str | None,
    action: ModerationAction,
    reason_code: str,
    subject_explanation: str,
    actor_method: str,
    expected_snapshot_sha256: str | None = None,
    now: datetime | None = None,
) -> ModerationDecisionResult:
    authorities = configured_moderation_authorities(settings)
    if action not in {"dismiss", "withhold"}:
        raise PostModerationInputError("moderation action is invalid")
    if reason_code not in _REASON_CODES:
        raise PostModerationInputError("moderation reason code is invalid")
    if not 1 <= len(subject_explanation) <= 500:
        raise PostModerationInputError("subject explanation must be 1-500 characters")
    bundle = await lock_case_review_bundle(
        session, case_id=case_id, expected_post_id=expected_post_id
    )
    post, case = bundle.post, bundle.case
    if case.subject_owner_id == authorities.moderator_id:
        raise PostModerationConflictError("post moderator is not independent for this case")
    if case.status != "open":
        raise PostModerationConflictError(
            "only an open moderation case can receive an initial decision"
        )
    if action == "withhold" and post.status != "published":
        raise PostModerationConflictError("only a published post can be withheld")

    evidence = case_evidence_snapshot(store, bundle)
    _require_expected_snapshot(expected_snapshot_sha256, evidence.sha256)
    occurred_at = now or datetime.now(UTC)
    if action == "withhold":
        post.status = "withheld"
        post.withheld_at = occurred_at
        post.updated_at = occurred_at
        case.status = "withheld"
        decision_action = "withhold"
        audit_type = "decision_withheld"
        session.add(
            ChangeEvent(
                owner_id=post.owner_id,
                event_type="post.withheld",
                resource_type="post",
                resource_id=post.id,
                actor_id=_SYSTEM_CHANGE_ACTOR,
                actor_method="system",
                grant_id=None,
                payload="{}",
                occurred_at=occurred_at,
            )
        )
    else:
        case.status = "dismissed"
        decision_action = "no_action"
        audit_type = "decision_no_action"
    case.updated_at = occurred_at
    case.closed_at = occurred_at
    case.retention_expires_at = occurred_at + timedelta(days=90)
    decision = ModerationDecision(
        id=new_id(),
        case_id=case.id,
        post_id=post.id,
        moderator_id=authorities.moderator_id,
        moderator_role=authorities.moderator_role,
        action=decision_action,
        reason_code=reason_code,
        subject_explanation=subject_explanation,
        internal_rationale=None,
        evidence=None,
        evidence_snapshot_sha256=evidence.sha256,
        decided_at=occurred_at,
    )
    session.add_all(
        (
            decision,
            ModerationAuditEvent(
                id=new_id(),
                case_id=case.id,
                post_id=post.id,
                event_type=audit_type,
                actor_id=authorities.moderator_id,
                actor_role=authorities.moderator_role,
                safe_metadata=json.dumps(
                    {"actor_method": actor_method}, sort_keys=True, separators=(",", ":")
                ),
                occurred_at=occurred_at,
            ),
        )
    )
    return ModerationDecisionResult(
        post=post,
        case=case,
        decision=decision,
        evidence=evidence,
        reports=bundle.reports,
    )


async def review_appeal(
    session: AsyncSession,
    store: VersionStore,
    settings: Settings,
    *,
    appeal_id: str,
    action: AppealAction,
    subject_explanation: str,
    actor_method: str,
    expected_snapshot_sha256: str | None = None,
    now: datetime | None = None,
) -> ModerationAppealResult:
    authorities = configured_moderation_authorities(settings)
    if action not in {"uphold", "overturn"}:
        raise PostModerationInputError("appeal action is invalid")
    if not 1 <= len(subject_explanation) <= 500:
        raise PostModerationInputError("subject explanation must be 1-500 characters")
    bundle = await lock_appeal_review_bundle(session, appeal_id=appeal_id)
    post, case, decision, appeal = (
        bundle.post,
        bundle.case,
        bundle.decision,
        bundle.appeal,
    )
    if appeal.status != "submitted" or case.status != "appealed" or decision.action != "withhold":
        raise PostModerationConflictError("appeal is not awaiting independent review")
    if (
        authorities.appeal_reviewer_id == decision.moderator_id
        or authorities.appeal_reviewer_id == case.subject_owner_id
    ):
        raise PostModerationConflictError("appeal reviewer is not independent for this case")
    if post.status not in {"withheld", "withdrawn"}:
        raise PostModerationConflictError("appeal authority records are inconsistent")

    evidence = appeal_evidence_snapshot(store, bundle)
    _require_expected_snapshot(expected_snapshot_sha256, evidence.sha256)
    occurred_at = now or datetime.now(UTC)
    appeal.status = "overturned" if action == "overturn" else "upheld"
    appeal.reviewed_at = occurred_at
    appeal.appeal_reviewer_id = authorities.appeal_reviewer_id
    appeal.appeal_reviewer_role = authorities.appeal_reviewer_role
    appeal.subject_explanation = subject_explanation
    appeal.internal_rationale = None
    appeal.review_snapshot_sha256 = evidence.sha256
    case.status = "appeal_overturned" if action == "overturn" else "appeal_upheld"
    case.closed_at = occurred_at
    case.updated_at = occurred_at
    case.retention_expires_at = occurred_at + timedelta(days=90)
    if action == "overturn" and post.status == "withheld":
        post.status = "published"
        post.withheld_at = None
        post.updated_at = occurred_at
        session.add(
            ChangeEvent(
                owner_id=post.owner_id,
                event_type="post.restored",
                resource_type="post",
                resource_id=post.id,
                actor_id=_SYSTEM_CHANGE_ACTOR,
                actor_method="system",
                grant_id=None,
                payload="{}",
                occurred_at=occurred_at,
            )
        )
    session.add(
        ModerationAuditEvent(
            id=new_id(),
            case_id=case.id,
            post_id=post.id,
            event_type="appeal_overturned" if action == "overturn" else "appeal_upheld",
            actor_id=authorities.appeal_reviewer_id,
            actor_role=authorities.appeal_reviewer_role,
            safe_metadata=json.dumps(
                {"actor_method": actor_method}, sort_keys=True, separators=(",", ":")
            ),
            occurred_at=occurred_at,
        )
    )
    return ModerationAppealResult(
        post=post,
        case=case,
        decision=decision,
        appeal=appeal,
        evidence=evidence,
        reports=bundle.reports,
    )
