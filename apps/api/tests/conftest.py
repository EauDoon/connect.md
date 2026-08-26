from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import Principal, optional_principal, require_principal
from app.config import Settings
from app.main import create_app
from app.models import Base


async def _owner() -> Principal:
    return Principal(subject="user_test", method="clerk_jwt", scopes=frozenset({"*"}))


@pytest_asyncio.fixture
async def api_client(tmp_path) -> AsyncIterator[tuple[object, AsyncClient]]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'connectmd.db'}",
        storage_path=tmp_path / "storage",
        api_key_pepper="test-only-pepper-is-long-enough",
        verification_reviewer_id="reviewer:preprovisioned",
        verification_reviewer_role="recruiting_verifier",
        recruiting_enabled=True,
    )
    app = create_app(settings)
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app.dependency_overrides[require_principal] = _owner
    app.dependency_overrides[optional_principal] = _owner
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield app, client
    await app.state.engine.dispose()
