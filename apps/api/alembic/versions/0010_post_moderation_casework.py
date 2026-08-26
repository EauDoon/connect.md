"""Add private post-moderation casework and independent appeals.

Revision ID: 0010_post_moderation_casework
Revises: 0009_professional_posts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_post_moderation_casework"
down_revision: str | None = "0009_professional_posts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "moderation_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("post_id", sa.String(length=36), nullable=False),
        sa.Column("subject_owner_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sensitive_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'dismissed', 'withheld', 'appealed', 'appeal_upheld', 'appeal_overturned')",
            name="ck_moderation_cases_status",
        ),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", name="uq_moderation_cases_post"),
    )
    op.create_index(
        "ix_moderation_cases_subject_owner_id", "moderation_cases", ["subject_owner_id"]
    )
    op.create_index(
        "ix_moderation_cases_subject_updated",
        "moderation_cases",
        ["subject_owner_id", "updated_at", "id"],
    )

    with op.batch_alter_table("post_reports") as batch:
        batch.add_column(sa.Column("case_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_post_reports_case_id_moderation_cases",
            "moderation_cases",
            ["case_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_post_reports_case_id", "post_reports", ["case_id"])

    op.create_table(
        "moderation_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("post_id", sa.String(length=36), nullable=False),
        sa.Column("moderator_id", sa.String(length=255), nullable=False),
        sa.Column(
            "moderator_role",
            sa.String(length=40),
            nullable=False,
            server_default="content_moderator",
        ),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.Column("subject_explanation", sa.String(length=500), nullable=False),
        sa.Column("internal_rationale", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('no_action', 'withhold')", name="ck_moderation_decisions_action"
        ),
        sa.CheckConstraint(
            "moderator_role = 'content_moderator'", name="ck_moderation_decisions_role"
        ),
        sa.ForeignKeyConstraint(["case_id"], ["moderation_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", name="uq_moderation_decisions_case"),
    )
    op.create_index(
        "ix_moderation_decisions_case_decided", "moderation_decisions", ["case_id", "decided_at"]
    )

    op.create_table(
        "moderation_appeals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("decision_id", sa.String(length=36), nullable=False),
        sa.Column("subject_owner_id", sa.String(length=255), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="submitted"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("appeal_reviewer_id", sa.String(length=255), nullable=True),
        sa.Column("appeal_reviewer_role", sa.String(length=40), nullable=True),
        sa.Column("subject_explanation", sa.String(length=500), nullable=True),
        sa.Column("internal_rationale", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('submitted', 'upheld', 'overturned')", name="ck_moderation_appeals_status"
        ),
        sa.CheckConstraint(
            "appeal_reviewer_role IS NULL OR appeal_reviewer_role = 'appeal_reviewer'",
            name="ck_moderation_appeals_reviewer_role",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["moderation_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decision_id"], ["moderation_decisions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", name="uq_moderation_appeals_decision"),
    )
    op.create_index(
        "ix_moderation_appeals_case_submitted", "moderation_appeals", ["case_id", "submitted_at"]
    )

    op.create_table(
        "moderation_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("post_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("actor_role", sa.String(length=40), nullable=False),
        sa.Column("safe_metadata", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('case_opened', 'report_linked', 'decision_no_action', "
            "'decision_withheld', 'appeal_submitted', 'appeal_upheld', "
            "'appeal_overturned', 'sensitive_purged')",
            name="ck_moderation_audit_events_type",
        ),
        sa.CheckConstraint(
            "actor_role IN ('system', 'subject', 'content_moderator', 'appeal_reviewer')",
            name="ck_moderation_audit_events_role",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["moderation_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_moderation_audit_events_case_occurred",
        "moderation_audit_events",
        ["case_id", "occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_moderation_audit_events_case_occurred", table_name="moderation_audit_events")
    op.drop_table("moderation_audit_events")
    op.drop_index("ix_moderation_appeals_case_submitted", table_name="moderation_appeals")
    op.drop_table("moderation_appeals")
    op.drop_index("ix_moderation_decisions_case_decided", table_name="moderation_decisions")
    op.drop_table("moderation_decisions")
    op.drop_index("ix_post_reports_case_id", table_name="post_reports")
    with op.batch_alter_table("post_reports") as batch:
        batch.drop_constraint("fk_post_reports_case_id_moderation_cases", type_="foreignkey")
        batch.drop_column("case_id")
    op.drop_index("ix_moderation_cases_subject_updated", table_name="moderation_cases")
    op.drop_index("ix_moderation_cases_subject_owner_id", table_name="moderation_cases")
    op.drop_table("moderation_cases")
