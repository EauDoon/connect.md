"""Add private connections, conversations, messages, and notifications.

Revision ID: 0005_private_social_graph
Revises: 0004_social_core
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_private_social_graph"
down_revision: str | None = "0004_social_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connection_blocks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("blocker_owner_id", sa.String(length=255), nullable=False),
        sa.Column("blocked_owner_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "blocker_owner_id <> blocked_owner_id", name="ck_connection_blocks_distinct"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "blocker_owner_id", "blocked_owner_id", name="uq_connection_blocks_pair"
        ),
    )
    op.create_index(
        "ix_connection_blocks_blocked", "connection_blocks", ["blocked_owner_id", "created_at"]
    )

    op.create_table(
        "connection_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("pair_owner_low", sa.String(length=255), nullable=False),
        sa.Column("pair_owner_high", sa.String(length=255), nullable=False),
        sa.Column("requester_owner_id", sa.String(length=255), nullable=False),
        sa.Column("recipient_owner_id", sa.String(length=255), nullable=False),
        sa.Column("requester_profile_handle", sa.String(length=100), nullable=False),
        sa.Column("recipient_profile_handle", sa.String(length=100), nullable=False),
        sa.Column("requested_messaging", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recipient_messaging_consent", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("requester_actor_id", sa.String(length=255), nullable=False),
        sa.Column("requester_actor_method", sa.String(length=32), nullable=False),
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
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "pair_owner_low < pair_owner_high", name="ck_connection_requests_pair_order"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'blocked')",
            name="ck_connection_requests_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_connection_requests_recipient_created",
        "connection_requests",
        ["recipient_owner_id", "created_at"],
    )
    op.create_index(
        "ix_connection_requests_requester_created",
        "connection_requests",
        ["requester_owner_id", "created_at"],
    )
    op.create_index(
        "uq_connection_requests_active_pair",
        "connection_requests",
        ["pair_owner_low", "pair_owner_high"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'accepted')"),
        sqlite_where=sa.text("status IN ('pending', 'accepted')"),
    )

    op.create_table(
        "connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_request_id", sa.String(length=36), nullable=False),
        sa.Column("pair_owner_low", sa.String(length=255), nullable=False),
        sa.Column("pair_owner_high", sa.String(length=255), nullable=False),
        sa.Column("requester_owner_id", sa.String(length=255), nullable=False),
        sa.Column("recipient_owner_id", sa.String(length=255), nullable=False),
        sa.Column("requester_profile_handle", sa.String(length=100), nullable=False),
        sa.Column("recipient_profile_handle", sa.String(length=100), nullable=False),
        sa.Column("requested_messaging", sa.Boolean(), nullable=False),
        sa.Column("recipient_messaging_consent", sa.Boolean(), nullable=False),
        sa.Column("messaging_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
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
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by_owner_id", sa.String(length=255), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("pair_owner_low < pair_owner_high", name="ck_connections_pair_order"),
        sa.CheckConstraint(
            "status IN ('active', 'removed', 'blocked')", name="ck_connections_status"
        ),
        sa.ForeignKeyConstraint(
            ["connection_request_id"], ["connection_requests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_request_id", name="uq_connections_request"),
    )
    op.create_index("ix_connections_low_created", "connections", ["pair_owner_low", "created_at"])
    op.create_index("ix_connections_high_created", "connections", ["pair_owner_high", "created_at"])
    op.create_index(
        "uq_connections_active_pair",
        "connections",
        ["pair_owner_low", "pair_owner_high"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("pair_owner_low", sa.String(length=255), nullable=False),
        sa.Column("pair_owner_high", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_by_owner_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("pair_owner_low < pair_owner_high", name="ck_conversations_pair_order"),
        sa.CheckConstraint(
            "status IN ('active', 'closed', 'blocked')", name="ck_conversations_status"
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", name="uq_conversations_connection"),
    )
    op.create_index(
        "ix_conversations_low_created", "conversations", ["pair_owner_low", "created_at"]
    )
    op.create_index(
        "ix_conversations_high_created", "conversations", ["pair_owner_high", "created_at"]
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("sender_owner_id", sa.String(length=255), nullable=False),
        sa.Column("sender_actor_id", sa.String(length=255), nullable=False),
        sa.Column("sender_actor_method", sa.String(length=32), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'active'", name="ck_messages_status"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )
    op.create_index("ix_messages_sender_created", "messages", ["sender_owner_id", "created_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recipient_owner_id", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("actor_owner_id", sa.String(length=255), nullable=True),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_recipient_created", "notifications", ["recipient_owner_id", "created_at"]
    )

    op.create_table(
        "connection_request_rate_buckets",
        sa.Column("requester_owner_id", sa.String(length=255), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("requester_owner_id", "bucket_date"),
    )
    op.create_table(
        "message_rate_buckets",
        sa.Column("sender_owner_id", sa.String(length=255), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("sender_owner_id", "bucket_date"),
    )


def downgrade() -> None:
    op.drop_table("message_rate_buckets")
    op.drop_table("connection_request_rate_buckets")
    op.drop_index("ix_notifications_recipient_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_messages_sender_created", table_name="messages")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_high_created", table_name="conversations")
    op.drop_index("ix_conversations_low_created", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_connections_high_created", table_name="connections")
    op.drop_index("ix_connections_low_created", table_name="connections")
    op.drop_index("uq_connections_active_pair", table_name="connections")
    op.drop_table("connections")
    op.drop_index("uq_connection_requests_active_pair", table_name="connection_requests")
    op.drop_index("ix_connection_requests_requester_created", table_name="connection_requests")
    op.drop_index("ix_connection_requests_recipient_created", table_name="connection_requests")
    op.drop_table("connection_requests")
    op.drop_index("ix_connection_blocks_blocked", table_name="connection_blocks")
    op.drop_table("connection_blocks")
