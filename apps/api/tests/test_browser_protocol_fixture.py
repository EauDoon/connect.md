from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import json
import os
import socket
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import HttpUrl, TypeAdapter

from app.config import Settings
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "apps" / "web" / "e2e" / "public-fixtures.json"
PROTOCOL_PATHS = (
    "/agent-readme.md",
    "/llms.txt",
    "/llms-full.txt",
    "/openapi.json",
    "/.well-known/agent-card.json",
)
BASE_URL = "https://connectmd.invalid"
REQUEST_ID = "fixture-protocol-v1"
EVIDENCE_BOUNDARY = "hermetic current-source fixture parity; not live or deployment parity"
REQUIRED_HEADERS = {
    "/agent-readme.md": {"content-type", "x-request-id"},
    "/llms.txt": {"content-type", "x-request-id"},
    "/llms-full.txt": {"content-type", "x-request-id"},
    "/openapi.json": {"content-type", "x-request-id"},
    "/.well-known/agent-card.json": {
        "cache-control",
        "content-type",
        "etag",
        "x-request-id",
    },
}
FRAMING_HEADERS = {"content-length", "connection", "date", "keep-alive", "transfer-encoding"}
EXPECTED_AGENT_SKILLS = {
    "search-public-documents",
    "discover-public-taxonomies",
    "discover-public-agents",
    "list-profile-agents",
    "request-mediated-contact",
    "send-mandate-bound-agent-outreach",
    "get-mandate-bound-agent-outreach-status",
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )


def _load_fixture() -> dict[str, Any]:
    return _load_json(FIXTURE_PATH)


@contextmanager
def isolated_connectmd_environment() -> Iterator[None]:
    original = {
        name: value for name, value in os.environ.items() if name.upper().startswith("CONNECTMD_")
    }
    for name in list(os.environ):
        if name.upper().startswith("CONNECTMD_"):
            os.environ.pop(name, None)
    try:
        yield
    finally:
        for name in list(os.environ):
            if name.upper().startswith("CONNECTMD_"):
                os.environ.pop(name, None)
        os.environ.update(original)


def _decode_body(entry: dict[str, Any], path: str) -> bytes:
    encoded = entry.get("body_base64")
    if not isinstance(encoded, str) or not encoded or len(encoded) % 4:
        raise ValueError(f"invalid base64 body for {path}")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"invalid base64 body for {path}") from exc
    if base64.b64encode(body).decode("ascii") != encoded:
        raise ValueError(f"non-canonical base64 body for {path}")
    digest = hashlib.sha256(body).hexdigest()
    if entry.get("sha256") != digest:
        raise ValueError(f"body hash mismatch for {path}")
    try:
        body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"protocol body is not UTF-8 for {path}") from exc
    return body


def validate_protocol_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("protocol manifest must be an object")
    expected_keys = {
        "version",
        "base_url",
        "environment",
        "recruiting_enabled",
        "account_lifecycle_enabled",
        "evidence_boundary",
        "responses",
    }
    if set(manifest) != expected_keys:
        raise ValueError("protocol manifest fields drifted")
    if (
        manifest["version"] != 1
        or manifest["base_url"] != BASE_URL
        or manifest["environment"] != "development"
        or manifest["recruiting_enabled"] is not False
        or manifest["account_lifecycle_enabled"] is not False
        or not isinstance(manifest["evidence_boundary"], str)
        or "hermetic current-source fixture parity" not in manifest["evidence_boundary"]
        or "not live" not in manifest["evidence_boundary"]
    ):
        raise ValueError("protocol manifest profile drifted")
    responses = manifest["responses"]
    if not isinstance(responses, dict) or set(responses) != set(PROTOCOL_PATHS):
        raise ValueError("protocol response route set drifted")
    for path in PROTOCOL_PATHS:
        entry = responses[path]
        if not isinstance(entry, dict) or set(entry) != {
            "status",
            "headers",
            "sha256",
            "body_base64",
        }:
            raise ValueError(f"protocol response fields drifted for {path}")
        if entry["status"] != 200:
            raise ValueError(f"protocol response status drifted for {path}")
        headers = entry["headers"]
        if not isinstance(headers, dict) or any(
            not isinstance(name, str)
            or name != name.lower()
            or name in FRAMING_HEADERS
            or not isinstance(value, str)
            for name, value in headers.items()
        ):
            raise ValueError(f"protocol response headers drifted for {path}")
        if set(headers) != REQUIRED_HEADERS[path]:
            raise ValueError(f"protocol response header set drifted for {path}")
        if headers["x-request-id"] != REQUEST_ID:
            raise ValueError(f"protocol request id drifted for {path}")
        if not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64:
            raise ValueError(f"protocol response hash drifted for {path}")
        _decode_body(entry, path)
    return manifest


