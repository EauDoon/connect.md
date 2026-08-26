"""Add durable, content-free moderation evidence snapshot digests.

Revision ID: 0026_moderation_evidence_snapshots
Revises: 0025_exact_public_search
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_moderation_evidence_snapshots"
down_revision: str | None = "0025_exact_public_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing decisions and reviews predate snapshot binding. Keep them NULL;
    # manufacturing a digest during migration would fabricate provenance.
    with op.batch_alter_table("moderation_decisions") as batch:
        batch.add_column(sa.Column("evidence_snapshot_sha256", sa.String(length=64), nullable=True))
        batch.create_check_constraint(
            "ck_moderation_decisions_evidence_snapshot_sha256",
            "evidence_snapshot_sha256 IS NULL OR length(evidence_snapshot_sha256) = 64",
        )
    with op.batch_alter_table("moderation_appeals") as batch:
        batch.add_column(sa.Column("review_snapshot_sha256", sa.String(length=64), nullable=True))
        batch.create_check_constraint(
            "ck_moderation_appeals_review_snapshot_sha256",
            "review_snapshot_sha256 IS NULL OR length(review_snapshot_sha256) = 64",
        )


def downgrade() -> None:
    with op.batch_alter_table("moderation_appeals") as batch:
        batch.drop_constraint("ck_moderation_appeals_review_snapshot_sha256", type_="check")
        batch.drop_column("review_snapshot_sha256")
    with op.batch_alter_table("moderation_decisions") as batch:
        batch.drop_constraint("ck_moderation_decisions_evidence_snapshot_sha256", type_="check")
        batch.drop_column("evidence_snapshot_sha256")
