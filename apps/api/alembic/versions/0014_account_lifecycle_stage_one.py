"""Add disabled-by-default account lifecycle Stage 1 state.

Revision ID: 0014_account_lifecycle_stage_one
Revises: 0013_scrub_sensitive_idempotency_bodies
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_account_lifecycle_stage_one"
down_revision: str | None = "0013_scrub_sensitive_idempotency_bodies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_lifecycles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject_hmac", sa.String(length=64), nullable=False),
        sa.Column("request_idempotency_hmac", sa.String(length=64), nullable=False),
        sa.Column("provider_subject_ciphertext", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("provider_state", sa.String(length=16), nullable=False),
        sa.Column("backup_state", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_failure_code", sa.String(length=80), nullable=True),
        sa.CheckConstraint(
            "state IN ('confirmation_pending', 'concealed', 'erasure_planned', 'erasing', "
            "'held', 'failed', 'live_erasure_complete', 'backup_expiry_pending', 'fully_erased')",
            name="ck_account_lifecycles_state",
        ),
        sa.CheckConstraint(
            "provider_state IN ('pending', 'verified', 'failed', 'unsupported')",
            name="ck_account_lifecycles_provider_state",
        ),
        sa.CheckConstraint(
            "backup_state IN ('expiry_pending', 'verified')",
            name="ck_account_lifecycles_backup_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_idempotency_hmac"),
        sa.UniqueConstraint("subject_hmac"),
    )
    op.create_index("ix_account_lifecycles_subject_hmac", "account_lifecycles", ["subject_hmac"])
    op.create_index(
        "ix_account_lifecycles_subject_state", "account_lifecycles", ["subject_hmac", "state"]
    )
    op.create_table(
        "account_access_denies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject_hmac", sa.String(length=64), nullable=False),
        sa.Column("deletion_id", sa.String(length=36), nullable=False),
        sa.Column("denied_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deletion_id"], ["account_lifecycles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deletion_id"),
        sa.UniqueConstraint("subject_hmac"),
    )
    op.create_index(
        "ix_account_access_denies_subject_hmac", "account_access_denies", ["subject_hmac"]
    )
    op.create_table(
        "account_reverification_uses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("reverification_id_hmac", sa.String(length=64), nullable=False),
        sa.Column("subject_hmac", sa.String(length=64), nullable=False),
        sa.Column("sid_hmac", sa.String(length=64), nullable=False),
        sa.Column("jti_hmac", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("action_hmac", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('export', 'delete_request', 'delete_confirm')",
            name="ck_account_reverification_uses_purpose",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reverification_id_hmac"),
    )
    op.create_index(
        "ix_account_reverification_uses_subject_hmac",
        "account_reverification_uses",
        ["subject_hmac"],
    )
    op.create_table(
        "account_erasure_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deletion_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("disposition", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["deletion_id"], ["account_lifecycles.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "phase IN ('conceal', 'revoke', 'detach', 'delete_row', 'delete_file', 'unindex', "
            "'provider', 'postcheck', 'backup')",
            name="ck_account_erasure_items_phase",
        ),
        sa.CheckConstraint(
            "disposition IN ('delete', 'detach', 'hold')",
            name="ck_account_erasure_items_disposition",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'leased', 'completed', 'held', 'dead_letter')",
            name="ck_account_erasure_items_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deletion_id",
            "resource_type",
            "resource_id",
            "phase",
            name="uq_account_erasure_items_resource_phase",
        ),
    )
    op.create_index(
        "ix_account_erasure_items_deletion_id", "account_erasure_items", ["deletion_id"]
    )
    op.create_index(
        "ix_account_erasure_items_available",
        "account_erasure_items",
        ["state", "available_at", "id"],
    )
    op.create_table(
        "account_backup_manifests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("generation_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("db_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("markdown_manifest_digest", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "state IN ('active', 'expired', 'crypto_destroyed')",
            name="ck_account_backup_manifests_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_id"),
    )
    op.create_table(
        "account_backup_obligations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deletion_id", sa.String(length=36), nullable=False),
        sa.Column("generation_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("proof_digest", sa.String(length=64), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["deletion_id"], ["account_lifecycles.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "state IN ('pending', 'verified')", name="ck_account_backup_obligations_state"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deletion_id", "generation_id", name="uq_account_backup_obligations_pair"
        ),
    )
    op.create_index(
        "ix_account_backup_obligations_deletion_id", "account_backup_obligations", ["deletion_id"]
    )
    op.create_table(
        "account_lifecycle_tombstones",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deletion_id", sa.String(length=36), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("result_digest", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deletion_id"),
    )
    op.create_index(
        "ix_account_lifecycle_tombstones_deletion_id",
        "account_lifecycle_tombstones",
        ["deletion_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_lifecycle_tombstones_deletion_id", table_name="account_lifecycle_tombstones"
    )
    op.drop_table("account_lifecycle_tombstones")
    op.drop_index(
        "ix_account_backup_obligations_deletion_id", table_name="account_backup_obligations"
    )
    op.drop_table("account_backup_obligations")
    op.drop_table("account_backup_manifests")
    op.drop_index("ix_account_erasure_items_available", table_name="account_erasure_items")
    op.drop_index("ix_account_erasure_items_deletion_id", table_name="account_erasure_items")
    op.drop_table("account_erasure_items")
    op.drop_index(
        "ix_account_reverification_uses_subject_hmac", table_name="account_reverification_uses"
    )
    op.drop_table("account_reverification_uses")
    op.drop_index("ix_account_access_denies_subject_hmac", table_name="account_access_denies")
    op.drop_table("account_access_denies")
    op.drop_index("ix_account_lifecycles_subject_state", table_name="account_lifecycles")
    op.drop_index("ix_account_lifecycles_subject_hmac", table_name="account_lifecycles")
    op.drop_table("account_lifecycles")
