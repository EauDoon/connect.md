"""Scrub historical duplicate private idempotency response bodies.

Revision ID: 0013_scrub_sensitive_idempotency_bodies
Revises: 0012_agent_identity_directory

This is a one-time forward-only privacy remediation.  Resource IDs, status,
and headers remain so replay can re-authorize and reconstruct the safe receipt.
For canonical documents, the immutable version number is retained as
``document-id@version`` before the body is removed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_scrub_sensitive_idempotency_bodies"
down_revision: str | None = "0012_agent_identity_directory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SENSITIVE_RESOURCE_TYPES = (
    "profile",
    "resume",
    "contact_request",
    "proposal",
    "organization",
    "organization_membership",
    "job",
    "post_report",
    "moderation_appeal",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE idempotency_records
                SET resource_id = resource_id || '@' || (response_body::jsonb ->> 'version')
                WHERE resource_type IN ('profile', 'resume')
                  AND response_body <> ''
                  AND resource_id NOT LIKE '%@%'
                  AND jsonb_typeof(response_body::jsonb -> 'version') = 'number'
                """
            )
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            sa.text(
                """
                UPDATE idempotency_records
                SET resource_id = resource_id || '@' || json_extract(response_body, '$.version')
                WHERE resource_type IN ('profile', 'resume')
                  AND response_body <> ''
                  AND resource_id NOT LIKE '%@%'
                  AND json_valid(response_body)
                  AND json_type(response_body, '$.version') = 'integer'
                """
            )
        )
    op.execute(
        sa.text(
            """
            UPDATE idempotency_records
            SET response_body = ''
            WHERE resource_type IN (
                'profile', 'resume', 'contact_request', 'proposal', 'organization',
                'organization_membership', 'job', 'post_report', 'moderation_appeal'
            )
            """
        )
    )


def downgrade() -> None:
    # Deleted duplicate private bodies cannot be safely reconstructed.
    pass
