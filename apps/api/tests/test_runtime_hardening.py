from __future__ import annotations

import tomllib
from pathlib import Path

from app import db
from app.config import Settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_database_engine_has_explicit_bounded_pool(monkeypatch) -> None:
    settings = Settings(database_url="postgresql+asyncpg://connectmd_api:test@postgres/connectmd")
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create_async_engine(url: str, **options: object) -> object:
        captured["url"] = url
        captured["options"] = options
        return sentinel

    monkeypatch.setattr(db, "create_async_engine", fake_create_async_engine)

    assert db.build_engine(settings) is sentinel
    assert captured == {
        "url": settings.database_url,
        "options": {
            "pool_pre_ping": True,
            "pool_size": 3,
            "max_overflow": 2,
            "pool_recycle": 1800,
        },
    }


def test_alembic_ini_uses_only_a_non_routable_placeholder() -> None:
    ini = (API_ROOT / "alembic.ini").read_text(encoding="utf-8")

    assert "sqlalchemy.url = postgresql+asyncpg://invalid:invalid@invalid.invalid:1/invalid" in ini
    assert "connectmd:connectmd" not in ini


def test_mypy_missing_imports_are_fail_closed_except_for_jsonschema() -> None:
    configuration = tomllib.loads((API_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["tool"]["mypy"]["ignore_missing_imports"] is False
    assert configuration["tool"]["mypy"]["overrides"] == [
        {
            "module": ["jsonschema", "jsonschema.*"],
            "ignore_missing_imports": True,
        }
    ]
