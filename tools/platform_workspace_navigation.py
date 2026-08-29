"""Fail-closed checks for the standalone public navigation shell."""

from __future__ import annotations

from pathlib import Path

try:
    from .platform_checker_source import read_anchor_source, require_source_markers
except ImportError:
    from platform_checker_source import read_anchor_source, require_source_markers


def workspace_navigation_errors(root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "apps/web/app/layout.tsx": {
            "static root header": "<SiteHeader />",
            "browser draft provider": "<Providers>",
        },
        "apps/web/components/site-header.tsx": {
            "standalone primary navigation": "PUBLIC_PRIMARY_NAVIGATION",
            "standalone utility navigation": "PUBLIC_UTILITY_NAVIGATION",
        },
        "apps/web/middleware.ts": {
            "retired workspace route": '"/workspace/:path*"',
            "bounded retired-route response": "status: 404",
        },
        "apps/web/tests/private-route-gate.test.ts": {
            "standalone route contract": "standalone route boundary",
            "bounded route behavior": "returns the same bounded no-store 404 for every retired route",
        },
        "apps/web/tests/site-header-truthfulness.test.ts": {
            "standalone header behavior": "exposes only create, Markdown, and trust navigation",
            "no server auth decision": "requires no server auth decision in the root layout",
        },
    }
    for relative_path, markers in required.items():
        source = read_anchor_source(root, relative_path, errors)
        require_source_markers(source, relative_path, markers, errors)
    return errors
