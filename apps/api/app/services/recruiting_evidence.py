"""Fail-closed verification of private recruiting-control evidence bytes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from app.services.organization_verification import (
    RECRUITING_CONTROL_PURPOSE,
    material_claim_digest,
)
from app.services.storage import StorageIntegrityError

VERIFICATION_ARTIFACT_MAX_BYTES = 262_144
VERIFICATION_METADATA_MAX_ITEMS = 20
VERIFICATION_METADATA_KEY_MAX_LENGTH = 64
VERIFICATION_METADATA_VALUE_MAX_LENGTH = 500
VERIFICATION_METADATA_JSON_MAX_CHARACTERS = 131_072
REVIEW_SNAPSHOT_SCHEMA = "connect.md/recruiting-verification-review-snapshot"
REVIEW_SNAPSHOT_SCHEMA_VERSION = 1

VERIFICATION_ARTIFACT_EXTENSIONS: Mapping[str, str] = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "text/plain": "txt",
}

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RecruitingEvidenceUnavailable(RuntimeError):
    """The private artifact cannot safely support recruiting authority."""


class VerifiedByteStore(Protocol):
    """Minimal storage contract required by the recruiting evidence verifier."""

    def read_verified_bytes(
        self,
        relative_path: str,
        expected_sha256: str,
        *,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> bytes: ...


class OrganizationEvidenceSource(Protocol):
    id: str
    name: str
    website_url: str | None
    verification_material_version: int


class VerificationEvidenceSource(Protocol):
    id: str
    purpose: str
    material_claim_digest: str


class ArtifactEvidenceSource(Protocol):
    evidence_kind: str
    metadata_json: str
    artifact_content_type: str
    artifact_sha256: str
    artifact_size_bytes: int
    storage_path: str
    retention_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RecruitingEvidenceClaims:
    organization_id: str
    organization_name: str
    organization_website_url: str | None
    organization_material_version: int
    verification_id: str
    purpose: str
    stored_material_claim_digest: str
    evidence_kind: str
    metadata_json: str
    artifact_content_type: str
    artifact_sha256: str
    artifact_size_bytes: int
    storage_path: str
    retention_expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedRecruitingEvidence:
    payload: bytes
    metadata: Mapping[str, str]
    artifact_sha256: str
    artifact_size_bytes: int
    material_claim_digest: str
    review_snapshot_sha256: str


def claims_from_rows(
    organization: OrganizationEvidenceSource,
    verification: VerificationEvidenceSource,
    evidence: ArtifactEvidenceSource,
) -> RecruitingEvidenceClaims:
    """Adapt current ORM-like rows without coupling this service to SQLAlchemy models."""

    return RecruitingEvidenceClaims(
        organization_id=organization.id,
        organization_name=organization.name,
        organization_website_url=organization.website_url,
        organization_material_version=organization.verification_material_version,
        verification_id=verification.id,
        purpose=verification.purpose,
        stored_material_claim_digest=verification.material_claim_digest,
        evidence_kind=evidence.evidence_kind,
        metadata_json=evidence.metadata_json,
        artifact_content_type=evidence.artifact_content_type,
        artifact_sha256=evidence.artifact_sha256,
        artifact_size_bytes=evidence.artifact_size_bytes,
        storage_path=evidence.storage_path,
        retention_expires_at=evidence.retention_expires_at,
    )


def canonical_evidence_path(
    organization_id: str, verification_id: str, artifact_sha256: str
) -> str:
    """Return the sole storage path allowed for one submitted artifact."""

    organization = _canonical_uuid(organization_id, "organization id")
    verification = _canonical_uuid(verification_id, "verification id")
    digest = _canonical_sha256(artifact_sha256, "artifact digest")
    return f"verification-evidence/{organization}/{verification}/{digest}.bin"


def parse_evidence_metadata(metadata_json: str) -> dict[str, str]:
    """Parse the stored mapping with the original submission bounds and no duplicate keys."""

    if (
        not isinstance(metadata_json, str)
        or len(metadata_json) > VERIFICATION_METADATA_JSON_MAX_CHARACTERS
    ):
        raise RecruitingEvidenceUnavailable("verification evidence metadata is unavailable")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate metadata key")
            result[key] = value
        return result

    try:
        parsed = json.loads(metadata_json, object_pairs_hook=unique_object)
    except (TypeError, ValueError) as exc:
        raise RecruitingEvidenceUnavailable(
            "verification evidence metadata is unavailable"
        ) from exc
    if not isinstance(parsed, dict) or len(parsed) > VERIFICATION_METADATA_MAX_ITEMS:
        raise RecruitingEvidenceUnavailable("verification evidence metadata is unavailable")
    metadata: dict[str, str] = {}
    for key, value in parsed.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or len(key) > VERIFICATION_METADATA_KEY_MAX_LENGTH
            or len(value) > VERIFICATION_METADATA_VALUE_MAX_LENGTH
        ):
            raise RecruitingEvidenceUnavailable("verification evidence metadata is unavailable")
        metadata[key] = value
    return metadata


def review_snapshot_sha256(
    claims: RecruitingEvidenceClaims,
    *,
    metadata: Mapping[str, str],
    actual_artifact_sha256: str,
    actual_artifact_size_bytes: int,
    material_digest: str,
) -> str:
    """Bind the exact reviewable claims and verified bytes to one stable validator."""

    snapshot = {
        "evidence": {
            "artifact_content_type": claims.artifact_content_type,
            "artifact_sha256": actual_artifact_sha256,
            "artifact_size_bytes": actual_artifact_size_bytes,
            "kind": claims.evidence_kind,
            "metadata": dict(metadata),
            "retention_expires_at": _canonical_timestamp(claims.retention_expires_at),
        },
        "material_claim_digest": material_digest,
        "organization": {
            "id": claims.organization_id,
            "material_version": claims.organization_material_version,
            "name": claims.organization_name,
            "website_url": claims.organization_website_url,
        },
        "purpose": claims.purpose,
        "schema": REVIEW_SNAPSHOT_SCHEMA,
        "schema_version": REVIEW_SNAPSHOT_SCHEMA_VERSION,
        "verification_id": claims.verification_id,
    }
    canonical = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def verify_recruiting_evidence(
    store: VerifiedByteStore,
    claims: RecruitingEvidenceClaims,
    *,
    now: datetime,
) -> VerifiedRecruitingEvidence:
    """Verify current claims, retention, canonical placement, size, and exact bytes."""

    _validate_claims(claims, now=now)
    metadata = parse_evidence_metadata(claims.metadata_json)
    expected_path = canonical_evidence_path(
        claims.organization_id, claims.verification_id, claims.artifact_sha256
    )
    if claims.storage_path != expected_path:
        raise RecruitingEvidenceUnavailable("verification evidence storage is unavailable")

    try:
        payload = store.read_verified_bytes(
            expected_path,
            claims.artifact_sha256,
            expected_size_bytes=claims.artifact_size_bytes,
            max_size_bytes=VERIFICATION_ARTIFACT_MAX_BYTES,
        )
    except (OSError, StorageIntegrityError) as exc:
        raise RecruitingEvidenceUnavailable("verification evidence storage is unavailable") from exc

    actual_size = len(payload)
    actual_sha256 = sha256(payload).hexdigest()
    if actual_size != claims.artifact_size_bytes or not compare_digest(
        actual_sha256, claims.artifact_sha256
    ):
        raise RecruitingEvidenceUnavailable("verification evidence storage is unavailable")

    current_material_digest = material_claim_digest(
        organization_id=claims.organization_id,
        organization_name=claims.organization_name,
        organization_website_url=claims.organization_website_url,
        organization_material_version=claims.organization_material_version,
        evidence_kind=claims.evidence_kind,
        metadata=metadata,
        artifact_content_type=claims.artifact_content_type,
        artifact_sha256=actual_sha256,
        artifact_size_bytes=actual_size,
    )
    if not compare_digest(current_material_digest, claims.stored_material_claim_digest):
        raise RecruitingEvidenceUnavailable("verification evidence claims are unavailable")

    snapshot_digest = review_snapshot_sha256(
        claims,
        metadata=metadata,
        actual_artifact_sha256=actual_sha256,
        actual_artifact_size_bytes=actual_size,
        material_digest=current_material_digest,
    )
    return VerifiedRecruitingEvidence(
        payload=payload,
        metadata=metadata,
        artifact_sha256=actual_sha256,
        artifact_size_bytes=actual_size,
        material_claim_digest=current_material_digest,
        review_snapshot_sha256=snapshot_digest,
    )


def artifact_extension(content_type: str) -> str:
    """Map only the four accepted evidence media types to server-owned extensions."""

    try:
        return VERIFICATION_ARTIFACT_EXTENSIONS[content_type]
    except KeyError as exc:
        raise RecruitingEvidenceUnavailable("verification evidence type is unavailable") from exc


def _validate_claims(claims: RecruitingEvidenceClaims, *, now: datetime) -> None:
    _canonical_uuid(claims.organization_id, "organization id")
    _canonical_uuid(claims.verification_id, "verification id")
    _canonical_sha256(claims.artifact_sha256, "artifact digest")
    _canonical_sha256(claims.stored_material_claim_digest, "material claim digest")
    if claims.purpose != RECRUITING_CONTROL_PURPOSE:
        raise RecruitingEvidenceUnavailable("verification evidence purpose is unavailable")
    if claims.evidence_kind not in {
        "corporate_registration",
        "domain_control",
        "employment_authority",
        "other",
    }:
        raise RecruitingEvidenceUnavailable("verification evidence kind is unavailable")
    artifact_extension(claims.artifact_content_type)
    if (
        isinstance(claims.artifact_size_bytes, bool)
        or not isinstance(claims.artifact_size_bytes, int)
        or not 1 <= claims.artifact_size_bytes <= VERIFICATION_ARTIFACT_MAX_BYTES
    ):
        raise RecruitingEvidenceUnavailable("verification evidence size is unavailable")
    if (
        isinstance(claims.organization_material_version, bool)
        or not isinstance(claims.organization_material_version, int)
        or claims.organization_material_version < 1
    ):
        raise RecruitingEvidenceUnavailable("organization verification material is unavailable")
    if (
        not isinstance(claims.organization_name, str)
        or not claims.organization_name
        or len(claims.organization_name) > 160
    ):
        raise RecruitingEvidenceUnavailable("organization verification material is unavailable")
    if claims.organization_website_url is not None:
        if (
            not isinstance(claims.organization_website_url, str)
            or len(claims.organization_website_url) > 2_048
        ):
            raise RecruitingEvidenceUnavailable("organization verification material is unavailable")
        parsed = urlsplit(claims.organization_website_url)
        host = (parsed.hostname or "").lower()
        try:
            address = ip_address(host)
            local_address = address.is_loopback or address.is_unspecified
        except ValueError:
            local_address = False
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or host == "localhost"
            or host.endswith(".localhost")
            or local_address
        ):
            raise RecruitingEvidenceUnavailable("organization verification material is unavailable")
    if _as_utc(claims.retention_expires_at) <= _as_utc(now):
        raise RecruitingEvidenceUnavailable("verification evidence retention has expired")


def _canonical_uuid(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise RecruitingEvidenceUnavailable(f"verification evidence {label} is unavailable")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as exc:
        raise RecruitingEvidenceUnavailable(
            f"verification evidence {label} is unavailable"
        ) from exc
    canonical = str(parsed)
    if value != canonical or parsed.version != 4:
        raise RecruitingEvidenceUnavailable(f"verification evidence {label} is unavailable")
    return canonical


def _canonical_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise RecruitingEvidenceUnavailable(f"verification evidence {label} is unavailable")
    return value


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise RecruitingEvidenceUnavailable("verification evidence timestamp is unavailable")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")
