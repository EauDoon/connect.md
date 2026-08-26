"""Fail-closed checks for the runtime private-workspace navigation shell."""

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
            "request-time root shell": 'export const dynamic = "force-dynamic";',
            "server-only workspace decision": "privateWorkspaceConfiguredFromEnvironment()",
            "header workspace state": (
                "<SiteHeader privateWorkspacesEnabled={privateWorkspacesEnabled} />"
            ),
        },
        "apps/web/lib/private-workspace-config.ts": {
            "server-only module": 'import "server-only";',
            "publishable-key input": "process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
            "runtime secret input": "process.env.CLERK_SECRET_KEY",
        },
        "apps/web/components/site-header.tsx": {
            "combined server and client gate": (
                "privateNavigationEnabled = privateWorkspacesEnabled && configured"
            ),
            "public navigation fallback": (
                "privateNavigationEnabled ? PRIMARY_NAVIGATION : PUBLIC_PRIMARY_NAVIGATION"
            ),
        },
        "apps/web/tests/private-route-gate.test.ts": {
            "runtime-only secret assertion": (
                'expect(frontend.build.args).not.toHaveProperty("CLERK_SECRET_KEY")'
            ),
            "runtime shell assertion": (
                "expect(rootLayout).toContain('export const dynamic = \"force-dynamic\";')"
            ),
        },
        "apps/web/tests/site-header-truthfulness.test.ts": {
            "partial configuration behavior": (
                "keeps private destinations and sign-in controls hidden when server auth is incomplete"
            ),
            "complete configuration behavior": (
                "shows configured private destinations to a signed-in account"
            ),
            "root runtime assertion": (
                "expect(layout).toContain('export const dynamic = \"force-dynamic\";')"
            ),
        },
    }
    for relative_path, markers in required.items():
        source = read_anchor_source(root, relative_path, errors)
        require_source_markers(source, relative_path, markers, errors)
    return errors
