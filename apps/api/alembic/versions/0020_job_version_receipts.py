"""Add immutable job versions for exact idempotent receipts.

Revision ID: 0020_job_version_receipts
Revises: 0019_search_projection_outbox

The version table is canonical job history, not a generic idempotency body.
Snapshots are deleted with their owning job and contain no account subject IDs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_job_version_receipts"
down_revision: str | None = "0019_search_projection_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_versions",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("organization_slug", sa.String(length=80), nullable=False),
        sa.Column("organization_name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("work_mode", sa.String(length=20), nullable=True),
        sa.Column("employment_type", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_job_versions_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'closed')",
            name="ck_job_versions_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "version"),
    )
    # Only the current version can be recovered for pre-migration jobs. Legacy
    # idempotency rows remain unversioned and therefore fail closed on replay.
    op.execute(
        sa.text(
            """
            INSERT INTO job_versions (
                job_id,
                version,
                organization_id,
                organization_slug,
                organization_name,
                slug,
                title,
                description,
                location,
                work_mode,
                employment_type,
                status,
                published_at,
                created_at,
                updated_at,
                response_body,
                response_sha256
            )
            SELECT
                jobs.id,
                jobs.version,
                organizations.id,
                organizations.slug,
                organizations.name,
                jobs.slug,
                jobs.title,
                jobs.description,
                jobs.location,
                jobs.work_mode,
                jobs.employment_type,
                jobs.status,
                jobs.published_at,
                jobs.created_at,
                jobs.updated_at,
                '',
                ''
            FROM jobs
            JOIN organizations ON organizations.id = jobs.organization_id
            """
        )
    )


def downgrade() -> None:
    op.drop_table("job_versions")
