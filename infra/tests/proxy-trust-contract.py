"""Hermetic counterexamples for connect.md's singleton reverse-proxy trust boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

ROOT = Path(__file__).resolve().parents[2]
TRUSTED_PROXY = "172.31.254.2"


async def observed_client(peer: str, forwarded_for: str) -> tuple[str, int]:
    captured: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        captured.update(scope)

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=[TRUSTED_PROXY])
    scope = {
        "type": "http",
        "scheme": "http",
        "client": (peer, 43210),
        "headers": [(b"x-forwarded-for", forwarded_for.encode("ascii"))],
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        return None

    await middleware(scope, receive, send)
    return captured["client"]


compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
services = compose["services"]
api = services["api"]
nginx = services["nginx"]
edge = compose["networks"]["connectmd_app"]

command = api["command"]
flag = command.index("--forwarded-allow-ips")
assert command[flag + 1] == TRUSTED_PROXY
assert command[flag + 1] != "*" and "/" not in command[flag + 1]
assert nginx["networks"]["connectmd_app"]["ipv4_address"] == TRUSTED_PROXY
assert edge["ipam"]["config"] == [{"subnet": "172.31.254.0/24"}]
assert "ports" not in api and "expose" not in api

# A direct request from any untrusted container cannot forge client identity.
assert asyncio.run(observed_client("172.31.254.19", "198.51.100.77")) == (
    "172.31.254.19",
    43210,
)
# For the trusted Nginx peer, Uvicorn's rightmost-untrusted walk ignores
# attacker-controlled leading entries in Nginx's appended chain.
assert asyncio.run(observed_client(TRUSTED_PROXY, "198.51.100.77, 203.0.113.25")) == (
    "203.0.113.25",
    0,
)

print("PROXY_TRUST_CONTRACT=PASS")
