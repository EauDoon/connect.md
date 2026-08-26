"""Bind application snapshot authority to an exact byte length.

Revision ID: 0027_application_snapshot_size
Revises: 0026_moderation_evidence_snapshots
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_application_snapshot_size"
down_revision: str | None = "0026_moderation_evidence_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy rows remain NULL: deriving a size from mutable or unavailable local
    # bytes during migration would manufacture authority. New writes are exact.
    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("snapshot_size_bytes", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "ck_applications_snapshot_size",
            "snapshot_size_bytes IS NULL OR "
            "(snapshot_size_bytes > 0 AND snapshot_size_bytes <= 131072)",
        )


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.drop_constraint("ck_applications_snapshot_size", type_="check")
        batch.drop_column("snapshot_size_bytes")
