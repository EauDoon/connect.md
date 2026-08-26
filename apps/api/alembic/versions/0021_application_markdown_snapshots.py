"""Retain application-owned immutable Markdown snapshots.

Revision ID: 0021_application_markdown_snapshots
Revises: 0020_job_version_receipts

Existing rows intentionally remain NULL: their original source bytes were not
copied at submission, and reconstructing them from a mutable or erased document
would misrepresent the application record.  New submissions write the immutable
file before their ledger row is committed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_application_markdown_snapshots"
down_revision: str | None = "0020_job_version_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("snapshot_storage_path", sa.String(length=1024), nullable=True),
    )
    op.create_index(
        "ux_applications_snapshot_storage_path",
        "applications",
        ["snapshot_storage_path"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_applications_snapshot_storage_path", table_name="applications")
    op.drop_column("applications", "snapshot_storage_path")
