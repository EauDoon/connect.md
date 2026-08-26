"""Add local-only account erasure execution metadata.

Revision ID: 0015_account_lifecycle_stage_two
Revises: 0014_account_lifecycle_stage_one
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_account_lifecycle_stage_two"
down_revision: str | None = "0014_account_lifecycle_stage_one"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_lifecycles", sa.Column("provider_session_ciphertext", sa.Text(), nullable=True)
    )
    op.add_column(
        "account_erasure_items", sa.Column("hold_kind", sa.String(length=24), nullable=True)
    )
    op.add_column(
        "account_erasure_items", sa.Column("hold_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "account_erasure_items",
        sa.Column("hold_review_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("account_erasure_items", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_account_erasure_items_hold_kind",
            "hold_kind IS NULL OR hold_kind IN ('retention', 'policy')",
        )
        batch.create_foreign_key(
            "fk_account_erasure_items_hold_id",
            "retention_holds",
            ["hold_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.add_column(
        "account_backup_manifests",
        sa.Column("crypto_destroyed_proof_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "account_backup_manifests",
        sa.Column("crypto_destroyed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "account_backup_manifests",
        sa.Column("expired_proof_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "account_backup_manifests",
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("account_backup_manifests", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_account_backup_manifests_proof_pair",
            "(state = 'active' AND expired_proof_digest IS NULL AND expired_at IS NULL "
            "AND crypto_destroyed_proof_digest IS NULL AND crypto_destroyed_at IS NULL) OR "
            "(state = 'expired' AND expired_proof_digest IS NOT NULL AND expired_at IS NOT NULL "
            "AND crypto_destroyed_proof_digest IS NULL AND crypto_destroyed_at IS NULL) OR "
            "(state = 'crypto_destroyed' AND crypto_destroyed_proof_digest IS NOT NULL "
            "AND crypto_destroyed_at IS NOT NULL AND expired_proof_digest IS NULL AND expired_at IS NULL)",
        )
    op.create_table(
        "account_backup_authority",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("current_generation_id", sa.String(length=128), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Existing Stage 1 obligations predate immutable backup evidence. They intentionally
    # remain incomplete: the Stage 2 worker rejects null snapshots rather than guessing.
    op.add_column(
        "account_backup_obligations",
        sa.Column("generation_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "account_backup_obligations",
        sa.Column("generation_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "account_backup_obligations",
        sa.Column("db_manifest_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "account_backup_obligations",
        sa.Column("markdown_manifest_digest", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "identifier_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("namespace", sa.String(length=64), nullable=False),
        sa.Column("identifier_hmac", sa.String(length=64), nullable=False),
        sa.Column("deletion_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace", "identifier_hmac", name="uq_identifier_reservations_namespace_hmac"
        ),
    )
    op.create_index(
        "ix_identifier_reservations_deletion_id", "identifier_reservations", ["deletion_id"]
    )
    op.create_table(
        "account_erasure_file_proofs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deletion_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deletion_id"], ["account_lifecycles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deletion_id",
            "resource_type",
            "resource_id",
            name="uq_account_erasure_file_proofs_resource",
        ),
    )
    op.create_index(
        "ix_account_erasure_file_proofs_deletion_id", "account_erasure_file_proofs", ["deletion_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_erasure_file_proofs_deletion_id", table_name="account_erasure_file_proofs"
    )
    op.drop_table("account_erasure_file_proofs")
    op.drop_index("ix_identifier_reservations_deletion_id", table_name="identifier_reservations")
    op.drop_table("identifier_reservations")
    op.drop_table("account_backup_authority")
    with op.batch_alter_table("account_backup_manifests", recreate="always") as batch:
        batch.drop_constraint("ck_account_backup_manifests_proof_pair", type_="check")
    op.drop_column("account_backup_manifests", "expired_at")
    op.drop_column("account_backup_manifests", "expired_proof_digest")
    op.drop_column("account_backup_manifests", "crypto_destroyed_at")
    op.drop_column("account_backup_manifests", "crypto_destroyed_proof_digest")
    op.drop_column("account_backup_obligations", "markdown_manifest_digest")
    op.drop_column("account_backup_obligations", "db_manifest_digest")
    op.drop_column("account_backup_obligations", "generation_expires_at")
    op.drop_column("account_backup_obligations", "generation_created_at")
    with op.batch_alter_table("account_erasure_items", recreate="always") as batch:
        batch.drop_constraint("fk_account_erasure_items_hold_id", type_="foreignkey")
        batch.drop_constraint("ck_account_erasure_items_hold_kind", type_="check")
    op.drop_column("account_erasure_items", "hold_review_at")
    op.drop_column("account_erasure_items", "hold_id")
    op.drop_column("account_erasure_items", "hold_kind")
    op.drop_column("account_lifecycles", "provider_session_ciphertext")
