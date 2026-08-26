"""Add private lifecycle status receipt capability.

Revision ID: 0016_lifecycle_receipts
Revises: 0015_account_lifecycle_stage_two
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_lifecycle_receipts"
down_revision: str | None = "0015_account_lifecycle_stage_two"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_lifecycles", sa.Column("receipt_hmac", sa.String(length=64), nullable=True)
    )
    op.add_column("account_lifecycles", sa.Column("receipt_ciphertext", sa.Text(), nullable=True))
    op.add_column(
        "account_lifecycles",
        sa.Column("receipt_recovery_idempotency_hmac", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_account_lifecycles_receipt_hmac", "account_lifecycles", ["receipt_hmac"], unique=True
    )
    with op.batch_alter_table("account_reverification_uses", recreate="always") as batch:
        batch.drop_constraint("ck_account_reverification_uses_purpose", type_="check")
        batch.create_check_constraint(
            "ck_account_reverification_uses_purpose",
            "purpose IN ('export', 'delete_request', 'delete_confirm', 'delete_receipt_recover')",
        )
    op.create_table(
        "account_lifecycle_receipt_rate_limits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deletion_id", sa.String(length=36), nullable=False),
        sa.Column("receipt_hmac", sa.String(length=64), nullable=False),
        sa.Column("ip_hmac", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deletion_id"], ["account_lifecycles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "receipt_hmac", "ip_hmac", "window_started_at", name="uq_lifecycle_receipt_rate"
        ),
    )
    op.create_index(
        "ix_lifecycle_receipt_rate_window",
        "account_lifecycle_receipt_rate_limits",
        ["window_started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lifecycle_receipt_rate_window", table_name="account_lifecycle_receipt_rate_limits"
    )
    op.drop_table("account_lifecycle_receipt_rate_limits")
    with op.batch_alter_table("account_reverification_uses", recreate="always") as batch:
        batch.drop_constraint("ck_account_reverification_uses_purpose", type_="check")
        batch.create_check_constraint(
            "ck_account_reverification_uses_purpose",
            "purpose IN ('export', 'delete_request', 'delete_confirm')",
        )
    op.drop_index("ix_account_lifecycles_receipt_hmac", table_name="account_lifecycles")
    op.drop_column("account_lifecycles", "receipt_recovery_idempotency_hmac")
    op.drop_column("account_lifecycles", "receipt_ciphertext")
    op.drop_column("account_lifecycles", "receipt_hmac")
