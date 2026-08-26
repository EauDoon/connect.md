from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings
from app.services.artifact_durability import ArtifactIntentGateLease
from app.services.database_roles import API_DATABASE_ROLE, require_database_role
from app.services.storage import StorageIntegrityError

EXPECTED_ALEMBIC_HEAD = "0028_scrub_verification_change_payloads"


class DatabaseSchemaNotCurrent(RuntimeError):
    pass


async def require_current_database_schema(session: AsyncSession) -> None:
    """Require the one exact deployed Alembic head without importing Alembic at runtime."""
    await require_database_role(session, API_DATABASE_ROLE)
    try:
        versions = tuple(
            str(value)
            for value in (
                await session.scalars(text("SELECT version_num FROM alembic_version"))
            ).all()
        )
    except SQLAlchemyError:
        raise DatabaseSchemaNotCurrent("database schema is not current") from None
    if versions != (EXPECTED_ALEMBIC_HEAD,):
        raise DatabaseSchemaNotCurrent("database schema is not current")


@dataclass(frozen=True)
class RollbackFileCleanup:
    relative_path: str
    sha256: str
    size_bytes: int
    max_size_bytes: int


def _compensate_rollback_files(request: Request, session: AsyncSession) -> None:
    """Best-effort removal of files created by a transaction that did not commit.

    Routes may register only freshly materialized immutable files in this
    request-scoped set.  The dependency clears the registration only after its
    durable commit; this fallback is intentionally not a filesystem scan.
    """
    registered = session.info.pop("connectmd_rollback_file_cleanup", ())
    if not isinstance(registered, (set, tuple, list)):
        return
    store = getattr(request.app.state, "store", None)
    if store is None:
        return
    for cleanup in registered:
        if not isinstance(cleanup, RollbackFileCleanup):
            continue
        try:
            store.delete_verified_exact(
                cleanup.relative_path,
                cleanup.sha256,
                expected_size_bytes=cleanup.size_bytes,
                max_size_bytes=cleanup.max_size_bytes,
            )
        except StorageIntegrityError:
            # Uncertainty is preservation: a rollback fallback must never remove
            # bytes that no longer match its exact registered authority.
            continue


def build_engine(settings: Settings | None = None) -> AsyncEngine:
    current = settings or get_settings()
    return create_async_engine(
        current.database_url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        pool_recycle=1800,
    )


def build_session_factory(
    settings: Settings | None = None, engine: AsyncEngine | None = None
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine or build_engine(settings), expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            try:
                yield session
            except BaseException:
                await session.rollback()
                _compensate_rollback_files(request, session)
                raise
            else:
                if session.info.pop("connectmd_auth_last_used", False):
                    try:
                        await session.commit()
                    except BaseException:
                        await session.rollback()
                        _compensate_rollback_files(request, session)
                        raise
                    session.info.pop("connectmd_rollback_file_cleanup", None)
                elif session.info.get("connectmd_rollback_file_cleanup"):
                    # A registered immutable file without a requested commit is
                    # never durable. Roll back before closing so the paired
                    # local file is not left unowned.
                    await session.rollback()
                    _compensate_rollback_files(request, session)
        finally:
            gate = session.info.pop("connectmd_artifact_intent_gate", None)
            if isinstance(gate, ArtifactIntentGateLease):
                await gate.release()
