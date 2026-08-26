"""Add immutable professional posts, private follows, and moderation records.

Revision ID: 0009_professional_posts
Revises: 0008_agent_identity_mandates
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_professional_posts"
down_revision: str | None = "0008_agent_identity_mandates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("author_profile_document_id", sa.String(length=36), nullable=False),
        sa.Column("author_profile_handle", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="published"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withheld_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('published', 'withdrawn', 'withheld')", name="ck_posts_status"
        ),
        sa.CheckConstraint("current_version = 1", name="ck_posts_current_version"),
        sa.ForeignKeyConstraint(
            ["author_profile_document_id"], ["documents.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_path"),
    )
    op.create_index("ix_posts_owner_created", "posts", ["owner_id", "created_at"])
    op.create_index("ix_posts_owner_id", "posts", ["owner_id"])
    op.create_index("ix_posts_public_published", "posts", ["status", "published_at", "id"])

    op.create_table(
        "post_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("post_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version = 1", name="ck_post_versions_version"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "version", name="uq_post_versions_version"),
        sa.UniqueConstraint("storage_path"),
    )
    op.create_index("ix_post_versions_post_id", "post_versions", ["post_id"])

    op.create_table(
        "post_rate_buckets",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("post_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("owner_id", "bucket_date"),
    )

    op.create_table(
        "profile_follows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("follower_owner_id", sa.String(length=255), nullable=False),
        sa.Column("followed_owner_id", sa.String(length=255), nullable=False),
        sa.Column("followed_profile_handle", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "follower_owner_id", "followed_owner_id", name="uq_profile_follows_pair"
        ),
    )
    op.create_index(
        "ix_profile_follows_follower_created",
        "profile_follows",
        ["follower_owner_id", "created_at"],
    )

    op.create_table(
        "post_graph_pair_locks",
        sa.Column("pair_owner_low", sa.String(length=255), nullable=False),
        sa.Column("pair_owner_high", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "pair_owner_low < pair_owner_high", name="ck_post_graph_pair_locks_order"
        ),
        sa.PrimaryKeyConstraint("pair_owner_low", "pair_owner_high"),
    )

    op.create_table(
        "follow_rate_buckets",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("follow_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("owner_id", "bucket_date"),
    )

    op.create_table(
        "post_content_blocks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("blocker_owner_id", sa.String(length=255), nullable=False),
        sa.Column("blocked_owner_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "blocker_owner_id", "blocked_owner_id", name="uq_post_content_blocks_pair"
        ),
    )
    op.create_index(
        "ix_post_content_blocks_blocker_owner_id", "post_content_blocks", ["blocker_owner_id"]
    )

    op.create_table(
        "post_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("post_id", sa.String(length=36), nullable=False),
        sa.Column("reporter_owner_id", sa.String(length=255), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "reporter_owner_id", name="uq_post_reports_reporter_post"),
    )
    op.create_index("ix_post_reports_post_created", "post_reports", ["post_id", "created_at"])

    op.create_table(
        "post_report_rate_buckets",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("report_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("owner_id", "bucket_date"),
    )

    op.create_table(
        "post_moderation_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("post_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column(
            "actor_role", sa.String(length=40), nullable=False, server_default="content_moderator"
        ),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('withhold', 'restore')", name="ck_post_moderation_events_action"
        ),
        sa.CheckConstraint(
            "actor_role = 'content_moderator'", name="ck_post_moderation_events_role"
        ),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_post_moderation_events_post_created",
        "post_moderation_events",
        ["post_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_post_moderation_events_post_created", table_name="post_moderation_events")
    op.drop_table("post_moderation_events")
    op.drop_table("post_report_rate_buckets")
    op.drop_index("ix_post_reports_post_created", table_name="post_reports")
    op.drop_table("post_reports")
    op.drop_index("ix_post_content_blocks_blocker_owner_id", table_name="post_content_blocks")
    op.drop_table("post_content_blocks")
    op.drop_table("follow_rate_buckets")
    op.drop_table("post_graph_pair_locks")
    op.drop_index("ix_profile_follows_follower_created", table_name="profile_follows")
    op.drop_table("profile_follows")
    op.drop_table("post_rate_buckets")
    op.drop_index("ix_post_versions_post_id", table_name="post_versions")
    op.drop_table("post_versions")
    op.drop_index("ix_posts_public_published", table_name="posts")
    op.drop_index("ix_posts_owner_id", table_name="posts")
    op.drop_index("ix_posts_owner_created", table_name="posts")
    op.drop_table("posts")