def _settings(storage_path: Path) -> Settings:
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        environment="development",
        database_url=f"sqlite+aiosqlite:///{storage_path.parent / 'fixture.db'}",
        storage_path=storage_path,
        api_key_pepper="browser-protocol-fixture-pepper",
        public_base_url=TypeAdapter(HttpUrl).validate_python(BASE_URL),
        recruiting_enabled=False,
        account_lifecycle_enabled=False,
    )


def _response_headers(response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in response.headers.multi_items():
        name = name.lower()
        if name in FRAMING_HEADERS:
            continue
        if name in headers:
            raise AssertionError(f"duplicate non-framing response header: {name}")
        headers[name] = value
    return dict(sorted(headers.items()))


async def _render_protocol_manifest(storage_path: Path) -> dict[str, Any]:
    with isolated_connectmd_environment():
        app = create_app(_settings(storage_path))
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
                responses: dict[str, Any] = {}
                for path in PROTOCOL_PATHS:
                    snapshots: list[tuple[int, bytes, dict[str, str]]] = []
                    for _ in range(2):
                        response = await client.get(path, headers={"X-Request-ID": REQUEST_ID})
                        snapshots.append(
                            (response.status_code, response.content, _response_headers(response))
                        )
                    if snapshots[0] != snapshots[1]:
                        raise AssertionError(f"non-repeatable protocol response: {path}")
                    status, body, headers = snapshots[0]
                    responses[path] = {
                        "status": status,
                        "headers": headers,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "body_base64": base64.b64encode(body).decode("ascii"),
                    }
                manifest = {
                    "version": 1,
                    "base_url": BASE_URL,
                    "environment": "development",
                    "recruiting_enabled": False,
                    "account_lifecycle_enabled": False,
                    "evidence_boundary": EVIDENCE_BOUNDARY,
                    "responses": responses,
                }
                validate_protocol_manifest(manifest)
                return manifest
        finally:
            await app.state.engine.dispose()


async def _disabled_recruiting_envelopes(
    storage_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    app = create_app(_settings(storage_path))
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            organizations = await client.get("/v1/organizations")
            jobs = await client.get("/v1/jobs")
            return organizations.json(), jobs.json()
    finally:
        await app.state.engine.dispose()


def _manifest_from_fixture() -> dict[str, Any]:
    fixture = _load_fixture()
    return validate_protocol_manifest(fixture.get("protocolManifest"))


@contextmanager
def blocked_network() -> Iterator[None]:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("hermetic browser fixture attempted a socket connection")

    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    socket.create_connection = fail  # type: ignore[assignment]
    socket.socket.connect = fail  # type: ignore[method-assign]
    socket.socket.connect_ex = fail  # type: ignore[assignment,method-assign]
    try:
        yield
    finally:
        socket.create_connection = original_create_connection
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]


def _assert_openapi_shape(body: bytes) -> None:
    payload = json.loads(
        body.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )
    paths = payload.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise AssertionError("OpenAPI paths are unavailable")
    for path, method in (
        ("/v1/profiles/{handle}.md", "get"),
        ("/v1/resumes/{slug}.md", "get"),
        ("/v1/search", "get"),
        ("/v1/search/query", "post"),
        ("/v1/taxonomies", "get"),
        ("/v1/taxonomies/{taxonomy}", "get"),
    ):
        if path not in paths or method not in paths[path]:
            raise AssertionError(f"OpenAPI route missing: {method} {path}")
    if "/mcp" in paths or "/a2a/message:send" in paths:
        raise AssertionError("private protocol routes leaked into OpenAPI")


def _assert_agent_card_shape(body: bytes) -> None:
    payload = json.loads(
        body.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )
    skills = payload.get("skills")
    if (
        not isinstance(skills, list)
        or {skill.get("id") for skill in skills} != EXPECTED_AGENT_SKILLS
    ):
        raise AssertionError("Agent Card skill inventory drifted")
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).lower()
    if any(marker in serialized for marker in ("tools/list", "private_tools", '"mcp"')):
        raise AssertionError("MCP/private tool details leaked into Agent Card")


