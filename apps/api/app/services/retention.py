"""Durable, fail-closed retention disposal without a network-facing control plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountBackupAuthority,
    AgentOutreachDirectPeerRateBucket,
    Application,
    ChangeEvent,
    Connection,
    ConnectionRequest,
    ContactRequest,
    Conversation,
    IdempotencyRecord,
    LifecycleTask,
    Message,
    ModerationAppeal,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    Notification,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
    PostReport,
    RetentionHold,
    RetentionTombstone,
    new_id,
)
from app.services.hold_lineage import hold_ancestors
from app.services.storage import StorageIntegrityError, VersionStore

SOCIAL_RETENTION_POLICY = "social-retention-v1"
CONTACT_RETENTION_POLICY = "contact-retention-v1"
VERIFICATION_EVIDENCE_RETENTION_POLICY = "verification-evidence-retention-v1"
MODERATION_CASE_RETENTION_POLICY = "post-moderation-case-retention-v1"
SUPPORTED_RESOURCE_TYPES = frozenset(
    {
        "application",
        "contact_request",
        "connection_request",
        "connection",
        "conversation",
        "message",
        "notification",
        "organization_verification_evidence",
        "moderation_case",
    }
)


@dataclass(frozen=True)
class RetentionRunResult:
    discovered: int = 0
    disposed: int = 0
    held: int = 0
    retried: int = 0
    dead_lettered: int = 0


@dataclass(frozen=True)
class _Claim:
    id: str
    resource_type: str
    resource_id: str
    policy_version: str
    claim_token: str
    attempts: int


class RetentionFailure(RuntimeError):
    """A sanitized operational outcome; its code must never contain user data."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class RetentionExecutor:
    """Queue expired records and dispose each exact resource once.

    Claims use PostgreSQL row locks with ``SKIP LOCKED`` and a guarded update.
    SQLite uses the same deterministic ordering and guarded update, which makes
    concurrent test workers converge on one lease without pretending SQLite has
    PostgreSQL lock semantics.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        store: VersionStore,
        *,
        worker_id: str,
        max_attempts: int = 3,
        lease_seconds: int = 60,
    ) -> None:
        self.session_factory = session_factory
        self.store = store
        self.worker_id = worker_id
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds

    async def run_once(
        self, *, limit: int = 100, now: datetime | None = None
    ) -> RetentionRunResult:
        if limit < 1 or limit > 1_000:
            raise ValueError("retention limit must be between 1 and 1000")
        current = _utc(now or datetime.now(UTC))
        discovered = await self.discover(limit=limit, now=current)
        result = RetentionRunResult(discovered=discovered)
        for _ in range(limit):
            claim = await self._claim_one(now=current)
            if claim is None:
                break
            outcome = await self._process(claim, now=current)
            result = RetentionRunResult(
                discovered=result.discovered,
                disposed=result.disposed + (outcome == "disposed"),
                held=result.held + (outcome == "held"),
                retried=result.retried + (outcome == "retry"),
                dead_lettered=result.dead_lettered + (outcome == "dead_letter"),
            )
        return result

    async def discover(self, *, limit: int, now: datetime) -> int:
        candidate_groups: list[list[tuple[str, str, str]]] = []
        async with self.session_factory() as session:
            try:
                await session.execute(
                    delete(AgentOutreachDirectPeerRateBucket).where(
                        AgentOutreachDirectPeerRateBucket.bucket_date < now.astimezone(UTC).date()
                    )
                )
            except Exception:
                raise RetentionFailure("direct_peer_rate_bucket_prune_failed") from None
            candidate_groups.append(
                await self._expired_candidates(
                    session, Application, "application", None, now, limit
                )
            )
            candidate_groups.append(
                await self._expired_candidates(
                    session, ContactRequest, "contact_request", CONTACT_RETENTION_POLICY, now, limit
                )
            )
            candidate_groups.append(
                await self._expired_candidates(
                    session,
                    ConnectionRequest,
                    "connection_request",
                    SOCIAL_RETENTION_POLICY,
                    now,
                    limit,
                )
            )
            candidate_groups.append(
                await self._expired_candidates(
                    session, Connection, "connection", SOCIAL_RETENTION_POLICY, now, limit
                )
            )
            candidate_groups.append(
                await self._expired_candidates(
                    session, Conversation, "conversation", SOCIAL_RETENTION_POLICY, now, limit
                )
            )
            candidate_groups.append(
                await self._expired_candidates(
                    session, Message, "message", SOCIAL_RETENTION_POLICY, now, limit
                )
            )
            candidate_groups.append(
                await self._expired_candidates(
                    session, Notification, "notification", SOCIAL_RETENTION_POLICY, now, limit
                )
            )
            evidence_rows = (
                await session.scalars(
                    select(OrganizationVerificationEvidence)
                    .where(
                        OrganizationVerificationEvidence.retention_expires_at <= now,
                        ~select(LifecycleTask.id)
                        .where(
                            LifecycleTask.resource_type == "organization_verification_evidence",
                            LifecycleTask.resource_id == OrganizationVerificationEvidence.id,
                        )
                        .exists(),
                        ~select(RetentionTombstone.id)
                        .where(
                            RetentionTombstone.resource_type
                            == "organization_verification_evidence",
                            RetentionTombstone.resource_id == OrganizationVerificationEvidence.id,
                        )
                        .exists(),
                    )
                    .order_by(
                        OrganizationVerificationEvidence.retention_expires_at.asc(),
                        OrganizationVerificationEvidence.id.asc(),
                    )
                    .limit(limit)
                )
            ).all()
            evidence_candidates: list[tuple[str, str, str]] = []
            for evidence in evidence_rows:
                if await self._verification_evidence_disposable(session, evidence, now):
                    evidence_candidates.append(
                        (
                            "organization_verification_evidence",
                            evidence.id,
                            VERIFICATION_EVIDENCE_RETENTION_POLICY,
                        )
                    )
            candidate_groups.append(evidence_candidates)
            moderation_cases = (
                await session.scalars(
                    select(ModerationCase)
                    .where(
                        ModerationCase.status.in_(
                            {
                                "dismissed",
                                "withheld",
                                "appeal_upheld",
                                "appeal_overturned",
                                "legacy_withheld",
                                "legacy_withdrawn",
                            }
                        ),
                        ModerationCase.retention_expires_at.is_not(None),
                        ModerationCase.retention_expires_at <= now,
                        ModerationCase.sensitive_purged_at.is_(None),
                        ~select(LifecycleTask.id)
                        .where(
                            LifecycleTask.resource_type == "moderation_case",
                            LifecycleTask.resource_id == ModerationCase.id,
                        )
                        .exists(),
                        ~select(RetentionTombstone.id)
                        .where(
                            RetentionTombstone.resource_type == "moderation_case",
                            RetentionTombstone.resource_id == ModerationCase.id,
                        )
                        .exists(),
                    )
                    .order_by(ModerationCase.retention_expires_at.asc(), ModerationCase.id.asc())
                    .limit(limit)
                )
            ).all()
            candidate_groups.append(
                [
                    ("moderation_case", case.id, MODERATION_CASE_RETENTION_POLICY)
                    for case in moderation_cases
                ]
            )
            inserted = 0
            for resource_type, resource_id, policy_version in self._fair_candidates(
                candidate_groups, limit=limit
            ):
                inserted += await self._insert_task(
                    session,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    policy_version=policy_version,
                    now=now,
                )
            await session.commit()
        return inserted

    @staticmethod
    def _fair_candidates(
        candidate_groups: list[list[tuple[str, str, str]]], *, limit: int
    ) -> list[tuple[str, str, str]]:
        """Take oldest candidates fairly across resource types in fixed type order."""
        selected: list[tuple[str, str, str]] = []
        positions = [0] * len(candidate_groups)
        while len(selected) < limit:
            added = False
            for index, group in enumerate(candidate_groups):
                position = positions[index]
                if position >= len(group):
                    continue
                selected.append(group[position])
                positions[index] += 1
                added = True
                if len(selected) == limit:
                    break
            if not added:
                break
        return selected

    async def _expired_candidates(
        self,
        session: AsyncSession,
        model: Any,
        resource_type: str,
        policy_version: str | None,
        now: datetime,
        limit: int,
    ) -> list[tuple[str, str, str]]:
        rows = (
            await session.scalars(
                select(model)
                .where(
                    model.retention_expires_at <= now,
                    ~select(LifecycleTask.id)
                    .where(
                        LifecycleTask.resource_type == resource_type,
                        LifecycleTask.resource_id == model.id,
                    )
                    .exists(),
                    ~select(RetentionTombstone.id)
                    .where(
                        RetentionTombstone.resource_type == resource_type,
                        RetentionTombstone.resource_id == model.id,
                    )
                    .exists(),
                )
                .order_by(model.retention_expires_at.asc(), model.id.asc())
                .limit(limit)
            )
        ).all()
        return [
            (
                resource_type,
                row.id,
                row.retention_policy_version
                if isinstance(row, Application)
                else policy_version or "",
            )
            for row in rows
        ]

    async def _verification_evidence_disposable(
        self,
        session: AsyncSession,
        evidence: OrganizationVerificationEvidence,
        now: datetime,
    ) -> bool:
        latest = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(OrganizationVerificationEvent.verification_id == evidence.verification_id)
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
        if latest is None or latest.to_state in {"submitted", "under_review", "suspended"}:
            return False
        if latest.to_state in {"rejected", "expired", "revoked"}:
            return True
        if latest.to_state != "active" or latest.expires_at is None:
            return False
        return _utc(latest.expires_at) <= now

    async def _insert_task(
        self,
        session: AsyncSession,
        *,
        resource_type: str,
        resource_id: str,
        policy_version: str,
        now: datetime,
    ) -> int:
        values = {
            "id": new_id(),
            "resource_type": resource_type,
            "resource_id": resource_id,
            "policy_version": policy_version,
            "state": "queued",
            "attempts": 0,
            "available_at": now,
            "created_at": now,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            statement: Any = postgresql_insert(LifecycleTask).values(**values)
        elif dialect == "sqlite":
            statement = sqlite_insert(LifecycleTask).values(**values)
        else:  # pragma: no cover - supported deployments are PostgreSQL and SQLite tests
            raise RetentionFailure("unsupported_database")
        result = await session.execute(
            statement.on_conflict_do_nothing(index_elements=["resource_type", "resource_id"])
        )
        return 1 if getattr(result, "rowcount", 0) == 1 else 0

    async def _claim_one(self, *, now: datetime) -> _Claim | None:
        async with self.session_factory() as session:
            eligible = or_(
                (LifecycleTask.state == "queued") & (LifecycleTask.available_at <= now),
                (LifecycleTask.state == "leased")
                & (LifecycleTask.lease_expires_at.is_not(None))
                & (LifecycleTask.lease_expires_at <= now),
            )
            dialect = session.get_bind().dialect.name
            statement = (
                select(LifecycleTask)
                .where(eligible)
                .order_by(
                    LifecycleTask.available_at.asc(),
                    LifecycleTask.created_at.asc(),
                    LifecycleTask.id.asc(),
                )
                .limit(1)
            )
            if dialect == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            row = await session.scalar(statement)
            if row is None:
                return None
            claim_token = new_id()
            lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            claimed = await session.execute(
                update(LifecycleTask)
                .where(LifecycleTask.id == row.id, eligible)
                .values(
                    state="leased",
                    attempts=LifecycleTask.attempts + 1,
                    lease_expires_at=lease_expires_at,
                    claimed_by=self.worker_id,
                    claim_token=claim_token,
                    last_error_code=None,
                )
                .execution_options(synchronize_session=False)
            )
            if getattr(claimed, "rowcount", 0) != 1:
                await session.rollback()
                return None
            await session.commit()
            return _Claim(
                id=row.id,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                policy_version=row.policy_version,
                claim_token=claim_token,
                attempts=row.attempts + 1,
            )

    async def _process(self, claim: _Claim, *, now: datetime) -> str:
        try:
            async with self.session_factory() as session:
                await self._acquire_hold_guard(session)
                task = await session.scalar(
                    select(LifecycleTask)
                    .where(
                        LifecycleTask.id == claim.id,
                        LifecycleTask.state == "leased",
                        LifecycleTask.claim_token == claim.claim_token,
                    )
                    .with_for_update()
                )
                if task is None:
                    return "retry"
                protected_resources = {
                    (task.resource_type, task.resource_id),
                    *await hold_ancestors(session, task.resource_type, task.resource_id),
                }
                active_hold = await session.scalar(
                    select(RetentionHold)
                    .where(
                        or_(
                            *[
                                and_(
                                    RetentionHold.resource_type == resource_type,
                                    RetentionHold.resource_id == resource_id,
                                )
                                for resource_type, resource_id in sorted(protected_resources)
                            ]
                        ),
                        RetentionHold.released_at.is_(None),
                    )
                    .order_by(
                        RetentionHold.review_at.asc(),
                        RetentionHold.created_at.asc(),
                        RetentionHold.id.asc(),
                    )
                    .limit(1)
                    .with_for_update()
                )
                if active_hold is not None:
                    task.state = "queued"
                    task.available_at = max(
                        _utc(active_hold.review_at),
                        now + timedelta(seconds=self.lease_seconds),
                    )
                    task.lease_expires_at = None
                    task.claimed_by = None
                    task.claim_token = None
                    task.last_error_code = None
                    task.attempts -= 1
                    await session.commit()
                    return "held"
                tombstone = await session.scalar(
                    select(RetentionTombstone).where(
                        RetentionTombstone.resource_type == task.resource_type,
                        RetentionTombstone.resource_id == task.resource_id,
                    )
                )
                if tombstone is None:
                    await self._dispose_resource(session, task)
                    session.add(
                        RetentionTombstone(
                            id=new_id(),
                            resource_type=task.resource_type,
                            resource_id=task.resource_id,
                            policy_version=task.policy_version,
                            task_id=task.id,
                            disposed_at=now,
                        )
                    )
                task.state = "completed"
                task.completed_at = now
                task.lease_expires_at = None
                task.claimed_by = None
                task.claim_token = None
                task.last_error_code = None
                await session.commit()
                return "disposed"
        except RetentionFailure as exc:
            if exc.code == "moderation_case_active":
                return await self._cancel_stale_task(claim)
            if exc.code == "dependency_pending":
                return await self._defer_dependency(claim, now=now)
            return await self._retry_or_dead_letter(claim, exc.code, now=now)
        except StorageIntegrityError:
            return await self._retry_or_dead_letter(claim, "storage_cleanup_failed", now=now)
        except Exception:
            # Never retain exception details: they can carry user content from a driver or filesystem.
            return await self._retry_or_dead_letter(claim, "disposition_failed", now=now)

    @staticmethod
    async def _acquire_hold_guard(session: AsyncSession) -> None:
        """Serialize preservation admission before any irreversible disposal."""
        await session.execute(
            update(AccountBackupAuthority)
            .where(AccountBackupAuthority.id == ACCOUNT_BACKUP_AUTHORITY_ID)
            .values(updated_at=AccountBackupAuthority.updated_at)
        )
        authority = await session.get(
            AccountBackupAuthority, ACCOUNT_BACKUP_AUTHORITY_ID, with_for_update=True
        )
        if authority is None:
            raise RetentionFailure("hold_guard_missing")

    async def _retry_or_dead_letter(self, claim: _Claim, code: str, *, now: datetime) -> str:
        async with self.session_factory() as session:
            task = await session.scalar(
                select(LifecycleTask)
                .where(
                    LifecycleTask.id == claim.id,
                    LifecycleTask.state == "leased",
                    LifecycleTask.claim_token == claim.claim_token,
                )
                .with_for_update()
            )
            if task is None:
                return "retry"
            task.lease_expires_at = None
            task.claimed_by = None
            task.claim_token = None
            task.last_error_code = code
            if task.attempts >= self.max_attempts:
                task.state = "dead_letter"
                await session.commit()
                return "dead_letter"
            task.state = "queued"
            task.available_at = now + timedelta(seconds=min(60, 2**task.attempts))
            await session.commit()
            return "retry"

    async def _defer_dependency(self, claim: _Claim, *, now: datetime) -> str:
        async with self.session_factory() as session:
            task = await session.scalar(
                select(LifecycleTask)
                .where(
                    LifecycleTask.id == claim.id,
                    LifecycleTask.state == "leased",
                    LifecycleTask.claim_token == claim.claim_token,
                )
                .with_for_update()
            )
            if task is None:
                return "retry"
            task.state = "queued"
            task.available_at = now + timedelta(seconds=1)
            task.lease_expires_at = None
            task.claimed_by = None
            task.claim_token = None
            task.last_error_code = None
            task.attempts -= 1
            await session.commit()
        return "retry"

    async def _cancel_stale_task(self, claim: _Claim) -> str:
        """Drop a claim whose case was reopened; later closure can rediscover it."""
        async with self.session_factory() as session:
            task = await session.scalar(
                select(LifecycleTask)
                .where(
                    LifecycleTask.id == claim.id,
                    LifecycleTask.state == "leased",
                    LifecycleTask.claim_token == claim.claim_token,
                )
                .with_for_update()
            )
            if task is None:
                return "retry"
            await session.delete(task)
            await session.commit()
        return "retry"

    async def _dispose_resource(self, session: AsyncSession, task: LifecycleTask) -> None:
        resource_type = task.resource_type
        evidence_verification_id: str | None = None
        if resource_type not in SUPPORTED_RESOURCE_TYPES:
            raise RetentionFailure("unsupported_resource")
        if resource_type == "application":
            application = await session.get(Application, task.resource_id)
            if application is not None:
                if application.snapshot_storage_path is not None:
                    try:
                        expected = self.store.application_snapshot_relative_path(application.id)
                    except ValueError as exc:  # pragma: no cover - ledger ids are UUIDs
                        raise RetentionFailure("storage_path_invalid") from exc
                    if (
                        application.snapshot_storage_path != expected
                        or application.snapshot_size_bytes is None
                    ):
                        raise RetentionFailure("storage_path_invalid")
                    self.store.delete_verified_exact(
                        expected,
                        application.snapshot_sha256,
                        expected_size_bytes=application.snapshot_size_bytes,
                        max_size_bytes=131_072,
                    )
                    if self.store._absolute(expected).exists():
                        raise RetentionFailure("storage_absence_unconfirmed")
                await session.delete(application)
        elif resource_type == "contact_request":
            contact_request = await session.get(ContactRequest, task.resource_id)
            if contact_request is not None:
                await session.delete(contact_request)
        elif resource_type == "message":
            message = await session.get(Message, task.resource_id)
            if message is not None:
                await session.delete(message)
        elif resource_type == "notification":
            notification = await session.get(Notification, task.resource_id)
            if notification is not None:
                await session.delete(notification)
        elif resource_type == "conversation":
            if await session.scalar(
                select(Message.id).where(Message.conversation_id == task.resource_id).limit(1)
            ):
                raise RetentionFailure("dependency_pending")
            conversation = await session.get(Conversation, task.resource_id)
            if conversation is not None:
                await session.delete(conversation)
        elif resource_type == "connection":
            if await session.scalar(
                select(Conversation.id)
                .where(Conversation.connection_id == task.resource_id)
                .limit(1)
            ):
                raise RetentionFailure("dependency_pending")
            connection = await session.get(Connection, task.resource_id)
            if connection is not None:
                await session.delete(connection)
        elif resource_type == "connection_request":
            if await session.scalar(
                select(Connection.id)
                .where(Connection.connection_request_id == task.resource_id)
                .limit(1)
            ):
                raise RetentionFailure("dependency_pending")
            connection_request = await session.get(ConnectionRequest, task.resource_id)
            if connection_request is not None:
                await session.delete(connection_request)
        elif resource_type == "moderation_case":
            await self._dispose_moderation_case(session, task.resource_id)
        else:
            evidence_verification_id = await self._dispose_evidence(session, task.resource_id)
        await session.flush()
        await self._dispose_residues(
            session,
            task,
            evidence_verification_id
            if resource_type == "organization_verification_evidence"
            else None,
        )

    async def _dispose_moderation_case(self, session: AsyncSession, case_id: str) -> None:
        case = await session.get(ModerationCase, case_id)
        if case is None:
            return
        if (
            case.status
            not in {
                "dismissed",
                "withheld",
                "appeal_upheld",
                "appeal_overturned",
                "legacy_withheld",
                "legacy_withdrawn",
            }
            or case.sensitive_purged_at is not None
        ):
            raise RetentionFailure("moderation_case_active")
        await session.execute(
            update(PostReport).where(PostReport.case_id == case.id).values(narrative=None)
        )
        await session.execute(
            update(ModerationDecision)
            .where(ModerationDecision.case_id == case.id)
            # The content-free evidence snapshot digest is durable provenance
            # and is intentionally not part of sensitive-content disposal.
            .values(internal_rationale=None, evidence=None)
        )
        await session.execute(
            update(ModerationAppeal)
            .where(ModerationAppeal.case_id == case.id)
            # Preserve review_snapshot_sha256 for the same reason.
            .values(rationale=None, internal_rationale=None)
        )
        case.sensitive_purged_at = datetime.now(UTC)
        case.updated_at = case.sensitive_purged_at
        session.add(
            ModerationAuditEvent(
                id=new_id(),
                case_id=case.id,
                post_id=case.post_id,
                event_type="sensitive_purged",
                actor_id="system:retention",
                actor_role="system",
                safe_metadata="{}",
                occurred_at=case.sensitive_purged_at,
            )
        )

    async def _dispose_evidence(self, session: AsyncSession, evidence_id: str) -> str | None:
        evidence = await session.get(OrganizationVerificationEvidence, evidence_id)
        if evidence is None:
            return None
        verification = await session.get(OrganizationVerification, evidence.verification_id)
        if verification is None:
            raise RetentionFailure("verification_missing")
        expected_path = (
            f"verification-evidence/{verification.organization_id}/{verification.id}/"
            f"{evidence.artifact_sha256}.bin"
        )
        if evidence.storage_path != expected_path:
            raise RetentionFailure("storage_path_invalid")
        self.store.delete_verified_exact(
            expected_path,
            evidence.artifact_sha256,
            expected_size_bytes=evidence.artifact_size_bytes,
            max_size_bytes=262_144,
        )
        await session.delete(evidence)
        return verification.id

    async def _dispose_residues(
        self, session: AsyncSession, task: LifecycleTask, evidence_verification_id: str | None
    ) -> None:
        resource_type = task.resource_type
        resource_id = task.resource_id
        if resource_type == "organization_verification_evidence":
            await session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.resource_type == resource_type,
                    IdempotencyRecord.resource_id == resource_id,
                )
            )
            await session.execute(
                delete(ChangeEvent).where(
                    ChangeEvent.resource_type == resource_type,
                    ChangeEvent.resource_id == resource_id,
                )
            )
            if evidence_verification_id is None:
                return
            resource_type = "organization_verification"
            resource_id = evidence_verification_id
        await session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.resource_type == resource_type,
                IdempotencyRecord.resource_id == resource_id,
            )
        )
        await session.execute(
            delete(ChangeEvent).where(
                ChangeEvent.resource_type == resource_type,
                ChangeEvent.resource_id == resource_id,
            )
        )
