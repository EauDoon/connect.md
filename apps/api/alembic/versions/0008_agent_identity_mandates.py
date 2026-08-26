"""Add public agent identities and private contact mandates.

Revision ID: 0008_agent_identity_mandates
Revises: 0007_retention_executor
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_agent_identity_mandates"
down_revision: str | None = "0007_retention_executor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("handle", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("profile_document_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'withdrawn', 'withheld')",
            name="ck_agent_identities_status",
        ),
        sa.ForeignKeyConstraint(["profile_document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("handle", name="uq_agent_identities_handle"),
    )
    op.create_index(
        "ix_agent_identities_owner_created", "agent_identities", ["owner_id", "created_at"]
    )
    op.create_table(
        "agent_mandates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("identity_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("scope = 'internal_contact_request'", name="ck_agent_mandates_scope"),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired', 'suspended')",
            name="ck_agent_mandates_status",
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["agent_identities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_mandates_owner_created", "agent_mandates", ["owner_id", "created_at"])
    op.create_index(
        "ix_agent_mandates_identity_status", "agent_mandates", ["identity_id", "status"]
    )
    op.create_index(
        "uq_agent_mandates_active_identity_scope",
        "agent_mandates",
        ["identity_id", "scope"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    with op.batch_alter_table("agent_grants") as batch:
        batch.add_column(
            sa.Column(
                "mandate_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "agent_mandates.id", name="fk_agent_grants_mandate", ondelete="RESTRICT"
                ),
                nullable=True,
            )
        )
        batch.create_unique_constraint("uq_agent_grants_mandate", ["mandate_id"])
    with op.batch_alter_table("contact_requests") as batch:
        batch.add_column(
            sa.Column(
                "sender_mandate_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "agent_mandates.id",
                    name="fk_contact_requests_sender_mandate",
                    ondelete="RESTRICT",
                ),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "origin", sa.String(length=32), nullable=False, server_default="profile_contact"
            )
        )
        batch.add_column(sa.Column("sender_identity_handle", sa.String(length=100), nullable=True))
        batch.add_column(
            sa.Column("sender_identity_display_name", sa.String(length=100), nullable=True)
        )
        batch.add_column(sa.Column("target_identity_handle", sa.String(length=100), nullable=True))
        batch.add_column(
            sa.Column("target_identity_display_name", sa.String(length=100), nullable=True)
        )
        batch.create_check_constraint(
            "ck_contact_requests_origin", "origin IN ('profile_contact', 'agent_outreach')"
        )
    op.execute(
        sa.text("UPDATE contact_requests SET origin = 'profile_contact' WHERE origin IS NULL")
    )
    op.create_index(
        "ix_contact_requests_origin_created", "contact_requests", ["origin", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_contact_requests_origin_created", table_name="contact_requests")
    with op.batch_alter_table("contact_requests") as batch:
        batch.drop_constraint("ck_contact_requests_origin", type_="check")
        batch.drop_column("target_identity_display_name")
        batch.drop_column("target_identity_handle")
        batch.drop_column("sender_identity_display_name")
        batch.drop_column("sender_identity_handle")
        batch.drop_column("origin")
        batch.drop_column("sender_mandate_id")
    with op.batch_alter_table("agent_grants") as batch:
        batch.drop_constraint("uq_agent_grants_mandate", type_="unique")
        batch.drop_column("mandate_id")
    op.drop_index("ix_agent_mandates_identity_status", table_name="agent_mandates")
    op.drop_index("uq_agent_mandates_active_identity_scope", table_name="agent_mandates")
    op.drop_index("ix_agent_mandates_owner_created", table_name="agent_mandates")
    op.drop_table("agent_mandates")
    op.drop_index("ix_agent_identities_owner_created", table_name="agent_identities")
    op.drop_table("agent_identities")
