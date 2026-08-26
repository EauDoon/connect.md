"""Pure, fail-closed material-claim binding for recruiting verification."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256

RECRUITING_CONTROL_PURPOSE = "recruiting_control"


def material_claim_digest(
    *,
    organization_id: str,
    organization_name: str,
    organization_website_url: str | None,
    organization_material_version: int,
    evidence_kind: str,
    metadata: Mapping[str, str],
    artifact_content_type: str,
    artifact_sha256: str,
    artifact_size_bytes: int,
) -> str:
    """Return the canonical digest a reviewer decision is allowed to activate."""
    claims = {
        "artifact_content_type": artifact_content_type,
        "artifact_sha256": artifact_sha256,
        "artifact_size_bytes": artifact_size_bytes,
        "evidence_kind": evidence_kind,
        "metadata": dict(metadata),
        "organization_id": organization_id,
        "organization_material_version": organization_material_version,
        "organization_name": organization_name,
        "organization_website_url": organization_website_url,
        "purpose": RECRUITING_CONTROL_PURPOSE,
    }
    return sha256(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def material_claim_digest_from_stored_metadata(
    *,
    organization_id: str,
    organization_name: str,
    organization_website_url: str | None,
    organization_material_version: int,
    evidence_kind: str,
    metadata_json: str,
    artifact_content_type: str,
    artifact_sha256: str,
    artifact_size_bytes: int,
) -> str | None:
    """Rebuild a current digest or fail closed for malformed stored metadata."""
    try:
        metadata = json.loads(metadata_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(metadata, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in metadata.items()
    ):
        return None
    return material_claim_digest(
        organization_id=organization_id,
        organization_name=organization_name,
        organization_website_url=organization_website_url,
        organization_material_version=organization_material_version,
        evidence_kind=evidence_kind,
        metadata=metadata,
        artifact_content_type=artifact_content_type,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=artifact_size_bytes,
    )
