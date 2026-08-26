"""Fail-closed checks for truthful public-profile Agent Identity failures."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from .platform_checker_source import (
        append_error,
        read_anchor_source,
        require_source_markers,
    )
except ImportError:
    from platform_checker_source import (
        append_error,
        read_anchor_source,
        require_source_markers,
    )


PUBLIC_DETAIL_TEST = "apps/web/tests/public-detail-ux.test.ts"


def public_profile_identity_errors(root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "apps/web/app/p/[handle]/page.tsx": {
            "secondary failure state": (
                "agentIdentitiesUnavailable={identities === null}"
            ),
        },
        "apps/web/components/public-document-page.tsx": {
            "unavailable-state guard": (
                "agentIdentitiesUnavailable && agentIdentities.length === 0"
            ),
            "truthful unavailable heading": "Published Agent Identities unavailable",
            "profile continuity disclosure": "This profile remains available",
            "bounded retry destination": "Open Agent Directory",
        },
        PUBLIC_DETAIL_TEST: {
            "profile content continuity assertion": (
                'expect(unavailableMarkup).toContain("Ari Chen")'
            ),
            "canonical Markdown continuity assertion": (
                'expect(unavailableMarkup).toContain("View canonical Markdown")'
            ),
            "unavailable disclosure assertion": (
                'expect(unavailableMarkup).toContain("Published Agent Identities unavailable")'
            ),
            "failed-data contact exclusion": (
                'expect(unavailableMarkup).not.toContain("Prepare private contact request")'
            ),
            "empty-result distinction": (
                'expect(emptyMarkup).not.toContain("Published Agent Identities unavailable")'
            ),
        },
    }
    for relative_path, markers in required.items():
        source = read_anchor_source(root, relative_path, errors)
        require_source_markers(source, relative_path, markers, errors)

    registry_path = "packages/platform-contract/platform-features.json"
    registry_source = read_anchor_source(root, registry_path, errors)
    try:
        features = json.loads(registry_source).get("features", [])
    except (AttributeError, json.JSONDecodeError) as exc:
        append_error(
            errors,
            f"repository.anchors.{registry_path}",
            f"cannot parse registry: {exc}",
        )
        return errors
    by_id = {
        feature.get("id"): feature
        for feature in features
        if isinstance(feature, dict) and isinstance(feature.get("id"), str)
    }
    for feature_id in ("canonical-documents", "agent-representation-outreach"):
        tests = by_id.get(feature_id, {}).get("tests", [])
        if PUBLIC_DETAIL_TEST not in tests:
            append_error(
                errors,
                f"registry.features.{feature_id}.tests",
                f"must include {PUBLIC_DETAIL_TEST}",
            )
    return errors
