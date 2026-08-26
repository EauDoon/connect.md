from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import Request
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


class _RecordHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _test_app(tmp_path: Path, *, cors_origins: list[str] | None = None):
    return create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'observability.db'}",
            storage_path=tmp_path / "storage",
            api_key_pepper="test-only-pepper-is-long-enough",
            cors_origins=cors_origins or [],
        )
    )


async def test_request_log_is_single_line_json_with_only_route_template(tmp_path: Path) -> None:
    app = _test_app(tmp_path)
    handler = _RecordHandler()
    app.state.request_logger.addHandler(handler)
    private_query = "PRIVATE_QUERY_SENTINEL_7c9e6f"
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                f"/healthz?probe={private_query}",
                headers={"X-Request-ID": "request-observe-0001"},
            )
    finally:
        app.state.request_logger.removeHandler(handler)
        await app.state.engine.dispose()

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-observe-0001"
    assert len(handler.records) == 1
    event = json.loads(handler.records[0].getMessage())
    assert event["event"] == "api.request.completed"
    assert event["method"] == "GET"
    assert event["request_id"] == "request-observe-0001"
    assert event["route"] == "/healthz"
    assert event["status"] == 200
    assert isinstance(event["duration_ms"], int | float)
    assert private_query not in handler.records[0].getMessage()


async def test_unhandled_exception_is_private_problem_json_and_sanitized_log(
    tmp_path: Path,
) -> None:
    origin = "https://workspace.example.test"
    app = _test_app(tmp_path, cors_origins=[origin])
    handler = _RecordHandler()
    app.state.request_logger.addHandler(handler)
    private_exception = "PRIVATE_EXCEPTION_SENTINEL_29df41"
    private_body = "PRIVATE_BODY_SENTINEL_f280aa"
    private_token = "PRIVATE_BEARER_SENTINEL_34cb1d"
    private_path = "PRIVATE_PATH_SENTINEL_e9942c"

    @app.post("/v1/__observability_test__/{opaque_segment}")
    async def explode(opaque_segment: str, request: Request) -> None:
        assert opaque_segment
        await request.body()
        raise RuntimeError(private_exception)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                f"/v1/__observability_test__/{private_path}",
                content=private_body,
                headers={
                    "Authorization": f"Bearer {private_token}",
                    "Origin": origin,
                    "X-Request-ID": "request-observe-5001",
                },
            )
    finally:
        app.state.request_logger.removeHandler(handler)
        await app.state.engine.dispose()

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-Request-ID"] == "request-observe-5001"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert {value.strip() for value in response.headers["Vary"].split(",")} == {
        "Authorization",
        "Origin",
    }
    assert response.json() == {
        "type": "https://connect.md/problems/internal-error",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "an unexpected server error occurred",
        "instance": "urn:connect.md:request:request-observe-5001",
        "request_id": "request-observe-5001",
    }
    assert len(handler.records) == 1
    event = json.loads(handler.records[0].getMessage())
    assert event["event"] == "api.request.completed"
    assert event["method"] == "POST"
    assert event["request_id"] == "request-observe-5001"
    assert event["route"] == "/v1/__observability_test__/{opaque_segment}"
    assert event["status"] == 500
    assert event["error"] == "unhandled_exception"
    assert event["exception_type"] == "RuntimeError"
    assert event["traceback"]
    combined = response.text + handler.records[0].getMessage()
    for private_value in (private_exception, private_body, private_token, private_path):
        assert private_value not in combined
