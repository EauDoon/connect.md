"""Make contact duplicate and daily quota enforcement atomic.

Revision ID: 0003_contact_abuse_boundaries
Revises: 0002_protocol_core
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_contact_abuse_boundaries"
down_revision: str | None = "0002_protocol_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact_rate_buckets",
        sa.Column("sender_owner_id", sa.String(length=255), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("sender_owner_id", "bucket_date"),
    )
    # A pre-0003 deployment could have admitted duplicate pending rows during
    # a race. Preserve the oldest request and retain later rows as rejected
    # audit evidence before installing the invariant.
    op.execute(
        sa.text(
            """
            WITH ranked_pending AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY sender_owner_id, recipient_owner_id
                        ORDER BY created_at ASC, id ASC
                    ) AS position
                FROM contact_requests
                WHERE status = 'pending'
            )
            UPDATE contact_requests
            SET
                status = 'rejected',
                decision_actor_id = 'system:migration:0003',
                decided_at = CURRENT_TIMESTAMP
            WHERE id IN (
                SELECT id FROM ranked_pending WHERE position > 1
            )
            """
        )
    )
    op.create_index(
        "uq_contact_requests_pending_pair",
        "contact_requests",
        ["sender_owner_id", "recipient_owner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_contact_requests_pending_pair", table_name="contact_requests")
    op.drop_table("contact_rate_buckets")
