"""Add organization-owned jobs and private applications.

Revision ID: 0004_social_core
Revises: 0003_contact_abuse_boundaries
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_social_core"
down_revision: str | None = "0003_contact_abuse_boundaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("website_url", sa.String(length=2048), nullable=True),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="private"),
        sa.Column(
            "verification_status", sa.String(length=20), nullable=False, server_default="unverified"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'private')", name="ck_organizations_visibility"
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'verified')",
            name="ck_organizations_verification_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("ix_organizations_owner_created", "organizations", ["owner_id", "created_at"])
    op.create_index(
        "ix_organizations_public_updated", "organizations", ["visibility", "updated_at"]
    )

    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("member_owner_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="invited"),
        sa.Column("invited_by_owner_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_organization_memberships_role"),
        sa.CheckConstraint(
            "status IN ('invited', 'active')", name="ck_organization_memberships_status"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "member_owner_id", name="uq_organization_membership_member"
        ),
    )
    op.create_index(
        "ix_organization_memberships_member_org",
        "organization_memberships",
        ["member_owner_id", "organization_id"],
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("work_mode", sa.String(length=20), nullable=True),
        sa.Column("employment_type", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("status IN ('draft', 'published', 'closed')", name="ck_jobs_status"),
        sa.CheckConstraint(
            "work_mode IS NULL OR work_mode IN ('remote', 'hybrid', 'onsite')",
            name="ck_jobs_work_mode",
        ),
        sa.CheckConstraint(
            "employment_type IS NULL OR employment_type IN ('full_time', 'part_time', 'contract', 'internship', 'temporary')",
            name="ck_jobs_employment_type",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_jobs_organization_slug"),
    )
    op.create_index("ix_jobs_organization_created", "jobs", ["organization_id", "created_at"])
    op.create_index("ix_jobs_public_updated", "jobs", ["status", "updated_at"])

    op.create_table(
        "applications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("applicant_owner_id", sa.String(length=255), nullable=False),
        sa.Column("applicant_actor_id", sa.String(length=255), nullable=False),
        sa.Column("applicant_actor_method", sa.String(length=32), nullable=False),
        sa.Column("applicant_grant_id", sa.String(length=36), nullable=True),
        sa.Column("snapshot_document_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_document_kind", sa.String(length=16), nullable=False),
        sa.Column("snapshot_document_identifier", sa.String(length=100), nullable=False),
        sa.Column("snapshot_document_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="submitted"),
        sa.Column("confirmed_by_owner_id", sa.String(length=255), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_policy_version", sa.String(length=32), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_actor_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('submitted', 'under_review', 'accepted', 'rejected', 'withdrawn')",
            name="ck_applications_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "applicant_owner_id", name="uq_applications_job_applicant"),
    )
    op.create_index("ix_applications_job_created", "applications", ["job_id", "created_at"])
    op.create_index(
        "ix_applications_applicant_created", "applications", ["applicant_owner_id", "created_at"]
    )

    op.create_table(
        "application_rate_buckets",
        sa.Column("applicant_owner_id", sa.String(length=255), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("application_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("applicant_owner_id", "bucket_date"),
    )


def downgrade() -> None:
    op.drop_table("application_rate_buckets")
    op.drop_index("ix_applications_applicant_created", table_name="applications")
    op.drop_index("ix_applications_job_created", table_name="applications")
    op.drop_table("applications")
    op.drop_index("ix_jobs_public_updated", table_name="jobs")
    op.drop_index("ix_jobs_organization_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_organization_memberships_member_org", table_name="organization_memberships")
    op.drop_table("organization_memberships")
    op.drop_index("ix_organizations_public_updated", table_name="organizations")
    op.drop_index("ix_organizations_owner_created", table_name="organizations")
    op.drop_table("organizations")
