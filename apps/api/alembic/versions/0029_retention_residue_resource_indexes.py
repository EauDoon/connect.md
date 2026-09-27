"""Index retention residue lookups by resource identity.

Revision ID: 0029_retention_residue_resource_indexes
Revises: 0028_scrub_verification_change_payloads
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029_retention_residue_resource_indexes"
down_revision: str | None = "0028_scrub_verification_change_payloads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The retention worker deletes idempotency and change-event residues with a
    # resource_type/resource_id predicate for every disposed resource. Without
    # these indexes each of those deletes is a sequential scan of a table that
    # only ever grows. CREATE INDEX is not concurrent here because the offline
    # SQLite migration path and the locked PostgreSQL deploy step both own an
    # empty-window schema change.
    op.create_index(
        "ix_change_events_resource",
        "change_events",
        ["resource_type", "resource_id"],
    )
    op.create_index(
        "ix_idempotency_resource",
        "idempotency_records",
        ["resource_type", "resource_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_resource", table_name="idempotency_records")
    op.drop_index("ix_change_events_resource", table_name="change_events")