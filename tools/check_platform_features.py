#!/usr/bin/env python3
"""Fail-closed validation for the connect.md platform feature registry."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

try:
    from .platform_contract_inventory import *
except ImportError:
    import sys as _sys

    _tools_directory = str(Path(__file__).resolve().parent)
    if _tools_directory not in _sys.path:
        _sys.path.insert(0, _tools_directory)
    from platform_contract_inventory import *

try:
    from .platform_contact_durability import (
        contact_durability_errors as _contact_durability_errors_impl,
    )
except ImportError:
    from platform_contact_durability import (
        contact_durability_errors as _contact_durability_errors_impl,
    )

try:
    from .platform_route_ownership import (
        UI_ROUTE_RE as _UI_ROUTE_RE,
    )
    from .platform_route_ownership import (
        load_route_ownership as _load_route_ownership,
    )
    from .platform_route_ownership import (
        route_ownership_parity_errors as _route_ownership_parity_errors,
    )
    from .platform_route_ownership import (
        ui_route_ownership_parity_errors as _ui_route_ownership_parity_errors,
    )
    from .platform_route_test_ownership import validate_source_witnesses as _rte
except ImportError:
    from platform_route_ownership import (
        UI_ROUTE_RE as _UI_ROUTE_RE,
    )
    from platform_route_ownership import (
        load_route_ownership as _load_route_ownership,
    )
    from platform_route_ownership import (
        route_ownership_parity_errors as _route_ownership_parity_errors,
    )
    from platform_route_ownership import (
        ui_route_ownership_parity_errors as _ui_route_ownership_parity_errors,
    )
    from platform_route_test_ownership import validate_source_witnesses as _rte

try:
    from .platform_checker_ast import (
        _a2a_action_branches,
        _discovery_function_strings,
        _lifecycle_discovery_errors,
        _literal_string_lists,
        _literal_string_values,
        _llms_workflow_errors,
        _static_string,
        _string_bindings,
    )
except ImportError:
    from platform_checker_ast import (
        _a2a_action_branches,
        _discovery_function_strings,
        _lifecycle_discovery_errors,
        _literal_string_lists,
        _literal_string_values,
        _llms_workflow_errors,
        _static_string,  # noqa: F401
        _string_bindings,  # noqa: F401
    )

try:
    from .platform_schema_contract import (
        evidence_schema_is_expected as _evidence_schema_is_expected_impl,
    )
    from .platform_schema_contract import (
        schema_is_expected as _schema_is_expected_impl,
    )
except ImportError:
    from platform_schema_contract import (
        evidence_schema_is_expected as _evidence_schema_is_expected_impl,
    )
    from platform_schema_contract import (
        schema_is_expected as _schema_is_expected_impl,
    )

try:
    from .platform_checker_source import append_error as _error
    from .platform_checker_source import (
        ordered_anchor_positions as _ordered_anchor_positions,
    )
    from .platform_checker_source import read_anchor_source as _read_anchor_source
    from .platform_checker_source import (
        require_source_markers as _require_source_markers,
    )
    from .platform_human_mode import (
        human_mode_surface_errors as _human_mode_surface_errors,
    )
    from .platform_public_profile import public_profile_identity_errors
    from .platform_workspace_navigation import workspace_navigation_errors
except ImportError:
    from platform_checker_source import append_error as _error
    from platform_checker_source import (
        ordered_anchor_positions as _ordered_anchor_positions,
    )
    from platform_checker_source import read_anchor_source as _read_anchor_source
    from platform_checker_source import (
        require_source_markers as _require_source_markers,
    )
    from platform_human_mode import (
        human_mode_surface_errors as _human_mode_surface_errors,
    )
    from platform_public_profile import public_profile_identity_errors
    from platform_workspace_navigation import workspace_navigation_errors

STAGES = {
    "design",
    "implemented",
    "feature_gated",
    "repository_verified",
    "deployment_verified",
    "releasable",
    "disabled",
}
LIFECYCLE_STATES = {
    "implemented",
    "feature_gated",
    "design",
    "disabled",
    "not_applicable",
}
CLASSIFICATIONS = {"public", "private", "mixed", "none"}
SEARCH_MODES = {"indexed", "excluded", "not_applicable"}
DISCOVERY_STATES = {"advertised", "denied", "hidden", "not_applicable"}
DISCOVERY_SURFACES = {"openapi", "capabilities", "llms", "mcp", "a2a", "agent_card"}
GATES = {"not_applicable", "enabled", "disabled_by_default", "absence_enforced"}
REQUIRED_FEATURE_IDS = {
    "canonical-documents",
    "document-ingestion",
    "public-search",
    "agent-authority",
    "agent-protocols",
    "agent-representation-outreach",
    "private-social-graph",
    "verified-recruitment",
    "professional-posts-moderation",
    "private-workspace-navigation",
    "retention-executor",
    "account-lifecycle",
    "external-egress",
    "production-operations",
}
REQUIRED_FEATURE_CONSTRAINTS = {
    "canonical-documents": {
        "guided input cleanup flush before unmount or mode switch",
        "timer and blur buffered flush",
        "fail-closed raw Markdown preservation",
        "explicit-name resume DigitalDocument structured data",
        "single-active-stage Human Mode composition",
        "accessible current-stage stepper and explicit chapter actions",
        "one-shot heading focus with a reduced-motion boundary",
        "mobile non-duplicating stage dock and 44px controls",
        "BufferedCommitRegistry flush before stage and release",
        "canonical Markdown ref serialization for buffered narrative",
        "Work-mode fieldset and legend",
        "Human Mode has no Monaco import",
        "production Playwright Human Mode and anonymous-boundary gate",
        "MCP list_my_documents is owner-scoped and cursor/resource bound",
        "exact strong document If-Match across HTTP MCP and service with replay first",
    },
    "public-search": {
        "exact PostgreSQL search is explicit and never falls back to Meilisearch",
        "exact cursor binds projection state, taxonomy revision, filters, and sort",
        "exact backfill and integrity verification are locked-state release barriers",
        "scalar singleton GET search location_id with duplicate rejection",
        "strict nonblank bounded optional cursors, duplicate rejection, and signed continuations across exact search and taxonomy REST MCP A2A",
        "public trust page is plain-language current behavior, not a legal privacy policy or agent authority",
    },
    "agent-protocols": {
        "discovery agreement compares capabilities MCP A2A Agent Card llms-full and OpenAPI",
        "Agent Card credential and required-header authority",
        "valid-envelope action validation returns terminal A2A tasks",
        "mandate-bound MCP outreach and status share canonical HTTP authority",
        "MCP outreach replay binds mandate source-handle and grant digests",
        "MCP outreach replay reauthorizes live mandate and identity state",
        "MCP caller key and privacy-safe outreach receipt/status envelopes",
        "human-only contact decisions and no external delivery",
        "capabilities and llms-full name the MCP outreach tools",
        "Agent Card retains seven A2A skills and does not advertise MCP tools",
        "MCP list_my_documents has strict kind/limit/cursor schema and owner/resource-bound pagination",
    },
    "agent-representation-outreach": {
        "required idempotency headers on contact and mandate outreach",
        "visible-ASCII idempotency keys for contact policy and decision writes",
        "contact-policy body and If-Match idempotency fingerprint",
        "mandatory exact strong contact-policy If-Match and 200 ETag OpenAPI",
        "contact-policy replay before stale If-Match precondition",
        "owner-namespaced PostgreSQL contact-policy advisory serialization",
        "web contact-policy ETag/If-Match precondition-bound logical attempts",
        "strict nonblank bounded optional directory cursors, duplicate rejection, and eligibility-bound signed continuations across REST OpenAPI MCP A2A",
        "contact decision authority before replay",
        "non-Clerk agent-outreach decision exclusion before request lock",
        "pre-lock and post-lock contact decision replay",
        "ContactRequest to ContactPolicy to ContactBlock lock ordering",
        "safe contact-policy receipt and empty hash-bound decision receipt",
        "report-reason privacy and fail-closed contact decision replay",
        "MCP and A2A cannot decide contact requests",
        "Clerk-human-only Agent Identity create and withdraw",
        "visible-ASCII caller keys on Agent Identity writes",
        "atomic Agent Identity event and IdempotencyRecord commit",
        "digest-bound Agent Identity create and withdraw replay",
        "Agent Identity replay corruption and state drift fail closed",
        "withdrawn identities leave public directory and handle resolution",
        "API keys Agent Grants MCP and A2A cannot create or withdraw Agent Identities",
        "canonical lowercase UUID outreach status for MCP and A2A parity",
    },
    "agent-authority": {
        "proposal-decision digest-bound receipt reconstruction",
        "owner-only API-key lifecycle",
        "secret-free API-key idempotency receipts",
        "human-only bounded recent changes with no private payloads",
        "impersonated Clerk principals are read-only before lookup or persistence",
        "fixed trusted-proxy forwarding boundary",
        "JWKS unknown-kid cooldown and generation-coalesced refresh",
    },
    "verified-recruitment": {
        "Clerk-only organization membership invitation, acceptance, and removal",
        "organization-first membership locks",
        "current-owner authority before membership-removal replay",
        "generation-bound invitation and acceptance receipts",
        "exact empty membership-removal replay",
        "visible-ASCII idempotency keys on application withdrawal and employer decisions",
        "organization-job-application transition lock ordering",
        "current recruiting authority before employer decision replay",
        "applicant owner-bound pre-lock and post-lock withdrawal replay",
        "privacy-safe application transition digest reconstruction",
        "empty application transition receipts and safe events",
        "fail-closed application transition replay integrity",
        "MCP and A2A cannot transition applications",
        "application submission receipts remain untouched",
        "public organization and job pagination uses active verification, set-based evidence, and limit-plus-one cursors",
        "application decisions emit one applicant-only metadata notification without reviewer or message content",
        "configured Clerk-human reviewer evidence authority",
        "verified recruiting evidence is private, verified, and fail closed",
        "hidden reviewer evidence artifact route and discovery exclusion",
        "artifact staging and database commit reconciliation",
        "artifact reconciler readiness gate",
    },
    "private-social-graph": {
        "subject-current private collection reads",
        "per-slice refresh and pagination coordination",
        "current-success-only dependent writes",
        "truthful retained-data refresh errors",
        "conversation-scoped generation and interaction coordination",
        "current-primary recovery gates private interactions",
        "durable connection deletion idempotency",
        "late same-intent receipt race rollback and replay",
        "durable follow idempotency replay and privacy-minimal receipt",
        "follow and content-block pair-lock integrity",
        "MCP and A2A cannot mutate follows or content blocks",
    },
    "professional-posts-moderation": {
        "durable content-block idempotency replay and privacy-minimal receipt",
        "follow and content-block pair-lock integrity",
        "strict private post-control mutation responses",
        "MCP and A2A cannot mutate follows or content blocks",
        "anonymous metadata-only public post inventory ordered by published_at and id",
        "public post inventory has bounded signed cursor and no ranking or Meilisearch body projection",
        "subject-only moderation case and appeal HTTP core",
        "private reviewer HTTP core is noindex, no-store, human moderator only, and exact decision receipts",
        "public post reads advertise text/markdown OpenAPI responses",
    },
    "account-lifecycle": {
        "Clerk-human-only confirmation claims",
        "confirmation HMAC durability through the terminal receipt window",
        "strict exact 202 confirmation replay",
        "receipt-only no-store and noindex terminal status",
        "tombstone journal live-mirror provider and backup-obligation terminal proof",
        "disabled-by-default lifecycle gate",
    },
    "production-operations": {
        "immutable upstream GitHub Actions and OCI image references",
        "source revision and API/web/Nginx image identity restore preflight",
        "frontend Docker context excludes secrets and generated artifacts",
        "frontend Docker context retains .env.example and required build inputs",
        "eight protected Python services (including the exact-search-admin profile) use UID 10001 read-only roots cap-drop-all and no-new-privileges",
        "API 64MiB tmpfs and lifecycle 16MiB heartbeat mount contracts",
        "semantic Compose verifier rejects duplicate keys hardening regressions and production overrides",
        "CI invokes the semantic Compose verifier once after requirements-test.lock provisions PyYAML",
        "release acceptance receipt v2 binds exact source and exact-search evidence",
        "release acceptance revalidates all required runtime health before receipt or active-marker mutation",
        "fixed trusted-proxy boundary is repository evidence only",
    },
}
UI_ROUTE_RE = _UI_ROUTE_RE
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODEL_RE = re.compile(r"^class\s+([A-Z][A-Za-z0-9]*)\(Base\):", re.MULTILINE)
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
EVIDENCE_RECEIPT_FIELDS = {
    "schema_version",
    "evidence_type",
    "feature_id",
    "source_revision",
    "recorded_at",
    "reviewer",
    "target",
    "configuration_scope",
    "checks",
}
EVIDENCE_CHECK_FIELDS = {
    "check_id",
    "command",
    "result",
    "output_path",
    "output_sha256",
}
REQUIRED_FEATURE_ANCHORS = {
    "canonical-documents": {
        "implementation": {
            "apps/api/app/main.py",
            "apps/web/app/p/[handle]/page.tsx",
            "apps/web/app/r/[slug]/page.tsx",
            "apps/web/app/robots.ts",
            "apps/web/app/sitemap.ts",
            "apps/web/components/human-builder.tsx",
            "apps/web/components/load-existing-panel.tsx",
            "apps/web/components/public-document-page.tsx",
            "apps/web/lib/guided-sections.ts",
            "apps/web/lib/human-input.ts",
            "apps/web/lib/logical-mutation.ts",
            "apps/web/lib/public-document.ts",
        },
        "tests": {
            "apps/api/tests/test_protocol_core.py",
            "apps/web/tests/guided-sections.test.ts",
            "apps/web/tests/human-builder-v2.test.ts",
            "apps/web/tests/human-input.test.ts",
            "apps/web/tests/load-existing-panel.test.ts",
            "apps/web/tests/logical-mutation.test.ts",
            "apps/web/tests/public-projections.test.ts",
            "apps/web/tests/sitemap.test.ts",
            "apps/web/e2e/public-release.spec.ts",
            "apps/web/e2e/production-harness.mjs",
        },
    },
    "agent-authority": {
        "implementation": set(
            "apps/api/app/auth.py apps/api/app/main.py apps/api/app/services/api_key_replay.py "  # noqa: SIM905
            "apps/api/app/services/documents.py apps/web/components/agent-delegation-panels.tsx "
            "apps/web/lib/agent-delegation-state.ts apps/web/lib/agent-api.ts "
            "apps/web/lib/logical-mutation.ts".split()
        ),
        "tests": set(
            "apps/api/tests/test_agent_grant_atomicity.py apps/api/tests/test_api_key_atomicity.py "  # noqa: SIM905
            "apps/api/tests/test_impersonation_authority.py apps/api/tests/test_protocol_core.py "
            "apps/web/tests/agent-delegation-manager.test.ts "
            "apps/web/tests/agent-integration-panel.test.ts apps/web/tests/logical-mutation.test.ts".split()
        ),
    },
    "agent-protocols": {
        "implementation": {
            "apps/api/app/http/origin.py",
            "apps/api/app/main.py",
            "apps/api/app/routes/discovery.py",
            "apps/api/app/routes/protocol_metadata.py",
        },
        "tests": {"apps/api/tests/test_protocol_core.py"},
    },
    "public-search": {
        "implementation": {"apps/web/app/trust/page.tsx"},
        "tests": {
            "apps/api/tests/test_protocol_core.py",
            "apps/web/tests/public-trust.test.ts",
        },
    },
    "agent-representation-outreach": {
        "implementation": {
            "apps/api/alembic/versions/0023_contact_request_status_constraint.py",
            "apps/api/app/main.py",
            "apps/api/app/models.py",
            "apps/web/app/robots.ts",
            "apps/web/app/sitemap.ts",
            "apps/web/components/agent-identity-manager.tsx",
            "apps/web/lib/agent-identity-api.ts",
            "apps/web/lib/logical-mutation.ts",
            "docs/agent-interoperability.md",
        },
        "tests": {
            "apps/api/tests/test_contact_durability.py",
            "apps/api/tests/test_agent_identity_lifecycle_durability.py",
            "apps/api/tests/test_agent_identity_directory.py",
            "apps/api/tests/test_migrations.py",
            "apps/api/tests/test_protocol_core.py",
            "apps/web/tests/logical-mutation.test.ts",
            "apps/web/tests/agent-identity-api.test.ts",
            "apps/web/tests/outreach-inbox.test.ts",
            "apps/web/tests/sitemap.test.ts",
        },
    },
    "private-social-graph": {
        "implementation": {
            "apps/web/components/conversation-thread.tsx",
            "apps/web/components/network-hub.tsx",
            "apps/web/components/network-panels.tsx",
            "apps/web/components/profile-connect-control.tsx",
            "apps/web/lib/auth-return-intent.ts",
            "apps/web/lib/logical-mutation.ts",
            "apps/web/lib/social-api.ts",
        },
        "tests": {
            "apps/api/tests/test_follow_block_durability.py",
            "apps/web/tests/auth-return-intent.test.ts",
            "apps/web/tests/conversation-thread.test.ts",
            "apps/web/tests/logical-mutation.test.ts",
            "apps/web/tests/network-hub.test.ts",
            "apps/web/tests/network-panels.test.ts",
            "apps/web/tests/profile-connect-control.test.ts",
            "apps/web/tests/social-api.test.ts",
        },
    },
    "verified-recruitment": {
        "implementation": {
            "apps/api/alembic/versions/0006_organization_verification.py",
            "apps/api/alembic/versions/0007_retention_executor.py",
            "apps/api/alembic/versions/0028_scrub_verification_change_payloads.py",
            "apps/api/app/main.py",
            "apps/api/app/services/artifact_durability.py",
            "apps/api/app/services/recruiting_evidence.py",
            "apps/web/app/discover/page.tsx",
            "apps/web/app/jobs/[organizationSlug]/[jobSlug]/page.tsx",
            "apps/web/app/jobs/page.tsx",
            "apps/web/app/organizations/[slug]/page.tsx",
            "apps/web/app/organizations/page.tsx",
            "apps/web/app/page.tsx",
            "apps/web/app/robots.ts",
            "apps/web/app/sitemap.ts",
            "apps/web/app/trust/page.tsx",
            "apps/web/app/verification-review/page.tsx",
            "apps/web/components/discover-hub.tsx",
            "apps/web/components/employer-inventory-panels.tsx",
            "apps/web/components/verification-evidence-viewer.tsx",
            "apps/web/lib/recruiting-release.ts",
            "apps/web/lib/recruiting-evidence-api.ts",
            "apps/web/lib/logical-mutation.ts",
        },
        "tests": {
            "apps/api/tests/test_artifact_durability.py",
            "apps/api/tests/test_application_decision_durability.py",
            "apps/api/tests/test_application_snapshot_atomicity.py",
            "apps/api/tests/test_cli_recruiting_evidence.py",
            "apps/api/tests/test_migrations.py",
            "apps/api/tests/test_recruiting_evidence_service.py",
            "apps/api/tests/test_recruiting_verification_evidence.py",
            "apps/api/tests/test_social_core.py",
            "apps/web/e2e/public-release.spec.ts",
            "apps/web/tests/agent-first-landing.test.ts",
            "apps/web/tests/cold-start-honesty.test.ts",
            "apps/web/tests/discover-hub.test.ts",
            "apps/web/tests/employer-inventory-panels.test.ts",
            "apps/web/tests/logical-mutation.test.ts",
            "apps/web/tests/public-trust.test.ts",
            "apps/web/tests/recruiting-evidence-api.test.ts",
            "apps/web/tests/recruiting-release.test.ts",
            "apps/web/tests/sitemap.test.ts",
            "apps/web/tests/verification-evidence-viewer.test.ts",
            "apps/web/tests/verification-review-queue.test.ts",
        },
    },
    "professional-posts-moderation": {
        "implementation": {
            "apps/web/components/profile-post-controls.tsx",
            "apps/web/lib/auth-return-intent.ts",
            "apps/web/lib/logical-mutation.ts",
            "apps/web/lib/posts-api.ts",
        },
        "tests": {
            "apps/api/tests/test_follow_block_durability.py",
            "apps/web/tests/auth-return-intent.test.ts",
            "apps/web/tests/logical-mutation.test.ts",
            "apps/web/tests/posts-api.test.ts",
        },
    },
    "account-lifecycle": {
        "implementation": {
            "apps/api/alembic/versions/0024_lifecycle_confirmation_idempotency.py",
            "apps/api/app/auth.py",
            "apps/api/app/config.py",
            "apps/api/app/main.py",
            "apps/api/app/models.py",
            "apps/api/app/services/account_erasure.py",
            "apps/api/app/services/deletion_journal.py",
            "apps/web/components/account-privacy-center.tsx",
            "apps/web/Dockerfile",
            "apps/web/lib/account-lifecycle-api.ts",
            "compose.yaml",
            "compose.prod.yaml",
        },
        "tests": {
            "apps/api/tests/test_account_erasure.py",
            "apps/api/tests/test_account_lifecycle.py",
            "apps/api/tests/test_auth.py",
            "apps/api/tests/test_deletion_journal.py",
            "apps/web/tests/account-lifecycle-api.test.ts",
            "apps/web/tests/account-privacy-center.test.ts",
        },
        "operations": {"docs/account-lifecycle.md"},
        "gate_paths": {
            ".env.example",
            "apps/api/app/config.py",
            "apps/web/Dockerfile",
            "apps/web/lib/account-lifecycle-api.ts",
            "compose.yaml",
            "compose.prod.yaml",
        },
    },
    "production-operations": {
        "implementation": {
            "apps/api/app/main.py",
            "apps/web/.dockerignore",
            ".github/workflows/ci.yml",
            "compose.yaml",
            "compose.prod.yaml",
            "infra/scripts/deploy.sh",
            "infra/scripts/health.sh",
            "infra/scripts/release-accept.sh",
            "infra/scripts/restore.sh",
        },
        "tests": {
            "apps/api/tests/test_readiness.py",
            "infra/tests/operational-contracts.py",
            "infra/tests/recovery-roundtrip.sh",
        },
    },
}
REQUIRED_EVIDENCE_CHECK_IDS = {
    ("canonical-documents", "repository"): {
        "llms-raw-markdown-workflow",
        "human-guided-losslessness",
        "human-owned-inventory-states",
    },
    ("agent-protocols", "repository"): {"llms-raw-markdown-workflow"},
    ("public-search", "repository"): {"llms-raw-markdown-workflow"},
    ("account-lifecycle", "repository"): {"lifecycle-default-disabled"},
    ("production-operations", "repository"): {
        "migration-single-writer-barrier",
        "restore-preflight-before-mutation",
    },
}


def _object(
    value: Any, location: str, errors: list[str], required: set[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        _error(errors, location, "must be an object")
        return None
    unknown = set(value) - required
    missing = required - set(value)
    if unknown:
        _error(errors, location, f"has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        _error(errors, location, f"is missing fields: {', '.join(sorted(missing))}")
    return value


def _strings(
    value: Any, location: str, errors: list[str], *, nonempty: bool = False
) -> list[str]:
    if not isinstance(value, list):
        _error(errors, location, "must be an array")
        return []
    if nonempty and not value:
        _error(errors, location, "must not be empty")
    if not all(isinstance(item, str) and item for item in value):
        _error(errors, location, "must contain non-empty strings only")
    return [item for item in value if isinstance(item, str) and item]


def _validate_evidence_receipt(
    path: Path,
    expected_type: str,
    feature_id: str,
    location: str,
    errors: list[str],
    expected_revision: str | None = None,
    repo_root: Path | None = None,
) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _error(errors, location, f"cannot load evidence receipt: {exc}")
        return
    receipt = _object(
        payload,
        location,
        errors,
        {
            "schema_version",
            "evidence_type",
            "feature_id",
            "source_revision",
            "recorded_at",
            "reviewer",
            "target",
            "configuration_scope",
            "checks",
        },
    )
    if receipt is None:
        return
    if receipt.get("schema_version") != 1:
        _error(errors, f"{location}.schema_version", "must equal 1")
    if receipt.get("evidence_type") != expected_type:
        _error(
            errors,
            f"{location}.evidence_type",
            f"must equal {expected_type!r}",
        )
    if receipt.get("feature_id") != feature_id:
        _error(
            errors,
            f"{location}.feature_id",
            f"must equal {feature_id!r}",
        )
    revision = receipt.get("source_revision")
    if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
        _error(
            errors,
            f"{location}.source_revision",
            "must be an exact 40-character lowercase Git revision",
        )
    elif expected_revision is not None and revision != expected_revision:
        _error(
            errors,
            f"{location}.source_revision",
            f"must equal current repository revision {expected_revision}",
        )
    recorded_at = receipt.get("recorded_at")
    if not isinstance(recorded_at, str) or not UTC_TIMESTAMP_RE.fullmatch(recorded_at):
        _error(
            errors,
            f"{location}.recorded_at",
            "must be a UTC RFC 3339 timestamp ending in Z",
        )
    else:
        try:
            parsed_at = datetime.fromisoformat(f"{recorded_at[:-1]}+00:00")
        except ValueError:
            _error(
                errors,
                f"{location}.recorded_at",
                "must be a real calendar timestamp",
            )
        else:
            if parsed_at.utcoffset() != timedelta(0):
                _error(errors, f"{location}.recorded_at", "must be UTC")
    for field in ("reviewer", "target", "configuration_scope"):
        value = receipt.get(field)
        if not isinstance(value, str) or not value.strip():
            _error(errors, f"{location}.{field}", "must be a non-empty string")
    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks:
        _error(errors, f"{location}.checks", "must be a non-empty array")
        return
    observed_check_ids: set[str] = set()
    for index, raw_check in enumerate(checks):
        check_location = f"{location}.checks[{index}]"
        check = _object(
            raw_check,
            check_location,
            errors,
            EVIDENCE_CHECK_FIELDS,
        )
        if check is None:
            continue
        check_id = check.get("check_id")
        if not isinstance(check_id, str) or not ID_RE.fullmatch(check_id):
            _error(
                errors,
                f"{check_location}.check_id",
                "must be a lowercase hyphenated control identifier",
            )
        elif check_id in observed_check_ids:
            _error(
                errors,
                f"{check_location}.check_id",
                f"duplicates control check {check_id!r}",
            )
        else:
            observed_check_ids.add(check_id)
        command = check.get("command")
        if not isinstance(command, str) or not command.strip():
            _error(errors, f"{check_location}.command", "must be non-empty")
        if check.get("result") != "pass":
            _error(errors, f"{check_location}.result", "must equal 'pass'")
        output_path = check.get("output_path")
        output_sha256 = check.get("output_sha256")
        if not isinstance(output_sha256, str) or not SHA256_RE.fullmatch(output_sha256):
            _error(
                errors,
                f"{check_location}.output_sha256",
                "must be a lowercase SHA-256 digest",
            )
        if (
            not isinstance(output_path, str)
            or not output_path.startswith("evidence/platform/outputs/")
            or "\\" in output_path
            or any(part in {"", ".", ".."} for part in output_path.split("/"))
        ):
            _error(
                errors,
                f"{check_location}.output_path",
                "must be a safe path under evidence/platform/outputs",
            )
        elif repo_root is not None:
            output_file = repo_root.joinpath(*output_path.split("/")).resolve()
            try:
                output_file.relative_to(repo_root.resolve())
            except ValueError:
                _error(
                    errors,
                    f"{check_location}.output_path",
                    "resolves outside the repository",
                )
                continue
            try:
                size = output_file.stat().st_size
            except OSError as exc:
                _error(
                    errors,
                    f"{check_location}.output_path",
                    f"cannot read captured output: {exc}",
                )
                continue
            if size > 5 * 1024 * 1024:
                _error(
                    errors,
                    f"{check_location}.output_path",
                    "captured output exceeds the 5 MiB evidence limit",
                )
                continue
            actual_digest = hashlib.sha256(output_file.read_bytes()).hexdigest()
            if isinstance(output_sha256, str) and actual_digest != output_sha256:
                _error(
                    errors,
                    f"{check_location}.output_sha256",
                    f"does not match captured output {output_path!r}",
                )
    missing_check_ids = sorted(
        REQUIRED_EVIDENCE_CHECK_IDS.get((feature_id, expected_type), set())
        - observed_check_ids
    )
    if missing_check_ids:
        _error(
            errors,
            f"{location}.checks",
            f"is missing required control checks: {', '.join(missing_check_ids)}",
        )


def _evidence_receipt(
    value: str,
    expected_type: str,
    feature_id: str,
    root: Path,
    location: str,
    errors: list[str],
    expected_revision: str | None,
) -> None:
    parts = value.split("/")
    if (
        not value.startswith("evidence/platform/")
        or not value.endswith(".json")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in parts)
    ):
        _error(
            errors,
            location,
            "advanced stages require JSON evidence receipts under evidence/platform",
        )
        return
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        _error(errors, location, "evidence receipt resolves outside the repository")
        return
    _validate_evidence_receipt(
        candidate,
        expected_type,
        feature_id,
        location,
        errors,
        expected_revision,
        root,
    )


def _clean_git_revision(root: Path, errors: list[str]) -> str | None:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        _error(
            errors,
            "repository.evidence_revision",
            f"advanced stages require a committed Git revision: {exc}",
        )
        return None
    if not REVISION_RE.fullmatch(revision):
        _error(
            errors,
            "repository.evidence_revision",
            "git rev-parse did not return a full lowercase revision",
        )
        return None
    if status.strip():
        _error(
            errors,
            "repository.evidence_revision",
            "advanced stages require a clean working tree",
        )
        return None
    return revision


def _discovery_agreement_errors(root: Path) -> list[str]:
    """Compare explicit discovery inventories without equating unlike transports."""
    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    discovery_path = "apps/api/app/routes/discovery.py"
    agent_card_path = "apps/api/app/routes/agent_card.py"
    main_source = _read_anchor_source(root, main_path, errors)
    discovery_source = _read_anchor_source(root, discovery_path, errors)
    agent_card_source = _read_anchor_source(root, agent_card_path, errors)
    capabilities = _function_source(main_source, "capabilities", main_path, errors)
    mcp_tools = _function_source(main_source, "mcp_tools", main_path, errors)
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    try:
        discovery_document_tree = ast.parse(discovery_source)
        agent_card_tree = ast.parse(agent_card_source)
    except SyntaxError as exc:
        _error(errors, "repository.discovery", f"cannot parse source: {exc}")
        return errors

    capability_mcp = {
        value
        for values in _literal_string_lists(
            capabilities, "agent_tools", "capabilities.agent_tools", errors
        )
        for value in values
    }
    capability_mcp.update(
        value
        for values in _literal_string_lists(
            capabilities, "mcp_tools", "capabilities.mcp_tools", errors
        )
        for value in values
    )
    capability_a2a = {
        value
        for values in _literal_string_lists(
            capabilities, "a2a_actions", "capabilities.a2a_actions", errors
        )
        for value in values
    }
    actual_mcp = _literal_string_values(mcp_tools, "name", "mcp_tools", errors)
    if len(actual_mcp) != len(set(actual_mcp)):
        _error(
            errors,
            f"repository.discovery.{main_path}#mcp_tools",
            "tools/list contains duplicate tool names",
        )
    missing_mcp = sorted(capability_mcp - set(actual_mcp))
    if missing_mcp:
        _error(
            errors,
            f"repository.discovery.{main_path}#mcp_tools",
            "capabilities advertises MCP tool(s) absent from tools/list: "
            + ", ".join(missing_mcp),
        )

    actual_a2a = _a2a_action_branches(a2a, "a2a_send_message", errors)
    missing_a2a = sorted(capability_a2a - actual_a2a)
    if missing_a2a:
        _error(
            errors,
            f"repository.discovery.{main_path}#a2a_send_message",
            "capabilities advertises A2A action(s) absent from dispatch: "
            + ", ".join(missing_a2a),
        )

    card_strings = (
        _discovery_function_strings(
            agent_card_tree, ("agent_card",), "agent_card", errors
        )
        if agent_card_source
        else set()
    )
    card_actions = {
        "search",
        "list_taxonomies",
        "list_taxonomy_terms",
        "get_agent_identity",
        "list_agent_directory",
        "list_profile_agents",
        "contact_request",
        "agent_outreach",
        "get_agent_outreach_status",
    }
    card_surface = "\n".join(card_strings)
    for action in sorted(capability_a2a | card_actions):
        if action.lower() not in card_surface:
            _error(
                errors,
                f"repository.discovery.{agent_card_path}#agent_card",
                f"Agent Card is missing A2A action example {action!r}",
            )

    llms_strings = (
        _discovery_function_strings(
            discovery_document_tree, ("llms_full_txt",), "llms_full", errors
        )
        if discovery_source
        else set()
    )
    llms_surface = "\n".join(llms_strings)
    for value in sorted(capability_mcp | capability_a2a):
        if value.lower() not in llms_surface:
            _error(
                errors,
                f"repository.discovery.{discovery_path}#llms_full_txt",
                f"/llms-full.txt is missing advertised discovery operation {value!r}",
            )

    route_errors: list[str] = []
    route_inventory = _implemented_route_inventory(root, main_source, route_errors)
    errors.extend(route_errors)
    expected_openapi = {
        "GET /v1/capabilities": False,
        "GET /llms.txt": True,
        "GET /llms-full.txt": True,
        "GET /.well-known/agent-card.json": True,
        "POST /mcp": True,
        "POST /a2a/message:send": True,
    }
    for route, hidden in expected_openapi.items():
        record = route_inventory.routes.get(route)
        if record is None:
            _error(
                errors,
                f"repository.discovery.{main_path}",
                f"missing discovery route {route!r}",
            )
        elif record.hidden != hidden:
            state = "hidden" if hidden else "advertised"
            _error(
                errors,
                f"repository.discovery.{main_path}",
                f"{route!r} must remain {state} in OpenAPI",
            )
    return errors


def _mcp_write_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "apps/api/app/main.py": {
            "MCP create tool": '"name": "create_document"',
            "MCP proposal tool": '"name": "propose_document_update"',
            "strict create idempotency schema": '"required": ["kind", "markdown", "idempotency_key"]',
            "transport-scoped create operation": 'operation=f"MCP:create_document:{kind}"',
            "transport-scoped proposal operation": 'operation=f"MCP:propose_document_update:{kind}:{identifier}"',
            "shared create helper": "async def _create_document_write(",
            "shared update helper": "async def _update_document_write(",
            "shared proposal helper": "async def _submit_proposal_write(",
            "truthful MCP credential instruction": "Management tools require an authenticated Bearer credential with applicable scopes; proposal submission requires a proposal-only Agent Grant.",
        },
        "apps/api/tests/test_protocol_core.py": {
            "MCP create authority and replay": "test_mcp_create_document_matches_http_write_receipts_and_api_key_authority",
            "MCP proposal-only resource scope": "test_mcp_propose_document_update_is_proposal_only_and_resource_scoped",
            "proposal leaves document unchanged": 'assert unchanged.json()["version"] == 1',
            "strict create tool schema": 'tool_by_name["create_document"]["inputSchema"]',
            "strict proposal tool schema": 'tool_by_name["propose_document_update"]["inputSchema"]',
        },
    }
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
    return errors


def _agent_authority_idempotency_surface_errors(root: Path) -> list[str]:
    """Keep owner credential lifecycle and proposal receipts fail closed."""
    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    persistent_authority = _function_source(
        main_source,
        "require_non_impersonated_clerk_human",
        main_path,
        errors,
    )
    _require_source_markers(
        persistent_authority,
        f"{main_path}#require_non_impersonated_clerk_human",
        {
            "persistent authority Clerk method gate": 'principal.method != "clerk_jwt"',
            "persistent authority impersonation denial": "principal.is_impersonated",
        },
        errors,
    )
    visible_ascii_header = {
        "Idempotency-Key name": '"name": "Idempotency-Key"',
        "Idempotency-Key header location": '"in": "header"',
        "required Idempotency-Key header": '"required": True',
        "visible-ASCII Idempotency-Key bound": '"minLength": 1',
    }
    for route, label, pattern in (
        (
            "POST /v1/proposals/{proposal_id}/{action}",
            "proposal decision",
            "_IDEMPOTENCY_KEY_PATTERN",
        ),
        ("POST /v1/api-keys", "API-key create", "_IDEMPOTENCY_KEY_RE.pattern"),
        (
            "DELETE /v1/api-keys/{key_id}",
            "API-key revoke",
            "_IDEMPOTENCY_KEY_RE.pattern",
        ),
    ):
        decorator = _route_decorator(route, main_source)
        if decorator is None:
            _error(
                errors,
                f"repository.agent_authority.{label}",
                f"cannot locate implemented route {route!r}",
            )
            continue
        _require_source_markers(
            decorator,
            f"{main_path}#{label}-route",
            {
                **visible_ascii_header,
                "maximum Idempotency-Key bound": '"maxLength": 128',
                "visible-ASCII Idempotency-Key pattern": pattern,
            },
            errors,
        )
    decision = _function_source(main_source, "decide_proposal", main_path, errors)
    decision_markers = {
        "proposal decision Clerk authority": 'if principal.method != "clerk_jwt":',
        "proposal decision required key": "idempotency_key(request, required=True)",
        "proposal decision operation binding": 'operation = f"POST:/v1/proposals/{proposal_id}/{action}"',
        "proposal decision owner lock": ".with_for_update()",
        "provisional proposal receipt": 'resource_type="proposal_decision"',
        "empty provisional receipt body": 'response_body=""',
        "empty provisional receipt headers": 'response_headers="{}"',
        "proposal acceptance actor": 'actor_method="proposal_accept"',
        "document-bound decision receipt": "idempotency_record=decision_receipt",
        "accepted receipt response headers": '"X-Connectmd-Search": "queued"',
        "accepted receipt finalization": "provisional_record=decision_receipt",
    }
    _require_source_markers(
        decision, f"{main_path}#decide_proposal", decision_markers, errors
    )
    replay_positions = [
        decision.find("replay = await idempotency_replay"),
        decision.find(".with_for_update()"),
        decision.find(
            "replay = await idempotency_replay", decision.find(".with_for_update()")
        ),
    ]
    if (
        any(position < 0 for position in replay_positions)
        or replay_positions[0] >= replay_positions[1]
        or replay_positions[1] >= replay_positions[2]
    ):
        _error(
            errors,
            f"repository.agent_authority.{main_path}",
            "proposal decision must replay before lookup and again after its owner lock",
        )
    documents_path = "apps/api/app/services/documents.py"
    documents = _read_anchor_source(root, documents_path, errors)
    _require_source_markers(
        documents,
        documents_path,
        {
            "proposal receipt prefix validation": "def validate_proposal_decision_receipt_prefix(",
            "proposal receipt UUID binding": "proposal_uuid = UUID(parts[0])",
            "proposal receipt document binding": "document_uuid = UUID(document_id)",
            "proposal receipt digest validation": "_SHA256_HEX_RE.fullmatch(digest)",
            "proposal receipt digest binding": 'return f"{prefix}:{document_id}:{version}:{digest}"',
            "proposal write receipt validation": "validate_proposal_decision_receipt_prefix(",
            "proposal write receipt finalization": "bind_proposal_decision_receipt(",
        },
        errors,
    )
    replay = _function_source(main_source, "idempotency_replay", main_path, errors)
    _require_source_markers(
        main_source,
        main_path,
        {
            "membership generation helper": "def _organization_membership_generation_digest(",
            "membership generation role binding": '"role": membership.role,',
            "membership generation inviter binding": '"invited_by_owner_id": membership.invited_by_owner_id,',
        },
        errors,
    )
    _require_source_markers(
        replay,
        f"{main_path}#idempotency_replay",
        {
            "proposal accept receipt shape": "len(parts) != 5",
            "proposal accept digest shape": "re.fullmatch(_SHA256_HEX_PATTERN, parts[4])",
            "proposal accept canonical version": "str(decision_version) != parts[3]",
            "proposal accept actual-version digest comparison": "compare_digest(version_row.sha256, parts[4])",
            "proposal accept expected headers": '"X-Connectmd-Search": "queued"',
            "proposal accept header corruption gate": "stored_headers != expected_headers",
            "API-key replay dispatch": "return await replay_api_key_receipt(",
        },
        errors,
    )
    api_key_replay_path = "apps/api/app/services/api_key_replay.py"
    api_key_replay = _function_source(
        _read_anchor_source(root, api_key_replay_path, errors),
        "replay_api_key_receipt",
        api_key_replay_path,
        errors,
    )
    _require_source_markers(
        api_key_replay,
        f"{api_key_replay_path}#replay_api_key_receipt",
        {
            "API-key creation receipt operation": 'operation == "POST:/v1/api-keys"',
            "API-key metadata-only recovery": "recovery_response_factory(",
            "API-key creation missing-row gate": "if api_key is None:",
            "API-key revocation receipt operation": 'operation.startswith("DELETE:/v1/api-keys/")',
            "API-key revocation owner-bound receipt": "record.resource_id != recorded_key_id",
            "API-key revocation missing-row gate": "if api_key is None or not api_key.revoked:",
            "API-key revocation replay status": "status_code=204",
        },
        errors,
    )
    for sensitive in ("raw_key", "secret_hash", "api_key_pepper", "pepper"):
        if sensitive in api_key_replay:
            _error(
                errors,
                f"repository.agent_authority.{api_key_replay_path}#replay_api_key_receipt",
                f"API-key replay must not expose credential {sensitive!r}",
            )
    create = _function_source(main_source, "create_api_key", main_path, errors)
    _require_source_markers(
        create,
        f"{main_path}#create_api_key",
        {
            "API-key create non-impersonated Clerk dependency": "require_non_impersonated_clerk_human(",
            "API-key normalized scope binding": "normalized_scopes = sorted({str(scope) for scope in body.scopes})",
            "API-key create idempotency operation": 'operation = "POST:/v1/api-keys"',
            "API-key create replay": "replay = await idempotency_replay",
            "API-key atomic create": "normalized_scopes, commit=False",
            "API-key created event": 'event_type="api_key.created"',
            "API-key scope-only event": 'payload=json.dumps({"scopes": normalized_scopes}, sort_keys=True)',
            "API-key empty idempotency receipt": 'body=""',
            "API-key empty receipt headers": "headers={}",
            "API-key one-time secret response": "key=raw_key",
        },
        errors,
    )
    receipt_start = create.find("await store_idempotency(")
    receipt_end = create.find("return ApiKeyCreatedResponse(", receipt_start)
    create_receipt = create[receipt_start:receipt_end] if receipt_start >= 0 else ""
    for sensitive in ("raw_key", "secret_hash", "api_key_pepper", "pepper"):
        if sensitive in create_receipt:
            _error(
                errors,
                f"repository.agent_authority.{main_path}#create_api_key",
                f"API-key receipt/event must not retain credential {sensitive!r}",
            )
    revoke = _function_source(main_source, "revoke_api_key", main_path, errors)
    _require_source_markers(
        revoke,
        f"{main_path}#revoke_api_key",
        {
            "API-key revoke non-impersonated Clerk dependency": "require_non_impersonated_clerk_human(",
            "API-key revoke idempotency operation": 'operation = f"DELETE:/v1/api-keys/{key_id}"',
            "API-key revoke replay": "replay = await idempotency_replay",
            "API-key owner lock": "ApiKey.owner_id == principal.subject",
            "API-key revoke transition guard": "if not record.revoked:",
            "API-key revoked event": 'event_type="api_key.revoked"',
            "API-key secret-free revoke event": 'payload="{}"',
            "API-key empty revoke receipt": 'body=""',
            "API-key revoke receipt resource": 'resource_type="api_key"',
            "API-key revoke empty response": "return Response(status_code=204)",
        },
        errors,
    )
    api_test_path = "apps/api/tests/test_api_key_atomicity.py"
    api_tests = _read_anchor_source(root, api_test_path, errors)
    _require_source_markers(
        api_tests,
        api_test_path,
        {
            "metadata-only recovery test": "test_create_replay_is_metadata_only_and_missing_row_fails_closed",
            "API-key OpenAPI authority test": "test_openapi_declares_typed_api_key_recovery_and_idempotency_contract",
            "API-key revocation replay test": "test_revoke_replays_before_lookup_and_only_emits_transition_event",
            "API-key create concurrency test": "test_concurrent_same_key_create_has_one_credential_and_one_event",
            "API-key revoke concurrency test": "test_concurrent_same_key_revoke_has_one_transition_and_receipt",
            "one-time secret omission assertion": 'assert "key" not in recovered',
            "missing create-row receipt assertion": "assert missing.status_code == 503",
            "exact empty 204 assertion": 'assert replay.content == b""',
        },
        errors,
    )
    impersonation_test_path = "apps/api/tests/test_impersonation_authority.py"
    impersonation_tests = _read_anchor_source(root, impersonation_test_path, errors)
    _require_source_markers(
        impersonation_tests,
        impersonation_test_path,
        {
            "pre-handler impersonation denial test": "test_impersonated_clerk_is_denied_before_lookup_or_persistence",
            "credential retention denial test": "test_non_impersonated_clerk_can_issue_and_use_credentials_while_impersonation_cannot_retain_them",
            "zero API-key persistence assertion": "assert (await session.scalars(select(ApiKey))).all() == []",
            "zero Agent Grant persistence assertion": "assert (await session.scalars(select(AgentGrant))).all() == []",
            "zero mandate persistence assertion": "assert (await session.scalars(select(AgentMandate))).all() == []",
            "impersonated revocation preservation": "assert mandate_grant_row is not None and mandate_grant_row.revoked is False",
        },
        errors,
    )
    protocol_test_path = "apps/api/tests/test_protocol_core.py"
    protocol_tests = _read_anchor_source(root, protocol_test_path, errors)
    _require_source_markers(
        protocol_tests,
        protocol_test_path,
        {
            "proposal decision OpenAPI header test": "test_proposal_rejection_is_idempotent_and_atomic",
            "digest-bound proposal receipt test": 'tampered_parts[3] = "1"',
            "malformed proposal digest test": "not-a-digest",
            "proposal decision concurrency receipt test": "test_proposal_decision_conflict_replays_committed_receipt_without_duplicates",
        },
        errors,
    )
    mcp = _function_source(main_source, "mcp_tools", main_path, errors)
    for forbidden in ('"name": "create_api_key"', '"name": "revoke_api_key"'):
        if forbidden in mcp:
            _error(
                errors,
                f"repository.agent_authority.{main_path}#mcp_tools",
                f"MCP API-key management tool is forbidden: {forbidden}",
            )
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    for forbidden in (
        'if action == "create_api_key":',
        'if action == "revoke_api_key":',
    ):
        if forbidden in a2a:
            _error(
                errors,
                f"repository.agent_authority.{main_path}#a2a_send_message",
                f"A2A API-key management action is forbidden: {forbidden}",
            )
    return errors


def _impersonation_read_only_surface_errors(root: Path) -> list[str]:
    """Bind Clerk impersonation to the read-only HTTP authentication boundary."""
    errors: list[str] = []
    auth_path = "apps/api/app/auth.py"
    auth_source = _read_anchor_source(root, auth_path, errors)
    optional = _function_source(auth_source, "optional_principal", auth_path, errors)
    _require_source_markers(
        auth_source,
        auth_path,
        {
            "stable impersonation read-only code": 'IMPERSONATION_READ_ONLY_CODE = "impersonation_read_only"',
        },
        errors,
    )
    _require_source_markers(
        optional,
        f"{auth_path}#optional_principal",
        {
            "Clerk verification": "clerk_principal = await request.app.state.clerk.verify(credential)",
            "impersonation mutation guard": "if clerk_principal.is_impersonated and _is_mutation(request):",
            "stable generic forbidden detail": "detail=IMPERSONATION_READ_ONLY_CODE,",
            "mutation classifier": "_is_mutation(request)",
            "account access gate": "await assert_account_access(",
        },
        errors,
    )
    verify_position = optional.find(
        "clerk_principal = await request.app.state.clerk.verify(credential)"
    )
    guard_position = optional.find(
        "if clerk_principal.is_impersonated and _is_mutation(request):"
    )
    account_access_position = optional.find(
        "await assert_account_access(", guard_position
    )
    if (
        verify_position < 0
        or guard_position < 0
        or account_access_position < 0
        or verify_position >= guard_position
        or guard_position >= account_access_position
    ):
        _error(
            errors,
            f"repository.auth_boundary.{auth_path}#optional_principal",
            "Clerk impersonation mutation denial must follow verification and precede account access",
        )
    if (
        auth_source.count(
            "if clerk_principal.is_impersonated and _is_mutation(request):"
        )
        != 1
    ):
        _error(
            errors,
            f"repository.auth_boundary.{auth_path}#optional_principal",
            "must define exactly one Clerk impersonation mutation denial",
        )
    test_path = "apps/api/tests/test_impersonation_authority.py"
    test_source = _read_anchor_source(root, test_path, errors)
    _require_source_markers(
        test_source,
        test_path,
        {
            "real-auth integration test": "test_real_clerk_impersonation_is_read_only_at_http_boundary",
            "cleared fixture overrides": "app.dependency_overrides.clear()",
            "monkeypatched Clerk verification": 'monkeypatch.setattr(app.state.clerk, "verify", verify_clerk)',
            "impersonated GET remains allowed": 'read_response = await client.get("/v1/me", headers=impersonated_headers)',
            "representative POST mutation denial": '("post", "/v1/profiles", b"{"',
            "representative PUT mutation denial": '("put", "/v1/profiles/missing-profile", b"{"',
            "representative DELETE mutation denial": '("delete", "/v1/api-keys/missing-key", None, {})',
            "private organization mutation denial": '"/v1/organizations",',
            "Agent Identity mutation denial": '"/v1/agent-identities",',
            "structured search POST denial": '"/v1/search/query",',
            "MCP POST denial": '"/mcp",',
            "A2A POST denial": '"/a2a/message:send",',
            "zero Organization persistence": "assert (await session.scalars(select(Organization))).all() == []",
            "zero AgentIdentity persistence": "assert (await session.scalars(select(AgentIdentity))).all() == []",
            "zero Document persistence": "assert (await session.scalars(select(Document))).all() == []",
            "zero Idempotency persistence": "assert (await session.scalars(select(IdempotencyRecord))).all() == []",
            "zero ChangeEvent persistence": "assert (await session.scalars(select(ChangeEvent))).all() == []",
            "ordinary Clerk success": "assert ordinary_create.status_code == 201",
        },
        errors,
    )
    return errors


def _agent_grant_creation_durability_errors(root: Path) -> list[str]:
    """Bind ordinary Agent Grant creation to its secret-safe replay contract."""
    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    persistent_authority = _function_source(
        main_source,
        "require_non_impersonated_clerk_human",
        main_path,
        errors,
    )
    _require_source_markers(
        persistent_authority,
        f"{main_path}#require_non_impersonated_clerk_human",
        {
            "persistent authority Clerk method gate": 'principal.method != "clerk_jwt"',
            "persistent authority impersonation denial": "principal.is_impersonated",
        },
        errors,
    )
    route = _route_decorator("POST /v1/agent-grants", main_source)
    if route is None:
        _error(
            errors,
            "repository.agent_grant_creation.route",
            "cannot locate implemented route 'POST /v1/agent-grants'",
        )
    else:
        _require_source_markers(
            route,
            f"{main_path}#agent-grant-create-route",
            {
                "human-only OpenAPI extension": '"x-connectmd-human-only": True',
                "Agent Grant created-or-recovery response union": "response_model=AgentGrantCreatedResponse | AgentGrantRecoveryResponse",
                "Agent Grant creation status": "status_code=201",
                "Idempotency-Key name": '"name": "Idempotency-Key"',
                "Idempotency-Key header location": '"in": "header"',
                "required Idempotency-Key header": '"required": True',
                "minimum Idempotency-Key bound": '"minLength": 1',
                "maximum Idempotency-Key bound": '"maxLength": 128',
                "visible-ASCII Idempotency-Key pattern": "_IDEMPOTENCY_KEY_PATTERN",
            },
            errors,
        )

    def require_order(
        source: str, function_name: str, anchors: list[tuple[str, str]]
    ) -> None:
        position = -1
        for label, marker in anchors:
            position = source.find(marker, position + 1)
            if position < 0:
                _error(
                    errors,
                    f"repository.agent_grant_creation.{main_path}#{function_name}",
                    f"is missing {label} anchor {marker!r}",
                )
                return

    create = _function_source(main_source, "create_agent_grant", main_path, errors)
    _require_source_markers(
        create,
        f"{main_path}#create_agent_grant",
        {
            "non-impersonated Clerk grant dependency": "require_non_impersonated_clerk_human(",
            "required Agent Grant key": "key = idempotency_key(request, required=True)",
            "Agent Grant operation binding": 'operation = "POST:/v1/agent-grants"',
            "normalized grant name": "normalized_name = body.name.strip()",
            "normalized grant scopes": "normalized_scopes = sorted({str(scope) for scope in body.scopes})",
            "normalized expiry intent": '"kind": "absolute"',
            "canonical grant intent fingerprint": '"endpoint": operation,',
            "canonical resource intent": '"resource": resource_intent,',
            "pre-resource owner/key receipt probe": "IdempotencyRecord.owner_id == principal.subject",
            "same-key intent collision": "existing.operation != operation or existing.request_hash != fingerprint",
            "pre-resource recovery context": '"resource_type": body.resource.type,',
            "pre-resource recovery": "return await agent_grant_recovery_replay(session, principal, existing, grant_context)",
            "document current-owner lock": "Document.id == body.resource.id, Document.owner_id == principal.subject",
            "organization current-authority lock": "select(Organization).where(Organization.id == body.resource.id).with_for_update()",
            "organization current-authority check": "role = await organization_role(session, organization, principal)",
            "ordinary grant excludes mandate": "mandate_id=None,",
            "post-lock Agent Grant replay": "replay = await idempotency_replay(",
            "atomic Agent Grant create": "commit=False,",
            "one-time secret response": "result = grant_response(row, key=raw_key)",
            "safe recovery projection": "safe_recovery = grant_recovery_response(row)",
            "recovery digest resource": "receipt_resource_id = _agent_grant_recovery_resource_id(",
            "empty Agent Grant receipt": 'status_code=201,\n                body="",\n                headers={},',
            "Agent Grant recovery receipt type": 'resource_type="agent_grant_recovery"',
        },
        errors,
    )
    require_order(
        create,
        "create_agent_grant",
        [
            ("Clerk authority", "require_non_impersonated_clerk_human("),
            ("idempotency key", "key = idempotency_key(request, required=True)"),
            ("canonical intent fingerprint", "fingerprint = _request_fingerprint("),
            ("pre-resource receipt probe", "existing = await session.scalar("),
            ("pre-resource recovery", "return await agent_grant_recovery_replay("),
            ("current resource authority", 'if body.resource.type == "document":'),
            ("ordinary no-mandate definition", "mandate_id=None,"),
            ("post-lock replay", "replay = await idempotency_replay("),
            ("atomic grant create", "commit=False,"),
            (
                "safe recovery projection",
                "safe_recovery = grant_recovery_response(row)",
            ),
            ("empty recovery receipt", "await store_idempotency("),
        ],
    )

    recovery = _function_source(
        main_source, "agent_grant_recovery_replay", main_path, errors
    )
    _require_source_markers(
        recovery,
        f"{main_path}#agent_grant_recovery_replay",
        {
            "receipt type guard": 'record.resource_type != "agent_grant_recovery"',
            "receipt status guard": "record.response_status != 201",
            "empty receipt body guard": 'record.response_body != ""',
            "empty receipt header guard": 'record.response_headers != "{}"',
            "digest receipt parser": "_agent_grant_recovery_resource_parts(record.resource_id)",
            "current document replay authority": "Document.id == resource_id, Document.owner_id == principal.subject",
            "current organization replay authority": "await organization_role(session, organization, principal) is None",
            "owner-bound grant replay lock": "AgentGrant.owner_id == principal.subject",
            "ordinary grant mandate absence": "or row.mandate_id is not None",
            "revoked grant replay gate": "or row.revoked",
            "expired grant replay gate": "or retention_expired(row.expires_at)",
            "normalized stored scope gate": "raw_scopes != sorted(set(raw_scopes))",
            "strict grant definition gate": "agent_grant_definition_is_valid(",
            "safe replay projection": "AgentGrantRecoveryResponse(",
            "recovery-required replay": "recovery_required=True",
            "exact recovery digest": "_agent_grant_recovery_digest(",
            "recovery digest comparison": 'compare_digest(parts["digest"], expected_digest)',
            "replayed status": "status_code=201",
            "replayed header": 'headers={"Idempotency-Replayed": "true"}',
            "fail-closed unavailable error": "status_code=503",
        },
        errors,
    )
    for sensitive in ("raw_key", "secret_hash", "api_key_pepper", "pepper"):
        if sensitive in recovery:
            _error(
                errors,
                f"repository.agent_grant_creation.{main_path}#agent_grant_recovery_replay",
                f"recovery replay must not expose credential {sensitive!r}",
            )

    replay = _function_source(main_source, "idempotency_replay", main_path, errors)
    _require_source_markers(
        replay,
        f"{main_path}#idempotency_replay",
        {
            "Agent Grant operation dispatch": 'if operation == "POST:/v1/agent-grants":',
            "Agent Grant receipt type dispatch guard": 'record.resource_type != "agent_grant_recovery"',
            "Agent Grant replay context guard": "agent_grant_context is None",
            "Agent Grant replay helper": "return await agent_grant_recovery_replay(",
        },
        errors,
    )

    auth_path = "apps/api/app/auth.py"
    auth_source = _read_anchor_source(root, auth_path, errors)
    manager_start = auth_source.find("class AgentGrantManager:")
    manager_end = auth_source.find("\n\nasync def optional_principal", manager_start)
    manager = (
        ""
        if manager_start < 0 or manager_end < 0
        else auth_source[manager_start:manager_end]
    )
    _require_source_markers(
        manager,
        f"{auth_path}#AgentGrantManager",
        {
            "Agent Grant manager": "class AgentGrantManager:",
            "Agent Grant manager create": "async def create(",
            "generated cng secret": 'raw_key = "cng_" + secrets.token_urlsafe(32)',
            "grant row staged": "session.add(record)",
            "grant row flush": "await session.flush()",
            "safe Agent Grant event": 'event_type="agent_grant.created"',
            "event resource class": 'resource_type="agent_grant"',
            "event payload": "payload=json.dumps(",
            "optional manager commit": "if commit:",
        },
        errors,
    )
    event_start = manager.find("session.add(\n            ChangeEvent(")
    event_end = manager.find("if commit:", event_start)
    event = (
        manager[event_start:event_end] if event_start >= 0 and event_end >= 0 else ""
    )
    for sensitive in (
        "raw_key",
        "secret_hash",
        "_peppered",
        "api_key_pepper",
        "pepper",
    ):
        if sensitive in event:
            _error(
                errors,
                f"repository.agent_grant_creation.{auth_path}#AgentGrantManager.event",
                f"Agent Grant event must not retain credential {sensitive!r}",
            )

    receipt_start = create.find("safe_recovery = grant_recovery_response(row)")
    receipt_end = create.find("return result", receipt_start)
    receipt = create[receipt_start:receipt_end] if receipt_start >= 0 else ""
    for sensitive in ("raw_key", "secret_hash", "api_key_pepper", "pepper"):
        if sensitive in receipt:
            _error(
                errors,
                f"repository.agent_grant_creation.{main_path}#create_agent_grant-receipt",
                f"Agent Grant receipt must not retain credential {sensitive!r}",
            )

    test_path = "apps/api/tests/test_agent_grant_atomicity.py"
    tests = _read_anchor_source(root, test_path, errors)
    _require_source_markers(
        tests,
        test_path,
        {
            "OpenAPI key and response union coverage": "test_agent_grant_requires_key_and_recovery_is_secret_safe",
            "one-time secret assertion": 'assert raw_key.startswith("cng_")',
            "safe recovery assertion": 'assert "key" not in safe',
            "safe receipt/event assertions": "assert len(grants) == len(events) == 1",
            "receipt body privacy assertion": 'assert receipt.response_body == ""',
            "event secret exclusion assertion": 'assert "secret_hash" not in event_payload',
            "normalized intent collision coverage": "test_agent_grant_fingerprint_normalizes_manager_name_and_rejects_changes",
            "cross-operation collision assertion": "assert cross_operation.status_code == 409",
            "absolute expiry normalization coverage": "test_agent_grant_absolute_expiry_replay_uses_utc_intent",
            "current authority and expiry replay coverage": "test_agent_grant_replay_rechecks_resource_authority_and_expiry",
            "expired replay failure": "assert expired.status_code == 503",
            "manual mandate tamper coverage": "test_agent_grant_receipt_and_manual_mandate_tamper_fail_closed",
            "corruption matrix": "test_agent_grant_corruption_never_replays_secret",
            "same-key SQLite atomicity": "test_agent_grant_same_key_sqlite_gather_keeps_one_safe_receipt",
            "one grant/event assertion": "assert len((await session.scalars(select(AgentGrant))).all()) == 1",
            "SQLite limitation": "does not prove PostgreSQL scheduling",
            "MCP and A2A issuance exclusion": "test_agent_grant_issuance_is_not_an_mcp_or_a2a_action",
        },
        errors,
    )
    corruption_test = _function_source(
        tests,
        "test_agent_grant_corruption_never_replays_secret",
        test_path,
        errors,
    )
    _require_source_markers(
        corruption_test,
        f"{test_path}#test_agent_grant_corruption_never_replays_secret",
        {
            "corruption 503 assertion": "assert replay.status_code == 503",
            "raw secret omission assertion": "assert raw_key not in replay.text",
            "secret verifier omission assertion": 'assert "secret_hash" not in replay.text',
        },
        errors,
    )

    protocol_tests = _read_anchor_source(
        root, "apps/api/tests/test_protocol_core.py", errors
    )
    _require_source_markers(
        protocol_tests,
        "apps/api/tests/test_protocol_core.py",
        {
            "resource-scope compatibility caller evidence": "test_agent_grant_resource_scope_matrix_fails_closed_at_every_boundary",
        },
        errors,
    )

    mcp = _function_source(main_source, "mcp_tools", main_path, errors)
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    for transport, source in (("mcp_tools", mcp), ("a2a_send_message", a2a)):
        if any(
            marker in source
            for marker in (
                '"name": "create_agent_grant"',
                '"name": "issue_agent_grant"',
                'if action == "create_agent_grant":',
                'if action == "issue_agent_grant":',
            )
        ):
            _error(
                errors,
                f"repository.agent_grant_creation.{main_path}#{transport}",
                f"{transport} must not expose Agent Grant issuance",
            )
    return errors


def _agent_web_helper_extraction_errors(root: Path) -> list[str]:
    errors: list[str] = []
    paths = {
        "agent": "apps/web/lib/agent-api.ts",
        "manager": "apps/web/components/agent-delegation-manager.tsx",
        "panel": "apps/web/components/agent-delegation-panels.tsx",
    }
    sources = {
        key: _read_anchor_source(root, path, errors) for key, path in paths.items()
    }
    helper_names = (  # noqa: SIM905
        "listDelegations listOwnedDocumentOptions listOwnedDocumentPageForSubject "
        "createDelegation setDelegationPaused revokeDelegation emergencyStopDelegations "
        "listDelegationAudit listAgentProposals listAgentProposalsForSubject "
        "decideAgentProposal loadProposalBaseMarkdown"
    ).split()
    panel_markers = (  # noqa: SIM905
        "function CopyGrantHandoff(|export function AgentGrantInventoryPanel(|"
        "export function AgentProposalReviewPanel(|Loading grants|"
        "Agent grants could not be loaded|No agent grants have been created.|"
        "Agent grants could not be refreshed|Loading proposals|"
        "Agent proposals could not be loaded|No agent proposals are awaiting review.|"
        "Agent proposals could not be refreshed"
    ).split("|")
    marker_groups = {
        "agent": tuple(f"export async function {name}(" for name in helper_names),
        "panel": panel_markers,
        "manager": (  # noqa: SIM905
            'from "@/lib/agent-api"|from "@/components/agent-delegation-panels"|'
            "<AgentGrantInventoryPanel|<AgentProposalReviewPanel"
        ).split("|"),
    }
    for key, markers in marker_groups.items():
        _require_source_markers(
            sources[key],
            f"{paths[key]}#agent-authority-extraction",
            {marker: marker for marker in markers},
            errors,
        )
    for marker in panel_markers:
        if marker in sources["manager"]:
            errors.append(
                f"repository.agent_authority.{paths['manager']}#extracted-delegation-panels: "
                f"must not retain extracted presentation marker {marker!r}"
            )
    return errors


def _function_source(
    source: str, function_name: str, relative_path: str, errors: list[str]
) -> str:
    """Return one named function body, failing closed on missing or ambiguous source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _error(
            errors,
            f"repository.anchors.{relative_path}",
            f"cannot parse source: {exc}",
        )
        return ""
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(functions) != 1:
        _error(
            errors,
            f"repository.anchors.{relative_path}",
            f"must define exactly one {function_name!r} function",
        )
        return ""
    return ast.get_source_segment(source, functions[0]) or ""


def _typescript_function_source(
    source: str, function_name: str, relative_path: str, errors: list[str]
) -> str:
    """Return one exported TypeScript function body without parsing TS as Python."""
    marker = f"export function {function_name}"
    starts = [match.start() for match in re.finditer(re.escape(marker), source)]
    if len(starts) != 1:
        _error(
            errors,
            f"repository.anchors.{relative_path}",
            f"must define exactly one {function_name!r} function",
        )
        return ""
    start = starts[0]
    next_start = source.find("\nexport function ", start + len(marker))
    return source[start : next_start if next_start >= 0 else len(source)]


def _document_visibility_guard_line(
    source: str, relative_path: str, errors: list[str]
) -> int | None:
    """Return a rejecting guard that excludes non-public documents."""

    def is_document_visibility(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "visibility"
            and isinstance(node.value, ast.Name)
            and node.value.id == "document"
        )

    def is_non_public_comparison(node: ast.AST) -> bool:
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            return False
        left_is_visibility = is_document_visibility(node.left)
        comparator = node.comparators[0]
        right_is_visibility = is_document_visibility(comparator)
        left_is_public = (
            isinstance(node.left, ast.Constant) and node.left.value == "public"
        )
        right_is_public = (
            isinstance(comparator, ast.Constant) and comparator.value == "public"
        )
        return isinstance(node.ops[0], ast.NotEq) and (
            (left_is_visibility and right_is_public)
            or (left_is_public and right_is_visibility)
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _error(
            errors,
            f"repository.anchors.{relative_path}",
            f"cannot parse source: {exc}",
        )
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if not any(is_non_public_comparison(value) for value in ast.walk(node.test)):
            continue
        if any(isinstance(statement, ast.Continue) for statement in node.body):
            return node.lineno
    _error(
        errors,
        f"repository.operations.{relative_path}",
        "is missing a rejecting public visibility authorization guard",
    )
    return None


def _public_html_mirror_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "apps/web/lib/public-document.ts": {
            "bounded metadata title": "boundedMetadataText(`${name} — ${role}`, 160)",
            "bounded metadata description": 'boundedMetadataText(view.fields.headline || `${role} in ${view.locationLabel || "connect.md"}`, 280)',
            "allowlisted Markdown alternate": "publicApiMarkdownUrl(markdownUrl)",
            "profile versus resume structured-data branch": 'return document.kind === "profile" ? profilePageJsonLd(document, canonicalUrl) : resumeJsonLd(document, canonicalUrl);',
            "resume DigitalDocument projection": "function resumeJsonLd(document: DocumentResponse, canonicalUrl: string)",
            "resume explicit bounded name": "const name = boundedMetadataText(stringValue(attributes.name), 160);",
            "resume omission without explicit name": "const name = boundedMetadataText(stringValue(attributes.name), 160);\n  if (!name) return null;\n  const description = boundedMetadataText(view.fields.headline, 280);",
            "resume optional bounded headline": "const description = boundedMetadataText(view.fields.headline, 280);",
            "resume canonical URL": "url: canonicalUrl",
            "resume modification/version evidence": "dateModified: document.updated_at",
            "resume Markdown format": 'encodingFormat: "text/markdown"',
            "script-safe JSON-LD": ".replace(/[<\\u2028\\u2029]/gu",
        },
        "apps/web/components/public-document-page.tsx": {
            "canonical Markdown source": "publicApiMarkdownUrl(document.markdown_url)",
            "canonical Markdown body": "MarkdownPreview markdown={document.markdown}",
            "canonical version": "Version {document.version}",
        },
        "apps/web/app/p/[handle]/page.tsx": {
            "canonical profile identifier": "`/p/${encodeURIComponent(document.identifier)}`",
        },
        "apps/web/app/r/[slug]/page.tsx": {
            "canonical resume identifier": "`/r/${encodeURIComponent(document.identifier)}`",
        },
        "apps/web/app/sitemap.ts": {
            "Next sitemap partition function": "export function generateSitemaps()",
            "stable sitemap category IDs": "const sitemapCategoryIds = [0, 1, 2, 3] as const;",
            "50,000 URL ceiling": "const maxSitemapEntries = 50_000;",
            "bounded cursor pages": "const maxCursorPages = 250;",
            "Next runtime category normalization": 'const categoryId = typeof id === "string" && /^[0-3]$/.test(id) ? Number(id) : id;',
            "document category": "if (categoryId === 0) return collectDocumentSitemap();",
            "recruitment category": "if (categoryId === 1) return collectRecruitmentSitemap();",
            "agent category": "if (categoryId === 2) return collectAgentSitemap();",
            "public post category": "if (categoryId === 3) return collectPostSitemap();",
            "public post inventory reader": "listPublicPostsOnServer(200, cursor)",
            "public post duplicate guard": "The public post inventory repeated a post.",
            "public post cursor guard": "The public post inventory cursor did not progress.",
            "public post sitemap ceiling": "The public post inventory exceeded its 50,000-post sitemap window.",
            "cap before append": "if (entries.length >= maxSitemapEntries) return false;",
            "document fallback": "return fallbackEntries;",
        },
        "apps/web/app/robots.ts": {
            "generated sitemap IDs": "[0, 1, 2, 3].map((id) => absoluteSiteUrl(`/sitemap/${id}.xml`))",
        },
        "apps/web/tests/public-projections.test.ts": {
            "minimal named resume DigitalDocument": "projects a named resume as minimal DigitalDocument structured data",
            "resume omission without explicit name": "requires an explicit resume name and omits absent descriptions",
            "resume private inference rejection": 'owner_id: "private-owner"',
            "resume bounds": "bounds and normalizes resume structured-data fields",
            "metadata bounds": "bounds and whitespace-normalizes title and description metadata",
            "canonical source and version": "binds the HTML body and source facts to the canonical response fields",
        },
        "apps/web/tests/sitemap.test.ts": {
            "stable sitemap IDs and robots parity": "generates stable category IDs and advertises their production URLs",
            "50,000 counterexample": "caps category 0 at 50,000 URLs including base entries",
            "document fail-closed fallback": "fails closed to category 0 base entries for malformed inventory data",
            "recruitment fail-closed fallback": "returns an empty category 1 sitemap when a later recruitment read fails",
            "agent fail-closed fallback": "returns an empty category 2 sitemap when a directory continuation is unavailable",
        },
    }
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
    return errors


def _logical_mutation_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "apps/web/lib/logical-mutation.ts": {
            "mandatory subject input": "subject: string,",
            "subject-scoped intent fingerprint": "fingerprintMutationIntent({ subject, intent })",
            "definitive 4xx clearance": "error.status >= 400 && error.status < 500",
            "ambiguous request/server retention": 'error.code === "request" || error.code === "server"',
        },
        "apps/web/tests/logical-mutation.test.ts": {
            "same-subject canonical retry": "reuses the key only for the same canonical intent",
            "subject rotation counterexample": "rotates the key when the captured subject changes even if intent is unchanged",
            "ambiguous acknowledgement settlement": "retains only ambiguous network/server acknowledgement loss",
            "offline non-retention": 'new ApiRequestError("offline", undefined, "offline")',
            "4xx non-retention": 'new ApiRequestError("bad request", 422, "request")',
        },
    }
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
    return errors


def _private_conversation_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    component_path = "apps/web/components/conversation-thread.tsx"
    component = _read_anchor_source(root, component_path, errors)
    _require_source_markers(
        component,
        component_path,
        {
            "subject and conversation remount boundary": "key={`${subject}:${conversationId}`}",
            "explicit initial load states": 'useState<"loading" | "loaded" | "error">("loading")',
            "truthful initial failure branch": 'loadState === "error" && messages.length === 0',
            "successful empty branch": 'loadState === "loaded" && messages.length === 0',
            "refresh failure with retained messages": 'loadState === "error" && messages.length > 0',
            "subject/conversation coordinator": "createConversationReadCoordinator(subject, conversationId)",
            "scope-reset coordinator call": "resetConversationReadCoordinator(",
            "cursor gate after failed primary read": 'if (cursor && loadStateRef.current !== "loaded") return;',
            "current-primary success interaction release": 'setLoadState("loaded");',
            "current-primary failure interaction gate": 'setLoadState("error");',
            "shared cursor/send interaction claim": "claimConversationSend(coordinator, subject, conversationId)",
            "current-read completion guard": "isCurrentConversationRead(coordinator, claim)",
            "composer disabled before successful load": 'disabled={busy || loadState !== "loaded"}',
            "pagination disabled before successful load": 'disabled={busy || loadState !== "loaded"}',
            "accessible retryable failure": '<div role="alert"',
        },
        errors,
    )

    def component_section(start_marker: str, end_marker: str, label: str) -> str:
        start = component.find(start_marker)
        end = component.find(end_marker, start + len(start_marker))
        if start < 0 or end < 0 or end <= start:
            _error(
                errors,
                f"repository.anchors.{component_path}",
                f"cannot isolate {label} coordinator section",
            )
            return ""
        return component[start:end]

    scope_reset = component_section(
        "export function resetConversationReadCoordinator(",
        "function claimConversationOperation(",
        "scope reset",
    )
    _require_source_markers(
        scope_reset,
        f"{component_path}#scope-reset",
        {
            "scope reset generation invalidation": "coordinator.generation += 1;",
            "scope reset primary claim release": "coordinator.primaryClaimId = null;",
            "scope reset interaction claim release": "coordinator.interactionClaimId = null;",
        },
        errors,
    )
    operation_claim = component_section(
        "function claimConversationOperation(",
        "export function claimConversationPrimary(",
        "operation claim",
    )
    _require_source_markers(
        operation_claim,
        f"{component_path}#operation-claim",
        {
            "primary generation invalidation": "coordinator.generation += 1;",
            "synchronous cursor/send mutual exclusion": "coordinator.interactionClaimId !== null",
            "primary claim ownership": "coordinator.primaryClaimId = id;",
            "cursor/send interaction ownership": "coordinator.interactionClaimId = id;",
        },
        errors,
    )
    current_read = component_section(
        "export function isCurrentConversationRead(",
        "export function releaseConversationOperation(",
        "current-read guard",
    )
    _require_source_markers(
        current_read,
        f"{component_path}#current-read",
        {
            "scope-and-generation completion guard": "coordinator.scope === claim.scope && coordinator.generation === claim.generation",
        },
        errors,
    )
    release = component_section(
        "export function releaseConversationOperation(",
        "export function ConversationThread(",
        "operation release",
    )
    _require_source_markers(
        release,
        f"{component_path}#operation-release",
        {
            "scope-owned release": "if (coordinator.scope !== claim.scope) return false;",
            "primary owner-only release": "coordinator.primaryClaimId === claim.id",
            "interaction owner-only release": "coordinator.interactionClaimId === claim.id",
        },
        errors,
    )

    test_path = "apps/web/tests/conversation-thread.test.ts"
    tests = _read_anchor_source(root, test_path, errors)
    _require_source_markers(
        tests,
        test_path,
        {
            "dynamic deferred helper": "function deferred<T>() {",
            "deferred refresh-versus-pagination test": "does not append a pagination response after a newer refresh wins",
            "deferred scope-change test": "invalidates an in-flight read when its subject or conversation scope changes",
            "deferred stale-release test": "does not let a stale completion release or replace a newer primary read",
            "duplicate cursor/send interaction test": "rejects same-tick duplicate cursor and send claims before another dispatch can start",
            "deferred current-primary recovery test": "keeps retained messages gated after a current refresh fails until a current load succeeds",
            "private-content non-persistence/logging assertion": "expect(source).not.toMatch(/localStorage|sessionStorage|console\\.|URLSearchParams/u);",
        },
        errors,
    )
    if tests.count("deferred<string[]>();") < 4:
        _error(
            errors,
            f"repository.anchors.{test_path}",
            "must retain dynamic deferred coverage across private coordinator reads",
        )
    if component.count('disabled={busy || loadState !== "loaded"}') < 2:
        _error(
            errors,
            f"repository.anchors.{component_path}",
            "must gate both private-message composing and pagination until current primary success",
        )
    return errors


def _private_read_epoch_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    relative_path = "apps/web/lib/private-read-epoch.ts"
    source = _read_anchor_source(root, relative_path, errors)
    _require_source_markers(
        source,
        relative_path,
        {
            "private-read epoch type": "export type PrivateReadEpoch = {",
            "ready private-read epoch type": "export type ReadyPrivateReadEpoch = PrivateReadEpoch & {",
            "private-read epoch creator": "export function createPrivateReadEpoch(): PrivateReadEpoch",
            "ready private-read epoch creator": "export function createReadyPrivateReadEpoch(): ReadyPrivateReadEpoch",
            "private-read begin primitive": "export function beginPrivateRead<T extends PrivateReadEpoch>(state: T): number",
            "private-read finish primitive": "export function finishPrivateRead<T extends PrivateReadEpoch>(state: T, requestEpoch: number): void",
            "private-read current primitive": "export function privateReadIsCurrent<T extends PrivateReadEpoch>(state: T, requestEpoch: number): boolean",
            "private-read dependent-write gate": "export function privateReadAllowsDependentWrite<T extends PrivateReadEpoch>(state: T): boolean",
            "private-read dependent-action gate": "export function privateReadAllowsDependentAction(state: ReadyPrivateReadEpoch): boolean",
        },
        errors,
    )
    if "@/components/" in source or "../components/" in source:
        _error(
            errors,
            f"repository.anchors.{relative_path}",
            "must not depend on component modules",
        )
    begin_source = _typescript_function_source(
        source, "beginPrivateRead", relative_path, errors
    )
    _ordered_anchor_positions(
        begin_source,
        f"{relative_path}#beginPrivateRead",
        [
            ("epoch increment", "state.current += 1;"),
            ("synchronous in-flight marker", "state.inFlight = true;"),
        ],
        errors,
    )
    ready_begin_source = _typescript_function_source(
        source, "beginReadyPrivateRead", relative_path, errors
    )
    _ordered_anchor_positions(
        ready_begin_source,
        f"{relative_path}#beginReadyPrivateRead",
        [
            ("synchronous readiness revocation", "state.ready = false;"),
            ("shared read begin", "return beginPrivateRead(state);"),
        ],
        errors,
    )
    action_source = _typescript_function_source(
        source, "privateReadAllowsDependentAction", relative_path, errors
    )
    _require_source_markers(
        action_source,
        f"{relative_path}#privateReadAllowsDependentAction",
        {
            "ready-and-settled dependent-action gate": "return state.ready && !state.inFlight;",
        },
        errors,
    )
    return errors


def _private_network_read_surface_errors(root: Path) -> list[str]:
    errors = _private_read_epoch_surface_errors(root)
    required = {
        "apps/web/lib/social-api.ts": {
            "subject-bound connection-request reader": "listConnectionRequestInboxForSubject(",
            "subject-bound connections reader": "listConnectionsForSubject(",
            "subject-bound conversations reader": "listConversationsForSubject(",
            "subject-bound notifications reader": "listNotificationsForSubject(",
            "shared subject-bound token gate": "withSubjectBoundToken(getToken, isSubjectCurrent",
        },
        "apps/web/components/network-hub.tsx": {
            "subject-keyed private state": "<AuthenticatedNetwork key={subject}",
            "extracted private-read hook": "usePrivateNetworkReads({",
            "retained private-read compatibility export": '} from "@/components/private-network-reads";',
            "synchronous global mutation claim": "if (busyRef.current !== null) return false;",
        },
        "apps/web/components/private-network-reads.ts": {
            "independent initial slice guard": "initialLoadInFlightRef = useRef(new Set<NetworkSlice>())",
            "request-specific read guard": "() => current(requestSubject)",
            "shared private-read epoch import": '} from "@/lib/private-read-epoch";',
            "retained private-network epoch type adapter": "export type PrivateNetworkReadEpoch = ReadyPrivateReadEpoch;",
            "retained private-network epoch creator adapter": "export function createPrivateNetworkReadEpoch()",
            "retained private-network begin adapter": "export function beginPrivateNetworkRead(",
            "retained private-network finish adapter": "export function finishPrivateNetworkRead(",
            "retained private-network readiness adapter": "export function markPrivateNetworkReadReady(",
            "retained private-network current adapter": "export function privateNetworkReadIsCurrent(",
            "retained private-network dependent-action adapter": "export function privateNetworkReadAllowsDependentAction(",
            "current-success readiness marker": "export function markPrivateNetworkReadReady(",
            "per-slice read state": "const readEpochRef = useRef(emptySliceEpochs());",
            "connection-request initial guard": 'initialLoadInFlightRef.current.has("requests")',
            "connections initial guard": 'initialLoadInFlightRef.current.has("connections")',
            "conversations initial guard": 'initialLoadInFlightRef.current.has("conversations")',
            "notifications initial guard": 'initialLoadInFlightRef.current.has("notifications")',
        },
        "apps/web/components/network-panels.tsx": {
            "connection retained-data error": 'label="Connections could not be refreshed"',
            "request retained-data error": 'label="Connection requests could not be refreshed"',
            "conversation retained-data error": 'label="Conversations could not be refreshed"',
            "notification retained-data error": 'label="Notifications could not be refreshed"',
        },
        "apps/web/tests/social-api.test.ts": {
            "subject rotation before dispatch test": "does not dispatch any private network collection after a subject changes during token resolution",
            "strict collection envelope test": "rejects missing private collection arrays instead of treating them as empty",
        },
        "apps/web/tests/network-hub.test.ts": {
            "subject remount test": "remounts private state by authenticated subject",
            "independent deduplication test": "deduplicates each initial slice independently and rejects stale responses",
            "pagination guard test": "keeps four slice states independent and guards paginated reads too",
            "refresh-pagination coordination test": "coordinates refresh, pagination, and dependent writes per slice",
            "behavioral overlapping-read test": "makes a newer same-subject read authoritative before either promise resolves",
            "retained-data write-exclusion test": "keeps retained rows visible but blocks dependent actions after a current refresh fails",
        },
    }
    component_path = "apps/web/components/private-network-reads.ts"
    component = ""
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
        component = source if relative_path == component_path else component
    loader_specs = [
        (
            "requests",
            "loadRequests",
            "loadConnections",
            "listConnectionRequestInboxForSubject",
        ),
        (
            "connections",
            "loadConnections",
            "loadConversations",
            "listConnectionsForSubject",
        ),
        (
            "conversations",
            "loadConversations",
            "loadNotifications",
            "listConversationsForSubject",
        ),
        (
            "notifications",
            "loadNotifications",
            "refresh",
            "listNotificationsForSubject",
        ),
    ]
    for slice_name, loader, next_loader, reader in loader_specs:
        start = component.find(f"const {loader} = useCallback")
        end = component.find(f"const {next_loader} = useCallback", start + 1)
        body = component[start:end] if start >= 0 and end > start else ""
        _require_source_markers(
            body,
            f"{component_path}#{slice_name}-loader",
            {
                f"{slice_name} pre-dispatch subject guard": "if (!current(requestSubject)) return;",
                f"{slice_name} read begin": f'beginRead("{slice_name}")',
                f"{slice_name} current response guard": f'readIsCurrent("{slice_name}", requestEpoch)',
                f"{slice_name} current success marker": f"markPrivateNetworkReadReady(readEpochRef.current.{slice_name}, requestEpoch)",
                f"{slice_name} current-only finish": f'finishRead("{slice_name}", requestEpoch)',
            },
            errors,
        )
        _ordered_anchor_positions(
            body,
            f"{component_path}#{slice_name}-loader",
            [
                ("subject precheck", "if (!current(requestSubject)) return;"),
                ("synchronous read begin", f'beginRead("{slice_name}")'),
                ("subject-bound dispatch", f"await {reader}(getToken"),
                (
                    "current response guard",
                    f'readIsCurrent("{slice_name}", requestEpoch)',
                ),
                (
                    "current success marker",
                    f"markPrivateNetworkReadReady(readEpochRef.current.{slice_name}, requestEpoch)",
                ),
            ],
            errors,
        )
    pagination_specs = [
        (
            "requests",
            "loadOlderRequests",
            "const loadOlderConnections",
            "requestCursorRef",
            "requestsRef",
        ),
        (
            "connections",
            "loadOlderConnections",
            "const loadOlderConversations",
            "connectionCursorRef",
            "connectionsRef",
        ),
        (
            "conversations",
            "loadOlderConversations",
            "const loadOlderNotifications",
            "conversationCursorRef",
            "conversationsRef",
        ),
        (
            "notifications",
            "loadOlderNotifications",
            "  return {",
            "notificationCursorRef",
            "notificationsRef",
        ),
    ]
    for slice_name, loader, next_symbol, cursor_ref, items_ref in pagination_specs:
        start = component.find(f"const {loader} = async")
        end = component.find(next_symbol, start + 1)
        body = component[start:end] if start >= 0 and end > start else ""
        _require_source_markers(
            body,
            f"{component_path}#{slice_name}-pagination",
            {
                f"{slice_name} current cursor ref": f"const cursor = {cursor_ref}.current;",
                f"{slice_name} refresh exclusion": f'!readAllowsDependentAction("{slice_name}")',
                f"{slice_name} pagination in-flight guard": f'moreInFlightRef.current.has("{slice_name}")',
                f"{slice_name} delivered-cursor guard": f'deliveredCursorsRef.current.get("{slice_name}")',
                f"{slice_name} current-epoch append guard": f'readIsCurrent("{slice_name}", requestEpoch)',
                f"{slice_name} current-ref append": f"appendCursorPage({items_ref}.current, page, cursor, delivered)",
            },
            errors,
        )
        _ordered_anchor_positions(
            body,
            f"{component_path}#{slice_name}-pagination",
            [
                ("current cursor read", f"const cursor = {cursor_ref}.current;"),
                ("request dispatch", "await list"),
                ("current epoch guard", f'readIsCurrent("{slice_name}", requestEpoch)'),
                (
                    "current-ref append",
                    f"appendCursorPage({items_ref}.current, page, cursor, delivered)",
                ),
            ],
            errors,
        )
    return errors


def _private_idempotency_surface_errors(root: Path) -> list[str]:
    """Require durable private-graph delete and shared late-receipt anchors."""

    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)

    delete_source = _function_source(
        main_source, "remove_connection", main_path, errors
    )
    _require_source_markers(
        delete_source,
        f"{main_path}#remove_connection",
        {
            "Request boundary": "request: Request",
            "human-only participant guard": 'require_social_human(principal, "connections:write")',
            "required idempotency key": "key = idempotency_key(request, required=True)",
            "canonical delete operation": 'operation = f"DELETE:/v1/connections/{connection_id}"',
            "canonical empty fingerprint": 'fingerprint = _request_fingerprint(operation, "")',
            "replay preflight": "replay = await idempotency_replay(",
            "replay return": "return replay",
            "participant lookup": "row = await active_connection_for_participant(",
            "transactional receipt helper": "await store_idempotency(",
            "empty 204 receipt status": "status_code=204,",
            "empty 204 receipt body": 'body="",',
            "empty 204 receipt headers": "headers={},",
            "connection receipt resource": 'resource_type="connection",',
            "empty 204 response": "return Response(status_code=204)",
        },
        errors,
    )
    _ordered_anchor_positions(
        delete_source,
        f"{main_path}#remove_connection",
        [
            (
                "human-only participant guard",
                'require_social_human(principal, "connections:write")',
            ),
            (
                "required idempotency key",
                "key = idempotency_key(request, required=True)",
            ),
            (
                "canonical operation",
                'operation = f"DELETE:/v1/connections/{connection_id}"',
            ),
            (
                "canonical fingerprint",
                'fingerprint = _request_fingerprint(operation, "")',
            ),
            ("replay before lookup", "replay = await idempotency_replay("),
            ("replay return", "return replay"),
            ("participant lookup", "row = await active_connection_for_participant("),
            ("transactional receipt", "await store_idempotency("),
            ("final empty response", "return Response(status_code=204)"),
        ],
        errors,
    )

    delete_test_path = "apps/api/tests/test_private_social_graph.py"
    delete_test_source = _read_anchor_source(root, delete_test_path, errors)
    delete_test = _function_source(
        delete_test_source,
        "test_remove_connection_is_durably_idempotent",
        delete_test_path,
        errors,
    )
    _require_source_markers(
        delete_test,
        f"{delete_test_path}#test_remove_connection_is_durably_idempotent",
        {
            "missing-key denial": "assert missing_key.status_code == 428",
            "nonparticipant denial": "assert nonparticipant.status_code == 404",
            "agent denial": "assert agent_attempt.status_code == 403",
            "first 204 status": "assert first.status_code == 204",
            "first empty body": 'assert first.content == b""',
            "stored 204 status": "assert receipt.response_status == 204",
            "stored empty body": 'assert receipt.response_body == ""',
            "stored connection resource": 'assert receipt.resource_type == "connection"',
            "stored connection id": "assert receipt.resource_id == connection_id",
            "row removal before replay": "await session.delete(row)",
            "replayed 204 status": "assert replay.status_code == 204",
            "replayed empty body": 'assert replay.content == b""',
            "replayed marker": 'assert replay.headers["idempotency-replayed"] == "true"',
            "different-path collision target": '"/v1/connections/different-connection"',
            "same-key collision": "assert collision.status_code == 409",
        },
        errors,
    )
    _ordered_anchor_positions(
        delete_test,
        f"{delete_test_path}#test_remove_connection_is_durably_idempotent",
        [
            ("first 204 status", "assert first.status_code == 204"),
            ("first empty body", 'assert first.content == b""'),
            ("stored 204 status", "assert receipt.response_status == 204"),
            ("stored empty body", 'assert receipt.response_body == ""'),
            ("row removal", "await session.delete(row)"),
            ("replayed 204 status", "assert replay.status_code == 204"),
            ("replayed empty body", 'assert replay.content == b""'),
            (
                "different-path collision target",
                '"/v1/connections/different-connection"',
            ),
            ("same-key collision", "assert collision.status_code == 409"),
        ],
        errors,
    )

    concurrent_test_path = "apps/api/tests/test_organization_verification.py"
    concurrent_test_source = _read_anchor_source(root, concurrent_test_path, errors)
    concurrent_test = _function_source(
        concurrent_test_source,
        "test_reviewer_decision_same_key_is_concurrently_replayed_once",
        concurrent_test_path,
        errors,
    )
    _require_source_markers(
        concurrent_test,
        f"{concurrent_test_path}#test_reviewer_decision_same_key_is_concurrently_replayed_once",
        {
            "concurrent same-key dispatch": "first, second = await asyncio.gather(",
            "both successful responses": "assert first.status_code == second.status_code == 200",
            "equal response payloads": "assert first.json() == second.json()",
            "one replay marker": (
                'assert {\n        first.headers.get("idempotency-replayed"),\n'
                '        second.headers.get("idempotency-replayed"),\n'
                '    } == {\n        None,\n        "true",\n    }'
            ),
            "same-key collision": "assert collision.status_code == 409",
            "one review event": "assert len(review_events) == 1",
            "one idempotency receipt": "assert len(receipts) == 1",
        },
        errors,
    )
    _ordered_anchor_positions(
        concurrent_test,
        f"{concurrent_test_path}#test_reviewer_decision_same_key_is_concurrently_replayed_once",
        [
            ("concurrent same-key dispatch", "first, second = await asyncio.gather("),
            (
                "both successful responses",
                "assert first.status_code == second.status_code == 200",
            ),
            ("equal response payloads", "assert first.json() == second.json()"),
            (
                "one replay marker",
                (
                    'assert {\n        first.headers.get("idempotency-replayed"),\n'
                    '        second.headers.get("idempotency-replayed"),\n'
                    '    } == {\n        None,\n        "true",\n    }'
                ),
            ),
            ("same-key collision", "assert collision.status_code == 409"),
            ("one review event", "assert len(review_events) == 1"),
            ("one idempotency receipt", "assert len(receipts) == 1"),
        ],
        errors,
    )

    store_source = _function_source(main_source, "store_idempotency", main_path, errors)
    store_path = f"{main_path}#store_idempotency"
    _require_source_markers(
        store_source,
        store_path,
        {
            "provisional receipt parameter": "provisional_record: IdempotencyRecord | None = None,",
            "object-identity provisional gate": (
                "if provisional_record is not None and existing is provisional_record:"
            ),
            "provisional status completion": "existing.response_status = status_code",
            "provisional body completion": "existing.response_body = body",
            "provisional header completion": (
                "existing.response_headers = json.dumps(headers, sort_keys=True)"
            ),
            "late-existing rollback": "await session.rollback()",
            "late-existing replay reload": "replay = await idempotency_replay(",
            "late-existing replay exception": (
                "raise ConcurrentIdempotencyReplay(replay)"
            ),
        },
        errors,
    )
    existing_start = store_source.find("if existing is not None:")
    outer_else = store_source.find("\n        else:\n", existing_start + 1)
    if existing_start < 0 or outer_else < 0:
        _error(
            errors,
            store_path,
            "must expose a scoped existing-record branch and new-record else branch",
        )
    else:
        existing_branch = store_source[existing_start:outer_else]
        provisional_start = existing_branch.find(
            "if provisional_record is not None and existing is provisional_record:"
        )
        concurrent_start = existing_branch.find(
            "\n            else:\n", provisional_start + 1
        )
        if concurrent_start < 0:
            _error(
                errors,
                store_path,
                "late-existing branch must separate provisional ownership from concurrent replay",
            )
        else:
            concurrent_branch = existing_branch[concurrent_start:]
            _ordered_anchor_positions(
                concurrent_branch,
                store_path,
                [
                    ("loser rollback", "await session.rollback()"),
                    ("winner receipt replay", "replay = await idempotency_replay("),
                    (
                        "concurrent replay exception",
                        "raise ConcurrentIdempotencyReplay(replay)",
                    ),
                ],
                errors,
            )
            if re.search(
                r"existing\.[A-Za-z_][A-Za-z0-9_]*\s*=",
                concurrent_branch,
            ):
                _error(
                    errors,
                    store_path,
                    "late-existing concurrent branch must not overwrite the committed receipt",
                )
            if "session.commit(" in concurrent_branch:
                _error(
                    errors,
                    store_path,
                    "late-existing concurrent branch must not commit loser mutations",
                )
    integrity_start = store_source.find("except IntegrityError as exc:")
    if integrity_start < 0:
        _error(errors, store_path, "must handle an idempotency insert race")
    else:
        integrity_branch = store_source[integrity_start:]
        _ordered_anchor_positions(
            integrity_branch,
            store_path,
            [
                ("insert-race rollback", "await session.rollback()"),
                ("insert-race replay reload", "replay = await idempotency_replay("),
                (
                    "insert-race replay exception",
                    "raise ConcurrentIdempotencyReplay(replay)",
                ),
            ],
            errors,
        )
    return errors


def _follow_content_block_durability_errors(root: Path) -> list[str]:
    """Bind private follow and content-block writes to one durable replay contract."""

    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    openapi = _function_source(main_source, "_social_openapi_extra", main_path, errors)
    _require_source_markers(
        openapi,
        f"{main_path}#_social_openapi_extra",
        {
            "human-only OpenAPI extension": '"x-connectmd-human-only": True',
            "Idempotency-Key helper delegation": '"parameters": [_idempotency_openapi_parameter()]',
        },
        errors,
    )
    idempotency_parameter = _function_source(main_source, "_idempotency_openapi_parameter", main_path, errors)  # fmt: skip
    _require_source_markers(
        idempotency_parameter,
        f"{main_path}#_idempotency_openapi_parameter",
        {
            "Idempotency-Key name": '"name": "Idempotency-Key"',
            "Idempotency-Key header location": '"in": "header"',
            "required Idempotency-Key header": '"required": True',
            "minimum Idempotency-Key bound": '"minLength": 1',
            "maximum Idempotency-Key bound": '"maxLength": 128',
            "visible-ASCII Idempotency-Key pattern": "_IDEMPOTENCY_KEY_PATTERN",
        },
        errors,
    )
    for route, label in (
        ("POST /v1/follows/{profile_handle}", "follow"),
        ("DELETE /v1/follows/{profile_handle}", "unfollow"),
        ("POST /v1/content-blocks/{profile_handle}", "content block"),
        ("DELETE /v1/content-blocks/{profile_handle}", "content unblock"),
    ):
        decorator = _route_decorator(route, main_source)
        if decorator is None:
            _error(
                errors,
                f"repository.follow_content_block.{label}",
                f"cannot locate implemented route {route!r}",
            )
            continue
        _require_source_markers(
            decorator,
            f"{main_path}#{label}-route",
            {"shared social OpenAPI contract": "openapi_extra=_social_openapi_extra()"},
            errors,
        )

    def require_order(source: str, function_name: str, anchors) -> None:
        position = -1
        for label, marker in anchors:
            position = source.find(marker, position + 1)
            if position < 0:
                _error(
                    errors,
                    f"repository.follow_content_block.{main_path}#{function_name}",
                    f"is missing {label} anchor {marker!r}",
                )
                return

    social_handlers = {
        "follow_profile": (
            'operation = f"POST:/v1/follows/{profile_handle}"',
            'resource_type="social_follow"',
            "follow",
        ),
        "unfollow_profile": (
            'operation = f"DELETE:/v1/follows/{profile_handle}"',
            'resource_type="social_follow"',
            "unfollow",
        ),
        "block_post_content": (
            'operation = f"POST:/v1/content-blocks/{profile_handle}"',
            'resource_type="social_content_block"',
            "block",
        ),
        "unblock_post_content": (
            'operation = f"DELETE:/v1/content-blocks/{profile_handle}"',
            'resource_type="social_content_block"',
            "unblock",
        ),
    }
    handler_sources: dict[str, str] = {}
    for function_name, (operation, resource_type, action) in social_handlers.items():
        source = _function_source(main_source, function_name, main_path, errors)
        handler_sources[function_name] = source
        _require_source_markers(
            source,
            f"{main_path}#{function_name}",
            {
                "signed-in human authority": 'require_social_human(principal, "documents:read")',
                "required idempotency key": "key = idempotency_key(request, required=True)",
                "operation binding": operation,
                "empty request fingerprint": 'fingerprint = _request_fingerprint(operation, "")',
                "pre-target replay": "replay = await idempotency_replay(",
                "initial public target": "initial_profile = await public_profile_by_handle(session, profile_handle)",
                "pair and target lock": "profile = await lock_social_target(",
                "post-lock replay": "replay = await idempotency_replay(",
                "locked pair rows": "await social_graph_pair_rows(",
                "transaction flush": "await session.flush()",
                "digest-bound social receipt": "resource_id = _social_resource_id(",
                "transactional receipt": "await store_idempotency(",
                "receipt type": resource_type,
                "final response": "return Response(",
            },
            errors,
        )
        require_order(
            source,
            function_name,
            [
                (
                    "human authority",
                    'require_social_human(principal, "documents:read")',
                ),
                ("idempotency key", "key = idempotency_key(request, required=True)"),
                ("operation", operation),
                ("pre-target replay", "replay = await idempotency_replay("),
                ("initial target", "initial_profile = await public_profile_by_handle("),
                ("pair and target lock", "profile = await lock_social_target("),
                ("post-lock replay", "replay = await idempotency_replay("),
                ("locked pair rows", "await social_graph_pair_rows("),
                ("receipt digest", "resource_id = _social_resource_id("),
                ("receipt storage", "await store_idempotency("),
            ],
        )
        if action == "follow":
            _require_source_markers(
                source,
                f"{main_path}#{function_name}",
                {
                    "quota consumption": "await consume_post_quota(",
                    "quota failure rollback": "await session.rollback()",
                    "safe follow response": "FollowResponse(",
                    "successful follow receipt": "status_code=200",
                    "follow receipt body": "body=response_body",
                },
                errors,
            )
            require_order(
                source,
                function_name,
                [
                    ("pair rows", "await social_graph_pair_rows("),
                    ("quota", "await consume_post_quota("),
                    ("flush", "await session.flush()"),
                    ("re-read rows", "await social_graph_pair_rows("),
                    ("receipt", "await store_idempotency("),
                ],
            )
        else:
            _require_source_markers(
                source,
                f"{main_path}#{function_name}",
                {
                    "empty 204 receipt": 'status_code=204,\n            body="",\n            headers={},',
                    "empty 204 response": "return Response(status_code=204)",
                },
                errors,
            )
    block = handler_sources.get("block_post_content", "")
    _require_source_markers(
        block,
        f"{main_path}#block_post_content",
        {
            "content-block row": "PostContentBlock(",
            "reverse-follow deletion": "ProfileFollow.follower_owner_id == profile.owner_id",
            "direct-follow deletion": "ProfileFollow.follower_owner_id == principal.subject",
            "post-delete pair re-read": "follows, blocks = await social_graph_pair_rows(",
        },
        errors,
    )
    require_order(
        block,
        "block_post_content",
        [
            ("block pair rows", "follows, blocks = await social_graph_pair_rows("),
            ("block row", "PostContentBlock("),
            ("two-direction follow deletion", "await session.execute("),
            ("flush", "await session.flush()"),
            (
                "post-delete pair re-read",
                "follows, blocks = await social_graph_pair_rows(",
            ),
            ("receipt", "await store_idempotency("),
        ],
    )
    pair_lock = _function_source(main_source, "lock_post_graph_pair", main_path, errors)
    _require_source_markers(
        pair_lock,
        f"{main_path}#lock_post_graph_pair",
        {
            "normalized owner pair": "owner_pair(first_owner_id, second_owner_id)",
            "PostgreSQL pair insert": "postgresql_insert(PostGraphPairLock)",
            "SQLite pair insert": "sqlite_insert(PostGraphPairLock)",
            "pair lock": ".with_for_update()",
        },
        errors,
    )
    target_lock = _function_source(main_source, "lock_social_target", main_path, errors)
    _require_source_markers(
        target_lock,
        f"{main_path}#lock_social_target",
        {
            "pair lock before target": "await lock_post_graph_pair(session, principal.subject, initial_profile.owner_id)",
            "current public target lock": "public_profile_by_handle(session, profile_handle, for_update=True)",
            "target id revalidation": "current_profile.id != initial_profile.id",
            "target owner revalidation": "current_profile.owner_id != initial_profile.owner_id",
            "target handle revalidation": "current_profile.public_identifier != profile_handle",
        },
        errors,
    )
    replay = _function_source(main_source, "social_graph_replay", main_path, errors)
    _require_source_markers(
        replay,
        f"{main_path}#social_graph_replay",
        {
            "social operation context": '"POST:/v1/follows/": ("follow", "follow", "social_follow")',
            "receipt type guard": "record.resource_type != expected_resource_type",
            "receipt status guard": 'record.response_status != (200 if action == "follow" else 204)',
            "receipt header guard": 'record.response_headers != "{}"',
            "follow receipt body guard": 'if action == "follow" and not record.response_body',
            "empty 204 receipt body guard": 'if action != "follow" and record.response_body != ""',
            "resource parser": '_social_resource_parts(record.resource_id or "")',
            "current public target": "profile = await public_profile_by_handle(session, profile_handle)",
            "resource target binding": 'profile.id != parts["target_document_id"]',
            "pair replay lock": "await lock_post_graph_pair(session, principal.subject, profile.owner_id)",
            "locked target": "public_profile_by_handle(\n                    session, profile_handle, for_update=True",
            "locked pair rows": "await social_graph_pair_rows(",
            "safe follow body keys": 'set(raw_body) != {\n                    "profile_handle",\n                    "created_at",\n                }',
            "safe follow response model": "FollowResponse.model_validate(raw_body)",
            "block side-effect replay gate": "or follows",
            "digest reconstruction": "expected_digest = _social_receipt_digest(",
            "digest comparison": 'compare_digest(parts["digest"], expected_digest)',
            "fail-closed response": "status_code=503",
            "replay marker": 'headers={"Idempotency-Replayed": "true"}',
        },
        errors,
    )
    digest = _function_source(main_source, "_social_receipt_digest", main_path, errors)
    _require_source_markers(
        digest,
        f"{main_path}#_social_receipt_digest",
        {
            "hashed actor fact": '"actor_owner_digest": sha256(actor_owner_id.encode()).hexdigest()',
            "hashed target fact": '"target_owner_digest": sha256(target_owner_id.encode()).hexdigest()',
            "response-body digest": '"response_digest": sha256(response_body.encode()).hexdigest()',
            "pair row facts": '"follows": sorted(',
            "content-block facts": '"blocks": sorted(',
        },
        errors,
    )
    idempotency = _function_source(main_source, "idempotency_replay", main_path, errors)
    _require_source_markers(
        idempotency,
        f"{main_path}#idempotency_replay",
        {
            "first-call compatibility": "if record is None:\n            return None",
            "same-key collision": "Idempotency-Key was already used for a different request",
            "social replay dispatch": '"POST:/v1/follows/",\n                "DELETE:/v1/follows/",',
            "social replay helper": "return await social_graph_replay(session, request, principal, record, operation)",
        },
        errors,
    )
    api_test_path = "apps/api/tests/test_follow_block_durability.py"
    api_tests = _read_anchor_source(root, api_test_path, errors)
    _require_source_markers(
        api_tests,
        api_test_path,
        {
            "OpenAPI and key coverage": "test_social_mutations_require_visible_ascii_keys_and_advertise_them",
            "exact follow, quota, and noop coverage": "test_follow_exact_replay_quota_and_noop_unfollow_are_durable",
            "two-direction block coverage": "test_block_receipt_binds_both_follow_side_effects_and_unblock",
            "pre-target replay and collision coverage": "test_social_replay_precedes_target_not_found_and_collisions_are_bounded",
            "target substitution corruption coverage": "test_social_receipt_corruption_and_target_substitution_fail_closed",
            "receipt metadata corruption coverage": "test_social_follow_receipt_metadata_corruption_fails_closed",
            "row-state corruption coverage": "test_social_receipt_result_state_corruption_fails_closed",
            "same-key block atomicity": "test_same_key_block_gather_has_one_atomic_effect_and_receipt",
            "no pre-success receipt coverage": "test_rejected_social_requests_and_noop_replay_state_are_not_recipted",
            "same-key follow atomicity": "test_same_key_social_gather_has_one_effect_and_one_receipt",
            "protocol exclusion coverage": "test_social_discovery_excludes_graph_writes",
            "collision assertion": "assert collision_handle.status_code == collision_method.status_code == 409",
            "safe corruption result": 'assert_social_unavailable(replay, "reader", key)',
            "SQLite gather concurrency": "await asyncio.gather(",
        },
        errors,
    )
    web_path = "apps/web/lib/posts-api.ts"
    web = _read_anchor_source(root, web_path, errors)
    _require_source_markers(
        web,
        web_path,
        {
            "caller-owned follow key": "export async function followProfile(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string)",
            "caller-owned unfollow key": "export async function unfollowProfile(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string)",
            "caller-owned block key": "export async function blockProfileContent(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string)",
            "caller-owned unblock key": "export async function unblockProfileContent(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string)",
            "subject-bound dispatch": "withSubjectBoundToken(getToken, isSubjectCurrent",
            "strict follow 200": 'if (response.status !== 200) throw invalidMutationResponse("follow")',
            "strict empty 204": 'if (response.status !== 204 || response.body !== "")',
            "strict replay marker": 'if (replayed !== null && replayed !== "true")',
            "safe follow parser": "parseFollow(response.body, handle)",
            "diagnostic-free mutation failure": 'new ApiRequestError(`The API returned an invalid ${label} response.`, 502, "server")',
        },
        errors,
    )
    controls_path = "apps/web/components/profile-post-controls.tsx"
    controls = _read_anchor_source(root, controls_path, errors)

    def control_handler_source(function_name: str, next_function: str | None) -> str:
        start = controls.find(f"  async function {function_name}() {{")
        if start < 0:
            _error(
                errors,
                f"repository.follow_content_block.{controls_path}",
                f"must define private control handler {function_name!r}",
            )
            return ""
        end = (
            controls.find(f"  async function {next_function}() {{", start + 1)
            if next_function
            else controls.find("\n\n  return <section", start + 1)
        )
        if end < 0:
            _error(
                errors,
                f"repository.follow_content_block.{controls_path}",
                f"cannot bound private control handler {function_name!r}",
            )
            return ""
        return controls[start:end]

    for function_name, next_function, attempt_ref, calls in (
        (
            "toggleFollow",
            "toggleBlock",
            "followAttemptRef.current = beginLogicalMutationAttempt(",
            (
                "unfollowProfile(handle, getToken, requestIsCurrent, attempt.idempotencyKey)",
                "followProfile(handle, getToken, requestIsCurrent, attempt.idempotencyKey)",
            ),
        ),
        (
            "toggleBlock",
            None,
            "blockAttemptRef.current = beginLogicalMutationAttempt(",
            (
                "unblockProfileContent(handle, getToken, requestIsCurrent, attempt.idempotencyKey)",
                "blockProfileContent(handle, getToken, requestIsCurrent, attempt.idempotencyKey)",
            ),
        ),
    ):
        handler = control_handler_source(function_name, next_function)
        _require_source_markers(
            handler,
            f"{controls_path}#{function_name}",
            {
                "synchronous mutation claim": "const claim = claimLogicalMutation(mutationClaimSlotRef.current);",
                "captured subject": "const requestSubject = subject;",
                "current subject and claim guard": "const requestIsCurrent = () => isSubjectCurrent() && claim.isCurrent();",
                "caller-owned logical attempt": attempt_ref,
                "caller-owned mutation calls": calls[0],
                "caller-owned mutation fallback": calls[1],
                "stale completion guard": "if (!requestIsCurrent()) return;",
                "owner-only mutation release": "claim.release(); setBusy(null)",
            },
            errors,
        )
        require_order(
            handler,
            function_name,
            [
                ("captured subject", "const requestSubject = subject;"),
                (
                    "synchronous mutation claim",
                    "const claim = claimLogicalMutation(mutationClaimSlotRef.current);",
                ),
                (
                    "current subject and claim guard",
                    "const requestIsCurrent = () => isSubjectCurrent() && claim.isCurrent();",
                ),
                ("caller-owned logical attempt", attempt_ref),
                ("mutation key handoff", "attempt.idempotencyKey"),
                ("stale completion guard", "if (!requestIsCurrent()) return;"),
                ("owner-only mutation release", "claim.release(); setBusy(null)"),
            ],
        )
    web_test_path = "apps/web/tests/posts-api.test.ts"
    web_tests = _read_anchor_source(root, web_test_path, errors)
    _require_source_markers(
        web_tests,
        web_test_path,
        {
            "caller-owned key and strict response test": "uses caller-owned keys and exact follow/content-control response contracts",
            "ambiguous response retry test": "reuses an explicit key after ambiguous or malformed successful follow responses",
            "strict status/body/replay test": "rejects wrong successful status, body, and replay marker without response diagnostics",
            "credential-shaped response privacy test": "rejects a mismatched or credential-shaped follow response",
            "stale subject pre-dispatch test": "keeps every post/follow/block/report mutation subject-bound before dispatch",
            "synchronous claim source test": "requires the synchronous owner and captured subject guard on every private post-control completion",
        },
        errors,
    )
    mcp = _function_source(main_source, "mcp_tools", main_path, errors)
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    for transport, source in (("mcp_tools", mcp), ("a2a_send_message", a2a)):
        if any(
            marker in source
            for marker in (
                '"name": "follow_profile"',
                '"name": "unfollow_profile"',
                '"name": "block_profile_content"',
                '"name": "unblock_profile_content"',
                'if action == "follow_profile":',
                'if action == "unfollow_profile":',
                'if action == "block_profile_content":',
                'if action == "unblock_profile_content":',
            )
        ):
            _error(
                errors,
                f"repository.follow_content_block.{main_path}#{transport}",
                f"{transport} must not expose follow or content-block mutations",
            )
    return errors


def _immutable_supply_chain_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    pins = {
        ".github/workflows/ci.yml": {
            "checkout v6.0.3 release commit": (
                "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3",
                5,
            ),
            "setup-python v6.2.0 release commit": (
                "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0",
                2,
            ),
            "setup-node v6.4.0 release commit": (
                "actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e # v6.4.0",
                1,
            ),
            "CI PostgreSQL manifest index": (
                "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777",
                3,
            ),
            "CI Meilisearch manifest index": (
                "getmeili/meilisearch:v1.45.0@sha256:7fde2b22e9a7ccfe7551613a521fc1b3abdbec20fedbd9aa0fb8ff133cd83c5d",
                1,
            ),
        },
        "apps/api/Dockerfile": {
            "API Python manifest index": (
                "python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b",
                1,
            ),
            "immutable Debian snapshot": (
                "ARG DEBIAN_SNAPSHOT=20260805T010740Z",
                1,
            ),
            "snapshot-only Debian source": (
                "https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}",
                1,
            ),
            "exact libmagic runtime package": ("libmagic1t64=1:5.46-5", 2),
            "exact Poppler utility package": ("poppler-utils=25.03.0-5+deb13u4", 2),
            "exact Tesseract package": ("tesseract-ocr=5.5.0-1+b1", 2),
            "post-install package verification": ("dpkg-query -W", 1),
        },
        "apps/api/tests/test_dockerfile_supply_chain.py": {
            "immutable snapshot contract test": (
                "test_api_dockerfile_uses_one_immutable_debian_snapshot_and_exact_packages",
                1,
            ),
            "moving apt input rejection test": (
                "test_api_dockerfile_rejects_bare_or_moving_apt_inputs",
                1,
            ),
        },
        "apps/web/Dockerfile": {
            "web Node manifest index": (
                "node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32",
                2,
            ),
        },
        "infra/nginx/Dockerfile": {
            "Nginx manifest index": (
                "nginx:1.30.4-alpine@sha256:97d490c12ba55b4946b01546d1c3ed324e8d41ab1c9fcb2a616aa470620e5b46",
                1,
            ),
        },
        "compose.yaml": {
            "Compose PostgreSQL manifest index": (
                "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777",
                4,
            ),
            "Compose Meilisearch manifest index": (
                "getmeili/meilisearch:v1.45.0@sha256:7fde2b22e9a7ccfe7551613a521fc1b3abdbec20fedbd9aa0fb8ff133cd83c5d",
                1,
            ),
            "Compose Alpine manifest index": (
                "alpine:3.23.5@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40",
                2,
            ),
        },
        "compose.prod.yaml": {
            "production Certbot manifest list": (
                "certbot/certbot:v5.7.0@sha256:34ee91d2f43008eb78a007d22f23ed4b2eaa9a454cb27ca2c042b49527a695b4",
                1,
            ),
        },
    }
    for relative_path, expected in pins.items():
        source = _read_anchor_source(root, relative_path, errors)
        for label, (marker, count) in expected.items():
            actual = source.count(marker)
            if actual != count:
                _error(
                    errors,
                    f"repository.supply_chain.{relative_path}",
                    f"must contain {label} exactly {count} time(s); found {actual}",
                )
    return errors


def _auth_return_intent_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "apps/web/lib/auth-return-intent.ts": {
            "canonical profile-handle allowlist": "const PROFILE_HANDLE_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/u;",
            "canonical return-path allowlist": "const PROFILE_RETURN_PATH_PATTERN = /^\\/p\\/([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)$/u;",
            "bounded return candidate": "candidate.length > MAX_AUTH_RETURN_PATH_LENGTH",
            "control and backslash rejection": "/[\\u0000-\\u001F\\u007F\\\\]/u.test(candidate)",
            "explicit action allowlist": 'export const AUTH_RETURN_ACTIONS = ["connect", "follow", "block"] as const;',
        },
        "apps/web/components/profile-connect-control.tsx": {
            "connect return intent": 'buildProfileActionReturnPath(handle, "connect")',
            "sign-in return": "forceRedirectUrl={returnPath}",
            "sign-up return": "signUpForceRedirectUrl={returnPath}",
            "explicit post-auth confirmation": "return here to confirm the request",
        },
        "apps/web/components/profile-post-controls.tsx": {
            "profile-action return intent": 'buildProfileActionReturnPath(handle, "follow")',
            "sign-in return": "forceRedirectUrl={returnPath}",
            "sign-up return": "signUpForceRedirectUrl={returnPath}",
            "explicit post-auth action": "return here to choose the action",
        },
        "apps/web/tests/auth-return-intent.test.ts": {
            "external URL rejection": '"https://evil.example/"',
            "scheme-relative rejection": '"//evil.example/"',
            "encoded dot-segment rejection": '"/p/%2e%2e"',
            "double-encoding rejection": '"/p/%252f%252fevil.example"',
            "encoded backslash rejection": '"/p/ari%5cchen"',
            "no browser persistence": "localStorage|sessionStorage|document\\.cookie|window\\.location",
            "no signed-out mutation replay": "connectSignedOut).not.toMatch",
        },
        "apps/web/tests/profile-connect-control.test.ts": {
            "canonical signed-out handoff test": "keeps the signed-out handoff on the canonical public profile",
            "connection replay rejection": 'signedOutBranch).not.toContain("createConnectionRequest")',
        },
    }
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
    return errors


def _outreach_inbox_read_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "apps/web/components/outreach-inbox.tsx": {
            "subject-keyed remount": "<AuthenticatedInbox key={`${subject}:",
            "independent policy initial guard": "initialPolicyLoadStartedRef",
            "independent inbox initial guard": "initialInboxLoadStartedRef",
            "shared private-read epoch import": '} from "@/lib/private-read-epoch";',
            "retained private-read epoch adapter exports": "export {\n  beginPrivateRead,\n  createPrivateReadEpoch,",
            "retained private-read dependent-write adapter": "  privateReadAllowsDependentWrite,",
            "retained private-read current adapter": "  privateReadIsCurrent,",
            "synchronous inbox refresh begin": "beginPrivateRead(inboxReadEpochRef.current)",
            "synchronous policy refresh begin": "beginPrivateRead(policyReadEpochRef.current)",
            "current-only inbox refresh finish": "finishPrivateRead(inboxReadEpochRef.current, requestEpoch)",
            "current-only policy refresh finish": "finishPrivateRead(policyReadEpochRef.current, requestEpoch)",
            "load-more refresh exclusion": 'inboxLoadState !== "loaded" || !privateReadAllowsDependentWrite(inboxReadEpochRef.current) || busy || !nextCursor',
            "current-epoch response guard": "privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)",
            "loaded-only empty state": 'inboxLoadState === "loaded" && threads.length === 0',
            "refresh failure with retained rows": 'label="Contact requests could not be refreshed"',
            "policy write prerequisite": 'busy || policyLoadState !== "loaded" || !policyHasLoaded || !privateReadAllowsDependentWrite(policyReadEpochRef.current)',
            "inbox action prerequisite": 'busy || inboxLoadState !== "loaded" || !privateReadAllowsDependentWrite(inboxReadEpochRef.current)',
        },
        "apps/web/tests/outreach-inbox.test.ts": {
            "Strict Mode initial dispatch test": "deduplicates each Strict Mode initial policy and inbox dispatch independently",
            "refresh-page race test": "invalidates load-more responses when a refresh supersedes them",
            "behavioral overlapping refresh test": "keeps a newer refresh in flight when an older request settles",
            "older completion assertion": "expect(state).toEqual({ current: 2, inFlight: true })",
            "terminal completion assertion": "expect(state).toEqual({ current: 2, inFlight: false })",
            "synchronous dependent-write test": "blocks policy saves and inbox actions synchronously once a refresh begins",
            "truthful state test": "keeps truthful empty/error states and disables dependent writes until loaded",
            "cursor behavior test": "keeps pagination cursor-bound, monotonic, and deduplicated",
        },
    }
    sources: dict[str, str] = {}
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        sources[relative_path] = source
        _require_source_markers(source, relative_path, markers, errors)
    component_path = "apps/web/components/outreach-inbox.tsx"
    _ordered_anchor_positions(
        sources.get(component_path, ""),
        component_path,
        [
            (
                "synchronous refresh epoch",
                "beginPrivateRead(inboxReadEpochRef.current)",
            ),
            (
                "inbox request dispatch",
                "await listOutreachForSubject(getToken, isSubjectCurrent)",
            ),
        ],
        errors,
    )
    return errors


def _employer_inventory_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    main_source = _read_anchor_source(root, "apps/api/app/main.py", errors)
    discovery_source = _read_anchor_source(
        root, "apps/api/app/routes/discovery.py", errors
    )
    agent_card_path = "apps/api/app/routes/agent_card.py"
    agent_card_source = _read_anchor_source(root, agent_card_path, errors)
    required = {
        "apps/api/app/main.py": {
            "organization inventory route": '"/v1/employer/organizations"',
            "job inventory route": '"/v1/employer/jobs"',
            "human-only OpenAPI extension": 'openapi_extra={"x-connectmd-human-only": True}',
            "Clerk JWT boundary": 'principal.method != "clerk_jwt"',
            "signed cursor encoder": "def employer_inventory_cursor_encode(",
            "signed cursor decoder": "def employer_inventory_cursor_decode(",
            "subject binding": "def employer_inventory_subject_binding(",
            "active administrator predicate": 'OrganizationMembership.role == "admin"',
            "active membership predicate": 'OrganizationMembership.status == "active"',
            "organization keyset scope": 'scope = "employer-organizations"',
            "job keyset scope": 'scope = "employer-jobs"',
            "bounded page read": "limit + 1",
        },
        "apps/api/app/schemas.py": {
            "organization summary": "class EmployerOrganizationSummary(BaseModel):",
            "organization inventory": "class EmployerOrganizationInventoryResponse(BaseModel):",
            "job summary": "class EmployerJobSummary(BaseModel):",
            "job inventory": "class EmployerJobInventoryResponse(BaseModel):",
        },
        "apps/web/lib/recruitment-api.ts": {
            "organization inventory helper": "export async function listManageableOrganizations(",
            "job inventory helper": "export async function listManageableJobs(",
            "organization private route": "/v1/employer/organizations",
            "job private route": "/v1/employer/jobs",
            "subject-bound request": "withSubjectBoundToken(",
            "no-store request": 'cache: "no-store"',
        },
        "apps/web/components/employer-workspace.tsx": {
            "subject-keyed remount": "<AuthenticatedEmployerWorkspace key={subject}",
            "organization initial-load guard": "organizationInventoryInitialInFlightRef",
            "job initial-load guard": "jobInventoryInitialInFlightRef",
            "exact organization reload": "loadOrganizationForOwner(summary.organizationSlug",
            "exact job reload": "loadJobForOwner(summary.organizationSlug, summary.slug",
        },
        "apps/web/components/employer-inventory-panels.tsx": {
            "organization inventory UI": "Organizations I manage",
            "truthful inventory error state": "No empty state is assumed",
        },
        "apps/web/tests/recruitment-api.test.ts": {
            "strict inventory helper test": "lists strict human-managed organization and job summaries",
            "invalid summary counterexample": "fails closed on invalid managed summary fields",
        },
        "apps/web/tests/private-workspace-isolation.test.ts": {
            "private workspace isolation": "remounts employer private state for every authenticated subject",
            "initial-load double-submit guards": "organizationInventoryInitialInFlightRef",
        },
        "apps/web/tests/private-workspace-truth.test.ts": {
            "application non-dispatch boundary": "keeps employer inventories independent, truthful, and separate from application reads",
        },
        "apps/api/tests/test_social_core.py": {
            "tenant and lifecycle coverage": "test_employer_inventory_is_private_tenant_scoped_and_lifecycle_complete",
            "signed cursor coverage": "test_employer_inventory_cursors_are_signed_bound_and_deterministic",
            "protocol exclusion coverage": "test_employer_inventory_discovery_is_human_only_and_not_agent_surface",
        },
    }
    for relative_path, markers in required.items():
        source = (
            main_source
            if relative_path == "apps/api/app/main.py"
            else _read_anchor_source(root, relative_path, errors)
        )
        _require_source_markers(source, relative_path, markers, errors)

    search_source = _read_anchor_source(root, "apps/api/app/services/search.py", errors)
    for forbidden in (
        "EmployerOrganizationSummary",
        "EmployerJobSummary",
        "/v1/employer/",
    ):
        if forbidden in search_source:
            _error(
                errors,
                "repository.employer_inventory.apps/api/app/services/search.py",
                f"must not project private employer inventory marker {forbidden!r}",
            )

    try:
        main_tree = ast.parse(main_source)
        discovery_tree = ast.parse(discovery_source)
        agent_card_tree = ast.parse(agent_card_source)
    except SyntaxError as exc:
        _error(
            errors,
            "repository.employer_inventory.apps/api/app/main.py",
            f"cannot parse API discovery surfaces: {exc}",
        )
        return errors
    regions = {
        "llms_txt": _discovery_function_strings(
            discovery_tree, ("llms_txt",), "employer_inventory_llms", errors
        ),
        "llms_full": _discovery_function_strings(
            discovery_tree,
            ("llms_full_txt",),
            "employer_inventory_llms_full",
            errors,
        ),
        "capabilities": _discovery_function_strings(
            main_tree, ("capabilities",), "employer_inventory_capabilities", errors
        ),
        "agent_card": _discovery_function_strings(
            agent_card_tree, ("agent_card",), "employer_inventory_agent_card", errors
        ),
        "mcp_tools": _discovery_function_strings(
            main_tree, ("mcp_tools",), "employer_inventory_mcp", errors
        ),
        "a2a": _discovery_function_strings(
            main_tree, ("a2a_send_message",), "employer_inventory_a2a", errors
        ),
    }
    route_markers = ("/v1/employer/organizations", "/v1/employer/jobs")
    for name in ("llms_full", "capabilities"):
        for marker in route_markers:
            if not any(marker in value for value in regions[name]):
                _error(
                    errors,
                    f"repository.employer_inventory.discovery.{name}",
                    f"must advertise the human-only route {marker!r}",
                )
    for name in ("llms_txt", "agent_card", "mcp_tools", "a2a"):
        for marker in route_markers:
            if any(marker in value for value in regions[name]):
                _error(
                    errors,
                    f"repository.employer_inventory.discovery.{name}",
                    f"must not advertise private human-only route {marker!r}",
                )
    return errors


def _recruiting_release_gate_errors(
    root: Path, route_inventory: _RouteInventory
) -> list[str]:
    """Bind the default-off recruiting release gate across runtime and discovery."""
    errors: list[str] = []
    config = _read_anchor_source(root, "apps/api/app/config.py", errors)
    main = _read_anchor_source(root, "apps/api/app/main.py", errors)
    protocol_metadata_path = "apps/api/app/routes/protocol_metadata.py"
    protocol_metadata = _read_anchor_source(root, protocol_metadata_path, errors)
    cli = _read_anchor_source(root, "apps/api/app/cli.py", errors)
    discovery = _read_anchor_source(root, "apps/api/app/routes/discovery.py", errors)
    conftest = _read_anchor_source(root, "apps/api/tests/conftest.py", errors)
    config_test = _read_anchor_source(root, "apps/api/tests/test_config.py", errors)
    cli_test = _read_anchor_source(
        root, "apps/api/tests/test_cli_recruiting_evidence.py", errors
    )
    gate_test = _read_anchor_source(
        root, "apps/api/tests/test_recruiting_release_gate.py", errors
    )
    env_example = _read_anchor_source(root, ".env.example", errors)
    compose = _read_anchor_source(root, "compose.yaml", errors)
    deployment = _read_anchor_source(root, "docs/deployment.md", errors)
    trust = _read_anchor_source(root, "docs/trust-safety.md", errors)
    release_matrix = _read_anchor_source(
        root, "docs/platform/release-matrix.md", errors
    )
    web_gate = _read_anchor_source(root, "apps/web/lib/recruiting-release.ts", errors)
    sitemap = _read_anchor_source(root, "apps/web/app/sitemap.ts", errors)
    robots = _read_anchor_source(root, "apps/web/app/robots.ts", errors)
    discover_page = _read_anchor_source(root, "apps/web/app/discover/page.tsx", errors)
    discover_hub = _read_anchor_source(
        root, "apps/web/components/discover-hub.tsx", errors
    )
    landing = _read_anchor_source(root, "apps/web/app/page.tsx", errors)
    public_trust = _read_anchor_source(root, "apps/web/app/trust/page.tsx", errors)
    web_gate_test = _read_anchor_source(
        root, "apps/web/tests/recruiting-release.test.ts", errors
    )
    sitemap_test = _read_anchor_source(root, "apps/web/tests/sitemap.test.ts", errors)
    discover_test = _read_anchor_source(
        root, "apps/web/tests/discover-hub.test.ts", errors
    )
    landing_test = _read_anchor_source(
        root, "apps/web/tests/agent-first-landing.test.ts", errors
    )
    public_trust_test = _read_anchor_source(
        root, "apps/web/tests/public-trust.test.ts", errors
    )
    browser_test = _read_anchor_source(
        root, "apps/web/e2e/public-release.spec.ts", errors
    )

    _require_source_markers(
        config,
        "apps/api/app/config.py#recruiting-release-gate",
        {"literal false default": "recruiting_enabled: bool = False"},
        errors,
    )
    _require_source_markers(
        cli,
        "apps/api/app/cli.py#recruiting-release-gate",
        {
            "active-only actions": 'args.action in {"activate", "restore"}',
            "pre-database gate": "not settings.recruiting_enabled",
            "stable refusal": 'print("recruiting release is disabled", file=sys.stderr)',
        },
        errors,
    )
    _require_source_markers(
        main,
        "apps/api/app/main.py#recruiting-release-gate",
        {
            "release helper": "def require_recruiting_release() -> None:",
            "default-off check": "if not settings.recruiting_enabled:",
            "release-gate capability": '"verified_recruitment": settings.recruiting_enabled',
            "hidden capability groups": '("employer_inventory", "organizations", "jobs", "applications")',
            "hidden organization grant matrix": 'capability_payload["agent_grants"]["resource_scope_matrix"].pop("organization")',
            "conditional OpenAPI metadata": "if settings.recruiting_enabled",
        },
        errors,
    )
    _require_source_markers(
        main,
        "apps/api/app/main.py#protocol-metadata-router",
        {
            "router import": "from app.routes.protocol_metadata import router as protocol_metadata_router",
            "router inclusion": "app.include_router(protocol_metadata_router)",
        },
        errors,
    )
    for function_name, markers in (
        (
            "list_organizations",
            {
                "empty list before query": "return OrganizationListResponse(organizations=[], next_cursor=None)"
            },
        ),
        (
            "search_jobs",
            {
                "empty list before filters": "return JobListResponse(jobs=[], next_cursor=None)"
            },
        ),
        (
            "update_organization",
            {
                "public visibility gate": 'if body.visibility == "public":',
                "gate before organization lookup": "require_recruiting_release()",
            },
        ),
        (
            "decide_recruiting_verification",
            {
                "active transition gate": 'if action in {"activate", "restore"}:',
                "gate before idempotency": "require_recruiting_release()",
            },
        ),
        (
            "change_job_lifecycle",
            {
                "publication gate": 'if action == "publish":',
                "gate before organization lookup": "require_recruiting_release()",
            },
        ),
        (
            "submit_application",
            {
                "gate before application authority and lookup": "require_recruiting_release()"
            },
        ),
        (
            "decide_application",
            {
                "positive acceptance gate": 'if action == "accept":',
                "gate before idempotency and lookup": "require_recruiting_release()",
            },
        ),
        (
            "protected_resource_metadata",
            {
                "conditional recruiting scopes": "request.app.state.settings.recruiting_enabled",
                "organization read scope": '"organizations:read"',
                "job write scope": '"jobs:write"',
            },
        ),
    ):
        source = (
            protocol_metadata
            if function_name == "protected_resource_metadata"
            else main
        )
        source_path = (
            protocol_metadata_path
            if function_name == "protected_resource_metadata"
            else "apps/api/app/main.py"
        )
        _require_source_markers(
            _function_source(source, function_name, source_path, errors),
            f"{source_path}#{function_name}",
            markers,
            errors,
        )
    for function_name in ("can_read_organization", "can_read_job"):
        _require_source_markers(
            _function_source(main, function_name, "apps/api/app/main.py", errors),
            f"apps/api/app/main.py#{function_name}",
            {"public release condition": "settings.recruiting_enabled"},
            errors,
        )

    recruiting_routes = (
        "POST /v1/organizations",
        "GET /v1/organizations",
        "GET /v1/employer/organizations",
        "GET /v1/employer/jobs",
        "GET /v1/organization-membership-invitations",
        "GET /v1/organizations/{organization_slug}",
        "PUT /v1/organizations/{organization_slug}",
        "GET /v1/organizations/{organization_slug}/members",
        "POST /v1/organizations/{organization_slug}/admins",
        "POST /v1/organizations/{organization_slug}/memberships/{membership_id}/accept",
        "DELETE /v1/organizations/{organization_slug}/memberships/{membership_id}",
        "GET /v1/organizations/{organization_slug}/verification-status",
        "POST /v1/organizations/{organization_slug}/verification-submissions",
        "POST /v1/organizations/{organization_slug}/jobs",
        "GET /v1/organizations/{organization_slug}/jobs/{job_slug}",
        "PUT /v1/organizations/{organization_slug}/jobs/{job_slug}",
        "POST /v1/organizations/{organization_slug}/jobs/{job_slug}/lifecycle/{action}",
        "GET /v1/jobs",
        "POST /v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
        "GET /v1/applications",
        "GET /v1/applications/{application_id}",
        "POST /v1/applications/{application_id}/withdraw",
        "GET /v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
        "GET /v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}",
        "POST /v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/{action}",
        "GET /v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/snapshot",
        "GET /v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/snapshot.md",
    )
    for route in recruiting_routes:
        if not _route_is_hidden_from_openapi(route, route_inventory):
            _error(
                errors,
                "apps/api/app/main.py#recruiting-release-gate",
                f"default-off recruiting route must use the dynamic OpenAPI gate: {route!r}",
            )

    _require_source_markers(
        discovery,
        "apps/api/app/routes/discovery.py#recruiting-release-gate",
        {
            "runtime setting": "request.app.state.settings.recruiting_enabled",
            "concise disabled disclosure": "disabled by the deployment release gate",
            "complete disabled disclosure": "disabled by default in this deployment",
            "route inference refusal": "Do not infer or attempt remembered recruiting routes.",
            "filtered grant matrix": 'if recruiting_enabled or resource_type != "organization"',
            "filtered grant prose": "grant_resource_options = (",
        },
        errors,
    )
    _require_source_markers(
        conftest,
        "apps/api/tests/conftest.py#recruiting-release-gate",
        {"legacy-suite explicit opt-in": "recruiting_enabled=True"},
        errors,
    )
    _require_source_markers(
        config_test,
        "apps/api/tests/test_config.py#recruiting-release-gate",
        {
            "default false test": "test_recruiting_release_defaults_off_and_requires_explicit_environment_opt_in",
            "explicit environment opt-in": 'monkeypatch.setenv("CONNECTMD_RECRUITING_ENABLED", "true")',
        },
        errors,
    )
    _require_source_markers(
        cli_test,
        "apps/api/tests/test_cli_recruiting_evidence.py#recruiting-release-gate",
        {
            "pre-database active refusal": "test_cli_active_transitions_fail_before_database_when_recruiting_is_disabled",
            "defensive CLI availability": "test_cli_defensive_transition_is_not_blocked_by_recruiting_release_gate",
        },
        errors,
    )
    _require_source_markers(
        gate_test,
        "apps/api/tests/test_recruiting_release_gate.py",
        {
            "discovery counterexample": "test_default_off_discovery_hides_every_recruiting_contract",
            "runtime counterexample": "test_default_off_gate_hides_public_state_and_blocks_release_mutations_first",
            "real defensive transition": '"disabled-release-defensive-suspend"',
            "zero-write gate assertion": "assert await idempotency_count(app) == receipts_before",
            "private direct grant": 'resource_type="organization"',
            "false OAuth scope assertion": '"/.well-known/oauth-protected-resource/mcp"',
            "true capability assertion": 'enabled_capabilities["release_gates"]',
            "positive acceptance refusal": "applications/not-present/accept",
        },
        errors,
    )
    _require_source_markers(
        env_example,
        ".env.example#recruiting-release-gate",
        {"literal false environment default": "CONNECTMD_RECRUITING_ENABLED=false"},
        errors,
    )
    compose_marker = (
        "CONNECTMD_RECRUITING_ENABLED: ${CONNECTMD_RECRUITING_ENABLED:-false}"
    )
    if compose.count(compose_marker) != 2:
        _error(
            errors,
            "compose.yaml#recruiting-release-gate",
            "the API and frontend services must each receive the default-false recruiting gate",
        )
    _require_source_markers(
        web_gate,
        "apps/web/lib/recruiting-release.ts#recruiting-release-gate",
        {
            "server-only boundary": 'import "server-only";',
            "shared exact flag": 'process.env.CONNECTMD_RECRUITING_ENABLED === "true"',
        },
        errors,
    )
    _require_source_markers(
        sitemap,
        "apps/web/app/sitemap.ts#recruiting-release-gate",
        {
            "runtime evaluation": 'export const dynamic = "force-dynamic";',
            "conditional base inventory": "if (recruitingReleaseEnabled()) {",
            "default-off category": "if (!recruitingReleaseEnabled()) return [];",
        },
        errors,
    )
    _require_source_markers(
        robots,
        "apps/web/app/robots.ts#recruiting-release-gate",
        {
            "runtime evaluation": 'export const dynamic = "force-dynamic";',
            "shared gate": "const recruitingEnabled = recruitingReleaseEnabled();",
            "default-off crawler exclusion": '...(recruitingEnabled ? [] : ["/organizations", "/jobs"]),',
        },
        errors,
    )
    _require_source_markers(
        discover_page,
        "apps/web/app/discover/page.tsx#recruiting-release-gate",
        {
            "runtime evaluation": 'export const dynamic = "force-dynamic";',
            "conditional organization read": "recruitingEnabled ? listPublicOrganizations() : Promise.resolve(null)",
            "conditional job read": "recruitingEnabled ? listPublicJobs(emptyJobSearchFilters) : Promise.resolve(null)",
            "availability passed to client": "recruitingEnabled={recruitingEnabled}",
        },
        errors,
    )
    _require_source_markers(
        discover_hub,
        "apps/web/components/discover-hub.tsx#recruiting-release-gate",
        {
            "explicit availability prop": "recruitingEnabled: boolean;",
            "organization link gate": '{recruitingEnabled && <PublicRailLink href="/organizations">Organizations</PublicRailLink>}',
            "recruiting card gate": "{recruitingEnabled && (",
        },
        errors,
    )
    for path, source_text in (
        ("apps/web/app/page.tsx", landing),
        ("apps/web/app/trust/page.tsx", public_trust),
    ):
        _require_source_markers(
            source_text,
            f"{path}#recruiting-release-gate",
            {
                "runtime evaluation": 'export const dynamic = "force-dynamic";',
                "shared gate": "const recruitingEnabled = recruitingReleaseEnabled();",
                "enabled public organizations": 'href="/organizations"',
                "enabled public jobs": 'href="/jobs"',
                "disabled release truth": "Public recruiting and applicant intake are disabled until the release gate is explicitly enabled",
            },
            errors,
        )
    for path in (
        "apps/web/app/organizations/page.tsx",
        "apps/web/app/organizations/[slug]/page.tsx",
        "apps/web/app/jobs/page.tsx",
        "apps/web/app/jobs/[organizationSlug]/[jobSlug]/page.tsx",
    ):
        source_text = _read_anchor_source(root, path, errors)
        _require_source_markers(
            source_text,
            f"{path}#recruiting-release-gate",
            {
                "runtime evaluation": 'export const dynamic = "force-dynamic";',
                "opaque disabled response": "if (!recruitingReleaseEnabled()) notFound();",
            },
            errors,
        )
        if source_text.count("if (!recruitingReleaseEnabled()) notFound();") != 2:
            _error(
                errors,
                f"{path}#recruiting-release-gate",
                "page and metadata paths must both fail before recruiting reads",
            )
    for path, source_text, markers in (
        (
            "apps/web/tests/recruiting-release.test.ts",
            web_gate_test,
            {
                "exact true-only test": "is false by default and accepts only the exact explicit true value",
                "zero-read disabled test": "stops list/detail pages and metadata before every recruiting API read while disabled",
                "enabled parity test": "preserves the existing list/detail reads when the shared gate is explicitly true",
            },
        ),
        (
            "apps/web/tests/sitemap.test.ts",
            sitemap_test,
            {
                "default-off zero-read test": "keeps category 1 stable but performs zero recruitment fetches by default",
                "enabled sitemap test": "includes only service-gated organizations and published jobs in category 1",
            },
        ),
        (
            "apps/web/tests/discover-hub.test.ts",
            discover_test,
            {
                "disabled discovery test": "omits recruiting cards, links, records, and availability errors while disabled",
                "enabled discovery test": "keeps gated organization and published job paths public only when explicitly enabled",
            },
        ),
        (
            "apps/web/tests/agent-first-landing.test.ts",
            landing_test,
            {
                "disabled landing test": "explains .md for novices without advertising unavailable private routes",
                "enabled landing test": "shows public recruiting without inventing an unavailable private workspace",
            },
        ),
        (
            "apps/web/tests/public-trust.test.ts",
            public_trust_test,
            {
                "disabled trust test": "links only to truthful current public contracts when private workspaces are unavailable",
                "enabled trust test": "describes public recruiting without advertising a missing private workspace",
            },
        ),
        (
            "apps/web/e2e/public-release.spec.ts",
            browser_test,
            {
                "disabled crawler inventory": '"/sitemap/1.xml": [],',
                "disabled organization route": '"/organizations"',
                "disabled job route": '"/jobs"',
            },
        ),
    ):
        _require_source_markers(
            source_text,
            f"{path}#recruiting-release-gate",
            markers,
            errors,
        )
    for path, source_text in (
        ("docs/deployment.md", deployment),
        ("docs/trust-safety.md", trust),
        ("docs/platform/release-matrix.md", release_matrix),
    ):
        _require_source_markers(
            source_text,
            f"{path}#recruiting-release-gate",
            {"default-off release truth": "CONNECTMD_RECRUITING_ENABLED"},
            errors,
        )
    return errors


def _organization_membership_durability_errors(root: Path) -> list[str]:
    """Bind private membership writes to their durable, human-only contract."""

    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    header_markers = {
        "human-only OpenAPI extension": '"x-connectmd-human-only": True',
        "Idempotency-Key name": '"name": "Idempotency-Key"',
        "Idempotency-Key header location": '"in": "header"',
        "required Idempotency-Key header": '"required": True',
        "minimum Idempotency-Key bound": '"minLength": 1',
        "maximum Idempotency-Key bound": '"maxLength": 128',
        "visible-ASCII Idempotency-Key pattern": "_IDEMPOTENCY_KEY_PATTERN",
    }
    for route, label in (
        ("POST /v1/organizations/{organization_slug}/admins", "membership invitation"),
        (
            "POST /v1/organizations/{organization_slug}/memberships/{membership_id}/accept",
            "membership acceptance",
        ),
        (
            "DELETE /v1/organizations/{organization_slug}/memberships/{membership_id}",
            "membership removal",
        ),
    ):
        decorator = _route_decorator(route, main_source)
        if decorator is None:
            _error(
                errors,
                f"repository.membership.{label}",
                f"cannot locate implemented route {route!r}",
            )
            continue
        _require_source_markers(
            decorator,
            f"{main_path}#{label}-route",
            header_markers,
            errors,
        )

    def require_order(
        source: str, function_name: str, anchors: list[tuple[str, str]]
    ) -> None:
        position = -1
        for label, marker in anchors:
            position = source.find(marker, position + 1)
            if position < 0:
                _error(
                    errors,
                    f"repository.membership.{main_path}#{function_name}",
                    f"is missing {label} anchor {marker!r}",
                )
                return

    invite = _function_source(main_source, "add_organization_admin", main_path, errors)
    _require_source_markers(
        invite,
        f"{main_path}#add_organization_admin",
        {
            "Clerk invitation authority": 'if principal.method != "clerk_jwt":',
            "owner-only invitation authority": "owner_only=True",
            "required invitation key": "key = idempotency_key(request, required=True)",
            "organization-bound invitation operation": 'operation = f"POST:/v1/organizations/{organization.id}/admins"',
            "invitation body fingerprint": "body.model_dump_json()",
            "membership invitation event": 'event_type="organization.member_invited"',
            "generation-bound invitation receipt": 'resource_id=f"{member.id}:{_organization_membership_generation_digest(member)}"',
        },
        errors,
    )
    require_order(
        invite,
        "add_organization_admin",
        [
            ("Clerk authority", 'if principal.method != "clerk_jwt":'),
            (
                "organization lock",
                "organization_by_slug(session, organization_slug, for_update=True)",
            ),
            ("owner authority", "await assert_organization_authority("),
            ("idempotency key", "key = idempotency_key(request, required=True)"),
            ("idempotency replay", "replay = await idempotency_replay"),
            ("public profile lookup", "await public_profile_by_handle("),
        ],
    )

    accept = _function_source(
        main_source, "accept_organization_membership", main_path, errors
    )
    _require_source_markers(
        accept,
        f"{main_path}#accept_organization_membership",
        {
            "Clerk acceptance authority": 'if principal.method != "clerk_jwt":',
            "required acceptance key": "key = idempotency_key(request, required=True)",
            "acceptance operation": 'operation = f"POST:/v1/organizations/{organization_slug}/memberships/{membership_id}/accept"',
            "recipient-bound membership": "OrganizationMembership.member_owner_id == principal.subject",
            "membership lock": ".with_for_update()",
            "acceptance state guard": 'if member.status != "invited":',
            "membership acceptance event": 'event_type="organization.membership_accepted"',
            "generation-bound acceptance receipt": "generation_digest = _organization_membership_generation_digest(member)",
            "acceptance receipt resource": "resource_id=generation_digest",
        },
        errors,
    )
    require_order(
        accept,
        "accept_organization_membership",
        [
            ("Clerk authority", 'if principal.method != "clerk_jwt":'),
            ("idempotency key", "key = idempotency_key(request, required=True)"),
            ("first replay", "replay = await idempotency_replay"),
            (
                "organization lock",
                "organization_by_slug(session, organization_slug, for_update=True)",
            ),
            ("second replay", "replay = await idempotency_replay"),
            ("membership lock", "select(OrganizationMembership)"),
        ],
    )
    if (
        accept.find(".with_for_update()", accept.find("select(OrganizationMembership)"))
        < 0
    ):
        _error(
            errors,
            f"repository.membership.{main_path}#accept_organization_membership",
            "must lock the membership after the organization lock",
        )

    remove = _function_source(
        main_source, "remove_organization_admin", main_path, errors
    )
    _require_source_markers(
        remove,
        f"{main_path}#remove_organization_admin",
        {
            "Clerk removal authority": 'if principal.method != "clerk_jwt":',
            "required removal key": "key = idempotency_key(request, required=True)",
            "removal operation": 'operation = f"DELETE:/v1/organizations/{organization_slug}/memberships/{membership_id}"',
            "owner-only removal authority": "owner_only=True",
            "membership removal event": 'event_type="organization.member_removed"',
            "empty removal receipt": 'status_code=204,\n            body="",\n            headers={}',
            "membership removal receipt": 'resource_type="organization_membership"',
            "exact initial empty removal": "return Response(status_code=204)",
        },
        errors,
    )
    require_order(
        remove,
        "remove_organization_admin",
        [
            ("Clerk authority", 'if principal.method != "clerk_jwt":'),
            ("idempotency key", "key = idempotency_key(request, required=True)"),
            (
                "organization lock",
                "organization_by_slug(session, organization_slug, for_update=True)",
            ),
            ("current owner authority", "await assert_organization_authority("),
            ("idempotency replay", "replay = await idempotency_replay"),
            ("membership lock", "select(OrganizationMembership)"),
            ("membership deletion", "await session.delete(member)"),
        ],
    )
    if (
        remove.find(".with_for_update()", remove.find("select(OrganizationMembership)"))
        < 0
    ):
        _error(
            errors,
            f"repository.membership.{main_path}#remove_organization_admin",
            "must lock the membership after current-owner authorization",
        )

    replay = _function_source(main_source, "idempotency_replay", main_path, errors)
    _require_source_markers(
        replay,
        f"{main_path}#idempotency_replay",
        {
            "membership invitation classification": "is_membership_invite = (",
            "membership acceptance classification": "is_membership_accept = (",
            "membership removal classification": "is_membership_remove = (",
            "membership resource-type corruption gate": 'record.resource_type != "organization_membership"',
            "generation digest receipt validation": "re.fullmatch(_SHA256_HEX_PATTERN, resource_parts[1])",
            "generation-bound invitation replay": "_organization_membership_generation_digest(membership)",
            "original invitation request binding": 'request_payload.get("member_profile_handle")',
            "original invitation role binding": 'request_payload.get("role", "member") != membership.role',
            "generation-bound acceptance replay": "receipt_digest,\n                    _organization_membership_generation_digest(membership)",
            "active acceptance replay state": 'OrganizationMembership.status == "active"',
            "exact removal receipt": "record.response_status != 204",
            "reincarnated removal gate": "reappeared = await session.get(OrganizationMembership, parts[2])",
            "exact empty removal replay": 'return Response(status_code=204, headers={"Idempotency-Replayed": "true"})',
        },
        errors,
    )

    social_test_path = "apps/api/tests/test_social_core.py"
    social_tests = _read_anchor_source(root, social_test_path, errors)
    _require_source_markers(
        social_tests,
        social_test_path,
        {
            "membership OpenAPI coverage": "test_membership_reads_are_human_only_in_openapi",
            "membership lock-order coverage": "test_membership_authority_and_lock_order_are_fail_closed",
            "invitation exact replay coverage": "test_membership_invitation_replay_is_exact_and_fail_closed",
            "original invitation request binding coverage": "omitted_default_role.content == first.content",
            "same-organization substitution coverage": "swapped_resource_id",
            "former-owner replay coverage": "test_membership_remove_replay_requires_current_owner",
            "accept/remove receipt coverage": "test_membership_accept_remove_receipts_are_atomic_and_collision_safe",
            "generation reincarnation coverage": "test_membership_replay_corruption_and_generation_reincarnation_fail_closed",
            "reappearing removal coverage": "reappeared_remove.status_code == 503",
            "same-key concurrency coverage": "test_membership_same_key_accept_and_remove_concurrency_replays_once",
        },
        errors,
    )
    fixture_path = "apps/api/tests/conftest.py"
    fixture = _read_anchor_source(root, fixture_path, errors)
    _require_source_markers(
        fixture,
        fixture_path,
        {"SQLite-only repository test fixture": "sqlite+aiosqlite:///"},
        errors,
    )

    mcp = _function_source(main_source, "mcp_tools", main_path, errors)
    if "membership" in mcp.lower():
        _error(
            errors,
            f"repository.membership.{main_path}#mcp_tools",
            "MCP must not expose organization membership management",
        )
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    if "membership" in a2a.lower():
        _error(
            errors,
            f"repository.membership.{main_path}#a2a_send_message",
            "A2A must not expose organization membership management",
        )
    return errors


def _application_transition_durability_errors(root: Path) -> list[str]:
    """Bind application withdrawal and employer transitions to durable replay."""

    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    protocol_metadata_path = "apps/api/app/routes/protocol_metadata.py"
    protocol_metadata_source = _read_anchor_source(root, protocol_metadata_path, errors)
    header_markers = {
        "human-only OpenAPI extension": '"x-connectmd-human-only": True',
        "Idempotency-Key name": '"name": "Idempotency-Key"',
        "Idempotency-Key header location": '"in": "header"',
        "required Idempotency-Key header": '"required": True',
        "minimum Idempotency-Key bound": '"minLength": 1',
        "maximum Idempotency-Key bound": '"maxLength": 128',
        "visible-ASCII Idempotency-Key pattern": "_IDEMPOTENCY_KEY_PATTERN",
    }
    for route, label in (
        ("POST /v1/applications/{application_id}/withdraw", "application withdrawal"),
        (
            "POST /v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/{action}",
            "employer application decision",
        ),
    ):
        decorator = _route_decorator(route, main_source)
        if decorator is None:
            _error(
                errors,
                f"repository.application_transition.{label}",
                f"cannot locate implemented route {route!r}",
            )
            continue
        _require_source_markers(
            decorator,
            f"{main_path}#{label}-route",
            header_markers,
            errors,
        )

    def require_order(
        source: str, function_name: str, anchors: list[tuple[str, str]]
    ) -> None:
        position = -1
        for label, marker in anchors:
            position = source.find(marker, position + 1)
            if position < 0:
                _error(
                    errors,
                    f"repository.application_transition.{main_path}#{function_name}",
                    f"is missing {label} anchor {marker!r}",
                )
                return

    withdrawal = _function_source(
        main_source, "withdraw_application", main_path, errors
    )
    _require_source_markers(
        withdrawal,
        f"{main_path}#withdraw_application",
        {
            "Clerk-human application gate": "require_application_human(principal)",
            "required withdrawal key": "key = idempotency_key(request, required=True)",
            "withdrawal operation": 'operation = f"POST:/v1/applications/{application_id}/withdraw"',
            "empty withdrawal fingerprint": 'fingerprint = _request_fingerprint(operation, "")',
            "applicant pre-lock transition context": '"mode": "applicant", "application_id": application_id, "action": "withdraw"',
            "applicant-bound application probe": "Application.applicant_owner_id == principal.subject",
            "organization lock": "select(Organization)",
            "job lock": "select(Job)",
            "applicant-bound application lock": "Application.job_id == job.id,\n                Application.applicant_owner_id == principal.subject",
            "canonical withdrawal transition context": '"organization_id": organization.id',
            "withdrawn status guard": 'if row.status not in {"submitted", "under_review"}:',
            "withdrawal retention guard": "if retention_expired(row.retention_expires_at):",
            "withdrawal resource digest": '_application_transition_resource_id(\n                row, job, organization, "withdraw", response_body',
            "safe withdrawal event": 'payload=json.dumps({"status": row.status}, sort_keys=True)',
            "empty withdrawal receipt": 'status_code=200,\n                body="",\n                headers={}',
            "withdrawal receipt type": 'resource_type="application_transition"',
            "initial exact withdrawal response": 'return Response(content=response_body, status_code=200, media_type="application/json")',
        },
        errors,
    )
    withdrawal_organization_lock = withdrawal.find("select(Organization)")
    if withdrawal_organization_lock < 0:
        _error(
            errors,
            f"repository.application_transition.{main_path}#withdraw_application",
            "is missing the canonical organization lock after the applicant probe",
        )
    else:
        _require_source_markers(
            withdrawal[:withdrawal_organization_lock],
            f"{main_path}#withdraw_application-prelock",
            {
                "applicant-bound application probe": "Application.applicant_owner_id == principal.subject",
                "applicant pre-lock transition context": '"mode": "applicant", "application_id": application_id, "action": "withdraw"',
            },
            errors,
        )
    require_order(
        withdrawal,
        "withdraw_application",
        [
            ("Clerk-human application gate", "require_application_human(principal)"),
            ("idempotency key", "key = idempotency_key(request, required=True)"),
            ("pre-lock replay", "replay = await idempotency_replay"),
            ("applicant probe", "Application.applicant_owner_id == principal.subject"),
            ("organization lock", "select(Organization)"),
            ("job lock", "select(Job)"),
            ("canonical transition context", '"organization_id": organization.id'),
            ("post-lock replay", "replay = await idempotency_replay"),
        ],
    )
    withdrawal_job_lock = withdrawal.find("select(Job)")
    withdrawal_application_lock = withdrawal.find(
        "select(Application)", withdrawal_job_lock
    )
    if (
        withdrawal_job_lock < 0
        or withdrawal_application_lock < 0
        or withdrawal.find(".with_for_update()", withdrawal_application_lock) < 0
    ):
        _error(
            errors,
            f"repository.application_transition.{main_path}#withdraw_application",
            "must lock organization, then job, then applicant-owned application",
        )

    decision = _function_source(main_source, "decide_application", main_path, errors)
    _require_source_markers(
        decision,
        f"{main_path}#decide_application",
        {
            "Clerk-human application gate": "require_application_human(principal)",
            "decision action allowlist": 'if action not in {"review", "accept", "reject"}:',
            "required decision key": "key = idempotency_key(request, required=True)",
            "decision operation": 'operation = f"POST:/v1/applications/{application_id}/{action}"',
            "route-bound decision fingerprint": '"job_slug": job_slug, "organization_slug": organization_slug',
            "organization decision lock": "organization_by_slug(session, organization_slug, for_update=True)",
            "job decision lock": "job_by_slug(session, organization, job_slug, for_update=True)",
            "live recruiting authority": "await assert_active_employer_application_authority(",
            "employer transition context": '"mode": "employer"',
            "application decision lock": "select(Application)",
            "fresh decision retention guard": "if retention_expired(row.retention_expires_at):",
            "decision status guards": 'if action == "review" and row.status != "submitted":',
            "decision digest": "_application_transition_resource_id(\n                row, job, organization, action, response_body",
            "safe decision event": 'payload=json.dumps({"status": row.status}, sort_keys=True)',
            "empty decision receipt": 'status_code=200,\n                body="",\n                headers={}',
            "decision receipt type": 'resource_type="application_transition"',
            "initial exact decision response": 'return Response(content=response_body, status_code=200, media_type="application/json")',
        },
        errors,
    )
    require_order(
        decision,
        "decide_application",
        [
            ("Clerk-human application gate", "require_application_human(principal)"),
            (
                "decision action allowlist",
                'if action not in {"review", "accept", "reject"}:',
            ),
            ("idempotency key", "key = idempotency_key(request, required=True)"),
            (
                "organization lock",
                "organization_by_slug(session, organization_slug, for_update=True)",
            ),
            (
                "job lock",
                "job_by_slug(session, organization, job_slug, for_update=True)",
            ),
            (
                "live recruiting authority",
                "await assert_active_employer_application_authority(",
            ),
            ("employer replay", "replay = await idempotency_replay"),
            ("application lock", "select(Application)"),
            ("post-lock replay", "replay = await idempotency_replay"),
            (
                "fresh decision retention guard",
                "if retention_expired(row.retention_expires_at):",
            ),
            (
                "decision status guard",
                'if action == "review" and row.status != "submitted":',
            ),
        ],
    )
    application_lock = decision.find("select(Application)")
    if (
        application_lock < 0
        or decision.find(".with_for_update()", application_lock) < 0
    ):
        _error(
            errors,
            f"repository.application_transition.{main_path}#decide_application",
            "must lock the application after organization, job, authority, and replay",
        )

    transition_replay = _function_source(
        main_source, "application_transition_replay", main_path, errors
    )
    _require_source_markers(
        transition_replay,
        f"{main_path}#application_transition_replay",
        {
            "Clerk-human application replay gate": "require_application_human(principal)",
            "transition receipt type guard": 'record.resource_type != "application_transition"',
            "transition receipt status guard": "record.response_status != 200",
            "empty transition body guard": 'record.response_body != ""',
            "empty transition header guard": 'record.response_headers != "{}"',
            "transition resource parser": "_application_transition_resource_parts(record.resource_id)",
            "organization replay lock": "select(Organization)",
            "job replay lock": "select(Job)",
            "employer replay authority": "await assert_active_employer_application_authority(",
            "application replay lock": "select(Application)",
            "applicant replay ownership": "row.applicant_owner_id != principal.subject",
            "retention replay guard": "retention_expired(row.retention_expires_at)",
            "transition status replay guard": "if row.status != expected_status",
            "snapshot hash replay guard": "re.fullmatch(_SHA256_HEX_PATTERN, row.snapshot_sha256)",
            "verified snapshot replay": "read_application_snapshot(request, row)",
            "exact response reconstruction": "response_body = idempotency_replay_json(result)",
            "receipt digest reconstruction": "_application_transition_receipt_digest(\n            row, job, organization, action, response_body",
            "receipt digest comparison": 'compare_digest(parts["digest"], expected_digest)',
            "exact replay header": 'headers={"Idempotency-Replayed": "true"}',
        },
        errors,
    )
    require_order(
        transition_replay,
        "application_transition_replay",
        [
            ("Clerk-human application gate", "require_application_human(principal)"),
            (
                "transition receipt type guard",
                'record.resource_type != "application_transition"',
            ),
            ("organization lock", "select(Organization)"),
            ("job lock", "select(Job)"),
            (
                "employer authority",
                "await assert_active_employer_application_authority(",
            ),
            ("application lock", "select(Application)"),
        ],
    )

    digest = _read_anchor_source(root, main_path, errors)
    digest_start = digest.find("def _application_transition_receipt_digest(")
    digest_end = digest.find("def _application_transition_resource_id(", digest_start)
    digest_source = (
        "" if digest_start < 0 or digest_end < 0 else digest[digest_start:digest_end]
    )
    _require_source_markers(
        digest_source,
        f"{main_path}#application-transition-digest",
        {
            "hashed applicant owner": '"applicant_owner_digest": sha256(row.applicant_owner_id.encode()).hexdigest()',
            "hashed decision actor": '"decision_actor_digest": sha256((row.decision_actor_id or "").encode()).hexdigest()',
            "exact response fact": '"response_body": response_body',
            "snapshot document fact": '"snapshot_document_id": row.snapshot_document_id',
            "snapshot digest fact": '"snapshot_sha256": row.snapshot_sha256',
            "hashed snapshot path": '"snapshot_storage_path_digest": sha256(',
            "retention fact": '"retention_expires_at": _application_transition_datetime(row.retention_expires_at)',
        },
        errors,
    )

    replay = _function_source(main_source, "idempotency_replay", main_path, errors)
    _require_source_markers(
        replay,
        f"{main_path}#idempotency_replay",
        {
            "transition-only idempotency dispatch": 'if operation.startswith("POST:/v1/applications/"):',
            "transition dispatch type guard": 'record.resource_type != "application_transition"',
            "transition context guard": "application_context is None",
            "transition replay helper": "return await application_transition_replay(",
        },
        errors,
    )
    submission = _function_source(main_source, "submit_application", main_path, errors)
    _require_source_markers(
        submission,
        f"{main_path}#submit_application",
        {
            "Clerk-human application gate": "require_application_human(principal)",
            "submission operation": 'operation = f"POST:/v1/organizations/{organization_slug}/jobs/{job_slug}/applications"',
            "submission success receipt": "status_code=201",
            "submission response receipt": "body=result.model_dump_json()",
            "submission receipt type": 'resource_type="application"',
        },
        errors,
    )
    require_order(
        submission,
        "submit_application",
        [
            ("Clerk-human application gate", "require_application_human(principal)"),
            ("idempotency key", "key = idempotency_key(request, required=True)"),
            ("organization lookup", "organization_by_slug(session, organization_slug)"),
        ],
    )
    if "application_context=" in submission:
        _error(
            errors,
            f"repository.application_transition.{main_path}#submit_application",
            "application submission must retain its non-transition receipt contract",
        )
    submission_receipt_marker = (
        "replay_after_commit = await commit_artifact_transaction("
    )
    submission_receipt_positions = [
        match.start()
        for match in re.finditer(re.escape(submission_receipt_marker), submission)
    ]
    if len(submission_receipt_positions) != 1:
        _error(
            errors,
            f"repository.application_transition.{main_path}#submit_application-receipt",
            "must contain exactly one authoritative commit_artifact_transaction receipt call",
        )
    submission_receipt_start = (
        submission_receipt_positions[0] if submission_receipt_positions else -1
    )
    _require_source_markers(
        "" if submission_receipt_start < 0 else submission[submission_receipt_start:],
        f"{main_path}#submit_application-receipt",
        {
            "submission success receipt": "status_code=201",
            "submission response receipt": "body=result.model_dump_json()",
            "submission receipt type": 'resource_type="application"',
            "submission receipt resource": "resource_id=row.id",
        },
        errors,
    )

    test_path = "apps/api/tests/test_application_decision_durability.py"
    tests = _read_anchor_source(root, test_path, errors)
    _require_source_markers(
        tests,
        test_path,
        {
            "application key OpenAPI and protocol exclusion": "test_application_transition_key_openapi_and_protocol_exclusion",
            "all application routes reject non-Clerk credentials": "test_all_application_http_surfaces_reject_non_clerk_before_route_state",
            "generic application authority denial": '"application access requires a signed-in human"',
            "legacy API-key and Agent-Grant denial coverage": 'method="agent_api_key"',
            "replay gate ordering coverage": 'replay.index("require_application_human(principal)")',
            "both transition OpenAPI paths": '"/v1/applications/{application_id}/withdraw",',
            "MCP transition exclusion": 'assert "application_transition" not in tool_names',
            "A2A transition exclusion": 'assert "application decision" not in card_text',
            "transition replay and privacy coverage": "test_application_review_accept_reject_withdraw_replay_once_and_privacy",
            "empty receipt assertion": 'assert all(receipt.response_body == "" for receipt in transition_receipts)',
            "safe event assertion": 'assert all("private application message" not in event.payload for event in events)',
            "collision assertion": "assert cross_path_collision.status_code == 409",
            "deterministic SQLite lock-order coverage": "test_application_transition_lock_order_is_explicit_but_sqlite_is_not_a_race_proof",
            "SQLite lock limitation": "SQLite does not provide PostgreSQL lock evidence.",
            "competing transition coverage": "test_application_competing_withdrawal_and_employer_decision_serialize_semantics",
            "competing transition assertion": "assert employer_loser.status_code == 409",
            "fresh expired decision coverage": "test_fresh_employer_decisions_fail_closed_after_application_retention_expiry",
            "all fresh employer actions": 'for action in ("review", "accept", "reject")',
            "expired decision opaque boundary": "assert response.status_code == 404",
            "expired decision event absence": "assert decision_events == []",
            "row-parent-status-retention-snapshot corruption": "test_application_transition_replays_fail_closed_for_row_parent_status_retention_and_snapshot_corruption",
            "relationship corruption": 'substituted_row.job_id = replacement_job.json()["id"]',
            "status corruption": 'status_row.status = "accepted"',
            "retention corruption": "expired_row.retention_expires_at = datetime.now(UTC) - timedelta(seconds=1)",
            "snapshot corruption": 'snapshot_row.snapshot_sha256 = "0" * 64',
            "deletion corruption": "await session.delete(deleted_row)",
            "corruption 503 assertion": "assert failed_replay.status_code == 503",
            "current employer authority replay": "test_application_employer_replay_requires_current_recruiting_authority",
            "authority-loss result": "assert replay_after_authority_loss.status_code == 404",
            "owner and receipt corruption": "test_application_transition_owner_snapshot_and_receipt_corruption_fail_closed",
            "applicant-owner corruption result": "assert applicant_corruption.status_code == 503",
        },
        errors,
    )
    live_test_path = "apps/api/tests/test_live_stack.py"
    live_tests = _read_anchor_source(root, live_test_path, errors)
    _require_source_markers(
        live_tests,
        live_test_path,
        {
            "forced PostgreSQL withdrawal-acceptance race": "test_live_postgres_application_withdraw_accept_race_has_one_terminal_effect",
            "shared organization-row lock gate": "select(Organization).where(Organization.id == organization_id).with_for_update()",
            "both transition lock waiters": "_wait_for_application_transition_lock_waiters(",
            "one terminal response": "assert {withdrawn.status_code, accepted.status_code} == {200, 409}",
            "one transition receipt": "assert len(receipts) == 1",
            "two owner-scoped terminal events": "assert len(events) == 2",
            "acceptance-only notification": 'if winning_status == "accepted":',
            "withdrawn employer-detail boundary": 'if winning_status == "withdrawn":',
        },
        errors,
    )

    application_gate = _function_source(
        main_source, "require_application_human", main_path, errors
    )
    _require_source_markers(
        application_gate,
        f"{main_path}#require_application_human",
        {
            "Clerk-only method check": 'principal.method != "clerk_jwt"',
            "generic private denial": 'detail="application access requires a signed-in human"',
        },
        errors,
    )
    for function_name in (
        "list_my_applications",
        "list_job_applications",
        "get_my_application_detail",
        "get_job_application_detail",
        "employer_application_snapshot",
    ):
        application_read = _function_source(
            main_source, function_name, main_path, errors
        )
        _require_source_markers(
            application_read,
            f"{main_path}#{function_name}",
            {"Clerk-human application gate": "require_application_human(principal)"},
            errors,
        )

    private_resource_types_start = main_source.find(
        "_NON_HUMAN_CHANGE_FEED_EXCLUDED_RESOURCE_TYPES ="
    )
    private_resource_types_end = main_source.find(
        "DocumentKind =", private_resource_types_start
    )
    private_resource_types = (
        ""
        if private_resource_types_start < 0 or private_resource_types_end < 0
        else main_source[private_resource_types_start:private_resource_types_end]
    )
    _require_source_markers(
        private_resource_types,
        f"{main_path}#private-change-resource-types",
        {
            "application event exclusion": '"application"',
            "verification event exclusion": '"organization_verification"',
        },
        errors,
    )
    for function_name in ("changes", "mcp"):
        change_reader = _function_source(main_source, function_name, main_path, errors)
        private_resource_exclusion = (
            "ChangeEvent.resource_type.not_in(_NON_HUMAN_CHANGE_FEED_EXCLUDED_RESOURCE_TYPES)"
            if function_name == "changes"
            else (
                "ChangeEvent.resource_type.not_in(\n"
                "                            _NON_HUMAN_CHANGE_FEED_EXCLUDED_RESOURCE_TYPES\n"
                "                        )"
            )
        )
        _require_source_markers(
            change_reader,
            f"{main_path}#{function_name}-private-application-events",
            {
                "non-Clerk boundary": 'principal.method != "clerk_jwt"',
                "private resource exclusion": private_resource_exclusion,
            },
            errors,
        )

    protected_metadata = _function_source(
        protocol_metadata_source,
        "protected_resource_metadata",
        protocol_metadata_path,
        errors,
    )
    for retired_scope in ('"applications:read"', '"applications:write"'):
        if (
            retired_scope in main_source
            or retired_scope in protocol_metadata_source
            or retired_scope in protected_metadata
        ):
            _error(
                errors,
                f"repository.application_transition.{protocol_metadata_path}#retired-scopes",
                f"retired application scope {retired_scope} must not remain advertised or enforced",
            )

    schemas_path = root / "apps/api/app/schemas.py"
    if schemas_path.is_file():
        schemas = schemas_path.read_text(encoding="utf-8")
        for retired_scope in ('"applications:read"', '"applications:write"'):
            if retired_scope in schemas:
                _error(
                    errors,
                    "repository.application_transition.apps/api/app/schemas.py#AgentScope",
                    f"retired application scope {retired_scope} must not be issuable",
                )

    protocol_test_path = root / "apps/api/tests/test_protocol_core.py"
    if protocol_test_path.is_file():
        protocol_tests = protocol_test_path.read_text(encoding="utf-8")
        _require_source_markers(
            protocol_tests,
            "apps/api/tests/test_protocol_core.py",
            {
                "scope discovery and change-feed regression": "test_application_authority_is_human_only_in_scopes_discovery_openapi_and_change_feeds",
                "OpenAPI Clerk security assertion": 'assert operation["security"] == [{"ClerkBearerAuth": []}]',
                "REST non-Clerk event filter assertion": 'key_changes.json()["events"]',
                "MCP non-Clerk event filter assertion": 'grant_changes.json()["result"]["structuredContent"]',
            },
            errors,
        )
    snapshot_test_path = "apps/api/tests/test_application_snapshot_atomicity.py"
    snapshot_tests = _read_anchor_source(root, snapshot_test_path, errors)
    _require_source_markers(
        snapshot_tests,
        snapshot_test_path,
        {
            "submission receipt atomicity": "test_application_snapshot_commit_failure_compensates_before_response",
            "submission commit acknowledgement recovery": "assert recovered.status_code == 201",
            "submission record compensation": "select(Application).where(Application.job_id == job.id)",
            "submission receipt compensation": "select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)",
        },
        errors,
    )
    fixture = _read_anchor_source(root, "apps/api/tests/conftest.py", errors)
    _require_source_markers(
        fixture,
        "apps/api/tests/conftest.py",
        {"SQLite-only repository test fixture": "sqlite+aiosqlite:///"},
        errors,
    )

    mcp = _function_source(main_source, "mcp_tools", main_path, errors)
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    for transport, source in (("mcp_tools", mcp), ("a2a_send_message", a2a)):
        if any(
            marker in source
            for marker in (
                "application_transition",
                "decide_application",
                "withdraw_application",
                '"application_decision"',
            )
        ):
            _error(
                errors,
                f"repository.application_transition.{main_path}#{transport}",
                f"{transport} must not expose application transition authority",
            )
    return errors


def _contact_durability_errors(root: Path) -> list[str]:
    return _contact_durability_errors_impl(
        root,
        _error=_error,
        _read_anchor_source=_read_anchor_source,
        _require_source_markers=_require_source_markers,
        _function_source=_function_source,
        _route_decorator=_route_decorator,
    )


def _settings_false_default(source: str, errors: list[str]) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _error(
            errors,
            "repository.lifecycle_defaults.apps/api/app/config.py",
            f"cannot parse settings: {exc}",
        )
        return
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Settings":
            continue
        for statement in node.body:
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id == "account_lifecycle_enabled"
                and isinstance(statement.value, ast.Constant)
                and statement.value.value is False
            ):
                return
    _error(
        errors,
        "repository.lifecycle_defaults.apps/api/app/config.py",
        "Settings.account_lifecycle_enabled must have the literal default False",
    )


def _lifecycle_default_errors(root: Path) -> list[str]:
    errors: list[str] = []
    config = _read_anchor_source(root, "apps/api/app/config.py", errors)
    _settings_false_default(config, errors)
    required = {
        ".env.example": {
            "API default false": "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED=false",
            "UI default false": "NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=false",
        },
        "compose.yaml": {
            "API fail-closed interpolation": "${CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED:-false}",
            "UI fail-closed interpolation": "${NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED:-false}",
            "worker opt-in profile": 'profiles: ["account-lifecycle"]',
        },
        "apps/web/Dockerfile": {
            "build-time default false": "ARG NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=false",
            "explicit builder propagation": "ENV NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=$NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED",
        },
        "apps/web/lib/account-lifecycle-api.ts": {
            "exact opt-in client gate": 'process.env.NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED === "true"',
        },
        "apps/api/tests/test_account_lifecycle.py": {
            "disabled pre-auth denial": "test_disabled_lifecycle_hides_export_before_any_authentication",
            "disabled status denial": "test_disabled_lifecycle_status_returns_404_before_request_parsing_or_database",
        },
    }
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
    compose = _read_anchor_source(root, "compose.yaml", errors)
    if compose.count("${CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED:-false}") < 2:
        _error(
            errors,
            "repository.lifecycle_defaults.compose.yaml",
            "API and lifecycle worker must both inherit the fail-closed lifecycle default",
        )
    if compose.count("${NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED:-false}") < 2:
        _error(
            errors,
            "repository.lifecycle_defaults.compose.yaml",
            "frontend build and runtime must both inherit the fail-closed lifecycle default",
        )
    return errors


def _account_lifecycle_confirmation_surface_errors(
    root: Path, route_inventory: _RouteInventory | None = None
) -> list[str]:
    """Bind the feature-gated lifecycle confirmation and terminal-status proofs.

    These are repository anchors only.  They deliberately do not attest a live
    Clerk verification, worker run, PostgreSQL lock schedule, or VPS outcome.
    """

    errors: list[str] = []
    auth_path = "apps/api/app/auth.py"
    auth = _read_anchor_source(root, auth_path, errors)
    confirmation_claims = _function_source(
        auth, "require_lifecycle_confirmation_claims", auth_path, errors
    )
    _require_source_markers(
        confirmation_claims,
        f"{auth_path}#require_lifecycle_confirmation_claims",
        {
            "Bearer-only route-private verifier": 'scheme.lower() != "bearer" or not credential',
            "API-key and Agent-Grant denial": 'credential.startswith(("cnd_", "cng_"))',
            "dedicated Clerk claims verifier": "verify_lifecycle_confirmation(credential)",
            "impersonation denial": "if claims.is_impersonated:",
        },
        errors,
    )
    if "require_principal" in confirmation_claims or "Principal" in confirmation_claims:
        _error(
            errors,
            f"repository.account_lifecycle.{auth_path}",
            "route-private confirmation verification must not use general Principal access",
        )
    main_path = "apps/api/app/main.py"
    main = _read_anchor_source(root, main_path, errors)
    confirmation_route = "POST /v1/account-deletion-requests/{deletion_id}/confirm"
    if not _route_is_hidden_from_openapi(confirmation_route, route_inventory or main):
        _error(
            errors,
            f"repository.account_lifecycle.{main_path}",
            "confirmation route must remain hidden by the default lifecycle setting",
        )
    lifecycle_gate = _function_source(
        main, "require_lifecycle_confirmation", main_path, errors
    )
    _require_source_markers(
        lifecycle_gate,
        f"{main_path}#require_lifecycle_confirmation",
        {
            "disabled-by-default gate": "Depends(require_lifecycle_enabled)",
            "route-private Clerk dependency": "Depends(require_lifecycle_confirmation_claims)",
        },
        errors,
    )
    confirmation_hmac = _function_source(
        main, "lifecycle_confirmation_hmac", main_path, errors
    )
    _require_source_markers(
        confirmation_hmac,
        f"{main_path}#lifecycle_confirmation_hmac",
        {
            "versioned confirmation action": '"action": "account-delete-confirm.v1"',
            "deletion binding": '"deletion_id": deletion_id',
            "subject-HMAC binding": '"subject_hmac": subject_hmac',
            "caller-key binding": '"idempotency_key": idempotency_key_value',
            "HMAC label": 'lifecycle_hmac(settings, "delete-confirm-key", canonical)',
        },
        errors,
    )
    confirmation = _function_source(
        main, "confirm_account_deletion_request", main_path, errors
    )
    _require_source_markers(
        confirmation,
        f"{main_path}#confirm_account_deletion_request",
        {
            "required visible-ASCII idempotency key": "idempotency_key(request, required=True)",
            "fresh confirmation step-up": "lifecycle_step_up(claims)",
            "subject-bound lifecycle lock": "AccountLifecycle.subject_hmac == subject_hmac",
            "durable confirmation marker": "lifecycle.confirmation_idempotency_hmac",
            "exact replay verifier": "await validate_lifecycle_confirmation_replay(",
            "exact 202 replay": "status_code=202",
            "replay marker": 'headers={"Idempotency-Replayed": "true"}',
        },
        errors,
    )
    status = _function_source(main, "account_lifecycle_status", main_path, errors)
    _require_source_markers(
        status,
        f"{main_path}#account_lifecycle_status",
        {
            "receipt-only authorization scheme": 'authorization.startswith("LifecycleReceipt ")',
            "terminal proof before rate mutation": "await validate_terminal_lifecycle_status(",
            "receipt rate mutation": "rate = await session.scalar(",
        },
        errors,
    )
    terminal_proof = _function_source(
        main, "validate_terminal_lifecycle_status", main_path, errors
    )
    _require_source_markers(
        terminal_proof,
        f"{main_path}#validate_terminal_lifecycle_status",
        {
            "fully-erased state": 'lifecycle.state != "fully_erased"',
            "verified provider state": 'lifecycle.provider_state != "verified"',
            "verified backup state": 'lifecycle.backup_state != "verified"',
            "access-deny proof": "select(AccountAccessDeny)",
            "tombstone proof": "select(AccountLifecycleTombstone)",
            "journal availability proof": "request.app.state.deletion_journal",
            "live-mirror proof": "await verify_live_deletion_mirror(session, journal)",
            "completed erasure work": 'item.state != "completed"',
            "backup-obligation proof": "select(AccountBackupObligation)",
            "backup proof digest": "obligation.proof_digest",
        },
        errors,
    )
    terminal_index = status.find("await validate_terminal_lifecycle_status(")
    rate_index = status.find("rate = await session.scalar(")
    if terminal_index < 0 or rate_index < 0 or terminal_index >= rate_index:
        _error(
            errors,
            f"repository.account_lifecycle.{main_path}#account_lifecycle_status",
            "terminal proof must complete before lifecycle receipt-rate mutation",
        )
    _require_source_markers(
        main,
        main_path,
        {
            "receipt no-store header": '"Cache-Control": "no-store, private"',
            "receipt noindex header": '"X-Robots-Tag": "noindex, nofollow, noarchive"',
        },
        errors,
    )

    required = {
        "apps/api/app/models.py": {
            "nullable request HMAC": "request_idempotency_hmac: Mapped[str | None]",
            "nullable confirmation HMAC": "confirmation_idempotency_hmac: Mapped[str | None]",
        },
        "apps/api/alembic/versions/0024_lifecycle_confirmation_idempotency.py": {
            "confirmation HMAC migration": 'sa.Column("confirmation_idempotency_hmac", sa.String(length=64), nullable=True)',
            "unsafe downgrade refusal": "cannot downgrade lifecycle confirmation idempotency without destroying receipt state",
        },
        "apps/web/lib/account-lifecycle-api.ts": {
            "disabled client gate": 'process.env.NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED === "true"',
            "confirmation idempotency header": '"Idempotency-Key": idempotencyKey',
            "strict first-or-replay 202": "response.status !== 202 || !isJsonResponse(response.headers) || !isAllowedReplayHeader(response.headers)",
        },
        "apps/web/components/account-privacy-center.tsx": {
            "in-memory confirmation attempt": "confirmAttemptRef",
            "subject-current confirmation guard": "const requestIsCurrent = () => requestSubject === subject",
            "no browser receipt persistence": "does not put them in browser persistence or URLs",
            "recovery guidance": "the only credential this view can use to read later sanitized status",
        },
        "apps/api/tests/test_account_lifecycle.py": {
            "confirmation key and OpenAPI test": "test_lifecycle_confirmation_requires_key_and_advertises_openapi_header",
            "lost acknowledgement replay test": "test_lifecycle_confirmation_lost_ack_replays_without_second_step_up_or_mutation",
            "terminal proof fail-closed test": "test_lifecycle_status_terminal_proof_fail_closed_without_rate_mutation",
            "migration safety test": "test_lifecycle_confirmation_migration_refuses_unsafe_downgrade",
        },
        "apps/api/tests/test_account_erasure.py": {
            "terminal marker scrub test": "test_terminal_lifecycle_cleanup_scrubs_expired_markers_but_retains_authorities",
            "corrupt-mirror retention test": "test_terminal_lifecycle_cleanup_keeps_markers_when_live_mirror_is_corrupt",
        },
        "apps/api/tests/test_auth.py": {
            "non-Clerk confirmation denial": "test_lifecycle_confirmation_verifier_rejects_non_clerk_credentials",
            "fresh non-impersonated confirmation claims": "test_lifecycle_confirmation_verifier_accepts_only_fresh_non_impersonated_clerk_claims",
        },
        "apps/api/tests/test_deletion_journal.py": {
            "live mirror parity test": "test_live_mirror_parity_is_bidirectional_and_subject_bound",
        },
        "apps/web/tests/account-lifecycle-api.test.ts": {
            "strict confirmation response test": "accepts only exact first or replayed 202 JSON confirmations",
            "ambiguous retry retention test": "retains an ambiguous key for exact retry and clears it after a definitive 4xx",
        },
        "apps/web/tests/account-privacy-center.test.ts": {
            "guarded confirmation attempt test": "owns one guarded confirmation attempt and retains ambiguous offline/server outcomes",
        },
        "docs/account-lifecycle.md": {
            "confirmation HMAC contract": "requires a caller-owned visible-ASCII `Idempotency-Key`",
            "lost acknowledgement contract": "A lost acknowledgement can therefore replay the exact `202` deletion response",
            "receipt-only terminal status": "The endpoint is non-cacheable, rate limited, excluded from OpenAPI and agent discovery",
            "terminal proof scope": "only the expired receipt/confirmation marker material is scrubbed after the exact terminal proof",
        },
    }
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
    return errors


def _frontend_docker_context_errors(root: Path) -> list[str]:
    """Require a repository-safe frontend Docker context without claiming a build ran."""

    errors: list[str] = []
    relative_path = "apps/web/.dockerignore"
    source = _read_anchor_source(root, relative_path, errors)
    rules = [
        line.strip()
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    required_exclusions = {
        ".env",
        ".env.*",
        ".next",
        "node_modules",
        "coverage",
        ".cache",
        ".turbo",
        ".vitest",
        "test-results",
        "playwright-report",
        "tsconfig.tsbuildinfo",
        "*.log",
    }
    for rule in sorted(required_exclusions - set(rules)):
        _error(
            errors,
            f"repository.docker_context.{relative_path}",
            f"is missing required secret or generated-artifact exclusion {rule!r}",
        )
    if "!.env.example" not in rules:
        _error(
            errors,
            f"repository.docker_context.{relative_path}",
            "must explicitly retain .env.example",
        )

    def matches(pattern: str, path: str) -> bool:
        normalized = pattern.strip("/")
        return (
            normalized in {path, "*", "**"}
            or normalized in {f"{path}/*", f"{path}/**"}
            or fnmatchcase(path, normalized)
        )

    build_inputs = {
        "package.json",
        "package-lock.json",
        "Dockerfile",
        "next.config.ts",
        "postcss.config.mjs",
        "tailwind.config.ts",
        "tsconfig.json",
        "next-env.d.ts",
        "app",
        "components",
        "lib",
        "public",
        "scripts",
    }
    for build_input in sorted(build_inputs):
        included = True
        for raw_rule in rules:
            negated = raw_rule.startswith("!")
            pattern = raw_rule[1:] if negated else raw_rule
            if matches(pattern, build_input):
                included = negated
        if not included:
            _error(
                errors,
                f"repository.docker_context.{relative_path}",
                f"must retain required frontend build input {build_input!r}",
            )

    operational_path = "infra/tests/operational-contracts.py"
    operational = _read_anchor_source(root, operational_path, errors)
    _require_source_markers(
        operational,
        operational_path,
        {
            "frontend dockerignore path": 'frontend_dockerignore_path = root / "apps/web/.dockerignore"',
            "secret exclusion assertion": '".env.*"',
            "example environment retention assertion": '"!.env.example"',
            "generated dependency exclusion assertion": '"node_modules"',
            "build-input retention assertion": "frontend_build_input not in frontend_dockerignore_rules",
        },
        errors,
    )
    return errors


def _document_ingestion_built_image_errors(root: Path) -> list[str]:
    """Bind the network-isolated built-image conversion gate without claiming a run."""
    errors: list[str] = []
    script_path = "infra/tests/converter-built-image.sh"
    script = _read_anchor_source(root, script_path, errors)
    _require_source_markers(
        script,
        script_path,
        {
            "exact built image": 'readonly IMAGE="connectmd-api:local"',
            "running converter identity": 'converter_id="$(docker compose "${COMPOSE_ARGS[@]}" ps -q converter)"',
            "converter network inspection": 'network_mode="$(docker inspect "$converter_id" --format \'{{.HostConfig.NetworkMode}}\')"',
            "network isolation assertion": '[ "$network_mode" = "none" ]',
            "built image assertion": '[ "$converter_image" = "$IMAGE" ]',
            "valid PDF conversion": 'run_ingest_case valid.pdf .pdf 8192 valid ""',
            "valid DOCX conversion": 'run_ingest_case valid.docx .docx 8192 valid ""',
            "valid Markdown conversion": 'run_ingest_case valid.md .md 8192 valid ""',
            "malformed PDF rejection": 'run_ingest_case malformed.pdf .pdf 8192 invalid "PDF upload does not have a valid PDF signature"',
            "malformed DOCX rejection": 'run_ingest_case malformed.docx .docx 8192 invalid "DOCX upload does not have a valid ZIP signature"',
            "oversized PDF rejection": 'run_ingest_case oversized.pdf .pdf 1024 oversized "converted text exceeds the configured extracted-text limit"',
            "oversized DOCX rejection": 'run_ingest_case oversized.docx .docx 1024 oversized "converted text exceeds the configured extracted-text limit"',
            "protocol residue rejection": 'raise SystemExit(f"orphan ingest protocol files remain: {residue}")',
            "timeout supervision": 'run_case("timeout", alive=True, timeout=True)',
            "crash supervision": 'run_case("crash", alive=False, timeout=False)',
            "bounded completion marker": "CONVERTER_BUILT_IMAGE=PASS",
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(root, ".github/workflows/ci.yml", errors),
        ".github/workflows/ci.yml#converter-built-image",
        {
            "built-image CI invocation": "bash infra/tests/converter-built-image.sh",
        },
        errors,
    )
    return errors


def _production_container_hardening_errors(root: Path) -> list[str]:
    """Bind the semantic Compose hardening verifier without claiming Docker ran."""

    errors: list[str] = []
    compose_path = "compose.yaml"
    compose = _read_anchor_source(root, compose_path, errors)
    python_services = (
        "db-migrate",
        "api",
        "converter",
        "search-projection-worker",
        "search-admin",
        "taxonomy-admin",
        "exact-search-admin",
        "search-key-bootstrap",
        "account-erasure-worker",
    )
    service_positions = []
    for service_name in python_services:
        start = compose.find(f"  {service_name}:")
        if start < 0:
            _error(
                errors,
                f"repository.container_hardening.{compose_path}",
                f"is missing Python runtime service {service_name!r}",
            )
            continue
        service_positions.append(start)
        next_service = re.search(
            r"\n  [A-Za-z0-9][A-Za-z0-9_-]*:\n", compose[start + 1 :]
        )
        end = (
            start + 1 + next_service.start()
            if next_service is not None
            else len(compose)
        )
        block = compose[start:end]
        _require_source_markers(
            block,
            f"{compose_path}#{service_name}",
            {
                "fixed UID/GID": 'user: "10001:10001"',
                "read-only root": "read_only: true",
                "all capability drop": "cap_drop:\n      - ALL",
                "no-new-privileges": "security_opt:\n      - no-new-privileges:true",
                "locally built API image": "image: connectmd-api:${CONNECTMD_IMAGE_TAG:-local}",
            },
            errors,
        )
    migration_start = compose.find("  db-migrate:")
    if migration_start >= 0:
        migration_end_match = re.search(
            r"\n  [A-Za-z0-9][A-Za-z0-9_-]*:\n",
            compose[migration_start + 1 :],
        )
        migration_end = (
            migration_start + 1 + migration_end_match.start()
            if migration_end_match is not None
            else len(compose)
        )
        migration_block = compose[migration_start:migration_end]
        _require_source_markers(
            migration_block,
            f"{compose_path}#db-migrate",
            {
                "database operations profile": 'profiles: ["database-operations"]',
                "migration command": 'command: ["alembic", "upgrade", "head"]',
                "least-privilege migrator URL": (
                    "CONNECTMD_DATABASE_URL: postgresql+asyncpg://connectmd_migrator:"
                ),
                "migrator password": "CONNECTMD_MIGRATOR_DB_PASSWORD",
            },
            errors,
        )
        if "POSTGRES_PASSWORD" in migration_block:
            _error(
                errors,
                f"repository.container_hardening.{compose_path}#db-migrate",
                "must not expose the operator PostgreSQL password",
            )
    if len(service_positions) != len(python_services) or len(
        set(service_positions)
    ) != len(service_positions):
        _error(
            errors,
            f"repository.container_hardening.{compose_path}",
            "must define exactly the nine protected Python runtime services, including db-migrate and the exact-search admin profile",
        )
    if compose.count("image: connectmd-api:${CONNECTMD_IMAGE_TAG:-local}") != len(
        python_services
    ):
        _error(
            errors,
            f"repository.container_hardening.{compose_path}",
            "must bind the locally built Python runtime image to exactly the nine protected services",
        )
    database_operation_services = (
        (
            "database-backup",
            "connectmd_backup",
            "CONNECTMD_BACKUP_DB_PASSWORD",
        ),
        (
            "database-restore",
            "connectmd_migrator",
            "CONNECTMD_MIGRATOR_DB_PASSWORD",
        ),
    )
    for service_name, role_name, password_key in database_operation_services:
        service_start = compose.find(f"  {service_name}:")
        if service_start < 0:
            _error(
                errors,
                f"repository.container_hardening.{compose_path}",
                f"is missing database operation service {service_name!r}",
            )
            continue
        next_service = re.search(
            r"\n  [A-Za-z0-9][A-Za-z0-9_-]*:\n",
            compose[service_start + 1 :],
        )
        service_end = (
            service_start + 1 + next_service.start()
            if next_service is not None
            else len(compose)
        )
        block = compose[service_start:service_end]
        _require_source_markers(
            block,
            f"{compose_path}#{service_name}",
            {
                "database operations profile": 'profiles: ["database-operations"]',
                "pinned PostgreSQL image": (
                    "image: postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
                ),
                "least-privilege UID/GID": 'user: "999:999"',
                "read-only root": "read_only: true",
                "all capability drop": "cap_drop:\n      - ALL",
                "no-new-privileges": "security_opt:\n      - no-new-privileges:true",
                "PostgreSQL host": "PGHOST: postgres",
                "PostgreSQL database": "PGDATABASE: ${POSTGRES_DB:-connectmd}",
                "database role": f"PGUSER: {role_name}",
                "database role password": password_key,
            },
            errors,
        )
        if "POSTGRES_PASSWORD" in block:
            _error(
                errors,
                f"repository.container_hardening.{compose_path}#{service_name}",
                "must not expose the operator PostgreSQL password",
            )
    for service_name, marker in (
        ("api", "/tmp:size=64m,mode=1777"),
        ("account-erasure-worker", "/tmp:size=16m,mode=1777"),
    ):
        start = compose.find(f"  {service_name}:")
        if start >= 0:
            next_service = re.search(
                r"\n  [A-Za-z0-9][A-Za-z0-9_-]*:\n", compose[start + 1 :]
            )
            end = (
                start + 1 + next_service.start()
                if next_service is not None
                else len(compose)
            )
            _require_source_markers(
                compose[start:end],
                f"{compose_path}#{service_name}",
                {"exact tmpfs boundary": marker},
                errors,
            )
    lifecycle_start = compose.find("  account-erasure-worker:")
    lifecycle_end_match = (
        re.search(r"\n  [A-Za-z0-9][A-Za-z0-9_-]*:\n", compose[lifecycle_start + 1 :])
        if lifecycle_start >= 0
        else None
    )
    lifecycle = (
        compose[
            lifecycle_start : lifecycle_start + 1 + lifecycle_end_match.start()
            if lifecycle_start >= 0 and lifecycle_end_match is not None
            else len(compose)
        ]
        if lifecycle_start >= 0
        else ""
    )
    _require_source_markers(
        lifecycle,
        f"{compose_path}#account-erasure-worker",
        {
            "heartbeat path": "CONNECTMD_ACCOUNT_LIFECYCLE_HEARTBEAT_PATH",
            "read-only journal mount": "target: /deletion-journal",
            "read-only witness mount": "target: /deletion-head-witness",
            "heartbeat health semantics": "d['state']=='healthy'",
        },
        errors,
    )
    operational_path = "infra/tests/operational-contracts.py"
    operational = _read_anchor_source(root, operational_path, errors)
    _require_source_markers(
        operational,
        operational_path,
        {
            "duplicate-key-safe YAML loader": "class DuplicateKeySafeLoader(yaml.SafeLoader):",
            "semantic runtime contracts": "PYTHON_SERVICE_RUNTIME_CONTRACTS = {",
            "exact hardening verifier": "def assert_compose_hardening_contract(",
            "excluded service exceptions": "excluded_base_hardening = {",
            "entrypoint rejection": '"base entrypoint override"',
            "capability addition rejection": '("cap_add", lambda service: service.update(cap_add=["NET_ADMIN"]))',
            "privileged rejection": '("privileged", lambda service: service.update(privileged=True))',
            "extra writable mount rejection": '"extra read-write volume"',
            "production override rejection": '"production hardening override"',
            "duplicate Compose key rejection": "duplicate Compose mapping keys must fail closed",
        },
        errors,
    )
    ci_path = ".github/workflows/ci.yml"
    ci = _read_anchor_source(root, ci_path, errors)
    provision = "python -m pip install --require-hashes -r requirements-test.lock"
    verifier = "python ../../infra/tests/operational-contracts.py"
    if ci.count(verifier) != 1:
        _error(
            errors,
            f"repository.container_hardening.{ci_path}",
            "must invoke the semantic operational verifier exactly once",
        )
    if (
        ci.find(provision) < 0
        or ci.find(verifier) < 0
        or ci.find(provision) >= ci.find(verifier)
    ):
        _error(
            errors,
            f"repository.container_hardening.{ci_path}",
            "must provision requirements-test.lock (including PyYAML) before the semantic verifier",
        )
    ci_role_markers = (
        "Bootstrap least-privilege database roles",
        "alembic upgrade head",
        "Reconcile and verify database roles",
        "postgresql+asyncpg://connectmd_projection_admin:",
        "CONNECTMD_RUN_LIVE_INTEGRATION=1 pytest -q tests/test_live_stack.py",
    )
    ci_role_positions = [ci.find(marker) for marker in ci_role_markers]
    if any(
        position < 0 for position in ci_role_positions
    ) or ci_role_positions != sorted(ci_role_positions):
        _error(
            errors,
            f"repository.container_hardening.{ci_path}",
            "must bootstrap, migrate, reconcile, project, and run live tests under ordered scoped database roles",
        )
    _require_source_markers(
        ci,
        ci_path,
        {
            "offline database owner": "POSTGRES_USER: postgres",
            "API runtime database role": "postgresql+asyncpg://connectmd_api:",
            "migration database role": "postgresql+asyncpg://connectmd_migrator:",
            "role reconciliation verification": "--set connectmd_verify=true",
        },
        errors,
    )
    return errors


def _search_projection_contract_errors(root: Path) -> list[str]:
    errors: list[str] = []
    compose_path = "compose.yaml"
    compose = _read_anchor_source(root, compose_path, errors)
    service_start = compose.find("  search-projection-worker:")
    service_end = compose.find("  search-admin:", service_start)
    if service_start < 0 or service_end < 0:
        _error(
            errors,
            "repository.search_projection.compose.yaml",
            "is missing the bounded search projection worker service",
        )
    else:
        worker = compose[service_start:service_end]
        _require_source_markers(
            worker,
            compose_path,
            {
                "read-only root filesystem": "read_only: true",
                "read-only canonical storage": "markdown_storage:/app/storage:ro",
                "internal data network": "- connectmd_data",
                "worker health check": "CONNECTMD_SEARCH_PROJECTION_HEARTBEAT_PATH",
            },
            errors,
        )
        for forbidden in (
            "CONNECTMD_CLERK_",
            "CONNECTMD_API_KEY_PEPPER",
            "CONNECTMD_ACCOUNT_LIFECYCLE",
            "- connectmd_app",
            "POSTGRES_PASSWORD",
            "MEILI_MASTER_KEY",
        ):
            if forbidden in worker:
                _error(
                    errors,
                    "repository.search_projection.compose.yaml",
                    f"worker must not receive forbidden authority marker {forbidden!r}",
                )
    bootstrap_start = compose.find("  search-key-bootstrap:")
    bootstrap_end = compose.find("  account-erasure-worker:", bootstrap_start)
    if bootstrap_start < 0 or bootstrap_end < 0:
        _error(
            errors,
            "repository.search_projection.compose.yaml",
            "is missing the bounded search-key bootstrap service",
        )
    else:
        bootstrap = compose[bootstrap_start:bootstrap_end]
        _require_source_markers(
            bootstrap,
            compose_path,
            {
                "bootstrap command": 'command: ["python", "-m", "app.search_key_bootstrap"]',
                "explicit opt-in profile": 'profiles: ["search-bootstrap"]',
                "read-only root filesystem": "read_only: true",
                "search-key issuer credential": "MEILI_MASTER_KEY",
                "internal data network": "- connectmd_data",
            },
            errors,
        )
        if "volumes:" in bootstrap:
            _error(
                errors,
                "repository.search_projection.compose.yaml",
                "search-key bootstrap must not mount canonical or lifecycle storage",
            )
        for forbidden in (
            "CONNECTMD_CLERK_",
            "CONNECTMD_API_KEY_PEPPER",
            "CONNECTMD_ACCOUNT_LIFECYCLE",
            "CONNECTMD_LIFECYCLE_",
            "CONNECTMD_DELETION_",
            "CONNECTMD_DATABASE_URL",
            "CONNECTMD_STORAGE_PATH",
            "POSTGRES_PASSWORD",
        ):
            if forbidden in bootstrap:
                _error(
                    errors,
                    "repository.search_projection.compose.yaml",
                    "search-key bootstrap must not receive application authority marker "
                    f"{forbidden!r}",
                )
    index_setting = (
        "CONNECTMD_MEILISEARCH_INDEX: ${CONNECTMD_MEILISEARCH_INDEX:-documents}"
    )
    if compose.count(index_setting) != 5:
        _error(
            errors,
            "repository.search_projection.compose.yaml",
            "API, projection worker, search admin, search-key bootstrap, and lifecycle worker must share one index setting",
        )
    search_source = _read_anchor_source(root, "apps/api/app/services/search.py", errors)
    for forbidden in ('"owner_id",', '"owner_id": document.owner_id'):
        if forbidden in search_source:
            _error(
                errors,
                "repository.search_projection.apps/api/app/services/search.py",
                "must not place the canonical account subject in Meilisearch",
            )
    api_source = _read_anchor_source(root, "apps/api/app/main.py", errors)
    for forbidden in (
        "await search.index(",
        "await search.delete_document(",
        "await search.configure_index(",
        "await search.reset_index(",
    ):
        if forbidden in api_source:
            _error(
                errors,
                "repository.search_projection.apps/api/app/main.py",
                f"API runtime must not invoke projection writer {forbidden!r}",
            )
    required = {
        "apps/api/app/models.py": {
            "version-keyed task model": "class SearchProjectionTask(Base):",
        },
        "apps/api/app/services/documents.py": {
            "transactional projection task": "SearchProjectionTask(",
        },
        "apps/api/app/services/search_projection.py": {
            "expired lease recovery": "SearchProjectionTask.lease_expires_at <= now",
            "stale-version supersession": 'action="superseded"',
            "dead-letter recovery": "async def retry_dead_letter(",
            "missing-document tombstone": 'action="removed_missing"',
        },
        "apps/api/app/services/search.py": {
            "read-only index gate": "async def require_index(",
            "explicit admin configuration": "async def configure_index(",
        },
        "apps/api/app/search_key_bootstrap.py": {
            "search-only key": 'actions=("search", "indexes.get")',
            "projection key scope": '"documents.add"',
            "delete-only key scope": 'environment_key="CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY"',
        },
        "apps/api/alembic/versions/0019_search_projection_outbox.py": {
            "existing-document backfill": "FROM documents",
        },
        "apps/api/tests/test_search_projection_worker.py": {
            "non-public removal test": "test_public_to_private_transition_removes_projection_without_indexing_private_bytes",
            "expired lease test": "test_failure_retries_with_bounded_metadata_and_recovers_expired_lease",
            "dead-letter recovery test": "test_dead_letter_is_content_free_and_operator_can_requeue_exact_version",
            "canonical deletion test": "test_canonical_delete_leaves_tombstone_task_that_removes_projection",
            "authenticated health test": "test_health_heartbeat_requires_database_and_authenticated_meili",
        },
    }
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
    return errors


def _a2a_action_error_surface_errors(root: Path) -> list[str]:
    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    action_error = _function_source(main_source, "a2a_action_error", main_path, errors)
    _require_source_markers(
        action_error,
        f"{main_path}#a2a_action_error",
        {
            "authentication mapping": 'state = "TASK_STATE_AUTH_REQUIRED"',
            "authentication code": 'code = "auth_required"',
            "invalid parameter statuses": "status_code in {400, 422, 428}",
            "invalid parameter code": 'code = "invalid_params"',
            "non-enumerating rejection statuses": "status_code in {403, 404}",
            "non-enumerating rejection code": 'code = "request_rejected"',
            "conflict mapping": 'code = "conflict"',
            "rate-limit mapping": 'code = "rate_limited"',
            "service failure state": 'state = "TASK_STATE_FAILED"',
            "service failure code": 'code = "service_unavailable"',
            "minimal error artifact": 'result={"error": {"code": code, "message": error_message}}',
            "A2A error media type": 'media_type="application/a2a+json"',
        },
        errors,
    )
    required = {
        "apps/api/tests/test_protocol_core.py": {
            "exact error artifact assertion": 'assert data == {"error": {"code": code, "message": message}}',
            "contact rejection coverage": "test_a2a_contact_rejections_are_bounded_and_non_enumerating",
            "outer failure coverage": "test_a2a_outer_transport_failures_remain_problem_responses",
            "missing idempotency coverage": '"a2a-d2-contact-precondition-0001"',
        },
        "apps/api/tests/test_agent_identity_mandates.py": {
            "outreach and status rejection coverage": "test_a2a_outreach_and_status_rejections_use_stable_privacy_minimal_errors",
            "exact error artifact assertion": 'assert data == {"error": {"code": code, "message": message}}',
            "service failure assertion": 'code="service_unavailable"',
        },
        "docs/agent-interoperability.md": {
            "bounded A2A action error shape": 'Action-level contact and outreach failures use the bounded artifact shape `{"error":{"code":"...","message":"..."}}`',
            "transport-only authority caveat": "This is transport normalization only",
            "outer failure boundary": "Outer media, version, and malformed-message failures remain protocol errors",
        },
    }
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)
    return errors


def _protected_agent_action_protocol_errors(root: Path) -> list[str]:
    """Bind protected action headers, credential authority, and terminal validation."""

    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    card_path = "apps/api/app/routes/agent_card.py"
    card_source = _read_anchor_source(root, card_path, errors)
    for route, label in (
        ("POST /v1/contact-requests", "contact"),
        ("POST /v1/agent-outreach", "mandate outreach"),
    ):
        decorator = _route_decorator(route, main_source)
        if decorator is None:
            _error(
                errors,
                f"repository.protected_agent_actions.{label}",
                f"cannot locate implemented route {route!r}",
            )
            continue
        _require_source_markers(
            decorator,
            f"{main_path}#{label}-route",
            {
                "required Idempotency-Key name": '"name": "Idempotency-Key"',
                "required Idempotency-Key header location": '"in": "header"',
                "required Idempotency-Key flag": '"required": True',
                "visible-ASCII Idempotency-Key bound": '"pattern": r"^[\\x21-\\x7E]{1,128}$"',
            },
            errors,
        )
    card = _function_source(card_source, "agent_card", card_path, errors)
    _require_source_markers(
        card,
        f"{card_path}#agent_card",
        {
            "Clerk-human card scheme": '"clerk_human": {\n                "httpAuthSecurityScheme": {',
            "eligible contact card scheme": '"eligible_agent_contact": {\n                "httpAuthSecurityScheme": {',
            "eligible contact credential boundary": '"cnd_ API key or non-mandate direct owner-bound cng_ Agent Grant"',
            "mandate card scheme": '"mandate_agent_grant": {\n                "httpAuthSecurityScheme": {',
            "mandate credential boundary": '"live mandate-bound cng_ Agent Grant"',
            "exact mandate scope boundary": "single contacts:write scope",
            "mediated-contact skill": '"id": "request-mediated-contact"',
            "mandate-outreach skill": '"id": "send-mandate-bound-agent-outreach"',
            "required protected header": '"x-connectmd-required-http-headers": ["Idempotency-Key"]',
            "contact credential requirements": '"eligible_agent_contact": {"list": ["contacts:write"]}',
            "mandate credential requirements": '"mandate_agent_grant": {"list": ["contacts:write"]}',
        },
        errors,
    )

    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    action_specs = (
        ("list_taxonomies", "list_taxonomy_terms", 'if set(data) != {"action"}:'),
        (
            "contact_request",
            "agent_outreach",
            "ContactRequestCreate.model_validate(data)",
        ),
        (
            "agent_outreach",
            "get_agent_outreach_status",
            "AgentOutreachCreate.model_validate(data)",
        ),
    )
    for action, next_action, validation_marker in action_specs:
        start = a2a.find(f'if action == "{action}":')
        end = a2a.find(f'if action == "{next_action}":', start + 1)
        branch = a2a[start:end] if start >= 0 and end > start else ""
        scoped_path = f"{main_path}#a2a-{action}-validation"
        _require_source_markers(
            branch,
            scoped_path,
            {
                f"{action} validation": validation_marker,
                f"{action} terminal validation error": "return a2a_action_error(",
                f"{action} validation status": "status_code=422",
            },
            errors,
        )
    protocol_test_path = "apps/api/tests/test_protocol_core.py"
    protocol_tests = _read_anchor_source(root, protocol_test_path, errors)
    card_test = _function_source(
        protocol_tests,
        "test_agent_card_protected_resource_metadata_and_mcp_boundary",
        protocol_test_path,
        errors,
    )
    _require_source_markers(
        card_test,
        f"{protocol_test_path}#agent-card",
        {
            "contact header assertion": 'assert contact_skill["x-connectmd-required-http-headers"] == ["Idempotency-Key"]',
            "contact authority assertion": '"eligible_agent_contact": {"list": ["contacts:write"]}',
            "outreach header assertion": 'assert outreach_skill["x-connectmd-required-http-headers"] == ["Idempotency-Key"]',
            "mandate authority assertion": '"mandate_agent_grant": {"list": ["contacts:write"]}',
        },
        errors,
    )
    validation_test = _function_source(
        protocol_tests,
        "test_a2a_action_validation_failures_are_terminal_tasks",
        protocol_test_path,
        errors,
    )
    _require_source_markers(
        validation_test,
        f"{protocol_test_path}#terminal-validation",
        {
            "valid-envelope action cases": "invalid_actions = (",
            "terminal rejected assertion": 'state="TASK_STATE_REJECTED"',
            "invalid-parameter assertion": 'code="invalid_params"',
        },
        errors,
    )
    contact_test = _function_source(
        protocol_tests,
        "test_a2a_contact_rejections_are_bounded_and_non_enumerating",
        protocol_test_path,
        errors,
    )
    _require_source_markers(
        contact_test,
        f"{protocol_test_path}#contact-precondition",
        {
            "missing contact idempotency case": '"a2a-d2-contact-precondition-0001"',
            "terminal contact precondition assertion": 'state="TASK_STATE_REJECTED"',
            "invalid contact precondition assertion": 'code="invalid_params"',
        },
        errors,
    )

    outreach_test_path = "apps/api/tests/test_agent_identity_mandates.py"
    outreach_tests = _read_anchor_source(root, outreach_test_path, errors)
    openapi_test = _function_source(
        outreach_tests,
        "test_agent_outreach_uses_mandate_and_safe_receipts",
        outreach_test_path,
        errors,
    )
    _require_source_markers(
        openapi_test,
        f"{outreach_test_path}#openapi-idempotency",
        {
            "exact Idempotency-Key parameter": '"name": "Idempotency-Key"',
            "required parameter assertion": '"required": True',
            "both protected HTTP routes": 'for path in ("/v1/contact-requests", "/v1/agent-outreach"):',
            "exact parameter list assertion": 'assert schema["paths"][path]["post"]["parameters"] == [expected_idempotency_parameter]',
        },
        errors,
    )
    protected_validation_test = _function_source(
        outreach_tests,
        "test_a2a_outreach_and_status_rejections_use_stable_privacy_minimal_errors",
        outreach_test_path,
        errors,
    )
    _require_source_markers(
        protected_validation_test,
        f"{outreach_test_path}#terminal-protected-validation",
        {
            "invalid outreach case": '"a2a-d2-outreach-invalid-0001"',
            "invalid status case": '"a2a-d2-status-invalid-0001"',
            "terminal rejected assertion": 'state="TASK_STATE_REJECTED"',
            "invalid-parameter assertion": 'code="invalid_params"',
        },
        errors,
    )
    return errors


def _agent_identity_durability_errors(root: Path) -> list[str]:
    """Bind the human-only Agent Identity receipt contract without claiming new storage."""

    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    create = _function_source(main_source, "create_agent_identity", main_path, errors)
    withdraw = _function_source(
        main_source, "withdraw_agent_identity", main_path, errors
    )
    replay = _function_source(main_source, "agent_identity_replay", main_path, errors)
    openapi = _function_source(
        main_source, "_agent_identity_openapi_extra", main_path, errors
    )
    _require_source_markers(
        openapi,
        f"{main_path}#agent-identity-openapi",
        {
            "human-only declaration": '"x-connectmd-human-only": True',
            "required Idempotency-Key": '"name": "Idempotency-Key"',
            "visible-ASCII idempotency bound": '"pattern": _IDEMPOTENCY_KEY_PATTERN',
        },
        errors,
    )
    for action, source, operation, status, body in (
        (
            "create",
            create,
            'operation = "POST:/v1/agent-identities"',
            "status_code=201",
            "body=response_body",
        ),
        (
            "withdraw",
            withdraw,
            'operation = f"DELETE:/v1/agent-identities/{agent_handle}"',
            "status_code=204",
            'body=""',
        ),
    ):
        _require_source_markers(
            source,
            f"{main_path}#agent-identity-{action}",
            {
                "Clerk-human authority": 'if principal.method != "clerk_jwt":',
                "caller key required": "key = idempotency_key(request, required=True)",
                "exact operation": operation,
                "pre-lock replay": "replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)",
                "post-lock replay": "replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)",
                "safe event": f'event_type="agent_identity.{"created" if action == "create" else "withdrawn"}"',
                "atomic durable receipt": "await store_idempotency(",
                "exact response status": status,
                "exact receipt body": body,
                "existing receipt type": 'resource_type="agent_identity"',
            },
            errors,
        )
    _require_source_markers(
        replay,
        f"{main_path}#agent-identity-replay",
        {
            "create operation": 'operation == "POST:/v1/agent-identities"',
            "withdraw operation": 'operation.startswith("DELETE:/v1/agent-identities/")',
            "safe receipt shape": 'record.response_headers != "{}"',
            "digest receipt parser": "_agent_identity_resource_parts(record.resource_id)",
            "current owner lock": "AgentIdentity.owner_id == principal.subject",
            "create state drift rejection": 'identity.status != "active"',
            "withdraw state drift rejection": 'identity.status != "withdrawn"',
            "digest comparison": 'compare_digest(parts["digest"], expected_digest)',
            "exact create replay": 'headers={"Idempotency-Replayed": "true"}',
            "exact empty withdraw replay": 'return Response(status_code=204, headers={"Idempotency-Replayed": "true"})',
        },
        errors,
    )
    test_path = "apps/api/tests/test_agent_identity_lifecycle_durability.py"
    tests = _read_anchor_source(root, test_path, errors)
    _require_source_markers(
        tests,
        test_path,
        {
            "header and authority test": "test_agent_identity_keys_openapi_and_clerk_boundary",
            "atomic create replay and collision test": "test_agent_identity_create_replays_and_collisions_are_atomic",
            "empty withdraw replay and public removal test": "test_agent_identity_withdraw_replays_empty_response_and_removes_public_identity",
            "create corruption test": "test_agent_identity_create_replay_corruption_fails_closed",
            "withdraw state drift test": "test_agent_identity_withdraw_replay_state_drift_fails_closed",
            "SQLite same-key limitation": "SQLite gather coverage; this does not prove PostgreSQL row-lock scheduling.",
            "MCP create exclusion": 'assert "create_agent_identity" not in tool_names',
            "MCP withdraw exclusion": 'assert "withdraw_agent_identity" not in tool_names',
            "public directory removal": 'assert directory.json()["identities"] == []',
        },
        errors,
    )
    web_path = "apps/web/tests/agent-identity-api.test.ts"
    web_tests = _read_anchor_source(root, web_path, errors)
    _require_source_markers(
        web_tests,
        web_path,
        {
            "caller key forwarding": 'get("Idempotency-Key")).toBe("identity-withdraw-0001")',
            "strict empty withdraw": "accepts only an empty 204 withdrawal and retains ambiguity for malformed success",
            "subject-bound identity request": "identity-subject-0001",
        },
        errors,
    )
    docs = _read_anchor_source(root, "docs/agent-interoperability.md", errors)
    _require_source_markers(
        docs,
        "docs/agent-interoperability.md",
        {
            "human-only identity durability prose": "Agent Identity create and withdrawal are separate Clerk-human HTTP mutations.",
            "identity MCP/A2A exclusion": "These lifecycle writes remain Clerk-human-only HTTP and are not MCP or A2A actions",
        },
        errors,
    )
    return errors


def _mcp_outreach_parity_errors(root: Path) -> list[str]:
    """Require MCP outreach to remain a thin canonical-authority transport."""

    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    discovery_path = "apps/api/app/routes/discovery.py"
    main_source = _read_anchor_source(root, main_path, errors)
    discovery_source = _read_anchor_source(root, discovery_path, errors)
    replay = _function_source(main_source, "agent_outreach_replay", main_path, errors)
    create = _function_source(main_source, "create_agent_outreach", main_path, errors)
    status = _function_source(
        main_source, "get_agent_outreach_status", main_path, errors
    )
    tools = _function_source(main_source, "mcp_tools", main_path, errors)
    dispatcher = _function_source(main_source, "mcp", main_path, errors)
    _require_source_markers(
        replay,
        f"{main_path}#agent-outreach-replay",
        {
            "mandate grant restriction": 'principal.method != "agent_grant"',
            "safe receipt type": 'record.resource_type != "contact_request"',
            "mandate digest binding": 'parts["mandate_digest"]',
            "source-handle digest binding": 'parts["source_identity_digest"]',
            "grant digest binding": 'parts["grant_digest"]',
            "live mandate reauthorization": "await mandate_bound_identity(session, principal)",
            "live identity reauthorization": "await lock_live_outreach_identities(",
            "privacy-safe receipt reconstruction": "agent_outreach_receipt(row)",
        },
        errors,
    )
    _require_source_markers(
        create,
        f"{main_path}#agent-outreach-create",
        {
            "MCP caller key seam": 'getattr(request.state, "mcp_idempotency_key", None)',
            "canonical contact operation": 'operation="POST:/v1/agent-outreach"',
            "exact origin context": '"mandate_id": mandate.id',
            "source handle context": '"source_identity_handle": sender_identity.handle',
            "grant context": '"grant_id": cast(str, principal.grant_id)',
        },
        errors,
    )
    _require_source_markers(
        status,
        f"{main_path}#agent-outreach-status",
        {
            "outreach-only status": 'ContactRequest.origin == "agent_outreach"',
            "human owner branch": 'if principal.method == "clerk_jwt":',
            "live mandate status branch": 'elif principal.method == "agent_grant":',
            "non-enumerating rejection": 'detail="agent outreach was not found"',
        },
        errors,
    )
    _require_source_markers(
        tools,
        f"{main_path}#mcp-tools",
        {
            "send tool": '"name": "send_agent_outreach"',
            "status tool": '"name": "get_agent_outreach_status"',
            "caller key schema": '"idempotency_key"',
            "no external endpoint": "this never calls an external endpoint",
            "privacy-safe status": "privacy-minimal status",
        },
        errors,
    )
    _require_source_markers(
        dispatcher,
        f"{main_path}#mcp-dispatcher",
        {
            "send dispatch": 'elif name == "send_agent_outreach":',
            "caller key handoff": "request.state.mcp_idempotency_key = outreach_key",
            "canonical create reuse": "await create_agent_outreach(",
            "status dispatch": 'elif name == "get_agent_outreach_status":',
            "canonical status reuse": "await get_agent_outreach_status(",
        },
        errors,
    )
    _require_source_markers(
        _function_source(main_source, "capabilities", main_path, errors),
        f"{main_path}#capabilities",
        {
            "capability tool discovery": '"mcp_tools": ["send_agent_outreach", "get_agent_outreach_status"]',
        },
        errors,
    )
    _require_source_markers(
        _function_source(discovery_source, "llms_full_txt", discovery_path, errors),
        f"{discovery_path}#llms_full_txt",
        {
            "llms full tool disclosure": "MCP also exposes `get_agent_identity`, `send_agent_outreach`, and `get_agent_outreach_status` as bounded tools.",
            "no external delivery": "connect.md performs no arbitrary outbound A2A delivery.",
        },
        errors,
    )
    test_path = "apps/api/tests/test_protocol_core.py"
    tests = _read_anchor_source(root, test_path, errors)
    parity_test = _function_source(
        tests,
        "test_mcp_agent_outreach_tools_share_canonical_authority_and_safe_receipts",
        test_path,
        errors,
    )
    _require_source_markers(
        parity_test,
        f"{test_path}#mcp-outreach-parity",
        {
            "strict send schema": 'send_schema = tools["send_agent_outreach"]["inputSchema"]',
            "strict status schema": 'status_schema = tools["get_agent_outreach_status"]["inputSchema"]',
            "capability discovery": 'capabilities.json()["agent_outreach"]["mcp_tools"]',
            "llms-full disclosure": 'assert "send_agent_outreach" in llms_full.text',
            "cross-transport replay": "cross_transport = await client.post(",
            "same-key collision": '"code": "conflict"',
            "human denial": "human_denied",
            "ordinary grant denial": "ordinary_denied",
            "API key denial": "api_key_denied",
            "revoked mandate denial": "revoked_denied",
            "message privacy": 'assert body["message"] not in first.text',
        },
        errors,
    )
    card_test = _function_source(
        tests,
        "test_agent_card_protected_resource_metadata_and_mcp_boundary",
        test_path,
        errors,
    )
    _require_source_markers(
        card_test,
        f"{test_path}#agent-card-mcp-boundary",
        {
            "exact seven A2A skills": 'assert {skill["id"] for skill in card.json()["skills"]} == {',
            "A2A status skill": '"get-mandate-bound-agent-outreach-status",',
        },
        errors,
    )
    docs = _read_anchor_source(root, "docs/agent-interoperability.md", errors)
    _require_source_markers(
        docs,
        "docs/agent-interoperability.md",
        {
            "MCP outreach documentation": "`send_agent_outreach` and `get_agent_outreach_status` reuse the canonical mandate-bound HTTP authority",
            "Agent Card seven-skill boundary": "advertises seven implemented skills",
            "external delivery exclusion": "The MVP does not call arbitrary third-party A2A endpoints.",
        },
        errors,
    )
    return errors


def _contact_request_status_invariant_errors(root: Path) -> list[str]:
    errors: list[str] = []
    allowed = "status IN ('pending', 'accepted', 'rejected', 'blocked', 'reported')"
    required = {
        "apps/api/app/models.py": {
            "contact-request status constraint": allowed,
            "named contact-request status constraint": 'name="ck_contact_requests_status"',
        },
        "apps/api/alembic/versions/0023_contact_request_status_constraint.py": {
            "migration revision": 'revision: str = "0023_contact_request_status_constraint"',
            "migration parent": 'down_revision: str | None = "0022_public_taxonomy_projection"',
            "bounded preflight query": "SELECT 1",
            "null status rejection": "status IS NULL",
            "unknown status rejection": "status NOT IN",
            "bounded preflight result": "LIMIT 1",
            "fixed preflight failure": '_PREFLIGHT_FAILURE = "contact request status invariant preflight failed"',
            "fixed preflight exception": "raise RuntimeError(_PREFLIGHT_FAILURE)",
            "canonical status set": f'_STATUS_CHECK = "{allowed}"',
            "named constraint creation": 'create_check_constraint("ck_contact_requests_status", _STATUS_CHECK)',
            "named constraint removal": 'drop_constraint("ck_contact_requests_status", type_="check")',
        },
        "apps/api/tests/test_migrations.py": {
            "valid and invalid write coverage": "test_0023_contact_request_status_constraint_preserves_schema_and_validates_writes",
            "failed upgrade revision coverage": "test_0023_invalid_contact_request_status_aborts_upgrade_without_advancing_revision",
            "downgrade re-upgrade coverage": "test_0023_downgrade_preserves_rows_and_reupgrade_preflights_again",
        },
        "docs/agent-interoperability.md": {
            "persisted status contract": "The persisted ContactRequest status set is exactly",
            "projection-only declined status": "`declined` is never stored",
        },
    }
    sources: dict[str, str] = {}
    for relative_path, markers in required.items():
        source = _read_anchor_source(root, relative_path, errors)
        sources[relative_path] = source
        _require_source_markers(source, relative_path, markers, errors)
    migration_path = (
        "apps/api/alembic/versions/0023_contact_request_status_constraint.py"
    )
    migration = sources.get(migration_path, "")
    _ordered_anchor_positions(
        migration,
        migration_path,
        [
            ("upgrade preflight", "def upgrade() -> None:\n    _preflight_statuses()"),
            (
                "constraint DDL",
                'with op.batch_alter_table("contact_requests") as batch_op:\n        batch_op.create_check_constraint',
            ),
        ],
        errors,
    )
    return errors


def _protocol_argument_contract_errors(root: Path) -> list[str]:
    """Bind extracted protocol parsers to one guarded module and its callers."""

    errors: list[str] = []
    protocol_path = "apps/api/app/protocol_arguments.py"
    protocol_source = _read_anchor_source(root, protocol_path, errors)
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)

    function_markers = {
        "canonical_agent_outreach_request_id": {
            "bounded UUID length": "len(value) != 36",
            "UUID normalization": "normalized = str(UUID(value))",
            "lowercase canonical equality": "if normalized != value:",
        },
        "protocol_search_arguments": {
            "agent capability selector": '"agent_capability",',
            "cursor blank/bounds guard": (
                "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048"
            ),
        },
        "protocol_profile_agents_arguments": {
            "cursor blank/bounds guard": (
                "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 500"
            ),
        },
        "protocol_agent_directory_arguments": {
            "cursor blank/bounds guard": (
                "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 500"
            ),
        },
        "protocol_agent_identity_argument": {
            "exact argument set": 'set(arguments) != {"agent_handle"}',
            "lowercase handle pattern": (
                're.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?", handle)'
            ),
        },
        "mcp_idempotency_argument": {
            "visible-ASCII key regex": "IDEMPOTENCY_KEY_RE.fullmatch(key)",
            "missing-key precondition": "status_code=428",
            "visible-ASCII key detail": (
                '"Idempotency-Key must contain 1-128 visible ASCII characters"'
            ),
        },
        "mcp_raw_markdown_is_bounded": {
            "UTF-8 byte length": 'len(value.encode("utf-8"))',
            "Unicode encoding rejection": "except UnicodeEncodeError",
            "configured byte cap": "1 <= byte_length <= max_upload_bytes",
        },
        "mcp_create_arguments": {
            "exact create argument set": (
                'set(arguments) != {"kind", "markdown", "idempotency_key"}'
            ),
            "create idempotency delegation": "mcp_idempotency_argument(arguments)",
            "create UTF-8 bound": (
                "mcp_raw_markdown_is_bounded(markdown, max_upload_bytes=max_upload_bytes)"
            ),
        },
        "mcp_update_arguments": {
            "exact update argument set": (
                'set(arguments) != {"kind", "identifier", "markdown", "if_match", "idempotency_key"}'
            ),
            "strong If-Match regex": (
                "re.fullmatch(STRONG_DOCUMENT_ETAG_PATTERN, if_match)"
            ),
            "strong If-Match rejection": (
                '"if_match must be an exact strong document ETag"'
            ),
        },
        "mcp_list_my_documents_arguments": {
            "inventory schema rejection": (
                '"list_my_documents arguments do not match its advertised schema"'
            ),
            "inventory cursor bound": "not 1 <= len(cursor) <= 500",
        },
        "mcp_read_document_arguments": {
            "exact read argument set": 'set(arguments) != {"kind", "identifier"}',
            "read identifier bound": "not 1 <= len(identifier) <= 100",
        },
        "mcp_get_changes_arguments": {
            "exact change-feed argument set": (
                'set(arguments) - {"after_sequence", "limit"}'
            ),
            "change-feed integer guards": ("isinstance(after_sequence, bool)"),
            "change-feed limit bound": "not 1 <= limit <= 100",
        },
        "mcp_agent_outreach_arguments": {
            "outreach idempotency delegation": "mcp_idempotency_argument(arguments)",
            "outreach schema validation": "AgentOutreachCreate.model_validate(",
        },
        "mcp_agent_outreach_status_argument": {
            "exact status argument set": 'set(arguments) != {"request_id"}',
            "canonical status request ID": (
                'canonical_agent_outreach_request_id(arguments["request_id"])'
            ),
        },
    }
    function_sources: dict[str, str] = {}
    for function_name, markers in function_markers.items():
        function_source = _function_source(
            protocol_source, function_name, protocol_path, errors
        )
        function_sources[function_name] = function_source
        _require_source_markers(
            function_source,
            f"{protocol_path}#{function_name}",
            markers,
            errors,
        )
    unique_guards = (
        (
            "protocol_search_arguments",
            "cursor blank/bounds guard",
            "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048",
        ),
        (
            "protocol_profile_agents_arguments",
            "cursor blank/bounds guard",
            "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 500",
        ),
        (
            "protocol_agent_directory_arguments",
            "cursor blank/bounds guard",
            "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 500",
        ),
        (
            "mcp_idempotency_argument",
            "visible-ASCII key regex",
            "IDEMPOTENCY_KEY_RE.fullmatch(key)",
        ),
        (
            "mcp_raw_markdown_is_bounded",
            "UTF-8 byte length",
            'len(value.encode("utf-8"))',
        ),
        (
            "mcp_update_arguments",
            "strong If-Match regex",
            "re.fullmatch(STRONG_DOCUMENT_ETAG_PATTERN, if_match)",
        ),
    )
    for function_name, label, marker in unique_guards:
        function_source = function_sources[function_name]
        if function_source and function_source.count(marker) != 1:
            _error(
                errors,
                f"repository.anchors.{protocol_path}#{function_name}",
                f"must contain exactly one {label} marker; found {function_source.count(marker)}",
            )

    expected_imports = {
        "IDEMPOTENCY_KEY_PATTERN": "_IDEMPOTENCY_KEY_PATTERN",
        "IDEMPOTENCY_KEY_RE": "_IDEMPOTENCY_KEY_RE",
        "canonical_agent_outreach_request_id": "_canonical_agent_outreach_request_id",
        "mcp_agent_outreach_arguments": "mcp_agent_outreach_arguments",
        "mcp_agent_outreach_status_argument": "mcp_agent_outreach_status_argument",
        "mcp_create_arguments": "mcp_create_arguments",
        "mcp_get_changes_arguments": "mcp_get_changes_arguments",
        "mcp_list_my_documents_arguments": "mcp_list_my_documents_arguments",
        "mcp_read_document_arguments": "mcp_read_document_arguments",
        "mcp_update_arguments": "mcp_update_arguments",
        "protocol_agent_directory_arguments": "protocol_agent_directory_arguments",
        "protocol_agent_identity_argument": "protocol_agent_identity_argument",
        "protocol_profile_agents_arguments": "protocol_profile_agents_arguments",
        "protocol_search_arguments": "protocol_search_arguments",
    }
    try:
        main_tree = ast.parse(main_source)
    except SyntaxError:
        main_tree = None
    if main_tree is not None:
        imported: dict[str, list[str]] = {}
        for node in ast.walk(main_tree):
            if (
                not isinstance(node, ast.ImportFrom)
                or node.module != "app.protocol_arguments"
            ):
                continue
            for alias in node.names:
                imported.setdefault(alias.name, []).append(alias.asname or alias.name)
        for source_name, expected_binding in expected_imports.items():
            bindings = imported.get(source_name, [])
            if bindings != [expected_binding]:
                _error(
                    errors,
                    f"repository.anchors.{main_path}#protocol-imports",
                    f"must import {source_name!r} exactly once as {expected_binding!r}; found {bindings!r}",
                )

    main_call_markers = {
        "_canonical_agent_outreach_request_id": "_canonical_agent_outreach_request_id(",
        "mcp_agent_outreach_arguments": "mcp_agent_outreach_arguments(",
        "mcp_agent_outreach_status_argument": "mcp_agent_outreach_status_argument(",
        "mcp_create_arguments": "mcp_create_arguments(",
        "mcp_get_changes_arguments": "mcp_get_changes_arguments(",
        "mcp_list_my_documents_arguments": "mcp_list_my_documents_arguments(",
        "mcp_read_document_arguments": "mcp_read_document_arguments(",
        "mcp_update_arguments": "mcp_update_arguments(",
        "protocol_agent_directory_arguments": "protocol_agent_directory_arguments(",
        "protocol_agent_identity_argument": "protocol_agent_identity_argument(",
        "protocol_profile_agents_arguments": "protocol_profile_agents_arguments(",
        "protocol_search_arguments": "protocol_search_arguments(",
    }
    for binding, marker in main_call_markers.items():
        if main_source.count(marker) < 1:
            _error(
                errors,
                f"repository.anchors.{main_path}#protocol-call-wiring",
                f"is missing a call to extracted helper {binding!r}",
            )

    for legacy_marker in (
        "from app.main import",
        "import app.main",
        "from app.config import Settings",
        "from app.db import",
        "from app.storage import",
    ):
        if legacy_marker in protocol_source:
            _error(
                errors,
                f"repository.anchors.{protocol_path}",
                f"must not depend on runtime authority through {legacy_marker!r}",
            )
    return errors


def _agent_directory_search_contract_errors(
    features: list[Any], root: Path
) -> list[str]:
    errors: list[str] = []
    feature = next(
        (
            item
            for item in features
            if isinstance(item, dict)
            and item.get("id") == "agent-representation-outreach"
        ),
        None,
    )
    if not isinstance(feature, dict):
        return errors
    surfaces = feature.get("surfaces")
    api = surfaces.get("api") if isinstance(surfaces, dict) else None
    declared_routes = (
        set(api.get("routes", []))
        if isinstance(api, dict) and isinstance(api.get("routes"), list)
        else set()
    )
    expected_public_read_routes = {
        "GET /v1/agent-identities/{agent_handle}",
        "GET /v1/agent-directory",
        "GET /v1/profiles/{handle}/agent-identities",
    }
    missing_public_read_routes = expected_public_read_routes - declared_routes
    if missing_public_read_routes:
        _error(
            errors,
            "registry.features.agent-representation-outreach.surfaces.api.routes",
            "must declare public Agent Identity read routes: "
            + ", ".join(sorted(missing_public_read_routes)),
        )
    search = surfaces.get("search") if isinstance(surfaces, dict) else None
    if not isinstance(search, dict):
        _error(
            errors,
            "registry.features.agent-representation-outreach.surfaces.search",
            "must classify the agent directory search boundary",
        )
    else:
        if search.get("mode") != "excluded":
            _error(
                errors,
                "registry.features.agent-representation-outreach.surfaces.search.mode",
                "must be excluded because AgentIdentity is not indexed or a standalone search hit",
            )
        if search.get("fields") != []:
            _error(
                errors,
                "registry.features.agent-representation-outreach.surfaces.search.fields",
                "must be empty because agent-directory lookup is SQL-backed",
            )
    data = feature.get("data")
    if not isinstance(data, dict) or data.get("search_projection") != "excluded":
        _error(
            errors,
            "registry.features.agent-representation-outreach.data.search_projection",
            "must be excluded because AgentIdentity is absent from Meilisearch",
        )
    search_source = _read_anchor_source(root, "apps/api/app/services/search.py", errors)
    if "AgentIdentity" in search_source:
        _error(
            errors,
            "repository.agent_directory_search.apps/api/app/services/search.py",
            "must not project AgentIdentity into Meilisearch",
        )
    if "agent_capability" in search_source:
        _error(
            errors,
            "repository.agent_directory_search.apps/api/app/services/search.py",
            "must not pass the SQL-only agent capability selector to Meilisearch",
        )
    main_source = _read_anchor_source(root, "apps/api/app/main.py", errors)
    main_path = "apps/api/app/main.py"
    public_search_path = "apps/api/app/services/public_search.py"
    public_search_source = _read_anchor_source(root, public_search_path, errors)
    card_path = "apps/api/app/routes/agent_card.py"
    card_source = _read_anchor_source(root, card_path, errors)
    capabilities = _function_source(main_source, "capabilities", main_path, errors)
    single_read = _function_source(main_source, "get_agent_identity", main_path, errors)
    profile_inventory = _function_source(
        main_source, "list_profile_agent_identities", main_path, errors
    )
    mcp_tools = _function_source(main_source, "mcp_tools", main_path, errors)
    mcp_dispatcher = _function_source(main_source, "mcp", main_path, errors)
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    card = _function_source(card_source, "agent_card", card_path, errors)
    _require_source_markers(
        capabilities,
        f"{main_path}#capabilities",
        {
            "Agent Identity public endpoint": '"public_endpoint": "/v1/agent-identities/{handle}",',
            "Agent Identity directory endpoint": '"directory_endpoint": "/v1/agent-directory",',
            "Agent Identity profile inventory endpoint": '"profile_inventory_endpoint": "/v1/profiles/{handle}/agent-identities",',
            "capabilities Agent Identity MCP read-tool parity": '"agent_tools": [\n                    "get_agent_identity",\n                    "list_agent_directory",\n                    "list_profile_agents",\n                ],',
            "capabilities Agent Identity A2A read-action parity": '"a2a_actions": [\n                    "get_agent_identity",\n                    "list_agent_directory",\n                    "list_profile_agents",\n                ],',
        },
        errors,
    )
    _require_source_markers(
        single_read,
        f"{main_path}#get_agent_identity",
        {
            "single-read live identity lookup": "live = await live_agent_identity(session, agent_handle)",
            "single-read safe projection": "return agent_identity_response(identity, profile)",
        },
        errors,
    )
    single_read_route = _route_decorator(
        "GET /v1/agent-identities/{agent_handle}", main_source
    )
    if single_read_route is None:
        _error(
            errors,
            f"repository.agent_directory_search.{main_path}",
            "must expose the public single Agent Identity read route",
        )
    else:
        _require_source_markers(
            single_read_route,
            f"{main_path}#single-agent-identity-route",
            {"single-read response model": "response_model=AgentIdentityResponse"},
            errors,
        )
    _require_source_markers(
        profile_inventory,
        f"{main_path}#list_profile_agent_identities",
        {
            "bounded HTTP profile-agent handle": "handle: Annotated[str, Path(min_length=1, max_length=100)]",
            "normalized HTTP profile-agent handle": "normalized_handle = handle.strip()",
            "profile inventory shared authority": "await list_public_agent_identities(",
        },
        errors,
    )
    _require_source_markers(
        mcp_tools,
        f"{main_path}#mcp_tools",
        {
            "MCP single Agent Identity tool": '"name": "get_agent_identity",',
            "MCP global directory tool": '"name": "list_agent_directory",',
            "MCP profile inventory tool": '"name": "list_profile_agents",',
        },
        errors,
    )
    _require_source_markers(
        mcp_dispatcher,
        f"{main_path}#mcp",
        {
            "MCP single-read dispatch": 'elif name == "get_agent_identity":',
            "MCP single-read argument guard": "agent_handle = protocol_agent_identity_argument(arguments)",
            "MCP single-read live identity": "live = await live_agent_identity(session, agent_handle)",
            "MCP global directory dispatch": 'elif name == "list_agent_directory":',
            "MCP profile inventory dispatch": 'elif name == "list_profile_agents":',
        },
        errors,
    )
    _require_source_markers(
        a2a,
        f"{main_path}#a2a_send_message",
        {
            "A2A single-read action": 'if action == "get_agent_identity":',
            "A2A single-read argument guard": "agent_handle = protocol_agent_identity_argument(",
            "A2A single-read live identity": "live = await live_agent_identity(session, agent_handle)",
            "A2A global directory action": 'if action == "list_agent_directory":',
            "A2A profile inventory action": 'if action == "list_profile_agents":',
        },
        errors,
    )
    _require_source_markers(
        card,
        f"{card_path}#agent_card",
        {
            "Agent Card global directory skill": '"id": "discover-public-agents",',
            "Agent Card single-read example": '\'{"action":"get_agent_identity","agent_handle":"ada-agent"}\',',
            "Agent Card profile inventory skill": '"id": "list-profile-agents",',
            "profile-agent discovery-only caveat": "Discovery never authorizes contact or outreach.",
        },
        errors,
    )
    _require_source_markers(
        main_source,
        main_path,
        {
            "bounded selector": 'agent_capability: Literal["internal_contact_request"] | None',
            "protocol selector call wiring": "protocol_search_arguments(",
            "shared public directory helper": "async def list_public_agent_identities(",
            "protocol directory helper import": "protocol_agent_directory_arguments,",
            "bounded protocol directory call wiring": "protocol_agent_directory_arguments(",
        },
        errors,
    )
    _require_source_markers(
        public_search_source,
        public_search_path,
        {
            "set-based enrichment helper": "async def enrich_public_search_hits(",
            "bounded reference helper": "async def search_agent_identity_references(",
            "chunk size": "_AGENT_IDENTITY_SEARCH_CHUNK_SIZE = 200",
            "per-profile cap": "_MAX_SEARCH_AGENT_IDENTITIES_PER_PROFILE = 10",
            "fixed public capability": '_INTERNAL_CONTACT_REQUEST_CAPABILITY: Literal["internal_contact_request"]',
            "candidate-window warning": "totals and completeness are bounded to that window",
        },
        errors,
    )
    projection_start = public_search_source.find(
        "    authoritative = {row.id: row for row in rows}"
    )
    projection_source = (
        public_search_source[projection_start:]
        if projection_start >= 0
        else public_search_source
    )
    positions = _ordered_anchor_positions(
        projection_source,
        f"{public_search_path}#projection-search",
        [
            (
                "canonical version authorization",
                'if document is None or hit.get("version") != document.current_version:',
            ),
            (
                "public hit sanitization",
                "safe_hits.append(sanitized_search_hit(hit, document))",
            ),
            (
                "live SQL enrichment",
                "safe_hits = await enrich_public_search_hits(\n        session,\n        safe_hits,",
            ),
        ],
        errors,
    )
    visibility_line = _document_visibility_guard_line(
        public_search_source, public_search_path, errors
    )
    canonical_position = positions.get("canonical version authorization")
    sanitization_position = positions.get("public hit sanitization")
    if (
        visibility_line is not None
        and canonical_position is not None
        and sanitization_position is not None
    ):
        canonical_line = (
            public_search_source.count("\n", 0, projection_start + canonical_position)
            + 1
        )
        sanitization_line = (
            public_search_source.count(
                "\n", 0, projection_start + sanitization_position
            )
            + 1
        )
        if not canonical_line < visibility_line < sanitization_line:
            _error(
                errors,
                f"repository.operations.{public_search_path}",
                "must reject non-public documents after version authorization and before hit sanitization",
            )
    schema_source = _read_anchor_source(root, "apps/api/app/schemas.py", errors)
    _require_source_markers(
        schema_source,
        "apps/api/app/schemas.py",
        {
            "bounded public reference": "class SearchAgentIdentityReference(BaseModel):",
            "additive hit references": "agent_identities: list[SearchAgentIdentityReference]",
            "reference cap": "default_factory=list, max_length=10",
        },
        errors,
    )
    tests_source = _read_anchor_source(
        root, "apps/api/tests/test_agent_identity_directory.py", errors
    )
    _require_source_markers(
        tests_source,
        "apps/api/tests/test_agent_identity_directory.py",
        {
            "eligible enrichment": "agent_capability",
            "identity reference": "agent_identities",
            "HTTP MCP A2A directory parity": "test_global_agent_directory_http_mcp_a2a_parity_and_privacy",
            "HTTP MCP A2A single-read parity": "test_single_public_agent_identity_http_mcp_a2a_parity_and_safe_errors",
            "privacy allowlist": '"owner_id",',
            "profile-agent oversized bound parity": 'oversized_handle = "x" * 101',
            "profile-agent whitespace normalization": '"/v1/profiles/%20protocol-owner%20/agent-identities"',
        },
        errors,
    )
    protocol_tests = _read_anchor_source(
        root, "apps/api/tests/test_protocol_core.py", errors
    )
    _require_source_markers(
        protocol_tests,
        "apps/api/tests/test_protocol_core.py",
        {
            "directory Agent Card skill": '"discover-public-agents",',
            "directory MCP tool schema": 'tool_by_name["list_agent_directory"]["inputSchema"]',
            "directory capability parity": 'capabilities.json()["agent_identities"]["agent_tools"]',
            "profile-agent discovery caveat test": '"never authorizes contact or outreach" in profile_agents_skill["description"]',
        },
        errors,
    )
    return errors


def _recruiting_evidence_surface_errors(
    root: Path,
    route_ownership: dict[str, str],
    route_inventory: _RouteInventory | None = None,
) -> list[str]:
    """Bind private recruiting evidence and artifact durability to source evidence."""

    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    health_path = "apps/api/app/routes/health.py"
    health_source = _read_anchor_source(root, health_path, errors)
    evidence_route = (
        "GET /v1/internal/recruiting-verifications/{verification_id}/evidence"
    )
    if route_ownership.get(evidence_route) != "verified-recruitment":
        _error(
            errors,
            "route_registry.routes",
            f"must map {evidence_route!r} to 'verified-recruitment'",
        )
    route_source = route_inventory or main_source
    if not _route_exists(evidence_route, route_source):
        _error(
            errors,
            f"repository.anchors.{main_path}",
            f"does not implement {evidence_route!r}",
        )
    elif not _route_is_hidden_from_openapi(evidence_route, route_source):
        _error(
            errors,
            f"repository.anchors.{main_path}",
            "recruiting evidence route must remain include_in_schema=False",
        )
    _require_source_markers(
        main_source,
        f"{main_path}#recruiting-evidence",
        {
            "private review headers": '"Cache-Control": "no-store, private"',
            "reviewer authority role": '"recruiting_verifier"',
            "artifact durability service": "from app.services.artifact_durability import",
        },
        errors,
    )
    reviewer = _function_source(
        main_source,
        "require_configured_verification_reviewer",
        main_path,
        errors,
    )
    _require_source_markers(
        reviewer,
        f"{main_path}#require_configured_verification_reviewer",
        {
            "Clerk-only authority": 'principal.method != "clerk_jwt"',
            "impersonation rejection": "principal.is_impersonated",
            "configured reviewer binding": "authority.verification_reviewer_id",
            "reviewer role check": "authority.verification_reviewer_role",
            "constant-time subject check": "compare_digest(principal.subject",
        },
        errors,
    )
    verified = _function_source(
        main_source, "verified_reviewer_evidence", main_path, errors
    )
    _require_source_markers(
        verified,
        f"{main_path}#verified_reviewer_evidence",
        {
            "service verification": "verify_recruiting_evidence(",
            "sanitized unavailable response": 'detail="verification evidence is unavailable"',
            "private headers on failure": "headers=verification_review_headers()",
        },
        errors,
    )
    route_function = _function_source(
        main_source, "read_recruiting_verification_evidence", main_path, errors
    )
    _require_source_markers(
        route_function,
        f"{main_path}#read_recruiting_verification_evidence",
        {
            "reviewer gate": "require_configured_verification_reviewer(",
            "verified artifact": "verified_reviewer_evidence(",
            "content length": '"Content-Length": str(verified.artifact_size_bytes)',
            "strong ETag": '"ETag": strong_etag(verified.artifact_sha256)',
            "content digest": '"Content-Digest": f"sha-256=:{artifact_digest}:"',
            "nosniff": '"X-Content-Type-Options": "nosniff"',
            "sandbox": '"Content-Security-Policy": "sandbox"',
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(
            root, "apps/api/tests/test_recruiting_verification_evidence.py", errors
        ),
        "apps/api/tests/test_recruiting_verification_evidence.py",
        {
            "private reviewer route test": "test_private_reviewer_reads_are_authorized_bounded_and_hidden",
            "private response headers": "assert_private_headers(response)",
            "hidden OpenAPI route": 'path.startswith("/v1/internal/recruiting-verifications")',
            "discovery exclusion": '"/.well-known/agent-card.json"',
            "artifact corruption test": "test_public_recruiting_surfaces_fail_closed_after_artifact_corruption",
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(
            root, "apps/api/app/services/recruiting_evidence.py", errors
        ),
        "apps/api/app/services/recruiting_evidence.py",
        {
            "bounded artifact size": "VERIFICATION_ARTIFACT_MAX_BYTES = 262_144",
            "canonical artifact path": "def canonical_evidence_path(",
            "verified bytes": "store.read_verified_bytes(",
            "material claim digest": "material_claim_digest(",
            "review snapshot digest": "def review_snapshot_sha256(",
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(root, "apps/web/lib/recruiting-evidence-api.ts", errors),
        "apps/web/lib/recruiting-evidence-api.ts",
        {
            "subject-bound read": "withSubjectBoundToken",
            "no-store read": 'cache: "no-store"',
            "private cache guard": "requirePrivateNoStore(response.headers)",
            "forbidden private fields": "FORBIDDEN_DETAIL_FIELDS",
            "exact evidence URL": "expectedEvidenceUrl",
            "artifact digest verification": '"content-digest"',
        },
        errors,
    )
    viewer_source = _read_anchor_source(
        root, "apps/web/components/verification-evidence-viewer.tsx", errors
    )
    _require_source_markers(
        viewer_source,
        "apps/web/components/verification-evidence-viewer.tsx",
        {
            "viewer loader": "loadReviewerEvidence",
            "explicit load control": "onClick={() => void load()}",
            "object URL lifecycle": "URL.createObjectURL",
            "object URL cleanup": "URL.revokeObjectURL",
            "attestation copy": "I reviewed this exact evidence and its displayed organization claims.",
        },
        errors,
    )
    if "dangerouslySetInnerHTML" in viewer_source:
        _error(
            errors,
            "repository.anchors.apps/web/components/verification-evidence-viewer.tsx",
            "must not render reviewer evidence with dangerouslySetInnerHTML",
        )
    _require_source_markers(
        _read_anchor_source(
            root, "apps/web/components/verification-review-queue.tsx", errors
        ),
        "apps/web/components/verification-review-queue.tsx",
        {
            "evidence readiness state": "evidenceReady",
            "explicit evidence prerequisite": "Load, verify, and attest the current private evidence before recording this decision.",
            "review ETag binding": "reviewEtag: evidenceReady.reviewEtag",
            "evidence reset": "clearEvidenceReview()",
            "subject-bound viewer": "subject={subject}",
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(root, "apps/web/app/verification-review/page.tsx", errors),
        "apps/web/app/verification-review/page.tsx",
        {"noindex review page": "robots: { index: false, follow: false }"},
        errors,
    )

    _require_source_markers(
        _read_anchor_source(
            root, "apps/api/app/services/artifact_durability.py", errors
        ),
        "apps/api/app/services/artifact_durability.py",
        {
            "evidence artifact flow": '"organization_verification_evidence"',
            "intent derivation": "def derive_artifact_intent_uuid(",
            "staging": "def stage_artifact(",
            "intent lock": "async def acquire_artifact_intent_lock(",
            "reconciler": "class ArtifactReconciler:",
            "descriptor reconciliation": "async def reconcile_descriptor(",
        },
        errors,
    )
    commit = _function_source(
        main_source, "commit_artifact_transaction", main_path, errors
    )
    _require_source_markers(
        commit,
        f"{main_path}#commit_artifact_transaction",
        {
            "receipt insert": "session.add(",
            "flush before commit": "await session.flush()",
            "commit": "await session.commit()",
            "rollback cleanup": "clear_application_snapshot_rollback_cleanup(session, cleanup)",
            "commit-failure reconciliation": "return await reconcile_commit_failure(",
        },
        errors,
    )
    _require_source_markers(
        main_source,
        f"{main_path}#artifact-readiness",
        {
            "artifact intent gate": "await request.app.state.artifact_reconciler.acquire_intent_gate(verification_id)",
            "artifact row lock": "await acquire_artifact_intent_lock(session, verification_id)",
        },
        errors,
    )
    _require_source_markers(
        health_source,
        f"{health_path}#artifact-readiness",
        {"readiness failure": '"reconciliation_unavailable"'},
        errors,
    )
    _require_source_markers(
        _read_anchor_source(
            root, "apps/api/alembic/versions/0006_organization_verification.py", errors
        ),
        "apps/api/alembic/versions/0006_organization_verification.py",
        {
            "evidence table": '"organization_verification_evidence"',
            "bounded evidence size": 'name="ck_organization_verification_evidence_size"',
            "unique verification evidence": 'name="uq_organization_verification_evidence_verification"',
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(
            root, "apps/api/alembic/versions/0007_retention_executor.py", errors
        ),
        "apps/api/alembic/versions/0007_retention_executor.py",
        {
            "evidence retention backfill": '"organization_verification_evidence"',
            "retention expiry": '"retention_expires_at"',
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(root, "apps/api/tests/test_artifact_durability.py", errors),
        "apps/api/tests/test_artifact_durability.py",
        {
            "precommit cleanup test": "test_precommit_verification_failure_leaves_no_file_or_graph",
            "unknown stage readiness test": "test_pending_shaped_unknown_stage_is_preserved_and_blocks_readiness",
            "reconciler readiness test": "test_reconciler_enablement_and_readiness_follow_attempted_scan",
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(root, "apps/api/tests/test_migrations.py", errors),
        "apps/api/tests/test_migrations.py",
        {
            "evidence migration test": "test_0006_organization_verification_schema_is_append_only_and_bounded",
            "retention migration test": "test_0007_retention_executor_schema_has_durable_worker_controls",
        },
        errors,
    )
    return errors


def _verification_event_scrub_errors(root: Path) -> list[str]:
    """Bind recruiting ChangeEvent evidence scrubbing to migration 0028."""

    errors: list[str] = []
    migration_path = (
        "apps/api/alembic/versions/0028_scrub_verification_change_payloads.py"
    )
    migration_source = _read_anchor_source(root, migration_path, errors)
    _require_source_markers(
        migration_source,
        migration_path,
        {
            "revision": 'revision: str = "0028_scrub_verification_change_payloads"',
            "parent revision": 'down_revision: str | None = "0027_application_snapshot_size"',
            "state-only sanitized payload": '_SANITIZED_PAYLOAD = json.dumps({"state": "submitted"}, sort_keys=True)',
            "duplicate-key guard": "def _unique_object(pairs: list[tuple[str, object]])",
            "duplicate-key rejection": 'raise ValueError("duplicate JSON key")',
        },
        errors,
    )
    validated = _function_source(
        migration_source,
        "_validated_sanitized_payload",
        migration_path,
        errors,
    )
    _require_source_markers(
        validated,
        f"{migration_path}#_validated_sanitized_payload",
        {
            "strict string input": "if not isinstance(payload, str):",
            "duplicate-key JSON parse": "object_pairs_hook=_unique_object",
            "state-only input": 'if parsed == {"state": "submitted"}:',
            "digest-bound input": 'set(parsed) == {"artifact_sha256", "state"}',
            "digest syntax": '_SHA256_HEX.fullmatch(parsed["artifact_sha256"]) is not None',
            "malformed rejection": 'raise RuntimeError("organization verification change payload is not canonical")',
        },
        errors,
    )
    upgrade = _function_source(migration_source, "upgrade", migration_path, errors)
    _require_source_markers(
        upgrade,
        f"{migration_path}#upgrade",
        {
            "verification resource predicate": "resource_type = 'organization_verification'",
            "submitted event predicate": "event_type = 'organization_verification.submitted'",
            "sequence cursor predicate": "AND sequence > :last_sequence",
            "ordered cursor": "ORDER BY sequence",
            "bounded batch": "LIMIT :batch_size",
            "cursor progression": 'last_sequence = int(row["sequence"])',
            "validated source payload": 'sanitized = _validated_sanitized_payload(row["payload"])',
            "canonical update": "UPDATE change_events SET payload = :payload WHERE sequence = :sequence",
        },
        errors,
    )
    downgrade = _function_source(migration_source, "downgrade", migration_path, errors)
    _require_source_markers(
        downgrade,
        f"{migration_path}#downgrade",
        {
            "irreversible privacy boundary": "Privacy minimization is intentionally irreversible.",
            "no downgrade rewrite": "pass",
        },
        errors,
    )

    migrations_test_path = "apps/api/tests/test_migrations.py"
    migrations_test_source = _read_anchor_source(root, migrations_test_path, errors)
    _require_source_markers(
        migrations_test_source,
        migrations_test_path,
        {
            "scrub behavior test": "test_0028_scrubs_only_canonical_verification_evidence_commitments",
            "malformed payload test": "test_0028_rejects_malformed_target_payload_without_rewriting_it",
            "scrubbed payload assertion": 'assert payloads["verification-old-0028"] == sanitized_payload',
            "malformed preservation assertion": 'assert preserved["verification-malformed-0028"] == malformed_payload',
        },
        errors,
    )
    return errors


def _public_trust_surface_errors(
    root: Path, ui_route_ownership: dict[str, str]
) -> list[str]:
    """Keep the public trust page descriptive, not a policy or authority surface."""

    errors: list[str] = []
    route = "/trust"
    if ui_route_ownership.get(route) != "public-search":
        _error(
            errors,
            "ui_route_registry.routes",
            f"must map {route!r} to 'public-search'",
        )
    page_path = "apps/web/app/trust/page.tsx"
    _require_source_markers(
        _read_anchor_source(root, page_path, errors),
        page_path,
        {
            "canonical public route": 'alternates: { canonical: "/trust" }',
            "plain-language current behavior": (
                "plain-language description of current product visibility, not a legal privacy policy"
            ),
            "no legal retention promise": (
                "does not set legal terms or promise a retention or deletion outcome"
            ),
            "no agent authority escalation": (
                "Finding a profile or Agent Identity does not grant contact, publishing, application, or maintenance authority"
            ),
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(root, "apps/web/tests/public-trust.test.ts", errors),
        "apps/web/tests/public-trust.test.ts",
        {
            "canonical route assertion": 'expect(metadata.alternates).toEqual({ canonical: "/trust" });',
            "plain-language assertion": "rather than an invented legal policy",
            "authority boundary assertion": "does not grant contact, publishing, application, or maintenance authority",
        },
        errors,
    )
    return errors


def _current_platform_surface_errors(
    root: Path,
    features: list[Any],
    route_ownership: dict[str, str],
    ui_route_ownership: dict[str, str],
    route_inventory: _RouteInventory | None = None,
) -> list[str]:
    """Bind the current cross-surface release anchors without claiming runtime proof."""
    errors: list[str] = []

    def source(path: str) -> str:
        return _read_anchor_source(root, path, errors)

    exact = source("apps/api/app/services/exact_search.py")
    _require_source_markers(
        exact,
        "apps/api/app/services/exact_search.py",
        {
            "exact document ceiling": "EXACT_SEARCH_MAX_DOCUMENTS = 50_000",
            "materialization guard": "EXACT_SEARCH_MATERIALIZATION_LIMIT = EXACT_SEARCH_MAX_DOCUMENTS + 1",
            "signed cursor bound": "EXACT_SEARCH_CURSOR_MAX_LENGTH = 2048",
            "PostgreSQL gate": 'if require_postgresql and session.get_bind().dialect.name != "postgresql":',
            "ready state gate": "await self.require_ready(session, require_postgresql=True)",
            "locked backfill": "async def backfill(",
            "integrity verification": "async def verify_integrity(",
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/app/cli.py"),
        "apps/api/app/cli.py",
        {
            "exact CLI": "async def run_exact_search(args: Namespace)",
            "exact backfill/verify parser": 'exact_search = commands.add_parser("exact-search")',
            "exact CLI dispatch": 'if args.command == "exact-search":',
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/tests/test_exact_search.py"),
        "apps/api/tests/test_exact_search.py",
        {
            "no Meilisearch fallback test": "test_exact_search_never_falls_back_to_meili_when_unready_or_non_postgresql",
            "cursor tamper test": "test_exact_search_cursor_is_signed_bounded_and_rejects_tampering",
            "PostgreSQL integrity test": "test_exact_verify_rejects_corrupt_tsvector_on_postgresql_only",
        },
        errors,
    )
    exact_search_source = source("apps/api/app/services/public_search.py")
    _require_source_markers(
        exact_search_source,
        "apps/api/app/services/public_search.py#exact-search",
        {
            "exact mode branch": 'mode == "exact"',
            "exact service call": "await request.app.state.exact_search.search(",
            "exact response mode": 'mode="exact"',
        },
        errors,
    )
    release_lib = source("infra/scripts/lib.sh")
    _require_source_markers(
        release_lib,
        "infra/scripts/lib.sh#release-receipt-v2",
        {
            "receipt v2": "connectmd-release-acceptance-v2",
            "evidence v2": "connectmd-release-acceptance-evidence-v2",
            "exact evidence binding": "exact_search_sha256",
        },
        errors,
    )
    _require_source_markers(
        source("infra/scripts/release-accept.sh"),
        "infra/scripts/release-accept.sh#release-receipt-v2",
        {
            "evidence format": "format=connectmd-release-acceptance-evidence-v2",
            "exact digest": "exact_search_sha256",
        },
        errors,
    )
    _require_source_markers(
        source("infra/tests/operational-contracts.py"),
        "infra/tests/operational-contracts.py#release-receipt-v2",
        {
            "receipt v2 test": 'assert "connectmd-release-acceptance-v2" in library',
            "exact digest test": 'assert "exact_search_sha256" in release_accept',
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/app/auth.py"),
        "apps/api/app/auth.py#jwks",
        {
            "unknown-kid cooldown": "_JWKS_UNKNOWN_KID_COOLDOWN_SECONDS = 30.0",
            "generation refresh": "async def _refresh_for_unknown_kid(",
            "refresh lock": "async with self._lock:",
            "retry deadline": "self._unknown_kid_retry_after",
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/tests/test_auth.py"),
        "apps/api/tests/test_auth.py#jwks",
        {
            "sequential cooldown test": "test_clerk_verifier_bounds_sequential_unknown_kid_refreshes",
            "concurrent coalescing test": "test_clerk_verifier_coalesces_concurrent_unknown_kid_refreshes",
            "rotation-after-cooldown test": "test_clerk_verifier_accepts_rotation_after_unknown_kid_cooldown",
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/app/main.py"),
        "apps/api/app/main.py#trusted-proxy",
        {
            "trusted source": '"allowlisted_source": "172.31.254.2"',
            "rightmost-untrusted": '"rightmost_untrusted": True',
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/app/routes/discovery.py"),
        "apps/api/app/routes/discovery.py#trusted-proxy",
        {
            "topology contract": "Forwarded client-IP headers are trusted only through the configured singleton reverse-proxy contract",
        },
        errors,
    )
    _require_source_markers(
        source("apps/web/app/workspace/page.tsx"),
        "apps/web/app/workspace/page.tsx",
        {"private robots metadata": "index: false", "workspace page": "WorkspaceHub"},
        errors,
    )
    _require_source_markers(
        source("apps/web/components/workspace-hub.tsx"),
        "apps/web/components/workspace-hub.tsx",
        {
            "workspace navigation": "WORKSPACE_NAVIGATION",
            "no private data fetch": "This page does not load account records",
            "destination authorization": "Access remains destination-specific",
        },
        errors,
    )
    _require_source_markers(
        source("apps/web/tests/workspace-hub.test.ts"),
        "apps/web/tests/workspace-hub.test.ts",
        {
            "workspace privacy test": "expect(hub).not.toMatch",
            "workspace navigation test": "Private workspace navigation",
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/app/main.py"),
        "apps/api/app/main.py#recent-changes-and-notifications",
        {
            "recent changes route": '"/v1/changes/recent"',
            "application notification helper": "add_notification(",
            "applicant-only notification": "recipient_owner_id=row.applicant_owner_id",
            "status-only notification type": 'type=f"application.{row.status}"',
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/tests/test_protocol_core.py"),
        "apps/api/tests/test_protocol_core.py#recent-changes",
        {
            "recent changes test": "test_recent_changes_is_human_bounded_descending_and_separate_from_sync_feed"
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/tests/test_application_decision_durability.py"),
        "apps/api/tests/test_application_decision_durability.py#notifications",
        {
            "notification count": "assert len(notifications) == 3",
            "notification privacy": '"private application message" not in str(notification.__dict__)',
        },
        errors,
    )
    _require_source_markers(
        source("apps/web/lib/agent-api.ts"),
        "apps/web/lib/agent-api.ts#recent-changes",
        {
            "recent changes endpoint": 'recentChanges: "/v1/changes/recent"',
            "audit helper": "export async function listDelegationAudit",
        },
        errors,
    )

    _require_source_markers(
        source("apps/api/app/protocol_arguments.py"),
        "apps/api/app/protocol_arguments.py#mcp-document-inventory",
        {"inventory parser": "def mcp_list_my_documents_arguments("},
        errors,
    )
    _require_source_markers(
        source("apps/api/app/main.py"),
        "apps/api/app/main.py#mcp-document-inventory",
        {
            "inventory scope": '"scope": "my_documents"',
            "inventory dispatch": 'elif name == "list_my_documents":',
            "inventory schema": '"name": "list_my_documents",',
            "inventory parser call": "mcp_list_my_documents_arguments(",
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/tests/test_protocol_core.py"),
        "apps/api/tests/test_protocol_core.py#mcp-document-inventory",
        {
            "inventory pagination": '"name": "list_my_documents", "arguments": {"limit": 1}',
            "inventory schema test": 'tool_by_name["list_my_documents"]["inputSchema"]',
        },
        errors,
    )

    _require_source_markers(
        source("apps/api/app/main.py"),
        "apps/api/app/main.py#public-organization-job-inventory",
        {
            "public organization route": '"/v1/organizations"',
            "public job route": '"/v1/jobs"',
            "organization cursor scope": '"scope": "organizations"',
            "job cursor scope": '"scope": "jobs"',
            "organization ordering": "Organization.updated_at.desc()",
            "job ordering": "Job.updated_at.desc()",
            "limit plus one": ".limit(limit + 1)",
            "verification join": "active_recruiting_verification_from_join(",
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/tests/test_social_core.py"),
        "apps/api/tests/test_social_core.py#public-organization-job-inventory",
        {
            "set-based verification test": "test_public_inventory_uses_set_based_verification_and_limit_plus_one",
            "duplicate evidence test": "test_public_inventory_deduplicates_duplicate_evidence_join_rows",
        },
        errors,
    )

    _require_source_markers(
        source("apps/api/app/main.py"),
        "apps/api/app/main.py#public-post-inventory",
        {
            "public post route": '"/v1/posts"',
            "chronological ordering": "Post.published_at.desc()",
            "public post cursor": '"scope": "public_posts"',
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/app/routes/discovery.py"),
        "apps/api/app/routes/discovery.py#public-post-inventory",
        {
            "metadata-only contract": "never indexes post Markdown bodies in Meilisearch",
        },
        errors,
    )
    _require_source_markers(
        source("apps/api/tests/test_public_post_inventory.py"),
        "apps/api/tests/test_public_post_inventory.py",
        {
            "metadata chronology": "test_public_post_inventory_is_metadata_only_chronological_and_not_search_backed",
            "cursor progression": "test_public_post_inventory_cursor_is_strict_and_raw_candidate_progresses",
            "integrity fail-closed": "test_public_inventory_fails_the_whole_page_for_missing_or_corrupt_canonical_bytes",
        },
        errors,
    )
    _require_source_markers(
        source("apps/web/app/sitemap.ts"),
        "apps/web/app/sitemap.ts#public-post-sitemap",
        {
            "four category source": "const sitemapCategoryIds = [0, 1, 2, 3] as const;",
            "Next runtime category normalization": 'const categoryId = typeof id === "string" && /^[0-3]$/.test(id) ? Number(id) : id;',
            "post category": "if (categoryId === 3) return collectPostSitemap();",
            "post reader": "listPublicPostsOnServer(200, cursor)",
        },
        errors,
    )
    _require_source_markers(
        source("apps/web/tests/sitemap.test.ts"),
        "apps/web/tests/sitemap.test.ts#public-post-sitemap",
        {
            "four category test": "toEqual([{ id: 0 }, { id: 1 }, { id: 2 }, { id: 3 }])",
            "category three test": "publishes chronological public post HTML URLs from every successful category 3 page",
            "category three fail-closed": "fails category 3 closed on a later request failure, duplicate post, or cursor loop",
        },
        errors,
    )

    expected_routes = {
        "GET /v1/changes/recent": "agent-authority",
        "GET /v1/posts": "professional-posts-moderation",
    }
    for route, owner in expected_routes.items():
        if route_ownership.get(route) != owner:
            _error(errors, "route_registry.routes", f"must map {route!r} to {owner!r}")
    expected_ui_routes = {
        "/appeal-review": "professional-posts-moderation",
        "/moderation-review": "professional-posts-moderation",
        "/workspace": "private-workspace-navigation",
    }
    for route, owner in expected_ui_routes.items():
        if ui_route_ownership.get(route) != owner:
            _error(
                errors, "ui_route_registry.routes", f"must map {route!r} to {owner!r}"
            )

    _require_source_markers(
        source("apps/api/app/main.py"),
        "apps/api/app/main.py#moderation-review-http",
        {
            "case queue": '"/v1/internal/post-moderation/cases"',
            "case detail": '"/v1/internal/post-moderation/cases/{case_id}"',
            "case decision": '"/v1/internal/post-moderation/cases/{case_id}/decision"',
            "appeal queue": '"/v1/internal/post-moderation/appeals"',
            "appeal detail": '"/v1/internal/post-moderation/appeals/{appeal_id}"',
            "appeal decision": '"/v1/internal/post-moderation/appeals/{appeal_id}/decision"',
            "hidden OpenAPI": "include_in_schema=False",
            "reviewer authority": "require_configured_moderation_reviewer(",
            "reviewer no-store headers": "moderation_review_headers()",
        },
        errors,
    )
    moderation_routes = (
        "GET /v1/internal/post-moderation/cases",
        "GET /v1/internal/post-moderation/cases/{case_id}",
        "POST /v1/internal/post-moderation/cases/{case_id}/decision",
        "GET /v1/internal/post-moderation/appeals",
        "GET /v1/internal/post-moderation/appeals/{appeal_id}",
        "POST /v1/internal/post-moderation/appeals/{appeal_id}/decision",
    )
    moderation_source = route_inventory or source("apps/api/app/main.py")
    for route in moderation_routes:
        if not _route_is_hidden_from_openapi(route, moderation_source):
            _error(
                errors,
                "repository.anchors.apps/api/app/main.py#moderation-review-http",
                f"moderation route {route!r} must remain include_in_schema=False",
            )
    _require_source_markers(
        source("apps/web/lib/moderation-review-api.ts"),
        "apps/web/lib/moderation-review-api.ts#moderation-review-http",
        {
            "case queue helper": "listModerationReviewCases",
            "appeal queue helper": "listModerationReviewAppeals",
            "strict decision 204": "assertEmptyDecisionResponse",
            "subject-bound token": "withSubjectBoundToken",
        },
        errors,
    )
    _require_source_markers(
        source("apps/web/tests/moderation-review-api.test.ts"),
        "apps/web/tests/moderation-review-api.test.ts#moderation-review-http",
        {
            "reviewer HTTP contract": "private moderation reviewer HTTP contract",
            "no-store read test": "uses Clerk-human subject binding, no-store reads, bounded cursors, and exact queue parsers",
            "malformed decision test": "rejects malformed decision receipts and duplicate queue identifiers",
        },
        errors,
    )
    return errors


def _cursor_contract_errors(root: Path) -> list[str]:
    """Bind optional cursor validation and continuation evidence across transports."""
    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    taxonomy_path = "apps/api/app/routes/taxonomy.py"
    taxonomy_source = _read_anchor_source(root, taxonomy_path, errors)
    protocol_path = "apps/api/app/protocol_arguments.py"
    protocol_source = _read_anchor_source(root, protocol_path, errors)

    for function_name, maximum, label in (
        ("search", 2048, "exact/public search"),
        ("list_agent_directory", 500, "global Agent Directory"),
        ("list_profile_agent_identities", 500, "profile Agent Directory"),
    ):
        function_source = _function_source(
            main_source, function_name, main_path, errors
        )
        _require_source_markers(
            function_source,
            f"{main_path}#{function_name}",
            {
                f"{label} OpenAPI cursor bound": (
                    "cursor: Annotated[str | None, "
                    f"Query(min_length=1, max_length={maximum})] = None"
                ),
                f"{label} duplicate cursor rejection": (
                    "reject_duplicate_cursor_query_parameter(request)"
                ),
            },
            errors,
        )
    taxonomy_terms = _function_source(
        taxonomy_source, "list_taxonomy_terms", taxonomy_path, errors
    )
    _require_source_markers(
        taxonomy_terms,
        f"{taxonomy_path}#list_taxonomy_terms",
        {
            "taxonomy terms OpenAPI cursor bound": (
                "cursor: Annotated[str | None, "
                "Query(min_length=1, max_length=2048)] = None"
            ),
            "taxonomy terms duplicate cursor rejection": (
                "reject_duplicate_cursor_query_parameter(request)"
            ),
        },
        errors,
    )

    duplicate_cursor = _function_source(
        main_source, "reject_duplicate_cursor_query_parameter", main_path, errors
    )
    _require_source_markers(
        duplicate_cursor,
        f"{main_path}#reject_duplicate_cursor_query_parameter",
        {
            "duplicate cursor query guard": 'len(request.query_params.getlist("cursor")) > 1',
            "duplicate cursor validation error": 'detail="cursor accepts one value"',
        },
        errors,
    )

    for function_name, maximum, label in (
        ("protocol_search_arguments", 2048, "exact/public search"),
        ("protocol_profile_agents_arguments", 500, "profile Agent Directory"),
        ("protocol_agent_directory_arguments", 500, "global Agent Directory"),
    ):
        function_source = _function_source(
            protocol_source, function_name, protocol_path, errors
        )
        _require_source_markers(
            function_source,
            f"{protocol_path}#{function_name}",
            {
                f"{label} protocol blank/bounds guard": (
                    "not isinstance(cursor, str) or not cursor.strip() "
                    f"or len(cursor) > {maximum}"
                ),
            },
            errors,
        )
    mcp_tools = _function_source(main_source, "mcp_tools", main_path, errors)
    _require_source_markers(
        mcp_tools,
        f"{main_path}#mcp_tools",
        {
            "MCP taxonomy cursor schema": (
                '"cursor": {"type": "string", "minLength": 1, "maxLength": 2048},'
            ),
            "MCP directory cursor schema": (
                '"cursor": {"type": "string", "minLength": 1, "maxLength": 500},'
            ),
            "MCP taxonomy tool": '"name": "list_taxonomy_terms",',
            "MCP directory tools": '"name": "list_agent_directory",',
        },
        errors,
    )
    mcp = _function_source(main_source, "mcp", main_path, errors)
    _require_source_markers(
        mcp,
        f"{main_path}#mcp",
        {
            "MCP taxonomy dispatch": 'elif name == "list_taxonomy_terms":',
            "MCP taxonomy blank/bounds guard": (
                "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048"
            ),
            "MCP directory dispatch": 'elif name == "list_agent_directory":',
            "MCP profile dispatch": 'elif name == "list_profile_agents":',
        },
        errors,
    )
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    _require_source_markers(
        a2a,
        f"{main_path}#a2a_send_message",
        {
            "A2A taxonomy dispatch": 'if action == "list_taxonomy_terms":',
            "A2A taxonomy blank/bounds guard": (
                "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048"
            ),
            "A2A exact search delegation": "await protocol_public_search(",
            "A2A directory helper": "protocol_agent_directory_arguments(",
            "A2A profile helper": "protocol_profile_agents_arguments(",
        },
        errors,
    )

    cursor_decode = _function_source(
        main_source, "agent_directory_cursor_decode", main_path, errors
    )
    _require_source_markers(
        cursor_decode,
        f"{main_path}#agent_directory_cursor_decode",
        {
            "directory signed cursor verification": (
                "compare_digest(supplied_signature, expected_signature)"
            ),
        },
        errors,
    )
    directory_statement = _function_source(
        main_source, "agent_directory_statement", main_path, errors
    )
    _require_source_markers(
        directory_statement,
        f"{main_path}#agent_directory_statement",
        {
            "directory live eligibility predicate": (
                "where(*public_agent_identity_eligibility_filters())"
            ),
        },
        errors,
    )
    directory_loader = _function_source(
        main_source, "list_public_agent_identities", main_path, errors
    )
    _require_source_markers(
        directory_loader,
        f"{main_path}#list_public_agent_identities",
        {
            "directory cursor anchor lookup": "AgentIdentity.id == identity_id",
            "directory cursor anchor lock": ".with_for_update()",
            "directory cursor continuation": "AgentIdentity.created_at < created_at",
        },
        errors,
    )

    exact_tests = _read_anchor_source(
        root, "apps/api/tests/test_exact_search.py", errors
    )
    _require_source_markers(
        exact_tests,
        "apps/api/tests/test_exact_search.py#cursor-contract",
        {
            "exact cursor validation test": (
                "test_exact_empty_resolution_cannot_bypass_offset_or_cursor_validation"
            ),
            "exact duplicate cursor test": "duplicate_cursor_get.status_code == 422",
            "exact blank cursor test": "blank_cursor_get.status_code == expected_get_status",
            "exact schema cursor minimum": (
                'get_parameters["cursor"]["schema"]["anyOf"][0]["minLength"] == 1'
            ),
            "exact signed continuation test": "valid_get = await client.get(",
        },
        errors,
    )
    taxonomy_tests = _read_anchor_source(
        root, "apps/api/tests/test_protocol_core.py", errors
    )
    _require_source_markers(
        taxonomy_tests,
        "apps/api/tests/test_protocol_core.py#taxonomy-cursor-contract",
        {
            "taxonomy protocol parity test": (
                "test_mcp_and_a2a_taxonomy_list_share_public_registry"
            ),
            "taxonomy duplicate cursor test": "duplicate_http_cursor.status_code == 422",
            "taxonomy blank cursor test": 'f"taxonomy-blank-cursor-{index}"',
            "taxonomy A2A blank cursor test": 'f"taxonomy-blank-a2a-cursor-{index}"',
            "taxonomy signed continuation": "registry_cursor",
        },
        errors,
    )
    directory_tests = _read_anchor_source(
        root, "apps/api/tests/test_agent_identity_directory.py", errors
    )
    _require_source_markers(
        directory_tests,
        "apps/api/tests/test_agent_identity_directory.py#cursor-contract",
        {
            "directory HTTP parity test": (
                "test_agent_directory_is_public_bounded_and_hides_ineligible_identities"
            ),
            "directory duplicate cursor test": "duplicate_directory_cursor.status_code == 422",
            "profile duplicate cursor test": "duplicate_profile_cursor.status_code == 422",
            "directory MCP blank cursor test": 'f"global-directory-blank-mcp-{index}"',
            "directory A2A blank cursor test": 'f"global-directory-blank-a2a-{index}"',
            "profile MCP blank cursor test": 'f"profile-agents-blank-mcp-{index}"',
            "profile A2A blank cursor test": 'f"profile-agents-blank-a2a-{index}"',
            "directory signed continuation": "continued = await restarted_client.get(",
            "directory schema cursor minimum": (
                'parameters["cursor"]["schema"]["anyOf"][0]["minLength"] == 1'
            ),
        },
        errors,
    )
    return errors


def _taxonomy_public_search_contract_errors(
    features: list[Any],
    route_ownership: dict[str, str],
    model_owners: dict[str, str],
    root: Path,
) -> list[str]:
    """Require one taxonomy authority path for public HTTP and protocols.

    Operational sequencing belongs to infra/tests/operational-contracts.py. This
    guard only requires that existing contract keeps its taxonomy anchors,
    rather than reimplementing its shell-control semantics here.
    """
    errors: list[str] = []
    feature = next(
        (
            item
            for item in features
            if isinstance(item, dict) and item.get("id") == "public-search"
        ),
        None,
    )
    if not isinstance(feature, dict):
        return errors

    required_routes = {
        "GET /v1/taxonomies",
        "GET /v1/taxonomies/{taxonomy}",
        "POST /v1/search/query",
    }
    surfaces = feature.get("surfaces")
    api = surfaces.get("api") if isinstance(surfaces, dict) else None
    declared_routes = set(api.get("routes", [])) if isinstance(api, dict) else set()
    for route in sorted(required_routes):
        if route_ownership.get(route) != "public-search":
            _error(
                errors,
                "repository.taxonomy_public_search.route_ownership",
                f"{route!r} must be owned by 'public-search'",
            )
        if route not in declared_routes:
            _error(
                errors,
                "registry.features.public-search.surfaces.api.routes",
                f"must declare taxonomy/public-search route {route!r}",
            )

    required_models = {
        "PublicTaxonomyProjectionState",
        "PublicTaxonomyDocumentSnapshot",
        "PublicTaxonomyTerm",
        "PublicTaxonomyMembership",
    }
    for model in sorted(required_models):
        if model_owners.get(model) != "public-search":
            _error(
                errors,
                "registry.features.public-search.data.models",
                f"{model!r} must be owned by 'public-search'",
            )

    implementation = feature.get("implementation")
    implementation_paths = (
        set(implementation.get("paths", []))
        if isinstance(implementation, dict)
        else set()
    )
    required_service_paths = {"apps/api/app/services/public_search.py"}
    for relative_path in sorted(required_service_paths):
        if relative_path not in implementation_paths:
            _error(
                errors,
                "registry.features.public-search.implementation.paths",
                f"must declare public search service implementation {relative_path!r}",
            )
    required_web_paths = {
        "apps/web/components/search-experience.tsx",
        "apps/web/components/taxonomy-filter-panel.tsx",
        "apps/web/lib/public-search-api.ts",
        "apps/web/lib/taxonomy-api.ts",
        "apps/web/lib/taxonomy-search-state.ts",
    }
    for relative_path in sorted(required_web_paths):
        if relative_path not in implementation_paths:
            _error(
                errors,
                "registry.features.public-search.implementation.paths",
                f"must declare Human Mode taxonomy implementation {relative_path!r}",
            )

    declared_tests = set(feature.get("tests", []))
    required_web_tests = {
        "apps/web/tests/public-search-api.test.ts",
        "apps/web/tests/taxonomy-filter-panel.test.ts",
        "apps/web/tests/taxonomy-search-state.test.ts",
    }
    for relative_path in sorted(required_web_tests):
        if relative_path not in declared_tests:
            _error(
                errors,
                "registry.features.public-search.tests",
                f"must declare Human Mode taxonomy test {relative_path!r}",
            )

    api_path = "apps/api/app/main.py"
    api_source = _read_anchor_source(root, api_path, errors)
    public_search_path = "apps/api/app/services/public_search.py"
    public_search_source = _read_anchor_source(root, public_search_path, errors)
    taxonomy_path = "apps/api/app/routes/taxonomy.py"
    taxonomy_source = _read_anchor_source(root, taxonomy_path, errors)
    _require_source_markers(
        api_source,
        api_path,
        {
            "taxonomy router import": "from app.routes.taxonomy import router as taxonomy_router",
            "taxonomy router inclusion": "app.include_router(taxonomy_router)",
        },
        errors,
    )
    _require_source_markers(
        taxonomy_source,
        taxonomy_path,
        {
            "taxonomy router declaration": "router = APIRouter()",
            "taxonomy catalog route": '@router.get(\n    "/v1/taxonomies",',
            "taxonomy terms route": '@router.get(\n    "/v1/taxonomies/{taxonomy}",',
        },
        errors,
    )
    execute_public_search = _function_source(
        public_search_source, "execute_public_search", public_search_path, errors
    )
    _require_source_markers(
        execute_public_search,
        public_search_path,
        {
            "taxonomy filter resolution": "await request.app.state.taxonomy.resolve_search(",
            "taxonomy hit hydration": "await request.app.state.taxonomy.hydrate_hits(",
        },
        errors,
    )
    hydration_marker = "await request.app.state.taxonomy.hydrate_hits("
    if execute_public_search.count(hydration_marker) < 3:
        _error(
            errors,
            f"repository.anchors.{public_search_path}",
            "taxonomy hit hydration must cover exact hits, exact facet hits, and projection hits",
        )
    protocol_public_search = _function_source(
        api_source, "protocol_public_search", api_path, errors
    )
    _require_source_markers(
        protocol_public_search,
        api_path,
        {
            "shared public search delegation": "await execute_public_search(",
        },
        errors,
    )
    if "request.app.state.search.search" in protocol_public_search:
        _error(
            errors,
            f"repository.anchors.{api_path}",
            "protocol_public_search must delegate to execute_public_search instead of querying the projection directly",
        )
    test_markers = {
        "apps/api/tests/test_taxonomy.py": {
            "public taxonomy catalog and term-list test": "test_public_taxonomy_catalog_and_terms_are_ready",
            "compact worst-case taxonomy cursor replay test": "test_taxonomy_compact_cursor_replays_maximum_legal_unicode_query_and_labels",
        },
        "apps/api/tests/test_api.py": {
            "POST search taxonomy test": "test_post_search_query_uses_taxonomy_registry",
        },
        "apps/api/tests/test_protocol_core.py": {
            "MCP and A2A taxonomy list parity test": "test_mcp_and_a2a_taxonomy_list_share_public_registry",
            "MCP and A2A taxonomy search parity test": "test_mcp_and_a2a_search_share_taxonomy_registry",
            "MCP and A2A non-ready taxonomy parity test": "test_mcp_and_a2a_search_fail_closed_when_taxonomy_is_not_ready",
        },
    }
    for relative_path, markers in test_markers.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)

    web_markers = {
        "apps/web/lib/public-search-api.ts": {
            "raw taxonomy value rejection before fetch": "if (filters.invalidTypedValues.length > 0) throw new ApiRequestError(",
            "date-only Human Mode API normalization": 'if (updatedAfter) params.set("updated_after", updatedAfter);',
            "aggregate selection and facet budget": "const remaining = Math.max(0, 50 - countSearchRepeatedValues(filters));",
        },
        "apps/web/lib/public-search-contract.ts": {
            "authoritative taxonomy facets required": 'if (!Object.prototype.hasOwnProperty.call(record, "taxonomy_facets"))',
            "required scalar search response fields": 'if (!isPlainRecord(record.facets)) throw new Error("The API response returned invalid search facets.");',
        },
        "apps/web/components/search-experience.tsx": {
            "Human Mode taxonomy panel mount": "<TaxonomyFilterPanel filters={filters}",
        },
        "apps/web/components/taxonomy-filter-panel.tsx": {
            "representative evidence caveat": "this does not verify identity, mandate, organization authority, availability, consent, or contact permission",
            "invalid typed URL warning": "Legacy or raw typed URL values were not re-submitted",
        },
        "apps/web/tests/public-search-api.test.ts": {
            "alias-only URL fail-closed test": "round-trips alias filters while rejecting raw location and typed values before fetch",
            "aggregate cap and dynamic facets test": "preflights the aggregate repeated-value cap and requests only remaining facets",
            "date-only API wire normalization test": "normalizes a Human Mode date-only update filter for the API wire",
            "taxonomy facet fail-closed test": "fails closed when authoritative taxonomy facets are missing or malformed",
        },
        "apps/web/tests/taxonomy-search-state.test.ts": {
            "cursor and revision fail-closed test": "fails closed when a cursor repeats or a registry revision changes",
        },
        "apps/web/tests/taxonomy-filter-panel.test.ts": {
            "mounted panel and raw facet exclusion test": "mounts the taxonomy panel in the existing form and excludes raw typed legacy facets",
        },
    }
    for relative_path, markers in web_markers.items():
        source = _read_anchor_source(root, relative_path, errors)
        _require_source_markers(source, relative_path, markers, errors)

    operations_path = "infra/tests/operational-contracts.py"
    operational_contracts = _read_anchor_source(root, operations_path, errors)
    _require_source_markers(
        operational_contracts,
        operations_path,
        {
            "deploy taxonomy and exact-search backfill before verification before search rebuild": "< deploy_taxonomy_backfill\n    < deploy_taxonomy_verify\n    < deploy_exact_backfill\n    < deploy_exact_verify\n    < deploy_rebuild",
            "taxonomy recovery backfill before verification before search rebuild": "< taxonomy_rebuild_backfill\n    < taxonomy_rebuild_verify\n    < taxonomy_rebuild_search",
            "search recovery taxonomy verification before rebuild": "< rebuild_taxonomy_verify < rebuild_admin",
        },
        errors,
    )
    return errors


def _api_semantic_parity_errors(root: Path) -> list[str]:
    """Bind the final API-only parity fixes to their exact source/test scopes."""
    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    protocol_path = "apps/api/app/protocol_arguments.py"
    protocol_source = _read_anchor_source(root, protocol_path, errors)

    canonical_request_id = _function_source(
        protocol_source,
        "canonical_agent_outreach_request_id",
        protocol_path,
        errors,
    )
    _require_source_markers(
        canonical_request_id,
        f"{protocol_path}#canonical-agent-outreach-uuid",
        {
            "bounded UUID length": "len(value) != 36",
            "UUID normalization": "normalized = str(UUID(value))",
            "lowercase canonical equality": "if normalized != value:",
        },
        errors,
    )
    _require_source_markers(
        main_source,
        f"{main_path}#agent-outreach-status-parity",
        {
            "HTTP status route": '"/v1/agent-outreach/{request_id}"',
            "HTTP status handler": "async def get_agent_outreach_status(",
            "A2A status action": 'if action == "get_agent_outreach_status":',
            "MCP status parser import": "mcp_agent_outreach_status_argument,",
            "MCP status parser call": "mcp_agent_outreach_status_argument(",
        },
        errors,
    )
    a2a_source = _function_source(main_source, "a2a_send_message", main_path, errors)
    _require_source_markers(
        a2a_source,
        f"{main_path}#a2a-outreach-status",
        {
            "canonical UUID parser": '_canonical_agent_outreach_request_id(data.get("request_id"))',
            "canonical HTTP delegation": "await get_agent_outreach_status(",
        },
        errors,
    )
    mcp_status_source = _function_source(
        protocol_source,
        "mcp_agent_outreach_status_argument",
        protocol_path,
        errors,
    )
    _require_source_markers(
        mcp_status_source,
        f"{protocol_path}#mcp-outreach-status",
        {
            "exact argument set": 'set(arguments) != {"request_id"}',
            "canonical UUID parser": 'canonical_agent_outreach_request_id(arguments["request_id"])',
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(
            root, "apps/api/tests/test_agent_identity_mandates.py", errors
        ),
        "apps/api/tests/test_agent_identity_mandates.py#agent-outreach-status",
        {
            "HTTP and A2A parity test": "test_agent_outreach_status_is_exact_origin_private_and_a2a_equivalent",
            "canonical UUID assertion": 'status_operation["responses"]["200"]',
        },
        errors,
    )

    post_read_start = main_source.find(
        "_POST_READ_RESPONSES: dict[int | str, dict[str, Any]]"
    )
    post_markdown_start = main_source.find(
        "_POST_MARKDOWN_ONLY_RESPONSES: dict[int | str, dict[str, Any]]"
    )
    post_route_start = main_source.find('@app.get(\n        "/v1/posts/{post_id}.md"')
    if (
        post_read_start < 0
        or post_markdown_start <= post_read_start
        or post_route_start <= post_markdown_start
    ):
        _error(
            errors,
            f"repository.anchors.{main_path}#public-post-markdown-openapi",
            "must define post response maps before their public read routes",
        )
        post_read_responses = ""
        post_markdown_responses = ""
        post_routes = ""
    else:
        post_read_responses = main_source[post_read_start:post_markdown_start]
        post_markdown_responses = main_source[post_markdown_start:post_route_start]
        post_routes = main_source[post_route_start:]
    _require_source_markers(
        post_read_responses,
        f"{main_path}#public-post-json-response-map",
        {
            "post JSON/Markdown response map": "_POST_READ_RESPONSES: dict[int | str, dict[str, Any]]",
            "JSON/Markdown media type": 'content": {MARKDOWN_MEDIA_TYPE: {"schema": {"type": "string"}}}',
        },
        errors,
    )
    _require_source_markers(
        post_markdown_responses,
        f"{main_path}#public-post-markdown-response-map",
        {
            "post Markdown-only response map": "_POST_MARKDOWN_ONLY_RESPONSES: dict[int | str, dict[str, Any]]",
            "Markdown-only media type": 'content": {MARKDOWN_MEDIA_TYPE: {"schema": {"type": "string"}}}',
        },
        errors,
    )
    _require_source_markers(
        post_routes,
        f"{main_path}#public-post-markdown-routes",
        {
            "post Markdown route binding": "responses=_POST_MARKDOWN_ONLY_RESPONSES",
            "post JSON route binding": "responses=_POST_READ_RESPONSES",
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(
            root, "apps/api/tests/test_public_post_inventory.py", errors
        ),
        "apps/api/tests/test_public_post_inventory.py#public-post-markdown-openapi",
        {
            "post OpenAPI discovery test": "test_public_post_inventory_openapi_and_discovery_contract",
            "Markdown discovery assertion": 'assert "GET /v1/posts?limit=&cursor=" in body',
        },
        errors,
    )

    search_source = _function_source(main_source, "search", main_path, errors)
    _require_source_markers(
        search_source,
        f"{main_path}#search-location-id",
        {
            "scalar location annotation": "location_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None",
            "duplicate location guard": 'request.query_params.getlist("location_id")',
            "singleton rejection": 'detail="location_id accepts one value"',
            "normalized location forwarding": '"location_id": normalized_location_id',
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(root, "apps/api/tests/test_api.py", errors),
        "apps/api/tests/test_api.py#search-location-id",
        {
            "singleton location test": "test_search_kind_and_singleton_location_are_rechecked",
            "duplicate location request": '("location_id", "connect.md:one"), ("location_id", "connect.md:two")',
        },
        errors,
    )

    document_service_path = "apps/api/app/services/documents.py"
    document_service = _read_anchor_source(root, document_service_path, errors)
    _require_source_markers(
        document_service,
        f"{document_service_path}#strong-document-if-match",
        {
            "exact strong pattern": "STRONG_DOCUMENT_ETAG_PATTERN = r'^\"sha256-[0-9a-f]{64}\"$'",
            "exact comparison helper": "def if_match_satisfied(header: str, current_etag: str) -> bool:",
            "byte-exact comparison": "return header == current_etag",
            "service precondition": "if if_match is not None and not if_match_satisfied(",
        },
        errors,
    )
    update_source = _function_source(
        main_source, "_update_document_write", main_path, errors
    )
    _require_source_markers(
        update_source,
        f"{main_path}#http-document-update-replay",
        {
            "HTTP update replay": "replay = await idempotency_replay(",
            "HTTP document service": "document_service.update(",
        },
        errors,
    )
    replay_position = update_source.find("replay = await idempotency_replay(")
    service_position = update_source.find("document_service.update(")
    if (
        replay_position < 0
        or service_position < 0
        or replay_position > service_position
    ):
        _error(
            errors,
            f"repository.anchors.{main_path}#http-document-update-replay",
            "HTTP document update must consult replay before service lookup/update",
        )
    mcp_update_source = _function_source(
        protocol_source, "mcp_update_arguments", protocol_path, errors
    )
    _require_source_markers(
        mcp_update_source,
        f"{protocol_path}#mcp-document-if-match",
        {
            "MCP strong pattern validation": "re.fullmatch(STRONG_DOCUMENT_ETAG_PATTERN, if_match)",
            "MCP exact strong error": '"if_match must be an exact strong document ETag"',
        },
        errors,
    )
    _require_source_markers(
        _read_anchor_source(root, "apps/api/tests/test_protocol_core.py", errors),
        "apps/api/tests/test_protocol_core.py#strong-document-if-match",
        {
            "HTTP wildcard weak list counterexamples": '"*",',
            "document replay test": "test_strong_etag_if_match_and_durable_idempotency",
            "MCP strong validation test": "test_mcp_create_document_matches_http_write_receipts_and_api_key_authority",
        },
        errors,
    )
    return errors


def _database_role_contract_errors(root: Path) -> list[str]:
    """Bind PostgreSQL ownership and privilege separation to current SQL bytes."""

    errors: list[str] = []
    relative_path = "infra/postgres/database-role-contract.sql"
    source = _read_anchor_source(root, relative_path, errors)
    _require_source_markers(
        source,
        relative_path,
        {
            "operator guard block": "DO $operator$",
            "operator identity guard": "session_user <> 'postgres' OR current_user <> 'postgres'",
            "operator guard failure": (
                "database role reconciliation requires the postgres operator"
            ),
            "operator database-owner repair": (
                "SELECT format('ALTER DATABASE %I OWNER TO %I', current_database(), session_user) \\gexec"
            ),
            "operator database-owner identity": "current_database(), session_user",
            "operator database-owner verification": "pg_get_userbyid(datdba)",
            "operator database owner is postgres": "<> 'postgres'",
            "migrator database CREATE denial": (
                "has_database_privilege('connectmd_migrator',current_database(),'CREATE')"
            ),
            "migrator database TEMPORARY denial": (
                "has_database_privilege('connectmd_migrator',current_database(),'TEMPORARY')"
            ),
            "migrator schema ownership": (
                "pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'"
            ),
            "migrator table and sequence ownership": (
                "public tables/sequences must be migrator-owned"
            ),
            "migrator function ownership": "public functions must be migrator-owned",
            "migrator object-owner predicate": "owner.rolname <> 'connectmd_migrator'",
        },
        errors,
    )
    if re.search(
        r"ALTER\s+DATABASE[^\n]*OWNER\s+TO\s+connectmd_migrator",
        source,
        flags=re.IGNORECASE,
    ):
        _error(
            errors,
            f"repository.operations.{relative_path}",
            "must not assign database ownership to connectmd_migrator",
        )
    migrator_privilege_guard = (
        "has_database_privilege('connectmd_migrator',current_database(),'CREATE')\n"
        "     OR has_database_privilege('connectmd_migrator',current_database(),'TEMPORARY') THEN"
    )
    if migrator_privilege_guard not in source:
        _error(
            errors,
            f"repository.operations.{relative_path}",
            "must deny both migrator database CREATE and TEMPORARY privileges",
        )
    schema_owner_guard = (
        "IF (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public')\n"
        "       <> 'connectmd_migrator'"
    )
    if schema_owner_guard not in source:
        _error(
            errors,
            f"repository.operations.{relative_path}",
            "must require migrator ownership of the public schema",
        )
    object_owner_guard = (
        "AND c.relkind IN ('r','p','S') AND owner.rolname <> 'connectmd_migrator'"
    )
    if object_owner_guard not in source:
        _error(
            errors,
            f"repository.operations.{relative_path}",
            "must require migrator ownership of public tables and sequences",
        )
    return errors


def _production_operations_errors(root: Path) -> list[str]:
    errors: list[str] = []
    errors.extend(_database_role_contract_errors(root))
    api_path = "apps/api/app/main.py"
    api = _read_anchor_source(root, api_path, errors)
    health_path = "apps/api/app/routes/health.py"
    health = _read_anchor_source(root, health_path, errors)
    _require_source_markers(
        api,
        api_path,
        {
            "health router import": "from app.routes.health import router as health_router",
            "health router inclusion": "app.include_router(health_router)",
        },
        errors,
    )
    _require_source_markers(
        health,
        health_path,
        {
            "health router declaration": "router = APIRouter()",
            "liveness route": '@router.get("/healthz", include_in_schema=False)',
            "readiness route": (
                '@router.get("/readyz", include_in_schema=False, response_model=None)'
            ),
            "unconfigured search readiness": "if not search.enabled:",
            "configured search health gate": "if not await search.health():",
            "configured search failure state": '"search": "unavailable"',
        },
        errors,
    )
    registry_path = "packages/platform-contract/platform-features.json"
    registry = _read_anchor_source(root, registry_path, errors)
    _require_source_markers(
        registry,
        registry_path,
        {"health router implementation": '"apps/api/app/routes/health.py"'},
        errors,
    )
    library_path = "infra/scripts/lib.sh"
    library = _read_anchor_source(root, library_path, errors)
    restore_attestation_start = library.find("attest_restore_migrator_role() {")
    restore_attestation_end = library.find("\n}\n", restore_attestation_start)
    if restore_attestation_start < 0 or restore_attestation_end < 0:
        _error(
            errors,
            f"repository.operations.{library_path}",
            "is missing the restore migrator attestation function",
        )
        restore_attestation = ""
    else:
        restore_attestation = library[
            restore_attestation_start : restore_attestation_end + 3
        ]
    _require_source_markers(
        restore_attestation,
        f"{library_path}#restore-migrator-attestation",
        {
            "exact migrator identity": "session_user <> 'connectmd_migrator' OR current_user <> 'connectmd_migrator'",
            "role attribute denial": "restore migrator role attributes failed",
            "membership denial": "restore migrator role membership failed",
            "database CREATE denial": "has_database_privilege(current_user, current_database(), 'CREATE')",
            "database TEMPORARY denial": "has_database_privilege(current_user, current_database(), 'TEMPORARY')",
            "schema CREATE requirement": "has_schema_privilege(current_user, 'public', 'CREATE')",
        },
        errors,
    )
    readiness_test_path = "apps/api/tests/test_readiness.py"
    readiness_tests = _read_anchor_source(root, readiness_test_path, errors)
    _require_source_markers(
        readiness_tests,
        readiness_test_path,
        {
            "configured search failure test": "test_readyz_fails_closed_when_configured_search_is_unavailable",
            "unconfigured local search test": "test_readyz_allows_intentionally_unconfigured_local_search",
        },
        errors,
    )
    deploy_path = "infra/scripts/deploy.sh"
    deploy = _read_anchor_source(root, deploy_path, errors)
    deploy_positions = _ordered_anchor_positions(
        deploy,
        deploy_path,
        [
            (
                "service stop barrier",
                "compose --profile account-lifecycle stop account-erasure-worker search-projection-worker nginx frontend api",
            ),
            ("database role bootstrap", "bootstrap_database_roles"),
            (
                "single migration",
                "compose --profile database-operations run --rm --no-deps -T db-migrate alembic upgrade head",
            ),
            ("database role reconcile", "reconcile_database_roles"),
            (
                "fail-closed projection rebuild",
                "compose --profile search-operations run --rm --no-deps -T search-admin python -m app.cli rebuild-search",
            ),
            (
                "public service start",
                "compose up -d --no-build converter search-projection-worker api frontend nginx",
            ),
            (
                "lifecycle worker health",
                "wait_for_profiled_service account-lifecycle account-erasure-worker",
            ),
            (
                "durable staged-release selection",
                'write_staged_release "$source_revision" "$image_tag" "$api_image_id" "$web_image_id" "$nginx_image_id"',
            ),
            ("successful rollout terminal", "rollout_complete=true"),
        ],
        errors,
    )
    _require_source_markers(
        deploy,
        deploy_path,
        {
            "strict shell mode": "set -Eeuo pipefail",
            "worker prior-state capture": "profiled_service_state account-lifecycle account-erasure-worker",
            "search projection worker health": "wait_for_service search-projection-worker",
            "failed-rollout stop trap": "stop_failed_rollout_on_exit",
            "completed restore phase gate": "phase=complete",
            "restore digest set": 'for digest in "$restore_db_digest" "$restore_markdown_digest" "$restore_receipt_digest"',
            "restore digest format": "^[0-9a-f]{64}$",
            "restore search rebuild gate": "search_rebuild_pending=false",
            "restore receipt mode": "stat -c '%a' \"$restore_receipt\"",
            "restore receipt binding": "Completed restore receipt does not match durable restore state",
            "partial restore rejection": "Restore state is incomplete; explicit recovery is required",
            "completed restore-state retirement": "clear_matching_completed_restore_state",
        },
        errors,
    )
    stop = deploy_positions.get("service stop barrier")
    migrate = deploy_positions.get("single migration")
    if stop is not None and migrate is not None and "|| true" in deploy[stop:migrate]:
        _error(
            errors,
            f"repository.operations.{deploy_path}",
            "migration stop barrier must not suppress failures with '|| true'",
        )
    release_accept_path = "infra/scripts/release-accept.sh"
    release_accept = _read_anchor_source(root, release_accept_path, errors)
    required_acceptance_services = (
        "postgres",
        "meilisearch",
        "converter",
        "search-projection-worker",
        "api",
        "frontend",
        "nginx",
    )
    acceptance_runtime_loop = (
        "for service in " + " ".join(required_acceptance_services) + "; do\n"
        '  wait_for_service "$service" "$acceptance_service_health_attempts"\n'
        "done"
    )
    acceptance_positions = _ordered_anchor_positions(
        release_accept,
        release_accept_path,
        [
            (
                "exact staged image identities",
                (
                    'assert_release_images_match "$STAGED_IMAGE_TAG" '
                    '"$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" '
                    '"$STAGED_NGINX_IMAGE_ID"'
                ),
            ),
            ("ordinary runtime health", acceptance_runtime_loop),
            (
                "enabled lifecycle health",
                (
                    'if [ "${lifecycle_enabled:-false}" = "true" ]; then\n'
                    "  wait_for_profiled_service account-lifecycle "
                    "account-erasure-worker "
                    '"$acceptance_lifecycle_health_attempts"\n'
                    "fi"
                ),
            ),
            ("public protocol probes", 'public_get / "$workdir/root.body"'),
            (
                "acceptance receipt mutation",
                'acceptance_receipt="$(write_release_acceptance',
            ),
            ("active-marker promotion", "persist_image_tag"),
        ],
        errors,
    )
    runtime_health = acceptance_positions.get("ordinary runtime health")
    receipt_mutation = acceptance_positions.get("acceptance receipt mutation")
    if (
        runtime_health is not None
        and receipt_mutation is not None
        and "health.sh"
        in release_accept[runtime_health:receipt_mutation].replace(
            "Do not call health.sh here", ""
        )
    ):
        _error(
            errors,
            f"repository.operations.{release_accept_path}",
            "must use in-process health helpers rather than a nested health script",
        )

    restore_path = "infra/scripts/restore.sh"
    restore = _read_anchor_source(root, restore_path, errors)
    library_path = "infra/scripts/lib.sh"
    library = _read_anchor_source(root, library_path, errors)
    for relative_path, source in (
        (deploy_path, deploy),
        (restore_path, restore),
        (library_path, library),
    ):
        for legacy_marker in (
            "configure_search_projection_db_role",
            "ensure_search_projection_cluster_role",
        ):
            if legacy_marker in source:
                _error(
                    errors,
                    f"repository.operations.{relative_path}",
                    f"must not retain legacy cluster-role helper anchor {legacy_marker!r}",
                )
    archive_path = "apps/api/app/services/backup_archive.py"
    archive_source = _read_anchor_source(root, archive_path, errors)
    _require_source_markers(
        archive_source,
        archive_path,
        {
            "streaming archive reader": 'with tarfile.open(archive_path, mode="r|gz") as archive:',
            "regular-file/directory-only member check": "if not (member.isdir() or member.isreg()):",
            "canonical member-name validation": "canonical = _canonical_member_name(member.name, is_directory=member.isdir())",
            "invalid-name rejection": 'raise BackupArchiveError("member_name_invalid")',
            "invalid-type rejection": 'raise BackupArchiveError("member_type_invalid")',
            "duplicate-name rejection": 'raise BackupArchiveError("duplicate_member")',
            "archive symlink rejection": "if archive_path.is_symlink() or not archive_path.is_file():",
            "compressed-size limit": "MAX_ARCHIVE_BYTES = 64 * 1024 * 1024 * 1024",
            "compressed-size bound": "if archive_path.stat().st_size > max_archive_bytes:",
            "compressed-size rejection": 'raise BackupArchiveError("archive_size_exceeded")',
            "duplicate normalized-name check": "if canonical in members:",
            "member-count rejection": 'raise BackupArchiveError("member_count_exceeded")',
            "expanded-size rejection": 'raise BackupArchiveError("expanded_size_exceeded")',
            "expanded-size limit": "MAX_ARCHIVE_EXPANDED_BYTES = 64 * 1024 * 1024 * 1024",
            "directory-size rejection": 'raise BackupArchiveError("member_size_invalid")',
            "file-ancestor conflict rejection": 'if member.isreg() and any(name.startswith(f"{canonical}/") for name in members):',
            "directory-ancestor conflict rejection": "if members.get(parent_name) is False:",
        },
        errors,
    )
    receipt_assignment = restore.find('receipt="$registration_root/$generation_id.env"')
    receipt_preflight = (
        restore.find("verify_registration_receipt", receipt_assignment)
        if receipt_assignment >= 0
        else -1
    )
    if receipt_preflight < 0:
        _error(
            errors,
            f"repository.operations.{restore_path}",
            "is missing registration-receipt preflight after receipt selection",
        )
    anchors = [
        (
            "strict metadata timestamp",
            'created_epoch="$(date -u -d "$created_at" +%s)"',
        ),
        (
            "source revision preflight",
            '[ "$(current_source_revision)" = "$backup_source_revision" ]',
        ),
        (
            "release receipt selection",
            'backup_release_receipt="$(release_receipt_path "$backup_image_tag")"',
        ),
        (
            "release receipt and image identity preflight",
            'validate_release_receipt "$backup_release_receipt" "$backup_source_revision" "$backup_image_tag" "$backup_api_image_id" "$backup_web_image_id" "$backup_nginx_image_id"',
        ),
        (
            "release receipt digest preflight",
            '[ "$(sha256sum "$backup_release_receipt" | cut -d\' \' -f1)" = "$backup_release_receipt_digest" ]',
        ),
        (
            "acceptance receipt branch",
            'if [ "$backup_format" = "connectmd-backup-v3" ]; then\n  backup_acceptance_receipt=',
        ),
        (
            "acceptance receipt selection",
            'backup_acceptance_receipt="$(load_release_acceptance "$backup_image_tag" "$backup_acceptance_receipt_digest")"',
        ),
        (
            "acceptance receipt digest preflight",
            '[ "$(digest_of_file "$backup_acceptance_receipt")" = "$backup_acceptance_receipt_digest" ]',
        ),
        (
            "release image identity preflight",
            'assert_release_images_match "$backup_image_tag" "$backup_api_image_id" "$backup_web_image_id" "$backup_nginx_image_id"',
        ),
        (
            "archive member preflight",
            "-m app.services.backup_archive /restore/markdown-storage.tar.gz",
        ),
        ("database dump preflight", "backup-verify pg_restore --list"),
        (
            "verify-only terminal",
            'if [ "$mode" = "--verify-only" ]; then',
        ),
        (
            "recorded API image contract probe",
            'docker run --rm --network none --entrypoint python "connectmd-api:$backup_image_tag"',
        ),
        ("evidence hard-link preflight", 'ln "$evidence_probe" "$evidence_probe.link"'),
        ("API prior-state capture", 'api_prior_state="$(service_state api)"'),
        (
            "search projection prior-state capture",
            'projection_prior_state="$(service_state search-projection-worker)"',
        ),
        (
            "worker prior-state capture",
            'worker_prior_state="$(profiled_service_state account-lifecycle account-erasure-worker)"',
        ),
        (
            "service stop barrier",
            "compose --profile account-lifecycle stop account-erasure-worker search-projection-worker api",
        ),
        (
            "durable restore-state write",
            "\nwrite_restore_state in_progress unavailable\n",
        ),
        ("destructive boundary", "mutation_started=true"),
        ("database role bootstrap before restore", "bootstrap_database_roles"),
        ("restore migrator pre-work attestation", "attest_restore_migrator_role"),
        (
            "migrator database restore",
            'pg_restore --exit-on-error --no-owner --no-privileges -d "$db_name" < "$directory/postgres.dump"',
        ),
        (
            "database role reconcile after restore",
            'postgres.dump"\nreconcile_database_roles',
        ),
        ("backup authority registration", "python -m app.cli account-backup register"),
        ("successful restore terminal", "restore_complete=true"),
    ]
    restore_positions = _ordered_anchor_positions(
        restore, restore_path, anchors, errors
    )
    archive_preflight = restore_positions.get("archive member preflight")
    if archive_preflight is not None:
        archive_command_start = restore.rfind("docker run", 0, archive_preflight)
        archive_command_end = restore.find("pg_restore --list", archive_preflight)
        archive_command = (
            restore[archive_command_start:archive_command_end]
            if archive_command_start >= 0
            and archive_command_end > archive_command_start
            else ""
        )
        _require_source_markers(
            archive_command,
            f"{restore_path}#archive-preflight",
            {
                "no-network read-only validator": "docker run --rm --network none --read-only",
                "read-only archive mount": '-v "$directory/markdown-storage.tar.gz:/restore/markdown-storage.tar.gz:ro"',
                "exact backup API image": '--entrypoint python "$backup_api_image_id"',
                "backup archive validator module": "-m app.services.backup_archive /restore/markdown-storage.tar.gz",
            },
            errors,
        )
        exact_archive_mount = '-v "$directory/markdown-storage.tar.gz:/restore/markdown-storage.tar.gz:ro"'
        if (
            archive_command.count("-v ") != 1
            or exact_archive_mount not in archive_command
        ):
            _error(
                errors,
                f"repository.operations.{restore_path}",
                "archive validator must mount only markdown-storage.tar.gz read-only",
            )
        if '-v "$directory:/restore:ro"' in archive_command:
            _error(
                errors,
                f"repository.operations.{restore_path}",
                "archive validator must not mount the backup directory",
            )
    source_preflight = restore_positions.get("source revision preflight")
    release_receipt_preflight = restore_positions.get(
        "release receipt and image identity preflight"
    )
    acceptance_branch = restore_positions.get("acceptance receipt branch")
    acceptance_preflight = restore_positions.get("acceptance receipt digest preflight")
    release_image_preflight = restore_positions.get("release image identity preflight")
    database_preflight = restore_positions.get("database dump preflight")
    verify_only = restore_positions.get("verify-only terminal")
    recorded_api_probe = restore_positions.get("recorded API image contract probe")
    writer_stop = restore_positions.get("service stop barrier")
    if (
        source_preflight is None
        or release_receipt_preflight is None
        or acceptance_branch is None
        or acceptance_preflight is None
        or release_image_preflight is None
        or archive_preflight is None
        or database_preflight is None
        or verify_only is None
        or recorded_api_probe is None
        or writer_stop is None
        or not (
            source_preflight
            < release_receipt_preflight
            < acceptance_branch
            < acceptance_preflight
            < release_image_preflight
            < archive_preflight
            < database_preflight
            < verify_only
            < recorded_api_probe
            < writer_stop
        )
    ):
        _error(
            errors,
            f"repository.operations.{restore_path}",
            "source, release, acceptance, and image identities must be authenticated before archive validation, verify-only exit, and the mutation path",
        )
    if (
        receipt_preflight >= 0
        and release_receipt_preflight is not None
        and receipt_preflight >= release_receipt_preflight
    ):
        _error(
            errors,
            f"repository.operations.{restore_path}",
            "registration receipt must be preflighted before release image identity acceptance",
        )
    _require_source_markers(
        restore,
        restore_path,
        {
            "strict shell mode": "set -Eeuo pipefail",
            "active restarting-state treatment": "running | restarting | paused",
            "API prior image": 'api_prior_tag="$(managed_api_tag "$(service_image api)")"',
            "search projection prior image": 'projection_prior_tag="$(managed_api_tag "$(service_image search-projection-worker)")"',
            "worker prior image": 'worker_prior_tag="$(managed_api_tag "$(profiled_service_image account-lifecycle account-erasure-worker)")"',
            "post-stop inactivity check": "service_is_active api || service_is_active search-projection-worker || profiled_service_is_active account-lifecycle account-erasure-worker",
            "pre-existing receipt requirement": '[ -f "$receipt" ] && [ ! -L "$receipt" ] || die "Destructive restore requires an existing durable registration receipt"',
            "receipt mode enforcement": '[ "$(stat -c \'%a\' "$receipt")" = "600" ]',
            "receipt exact-field enforcement": '[ "$(wc -l < "$receipt" | tr -d \' \')" = "8" ]',
            "atomic restore-state replacement": 'mv -f -- "$temporary" "$RESTORE_STATE_FILE"',
        },
        errors,
    )
    state_write = restore_positions.get("durable restore-state write")
    mutation = restore_positions.get("destructive boundary")
    if state_write is not None and mutation is not None:
        between = restore[state_write:mutation]
        if between.strip() != "write_restore_state in_progress unavailable":
            _error(
                errors,
                f"repository.operations.{restore_path}",
                "write_restore_state must occur immediately before the destructive boundary",
            )
    register = restore_positions.get("backup authority registration")
    complete = restore_positions.get("successful restore terminal")
    if register is not None and complete is not None:
        post_registration = restore[register:complete]
        post_registration_markers = (
            "verify_registration_receipt",
            "python -m app.cli deletion-journal verify-live",
            'registration_receipt_digest="$(sha256sum "$receipt" | cut -d\' \' -f1)"',
            'write_restore_state complete "$registration_receipt_digest"',
        )
        post_registration_positions = [
            post_registration.find(marker) for marker in post_registration_markers
        ]
        if any(
            position < 0 for position in post_registration_positions
        ) or post_registration_positions != sorted(post_registration_positions):
            _error(
                errors,
                f"repository.operations.{restore_path}",
                "restore must verify its pre-existing receipt and live deletion authority before completing durable restore state",
            )

    test_path = "infra/tests/operational-contracts.py"
    operation_tests = _read_anchor_source(root, test_path, errors)
    _require_source_markers(
        operation_tests,
        test_path,
        {
            "deploy barrier assertion": "deploy_start < deploy_lifecycle_wait < deploy_stage < deploy_complete",
            "deploy database-role order assertion": "assert deploy_stop < deploy_bootstrap_roles < deploy_migrate < deploy_role",
            "failed rollout assertion": "stop_failed_rollout_on_exit",
            "restore-state assertion": "restore_state_write < restore_mutation",
            "per-service prior-state assertion": 'api_prior_state="$(service_state api)"',
            "backup authority order assertion": "restore_database < restore_register < restore_complete",
            "restore database-role order assertion": "    < restore_role_bootstrap\n    < restore_migrator_attestation\n    < restore_database\n    < restore_role_reconcile",
            "archive validator order assertion": 'restore_archive_validation = restore.index("-m app.services.backup_archive")',
            "archive validator network assertion": "assert '--network none --read-only' in archive_validation",
            "archive validator mount assertion": "assert '-v \"$directory/markdown-storage.tar.gz:/restore/markdown-storage.tar.gz:ro\"' in archive_validation",
            "archive validator image assertion": "assert '\"$backup_api_image_id\"' in archive_validation",
            "archive validator source/auth order assertion": "restore_receipt_preflight\n    < restore_release_receipt_validation\n    < restore_release_receipt_digest\n    < restore_acceptance_receipt_validation\n    < restore_acceptance_receipt_digest\n    < restore_image\n    < restore_checkpoint_image_binding\n    < restore_journal_checkpoint\n    < restore_archive_validation",
            "completed restore-state assertion": 'write_restore_state complete "$registration_receipt_digest"',
            "atomic state assertion": 'assert \'mv -f -- "$temporary" "$RESTORE_STATE_FILE"\' in restore',
            "completed restore phase assertion": '"phase=complete" in deploy',
            "health environment preflight assertion": 'health.index("validate_production_env") < health.index("wait_for_service")',
        },
        errors,
    )
    health_path = "infra/scripts/health.sh"
    health = _read_anchor_source(root, health_path, errors)
    health_validation = health.find("validate_production_env")
    health_wait = health.find("wait_for_service")
    if health_validation < 0 or health_wait < 0 or health_validation >= health_wait:
        _error(
            errors,
            f"repository.operations.{health_path}",
            "production environment validation must precede service health checks",
        )
    _require_source_markers(
        health,
        health_path,
        {
            "strict shell mode": "set -Eeuo pipefail",
            "lifecycle worker health": "wait_for_profiled_service account-lifecycle account-erasure-worker",
            "search projection worker health": "search-projection-worker",
        },
        errors,
    )
    return errors


def _required_feature_anchor_errors(features: list[Any]) -> list[str]:
    errors: list[str] = []
    by_id = {
        feature.get("id"): feature
        for feature in features
        if isinstance(feature, dict) and isinstance(feature.get("id"), str)
    }
    for feature_id, required in REQUIRED_FEATURE_ANCHORS.items():
        feature = by_id.get(feature_id)
        if not isinstance(feature, dict):
            continue
        implementation = feature.get("implementation")
        operations = feature.get("operations")
        evidence = feature.get("evidence")
        actual = {
            "implementation": set(
                implementation.get("paths", [])
                if isinstance(implementation, dict)
                else []
            ),
            "tests": set(feature.get("tests", [])),
            "gate_paths": set(
                evidence.get("gate_paths", []) if isinstance(evidence, dict) else []
            ),
            "operations": set(
                operations.get("paths", []) if isinstance(operations, dict) else []
            ),
        }
        for category, expected_paths in required.items():
            missing = sorted(expected_paths - actual[category])
            if missing:
                _error(
                    errors,
                    f"registry.features.{feature_id}.{category}",
                    f"is missing required control anchors: {', '.join(missing)}",
                )
    return errors


def _release_matrix_feature_mapping_errors(
    repo_root: Path, features: list[Any]
) -> list[str]:
    """Require every registered feature to appear in the matrix owner column."""
    path = repo_root / "docs/platform/release-matrix.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"repository.release_matrix: cannot read {path}: {exc}"]
    if "## Current control anchors" not in text:
        return [
            "repository.release_matrix: missing '## Current control anchors' section"
        ]
    owner_cells: list[str] = []
    for line in text.splitlines():
        if line.startswith("|") and line.count("|") >= 4:
            cells = line.split("|")
            if len(cells) >= 3:
                owner_cells.append(cells[2])
    mapped_ids = {
        token
        for cell in owner_cells
        for token in re.findall(r"`([a-z][a-z0-9-]*)`", cell)
    }
    feature_ids = {
        feature.get("id")
        for feature in features
        if isinstance(feature, dict) and isinstance(feature.get("id"), str)
    }
    missing = sorted(feature_ids - mapped_ids)
    if missing:
        return [
            "repository.release_matrix.owner_mapping: missing registry feature(s): "
            + ", ".join(missing)
        ]
    return []


def _schema_is_expected(schema: Any, errors: list[str]) -> None:
    _schema_is_expected_impl(schema, errors, error=_error)


def _evidence_schema_is_expected(schema: Any, errors: list[str]) -> None:
    _evidence_schema_is_expected_impl(
        schema,
        errors,
        error=_error,
        evidence_receipt_fields=EVIDENCE_RECEIPT_FIELDS,
        evidence_check_fields=EVIDENCE_CHECK_FIELDS,
        id_pattern=ID_RE.pattern,
        revision_pattern=REVISION_RE.pattern,
        sha256_pattern=SHA256_RE.pattern,
    )


def check_registry(
    registry_path: Path,
    schema_path: Path,
    repo_root: Path,
    route_ownership_path: Path | None = None,
    ui_route_ownership_path: Path | None = None,
    evidence_schema_path: Path | None = None,
) -> list[str]:
    """Return deterministic validation errors; an empty list means pass."""
    errors: list[str] = []
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"schema: cannot load JSON: {exc}"]
    _schema_is_expected(schema, errors)
    if evidence_schema_path is None:
        evidence_schema_path = (
            repo_root
            / "packages/platform-contract/platform-evidence-receipt.schema.json"
        )
    try:
        evidence_schema = json.loads(evidence_schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [*errors, f"evidence schema: cannot load JSON: {exc}"]
    _evidence_schema_is_expected(evidence_schema, errors)
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [*errors, f"registry: cannot load JSON: {exc}"]

    if route_ownership_path is None:
        route_ownership_path = (
            repo_root / "packages/platform-contract/platform-route-ownership.json"
        )
    if ui_route_ownership_path is None:
        ui_route_ownership_path = (
            repo_root / "packages/platform-contract/platform-ui-route-ownership.json"
        )
    (
        route_ownership,
        ui_route_ownership,
        route_registry_errors,
        route_registry_fatal,
    ) = _load_route_ownership(route_ownership_path, ui_route_ownership_path)
    errors.extend(route_registry_errors)
    if route_registry_fatal:
        return errors

    root = _object(
        registry, "registry", errors, {"schema_version", "registry_id", "features"}
    )
    if root is None:
        return errors
    if root.get("schema_version") != 1:
        _error(errors, "registry.schema_version", "must equal 1")
    if root.get("registry_id") != "connect-md-platform-features":
        _error(
            errors, "registry.registry_id", "must equal 'connect-md-platform-features'"
        )
    features = root.get("features")
    if not isinstance(features, list) or not features:
        _error(errors, "registry.features", "must be a non-empty array")
        return errors

    advanced_claim_present = any(
        isinstance(feature, dict)
        and feature.get("stage")
        in {"repository_verified", "deployment_verified", "releasable"}
        for feature in features
    )
    evidence_revision = (
        _clean_git_revision(repo_root, errors) if advanced_claim_present else None
    )

    try:
        api_source = (repo_root / "apps/api/app/main.py").read_text(encoding="utf-8")
        discovery_source = (repo_root / "apps/api/app/routes/discovery.py").read_text(
            encoding="utf-8"
        )
        model_source = (repo_root / "apps/api/app/models.py").read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        return [*errors, f"repository: cannot read API sources: {exc}"]

    route_inventory = _implemented_route_inventory(repo_root, api_source, errors)

    ids: set[str] = set()
    protocol_ids: set[str] = set()
    model_owners: dict[str, str] = {}
    route_anchors: dict[str, str] = {}
    feature_ui_routes: dict[str, set[str]] = {}
    for index, feature in enumerate(features):
        prefix = f"registry.features[{index}]"
        fields = {
            "id",
            "domain",
            "stage",
            "authority",
            "surfaces",
            "data",
            "lifecycle",
            "implementation",
            "workers",
            "operations",
            "tests",
            "exclusions",
            "evidence",
        }
        item = _object(feature, prefix, errors, fields)
        if item is None:
            continue
        feature_id = item.get("id")
        if not isinstance(feature_id, str) or not ID_RE.fullmatch(feature_id):
            _error(errors, f"{prefix}.id", "must be a lowercase kebab-case identifier")
        elif feature_id in ids:
            _error(errors, f"{prefix}.id", f"duplicates feature id {feature_id!r}")
        else:
            ids.add(feature_id)
        if not isinstance(item.get("domain"), str) or not item["domain"]:
            _error(errors, f"{prefix}.domain", "must be a non-empty string")
        stage = item.get("stage")
        if stage not in STAGES:
            _error(errors, f"{prefix}.stage", "is not a valid stage claim")

        authority = _object(
            item.get("authority"),
            f"{prefix}.authority",
            errors,
            {"actors", "write_actors", "constraints"},
        )
        if authority:
            _strings(
                authority.get("actors"),
                f"{prefix}.authority.actors",
                errors,
                nonempty=True,
            )
            _strings(
                authority.get("write_actors"),
                f"{prefix}.authority.write_actors",
                errors,
            )
            constraints = _strings(
                authority.get("constraints"),
                f"{prefix}.authority.constraints",
                errors,
                nonempty=True,
            )
            required_constraints = REQUIRED_FEATURE_CONSTRAINTS.get(feature_id, set())
            missing_constraints = required_constraints - set(constraints)
            if missing_constraints:
                _error(
                    errors,
                    f"{prefix}.authority.constraints",
                    "is missing required constraints: "
                    + ", ".join(sorted(missing_constraints)),
                )

        surfaces = _object(
            item.get("surfaces"),
            f"{prefix}.surfaces",
            errors,
            {"api", "ui", "search", "discovery", "protocols"},
        )
        api_routes: list[str] = []
        discovery_statuses: dict[str, str] = {}
        if surfaces:
            discovery = _object(
                surfaces.get("discovery"),
                f"{prefix}.surfaces.discovery",
                errors,
                DISCOVERY_SURFACES,
            )
            if discovery:
                for discovery_surface in sorted(DISCOVERY_SURFACES):
                    status = discovery.get(discovery_surface)
                    if status not in DISCOVERY_STATES:
                        _error(
                            errors,
                            f"{prefix}.surfaces.discovery.{discovery_surface}",
                            "has an invalid discovery state",
                        )
                    elif isinstance(status, str):
                        discovery_statuses[discovery_surface] = status
                if stage == "feature_gated" and any(
                    status in {"advertised", "denied"}
                    for status in discovery_statuses.values()
                ):
                    _error(
                        errors,
                        f"{prefix}.surfaces.discovery",
                        "feature-gated features must remain hidden or not applicable",
                    )
                if stage == "disabled":
                    if any(
                        status == "advertised" for status in discovery_statuses.values()
                    ):
                        _error(
                            errors,
                            f"{prefix}.surfaces.discovery",
                            "disabled features cannot be advertised",
                        )
                    if "denied" not in discovery_statuses.values():
                        _error(
                            errors,
                            f"{prefix}.surfaces.discovery",
                            "disabled features require an explicit denied surface",
                        )
            api = _object(
                surfaces.get("api"), f"{prefix}.surfaces.api", errors, {"routes"}
            )
            if api:
                api_routes = _strings(
                    api.get("routes"), f"{prefix}.surfaces.api.routes", errors
                )
                for route in api_routes:
                    if not ROUTE_RE.fullmatch(route):
                        _error(
                            errors,
                            f"{prefix}.surfaces.api.routes",
                            f"is not a method and route: {route!r}",
                        )
                    elif not _route_exists(route, route_inventory):
                        _error(
                            errors,
                            f"{prefix}.surfaces.api.routes",
                            f"is not implemented by apps/api/app/main.py: {route!r}",
                        )
                    elif discovery_statuses.get(
                        "openapi"
                    ) == "hidden" and not _route_is_hidden_from_openapi(
                        route, route_inventory
                    ):
                        _error(
                            errors,
                            f"{prefix}.surfaces.api.routes",
                            f"route declared hidden is visible in OpenAPI: {route!r}",
                        )
                    elif discovery_statuses.get(
                        "openapi"
                    ) == "advertised" and _route_is_hidden_from_openapi(
                        route, route_inventory
                    ):
                        _error(
                            errors,
                            f"{prefix}.surfaces.api.routes",
                            f"route declared advertised is hidden from OpenAPI: {route!r}",
                        )
                    if isinstance(feature_id, str):
                        previous_owner = route_anchors.get(route)
                        if previous_owner is not None:
                            _error(
                                errors,
                                f"{prefix}.surfaces.api.routes",
                                f"route {route!r} is also claimed by feature {previous_owner!r}",
                            )
                        else:
                            route_anchors[route] = feature_id
            ui = _object(
                surfaces.get("ui"), f"{prefix}.surfaces.ui", errors, {"routes"}
            )
            if ui:
                declared_ui_routes = _strings(
                    ui.get("routes"), f"{prefix}.surfaces.ui.routes", errors
                )
                if isinstance(feature_id, str):
                    feature_ui_routes[feature_id] = set(declared_ui_routes)
                for route in declared_ui_routes:
                    if not route.startswith("/"):
                        _error(
                            errors,
                            f"{prefix}.surfaces.ui.routes",
                            f"is not a UI route: {route!r}",
                        )
                    elif not _ui_route_exists(route, repo_root):
                        _error(
                            errors,
                            f"{prefix}.surfaces.ui.routes",
                            f"is not implemented by apps/web/app: {route!r}",
                        )
            search = _object(
                surfaces.get("search"),
                f"{prefix}.surfaces.search",
                errors,
                {"mode", "fields"},
            )
            if search:
                if search.get("mode") not in SEARCH_MODES:
                    _error(errors, f"{prefix}.surfaces.search.mode", "is unclassified")
                _strings(
                    search.get("fields"), f"{prefix}.surfaces.search.fields", errors
                )
            raw_protocols = surfaces.get("protocols")
            if not isinstance(raw_protocols, list):
                _error(errors, f"{prefix}.surfaces.protocols", "must be an array")
            else:
                for protocol_index, protocol in enumerate(raw_protocols):
                    protocol_prefix = f"{prefix}.surfaces.protocols[{protocol_index}]"
                    protocol_obj = _object(
                        protocol, protocol_prefix, errors, {"id", "routes", "tests"}
                    )
                    if not protocol_obj:
                        continue
                    protocol_id = protocol_obj.get("id")
                    if not isinstance(protocol_id, str) or not ID_RE.fullmatch(
                        protocol_id
                    ):
                        _error(
                            errors,
                            f"{protocol_prefix}.id",
                            "must be a lowercase kebab-case identifier",
                        )
                    elif protocol_id in protocol_ids:
                        _error(
                            errors,
                            f"{protocol_prefix}.id",
                            f"duplicates advertised protocol {protocol_id!r}",
                        )
                    else:
                        protocol_ids.add(protocol_id)
                    routes = _strings(
                        protocol_obj.get("routes"),
                        f"{protocol_prefix}.routes",
                        errors,
                        nonempty=True,
                    )
                    tests = _strings(
                        protocol_obj.get("tests"),
                        f"{protocol_prefix}.tests",
                        errors,
                        nonempty=True,
                    )
                    for route in routes:
                        if route not in api_routes:
                            _error(
                                errors,
                                f"{protocol_prefix}.routes",
                                f"is not declared by this feature's API surface: {route!r}",
                            )
                        elif not _route_exists(route, route_inventory):
                            _error(
                                errors,
                                f"{protocol_prefix}.routes",
                                f"is not implemented by apps/api/app/main.py: {route!r}",
                            )
                    for test_path in tests:
                        _repo_path(
                            test_path, repo_root, f"{protocol_prefix}.tests", errors
                        )

        data = _object(
            item.get("data"),
            f"{prefix}.data",
            errors,
            {"classification", "storage", "search_projection", "models"},
        )
        if data:
            if data.get("classification") not in CLASSIFICATIONS:
                _error(errors, f"{prefix}.data.classification", "is unclassified")
            _strings(
                data.get("storage"), f"{prefix}.data.storage", errors, nonempty=True
            )
            if data.get("search_projection") not in {
                "public",
                "excluded",
                "not_applicable",
            }:
                _error(errors, f"{prefix}.data.search_projection", "is unclassified")
            for model in _strings(data.get("models"), f"{prefix}.data.models", errors):
                if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", model):
                    _error(
                        errors,
                        f"{prefix}.data.models",
                        f"is not a model name: {model!r}",
                    )
                elif model in model_owners:
                    _error(
                        errors,
                        f"{prefix}.data.models",
                        f"model {model!r} is already owned by feature {model_owners[model]!r}",
                    )
                else:
                    model_owners[model] = str(feature_id)

        lifecycle = _object(
            item.get("lifecycle"),
            f"{prefix}.lifecycle",
            errors,
            {"export", "conceal", "erase", "retention"},
        )
        if lifecycle:
            for field in ("export", "conceal", "erase", "retention"):
                if lifecycle.get(field) not in LIFECYCLE_STATES:
                    _error(errors, f"{prefix}.lifecycle.{field}", "is unclassified")

        implementation = _object(
            item.get("implementation"), f"{prefix}.implementation", errors, {"paths"}
        )
        workers = _object(
            item.get("workers"), f"{prefix}.workers", errors, {"names", "paths"}
        )
        operations = _object(
            item.get("operations"),
            f"{prefix}.operations",
            errors,
            {"commands", "paths"},
        )
        evidence = _object(
            item.get("evidence"),
            f"{prefix}.evidence",
            errors,
            {"repository_paths", "deployment_paths", "feature_gate", "gate_paths"},
        )
        all_paths: list[tuple[str, list[str]]] = []
        implementation_paths: list[str] = []
        if implementation:
            implementation_paths = _strings(
                implementation.get("paths"), f"{prefix}.implementation.paths", errors
            )
            all_paths.append((f"{prefix}.implementation.paths", implementation_paths))
        if workers:
            _strings(workers.get("names"), f"{prefix}.workers.names", errors)
            all_paths.append(
                (
                    f"{prefix}.workers.paths",
                    _strings(workers.get("paths"), f"{prefix}.workers.paths", errors),
                )
            )
        if operations:
            _strings(
                operations.get("commands"), f"{prefix}.operations.commands", errors
            )
            all_paths.append(
                (
                    f"{prefix}.operations.paths",
                    _strings(
                        operations.get("paths"), f"{prefix}.operations.paths", errors
                    ),
                )
            )
        tests = _strings(item.get("tests"), f"{prefix}.tests", errors, nonempty=True)
        all_paths.append((f"{prefix}.tests", tests))
        _strings(item.get("exclusions"), f"{prefix}.exclusions", errors, nonempty=True)
        if evidence:
            repository_paths = _strings(
                evidence.get("repository_paths"),
                f"{prefix}.evidence.repository_paths",
                errors,
            )
            deployment_paths = _strings(
                evidence.get("deployment_paths"),
                f"{prefix}.evidence.deployment_paths",
                errors,
            )
            gate_paths = _strings(
                evidence.get("gate_paths"), f"{prefix}.evidence.gate_paths", errors
            )
            all_paths.extend(
                [
                    (f"{prefix}.evidence.repository_paths", repository_paths),
                    (f"{prefix}.evidence.deployment_paths", deployment_paths),
                    (f"{prefix}.evidence.gate_paths", gate_paths),
                ]
            )
            gate = evidence.get("feature_gate")
            if gate not in GATES:
                _error(errors, f"{prefix}.evidence.feature_gate", "is invalid")
            if stage == "feature_gated" and (
                gate == "not_applicable" or not gate_paths
            ):
                _error(
                    errors,
                    f"{prefix}.stage",
                    "feature_gated requires an explicit gate and gate path",
                )
            if stage == "disabled" and gate != "absence_enforced":
                _error(
                    errors,
                    f"{prefix}.stage",
                    "disabled requires feature_gate 'absence_enforced'",
                )
            if stage == "disabled" and api_routes:
                _error(
                    errors,
                    f"{prefix}.stage",
                    "disabled features cannot declare API routes",
                )
            if (
                stage
                in {
                    "implemented",
                    "feature_gated",
                    "repository_verified",
                    "deployment_verified",
                    "releasable",
                }
                and not implementation_paths
            ):
                _error(
                    errors, f"{prefix}.stage", f"{stage} requires implementation paths"
                )
            if (
                stage in {"repository_verified", "deployment_verified", "releasable"}
                and not repository_paths
            ):
                _error(
                    errors, f"{prefix}.stage", f"{stage} requires repository evidence"
                )
            if stage in {"deployment_verified", "releasable"} and not deployment_paths:
                _error(
                    errors, f"{prefix}.stage", f"{stage} requires deployment evidence"
                )
            if stage in {"repository_verified", "deployment_verified", "releasable"}:
                for path in repository_paths:
                    _evidence_receipt(
                        path,
                        "repository",
                        feature_id,
                        repo_root,
                        f"{prefix}.evidence.repository_paths",
                        errors,
                        evidence_revision,
                    )
            if stage in {"deployment_verified", "releasable"}:
                for path in deployment_paths:
                    _evidence_receipt(
                        path,
                        "deployment",
                        feature_id,
                        repo_root,
                        f"{prefix}.evidence.deployment_paths",
                        errors,
                        evidence_revision,
                    )
            if stage == "design" and deployment_paths:
                _error(
                    errors, f"{prefix}.stage", "design cannot claim deployment evidence"
                )
        for location, paths in all_paths:
            for path in paths:
                _repo_path(path, repo_root, location, errors)

    missing_features = sorted(REQUIRED_FEATURE_IDS - ids)
    if missing_features:
        _error(
            errors,
            "registry.features",
            f"is missing required feature ids: {', '.join(missing_features)}",
        )
    implemented_ui_routes = _implemented_ui_routes(repo_root, errors)
    errors.extend(
        _route_ownership_parity_errors(
            route_ownership,
            route_anchors,
            route_inventory,
            ids,
        )
    )
    errors.extend(
        _ui_route_ownership_parity_errors(
            ui_route_ownership,
            implemented_ui_routes,
            feature_ui_routes,
            ids,
        )
    )
    errors.extend(_rte(repo_root, route_ownership, ui_route_ownership, features))
    actual_models = set(MODEL_RE.findall(model_source))
    declared_models = set(model_owners)
    missing_models = sorted(actual_models - declared_models)
    unknown_models = sorted(declared_models - actual_models)
    if missing_models:
        _error(
            errors,
            "registry.features.data.models",
            f"does not classify persistent models: {', '.join(missing_models)}",
        )
    if unknown_models:
        _error(
            errors,
            "registry.features.data.models",
            f"declares models absent from apps/api/app/models.py: {', '.join(unknown_models)}",
        )
    if "mcp" not in protocol_ids:
        _error(
            errors,
            "registry.features.surfaces.protocols",
            "does not own the MCP surface",
        )
    if "a2a" not in protocol_ids:
        _error(
            errors,
            "registry.features.surfaces.protocols",
            "does not own the A2A surface",
        )

    lifecycle = next(
        (
            feature
            for feature in features
            if isinstance(feature, dict) and feature.get("id") == "account-lifecycle"
        ),
        None,
    )
    if isinstance(lifecycle, dict) and lifecycle.get("stage") == "feature_gated":
        agent_card_source = _read_anchor_source(
            repo_root, "apps/api/app/routes/agent_card.py", errors
        )
        errors.extend(
            _lifecycle_discovery_errors(api_source, discovery_source, agent_card_source)
        )
    errors.extend(_discovery_agreement_errors(repo_root))
    errors.extend(_llms_workflow_errors(discovery_source))
    errors.extend(_mcp_write_surface_errors(repo_root))
    errors.extend(_agent_authority_idempotency_surface_errors(repo_root))
    errors.extend(_impersonation_read_only_surface_errors(repo_root))
    errors.extend(_agent_grant_creation_durability_errors(repo_root))
    errors.extend(_agent_web_helper_extraction_errors(repo_root))
    errors.extend(_human_mode_surface_errors(repo_root))
    errors.extend(workspace_navigation_errors(repo_root))
    errors.extend(public_profile_identity_errors(repo_root))
    errors.extend(_public_html_mirror_surface_errors(repo_root))
    errors.extend(_logical_mutation_surface_errors(repo_root))
    errors.extend(_private_conversation_surface_errors(repo_root))
    errors.extend(_private_network_read_surface_errors(repo_root))
    errors.extend(_private_idempotency_surface_errors(repo_root))
    errors.extend(_follow_content_block_durability_errors(repo_root))
    errors.extend(_immutable_supply_chain_surface_errors(repo_root))
    errors.extend(_auth_return_intent_surface_errors(repo_root))
    errors.extend(_outreach_inbox_read_surface_errors(repo_root))
    errors.extend(_employer_inventory_surface_errors(repo_root))
    errors.extend(_recruiting_release_gate_errors(repo_root, route_inventory))
    errors.extend(_organization_membership_durability_errors(repo_root))
    errors.extend(_application_transition_durability_errors(repo_root))
    errors.extend(_contact_durability_errors(repo_root))
    errors.extend(_a2a_action_error_surface_errors(repo_root))
    errors.extend(_protected_agent_action_protocol_errors(repo_root))
    errors.extend(_agent_identity_durability_errors(repo_root))
    errors.extend(_mcp_outreach_parity_errors(repo_root))
    errors.extend(_contact_request_status_invariant_errors(repo_root))
    errors.extend(_lifecycle_default_errors(repo_root))
    errors.extend(
        _account_lifecycle_confirmation_surface_errors(repo_root, route_inventory)
    )
    errors.extend(_frontend_docker_context_errors(repo_root))
    errors.extend(_document_ingestion_built_image_errors(repo_root))
    errors.extend(_production_container_hardening_errors(repo_root))
    errors.extend(_search_projection_contract_errors(repo_root))
    errors.extend(
        _current_platform_surface_errors(
            repo_root,
            features,
            route_ownership,
            ui_route_ownership,
            route_inventory,
        )
    )
    errors.extend(_public_trust_surface_errors(repo_root, ui_route_ownership))
    errors.extend(
        _recruiting_evidence_surface_errors(repo_root, route_ownership, route_inventory)
    )
    errors.extend(_verification_event_scrub_errors(repo_root))
    errors.extend(
        _taxonomy_public_search_contract_errors(
            features, route_ownership, model_owners, repo_root
        )
    )
    errors.extend(_protocol_argument_contract_errors(repo_root))
    errors.extend(_cursor_contract_errors(repo_root))
    errors.extend(_api_semantic_parity_errors(repo_root))
    errors.extend(_agent_directory_search_contract_errors(features, repo_root))
    errors.extend(_production_operations_errors(repo_root))
    errors.extend(_release_matrix_feature_mapping_errors(repo_root, features))
    errors.extend(_required_feature_anchor_errors(features))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("packages/platform-contract/platform-features.json"),
    )
    parser.add_argument(
        "--route-ownership",
        type=Path,
        default=Path("packages/platform-contract/platform-route-ownership.json"),
    )
    parser.add_argument(
        "--ui-route-ownership",
        type=Path,
        default=Path("packages/platform-contract/platform-ui-route-ownership.json"),
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(
            "packages/platform-contract/platform-feature-registry.schema.json"
        ),
    )
    parser.add_argument(
        "--evidence-schema",
        type=Path,
        default=Path(
            "packages/platform-contract/platform-evidence-receipt.schema.json"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    errors = check_registry(
        args.registry,
        args.schema,
        args.repo_root,
        args.route_ownership,
        args.ui_route_ownership,
        args.evidence_schema,
    )
    if errors:
        print(f"platform feature registry: FAIL ({len(errors)} error(s))")
        for error in errors:
            print(f"- {error}")
        return 1
    print("platform feature registry: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
