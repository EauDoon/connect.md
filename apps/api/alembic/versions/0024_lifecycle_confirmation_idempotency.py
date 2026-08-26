"""Add lifecycle confirmation idempotency markers without retaining raw keys.

Revision ID: 0024_lifecycle_confirmation_idempotency
Revises: 0023_contact_request_status_constraint
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_lifecycle_confirmation_idempotency"
down_revision: str | None = "0023_contact_request_status_constraint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONFIRMATION_INDEX = "ix_account_lifecycles_confirmation_idempotency_hmac"


def upgrade() -> None:
    # Batch recreation keeps the nullable transition valid on SQLite while
    # remaining deterministic on PostgreSQL.  Existing request markers are
    # preserved; only a successful confirmation clears its own marker.
    with op.batch_alter_table("account_lifecycles", recreate="always") as batch:
        batch.alter_column(
            "request_idempotency_hmac",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch.add_column(
            sa.Column("confirmation_idempotency_hmac", sa.String(length=64), nullable=True)
        )
        batch.create_index(_CONFIRMATION_INDEX, ["confirmation_idempotency_hmac"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    unsafe = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM account_lifecycles
            WHERE request_idempotency_hmac IS NULL
               OR confirmation_idempotency_hmac IS NOT NULL
            """
        )
    ).scalar_one()
    if int(unsafe) != 0:
        raise RuntimeError(
            "cannot downgrade lifecycle confirmation idempotency without destroying receipt state"
        )
    with op.batch_alter_table("account_lifecycles", recreate="always") as batch:
        batch.drop_index(_CONFIRMATION_INDEX)
        batch.drop_column("confirmation_idempotency_hmac")
        batch.alter_column(
            "request_idempotency_hmac",
            existing_type=sa.String(length=64),
            nullable=False,
        )
