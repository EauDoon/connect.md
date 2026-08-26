"""Remove private evidence commitments from generic verification changes.

Revision ID: 0028_scrub_verification_change_payloads
Revises: 0027_application_snapshot_size
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_scrub_verification_change_payloads"
down_revision: str | None = "0027_application_snapshot_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_SANITIZED_PAYLOAD = json.dumps({"state": "submitted"}, sort_keys=True)
_BATCH_SIZE = 500


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validated_sanitized_payload(payload: object) -> str:
    if not isinstance(payload, str):
        raise RuntimeError("organization verification change payload is not canonical")
    try:
        parsed = json.loads(payload, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise RuntimeError("organization verification change payload is not canonical") from None
    if parsed == {"state": "submitted"}:
        return _SANITIZED_PAYLOAD
    if (
        isinstance(parsed, dict)
        and set(parsed) == {"artifact_sha256", "state"}
        and parsed.get("state") == "submitted"
        and isinstance(parsed.get("artifact_sha256"), str)
        and _SHA256_HEX.fullmatch(parsed["artifact_sha256"]) is not None
    ):
        return _SANITIZED_PAYLOAD
    raise RuntimeError("organization verification change payload is not canonical")


def upgrade() -> None:
    bind = op.get_bind()
    last_sequence = 0
    while True:
        rows = (
            bind.execute(
                sa.text(
                    """
                    SELECT sequence, payload
                    FROM change_events
                    WHERE resource_type = 'organization_verification'
                      AND event_type = 'organization_verification.submitted'
                      AND sequence > :last_sequence
                    ORDER BY sequence
                    LIMIT :batch_size
                    """
                ),
                {"last_sequence": last_sequence, "batch_size": _BATCH_SIZE},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        for row in rows:
            sanitized = _validated_sanitized_payload(row["payload"])
            if row["payload"] != sanitized:
                bind.execute(
                    sa.text(
                        "UPDATE change_events SET payload = :payload WHERE sequence = :sequence"
                    ),
                    {"payload": sanitized, "sequence": row["sequence"]},
                )
            last_sequence = int(row["sequence"])


def downgrade() -> None:
    # Privacy minimization is intentionally irreversible. The authoritative
    # evidence row retains the digest required by receipt reconstruction.
    pass
