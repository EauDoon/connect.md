from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import func, select

from app import cli
from app.models import (
    Organization,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
)
from app.services.organization_verification import material_claim_digest
from app.services.recruiting_evidence import (
    canonical_evidence_path,
    claims_from_rows,
    verify_recruiting_evidence,
)

ORGANIZATION_ID = "70000000-0000-4000-8000-000000000001"
VERIFICATION_ID = "70000000-0000-4000-8000-000000000002"
EVIDENCE_ID = "70000000-0000-4000-8000-000000000003"
INITIAL_EVENT_ID = "70000000-0000-4000-8000-000000000004"
EVIDENCE_ACTION_CASES = (
    ("submitted", "review", "under_review"),
    ("under_review", "activate", "active"),
    ("under_review", "reject", "rejected"),
    ("suspended", "restore", "active"),
)


def _arguments(
    action: str,
    *,
    snapshot: str | None,
    material_digest: str | None = None,
    expires_at: str | None = None,
) -> Namespace:
    return Namespace(
        verification_id=VERIFICATION_ID,
        action=action,
        policy_version="recruiting-control-v1" if action in {"activate", "restore"} else None,
        material_claim_digest=(material_digest if action in {"activate", "restore"} else None),
        expires_at=expires_at if action in {"activate", "restore"} else None,
        expected_review_snapshot_sha256=snapshot,
    )


