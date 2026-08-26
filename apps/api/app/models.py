from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid4())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("kind", "public_identifier", name="uq_documents_kind_public_identifier"),
        Index("ix_documents_owner_kind", "owner_id", "kind"),
        CheckConstraint(
            "schema_version IS NULL OR schema_version IN (1, 2)",
            name="ck_documents_schema_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    public_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_versions_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default="clerk_jwt", server_default="clerk_jwt"
    )
    grant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    document: Mapped[Document] = relationship(back_populates="versions")


class PublicTaxonomyProjectionState(Base):
    """Per-taxonomy readiness and monotonic revision for the public projection."""

    __tablename__ = "public_taxonomy_projection_state"
    __table_args__ = (
        CheckConstraint(
            "taxonomy IN ('occupation', 'industry', 'location', 'skill', 'language', "
            "'seniority', 'open_to', 'organization', 'representative', 'work_mode')",
            name="ck_public_taxonomy_state_taxonomy",
        ),
        CheckConstraint(
            "status IN ('backfill_required', 'building', 'ready', 'failed')",
            name="ck_public_taxonomy_state_status",
        ),
        CheckConstraint("revision >= 0", name="ck_public_taxonomy_state_revision"),
    )

    taxonomy: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="backfill_required", server_default="backfill_required"
    )
    contract_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicTaxonomyDocumentSnapshot(Base):
    """One current public v2 document represented in the taxonomy projection."""

    __tablename__ = "public_taxonomy_document_snapshots"
    __table_args__ = (
        CheckConstraint("document_version >= 1", name="ck_public_taxonomy_snapshot_version"),
        CheckConstraint("schema_version = 2", name="ck_public_taxonomy_snapshot_schema_version"),
        Index("ix_public_taxonomy_snapshots_version", "document_version", "document_id"),
    )

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    availability_status: Mapped[str] = mapped_column(String(32), nullable=False)
    availability_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    representation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_disclosure: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicTaxonomyTerm(Base):
    """A currently observed, publicly callable typed taxonomy identity."""

    __tablename__ = "public_taxonomy_terms"
    __table_args__ = (
        UniqueConstraint(
            "taxonomy", "scheme", "external_id", name="uq_public_taxonomy_terms_identity"
        ),
        CheckConstraint(
            "taxonomy IN ('occupation', 'industry', 'location', 'skill', 'language', "
            "'seniority', 'open_to', 'organization', 'representative', 'work_mode')",
            name="ck_public_taxonomy_terms_taxonomy",
        ),
        CheckConstraint(
            "length(canonical_id) <= 336", name="ck_public_taxonomy_terms_canonical_id"
        ),
        CheckConstraint(
            "canonical_id = scheme || ':' || external_id",
            name="ck_public_taxonomy_terms_canonical_id_exact",
        ),
        CheckConstraint("length(filter_value) = 68", name="ck_public_taxonomy_terms_filter_value"),
        CheckConstraint(
            "substr(filter_value, 1, 4) = 'tx1_'",
            name="ck_public_taxonomy_terms_filter_value_prefix",
        ),
        Index("ix_public_taxonomy_terms_listing", "taxonomy", "label", "canonical_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    taxonomy: Mapped[str] = mapped_column(String(32), nullable=False)
    scheme: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_id: Mapped[str] = mapped_column(String(336), nullable=False)
    filter_value: Mapped[str] = mapped_column(String(68), nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    label_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vocabulary_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PublicTaxonomyMembership(Base):
    """Document-local assertions used for authoritative search hydration."""

    __tablename__ = "public_taxonomy_memberships"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "term_id", "field_name", name="uq_public_taxonomy_memberships_source"
        ),
        UniqueConstraint(
            "document_id",
            "field_name",
            "source_ordinal",
            name="uq_public_taxonomy_memberships_ordinal",
        ),
        Index("ix_public_taxonomy_memberships_term", "term_id", "document_id"),
        Index(
            "ix_public_taxonomy_memberships_document",
            "document_id",
            "field_name",
            "source_ordinal",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("public_taxonomy_document_snapshots.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    term_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("public_taxonomy_terms.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    label_assertion: Mapped[str] = mapped_column(String(160), nullable=False)
    vocabulary_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language_proficiency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    organization_relationship: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location_country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    location_region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True)


_EXACT_SEARCH_VECTOR_TYPE = Text().with_variant(TSVECTOR(), "postgresql")


class PublicExactSearchProjectionState(Base):
    """Singleton readiness and revision state for canonical exact public search."""

    __tablename__ = "public_exact_search_projection_state"
    __table_args__ = (
        CheckConstraint("scope = 'documents'", name="ck_public_exact_search_state_scope"),
        CheckConstraint(
            "status IN ('backfill_required', 'building', 'ready', 'failed')",
            name="ck_public_exact_search_state_status",
        ),
        CheckConstraint("revision >= 0", name="ck_public_exact_search_state_revision"),
    )

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="backfill_required", server_default="backfill_required"
    )
    contract_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicExactSearchDocumentSnapshot(Base):
    """Public-only, current-version search data bound to canonical storage hashes."""

    __tablename__ = "public_exact_search_document_snapshots"
    __table_args__ = (
        CheckConstraint("document_version >= 1", name="ck_public_exact_search_snapshot_version"),
        CheckConstraint(
            "schema_version IN (1, 2)", name="ck_public_exact_search_snapshot_schema_version"
        ),
        CheckConstraint(
            "kind IN ('profile', 'resume')", name="ck_public_exact_search_snapshot_kind"
        ),
        Index(
            "ix_public_exact_search_snapshots_kind_updated_id",
            "kind",
            "updated_at",
            "document_id",
        ),
        Index("ix_public_exact_search_snapshots_updated_id", "updated_at", "document_id"),
    )

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    search_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    headline: Mapped[str] = mapped_column(String(280), nullable=False)
    title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    location: Mapped[str] = mapped_column(String(280), nullable=False)
    availability_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    availability_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    representation_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contact_disclosure: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    normalized_search_text: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(_EXACT_SEARCH_VECTOR_TYPE, nullable=False)


class PublicExactSearchCompactValue(Base):
    """Bounded legacy compact search values for exact skills/location filters."""

    __tablename__ = "public_exact_search_compact_values"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "field_name", "value", name="uq_public_exact_search_compact_value"
        ),
        CheckConstraint(
            "field_name IN ('skill', 'location')",
            name="ck_public_exact_search_compact_value_field",
        ),
        Index(
            "ix_public_exact_search_compact_values_lookup",
            "field_name",
            "value",
            "document_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("public_exact_search_document_snapshots.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    field_name: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[str] = mapped_column(String(280), nullable=False)
    source_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SearchProjectionTask(Base):
    """Content-free, version-keyed work needed to reconcile the search projection."""

    __tablename__ = "search_projection_tasks"
    __table_args__ = (
        Index(
            "ix_search_projection_tasks_available",
            "state",
            "available_at",
            "created_at",
        ),
        CheckConstraint(
            "state IN ('pending', 'leased', 'dead_letter')",
            name="ck_search_projection_tasks_state",
        ),
        CheckConstraint("version >= 1", name="ck_search_projection_tasks_version"),
        CheckConstraint("attempts >= 0", name="ck_search_projection_tasks_attempts"),
    )

    document_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Post(Base):
    """One immutable, public professional post; deliberately not a Document kind."""

    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_owner_created", "owner_id", "created_at"),
        Index("ix_posts_public_published", "status", "published_at", "id"),
        CheckConstraint("status IN ('published', 'withdrawn', 'withheld')", name="ck_posts_status"),
        CheckConstraint("current_version = 1", name="ck_posts_current_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    author_profile_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    author_profile_handle: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="published")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withheld_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    versions: Mapped[list[PostVersion]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="PostVersion.version"
    )


class PostVersion(Base):
    __tablename__ = "post_versions"
    __table_args__ = (
        UniqueConstraint("post_id", "version", name="uq_post_versions_version"),
        CheckConstraint("version = 1", name="ck_post_versions_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    post: Mapped[Post] = relationship(back_populates="versions")


class PostRateBucket(Base):
    __tablename__ = "post_rate_buckets"

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    post_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProfileFollow(Base):
    __tablename__ = "profile_follows"
    __table_args__ = (
        UniqueConstraint("follower_owner_id", "followed_owner_id", name="uq_profile_follows_pair"),
        Index("ix_profile_follows_follower_created", "follower_owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    follower_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    followed_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    followed_profile_handle: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PostGraphPairLock(Base):
    """Persistent normalized pair lock shared by follow and content-block writes."""

    __tablename__ = "post_graph_pair_locks"
    __table_args__ = (
        CheckConstraint("pair_owner_low < pair_owner_high", name="ck_post_graph_pair_locks_order"),
    )

    pair_owner_low: Mapped[str] = mapped_column(String(255), primary_key=True)
    pair_owner_high: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FollowRateBucket(Base):
    __tablename__ = "follow_rate_buckets"

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    follow_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PostContentBlock(Base):
    """A content/feed block, kept separate from ConnectionBlock and ContactBlock."""

    __tablename__ = "post_content_blocks"
    __table_args__ = (
        UniqueConstraint(
            "blocker_owner_id", "blocked_owner_id", name="uq_post_content_blocks_pair"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    blocker_owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    blocked_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PostReport(Base):
    __tablename__ = "post_reports"
    __table_args__ = (
        UniqueConstraint("post_id", "reporter_owner_id", name="uq_post_reports_reporter_post"),
        Index("ix_post_reports_post_created", "post_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("posts.id", ondelete="RESTRICT"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("moderation_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    reporter_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PostReportRateBucket(Base):
    __tablename__ = "post_report_rate_buckets"

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ModerationCase(Base):
    """Private, append-only case authority for one reported professional post."""

    __tablename__ = "moderation_cases"
    __table_args__ = (
        Index("ix_moderation_cases_subject_updated", "subject_owner_id", "updated_at", "id"),
        Index(
            "uq_moderation_cases_open_post",
            "post_id",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
        CheckConstraint(
            "status IN ('open', 'dismissed', 'withheld', 'appealed', 'appeal_upheld', "
            "'appeal_overturned', 'legacy_withheld', 'legacy_withdrawn')",
            name="ck_moderation_cases_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("posts.id", ondelete="RESTRICT"), nullable=False
    )
    subject_owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sensitive_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ModerationDecision(Base):
    """An initial moderator decision. Rows are never changed or removed."""

    __tablename__ = "moderation_decisions"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_moderation_decisions_case"),
        Index("ix_moderation_decisions_case_decided", "case_id", "decided_at"),
        CheckConstraint(
            "action IN ('no_action', 'withhold')", name="ck_moderation_decisions_action"
        ),
        CheckConstraint(
            "moderator_role = 'content_moderator'", name="ck_moderation_decisions_role"
        ),
        CheckConstraint(
            "evidence_snapshot_sha256 IS NULL OR length(evidence_snapshot_sha256) = 64",
            name="ck_moderation_decisions_evidence_snapshot_sha256",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("moderation_cases.id", ondelete="RESTRICT"), nullable=False
    )
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("posts.id", ondelete="RESTRICT"), nullable=False
    )
    moderator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    moderator_role: Mapped[str] = mapped_column(
        String(40), nullable=False, default="content_moderator"
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_explanation: Mapped[str] = mapped_column(String(500), nullable=False)
    internal_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModerationAppeal(Base):
    """A single private subject appeal against one adverse decision."""

    __tablename__ = "moderation_appeals"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_moderation_appeals_decision"),
        Index("ix_moderation_appeals_case_submitted", "case_id", "submitted_at"),
        CheckConstraint(
            "status IN ('submitted', 'upheld', 'overturned')", name="ck_moderation_appeals_status"
        ),
        CheckConstraint(
            "appeal_reviewer_role IS NULL OR appeal_reviewer_role = 'appeal_reviewer'",
            name="ck_moderation_appeals_reviewer_role",
        ),
        CheckConstraint(
            "review_snapshot_sha256 IS NULL OR length(review_snapshot_sha256) = 64",
            name="ck_moderation_appeals_review_snapshot_sha256",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("moderation_cases.id", ondelete="RESTRICT"), nullable=False
    )
    decision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("moderation_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    subject_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    appeal_reviewer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    appeal_reviewer_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subject_explanation: Mapped[str | None] = mapped_column(String(500), nullable=True)
    internal_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ModerationAuditEvent(Base):
    """Safe, append-only transition metadata; never place sensitive narratives here."""

    __tablename__ = "moderation_audit_events"
    __table_args__ = (
        Index("ix_moderation_audit_events_case_occurred", "case_id", "occurred_at", "id"),
        CheckConstraint(
            "event_type IN ('case_opened', 'report_linked', 'decision_no_action', "
            "'decision_withheld', 'appeal_submitted', 'appeal_upheld', "
            "'appeal_overturned', 'sensitive_purged')",
            name="ck_moderation_audit_events_type",
        ),
        CheckConstraint(
            "actor_role IN ('system', 'subject', 'content_moderator', 'appeal_reviewer')",
            name="ck_moderation_audit_events_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("moderation_cases.id", ondelete="RESTRICT"), nullable=False
    )
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("posts.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(40), nullable=False)
    safe_metadata: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PostModerationEvent(Base):
    """Pre-case legacy ledger; ModerationAuditEvent is authoritative from 0010 onward."""

    __tablename__ = "post_moderation_events"
    __table_args__ = (
        Index("ix_post_moderation_events_post_created", "post_id", "occurred_at"),
        CheckConstraint(
            "action IN ('withhold', 'restore')", name="ck_post_moderation_events_action"
        ),
        CheckConstraint("actor_role = 'content_moderator'", name="ck_post_moderation_events_role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("posts.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(40), nullable=False, default="content_moderator")
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_prefix", "prefix"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="documents:write")
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentIdentity(Base):
    __tablename__ = "agent_identities"
    __table_args__ = (
        UniqueConstraint("handle", name="uq_agent_identities_handle"),
        Index("ix_agent_identities_owner_created", "owner_id", "created_at"),
        Index("ix_agent_identities_status_created", "status", "created_at", "id"),
        Index(
            "ix_agent_identities_profile_status_created",
            "profile_document_id",
            "status",
            "created_at",
            "id",
        ),
        CheckConstraint(
            "status IN ('active', 'withdrawn', 'withheld')",
            name="ck_agent_identities_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    handle: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    profile_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentGrant(Base):
    __tablename__ = "agent_grants"
    __table_args__ = (
        Index("ix_agent_grants_prefix", "prefix"),
        Index("ix_agent_grants_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mandate_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_mandates.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMandate(Base):
    __tablename__ = "agent_mandates"
    __table_args__ = (
        Index("ix_agent_mandates_owner_created", "owner_id", "created_at"),
        Index("ix_agent_mandates_identity_status", "identity_id", "status"),
        Index(
            "uq_agent_mandates_active_identity_scope",
            "identity_id",
            "scope",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint("scope = 'internal_contact_request'", name="ck_agent_mandates_scope"),
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired', 'suspended')",
            name="ck_agent_mandates_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    identity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_identities.id", ondelete="RESTRICT"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_idempotency_owner_key"),
        Index("ix_idempotency_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    response_headers: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccountLifecycle(Base):
    __tablename__ = "account_lifecycles"
    __table_args__ = (
        CheckConstraint(
            "state IN ('confirmation_pending', 'concealed', 'erasure_planned', 'erasing', "
            "'held', 'failed', 'live_erasure_complete', 'backup_expiry_pending', 'fully_erased')",
            name="ck_account_lifecycles_state",
        ),
        CheckConstraint(
            "provider_state IN ('pending', 'verified', 'failed', 'unsupported')",
            name="ck_account_lifecycles_provider_state",
        ),
        CheckConstraint(
            "backup_state IN ('expiry_pending', 'verified')",
            name="ck_account_lifecycles_backup_state",
        ),
        Index("ix_account_lifecycles_subject_state", "subject_hmac", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject_hmac: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    request_idempotency_hmac: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    confirmation_idempotency_hmac: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    receipt_hmac: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    receipt_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_recovery_idempotency_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_subject_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_session_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="confirmation_pending")
    provider_state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    backup_state: Mapped[str] = mapped_column(String(16), nullable=False, default="expiry_pending")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safe_failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class AccountAccessDeny(Base):
    __tablename__ = "account_access_denies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject_hmac: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    deletion_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("account_lifecycles.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    denied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccountLifecycleReceiptRateLimit(Base):
    __tablename__ = "account_lifecycle_receipt_rate_limits"
    __table_args__ = (
        UniqueConstraint(
            "receipt_hmac", "ip_hmac", "window_started_at", name="uq_lifecycle_receipt_rate"
        ),
        Index("ix_lifecycle_receipt_rate_window", "window_started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deletion_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("account_lifecycles.id", ondelete="CASCADE"), nullable=False
    )
    receipt_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccountReverificationUse(Base):
    __tablename__ = "account_reverification_uses"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('export', 'delete_request', 'delete_confirm', 'delete_receipt_recover')",
            name="ck_account_reverification_uses_purpose",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    reverification_id_hmac: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    subject_hmac: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sid_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    jti_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    action_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccountErasureItem(Base):
    __tablename__ = "account_erasure_items"
    __table_args__ = (
        UniqueConstraint(
            "deletion_id",
            "resource_type",
            "resource_id",
            "phase",
            name="uq_account_erasure_items_resource_phase",
        ),
        CheckConstraint(
            "phase IN ('conceal', 'revoke', 'detach', 'delete_row', 'delete_file', 'unindex', "
            "'provider', 'postcheck', 'backup')",
            name="ck_account_erasure_items_phase",
        ),
        CheckConstraint(
            "disposition IN ('delete', 'detach', 'hold')",
            name="ck_account_erasure_items_disposition",
        ),
        CheckConstraint(
            "state IN ('queued', 'leased', 'completed', 'held', 'dead_letter')",
            name="ck_account_erasure_items_state",
        ),
        CheckConstraint(
            "hold_kind IS NULL OR hold_kind IN ('retention', 'policy')",
            name="ck_account_erasure_items_hold_kind",
        ),
        Index("ix_account_erasure_items_available", "state", "available_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deletion_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("account_lifecycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hold_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    hold_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("retention_holds.id", ondelete="RESTRICT"), nullable=True
    )
    hold_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccountBackupManifest(Base):
    __tablename__ = "account_backup_manifests"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'expired', 'crypto_destroyed')",
            name="ck_account_backup_manifests_state",
        ),
        CheckConstraint(
            "(state = 'active' AND expired_proof_digest IS NULL AND expired_at IS NULL "
            "AND crypto_destroyed_proof_digest IS NULL AND crypto_destroyed_at IS NULL) OR "
            "(state = 'expired' AND expired_proof_digest IS NOT NULL AND expired_at IS NOT NULL "
            "AND crypto_destroyed_proof_digest IS NULL AND crypto_destroyed_at IS NULL) OR "
            "(state = 'crypto_destroyed' AND crypto_destroyed_proof_digest IS NOT NULL "
            "AND crypto_destroyed_at IS NOT NULL AND expired_proof_digest IS NULL AND expired_at IS NULL)",
            name="ck_account_backup_manifests_proof_pair",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    db_manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    markdown_manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expired_proof_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    crypto_destroyed_proof_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    crypto_destroyed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


ACCOUNT_BACKUP_AUTHORITY_ID = "account-backup-generation-authority"


class AccountBackupAuthority(Base):
    """Singleton serialization point for registered backup generations."""

    __tablename__ = "account_backup_authority"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    current_generation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccountBackupObligation(Base):
    __tablename__ = "account_backup_obligations"
    __table_args__ = (
        UniqueConstraint("deletion_id", "generation_id", name="uq_account_backup_obligations_pair"),
        CheckConstraint(
            "state IN ('pending', 'verified')", name="ck_account_backup_obligations_state"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deletion_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("account_lifecycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    generation_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    generation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    db_manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    markdown_manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    proof_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccountLifecycleTombstone(Base):
    __tablename__ = "account_lifecycle_tombstones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deletion_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccountErasureFileProof(Base):
    """Non-content proof that one exact canonical lifecycle file was absent."""

    __tablename__ = "account_erasure_file_proofs"
    __table_args__ = (
        UniqueConstraint(
            "deletion_id",
            "resource_type",
            "resource_id",
            name="uq_account_erasure_file_proofs_resource",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deletion_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("account_lifecycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentifierReservation(Base):
    """Opaque permanent reservation for names released by account erasure."""

    __tablename__ = "identifier_reservations"
    __table_args__ = (
        UniqueConstraint(
            "namespace", "identifier_hmac", name="uq_identifier_reservations_namespace_hmac"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    deletion_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChangeEvent(Base):
    __tablename__ = "change_events"
    __table_args__ = (Index("ix_change_events_owner_sequence", "owner_id", "sequence"),)

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_method: Mapped[str] = mapped_column(String(32), nullable=False)
    grant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContactPolicy(Base):
    __tablename__ = "contact_policies"

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    allow_agent_requests: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_request_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ContactBlock(Base):
    __tablename__ = "contact_blocks"
    __table_args__ = (
        UniqueConstraint("blocker_owner_id", "blocked_owner_id", name="uq_contact_blocks_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    blocker_owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    blocked_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContactRequest(Base):
    __tablename__ = "contact_requests"
    __table_args__ = (
        Index("ix_contact_requests_recipient_created", "recipient_owner_id", "created_at"),
        Index("ix_contact_requests_sender_created", "sender_owner_id", "created_at"),
        Index("ix_contact_requests_origin_created", "origin", "created_at"),
        CheckConstraint(
            "origin IN ('profile_contact', 'agent_outreach')", name="ck_contact_requests_origin"
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'blocked', 'reported')",
            name="ck_contact_requests_status",
        ),
        Index(
            "uq_contact_requests_pending_pair",
            "sender_owner_id",
            "recipient_owner_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sender_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_actor_method: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_grant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sender_mandate_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_mandates.id", ondelete="RESTRICT"), nullable=True
    )
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="profile_contact")
    sender_identity_handle: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sender_identity_display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_identity_handle: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_identity_display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    purpose: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decision_actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    report_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContactRateBucket(Base):
    __tablename__ = "contact_rate_buckets"

    sender_owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AgentOutreachRecipientRateBucket(Base):
    __tablename__ = "agent_outreach_recipient_rate_buckets"

    recipient_owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AgentOutreachDirectPeerRateBucket(Base):
    __tablename__ = "agent_outreach_direct_peer_rate_buckets"

    direct_peer_hmac: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_organizations_slug"),
        Index("ix_organizations_owner_created", "owner_id", "created_at"),
        Index("ix_organizations_public_updated", "visibility", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unverified"
    )
    verification_material_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "member_owner_id", name="uq_organization_membership_member"
        ),
        Index("ix_organization_memberships_member_org", "member_owner_id", "organization_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    member_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    member_profile_handle: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="invited")
    invited_by_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrganizationVerification(Base):
    __tablename__ = "organization_verifications"
    __table_args__ = (
        Index(
            "ix_organization_verifications_organization_created", "organization_id", "created_at"
        ),
        CheckConstraint(
            "purpose = 'recruiting_control'", name="ck_organization_verifications_purpose"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(40), nullable=False, default="recruiting_control")
    submitted_by_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    material_claim_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationVerificationEvidence(Base):
    __tablename__ = "organization_verification_evidence"
    __table_args__ = (
        UniqueConstraint(
            "verification_id", name="uq_organization_verification_evidence_verification"
        ),
        Index(
            "ix_organization_verification_evidence_verification", "verification_id", "created_at"
        ),
        CheckConstraint(
            "artifact_size_bytes > 0 AND artifact_size_bytes <= 262144",
            name="ck_organization_verification_evidence_size",
        ),
        CheckConstraint(
            "evidence_kind IN ('corporate_registration', 'domain_control', 'employment_authority', 'other')",
            name="ck_organization_verification_evidence_kind",
        ),
        CheckConstraint(
            "artifact_content_type IN ('application/pdf', 'image/jpeg', 'image/png', 'text/plain')",
            name="ck_organization_verification_evidence_content_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    verification_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization_verifications.id", ondelete="RESTRICT"), nullable=False
    )
    evidence_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationVerificationEvent(Base):
    __tablename__ = "organization_verification_events"
    __table_args__ = (
        Index(
            "ix_organization_verification_events_organization_created",
            "organization_id",
            "occurred_at",
        ),
        Index(
            "ix_organization_verification_events_verification_created",
            "verification_id",
            "occurred_at",
        ),
        CheckConstraint(
            "to_state IN ('submitted', 'under_review', 'active', 'rejected', 'expired', 'suspended', 'revoked')",
            name="ck_organization_verification_events_state",
        ),
        CheckConstraint(
            "purpose = 'recruiting_control'", name="ck_organization_verification_events_purpose"
        ),
        CheckConstraint(
            "actor_role IN ('submitter', 'recruiting_verifier')",
            name="ck_organization_verification_events_actor_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    verification_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization_verifications.id", ondelete="RESTRICT"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    to_state: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    material_claim_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_jobs_organization_slug"),
        Index("ix_jobs_organization_created", "organization_id", "created_at"),
        Index("ix_jobs_public_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class JobVersion(Base):
    """Immutable job response snapshot retained for the lifetime of its canonical job."""

    __tablename__ = "job_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_job_versions_version"),
        CheckConstraint(
            "status IN ('draft', 'published', 'closed')",
            name="ck_job_versions_status",
        ),
    )

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    organization_slug: Mapped[str] = mapped_column(String(80), nullable=False)
    organization_name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("job_id", "applicant_owner_id", name="uq_applications_job_applicant"),
        Index("ix_applications_job_created", "job_id", "created_at"),
        Index("ix_applications_applicant_created", "applicant_owner_id", "created_at"),
        CheckConstraint(
            "snapshot_size_bytes IS NULL OR "
            "(snapshot_size_bytes > 0 AND snapshot_size_bytes <= 131072)",
            name="ck_applications_snapshot_size",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    applicant_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    applicant_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    applicant_actor_method: Mapped[str] = mapped_column(String(32), nullable=False)
    applicant_grant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    snapshot_document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_document_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_document_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    snapshot_document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Legacy rows predate independently retained application snapshots.  New
    # submissions always populate this path; a missing legacy copy fails closed
    # rather than substituting a later or unrelated document version.
    snapshot_storage_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True, unique=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="submitted")
    confirmed_by_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision_actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApplicationRateBucket(Base):
    __tablename__ = "application_rate_buckets"

    applicant_owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    application_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ConnectionBlock(Base):
    __tablename__ = "connection_blocks"
    __table_args__ = (
        UniqueConstraint("blocker_owner_id", "blocked_owner_id", name="uq_connection_blocks_pair"),
        Index("ix_connection_blocks_blocked", "blocked_owner_id", "created_at"),
        CheckConstraint(
            "blocker_owner_id <> blocked_owner_id", name="ck_connection_blocks_distinct"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    blocker_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    blocked_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConnectionRequest(Base):
    __tablename__ = "connection_requests"
    __table_args__ = (
        Index("ix_connection_requests_recipient_created", "recipient_owner_id", "created_at"),
        Index("ix_connection_requests_requester_created", "requester_owner_id", "created_at"),
        Index(
            "uq_connection_requests_active_pair",
            "pair_owner_low",
            "pair_owner_high",
            unique=True,
            sqlite_where=text("status IN ('pending', 'accepted')"),
            postgresql_where=text("status IN ('pending', 'accepted')"),
        ),
        CheckConstraint(
            "pair_owner_low < pair_owner_high", name="ck_connection_requests_pair_order"
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'blocked')",
            name="ck_connection_requests_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pair_owner_low: Mapped[str] = mapped_column(String(255), nullable=False)
    pair_owner_high: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_profile_handle: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient_profile_handle: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_messaging: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recipient_messaging_consent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    requester_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_actor_method: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("connection_request_id", name="uq_connections_request"),
        Index(
            "uq_connections_active_pair",
            "pair_owner_low",
            "pair_owner_high",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_connections_low_created", "pair_owner_low", "created_at"),
        Index("ix_connections_high_created", "pair_owner_high", "created_at"),
        CheckConstraint("pair_owner_low < pair_owner_high", name="ck_connections_pair_order"),
        CheckConstraint("status IN ('active', 'removed', 'blocked')", name="ck_connections_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connection_requests.id", ondelete="RESTRICT"), nullable=False
    )
    pair_owner_low: Mapped[str] = mapped_column(String(255), nullable=False)
    pair_owner_high: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_profile_handle: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient_profile_handle: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_messaging: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recipient_messaging_consent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    messaging_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by_owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("connection_id", name="uq_conversations_connection"),
        Index("ix_conversations_low_created", "pair_owner_low", "created_at"),
        Index("ix_conversations_high_created", "pair_owner_high", "created_at"),
        CheckConstraint("pair_owner_low < pair_owner_high", name="ck_conversations_pair_order"),
        CheckConstraint(
            "status IN ('active', 'closed', 'blocked')", name="ck_conversations_status"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connections.id", ondelete="RESTRICT"), nullable=False
    )
    pair_owner_low: Mapped[str] = mapped_column(String(255), nullable=False)
    pair_owner_high: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_by_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_sender_created", "sender_owner_id", "created_at"),
        CheckConstraint("status = 'active'", name="ck_messages_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    sender_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_actor_method: Mapped[str] = mapped_column(String(32), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_recipient_created", "recipient_owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recipient_owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LifecycleTask(Base):
    __tablename__ = "lifecycle_tasks"
    __table_args__ = (
        UniqueConstraint("resource_type", "resource_id", name="uq_lifecycle_tasks_resource"),
        Index("ix_lifecycle_tasks_claim", "state", "available_at", "created_at"),
        CheckConstraint(
            "state IN ('queued', 'leased', 'completed', 'dead_letter')",
            name="ck_lifecycle_tasks_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_lifecycle_tasks_attempts"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RetentionHold(Base):
    __tablename__ = "retention_holds"
    __table_args__ = (
        Index("ix_retention_holds_resource", "resource_type", "resource_id", "expires_at"),
        CheckConstraint("review_at <= expires_at", name="ck_retention_holds_review_before_expiry"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(160), nullable=False)
    authority: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by_authority: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RetentionTombstone(Base):
    __tablename__ = "retention_tombstones"
    __table_args__ = (
        UniqueConstraint("resource_type", "resource_id", name="uq_retention_tombstones_resource"),
        Index("ix_retention_tombstones_disposed", "disposed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    disposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectionRequestRateBucket(Base):
    __tablename__ = "connection_request_rate_buckets"

    requester_owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MessageRateBucket(Base):
    __tablename__ = "message_rate_buckets"

    sender_owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(primary_key=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AgentProposal(Base):
    __tablename__ = "agent_proposals"
    __table_args__ = (Index("ix_agent_proposals_owner_created", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    submitter_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    submitter_grant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    document_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    if_match: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decision_actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
