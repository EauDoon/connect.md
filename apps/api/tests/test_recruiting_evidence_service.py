from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import (
    RecruitingEvidenceClaims,
    RecruitingEvidenceUnavailable,
    artifact_extension,
    canonical_evidence_path,
    parse_evidence_metadata,
    verify_recruiting_evidence,
)

ORGANIZATION_ID = "11111111-1111-4111-8111-111111111111"
VERIFICATION_ID = "22222222-2222-4222-8222-222222222222"
PAYLOAD = b"private reviewer evidence"
PAYLOAD_SHA256 = sha256(PAYLOAD).hexdigest()


class FakeVerifiedStore:
    def __init__(self, payload: bytes = PAYLOAD) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, int, int]] = []

    def read_verified_bytes(
        self,
        relative_path: str,
        expected_sha256: str,
        *,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> bytes:
        self.calls.append((relative_path, expected_sha256, expected_size_bytes, max_size_bytes))
        return self.payload


def claims(*, now: datetime | None = None) -> RecruitingEvidenceClaims:
    current = now or datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    metadata = {"jurisdiction": "SG", "registration": "2026-001"}
    digest = material_claim_digest(
        organization_id=ORGANIZATION_ID,
        organization_name="Acme",
        organization_website_url="https://acme.example",
        organization_material_version=3,
        evidence_kind="corporate_registration",
        metadata=metadata,
        artifact_content_type="text/plain",
        artifact_sha256=PAYLOAD_SHA256,
        artifact_size_bytes=len(PAYLOAD),
    )
    path = canonical_evidence_path(ORGANIZATION_ID, VERIFICATION_ID, PAYLOAD_SHA256)
    return RecruitingEvidenceClaims(
        organization_id=ORGANIZATION_ID,
        organization_name="Acme",
        organization_website_url="https://acme.example",
        organization_material_version=3,
        verification_id=VERIFICATION_ID,
        purpose="recruiting_control",
        stored_material_claim_digest=digest,
        evidence_kind="corporate_registration",
        metadata_json=json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        artifact_content_type="text/plain",
        artifact_sha256=PAYLOAD_SHA256,
        artifact_size_bytes=len(PAYLOAD),
        storage_path=path,
        retention_expires_at=current + timedelta(days=30),
    )


def test_verifies_exact_bytes_and_returns_stable_review_snapshot() -> None:
    now = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)
    store = FakeVerifiedStore()

    verified = verify_recruiting_evidence(store, claims(now=now), now=now)

    assert verified.payload == PAYLOAD
    assert verified.metadata == {"jurisdiction": "SG", "registration": "2026-001"}
    assert verified.artifact_sha256 == PAYLOAD_SHA256
    assert verified.artifact_size_bytes == len(PAYLOAD)
    assert verified.material_claim_digest == claims(now=now).stored_material_claim_digest
    assert verified.review_snapshot_sha256 == (
        "687371557885bef9532a1eddf90e891e51c0b8a771cb874eef41f49c258bd738"
    )
    assert store.calls == [
        (
            canonical_evidence_path(ORGANIZATION_ID, VERIFICATION_ID, PAYLOAD_SHA256),
            PAYLOAD_SHA256,
            len(PAYLOAD),
            262_144,
        )
    ]


def test_metadata_rejects_duplicates_and_submission_bound_drift() -> None:
    with pytest.raises(RecruitingEvidenceUnavailable):
        parse_evidence_metadata('{"registration":"one","registration":"two"}')
    with pytest.raises(RecruitingEvidenceUnavailable):
        parse_evidence_metadata(json.dumps({"": "value"}))
    with pytest.raises(RecruitingEvidenceUnavailable):
        parse_evidence_metadata(json.dumps({"key": "x" * 501}))
    with pytest.raises(RecruitingEvidenceUnavailable):
        parse_evidence_metadata(json.dumps({f"key-{index}": "value" for index in range(21)}))


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("purpose", "identity_control"),
        ("artifact_content_type", "text/html"),
        ("artifact_size_bytes", 0),
        ("organization_material_version", 0),
        ("storage_path", "verification-evidence/elsewhere.bin"),
        ("artifact_sha256", "A" * 64),
        ("organization_name", "x" * 161),
        ("organization_website_url", "http://acme.example"),
        ("verification_id", "22222222-2222-4222-8222-22222222222A"),
    ],
)
def test_invalid_claims_fail_before_storage_read(change: str, value: object) -> None:
    store = FakeVerifiedStore()
    invalid = replace(claims(), **{change: value})

    with pytest.raises(RecruitingEvidenceUnavailable):
        verify_recruiting_evidence(store, invalid, now=datetime(2026, 8, 11, tzinfo=UTC))

    assert store.calls == []


def test_expired_evidence_fails_before_storage_read() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    store = FakeVerifiedStore()

    with pytest.raises(RecruitingEvidenceUnavailable):
        verify_recruiting_evidence(
            store, replace(claims(now=now), retention_expires_at=now), now=now
        )

    assert store.calls == []


@pytest.mark.parametrize("payload", [b"tampered", PAYLOAD + b"x"])
def test_store_output_is_independently_rehashed(payload: bytes) -> None:
    with pytest.raises(RecruitingEvidenceUnavailable):
        verify_recruiting_evidence(
            FakeVerifiedStore(payload), claims(), now=datetime(2026, 8, 11, tzinfo=UTC)
        )


def test_material_claim_drift_fails_closed() -> None:
    drifted = replace(claims(), organization_name="Renamed after submission")

    with pytest.raises(RecruitingEvidenceUnavailable):
        verify_recruiting_evidence(
            FakeVerifiedStore(), drifted, now=datetime(2026, 8, 11, tzinfo=UTC)
        )


def test_server_owned_extensions_cover_only_the_allowlist() -> None:
    assert artifact_extension("application/pdf") == "pdf"
    assert artifact_extension("image/jpeg") == "jpg"
    assert artifact_extension("image/png") == "png"
    assert artifact_extension("text/plain") == "txt"
    with pytest.raises(RecruitingEvidenceUnavailable):
        artifact_extension("image/svg+xml")
