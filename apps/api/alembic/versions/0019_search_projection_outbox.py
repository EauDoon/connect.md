"""Add the durable, version-keyed search projection outbox.

Revision ID: 0019_search_projection_outbox
Revises: 0018_organization_membership_profile_handle
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_search_projection_outbox"
down_revision: str | None = "0018_organization_membership_profile_handle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_projection_tasks",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'leased', 'dead_letter')",
            name="ck_search_projection_tasks_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_search_projection_tasks_version"),
        sa.CheckConstraint("attempts >= 0", name="ck_search_projection_tasks_attempts"),
        sa.PrimaryKeyConstraint("document_id", "version"),
    )
    op.create_index(
        "ix_search_projection_tasks_available",
        "search_projection_tasks",
        ["state", "available_at", "created_at"],
    )
    # Upgrades put every pre-existing canonical row under the same durable
    # projection workflow. The outbox stores identifiers and versions only.
    op.execute(
        sa.text(
            """
            INSERT INTO search_projection_tasks (
                document_id,
                version,
                state,
                attempts,
                available_at,
                lease_expires_at,
                claimed_by,
                claim_token,
                last_error_code,
                dead_lettered_at,
                created_at,
                updated_at
            )
            SELECT
                id,
                current_version,
                'pending',
                0,
                CURRENT_TIMESTAMP,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM documents
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_search_projection_tasks_available", table_name="search_projection_tasks")
    op.drop_table("search_projection_tasks")
