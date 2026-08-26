"""Disabled-by-default local executor for confirmed account-erasure inventories.

This service is deliberately independent from the generic retention worker.  It only
acts on durable AccountErasureItem rows created during hidden lifecycle confirmation.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote

import httpx
from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import (
    AuthenticationUnavailable,
    decrypt_lifecycle_provider_session,
    decrypt_lifecycle_provider_subject,
)
from app.config import Settings
from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountBackupAuthority,
    AccountBackupManifest,
    AccountBackupObligation,
    AccountErasureFileProof,
    AccountErasureItem,
    AccountLifecycle,
    AccountLifecycleReceiptRateLimit,
    AccountLifecycleTombstone,
    AgentGrant,
    AgentIdentity,
    AgentMandate,
    AgentOutreachRecipientRateBucket,
    AgentProposal,
    ApiKey,
    Application,
    ApplicationRateBucket,
    ChangeEvent,
    Connection,
    ConnectionBlock,
    ConnectionRequest,
    ConnectionRequestRateBucket,
    ContactBlock,
    ContactPolicy,
    ContactRateBucket,
    ContactRequest,
    Conversation,
    Document,
    DocumentVersion,
    FollowRateBucket,
    IdempotencyRecord,
    IdentifierReservation,
    Job,
    Message,
    MessageRateBucket,
    ModerationAppeal,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    Notification,
    Organization,
    OrganizationMembership,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
    Post,
    PostContentBlock,
    PostGraphPairLock,
    PostModerationEvent,
    PostRateBucket,
    PostReport,
    PostReportRateBucket,
    PostVersion,
    ProfileFollow,
    RetentionHold,
    SearchProjectionTask,
)
from app.services.deletion_journal import (
    DeletionCommitmentJournal,
    DeletionJournalError,
    verify_live_deletion_mirror,
)
from app.services.exact_search import ExactSearchService, ExactSearchUnavailable
from app.services.hold_lineage import hold_ancestors
from app.services.reservations import identifier_reservation_hmac
from app.services.search import MeiliSearchProjection, SearchUnavailable
from app.services.storage import StorageIntegrityError, VersionStore
from app.services.taxonomy import remove_document_projection

ProviderOutcome = Literal["deleted", "already_absent", "retryable", "permanent_unsupported"]


class ClerkLifecycleProvider(Protocol):
    async def revoke_sessions(
        self, *, subject: str, current_session_id: str
    ) -> ProviderOutcome: ...

    async def delete_user(self, *, subject: str) -> ProviderOutcome: ...


class HttpClerkLifecycleProvider:
    """Narrow Clerk Backend API adapter with no response persistence or logging."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        assert self.settings.clerk_backend_secret is not None
        return {"Authorization": f"Bearer {self.settings.clerk_backend_secret}"}

    def _url(self, suffix: str) -> str:
        assert self.settings.clerk_backend_base_url is not None
        return str(self.settings.clerk_backend_base_url).rstrip("/") + suffix

    def _is_configured(self) -> bool:
        try:
            self.settings.require_clerk_backend_configuration()
        except ValueError:
            return False
        return True

    async def check_ready(self) -> None:
        """Verify the deletion credential without retaining provider content."""
        if not self._is_configured():
            raise AuthenticationUnavailable("account lifecycle provider is not configured")
        try:
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
                response = await client.get(
                    self._url("/v1/users"),
                    headers=self._headers(),
                    params={"limit": 1},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthenticationUnavailable("account lifecycle provider readiness failed") from exc

    @staticmethod
    def _outcome(status_code: int) -> ProviderOutcome:
        if status_code in {401, 403, 404, 422}:
            return "permanent_unsupported"
        return "retryable"

    async def revoke_sessions(self, *, subject: str, current_session_id: str) -> ProviderOutcome:
        if not self._is_configured():
            return "permanent_unsupported"
        session_ids = {current_session_id}
        offset = 0
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                while True:
                    response = await client.get(
                        self._url("/v1/sessions"),
                        headers=self._headers(),
                        params={"user_id": subject, "limit": 100, "offset": offset},
                    )
                    if not response.is_success:
                        return self._outcome(response.status_code)
                    payload = response.json()
                    rows = payload.get("data") if isinstance(payload, dict) else None
                    if not isinstance(rows, list):
                        return "retryable"
                    for row in rows:
                        session_id = row.get("id") if isinstance(row, dict) else None
                        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 255:
                            return "retryable"
                        session_ids.add(session_id)
                    if len(rows) < 100:
                        break
                    offset += len(rows)
                    if offset > 10_000:
                        return "retryable"
                for session_id in sorted(session_ids):
                    revoked = await client.post(
                        self._url(f"/v1/sessions/{quote(session_id, safe='')}/revoke"),
                        headers=self._headers(),
                    )
                    if not revoked.is_success and revoked.status_code != 404:
                        return self._outcome(revoked.status_code)
                return "deleted"
        except (httpx.HTTPError, ValueError):
            return "retryable"

    async def delete_user(self, *, subject: str) -> ProviderOutcome:
        if not self._is_configured():
            return "permanent_unsupported"
        encoded_subject = quote(subject, safe="")
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                deleted = await client.delete(
                    self._url(f"/v1/users/{encoded_subject}"), headers=self._headers()
                )
                if deleted.status_code == 404:
                    return "already_absent"
                if not deleted.is_success:
                    return self._outcome(deleted.status_code)
                absence = await client.get(
                    self._url(f"/v1/users/{encoded_subject}"), headers=self._headers()
                )
                return "deleted" if absence.status_code == 404 else "retryable"
        except httpx.HTTPError:
            return "retryable"


class AccountErasureFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, retry_at: datetime | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_at = retry_at


@dataclass(frozen=True)
class AccountErasureRunResult:
    claimed: int = 0
    completed: int = 0
    held: int = 0
    retried: int = 0
    dead_lettered: int = 0


@dataclass(frozen=True)
class _Claim:
    id: str
    deletion_id: str
    resource_type: str
    resource_id: str
    phase: str
    claim_token: str
    attempts: int


# A parent may not run until every queued child type has completed.  This is a
# conservative inventory barrier; unrelated rows only make progress later.
_CHILD_TYPES: dict[str, frozenset[str]] = {
    "agent_mandate": frozenset({"agent_grant"}),
    "agent_identity": frozenset({"agent_grant", "agent_mandate"}),
    "document": frozenset({"document_version", "post", "agent_identity"}),
    "post": frozenset(
        {
            "post_version",
            "post_report",
            "moderation_case",
            "moderation_decision",
            "moderation_appeal",
            "moderation_audit_event",
            "post_moderation_event",
        }
    ),
    "conversation": frozenset({"message"}),
    "connection": frozenset({"conversation"}),
    "connection_request": frozenset({"connection"}),
    "organization_verification": frozenset(
        {"organization_verification_evidence", "organization_verification_event"}
    ),
    "job": frozenset({"application"}),
    "organization": frozenset({"job", "organization_membership", "organization_verification"}),
}


class AccountErasureExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        store: VersionStore,
        search: MeiliSearchProjection,
        provider: ClerkLifecycleProvider,
        settings: Settings,
        *,
        worker_id: str,
        max_attempts: int = 3,
        lease_seconds: int = 60,
    ) -> None:
        self.session_factory = session_factory
        self.store = store
        self.search = search
        self.provider = provider
        self.settings = settings
        self.exact_search = ExactSearchService(settings)
        self.worker_id = worker_id
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds

    async def run_once(
        self, *, limit: int = 100, now: datetime | None = None
    ) -> AccountErasureRunResult:
        if not self.settings.account_lifecycle_enabled:
            raise RuntimeError("account lifecycle executor is disabled")
        if limit < 1 or limit > 1000:
            raise ValueError("account erasure limit must be between 1 and 1000")
        current = self._utc(now or datetime.now(UTC))
        result = AccountErasureRunResult()
        await self._requeue_explicitly_released_retention_holds(current)
        for _ in range(limit):
            claim, held = await self._claim_one(current)
            if held:
                result = AccountErasureRunResult(
                    claimed=result.claimed,
                    completed=result.completed,
                    held=result.held + held,
                    retried=result.retried,
                    dead_lettered=result.dead_lettered,
                )
            if claim is None:
                # A held canonical row must not prevent the same pass from
                # concealing/unindexing independent public projections.
                if held:
                    continue
                break
            outcome = await self._process(claim, current)
            result = AccountErasureRunResult(
                claimed=result.claimed + 1,
                completed=result.completed + (outcome == "completed"),
                held=result.held,
                retried=result.retried + (outcome == "retried"),
                dead_lettered=result.dead_lettered + (outcome == "dead_lettered"),
            )
        await self._reconcile_all(current)
        return result

    async def _requeue_explicitly_released_retention_holds(self, now: datetime) -> None:
        """Only an explicit release may revive a retention-held item."""
        async with self.session_factory() as session:
            items = (
                await session.scalars(
                    select(AccountErasureItem)
                    .where(
                        AccountErasureItem.state == "held",
                        AccountErasureItem.hold_kind == "retention",
                    )
                    .with_for_update()
                )
            ).all()
            changed = False
            for item in items:
                if item.hold_id is None:
                    item.state = "dead_letter"
                    item.last_error_code = "retention_hold_reference_invalid"
                    item.updated_at = now
                    changed = True
                    continue
                hold = await session.get(RetentionHold, item.hold_id, with_for_update=True)
                if hold is None or not await self._hold_covers_item(session, hold, item):
                    item.state = "dead_letter"
                    item.last_error_code = "retention_hold_reference_invalid"
                    item.updated_at = now
                    changed = True
                elif hold.released_at is not None:
                    item.state = "queued"
                    item.disposition = "delete"
                    item.hold_kind = None
                    item.hold_id = None
                    item.hold_review_at = None
                    item.available_at = now
                    item.lease_expires_at = None
                    item.claimed_by = None
                    item.claim_token = None
                    item.last_error_code = "retention_hold_released"
                    item.updated_at = now
                    changed = True
            if changed:
                await session.commit()
            else:
                await session.rollback()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    async def _claim_one(self, now: datetime) -> tuple[_Claim | None, int]:
        async with self.session_factory() as session:
            dialect = session.get_bind().dialect.name
            statement = (
                select(AccountErasureItem)
                .where(
                    or_(
                        and_(
                            AccountErasureItem.state == "queued",
                            AccountErasureItem.available_at <= now,
                        ),
                        and_(
                            AccountErasureItem.state == "leased",
                            AccountErasureItem.lease_expires_at.is_not(None),
                            AccountErasureItem.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(
                    AccountErasureItem.available_at,
                    AccountErasureItem.created_at,
                    AccountErasureItem.id,
                )
                .limit(100)
            )
            if dialect == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = (await session.scalars(statement)).all()
            held = 0
            for item in rows:
                lifecycle = await session.get(
                    AccountLifecycle, item.deletion_id, with_for_update=True
                )
                if lifecycle is None or lifecycle.state == "fully_erased":
                    continue
                hold = None if item.phase == "unindex" else await self._active_hold(session, item)
                if hold is not None:
                    item.state = "held"
                    item.disposition = "hold"
                    item.hold_kind = "retention"
                    item.hold_id = hold.id
                    item.hold_review_at = hold.review_at
                    item.available_at = None
                    item.lease_expires_at = None
                    item.claimed_by = None
                    item.claim_token = None
                    item.last_error_code = "retention_hold_active"
                    item.updated_at = now
                    await session.commit()
                    await self._reconcile(item.deletion_id, now)
                    return None, 1
                if item.phase != "unindex" and await self._active_policy_ancestor(session, item):
                    await self._mark_policy_held(session, item, now)
                    await self._reconcile(item.deletion_id, now)
                    return None, 1
                if not await self._prerequisites_complete(session, item):
                    continue
                token = secrets.token_hex(16)
                changed = await session.execute(
                    update(AccountErasureItem)
                    .where(
                        AccountErasureItem.id == item.id,
                        AccountErasureItem.state.in_(["queued", "leased"]),
                        or_(
                            AccountErasureItem.state == "queued",
                            AccountErasureItem.lease_expires_at <= now,
                        ),
                    )
                    .values(
                        state="leased",
                        attempts=AccountErasureItem.attempts + 1,
                        lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                        claimed_by=self.worker_id,
                        claim_token=token,
                        updated_at=now,
                    )
                )
                if cast(Any, changed).rowcount != 1:
                    await session.rollback()
                    continue
                await session.commit()
                return (
                    _Claim(
                        id=item.id,
                        deletion_id=item.deletion_id,
                        resource_type=item.resource_type,
                        resource_id=item.resource_id,
                        phase=item.phase,
                        claim_token=token,
                        attempts=item.attempts + 1,
                    ),
                    held,
                )
            await session.rollback()
        return None, held

    async def _active_hold(
        self, session: AsyncSession, item: AccountErasureItem
    ) -> RetentionHold | None:
        # An unreleased legal hold never auto-expires.  The nominal expiry is a
        # review deadline, not authorization to destroy preserved evidence.
        direct = await session.scalar(
            select(RetentionHold)
            .where(
                RetentionHold.resource_type == item.resource_type,
                RetentionHold.resource_id == item.resource_id,
                RetentionHold.released_at.is_(None),
            )
            .with_for_update()
        )
        if direct is not None:
            return direct
        for resource_type, resource_id in await hold_ancestors(
            session, item.resource_type, item.resource_id
        ):
            ancestor_hold = await session.scalar(
                select(RetentionHold)
                .where(
                    RetentionHold.resource_type == resource_type,
                    RetentionHold.resource_id == resource_id,
                    RetentionHold.released_at.is_(None),
                )
                .with_for_update()
            )
            if ancestor_hold is not None:
                return ancestor_hold
        return None

    async def _hold_covers_item(
        self, session: AsyncSession, hold: RetentionHold, item: AccountErasureItem
    ) -> bool:
        if hold.resource_type == item.resource_type and hold.resource_id == item.resource_id:
            return True
        return (hold.resource_type, hold.resource_id) in await hold_ancestors(
            session, item.resource_type, item.resource_id
        )

    async def _active_policy_ancestor(
        self, session: AsyncSession, item: AccountErasureItem
    ) -> bool:
        for resource_type, resource_id in await hold_ancestors(
            session, item.resource_type, item.resource_id
        ):
            if (
                await session.scalar(
                    select(AccountErasureItem.id)
                    .where(
                        AccountErasureItem.deletion_id == item.deletion_id,
                        AccountErasureItem.resource_type == resource_type,
                        AccountErasureItem.resource_id == resource_id,
                        AccountErasureItem.phase == "delete_row",
                        AccountErasureItem.state == "held",
                        AccountErasureItem.hold_kind == "policy",
                    )
                    .with_for_update()
                )
            ) is not None:
                return True
        return False

    async def _prerequisites_complete(
        self, session: AsyncSession, item: AccountErasureItem
    ) -> bool:
        if item.phase == "backup":
            return True
        if item.resource_type == "provider_session":
            return True
        if await self._incomplete_items(
            session,
            item.deletion_id,
            resource_type="provider_session",
            include_phases=frozenset({"provider"}),
        ):
            return False
        if item.phase == "postcheck":
            return not await self._incomplete_items(
                session,
                item.deletion_id,
                exclude_phases=frozenset({"postcheck", "provider", "backup"}),
                exclude_resource_types=frozenset({"provider_subject_ciphertext"}),
            )
        if item.phase == "provider":
            if await self._incomplete_items(
                session,
                item.deletion_id,
                include_phases=frozenset({"postcheck"}),
            ):
                return False
            return not await self._incomplete_items(
                session,
                item.deletion_id,
                resource_type="provider_session",
                include_phases=frozenset({"provider"}),
            )
        if item.resource_type == "provider_subject_ciphertext":
            lifecycle = await session.get(AccountLifecycle, item.deletion_id)
            if lifecycle is None or lifecycle.provider_state != "verified":
                return False
            return not await self._incomplete_items(
                session,
                item.deletion_id,
                resource_type="provider_user",
                include_phases=frozenset({"provider"}),
            )
        if item.phase == "delete_row":
            if item.resource_type in {
                "document_version",
                "post_version",
                "organization_verification_evidence",
                "application",
            }:
                if await self._incomplete_items(
                    session,
                    item.deletion_id,
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    include_phases=frozenset({"delete_file"}),
                ):
                    return False
            if item.resource_type in {"document", "post"} and await self._incomplete_items(
                session,
                item.deletion_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                include_phases=frozenset({"unindex"}),
            ):
                return False
            children = _CHILD_TYPES.get(item.resource_type, frozenset())
            if children and await self._incomplete_items(
                session, item.deletion_id, resource_types=children
            ):
                return False
        return True

    async def _incomplete_items(
        self,
        session: AsyncSession,
        deletion_id: str,
        *,
        resource_type: str | None = None,
        resource_types: frozenset[str] | None = None,
        resource_id: str | None = None,
        phase: str | None = None,
        include_phases: frozenset[str] | None = None,
        exclude_phases: frozenset[str] | None = None,
        exclude_resource_types: frozenset[str] | None = None,
    ) -> bool:
        conditions = [
            AccountErasureItem.deletion_id == deletion_id,
            AccountErasureItem.state != "completed",
        ]
        if resource_type is not None:
            conditions.append(AccountErasureItem.resource_type == resource_type)
        if resource_types is not None:
            conditions.append(AccountErasureItem.resource_type.in_(resource_types))
        if resource_id is not None:
            conditions.append(AccountErasureItem.resource_id == resource_id)
        if phase is not None:
            conditions.append(AccountErasureItem.phase == phase)
        if include_phases is not None:
            conditions.append(AccountErasureItem.phase.in_(include_phases))
        if exclude_phases is not None:
            conditions.append(AccountErasureItem.phase.not_in(exclude_phases))
        if exclude_resource_types is not None:
            conditions.append(AccountErasureItem.resource_type.not_in(exclude_resource_types))
        return (
            await session.scalar(select(AccountErasureItem.id).where(*conditions).limit(1))
            is not None
        )

    async def _process(self, claim: _Claim, now: datetime) -> str:
        if claim.phase == "unindex" or claim.phase == "provider":
            return await self._process_network_action(claim, now)
        try:
            async with self.session_factory() as session:
                await self._acquire_hold_guard(session)
                item = await self._leased_item(session, claim)
                if item is None:
                    return "retried"
                hold = None if item.phase == "unindex" else await self._active_hold(session, item)
                if hold is not None:
                    await self._mark_held(session, item, hold, now)
                    return "retried"
                if item.phase != "unindex" and await self._active_policy_ancestor(session, item):
                    await self._mark_policy_held(session, item, now)
                    return "retried"
                attestation = await self._execute(session, item, now)
                await self._complete(session, item, now, attestation)
            await self._reconcile(claim.deletion_id, now)
            return "completed"
        except AccountErasureFailure as exc:
            return await self._fail(claim, exc, now)
        except (
            StorageIntegrityError,
            SearchUnavailable,
            ExactSearchUnavailable,
            AuthenticationUnavailable,
        ):
            return await self._fail(
                claim, AccountErasureFailure("erasure_dependency_unavailable", retryable=True), now
            )
        except (IntegrityError, OSError, ValueError):
            return await self._fail(
                claim, AccountErasureFailure("erasure_persistence_conflict", retryable=True), now
            )
        except Exception:
            # Do not persist exception text: drivers and providers can include
            # private record content in their messages.
            return await self._fail(
                claim, AccountErasureFailure("erasure_execution_failed", retryable=True), now
            )

    async def _process_network_action(self, claim: _Claim, now: datetime) -> str:
        """Perform provider/search I/O after releasing the database lease transaction."""
        try:
            subject: str | None = None
            session_id: str | None = None
            async with self.session_factory() as session:
                await self._acquire_hold_guard(session)
                item = await self._leased_item(session, claim)
                if item is None:
                    return "retried"
                hold = None if item.phase == "unindex" else await self._active_hold(session, item)
                if hold is not None:
                    await self._mark_held(session, item, hold, now)
                    await self._reconcile(item.deletion_id, now)
                    return "retried"
                if claim.phase == "provider":
                    lifecycle = await session.get(AccountLifecycle, claim.deletion_id)
                    if lifecycle is None or lifecycle.provider_subject_ciphertext is None:
                        raise AccountErasureFailure(
                            "provider_ciphertext_unavailable", retryable=False
                        )
                    subject = decrypt_lifecycle_provider_subject(
                        self.settings,
                        deletion_id=claim.deletion_id,
                        ciphertext=lifecycle.provider_subject_ciphertext,
                    )
                    if claim.resource_type == "provider_session":
                        if lifecycle.provider_session_ciphertext is None:
                            raise AccountErasureFailure(
                                "provider_session_ciphertext_unavailable", retryable=False
                            )
                        session_id = decrypt_lifecycle_provider_session(
                            self.settings,
                            deletion_id=claim.deletion_id,
                            ciphertext=lifecycle.provider_session_ciphertext,
                        )
                    elif claim.resource_type != "provider_user":
                        raise AccountErasureFailure("provider_plan_unsupported", retryable=False)
                await session.rollback()
            if claim.phase == "unindex":
                attestation = await self.search.delete_document(claim.resource_id)
                if not attestation.configured or attestation.state not in {"deleted", "absent"}:
                    raise AccountErasureFailure("erasure_dependency_unavailable", retryable=True)
                completion_code = "search_absence_attested"
                provider_outcome: ProviderOutcome | None = None
            elif claim.resource_type == "provider_session":
                assert subject is not None and session_id is not None
                provider_outcome = await self.provider.revoke_sessions(
                    subject=subject, current_session_id=session_id
                )
                completion_code = "provider_sessions_revoked"
            else:
                assert subject is not None
                provider_outcome = await self.provider.delete_user(subject=subject)
                completion_code = "provider_absence_attested"
            async with self.session_factory() as session:
                await self._acquire_hold_guard(session)
                item = await self._leased_item(session, claim)
                if item is None:
                    return "retried"
                hold = None if item.phase == "unindex" else await self._active_hold(session, item)
                if hold is not None:
                    await self._mark_held(session, item, hold, now)
                    await self._reconcile(item.deletion_id, now)
                    return "retried"
                if provider_outcome is not None:
                    if provider_outcome not in {"deleted", "already_absent"}:
                        if claim.resource_type == "provider_user":
                            lifecycle = await session.get(AccountLifecycle, claim.deletion_id)
                            if (
                                lifecycle is not None
                                and provider_outcome == "permanent_unsupported"
                            ):
                                lifecycle.provider_state = "unsupported"
                        raise AccountErasureFailure(
                            "provider_unsupported"
                            if provider_outcome == "permanent_unsupported"
                            else "provider_retryable",
                            retryable=provider_outcome != "permanent_unsupported",
                        )
                    if claim.resource_type == "provider_user":
                        lifecycle = await session.get(
                            AccountLifecycle, claim.deletion_id, with_for_update=True
                        )
                        if lifecycle is None:
                            raise AccountErasureFailure(
                                "erasure_lifecycle_missing", retryable=False
                            )
                        lifecycle.provider_state = "verified"
                await self._complete(session, item, now, completion_code)
            await self._reconcile(claim.deletion_id, now)
            return "completed"
        except AccountErasureFailure as exc:
            return await self._fail(claim, exc, now)
        except (SearchUnavailable, AuthenticationUnavailable):
            return await self._fail(
                claim, AccountErasureFailure("erasure_dependency_unavailable", retryable=True), now
            )
        except Exception:
            # Keep unexpected provider/search failures content-free while
            # releasing the claim through the bounded retry/dead-letter path.
            return await self._fail(
                claim, AccountErasureFailure("erasure_execution_failed", retryable=True), now
            )

    async def _leased_item(self, session: AsyncSession, claim: _Claim) -> AccountErasureItem | None:
        return await session.scalar(
            select(AccountErasureItem)
            .where(
                AccountErasureItem.id == claim.id,
                AccountErasureItem.state == "leased",
                AccountErasureItem.claim_token == claim.claim_token,
            )
            .with_for_update()
        )

    async def _acquire_hold_guard(self, session: AsyncSession) -> None:
        """Share the SQLite-safe confirmation/hold serialization point."""
        await session.execute(
            update(AccountBackupAuthority)
            .where(AccountBackupAuthority.id == ACCOUNT_BACKUP_AUTHORITY_ID)
            .values(updated_at=AccountBackupAuthority.updated_at)
        )
        authority = await session.get(
            AccountBackupAuthority, ACCOUNT_BACKUP_AUTHORITY_ID, with_for_update=True
        )
        if authority is None:
            raise AccountErasureFailure("backup_authority_missing", retryable=True)

    async def _mark_held(
        self, session: AsyncSession, item: AccountErasureItem, hold: RetentionHold, now: datetime
    ) -> None:
        item.state = "held"
        item.disposition = "hold"
        item.hold_kind = "retention"
        item.hold_id = hold.id
        item.hold_review_at = hold.review_at
        item.available_at = None
        item.lease_expires_at = None
        item.claimed_by = None
        item.claim_token = None
        item.last_error_code = "retention_hold_active"
        item.updated_at = now
        await session.commit()

    async def _mark_policy_held(
        self, session: AsyncSession, item: AccountErasureItem, now: datetime
    ) -> None:
        item.state = "held"
        item.disposition = "hold"
        item.hold_kind = "policy"
        item.hold_id = None
        item.hold_review_at = None
        item.available_at = None
        item.lease_expires_at = None
        item.claimed_by = None
        item.claim_token = None
        item.last_error_code = "policy_parent_held"
        item.updated_at = now
        await session.commit()

    async def _execute(
        self, session: AsyncSession, item: AccountErasureItem, now: datetime
    ) -> str | None:
        if item.phase == "delete_file":
            await self._delete_file(session, item, now)
            return "file_absence_attested"
        if item.resource_type == "provider_subject_ciphertext" and item.phase == "delete_row":
            lifecycle = await session.get(AccountLifecycle, item.deletion_id, with_for_update=True)
            if lifecycle is None or lifecycle.provider_state != "verified":
                raise AccountErasureFailure("provider_not_verified", retryable=True)
            lifecycle.provider_subject_ciphertext = None
            lifecycle.provider_session_ciphertext = None
            return "provider_ciphertext_erased"
        if item.phase == "delete_row":
            await self._delete_row(session, item, now)
            return None
        if item.phase == "postcheck":
            await self._postcheck(session, item.deletion_id)
            return "postcheck_attested"
        if item.phase == "backup":
            return await self._verify_backup(session, item, now)
        raise AccountErasureFailure("erasure_plan_unsupported", retryable=False)

    async def _delete_file(
        self, session: AsyncSession, item: AccountErasureItem, now: datetime
    ) -> None:
        expected: str
        stored: str
        expected_sha256: str | None = None
        expected_size_bytes: int | None = None
        max_size_bytes: int | None = None
        verified_deletion_required = False
        row: Any
        if item.resource_type == "document_version":
            row = await session.get(DocumentVersion, item.resource_id)
            if row is None:
                return
            document = await session.get(Document, row.document_id)
            if document is None:
                raise AccountErasureFailure("storage_parent_missing", retryable=False)
            expected = self.store.relative_path(document.kind, document.id, row.version)
            stored = row.storage_path
        elif item.resource_type == "post_version":
            row = await session.get(PostVersion, item.resource_id)
            if row is None:
                return
            expected = self.store.relative_path("post", row.post_id, row.version)
            stored = row.storage_path
        elif item.resource_type == "organization_verification_evidence":
            row = await session.get(OrganizationVerificationEvidence, item.resource_id)
            if row is None:
                return
            verification = await session.get(OrganizationVerification, row.verification_id)
            if verification is None:
                raise AccountErasureFailure("storage_parent_missing", retryable=False)
            expected = f"verification-evidence/{verification.organization_id}/{verification.id}/{row.artifact_sha256}.bin"
            stored = row.storage_path
            expected_sha256 = row.artifact_sha256
            expected_size_bytes = row.artifact_size_bytes
            max_size_bytes = 262_144
            verified_deletion_required = True
        elif item.resource_type == "application":
            row = await session.get(Application, item.resource_id)
            if row is None:
                return
            expected = self.store.application_snapshot_relative_path(row.id)
            stored = row.snapshot_storage_path
            expected_sha256 = row.snapshot_sha256
            expected_size_bytes = row.snapshot_size_bytes
            max_size_bytes = 131_072
            verified_deletion_required = True
        else:
            raise AccountErasureFailure("erasure_file_plan_unsupported", retryable=False)
        if stored != expected:
            raise AccountErasureFailure("storage_path_mismatch", retryable=False)
        if verified_deletion_required:
            if not expected_sha256 or expected_size_bytes is None or max_size_bytes is None:
                raise AccountErasureFailure("storage_metadata_invalid", retryable=False)
            self.store.delete_verified_exact(
                expected,
                expected_sha256,
                expected_size_bytes=expected_size_bytes,
                max_size_bytes=max_size_bytes,
            )
        else:
            self.store.delete_exact(expected)
        if self.store._absolute(expected).exists():
            raise AccountErasureFailure("storage_absence_unconfirmed", retryable=True)
        proof = await session.scalar(
            select(AccountErasureFileProof)
            .where(
                AccountErasureFileProof.deletion_id == item.deletion_id,
                AccountErasureFileProof.resource_type == item.resource_type,
                AccountErasureFileProof.resource_id == item.resource_id,
            )
            .with_for_update()
        )
        if proof is None:
            session.add(
                AccountErasureFileProof(
                    deletion_id=item.deletion_id,
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    relative_path=expected,
                    deleted_at=now,
                )
            )

    async def _delete_row(
        self, session: AsyncSession, item: AccountErasureItem, now: datetime
    ) -> None:
        if item.resource_type == "contact_policy":
            lifecycle = await session.get(AccountLifecycle, item.deletion_id)
            if lifecycle is None or lifecycle.provider_subject_ciphertext is None:
                raise AccountErasureFailure("provider_ciphertext_unavailable", retryable=False)
            subject = decrypt_lifecycle_provider_subject(
                self.settings,
                deletion_id=lifecycle.id,
                ciphertext=lifecycle.provider_subject_ciphertext,
            )
            row = await session.get(ContactPolicy, subject)
        else:
            model: type[Any] | None = {
                "document": Document,
                "document_version": DocumentVersion,
                "post": Post,
                "post_version": PostVersion,
                "api_key": ApiKey,
                "agent_grant": AgentGrant,
                "agent_mandate": AgentMandate,
                "agent_identity": AgentIdentity,
                "connection_request": ConnectionRequest,
                "connection": Connection,
                "conversation": Conversation,
                "message": Message,
                "organization": Organization,
                "job": Job,
                "application": Application,
                "organization_membership": OrganizationMembership,
                "agent_proposal": AgentProposal,
            }.get(item.resource_type)
            if model is None:
                raise AccountErasureFailure("erasure_row_plan_unsupported", retryable=False)
            row = await session.get(
                model,
                item.resource_id,
                with_for_update=model is Document,
            )
        if row is None:
            return
        await self._reserve_identifier(session, item.deletion_id, row, now)
        if isinstance(row, Document):
            # The earlier remote unindex is only an erasure-stage attestation:
            # a previously claimed projection task can still replay afterward.
            # Re-arm the exact-version tombstone in the canonical delete
            # transaction so the sole projection worker confirms final absence.
            task = await session.get(
                SearchProjectionTask,
                (row.id, row.current_version),
                with_for_update=True,
            )
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:document_id, 0))"),
                    {"document_id": row.id},
                )
            if task is None:
                session.add(
                    SearchProjectionTask(
                        document_id=row.id,
                        version=row.current_version,
                        state="pending",
                        attempts=0,
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                task.state = "pending"
                task.attempts = 0
                task.available_at = now
                task.lease_expires_at = None
                task.claimed_by = None
                task.claim_token = None
                task.last_error_code = None
                task.dead_lettered_at = None
                task.updated_at = now
            await remove_document_projection(session, row.id)
            await self.exact_search.remove_document(session, row.id)
        await session.delete(row)

    async def _reserve_identifier(
        self, session: AsyncSession, deletion_id: str, row: object, now: datetime
    ) -> None:
        namespace: str | None = None
        identifier: str | None = None
        if isinstance(row, Document):
            namespace, identifier = f"document:{row.kind}", row.public_identifier
        elif isinstance(row, AgentIdentity):
            namespace, identifier = "agent_identity", row.handle
        elif isinstance(row, Organization):
            namespace, identifier = "organization", row.slug
        elif isinstance(row, Job):
            namespace, identifier = f"job:{row.organization_id}", row.slug
        if namespace is None or identifier is None:
            return
        digest = identifier_reservation_hmac(
            self.settings, namespace=namespace, identifier=identifier
        )
        existing = await session.scalar(
            select(IdentifierReservation)
            .where(
                IdentifierReservation.namespace == namespace,
                IdentifierReservation.identifier_hmac == digest,
            )
            .with_for_update()
        )
        if existing is None:
            session.add(
                IdentifierReservation(
                    namespace=namespace,
                    identifier_hmac=digest,
                    deletion_id=deletion_id,
                    created_at=now,
                )
            )
            await session.flush()

    async def _verify_backup(
        self, session: AsyncSession, item: AccountErasureItem, now: datetime
    ) -> str | None:
        manifest = await session.get(AccountBackupManifest, item.resource_id, with_for_update=True)
        if manifest is None:
            raise AccountErasureFailure("backup_manifest_missing", retryable=False)
        obligation = await session.scalar(
            select(AccountBackupObligation)
            .where(
                AccountBackupObligation.deletion_id == item.deletion_id,
                AccountBackupObligation.generation_id == manifest.generation_id,
            )
            .with_for_update()
        )
        if obligation is None or any(
            value is None
            for value in (
                obligation.generation_created_at,
                obligation.generation_expires_at,
                obligation.db_manifest_digest,
                obligation.markdown_manifest_digest,
            )
        ):
            raise AccountErasureFailure("backup_snapshot_missing", retryable=False)
        assert obligation.generation_created_at is not None
        assert obligation.generation_expires_at is not None
        assert obligation.db_manifest_digest is not None
        assert obligation.markdown_manifest_digest is not None
        generation_created_at = self._utc(obligation.generation_created_at)
        generation_expires_at = self._utc(obligation.generation_expires_at)
        if (
            self._utc(manifest.created_at) != generation_created_at
            or self._utc(manifest.expires_at) != generation_expires_at
            or manifest.db_manifest_digest != obligation.db_manifest_digest
            or manifest.markdown_manifest_digest != obligation.markdown_manifest_digest
        ):
            raise AccountErasureFailure("backup_snapshot_mismatch", retryable=False)
        reason: str
        proof_time: datetime
        if (
            manifest.state == "expired"
            and manifest.expired_proof_digest is not None
            and manifest.expired_at is not None
            and self._utc(manifest.expired_at) <= now
            and generation_expires_at <= now
        ):
            reason, proof_time = "expired", self._utc(manifest.expired_at)
        elif (
            manifest.state == "crypto_destroyed"
            and manifest.crypto_destroyed_proof_digest is not None
            and manifest.crypto_destroyed_at is not None
            and self._utc(manifest.crypto_destroyed_at) <= now
        ):
            reason, proof_time = "crypto_destroyed", self._utc(manifest.crypto_destroyed_at)
        else:
            raise AccountErasureFailure(
                "backup_expiry_pending",
                retryable=True,
                retry_at=max(now + timedelta(seconds=30), generation_expires_at),
            )
        snapshot = {
            "deletion_id": item.deletion_id,
            "generation_id": obligation.generation_id,
            "created_at": generation_created_at.isoformat(),
            "expires_at": generation_expires_at.isoformat(),
            "db_manifest_digest": obligation.db_manifest_digest,
            "markdown_manifest_digest": obligation.markdown_manifest_digest,
            "reason": reason,
            "proof_time": self._utc(proof_time).isoformat(),
            "operator_proof": (
                manifest.expired_proof_digest
                if reason == "expired"
                else manifest.crypto_destroyed_proof_digest
            ),
        }
        obligation.proof_digest = sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        obligation.verified_at = now
        obligation.state = "verified"
        return "backup_proof_attested"

    async def _postcheck(self, session: AsyncSession, deletion_id: str) -> None:
        lifecycle = await session.get(AccountLifecycle, deletion_id)
        if lifecycle is None or lifecycle.provider_subject_ciphertext is None:
            raise AccountErasureFailure("provider_ciphertext_unavailable", retryable=False)
        subject = decrypt_lifecycle_provider_subject(
            self.settings, deletion_id=deletion_id, ciphertext=lifecycle.provider_subject_ciphertext
        )
        for model, column in _POSTCHECK_IDENTITY_COLUMNS:
            if await session.scalar(select(model).where(column == subject).limit(1)) is not None:
                raise AccountErasureFailure("postcheck_identity_residue", retryable=False)
        for model, column in _POSTCHECK_CONTENT_COLUMNS:
            if (
                await session.scalar(
                    select(model).where(column.contains(subject, autoescape=True)).limit(1)
                )
                is not None
            ):
                raise AccountErasureFailure("postcheck_content_residue", retryable=False)
        incomplete_files = await session.scalar(
            select(AccountErasureItem.id)
            .where(
                AccountErasureItem.deletion_id == deletion_id,
                AccountErasureItem.phase == "delete_file",
                AccountErasureItem.state != "completed",
            )
            .limit(1)
        )
        if incomplete_files is not None:
            raise AccountErasureFailure("postcheck_file_absence_unconfirmed", retryable=True)
        file_items = (
            await session.scalars(
                select(AccountErasureItem).where(
                    AccountErasureItem.deletion_id == deletion_id,
                    AccountErasureItem.phase == "delete_file",
                )
            )
        ).all()
        for file_item in file_items:
            proof = await session.scalar(
                select(AccountErasureFileProof).where(
                    AccountErasureFileProof.deletion_id == deletion_id,
                    AccountErasureFileProof.resource_type == file_item.resource_type,
                    AccountErasureFileProof.resource_id == file_item.resource_id,
                )
            )
            if proof is None or self.store._absolute(proof.relative_path).exists():
                raise AccountErasureFailure("postcheck_file_absence_unconfirmed", retryable=False)

    async def _complete(
        self, session: AsyncSession, item: AccountErasureItem, now: datetime, code: str | None
    ) -> None:
        item.state = "completed"
        item.available_at = None
        item.lease_expires_at = None
        item.claimed_by = None
        item.claim_token = None
        item.hold_kind = None
        item.hold_id = None
        item.hold_review_at = None
        item.last_error_code = code
        item.completed_at = now
        item.updated_at = now
        await session.commit()

    async def _fail(self, claim: _Claim, failure: AccountErasureFailure, now: datetime) -> str:
        async with self.session_factory() as session:
            item = await self._leased_item(session, claim)
            if item is None:
                return "retried"
            item.lease_expires_at = None
            item.claimed_by = None
            item.claim_token = None
            item.last_error_code = failure.code
            item.updated_at = now
            if failure.code == "backup_expiry_pending":
                item.state = "queued"
                item.attempts = max(0, item.attempts - 1)
                item.available_at = failure.retry_at or now + timedelta(seconds=30)
                await session.commit()
                await self._reconcile(item.deletion_id, now)
                return "retried"
            if failure.code != "backup_expiry_pending" and (
                not failure.retryable or item.attempts >= self.max_attempts
            ):
                item.state = "dead_letter"
                item.available_at = None
                await session.commit()
                await self._reconcile(item.deletion_id, now)
                return "dead_lettered"
            item.state = "queued"
            item.available_at = now + timedelta(seconds=2 ** min(item.attempts, 6))
            await session.commit()
        await self._reconcile(claim.deletion_id, now)
        return "retried"

    async def _reconcile_all(self, now: datetime) -> None:
        async with self.session_factory() as session:
            ids = (await session.scalars(select(AccountLifecycle.id))).all()
        for deletion_id in ids:
            await self._reconcile(deletion_id, now)

    async def _scrub_expired_terminal_markers(
        self, session: AsyncSession, lifecycle: AccountLifecycle, now: datetime
    ) -> None:
        """Scrub only after the terminal receipt window and exact mirror proof.

        Lifecycle and deny rows are durable authorities.  The bounded HMAC
        markers and receipt-rate counters are disposable only after the
        tombstone, external commitment journal/witness, and complete live
        mirror have all been revalidated in the same backup-serialized worker
        transaction.  Any uncertainty leaves the rows untouched for a later
        recovery pass.
        """
        terminal_at = lifecycle.terminal_at
        if terminal_at is None:
            return
        terminal_at = self._utc(terminal_at).astimezone(UTC)
        if now < terminal_at + timedelta(days=30):
            return
        tombstone = await session.scalar(
            select(AccountLifecycleTombstone)
            .where(AccountLifecycleTombstone.deletion_id == lifecycle.id)
            .with_for_update()
        )
        if tombstone is None:
            return
        tombstone_at = self._utc(tombstone.occurred_at).astimezone(UTC)
        expected_digest = sha256(
            f"connect.md:lifecycle:terminal:v1:{lifecycle.id}:{lifecycle.policy_version}:"
            f"{terminal_at.isoformat()}".encode()
        ).hexdigest()
        if (
            lifecycle.state != "fully_erased"
            or tombstone.phase != "fully_erased"
            or tombstone.policy_version != lifecycle.policy_version
            or tombstone_at != terminal_at
            or tombstone.result_digest != expected_digest
        ):
            return
        if self.settings.deletion_journal_path is None:
            return
        try:
            journal = DeletionCommitmentJournal(self.settings)
            if await verify_live_deletion_mirror(session, journal) < 1:
                return
        except (DeletionJournalError, OSError, ValueError, AuthenticationUnavailable):
            return

        def is_sha256_hmac(value: str | None) -> bool:
            return (
                isinstance(value, str)
                and len(value) == 64
                and all(character in "0123456789abcdef" for character in value)
            )

        for marker in (
            lifecycle.request_idempotency_hmac,
            lifecycle.confirmation_idempotency_hmac,
            lifecycle.receipt_hmac,
            lifecycle.receipt_recovery_idempotency_hmac,
        ):
            if marker is not None and not is_sha256_hmac(marker):
                return
        await session.execute(
            delete(AccountLifecycleReceiptRateLimit).where(
                AccountLifecycleReceiptRateLimit.deletion_id == lifecycle.id
            )
        )
        lifecycle.request_idempotency_hmac = None
        lifecycle.confirmation_idempotency_hmac = None
        lifecycle.receipt_hmac = None
        lifecycle.receipt_recovery_idempotency_hmac = None
        await session.commit()

    async def _reconcile(self, deletion_id: str, now: datetime) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(AccountBackupAuthority)
                .where(AccountBackupAuthority.id == ACCOUNT_BACKUP_AUTHORITY_ID)
                .values(updated_at=AccountBackupAuthority.updated_at)
            )
            authority = await session.get(
                AccountBackupAuthority, ACCOUNT_BACKUP_AUTHORITY_ID, with_for_update=True
            )
            if authority is None:
                await session.rollback()
                return
            lifecycle = await session.get(AccountLifecycle, deletion_id, with_for_update=True)
            if lifecycle is None or lifecycle.state == "confirmation_pending":
                return
            if lifecycle.state == "fully_erased":
                await self._scrub_expired_terminal_markers(session, lifecycle, now)
                return
            items = (
                await session.scalars(
                    select(AccountErasureItem).where(AccountErasureItem.deletion_id == deletion_id)
                )
            ).all()
            if any(item.state == "dead_letter" for item in items):
                lifecycle.state = "failed"
                lifecycle.safe_failure_code = "erasure_dead_letter"
                await session.commit()
                return
            if any(item.state == "held" for item in items):
                lifecycle.state = "held"
                lifecycle.safe_failure_code = "erasure_hold_active"
                await session.commit()
                return
            local = [item for item in items if item.phase != "backup"]
            if any(item.state != "completed" for item in local):
                lifecycle.state = "erasing"
                lifecycle.safe_failure_code = None
                await session.commit()
                return
            if (
                lifecycle.provider_state != "verified"
                or lifecycle.provider_subject_ciphertext is not None
                or lifecycle.provider_session_ciphertext is not None
            ):
                lifecycle.state = "erasing"
                lifecycle.safe_failure_code = None
                await session.commit()
                return
            erased_document_ids = {
                item.resource_id for item in items if item.resource_type == "document"
            }
            projection_states = (
                (
                    await session.scalars(
                        select(SearchProjectionTask.state).where(
                            SearchProjectionTask.document_id.in_(erased_document_ids)
                        )
                    )
                ).all()
                if erased_document_ids
                else []
            )
            if "dead_letter" in projection_states:
                lifecycle.state = "failed"
                lifecycle.safe_failure_code = "search_projection_dead_letter"
                await session.commit()
                return
            if projection_states:
                # Local row deletion is not final search erasure. The projection
                # worker removes a missing canonical document, verifies remote
                # absence, and only then consumes its tombstone.
                lifecycle.state = "erasing"
                lifecycle.safe_failure_code = None
                await session.commit()
                return
            if lifecycle.live_erased_at is None:
                lifecycle.live_erased_at = now
            lifecycle.state = "live_erasure_complete"
            obligations = (
                await session.scalars(
                    select(AccountBackupObligation).where(
                        AccountBackupObligation.deletion_id == deletion_id
                    )
                )
            ).all()
            backup_items = [item for item in items if item.phase == "backup"]
            if any(item.state != "completed" for item in backup_items) or any(
                obligation.state != "verified" or obligation.proof_digest is None
                for obligation in obligations
            ):
                lifecycle.backup_state = "expiry_pending"
                lifecycle.state = "backup_expiry_pending"
                await session.commit()
                return
            lifecycle.backup_state = "verified"
            lifecycle.terminal_at = now
            lifecycle.state = "fully_erased"
            lifecycle.safe_failure_code = None
            existing = await session.scalar(
                select(AccountLifecycleTombstone).where(
                    AccountLifecycleTombstone.deletion_id == deletion_id
                )
            )
            if existing is None:
                digest = sha256(
                    f"connect.md:lifecycle:terminal:v1:{deletion_id}:{lifecycle.policy_version}:{now.isoformat()}".encode()
                ).hexdigest()
                session.add(
                    AccountLifecycleTombstone(
                        deletion_id=deletion_id,
                        policy_version=lifecycle.policy_version,
                        phase="fully_erased",
                        result_digest=digest,
                        occurred_at=now,
                    )
                )
            await session.commit()


# This explicit registry is the postcondition contract. Keep direct identity columns
# and user-controllable content columns separate so errors remain sanitized.
_POSTCHECK_IDENTITY_COLUMNS: tuple[tuple[type[Any], Any], ...] = (
    (Document, Document.owner_id),
    (DocumentVersion, DocumentVersion.actor_id),
    (Post, Post.owner_id),
    (PostRateBucket, PostRateBucket.owner_id),
    (FollowRateBucket, FollowRateBucket.owner_id),
    (ProfileFollow, ProfileFollow.follower_owner_id),
    (ProfileFollow, ProfileFollow.followed_owner_id),
    (PostGraphPairLock, PostGraphPairLock.pair_owner_low),
    (PostGraphPairLock, PostGraphPairLock.pair_owner_high),
    (PostContentBlock, PostContentBlock.blocker_owner_id),
    (PostContentBlock, PostContentBlock.blocked_owner_id),
    (PostReport, PostReport.reporter_owner_id),
    (PostReportRateBucket, PostReportRateBucket.owner_id),
    (ModerationCase, ModerationCase.subject_owner_id),
    (ModerationDecision, ModerationDecision.moderator_id),
    (ModerationAppeal, ModerationAppeal.subject_owner_id),
    (ModerationAppeal, ModerationAppeal.appeal_reviewer_id),
    (ModerationAuditEvent, ModerationAuditEvent.actor_id),
    (PostModerationEvent, PostModerationEvent.actor_id),
    (ApiKey, ApiKey.owner_id),
    (AgentIdentity, AgentIdentity.owner_id),
    (AgentGrant, AgentGrant.owner_id),
    (AgentMandate, AgentMandate.owner_id),
    (IdempotencyRecord, IdempotencyRecord.owner_id),
    (ChangeEvent, ChangeEvent.owner_id),
    (ChangeEvent, ChangeEvent.actor_id),
    (ContactPolicy, ContactPolicy.owner_id),
    (ContactBlock, ContactBlock.blocker_owner_id),
    (ContactBlock, ContactBlock.blocked_owner_id),
    (ContactRequest, ContactRequest.sender_owner_id),
    (ContactRequest, ContactRequest.recipient_owner_id),
    (ContactRequest, ContactRequest.sender_actor_id),
    (ContactRequest, ContactRequest.decision_actor_id),
    (ContactRateBucket, ContactRateBucket.sender_owner_id),
    (AgentOutreachRecipientRateBucket, AgentOutreachRecipientRateBucket.recipient_owner_id),
    (Organization, Organization.owner_id),
    (OrganizationMembership, OrganizationMembership.member_owner_id),
    (OrganizationMembership, OrganizationMembership.invited_by_owner_id),
    (OrganizationVerification, OrganizationVerification.submitted_by_owner_id),
    (OrganizationVerificationEvent, OrganizationVerificationEvent.actor_id),
    (Application, Application.applicant_owner_id),
    (Application, Application.applicant_actor_id),
    (Application, Application.confirmed_by_owner_id),
    (Application, Application.decision_actor_id),
    (ApplicationRateBucket, ApplicationRateBucket.applicant_owner_id),
    (ConnectionBlock, ConnectionBlock.blocker_owner_id),
    (ConnectionBlock, ConnectionBlock.blocked_owner_id),
    (ConnectionRequest, ConnectionRequest.pair_owner_low),
    (ConnectionRequest, ConnectionRequest.pair_owner_high),
    (ConnectionRequest, ConnectionRequest.requester_owner_id),
    (ConnectionRequest, ConnectionRequest.recipient_owner_id),
    (ConnectionRequest, ConnectionRequest.requester_actor_id),
    (ConnectionRequest, ConnectionRequest.decision_actor_id),
    (ConnectionRequestRateBucket, ConnectionRequestRateBucket.requester_owner_id),
    (Connection, Connection.pair_owner_low),
    (Connection, Connection.pair_owner_high),
    (Connection, Connection.requester_owner_id),
    (Connection, Connection.recipient_owner_id),
    (Connection, Connection.ended_by_owner_id),
    (Conversation, Conversation.pair_owner_low),
    (Conversation, Conversation.pair_owner_high),
    (Conversation, Conversation.created_by_owner_id),
    (Message, Message.sender_owner_id),
    (Message, Message.sender_actor_id),
    (MessageRateBucket, MessageRateBucket.sender_owner_id),
    (Notification, Notification.recipient_owner_id),
    (Notification, Notification.actor_owner_id),
    (AgentProposal, AgentProposal.owner_id),
    (AgentProposal, AgentProposal.submitter_actor_id),
    (AgentProposal, AgentProposal.decision_actor_id),
    (RetentionHold, RetentionHold.authority),
    (RetentionHold, RetentionHold.released_by_authority),
)

_POSTCHECK_CONTENT_COLUMNS: tuple[tuple[type[Any], Any], ...] = (
    (ChangeEvent, ChangeEvent.payload),
    (IdempotencyRecord, IdempotencyRecord.response_body),
    (IdempotencyRecord, IdempotencyRecord.response_headers),
    (Message, Message.markdown),
    (AgentProposal, AgentProposal.markdown),
    (ContactRequest, ContactRequest.purpose),
    (ContactRequest, ContactRequest.message),
    (ContactRequest, ContactRequest.report_reason),
    (PostReport, PostReport.narrative),
    (ModerationDecision, ModerationDecision.subject_explanation),
    (ModerationDecision, ModerationDecision.internal_rationale),
    (ModerationDecision, ModerationDecision.evidence),
    (ModerationAppeal, ModerationAppeal.rationale),
    (ModerationAppeal, ModerationAppeal.subject_explanation),
    (ModerationAppeal, ModerationAppeal.internal_rationale),
    (ModerationAuditEvent, ModerationAuditEvent.safe_metadata),
    (Organization, Organization.description),
    (OrganizationVerificationEvidence, OrganizationVerificationEvidence.metadata_json),
    (Job, Job.description),
    (Application, Application.message),
)
