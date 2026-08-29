#!/usr/bin/env python3
"""Verify the public Vercel site stays standalone."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
ACTIVE_ROUTES = {"human", "md", "trust"}


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
    route_roots = {
        entry.name
        for entry in app.iterdir()
        if entry.is_dir() and any(entry.rglob("page.tsx"))
    }
    retired_routes = route_roots - ACTIVE_ROUTES

    middleware = read(WEB / "middleware.ts")
    matchers = set(re.findall(r'"(/[^"]+/:path\*)"', middleware))
    expected_matchers = {f"/{route}/:path*" for route in retired_routes}
    if matchers != expected_matchers:
        errors.append(
            "middleware retired-route mismatch: "
            f"missing={sorted(expected_matchers - matchers)} "
            f"extra={sorted(matchers - expected_matchers)}"
        )
    for marker in ("status: 404", '"Cache-Control": "private, no-store, max-age=0"', '"X-Robots-Tag": "noindex, nofollow"'):
        if marker not in middleware:
            errors.append(f"middleware is missing {marker}")

    navigation = read(WEB / "lib" / "navigation.ts")
    expected_navigation = {
        "PUBLIC_PRIMARY_NAVIGATION": ["/human", "/md"],
        "PUBLIC_UTILITY_NAVIGATION": ["/trust"],
    }
    for name, expected in expected_navigation.items():
        try:
            actual = array_hrefs(navigation, name)
        except ValueError as error:
            errors.append(str(error))
        else:
            if actual != expected:
                errors.append(f"{name} must be {expected}, got {actual}")

    sitemap = read(app / "sitemap.ts")
    sitemap_routes = re.findall(r'absoluteSiteUrl\("([^"]+)"\)', sitemap)
    if sitemap_routes != ["/", "/human", "/md", "/trust"]:
        errors.append(f"sitemap must contain only standalone routes, got {sitemap_routes}")

    active_sources = {
        path.relative_to(WEB): read(path)
        for path in [
            app / "layout.tsx",
            app / "page.tsx",
            app / "trust" / "page.tsx",
            WEB / "components" / "agent-handoff.tsx",
            WEB / "components" / "human-builder.tsx",
            WEB / "components" / "markdown-editor.tsx",
            WEB / "components" / "publish-panel.tsx",
            WEB / "components" / "site-header.tsx",
        ]
    }
    banned = (
        "NEXT_PUBLIC_API_BASE_URL",
        "CONNECTMD_API_BASE_URL",
        "privateWorkspaceConfiguredFromEnvironment",
        "recruitingReleaseEnabled",
        "auth-provider",
        "Clerk",
    )
    for path, source in active_sources.items():
        for marker in banned:
            if marker in source:
                errors.append(f"{path} contains retired marker {marker}")

    deployment = read(WEB / "lib" / "deployment-config.ts")
    for marker in ("connect-src 'self'", "worker-src 'self' blob:", "NEXT_PUBLIC_SITE_URL"):
        if marker not in deployment:
            errors.append(f"deployment config is missing {marker}")
    for marker in ("CLERK", "NEXT_PUBLIC_API_BASE_URL"):
        if marker in deployment:
            errors.append(f"deployment config contains retired marker {marker}")

    environment = [
        line
        for line in read(WEB / ".env.example").splitlines()
        if line and not line.startswith("#")
    ]
    if environment != ["NEXT_PUBLIC_SITE_URL=https://connect-md.vercel.app"]:
        errors.append(f"unexpected Vercel environment contract: {environment}")

    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")
    for retired_job in ("api", "infrastructure"):
        if f"\n  {retired_job}:" in workflow:
            errors.append(f"CI still runs retired {retired_job} job")

    for name in ("agent-readme.md", "llms.txt"):
        contents = read(WEB / "public" / name)
        for marker in ("browser", "no publishing API", "download"):
            if marker not in contents:
                errors.append(f"public/{name} is missing {marker}")

    playwright = read(WEB / "playwright.config.ts") + read(WEB / "e2e" / "production-runtime.mjs")
    if playwright.count("standalone-release.spec.ts") != 2:
        errors.append("Playwright must run only the standalone release spec")
    release_spec = read(WEB / "e2e" / "standalone-release.spec.ts")
    for marker in ("retired backend routes fail closed", "no publishing API", "serious accessibility checks"):
        if marker not in release_spec:
            errors.append(f"standalone browser release spec is missing {marker}")

    download = read(WEB / "components" / "publish-panel.tsx")
    for marker in ("new Blob([markdown]", "anchor.click()", "anchor.remove()", "URL.revokeObjectURL(objectUrl)"):
        if marker not in download:
            errors.append(f"local download is missing {marker}")

    if errors:
        print(f"standalone Vercel site: FAIL ({len(errors)} error(s))")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print(f"standalone Vercel site: PASS ({len(retired_routes)} retired routes blocked)")


if __name__ == "__main__":
    main()
