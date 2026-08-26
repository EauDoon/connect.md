"""Add the canonical PostgreSQL exact public-search projection.

The projection is deliberately installed non-ready.  A verified backfill must
materialize every current public Profile/Resume before exact search is exposed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025_exact_public_search"
down_revision: str | None = "0024_lifecycle_confirmation_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTRACT_DIGEST = hashlib.sha256(b"connect.md:exact-public-search:v1").hexdigest()


def upgrade() -> None:
    op.create_table(
        "public_exact_search_projection_state",
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="backfill_required",
            nullable=False,
        ),
        sa.Column("contract_digest", sa.String(length=64), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope = 'documents'", name="ck_public_exact_search_state_scope"),
        sa.CheckConstraint(
            "status IN ('backfill_required', 'building', 'ready', 'failed')",
            name="ck_public_exact_search_state_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_public_exact_search_state_revision"),
        sa.PrimaryKeyConstraint("scope"),
    )
    now = datetime.now(UTC)
    state_table = sa.table(
        "public_exact_search_projection_state",
        sa.column("scope", sa.String),
        sa.column("revision", sa.Integer),
        sa.column("status", sa.String),
        sa.column("contract_digest", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        state_table,
        [
            {
                "scope": "documents",
                "revision": 0,
                "status": "backfill_required",
                "contract_digest": _CONTRACT_DIGEST,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    op.create_table(
        "public_exact_search_document_snapshots",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("search_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("headline", sa.String(length=280), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("location", sa.String(length=280), nullable=False),
        sa.Column("availability_status", sa.String(length=32), nullable=True),
        sa.Column("availability_from", sa.String(length=40), nullable=True),
        sa.Column("representation_status", sa.String(length=32), nullable=True),
        sa.Column("contact_disclosure", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalized_search_text", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            sa.Text().with_variant(postgresql.TSVECTOR(), "postgresql"),
            nullable=False,
        ),
        sa.CheckConstraint("document_version >= 1", name="ck_public_exact_search_snapshot_version"),
        sa.CheckConstraint(
            "schema_version IN (1, 2)", name="ck_public_exact_search_snapshot_schema_version"
        ),
        sa.CheckConstraint(
            "kind IN ('profile', 'resume')", name="ck_public_exact_search_snapshot_kind"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_index(
            "ix_public_exact_search_snapshots_search_vector",
            "public_exact_search_document_snapshots",
            ["search_vector"],
            postgresql_using="gin",
        )
    else:
        op.create_index(
            "ix_public_exact_search_snapshots_search_vector",
            "public_exact_search_document_snapshots",
            ["search_vector"],
        )
    op.create_index(
        "ix_public_exact_search_snapshots_kind_updated_id",
        "public_exact_search_document_snapshots",
        ["kind", "updated_at", "document_id"],
    )
    op.create_index(
        "ix_public_exact_search_snapshots_updated_id",
        "public_exact_search_document_snapshots",
        ["updated_at", "document_id"],
    )

    op.create_table(
        "public_exact_search_compact_values",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=280), nullable=False),
        sa.Column("source_ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "field_name IN ('skill', 'location')",
            name="ck_public_exact_search_compact_value_field",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["public_exact_search_document_snapshots.document_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "field_name", "value", name="uq_public_exact_search_compact_value"
        ),
    )
    op.create_index(
        "ix_public_exact_search_compact_values_lookup",
        "public_exact_search_compact_values",
        ["field_name", "value", "document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_public_exact_search_compact_values_lookup",
        table_name="public_exact_search_compact_values",
    )
    op.drop_table("public_exact_search_compact_values")
    op.drop_index(
        "ix_public_exact_search_snapshots_updated_id",
        table_name="public_exact_search_document_snapshots",
    )
    op.drop_index(
        "ix_public_exact_search_snapshots_kind_updated_id",
        table_name="public_exact_search_document_snapshots",
    )
    op.drop_index(
        "ix_public_exact_search_snapshots_search_vector",
        table_name="public_exact_search_document_snapshots",
    )
    op.drop_table("public_exact_search_document_snapshots")
    op.drop_table("public_exact_search_projection_state")
