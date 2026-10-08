"""The API advertises the repository version everywhere it reports one."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from app import __version__
from app.cli import parse_args

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]


def test_package_version_matches_the_repository_version_file() -> None:
    assert __version__ == (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_distribution_metadata_matches_the_package_version() -> None:
    pyproject = tomllib.loads((API_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__


async def test_openapi_and_agent_card_report_the_package_version(api_client) -> None:
    app, client = api_client
    assert app.version == __version__
    openapi = await client.get("/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["info"]["version"] == __version__
    card = await client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert card.json()["version"] == __version__


def test_cli_reports_the_package_version(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["app.cli", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        parse_args()
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"connect.md API {__version__}"
