"""Add the current-public-v2 PostgreSQL taxonomy projection.

The state rows intentionally start ``backfill_required``.  A deployment must
run the approved taxonomy backfill before discovery or taxonomy-aware search
can become ready.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0022_public_taxonomy_projection"
down_revision: str | None = "0021_application_markdown_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TAXONOMIES = (
    "occupation",
    "industry",
    "location",
    "skill",
    "language",
    "seniority",
    "open_to",
    "organization",
    "representative",
    "work_mode",
)
_CONTRACT_DIGEST = "680c5e3cb5595032a8e46e97e38fa8c23e1006419067e255b14f0db708101892"


def upgrade() -> None:
    op.add_column("documents", sa.Column("schema_version", sa.Integer(), nullable=True))
    with op.batch_alter_table("documents") as batch_op:
        batch_op.create_check_constraint(
            "ck_documents_schema_version",
            "schema_version IS NULL OR schema_version IN (1, 2)",
        )

    op.create_table(
        "public_taxonomy_projection_state",
        sa.Column("taxonomy", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="backfill_required",
            nullable=False,
        ),
        sa.Column("contract_digest", sa.String(length=64), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "taxonomy IN ('occupation', 'industry', 'location', 'skill', 'language', "
            "'seniority', 'open_to', 'organization', 'representative', 'work_mode')",
            name="ck_public_taxonomy_state_taxonomy",
        ),
        sa.CheckConstraint(
            "status IN ('backfill_required', 'building', 'ready', 'failed')",
            name="ck_public_taxonomy_state_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_public_taxonomy_state_revision"),
        sa.PrimaryKeyConstraint("taxonomy"),
    )

    op.create_table(
        "public_taxonomy_document_snapshots",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="2", nullable=False),
        sa.Column("availability_status", sa.String(length=32), nullable=False),
        sa.Column("availability_from", sa.String(length=40), nullable=True),
        sa.Column("representation_status", sa.String(length=32), nullable=False),
        sa.Column("contact_disclosure", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("document_version >= 1", name="ck_public_taxonomy_snapshot_version"),
        sa.CheckConstraint("schema_version = 2", name="ck_public_taxonomy_snapshot_schema_version"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index(
        "ix_public_taxonomy_snapshots_version",
        "public_taxonomy_document_snapshots",
        ["document_version", "document_id"],
    )

    op.create_table(
        "public_taxonomy_terms",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("taxonomy", sa.String(length=32), nullable=False),
        sa.Column("scheme", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_id", sa.String(length=336), nullable=False),
        sa.Column("filter_value", sa.String(length=68), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=True),
        sa.Column("label_conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("vocabulary_version", sa.String(length=64), nullable=True),
        sa.Column("version_conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "taxonomy IN ('occupation', 'industry', 'location', 'skill', 'language', "
            "'seniority', 'open_to', 'organization', 'representative', 'work_mode')",
            name="ck_public_taxonomy_terms_taxonomy",
        ),
        sa.CheckConstraint(
            "length(canonical_id) <= 336", name="ck_public_taxonomy_terms_canonical_id"
        ),
        sa.CheckConstraint(
            "canonical_id = scheme || ':' || external_id",
            name="ck_public_taxonomy_terms_canonical_id_exact",
        ),
        sa.CheckConstraint(
            "length(filter_value) = 68", name="ck_public_taxonomy_terms_filter_value"
        ),
        sa.CheckConstraint(
            "substr(filter_value, 1, 4) = 'tx1_'",
            name="ck_public_taxonomy_terms_filter_value_prefix",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "taxonomy", "scheme", "external_id", name="uq_public_taxonomy_terms_identity"
        ),
        sa.UniqueConstraint("filter_value", name="uq_public_taxonomy_terms_filter_value"),
    )
    op.create_index(
        "ix_public_taxonomy_terms_listing",
        "public_taxonomy_terms",
        ["taxonomy", "label", "canonical_id"],
    )

    op.create_table(
        "public_taxonomy_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("term_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=32), nullable=False),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        sa.Column("label_assertion", sa.String(length=160), nullable=False),
        sa.Column("vocabulary_version", sa.String(length=64), nullable=True),
        sa.Column("language_proficiency", sa.String(length=32), nullable=True),
        sa.Column("organization_relationship", sa.String(length=32), nullable=True),
        sa.Column("location_country_code", sa.String(length=2), nullable=True),
        sa.Column("location_region", sa.String(length=100), nullable=True),
        sa.Column("location_city", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["public_taxonomy_document_snapshots.document_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["term_id"], ["public_taxonomy_terms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "term_id", "field_name", name="uq_public_taxonomy_memberships_source"
        ),
        sa.UniqueConstraint(
            "document_id",
            "field_name",
            "source_ordinal",
            name="uq_public_taxonomy_memberships_ordinal",
        ),
    )
    op.create_index(
        "ix_public_taxonomy_memberships_term",
        "public_taxonomy_memberships",
        ["term_id", "document_id"],
    )
    op.create_index(
        "ix_public_taxonomy_memberships_document",
        "public_taxonomy_memberships",
        ["document_id", "field_name", "source_ordinal"],
    )

    state_table = sa.table(
        "public_taxonomy_projection_state",
        sa.column("taxonomy", sa.String),
        sa.column("revision", sa.Integer),
        sa.column("status", sa.String),
        sa.column("contract_digest", sa.String),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        state_table,
        [
            {
                "taxonomy": taxonomy,
                "revision": 0,
                "status": "backfill_required",
                "contract_digest": _CONTRACT_DIGEST,
                "updated_at": datetime.now(UTC),
            }
            for taxonomy in _TAXONOMIES
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_public_taxonomy_memberships_document",
        table_name="public_taxonomy_memberships",
    )
    op.drop_index(
        "ix_public_taxonomy_memberships_term",
        table_name="public_taxonomy_memberships",
    )
    op.drop_table("public_taxonomy_memberships")
    op.drop_index("ix_public_taxonomy_terms_listing", table_name="public_taxonomy_terms")
    op.drop_table("public_taxonomy_terms")
    op.drop_index(
        "ix_public_taxonomy_snapshots_version",
        table_name="public_taxonomy_document_snapshots",
    )
    op.drop_table("public_taxonomy_document_snapshots")
    op.drop_table("public_taxonomy_projection_state")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("ck_documents_schema_version", type_="check")
    op.drop_column("documents", "schema_version")
