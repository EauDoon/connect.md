"""Bind organization membership invitations to a public profile handle.

Revision ID: 0018_organization_membership_profile_handle
Revises: 0017_agent_outreach_rate_controls
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_organization_membership_profile_handle"
down_revision: str | None = "0017_agent_outreach_rate_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization_memberships",
        sa.Column("member_profile_handle", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organization_memberships", "member_profile_handle")
