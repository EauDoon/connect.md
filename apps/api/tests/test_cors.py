from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


async def test_configured_browser_origin_can_preflight_exact_employer_purpose_header(
    tmp_path,
) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'cors.db'}",
            storage_path=tmp_path / "storage",
            api_key_pepper="test-only-pepper-is-long-enough",
            cors_origins=["https://workspace.example.test"],
        )
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            allowed = await client.options(
                "/v1/organizations/acme/jobs/role/applications",
                headers={
                    "Origin": "https://workspace.example.test",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization,x-connectmd-purpose",
                },
            )
            denied = await client.options(
                "/v1/organizations/acme/jobs/role/applications",
                headers={
                    "Origin": "https://workspace.example.test",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization,x-private-override",
                },
            )
    finally:
        await app.state.engine.dispose()

    assert allowed.status_code == 200
    allowed_headers = {
        value.strip().lower()
        for value in allowed.headers["access-control-allow-headers"].split(",")
    }
    assert "x-connectmd-purpose" in allowed_headers
    assert allowed.headers["access-control-allow-origin"] == "https://workspace.example.test"
    assert denied.status_code == 400
    assert "x-private-override" not in allowed_headers
