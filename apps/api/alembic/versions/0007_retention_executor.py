"""Add durable retention tasks, holds, tombstones, and missing expiry fields.

Revision ID: 0007_retention_executor
Revises: 0006_organization_verification
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_retention_executor"
down_revision: str | None = "0006_organization_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_backfilled_expiry(table: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.add_column(
            sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(f"UPDATE {table} SET retention_expires_at = created_at + INTERVAL '365 days'")
        )
    else:
        op.execute(
            sa.text(f"UPDATE {table} SET retention_expires_at = datetime(created_at, '+365 days')")
        )
    with op.batch_alter_table(table) as batch:
        batch.alter_column("retention_expires_at", nullable=False)


def upgrade() -> None:
    _add_backfilled_expiry("contact_requests")
    _add_backfilled_expiry("organization_verification_evidence")

    op.create_table(
        "lifecycle_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=120), nullable=True),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'leased', 'completed', 'dead_letter')",
            name="ck_lifecycle_tasks_state",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_lifecycle_tasks_attempts"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_type", "resource_id", name="uq_lifecycle_tasks_resource"),
    )
    op.create_index(
        "ix_lifecycle_tasks_claim", "lifecycle_tasks", ["state", "available_at", "created_at"]
    )

    op.create_table(
        "retention_holds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=160), nullable=False),
        sa.Column("authority", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by_authority", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "review_at <= expires_at", name="ck_retention_holds_review_before_expiry"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_retention_holds_resource",
        "retention_holds",
        ["resource_type", "resource_id", "expires_at"],
    )

    op.create_table(
        "retention_tombstones",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_type", "resource_id", name="uq_retention_tombstones_resource"
        ),
    )
    op.create_index("ix_retention_tombstones_disposed", "retention_tombstones", ["disposed_at"])


def downgrade() -> None:
    op.drop_index("ix_retention_tombstones_disposed", table_name="retention_tombstones")
    op.drop_table("retention_tombstones")
    op.drop_index("ix_retention_holds_resource", table_name="retention_holds")
    op.drop_table("retention_holds")
    op.drop_index("ix_lifecycle_tasks_claim", table_name="lifecycle_tasks")
    op.drop_table("lifecycle_tasks")
    with op.batch_alter_table("organization_verification_evidence") as batch:
        batch.drop_column("retention_expires_at")
    with op.batch_alter_table("contact_requests") as batch:
        batch.drop_column("retention_expires_at")
