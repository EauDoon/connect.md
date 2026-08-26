"""Add protocol, grant, idempotency, change-feed, and contact state.

Revision ID: 0002_protocol_core
Revises: 0001_initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_protocol_core"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column(
            "actor_method",
            sa.String(length=32),
            nullable=False,
            server_default="clerk_jwt",
        ),
    )
    op.add_column("document_versions", sa.Column("grant_id", sa.String(length=36), nullable=True))

    op.create_table(
        "agent_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("prefix", sa.String(length=24), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("resource_type", sa.String(length=20), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_grants_prefix", "agent_grants", ["prefix"])
    op.create_index("ix_agent_grants_owner_created", "agent_grants", ["owner_id", "created_at"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("response_headers", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resource_type", sa.String(length=32), nullable=True),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_idempotency_owner_key"),
    )
    op.create_index("ix_idempotency_created", "idempotency_records", ["created_at"])

    op.create_table(
        "change_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("actor_method", sa.String(length=32), nullable=False),
        sa.Column("grant_id", sa.String(length=36), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("sequence"),
    )
    op.create_index("ix_change_events_owner_sequence", "change_events", ["owner_id", "sequence"])

    op.create_table(
        "contact_policies",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("allow_agent_requests", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("daily_request_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("owner_id"),
    )

    op.create_table(
        "contact_blocks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("blocker_owner_id", sa.String(length=255), nullable=False),
        sa.Column("blocked_owner_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blocker_owner_id", "blocked_owner_id", name="uq_contact_blocks_pair"),
    )
    op.create_index("ix_contact_blocks_blocker_owner_id", "contact_blocks", ["blocker_owner_id"])

    op.create_table(
        "contact_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sender_owner_id", sa.String(length=255), nullable=False),
        sa.Column("recipient_owner_id", sa.String(length=255), nullable=False),
        sa.Column("sender_actor_id", sa.String(length=255), nullable=False),
        sa.Column("sender_actor_method", sa.String(length=32), nullable=False),
        sa.Column("sender_grant_id", sa.String(length=36), nullable=True),
        sa.Column("target_document_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=160), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("decision_actor_id", sa.String(length=255), nullable=True),
        sa.Column("report_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_contact_requests_recipient_created",
        "contact_requests",
        ["recipient_owner_id", "created_at"],
    )
    op.create_index(
        "ix_contact_requests_sender_created",
        "contact_requests",
        ["sender_owner_id", "created_at"],
    )

    op.create_table(
        "agent_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("submitter_actor_id", sa.String(length=255), nullable=False),
        sa.Column("submitter_grant_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("document_kind", sa.String(length=16), nullable=False),
        sa.Column("document_identifier", sa.String(length=100), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("if_match", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("decision_actor_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_proposals_owner_created", "agent_proposals", ["owner_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_proposals_owner_created", table_name="agent_proposals")
    op.drop_table("agent_proposals")
    op.drop_index("ix_contact_requests_sender_created", table_name="contact_requests")
    op.drop_index("ix_contact_requests_recipient_created", table_name="contact_requests")
    op.drop_table("contact_requests")
    op.drop_index("ix_contact_blocks_blocker_owner_id", table_name="contact_blocks")
    op.drop_table("contact_blocks")
    op.drop_table("contact_policies")
    op.drop_index("ix_change_events_owner_sequence", table_name="change_events")
    op.drop_table("change_events")
    op.drop_index("ix_idempotency_created", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("ix_agent_grants_owner_created", table_name="agent_grants")
    op.drop_index("ix_agent_grants_prefix", table_name="agent_grants")
    op.drop_table("agent_grants")
    op.drop_column("document_versions", "grant_id")
    op.drop_column("document_versions", "actor_method")
