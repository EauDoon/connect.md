#!/usr/bin/env python3
"""Verify the site contract: guest-only promises hold and the network MVP
routes are active while the retired backend surfaces stay blocked.

Evolves the former standalone check (ADR 0002): the retired routes list
shrinks because account, network, discover, inbox, conversations, and p are
now live network MVP surfaces, and the guest-builder guarantees (no draft
upload, no analytics) are still enforced.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
ACTIVE_ROUTES = {"human", "md", "trust", "account", "network", "discover", "inbox", "conversations"}
RETIRED_ROUTES = {
    "agent-directory", "agents", "appeal-review", "applications", "employer",
    "feed", "jobs", "messages", "moderation", "moderation-review",
    "organizations", "posts", "r", "representatives", "search",
    "verification-review", "workspace",
}
# p/[handle] is a dynamic public route; it is live but not in ACTIVE_ROUTES set form.
NETWORK_API_ROUTES = [
    "accounts/register",
    "accounts/login",
    "accounts/logout",
    "session",
    "profile",
    "profile/publish",
    "profile/unpublish",
    "contacts",
    "conversations",
    "agent-grants",
    "agent/profile",
    "agent/contacts",
    "public/profiles",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def array_hrefs(source: str, name: str) -> list[str]:
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.DOTALL)
    if not match:
        raise ValueError(f"{name} is missing")
    return re.findall(r'href: "([^"]+)"', match.group(1))


def main() -> None:
    errors: list[str] = []
    app = WEB / "app"

    # Retained retired pages may stay in-tree; middleware must block them all.
    middleware = read(WEB / "middleware.ts")
    matchers = set(re.findall(r'"(/[^"]+/:path\*)"', middleware))
    expected_matchers = {f"/{route}/:path*" for route in RETIRED_ROUTES}
    if matchers != expected_matchers:
        errors.append(
            "middleware retired-route mismatch: "
            f"missing={sorted(expected_matchers - matchers)} "
            f"extra={sorted(matchers - expected_matchers)}"
        )
    for marker in ("status: 404", '"Cache-Control": "private, no-store, max-age=0"', '"X-Robots-Tag": "noindex, nofollow"'):
        if marker not in middleware:
            errors.append(f"middleware is missing {marker}")

    # The guest builder keeps its no-upload, no-analytics promises.
    for guest in ("human", "md"):
        guest_dir = app / guest
        if not guest_dir.exists():
            errors.append(f"guest route {guest} is missing")
    guest_sources = {
        path.relative_to(WEB): read(path)
        for path in [
            WEB / "components" / "draft-provider.tsx",
            WEB / "components" / "human-builder.tsx",
            WEB / "components" / "markdown-editor.tsx",
        ]
    }
    for path, source in guest_sources.items():
        for marker in ("fetch(", "localStorage", "XMLHttpRequest", "navigator.sendBeacon"):
            if marker in source:
                errors.append(f"{path} (guest surface) contains network/storage marker {marker}")

    # Network MVP API routes exist.
    api_root = app / "api" / "network" / "v1"
    for route in NETWORK_API_ROUTES:
        if not (api_root / route / "route.ts").exists():
            errors.append(f"network API route missing: {route}")

    # Domain modules exist.
    for module in ("identity.ts", "secrets.ts", "contact.ts", "agent-grants.ts", "db.ts", "auth-service.ts", "profiles.ts", "contacts.ts", "conversations.ts", "agent-service.ts"):
        if not (WEB / "lib" / "network" / module).exists():
            errors.append(f"network domain module missing: {module}")

    # The database contract is explicit in the env example.
    env = read(WEB / ".env.example")
    if "CONNECTMD_NETWORK_DATABASE_URL=" not in env:
        errors.append(".env.example is missing CONNECTMD_NETWORK_DATABASE_URL")
    if "NEXT_PUBLIC_SITE_URL=https://connect-md.vercel.app" not in env:
        errors.append(".env.example is missing NEXT_PUBLIC_SITE_URL")

    deployment = read(WEB / "lib" / "deployment-config.ts")
    for marker in ("connect-src 'self'", "worker-src 'self' blob:", "NEXT_PUBLIC_SITE_URL"):
        if marker not in deployment:
            errors.append(f"deployment config is missing {marker}")

    for name in ("agent-readme.md", "llms.txt"):
        contents = read(WEB / "public" / name)
        for marker in ("browser", "download"):
            if marker not in contents:
                errors.append(f"public/{name} is missing {marker}")

    download = read(WEB / "components" / "publish-panel.tsx")
    for marker in ("new Blob([markdown]", "anchor.click()", "anchor.remove()", "URL.revokeObjectURL(objectUrl)"):
        if marker not in download:
            errors.append(f"local download is missing {marker}")

    if errors:
        print(f"site contract: FAIL ({len(errors)} error(s))")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print(f"site contract: PASS ({len(RETIRED_ROUTES)} retired routes blocked, network MVP routes active)")


if __name__ == "__main__":
    main()