async def _seed_verification(
    app,
    *,
    state: str,
    evidence_expires_at: datetime | None = None,
) -> tuple[str, str]:
    now = datetime.now(UTC)
    payload = b"private recruiting control evidence"
    artifact_digest = sha256(payload).hexdigest()
    retained_until = evidence_expires_at or now + timedelta(days=30)
    snapshot_retained_until = retained_until if retained_until > now else now + timedelta(days=30)
    material_digest = material_claim_digest(
        organization_id=ORGANIZATION_ID,
        organization_name="Evidence Employer",
        organization_website_url=None,
        organization_material_version=1,
        evidence_kind="other",
        metadata={"registry": "verified"},
        artifact_content_type="text/plain",
        artifact_sha256=artifact_digest,
        artifact_size_bytes=len(payload),
    )
    organization = Organization(
        id=ORGANIZATION_ID,
        owner_id="owner",
        slug="evidence-employer",
        name="Evidence Employer",
        description=None,
        website_url=None,
        visibility="private",
        verification_status="verified",
        verification_material_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    verification = OrganizationVerification(
        id=VERIFICATION_ID,
        organization_id=ORGANIZATION_ID,
        purpose="recruiting_control",
        submitted_by_owner_id="owner",
        material_claim_digest=material_digest,
        created_at=now,
    )
    storage_path = canonical_evidence_path(ORGANIZATION_ID, VERIFICATION_ID, artifact_digest)
    evidence = OrganizationVerificationEvidence(
        id=EVIDENCE_ID,
        verification_id=VERIFICATION_ID,
        evidence_kind="other",
        metadata_json='{"registry":"verified"}',
        artifact_content_type="text/plain",
        artifact_sha256=artifact_digest,
        artifact_size_bytes=len(payload),
        storage_path=storage_path,
        created_at=now,
        retention_expires_at=snapshot_retained_until,
    )
    event = OrganizationVerificationEvent(
        id=INITIAL_EVENT_ID,
        verification_id=VERIFICATION_ID,
        organization_id=ORGANIZATION_ID,
        purpose="recruiting_control",
        to_state=state,
        actor_id="reviewer:preprovisioned",
        actor_role="recruiting_verifier",
        policy_version="recruiting-control-v1" if state in {"active", "suspended"} else None,
        material_claim_digest=material_digest,
        expires_at=now + timedelta(days=7) if state in {"active", "suspended"} else None,
        occurred_at=now,
    )
    app.state.store.write_immutable_bytes(storage_path, payload)
    snapshot = verify_recruiting_evidence(
        app.state.store,
        claims_from_rows(organization, verification, evidence),
        now=now,
    ).review_snapshot_sha256
    evidence.retention_expires_at = retained_until
    async with app.state.session_factory() as session:
        session.add_all((organization, verification, evidence, event))
        await session.commit()
    return material_digest, snapshot


def _configure_cli(app, monkeypatch) -> None:
    reviewer_settings = app.state.settings.model_copy(
        update={
            "verification_reviewer_id": "reviewer:preprovisioned",
            "verification_reviewer_role": "recruiting_verifier",
        }
    )
    monkeypatch.setattr(cli, "get_settings", lambda: reviewer_settings)


async def _event_count(app) -> int:
    async with app.state.session_factory() as session:
        return int(
            await session.scalar(
                select(func.count(OrganizationVerificationEvent.id)).where(
                    OrganizationVerificationEvent.verification_id == VERIFICATION_ID
                )
            )
            or 0
        )


@pytest.mark.parametrize(
    ("state", "action", "expected_state"),
    EVIDENCE_ACTION_CASES,
)
async def test_evidence_dependent_cli_transitions_require_and_accept_exact_snapshot(
    api_client, monkeypatch, state: str, action: str, expected_state: str
) -> None:
    app, _ = api_client
    material_digest, snapshot = await _seed_verification(app, state=state)
    _configure_cli(app, monkeypatch)
    expires_at = (datetime.now(UTC) + timedelta(days=3)).isoformat()

    assert (
        await cli.apply_verification_transition(
            _arguments(
                action,
                snapshot=snapshot,
                material_digest=material_digest,
                expires_at=expires_at,
            )
        )
        == 0
    )
    async with app.state.session_factory() as session:
        latest = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(OrganizationVerificationEvent.verification_id == VERIFICATION_ID)
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
    assert latest is not None
    assert latest.to_state == expected_state


@pytest.mark.parametrize(
    ("state", "action", "_expected_state"),
    EVIDENCE_ACTION_CASES,
)
async def test_cli_rejects_missing_or_stale_snapshot_without_event(
    api_client, monkeypatch, state: str, action: str, _expected_state: str
) -> None:
    app, _ = api_client
    material_digest, _ = await _seed_verification(app, state=state)
    _configure_cli(app, monkeypatch)
    expires_at = (datetime.now(UTC) + timedelta(days=3)).isoformat()

    assert (
        await cli.apply_verification_transition(
            _arguments(
                action,
                snapshot=None,
                material_digest=material_digest,
                expires_at=expires_at,
            )
        )
        == 2
    )
    assert (
        await cli.apply_verification_transition(
            _arguments(
                action,
                snapshot="0" * 64,
                material_digest=material_digest,
                expires_at=expires_at,
            )
        )
        == 1
    )
    assert await _event_count(app) == 1


@pytest.mark.parametrize("fault", ["missing", "corrupt", "expired"])
@pytest.mark.parametrize(
    ("state", "action", "_expected_state"),
    EVIDENCE_ACTION_CASES,
)
async def test_cli_rejects_unavailable_evidence_without_event(
    api_client,
    monkeypatch,
    fault: str,
    state: str,
    action: str,
    _expected_state: str,
) -> None:
    app, _ = api_client
    retained_until = datetime.now(UTC) - timedelta(seconds=1) if fault == "expired" else None
    material_digest, snapshot = await _seed_verification(
        app, state=state, evidence_expires_at=retained_until
    )
    _configure_cli(app, monkeypatch)
    async with app.state.session_factory() as session:
        evidence = await session.get(OrganizationVerificationEvidence, EVIDENCE_ID)
        assert evidence is not None
        path = app.state.store._absolute(evidence.storage_path)
    if fault == "missing":
        path.unlink()
    elif fault == "corrupt":
        path.write_bytes(b"tampered")

    assert (
        await cli.apply_verification_transition(
            _arguments(
                action,
                snapshot=snapshot,
                material_digest=material_digest,
                expires_at=(datetime.now(UTC) + timedelta(days=3)).isoformat(),
            )
        )
        == 1
    )
    assert await _event_count(app) == 1


@pytest.mark.parametrize(
    ("action", "expected_state"),
    [("suspend", "suspended"), ("revoke", "revoked"), ("expire", "expired")],
)
async def test_non_evidence_cli_transitions_remain_available_without_snapshot(
    api_client, monkeypatch, action: str, expected_state: str
) -> None:
    app, _ = api_client
    await _seed_verification(app, state="active")
    _configure_cli(app, monkeypatch)

    assert await cli.apply_verification_transition(_arguments(action, snapshot=None)) == 0
    async with app.state.session_factory() as session:
        latest = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(OrganizationVerificationEvent.verification_id == VERIFICATION_ID)
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
    assert latest is not None
    assert latest.to_state == expected_state


@pytest.mark.parametrize("action", ["activate", "restore"])
async def test_cli_active_transitions_fail_before_database_when_recruiting_is_disabled(
    api_client, monkeypatch, capsys, action: str
) -> None:
    app, _ = api_client
    disabled = app.state.settings.model_copy(update={"recruiting_enabled": False})
    monkeypatch.setattr(cli, "get_settings", lambda: disabled)
    monkeypatch.setattr(
        cli,
        "build_engine",
        lambda _settings: pytest.fail("disabled activation must not construct a database engine"),
    )

    assert await cli.apply_verification_transition(_arguments(action, snapshot=None)) == 2
    assert capsys.readouterr().err == "recruiting release is disabled\n"


async def test_cli_defensive_transition_is_not_blocked_by_recruiting_release_gate(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    disabled = app.state.settings.model_copy(update={"recruiting_enabled": False})
    monkeypatch.setattr(cli, "get_settings", lambda: disabled)

    class DatabaseReached(RuntimeError):
        pass

    def database_reached(_settings):
        raise DatabaseReached

    monkeypatch.setattr(cli, "build_engine", database_reached)
    with pytest.raises(DatabaseReached):
        await cli.apply_verification_transition(_arguments("suspend", snapshot=None))
