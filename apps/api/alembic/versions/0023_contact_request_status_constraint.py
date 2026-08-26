"""Constrain persisted contact-request statuses to the canonical set.

Revision ID: 0023_contact_request_status_constraint
Revises: 0022_public_taxonomy_projection
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_contact_request_status_constraint"
down_revision: str | None = "0022_public_taxonomy_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CHECK = "status IN ('pending', 'accepted', 'rejected', 'blocked', 'reported')"
_PREFLIGHT_FAILURE = "contact request status invariant preflight failed"


def _preflight_statuses() -> None:
    invalid = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT 1
            FROM contact_requests
            WHERE status IS NULL
               OR status NOT IN ('pending', 'accepted', 'rejected', 'blocked', 'reported')
            LIMIT 1
            """
            )
        )
        .first()
    )
    if invalid is not None:
        raise RuntimeError(_PREFLIGHT_FAILURE)


def upgrade() -> None:
    _preflight_statuses()
    with op.batch_alter_table("contact_requests") as batch_op:
        batch_op.create_check_constraint("ck_contact_requests_status", _STATUS_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("contact_requests") as batch_op:
        batch_op.drop_constraint("ck_contact_requests_status", type_="check")
