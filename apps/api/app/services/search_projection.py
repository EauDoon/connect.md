"""Durable, content-free reconciliation between canonical documents and Meilisearch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, DocumentVersion, SearchProjectionTask
from app.services.search import MeiliSearchProjection, SearchUnavailable
from app.services.storage import StorageIntegrityError, VersionStore


@dataclass(frozen=True)
class ProjectionClaim:
    document_id: str
    version: int
    claim_token: str
    attempts: int


@dataclass(frozen=True)
class ProjectionResult:
    action: str
    document_id: str | None = None
    version: int | None = None
    attempts: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ProjectionHealth:
    backlog_count: int
    eligible_count: int
    dead_letter_count: int
    oldest_backlog_age_seconds: int


class ProjectionFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class SearchProjectionExecutor:
    """Lease and reconcile one version-keyed task at a time."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        store: VersionStore,
        search: MeiliSearchProjection,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        max_attempts: int = 8,
        max_backoff_seconds: int = 300,
    ) -> None:
        self.session_factory = session_factory
        self.store = store
        self.search = search
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.max_backoff_seconds = max_backoff_seconds

    async def run_once(self, *, now: datetime | None = None) -> ProjectionResult:
        current = now or datetime.now(UTC)
        claim = await self._claim(current)
        if claim is None:
            return ProjectionResult(action="idle")
        try:
            return await self._project(claim)
        except ProjectionFailure as exc:
            return await self._fail(claim, exc.code, exc.retryable)
        except SearchUnavailable:
            return await self._fail(claim, "search_unavailable", True)
        except StorageIntegrityError:
            return await self._fail(claim, "storage_integrity", True)
        except (OSError, ValueError):
            return await self._fail(claim, "projection_persistence", True)
        except Exception:
            # Never persist or emit exception text: converter/profile content and
            # provider responses are outside the worker's observable contract.
            return await self._fail(claim, "projection_failed", True)

    async def _claim(self, now: datetime) -> ProjectionClaim | None:
        async with self.session_factory() as session:
            task = await session.scalar(
                select(SearchProjectionTask)
                .where(
                    or_(
                        and_(
                            SearchProjectionTask.state == "pending",
                            SearchProjectionTask.available_at <= now,
                        ),
                        and_(
                            SearchProjectionTask.state == "leased",
                            SearchProjectionTask.lease_expires_at.is_not(None),
                            SearchProjectionTask.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(
                    SearchProjectionTask.available_at.asc(),
                    SearchProjectionTask.created_at.asc(),
                    SearchProjectionTask.document_id.asc(),
                    SearchProjectionTask.version.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return None
            token = str(uuid4())
            task.state = "leased"
            task.claimed_by = self.worker_id
            task.claim_token = token
            task.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            task.attempts += 1
            task.updated_at = now
            await session.commit()
            return ProjectionClaim(
                document_id=task.document_id,
                version=task.version,
                claim_token=token,
                attempts=task.attempts,
            )

    async def health_snapshot(self, *, now: datetime | None = None) -> ProjectionHealth:
        current = now or datetime.now(UTC)
        eligible = or_(
            and_(
                SearchProjectionTask.state == "pending",
                SearchProjectionTask.available_at <= current,
            ),
            and_(
                SearchProjectionTask.state == "leased",
                SearchProjectionTask.lease_expires_at.is_not(None),
                SearchProjectionTask.lease_expires_at <= current,
            ),
        )
        async with self.session_factory() as session:
            backlog_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(SearchProjectionTask)
                    .where(SearchProjectionTask.state.in_(("pending", "leased")))
                )
                or 0
            )
            eligible_count = int(
                await session.scalar(
                    select(func.count()).select_from(SearchProjectionTask).where(eligible)
                )
                or 0
            )
            dead_letter_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(SearchProjectionTask)
                    .where(SearchProjectionTask.state == "dead_letter")
                )
                or 0
            )
            oldest = await session.scalar(
                select(func.min(SearchProjectionTask.created_at)).where(
                    SearchProjectionTask.state.in_(("pending", "leased"))
                )
            )
        if oldest is None:
            age = 0
        else:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=UTC)
            age = max(0, int((current - oldest).total_seconds()))
        return ProjectionHealth(
            backlog_count=backlog_count,
            eligible_count=eligible_count,
            dead_letter_count=dead_letter_count,
            oldest_backlog_age_seconds=age,
        )

    async def _project(self, claim: ProjectionClaim) -> ProjectionResult:
        async with self.session_factory() as session:
            task = await self._leased_task(session, claim)
            if task is None:
                return ProjectionResult(
                    action="lost_lease",
                    document_id=claim.document_id,
                    version=claim.version,
                    attempts=claim.attempts,
                )
            if session.get_bind().dialect.name == "postgresql":
                # Advisory serialization avoids granting the worker UPDATE on
                # canonical tables. Document writes acquire the same key.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:document_id, 0))"),
                    {"document_id": claim.document_id},
                )
            document = await session.scalar(
                select(Document).where(Document.id == claim.document_id)
            )
            if document is None:
                attestation = await self.search.delete_document(claim.document_id)
                if not attestation.configured or attestation.state not in {"deleted", "absent"}:
                    raise ProjectionFailure("search_unconfigured", retryable=True)
                await session.delete(task)
                await session.commit()
                return ProjectionResult(
                    action="removed_missing",
                    document_id=claim.document_id,
                    version=claim.version,
                    attempts=claim.attempts,
                )
            if document.current_version != claim.version:
                await session.delete(task)
                await session.commit()
                return ProjectionResult(
                    action="superseded",
                    document_id=claim.document_id,
                    version=claim.version,
                    attempts=claim.attempts,
                )
            if not self.search.enabled:
                raise ProjectionFailure("search_unconfigured", retryable=True)
            if document.visibility == "public":
                version = await session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.version == document.current_version,
                    )
                )
                if version is None:
                    raise ProjectionFailure("projection_version_missing", retryable=False)
                markdown = self.store.read_verified(version.storage_path, version.sha256)
                await self.search.index(document, markdown)
                action = "indexed"
            else:
                attestation = await self.search.delete_document(document.id)
                if not attestation.configured or attestation.state not in {"deleted", "absent"}:
                    raise ProjectionFailure("search_unconfigured", retryable=True)
                action = "removed"
            await session.delete(task)
            await session.commit()
            return ProjectionResult(
                action=action,
                document_id=claim.document_id,
                version=claim.version,
                attempts=claim.attempts,
            )

    async def _leased_task(
        self, session: AsyncSession, claim: ProjectionClaim
    ) -> SearchProjectionTask | None:
        return await session.scalar(
            select(SearchProjectionTask)
            .where(
                SearchProjectionTask.document_id == claim.document_id,
                SearchProjectionTask.version == claim.version,
                SearchProjectionTask.state == "leased",
                SearchProjectionTask.claimed_by == self.worker_id,
                SearchProjectionTask.claim_token == claim.claim_token,
            )
            .with_for_update()
        )

    async def _fail(
        self, claim: ProjectionClaim, error_code: str, retryable: bool
    ) -> ProjectionResult:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            task = await self._leased_task(session, claim)
            if task is None:
                return ProjectionResult(
                    action="lost_lease",
                    document_id=claim.document_id,
                    version=claim.version,
                    attempts=claim.attempts,
                )
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:document_id, 0))"),
                    {"document_id": claim.document_id},
                )
            current_version = await session.scalar(
                select(Document.current_version).where(Document.id == claim.document_id)
            )
            if current_version is not None and current_version != claim.version:
                # A canonical update can commit between the failed remote/storage
                # attempt and this retry-state transaction. Never turn that now-
                # stale failure into a dead letter that degrades current health.
                await session.delete(task)
                await session.commit()
                return ProjectionResult(
                    action="superseded",
                    document_id=claim.document_id,
                    version=claim.version,
                    attempts=claim.attempts,
                )
            dead_letter = not retryable or task.attempts >= self.max_attempts
            task.state = "dead_letter" if dead_letter else "pending"
            task.available_at = (
                now
                if dead_letter
                else now
                + timedelta(seconds=min(2 ** min(task.attempts, 20), self.max_backoff_seconds))
            )
            task.lease_expires_at = None
            task.claimed_by = None
            task.claim_token = None
            task.last_error_code = error_code
            task.dead_lettered_at = now if dead_letter else None
            task.updated_at = now
            await session.commit()
            return ProjectionResult(
                action="dead_lettered" if dead_letter else "retry_scheduled",
                document_id=claim.document_id,
                version=claim.version,
                attempts=task.attempts,
                error_code=error_code,
            )


async def list_dead_letters(
    session_factory: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> list[SearchProjectionTask]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(SearchProjectionTask)
                    .where(SearchProjectionTask.state == "dead_letter")
                    .order_by(
                        SearchProjectionTask.dead_lettered_at.asc(),
                        SearchProjectionTask.document_id.asc(),
                        SearchProjectionTask.version.asc(),
                    )
                    .limit(limit)
                )
            ).all()
        )


async def count_dead_letters(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(SearchProjectionTask)
                .where(SearchProjectionTask.state == "dead_letter")
            )
            or 0
        )


async def retry_dead_letter(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    document_id: str,
    version: int,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(UTC)
    async with session_factory() as session:
        task = await session.scalar(
            select(SearchProjectionTask)
            .where(
                SearchProjectionTask.document_id == document_id,
                SearchProjectionTask.version == version,
                SearchProjectionTask.state == "dead_letter",
            )
            .with_for_update()
        )
        if task is None:
            return False
        task.state = "pending"
        task.attempts = 0
        task.available_at = current
        task.lease_expires_at = None
        task.claimed_by = None
        task.claim_token = None
        task.last_error_code = None
        task.dead_lettered_at = None
        task.updated_at = current
        await session.commit()
        return True
