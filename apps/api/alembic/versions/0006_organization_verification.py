"""Add append-only recruiting-control verification evidence and decisions.

Revision ID: 0006_organization_verification
Revises: 0005_private_social_graph
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_organization_verification"
down_revision: str | None = "0005_private_social_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "verification_material_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_table(
        "organization_verifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column(
            "purpose", sa.String(length=40), nullable=False, server_default="recruiting_control"
        ),
        sa.Column("submitted_by_owner_id", sa.String(length=255), nullable=False),
        sa.Column("material_claim_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose = 'recruiting_control'", name="ck_organization_verifications_purpose"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_organization_verifications_organization_created",
        "organization_verifications",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "organization_verification_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("verification_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_kind", sa.String(length=40), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("artifact_content_type", sa.String(length=80), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "artifact_size_bytes > 0 AND artifact_size_bytes <= 262144",
            name="ck_organization_verification_evidence_size",
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('corporate_registration', 'domain_control', 'employment_authority', 'other')",
            name="ck_organization_verification_evidence_kind",
        ),
        sa.CheckConstraint(
            "artifact_content_type IN ('application/pdf', 'image/jpeg', 'image/png', 'text/plain')",
            name="ck_organization_verification_evidence_content_type",
        ),
        sa.ForeignKeyConstraint(
            ["verification_id"], ["organization_verifications.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "verification_id", name="uq_organization_verification_evidence_verification"
        ),
    )
    op.create_index(
        "ix_organization_verification_evidence_verification",
        "organization_verification_evidence",
        ["verification_id", "created_at"],
    )

    op.create_table(
        "organization_verification_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("verification_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("to_state", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("actor_role", sa.String(length=40), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=True),
        sa.Column("material_claim_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose = 'recruiting_control'", name="ck_organization_verification_events_purpose"
        ),
        sa.CheckConstraint(
            "to_state IN ('submitted', 'under_review', 'active', 'rejected', 'expired', 'suspended', 'revoked')",
            name="ck_organization_verification_events_state",
        ),
        sa.CheckConstraint(
            "actor_role IN ('submitter', 'recruiting_verifier')",
            name="ck_organization_verification_events_actor_role",
        ),
        sa.ForeignKeyConstraint(
            ["verification_id"], ["organization_verifications.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_organization_verification_events_organization_created",
        "organization_verification_events",
        ["organization_id", "occurred_at"],
    )
    op.create_index(
        "ix_organization_verification_events_verification_created",
        "organization_verification_events",
        ["verification_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organization_verification_events_verification_created",
        table_name="organization_verification_events",
    )
    op.drop_index(
        "ix_organization_verification_events_organization_created",
        table_name="organization_verification_events",
    )
    op.drop_table("organization_verification_events")
    op.drop_index(
        "ix_organization_verification_evidence_verification",
        table_name="organization_verification_evidence",
    )
    op.drop_table("organization_verification_evidence")
    op.drop_index(
        "ix_organization_verifications_organization_created",
        table_name="organization_verifications",
    )
    op.drop_table("organization_verifications")
    op.drop_column("organizations", "verification_material_version")
