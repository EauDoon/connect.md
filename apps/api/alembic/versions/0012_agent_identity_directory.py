"""Index public Agent Identity directory pagination.

Revision ID: 0012_agent_identity_directory
Revises: 0011_post_moderation_legacy_backfill
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_agent_identity_directory"
down_revision: str | None = "0011_post_moderation_legacy_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_identities_status_created",
        "agent_identities",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_agent_identities_profile_status_created",
        "agent_identities",
        ["profile_document_id", "status", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_identities_profile_status_created", table_name="agent_identities")
    op.drop_index("ix_agent_identities_status_created", table_name="agent_identities")
