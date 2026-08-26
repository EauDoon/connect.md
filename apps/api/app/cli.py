"""Operational commands; run `python -m app.cli --help`."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from argparse import ArgumentParser, Namespace
from datetime import UTC, datetime
from hmac import compare_digest

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import build_engine, build_session_factory
from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountBackupAuthority,
    AccountErasureItem,
    AgentIdentity,
    AgentMandate,
    ChangeEvent,
    Document,
    LifecycleTask,
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
    RetentionHold,
    SearchProjectionTask,
    new_id,
)
from app.services.account_erasure import AccountErasureExecutor, HttpClerkLifecycleProvider
from app.services.backup_authority import (
    register_backup_generation as register_backup_generation_with_settings,
)
from app.services.backup_authority import (
    transition_backup_generation as transition_backup_generation_with_settings,
)
from app.services.database_roles import (
    ACCOUNT_ERASURE_DATABASE_ROLE,
    API_DATABASE_ROLE,
    PROJECTION_ADMIN_DATABASE_ROLE,
    require_database_role,
)
from app.services.deletion_journal import (
    DeletionCommitmentJournal,
    DeletionJournalError,
    verify_live_deletion_mirror,
)
from app.services.exact_search import ExactSearchService, ExactSearchUnavailable
from app.services.hold_lineage import hold_descendants
from app.services.post_moderation_authority import (
    inspect_post_moderation_case as inspect_post_moderation_case_with_settings,
)
from app.services.post_moderation_authority import (
    list_post_moderation_cases as list_post_moderation_cases_with_settings,
)
from app.services.post_moderation_authority import (
    moderate_post as moderate_post_with_settings,
)
from app.services.post_moderation_authority import (
    review_post_appeal as review_post_appeal_with_settings,
)
from app.services.recruiting_evidence import (
    RecruitingEvidenceUnavailable,
    claims_from_rows,
    verify_recruiting_evidence,
)
from app.services.retention import RetentionExecutor
from app.services.search import MeiliSearchProjection, SearchUnavailable
from app.services.storage import StorageIntegrityError, VersionStore
from app.services.taxonomy import TaxonomyService, TaxonomyUnavailable

_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

# This is deliberately broader than retention disposal.  A preservation hold
# may cover any real account-erasure resource, but never synthetic provider or
# search work items.
HOLDABLE_RESOURCE_TYPES = frozenset(
    {
        "agent_grant",
        "agent_identity",
        "agent_mandate",
        "agent_proposal",
        "api_key",
        "application",
        "contact_policy",
        "contact_request",
        "connection",
        "connection_request",
        "conversation",
        "document",
        "document_version",
        "idempotency_record",
        "job",
        "message",
        "moderation_appeal",
        "moderation_audit_event",
        "moderation_case",
        "moderation_decision",
        "notification",
        "organization",
        "organization_membership",
        "organization_verification",
        "organization_verification_evidence",
        "organization_verification_event",
        "post",
        "post_moderation_event",
        "post_report",
        "post_version",
        "retention_hold",
    }
)


async def _acquire_retention_hold_guard(session: AsyncSession) -> bool:
    """Lock the shared preservation boundary before hold or disposal state changes."""
    await session.execute(
        update(AccountBackupAuthority)
        .where(AccountBackupAuthority.id == ACCOUNT_BACKUP_AUTHORITY_ID)
        .values(updated_at=AccountBackupAuthority.updated_at)
    )
    return (
        await session.get(AccountBackupAuthority, ACCOUNT_BACKUP_AUTHORITY_ID, with_for_update=True)
        is not None
    )


async def register_backup_generation(args: Namespace) -> int:
    return await register_backup_generation_with_settings(
        get_settings,
        args,
        parse_timestamp=_parse_retention_timestamp,
    )


async def transition_backup_generation(args: Namespace) -> int:
    return await transition_backup_generation_with_settings(get_settings, args)


async def rebuild_search() -> int:
    settings = get_settings()
    settings.require_database_role_configuration(PROJECTION_ADMIN_DATABASE_ROLE)
    gate_engine = build_engine(settings)
    gate_factory = build_session_factory(settings, gate_engine)
    try:
        async with gate_factory() as gate_session:
            await require_database_role(gate_session, PROJECTION_ADMIN_DATABASE_ROLE)
            if settings.deletion_journal_path is None:
                journal = None
            else:
                journal = DeletionCommitmentJournal(settings)
            if journal is not None:
                commitments = journal.verify()
                if commitments and not settings.account_lifecycle_enabled:
                    raise DeletionJournalError(
                        "account lifecycle cannot be disabled while deletion commitments exist"
                    )
                await verify_live_deletion_mirror(gate_session, journal)
    except DeletionJournalError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("projection admin database authorization is unavailable", file=sys.stderr)
        return 1
    finally:
        await gate_engine.dispose()
    projection = MeiliSearchProjection(settings)
    if not projection.enabled:
        print(
            "Meilisearch is not configured; no connect.md projection was rebuilt.", file=sys.stderr
        )
        return 2
    try:
        await projection.reset_index()
    except SearchUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    store = VersionStore(settings.storage_path)
    count = 0
    total = 0
    try:
        async with session_factory() as session:
            await require_database_role(session, PROJECTION_ADMIN_DATABASE_ROLE)
            documents = (
                await session.scalars(select(Document).options(selectinload(Document.versions)))
            ).all()
            total = len(documents)
            for document in documents:
                if document.visibility != "public":
                    continue
                version = next(
                    item for item in document.versions if item.version == document.current_version
                )
                await projection.index(
                    document, store.read_verified(version.storage_path, version.sha256)
                )
                count += 1
            # The operational rebuild barrier stops every canonical/search writer.
            # Retire the now-satisfied projection log only after every remote write
            # succeeds so stale dead letters cannot degrade the rebuilt index.
            await session.execute(delete(SearchProjectionTask))
            await session.commit()
    except SearchUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(
        f"Reindexed {count} public canonical document(s); "
        f"excluded {total - count} non-public of {total} total."
    )
    return 0


async def run_taxonomy(args: Namespace) -> int:
    settings = get_settings()
    settings.require_database_role_configuration(PROJECTION_ADMIN_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    service = TaxonomyService(b"connect.md:taxonomy-cli:v1")
    try:
        async with session_factory() as session:
            await require_database_role(session, PROJECTION_ADMIN_DATABASE_ROLE)
            if args.taxonomy_action == "backfill":
                result = await service.backfill(
                    session,
                    VersionStore(settings.storage_path),
                    if_required=bool(args.if_required),
                )
                print(json.dumps(result, sort_keys=True))
            else:
                await service.verify_integrity(session, require_ready=True, deterministic=True)
                print(json.dumps({"status": "verified"}, sort_keys=True))
    except TaxonomyUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


async def run_exact_search(args: Namespace) -> int:
    settings = get_settings()
    settings.require_database_role_configuration(PROJECTION_ADMIN_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    service = ExactSearchService(settings)
    try:
        async with session_factory() as session:
            await require_database_role(session, PROJECTION_ADMIN_DATABASE_ROLE)
            if args.exact_search_action == "backfill":
                result = await service.backfill(
                    session,
                    VersionStore(settings.storage_path),
                    if_required=bool(args.if_required),
                )
            else:
                await service.verify_integrity(
                    session, VersionStore(settings.storage_path), require_ready=True
                )
                result = {"status": "verified"}
            print(json.dumps(result, sort_keys=True))
    except ExactSearchUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


async def run_deletion_journal(args: Namespace) -> int:
    settings = get_settings()
    try:
        journal = DeletionCommitmentJournal(settings)
        if args.journal_action == "init":
            journal.initialize()
            print("DELETION_JOURNAL=INITIALIZED")
            return 0
        if args.journal_action == "checkpoint":
            head_sequence, head_digest = journal.checkpoint()
            print(f"deletion_journal_head_sequence={head_sequence}")
            print(f"deletion_journal_head_digest={head_digest}")
            return 0
        if args.journal_action == "verify-checkpoint":
            journal.assert_checkpoint(
                head_sequence=args.head_sequence, head_digest=args.head_digest
            )
            print("DELETION_JOURNAL_CHECKPOINT=VERIFIED")
            return 0
        settings.require_database_role_configuration(API_DATABASE_ROLE)
        engine = build_engine(settings)
        factory = build_session_factory(settings, engine)
        try:
            async with factory() as session:
                await require_database_role(session, API_DATABASE_ROLE)
                commitments = journal.verify()
                if commitments and not settings.account_lifecycle_enabled:
                    raise DeletionJournalError(
                        "account lifecycle cannot be disabled while deletion commitments exist"
                    )
                count = await verify_live_deletion_mirror(session, journal)
        finally:
            await engine.dispose()
        print(f"DELETION_JOURNAL_LIVE_MIRROR=VERIFIED commitments={count}")
        return 0
    except DeletionJournalError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("deletion journal operation is unavailable", file=sys.stderr)
        return 1


async def apply_verification_transition(args: Namespace) -> int:
    settings = get_settings()
    if args.action in {"activate", "restore"} and not settings.recruiting_enabled:
        print("recruiting release is disabled", file=sys.stderr)
        return 2
    if not settings.verification_reviewer_id or settings.verification_reviewer_role is None:
        print("verification reviewer identity and role are not pre-provisioned", file=sys.stderr)
        return 2
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    evidence_actions = {"review", "activate", "reject", "restore"}
    expected_review_snapshot_sha256 = getattr(args, "expected_review_snapshot_sha256", None)
    if args.action in evidence_actions and (
        not isinstance(expected_review_snapshot_sha256, str)
        or _HEX_DIGEST.fullmatch(expected_review_snapshot_sha256) is None
    ):
        print(
            "exact review snapshot precondition is required for this transition",
            file=sys.stderr,
        )
        return 2
    evidence_store: VersionStore | None = None
    if args.action in evidence_actions:
        try:
            evidence_store = VersionStore(settings.storage_path)
        except StorageIntegrityError:
            print("verification evidence is unavailable", file=sys.stderr)
            return 1
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            verification = await session.scalar(
                select(OrganizationVerification).where(
                    OrganizationVerification.id == args.verification_id
                )
            )
            if verification is None:
                print("verification was not found", file=sys.stderr)
                return 1
            organization = await session.scalar(
                select(Organization)
                .where(Organization.id == verification.organization_id)
                .with_for_update()
            )
            if organization is None:
                print("verification organization was not found", file=sys.stderr)
                return 1
            latest = await session.scalar(
                select(OrganizationVerificationEvent)
                .where(OrganizationVerificationEvent.organization_id == organization.id)
                .order_by(
                    OrganizationVerificationEvent.occurred_at.desc(),
                    OrganizationVerificationEvent.id.desc(),
                )
                .limit(1)
            )
            if latest is None or latest.verification_id != verification.id:
                print("verification is not the organization’s current application", file=sys.stderr)
                return 1
            now = datetime.now(UTC)
            current_state = latest.to_state
            latest_expiry = latest.expires_at
            if latest_expiry is not None and latest_expiry.tzinfo is None:
                latest_expiry = latest_expiry.replace(tzinfo=UTC)
            if current_state == "active" and latest_expiry is not None and latest_expiry <= now:
                current_state = "expired"
            allowed = {
                "review": {"submitted"},
                "activate": {"under_review"},
                "reject": {"under_review"},
                "expire": {"active", "expired"},
                "suspend": {"active"},
                "revoke": {"active"},
                "restore": {"suspended"},
            }
            if current_state not in allowed[args.action]:
                print(
                    "verification transition is not allowed from its current state", file=sys.stderr
                )
                return 1
            evidence: OrganizationVerificationEvidence | None = None
            if args.action in evidence_actions:
                evidence = await session.scalar(
                    select(OrganizationVerificationEvidence).where(
                        OrganizationVerificationEvidence.verification_id == verification.id
                    )
                )
                if evidence is None:
                    print("verification evidence is unavailable", file=sys.stderr)
                    return 1
                assert evidence_store is not None
                try:
                    verified_evidence = verify_recruiting_evidence(
                        evidence_store,
                        claims_from_rows(organization, verification, evidence),
                        now=now,
                    )
                except (RecruitingEvidenceUnavailable, StorageIntegrityError):
                    print("verification evidence is unavailable", file=sys.stderr)
                    return 1
                assert expected_review_snapshot_sha256 is not None
                if not compare_digest(
                    verified_evidence.review_snapshot_sha256,
                    expected_review_snapshot_sha256,
                ):
                    print("review snapshot precondition is stale", file=sys.stderr)
                    return 1
            to_state = {
                "review": "under_review",
                "activate": "active",
                "reject": "rejected",
                "expire": "expired",
                "suspend": "suspended",
                "revoke": "revoked",
                "restore": "active",
            }[args.action]
            requires_activation_fields = to_state == "active"
            policy_version = latest.policy_version
            expires_at = latest_expiry
            if requires_activation_fields:
                if not args.policy_version or not args.material_claim_digest or not args.expires_at:
                    print(
                        "active verification requires policy version, claim digest, and expiry",
                        file=sys.stderr,
                    )
                    return 2
                if args.material_claim_digest != verification.material_claim_digest:
                    print(
                        "material claim digest does not match the submitted evidence",
                        file=sys.stderr,
                    )
                    return 1
                assert evidence is not None
                evidence_expires_at = evidence.retention_expires_at
                if evidence_expires_at.tzinfo is None:
                    evidence_expires_at = evidence_expires_at.replace(tzinfo=UTC)
                try:
                    expires_at = datetime.fromisoformat(args.expires_at)
                except ValueError:
                    print("expiry must be ISO-8601", file=sys.stderr)
                    return 2
                if expires_at.tzinfo is None:
                    print("expiry must include a timezone", file=sys.stderr)
                    return 2
                expires_at = expires_at.astimezone(UTC)
                if expires_at <= now:
                    print("expiry must be in the future", file=sys.stderr)
                    return 2
                if expires_at > evidence_expires_at:
                    print(
                        "active decision expiry cannot outlive retained evidence", file=sys.stderr
                    )
                    return 2
                policy_version = args.policy_version
            session.add(
                OrganizationVerificationEvent(
                    id=new_id(),
                    verification_id=verification.id,
                    organization_id=organization.id,
                    purpose="recruiting_control",
                    to_state=to_state,
                    actor_id=settings.verification_reviewer_id,
                    actor_role=settings.verification_reviewer_role,
                    policy_version=policy_version,
                    material_claim_digest=verification.material_claim_digest,
                    expires_at=expires_at if to_state in {"active", "suspended"} else None,
                    occurred_at=now,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(f"verification {args.verification_id} transitioned to {to_state}")
    return 0


def _parse_retention_timestamp(value: str, field: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        print(f"{field} must be ISO-8601", file=sys.stderr)
        return None
    if parsed.tzinfo is None:
        print(f"{field} must include a timezone", file=sys.stderr)
        return None
    return parsed.astimezone(UTC)


async def run_retention(args: Namespace) -> int:
    settings = get_settings()
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
        result = await RetentionExecutor(
            session_factory,
            VersionStore(settings.storage_path),
            worker_id=settings.retention_worker_id,
        ).run_once(limit=args.limit)
    finally:
        await engine.dispose()
    print(
        "retention "
        f"discovered={result.discovered} disposed={result.disposed} held={result.held} "
        f"retried={result.retried} dead_lettered={result.dead_lettered}"
    )
    return 0


async def run_account_erasure(args: Namespace) -> int:
    settings = get_settings()
    if not settings.account_lifecycle_enabled:
        print("account lifecycle executor is disabled", file=sys.stderr)
        return 2
    settings.require_clerk_backend_configuration()
    settings.require_database_role_configuration(ACCOUNT_ERASURE_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, ACCOUNT_ERASURE_DATABASE_ROLE)
        result = await AccountErasureExecutor(
            session_factory,
            VersionStore(settings.storage_path),
            MeiliSearchProjection(settings),
            HttpClerkLifecycleProvider(settings),
            settings,
            worker_id=settings.account_lifecycle_worker_id,
        ).run_once(limit=args.limit)
    finally:
        await engine.dispose()
    print(
        "account-erasure "
        f"claimed={result.claimed} completed={result.completed} held={result.held} "
        f"retried={result.retried} dead_lettered={result.dead_lettered}"
    )
    return 0


async def create_retention_hold(args: Namespace) -> int:
    if args.resource_type not in HOLDABLE_RESOURCE_TYPES:
        print("unsupported retention resource type", file=sys.stderr)
        return 2
    expires_at = _parse_retention_timestamp(args.expires_at, "expires-at")
    review_at = _parse_retention_timestamp(args.review_at, "review-at")
    now = datetime.now(UTC)
    if expires_at is None or review_at is None:
        return 2
    if expires_at <= now or review_at > expires_at:
        print("hold review time must not exceed a future hold expiry", file=sys.stderr)
        return 2
    settings = get_settings()
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    hold_id = new_id()
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            if not await _acquire_retention_hold_guard(session):
                await session.rollback()
                print("retention hold guard is unavailable", file=sys.stderr)
                return 1
            protected_resources = await hold_descendants(
                session, args.resource_type, args.resource_id
            )
            # Acquire the same row lock used by the erasure claimant before
            # accepting a hold.  If a destructive action already owns a lease,
            # reject rather than falsely report an accepted preservation hold.
            active_erasure_items = (
                await session.scalars(
                    select(AccountErasureItem)
                    .where(
                        or_(
                            *[
                                and_(
                                    AccountErasureItem.resource_type == resource_type,
                                    AccountErasureItem.resource_id == resource_id,
                                )
                                for resource_type, resource_id in protected_resources
                            ]
                        ),
                        AccountErasureItem.phase.in_(["delete_row", "delete_file"]),
                    )
                    .order_by(
                        AccountErasureItem.resource_type.asc(),
                        AccountErasureItem.resource_id.asc(),
                        AccountErasureItem.phase.asc(),
                        AccountErasureItem.id.asc(),
                    )
                    .with_for_update()
                )
            ).all()
            if any(item.state in {"leased", "completed"} for item in active_erasure_items):
                await session.rollback()
                print("account erasure action has already progressed", file=sys.stderr)
                return 1
            retention_tasks = (
                await session.scalars(
                    select(LifecycleTask)
                    .where(
                        or_(
                            *[
                                and_(
                                    LifecycleTask.resource_type == resource_type,
                                    LifecycleTask.resource_id == resource_id,
                                )
                                for resource_type, resource_id in sorted(protected_resources)
                            ]
                        )
                    )
                    .order_by(
                        LifecycleTask.resource_type.asc(),
                        LifecycleTask.resource_id.asc(),
                        LifecycleTask.id.asc(),
                    )
                    .with_for_update()
                )
            ).all()
            if any(task.state != "queued" or task.attempts > 0 for task in retention_tasks):
                await session.rollback()
                print("retention disposal has already progressed", file=sys.stderr)
                return 1
            session.add(
                RetentionHold(
                    id=hold_id,
                    resource_type=args.resource_type,
                    resource_id=args.resource_id,
                    purpose=args.purpose,
                    authority=args.authority,
                    expires_at=expires_at,
                    review_at=review_at,
                    created_at=now,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(f"retention hold {hold_id} created")
    return 0


async def release_retention_hold(args: Namespace) -> int:
    settings = get_settings()
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            if not await _acquire_retention_hold_guard(session):
                await session.rollback()
                print("retention hold guard is unavailable", file=sys.stderr)
                return 1
            hold = await session.scalar(
                select(RetentionHold).where(RetentionHold.id == args.hold_id).with_for_update()
            )
            if hold is None or hold.released_at is not None:
                print("retention hold was not found", file=sys.stderr)
                return 1
            if hold.authority != args.authority:
                print("retention hold release authority does not match", file=sys.stderr)
                return 1
            protected_resources = await hold_descendants(
                session, hold.resource_type, hold.resource_id
            )
            hold.released_at = datetime.now(UTC)
            hold.released_by_authority = args.authority
            await session.execute(
                update(LifecycleTask)
                .where(
                    or_(
                        *[
                            and_(
                                LifecycleTask.resource_type == resource_type,
                                LifecycleTask.resource_id == resource_id,
                            )
                            for resource_type, resource_id in sorted(protected_resources)
                        ]
                    ),
                    LifecycleTask.state == "queued",
                )
                .values(available_at=hold.released_at)
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(f"retention hold {args.hold_id} released")
    return 0


async def transition_agent_identity(args: Namespace) -> int:
    settings = get_settings()
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            identity = await session.scalar(
                select(AgentIdentity).where(AgentIdentity.handle == args.handle).with_for_update()
            )
            if identity is None:
                print("agent identity was not found", file=sys.stderr)
                return 1
            if args.agent_identity_action == "withhold":
                if identity.status != "active":
                    print(
                        "agent identity cannot be withheld from its current state", file=sys.stderr
                    )
                    return 1
                identity.status = "withheld"
                event_type = "agent_identity.withheld"
            else:
                if identity.status != "withheld":
                    print("only a withheld agent identity can be restored", file=sys.stderr)
                    return 1
                identity.status = "active"
                event_type = "agent_identity.restored"
            now = datetime.now(UTC)
            identity.updated_at = now
            session.add(
                ChangeEvent(
                    owner_id=identity.owner_id,
                    event_type=event_type,
                    resource_type="agent_identity",
                    resource_id=identity.id,
                    actor_id=args.reviewer,
                    actor_method="internal_cli",
                    payload="{}",
                    occurred_at=now,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    verb = "withheld" if args.agent_identity_action == "withhold" else "restored"
    print(f"agent identity {args.handle} {verb}")
    return 0


async def transition_agent_mandate(args: Namespace) -> int:
    settings = get_settings()
    settings.require_database_role_configuration(API_DATABASE_ROLE)
    engine = build_engine(settings)
    session_factory = build_session_factory(settings, engine)
    try:
        async with session_factory() as session:
            await require_database_role(session, API_DATABASE_ROLE)
            mandate = await session.scalar(
                select(AgentMandate).where(AgentMandate.id == args.mandate_id).with_for_update()
            )
            if mandate is None:
                print("agent mandate was not found", file=sys.stderr)
                return 1
            now = datetime.now(UTC)
            expiry = mandate.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if args.agent_mandate_action == "suspend":
                if mandate.status != "active" or expiry <= now:
                    print(
                        "agent mandate cannot be suspended from its current state", file=sys.stderr
                    )
                    return 1
                mandate.status = "suspended"
                mandate.suspended_at = now
                event_type = "agent_mandate.suspended"
            else:
                if mandate.status != "suspended" or expiry <= now:
                    print(
                        "only an unexpired suspended agent mandate can be restored", file=sys.stderr
                    )
                    return 1
                mandate.status = "active"
                mandate.suspended_at = None
                event_type = "agent_mandate.restored"
            session.add(
                ChangeEvent(
                    owner_id=mandate.owner_id,
                    event_type=event_type,
                    resource_type="agent_mandate",
                    resource_id=mandate.id,
                    actor_id=args.reviewer,
                    actor_method="internal_cli",
                    payload="{}",
                    occurred_at=now,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    verb = "suspended" if args.agent_mandate_action == "suspend" else "restored"
    print(f"agent mandate {args.mandate_id} {verb}")
    return 0


async def moderate_post(args: Namespace) -> int:
    return await moderate_post_with_settings(get_settings, args)


async def review_post_appeal(args: Namespace) -> int:
    return await review_post_appeal_with_settings(get_settings, args)


async def list_post_moderation_cases(args: Namespace) -> int:
    return await list_post_moderation_cases_with_settings(get_settings, args)


async def inspect_post_moderation_case(args: Namespace) -> int:
    return await inspect_post_moderation_case_with_settings(get_settings, args)


def parse_args() -> Namespace:
    parser = ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rebuild-search")
    taxonomy = commands.add_parser("taxonomy")
    taxonomy_commands = taxonomy.add_subparsers(dest="taxonomy_action", required=True)
    taxonomy_backfill = taxonomy_commands.add_parser("backfill")
    taxonomy_backfill.add_argument("--if-required", action="store_true")
    taxonomy_commands.add_parser("verify")
    exact_search = commands.add_parser("exact-search")
    exact_search_commands = exact_search.add_subparsers(dest="exact_search_action", required=True)
    exact_search_backfill = exact_search_commands.add_parser("backfill")
    exact_search_backfill.add_argument("--if-required", action="store_true")
    exact_search_commands.add_parser("verify")
    deletion_journal = commands.add_parser("deletion-journal")
    deletion_journal_commands = deletion_journal.add_subparsers(
        dest="journal_action", required=True
    )
    deletion_journal_commands.add_parser("init")
    deletion_journal_commands.add_parser("checkpoint")
    journal_verify_checkpoint = deletion_journal_commands.add_parser("verify-checkpoint")
    journal_verify_checkpoint.add_argument("--head-sequence", required=True, type=int)
    journal_verify_checkpoint.add_argument("--head-digest", required=True)
    deletion_journal_commands.add_parser("verify-live")
    verification = commands.add_parser("verification")
    verification.add_argument(
        "action", choices=["review", "activate", "reject", "expire", "suspend", "revoke", "restore"]
    )
    verification.add_argument("--verification-id", required=True)
    verification.add_argument("--policy-version")
    verification.add_argument("--material-claim-digest")
    verification.add_argument("--expires-at")
    verification.add_argument("--expected-review-snapshot-sha256")
    retention = commands.add_parser("retention")
    retention_commands = retention.add_subparsers(dest="retention_command", required=True)
    retention_run = retention_commands.add_parser("run")
    retention_run.add_argument("--limit", type=int, default=100)
    retention_hold = retention_commands.add_parser("hold")
    retention_hold.add_argument("--resource-type", required=True)
    retention_hold.add_argument("--resource-id", required=True)
    retention_hold.add_argument("--purpose", required=True)
    retention_hold.add_argument("--authority", required=True)
    retention_hold.add_argument("--expires-at", required=True)
    retention_hold.add_argument("--review-at", required=True)
    retention_release = retention_commands.add_parser("release")
    retention_release.add_argument("--hold-id", required=True)
    retention_release.add_argument("--authority", required=True)
    account_erasure = commands.add_parser("account-erasure")
    account_erasure.add_argument("--limit", type=int, default=100)
    account_backup = commands.add_parser("account-backup")
    account_backup_commands = account_backup.add_subparsers(dest="backup_action", required=True)
    backup_register = account_backup_commands.add_parser("register")
    backup_register.add_argument("--generation-id", required=True)
    backup_register.add_argument("--created-at", required=True)
    backup_register.add_argument("--expires-at", required=True)
    backup_register.add_argument("--db-manifest-digest", required=True)
    backup_register.add_argument("--markdown-manifest-digest", required=True)
    backup_expire = account_backup_commands.add_parser("expire")
    backup_expire.add_argument("--generation-id", required=True)
    backup_expire.add_argument("--proof-digest", required=True)
    backup_crypto = account_backup_commands.add_parser("crypto-destroyed")
    backup_crypto.add_argument("--generation-id", required=True)
    backup_crypto.add_argument("--proof-digest", required=True)
    agent_identity = commands.add_parser("agent-identity")
    agent_identity.add_argument("agent_identity_action", choices=["withhold", "restore"])
    agent_identity.add_argument("--handle", required=True)
    agent_identity.add_argument("--reviewer", required=True)
    agent_mandate = commands.add_parser("agent-mandate")
    agent_mandate.add_argument("agent_mandate_action", choices=["suspend", "restore"])
    agent_mandate.add_argument("--mandate-id", required=True)
    agent_mandate.add_argument("--reviewer", required=True)
    post_moderation = commands.add_parser("post-moderation")
    post_moderation_commands = post_moderation.add_subparsers(
        dest="post_moderation_command", required=True
    )
    post_decision = post_moderation_commands.add_parser("decide")
    post_decision.add_argument("post_moderation_action", choices=["dismiss", "withhold"])
    post_decision.add_argument("--case-id", required=True)
    post_decision.add_argument("--post-id", required=True)
    post_decision.add_argument(
        "--reason-code",
        required=True,
        choices=["spam", "harassment", "misinformation", "privacy", "illegal_content", "other"],
    )
    post_decision.add_argument("--subject-explanation", required=True)
    post_appeal = post_moderation_commands.add_parser("appeal")
    post_appeal.add_argument("appeal_action", choices=["uphold", "overturn"])
    post_appeal.add_argument("--appeal-id", required=True)
    post_appeal.add_argument("--subject-explanation", required=True)
    post_list = post_moderation_commands.add_parser("list")
    post_list.add_argument("--limit", type=int, default=25, choices=range(1, 101))
    post_inspect = post_moderation_commands.add_parser("inspect")
    post_inspect.add_argument("--case-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "rebuild-search":
        raise SystemExit(asyncio.run(rebuild_search()))
    if args.command == "taxonomy":
        raise SystemExit(asyncio.run(run_taxonomy(args)))
    if args.command == "exact-search":
        raise SystemExit(asyncio.run(run_exact_search(args)))
    if args.command == "deletion-journal":
        raise SystemExit(asyncio.run(run_deletion_journal(args)))
    if args.command == "retention":
        if args.retention_command == "run":
            raise SystemExit(asyncio.run(run_retention(args)))
        if args.retention_command == "hold":
            raise SystemExit(asyncio.run(create_retention_hold(args)))
        raise SystemExit(asyncio.run(release_retention_hold(args)))
    if args.command == "account-erasure":
        raise SystemExit(asyncio.run(run_account_erasure(args)))
    if args.command == "account-backup":
        if args.backup_action == "register":
            raise SystemExit(asyncio.run(register_backup_generation(args)))
        raise SystemExit(asyncio.run(transition_backup_generation(args)))
    if args.command == "agent-identity":
        raise SystemExit(asyncio.run(transition_agent_identity(args)))
    if args.command == "agent-mandate":
        raise SystemExit(asyncio.run(transition_agent_mandate(args)))
    if args.command == "post-moderation":
        if args.post_moderation_command == "list":
            raise SystemExit(asyncio.run(list_post_moderation_cases(args)))
        if args.post_moderation_command == "inspect":
            raise SystemExit(asyncio.run(inspect_post_moderation_case(args)))
        if args.post_moderation_command == "appeal":
            raise SystemExit(asyncio.run(review_post_appeal(args)))
        raise SystemExit(asyncio.run(moderate_post(args)))
    raise SystemExit(asyncio.run(apply_verification_transition(args)))


if __name__ == "__main__":
    main()
