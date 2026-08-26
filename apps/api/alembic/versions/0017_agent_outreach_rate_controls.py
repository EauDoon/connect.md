"""Add recipient-inbox and direct-source agent-outreach rate controls.

Revision ID: 0017_agent_outreach_rate_controls
Revises: 0016_lifecycle_receipts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_agent_outreach_rate_controls"
down_revision: str | None = "0016_lifecycle_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_outreach_recipient_rate_buckets",
        sa.Column("recipient_owner_id", sa.String(length=255), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("recipient_owner_id", "bucket_date"),
    )
    op.create_table(
        "agent_outreach_direct_peer_rate_buckets",
        sa.Column("direct_peer_hmac", sa.String(length=64), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("direct_peer_hmac", "bucket_date"),
    )


def downgrade() -> None:
    op.drop_table("agent_outreach_direct_peer_rate_buckets")
    op.drop_table("agent_outreach_recipient_rate_buckets")