@pytest.mark.asyncio
async def test_browser_protocol_fixture_matches_fixed_source_twice(
    tmp_path: Path,
) -> None:
    fixture_manifest = _manifest_from_fixture()
    with blocked_network():
        rendered = await _render_protocol_manifest(tmp_path / "rendered")
    assert rendered == fixture_manifest
    _assert_openapi_shape(
        base64.b64decode(fixture_manifest["responses"]["/openapi.json"]["body_base64"])
    )
    _assert_agent_card_shape(
        base64.b64decode(
            fixture_manifest["responses"]["/.well-known/agent-card.json"]["body_base64"]
        )
    )


@pytest.mark.asyncio
async def test_renderer_ignores_ambient_connectmd_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in {
        "CONNECTMD_RECRUITING_ENABLED": "true",
        "connectmd_account_lifecycle_enabled": "true",
        "CONNECTMD_PUBLIC_BASE_URL": "https://ambient.invalid",
        "CONNECTMD_MAX_UPLOAD_BYTES": "not-an-integer",
        "CONNECTMD_MAX_INGEST_CONCURRENCY": "1",
        "CONNECTMD_INGEST_TIMEOUT_SECONDS": "120",
    }.items():
        monkeypatch.setenv(name, value)
    with blocked_network():
        rendered = await _render_protocol_manifest(tmp_path / "ambient")
    assert rendered == _manifest_from_fixture()


@pytest.mark.asyncio
async def test_disabled_recruiting_fixture_is_truthfully_empty(tmp_path: Path) -> None:
    organizations, jobs = await _disabled_recruiting_envelopes(tmp_path / "disabled")
    assert organizations == {"organizations": [], "next_cursor": None}
    assert jobs == {"jobs": [], "next_cursor": None}
    fixture = _load_fixture()
    assert "organizations" not in fixture
    assert "jobs" not in fixture


def test_protocol_manifest_rejects_mutations() -> None:
    manifest = _manifest_from_fixture()
    mutations: list[tuple[str, Any]] = []

    malformed_base64 = copy.deepcopy(manifest)
    malformed_base64["responses"]["/llms.txt"]["body_base64"] = "!"
    mutations.append(("malformed base64", malformed_base64))

    wrong_hash = copy.deepcopy(manifest)
    wrong_hash["responses"]["/llms.txt"]["sha256"] = "0" * 64
    mutations.append(("wrong hash", wrong_hash))

    missing_route = copy.deepcopy(manifest)
    del missing_route["responses"]["/llms.txt"]
    mutations.append(("missing route", missing_route))

    unknown_route = copy.deepcopy(manifest)
    unknown_route["responses"]["/unknown"] = copy.deepcopy(unknown_route["responses"]["/llms.txt"])
    mutations.append(("unknown route", unknown_route))

    wrong_profile = copy.deepcopy(manifest)
    wrong_profile["recruiting_enabled"] = True
    mutations.append(("wrong profile", wrong_profile))

    omitted_header = copy.deepcopy(manifest)
    del omitted_header["responses"]["/.well-known/agent-card.json"]["headers"]["etag"]
    mutations.append(("omitted Agent Card header", omitted_header))

    for _label, candidate in mutations:
        with pytest.raises(ValueError, match="protocol|body|header|profile|route"):
            validate_protocol_manifest(candidate)


def test_fixture_json_parser_rejects_duplicates_and_nonfinite_constants(tmp_path: Path) -> None:
    for name, content in {
        "duplicate.json": '{"version":1,"version":2}',
        "nan.json": '{"value":NaN}',
        "infinity.json": '{"value":Infinity}',
        "negative-infinity.json": '{"value":-Infinity}',
    }.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError):
            _load_json(path)


def _write_fixture(manifest: dict[str, Any]) -> None:
    fixture = _load_fixture()
    for key in ("agentReadme", "llms", "llmsFull", "openapi", "agentCard", "organizations", "jobs"):
        fixture.pop(key, None)
    fixture["protocolManifest"] = manifest
    FIXTURE_PATH.write_text(
        json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_from_source() -> None:
    with tempfile.TemporaryDirectory(prefix="connectmd-browser-fixture-") as directory:
        manifest = asyncio.run(_render_protocol_manifest(Path(directory) / "storage"))
    _write_fixture(manifest)


if __name__ == "__main__":
    if "--write" not in __import__("sys").argv:
        raise SystemExit("pass --write to regenerate the checked-in protocol fixture")
    _write_from_source()
