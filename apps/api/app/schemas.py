from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.services.documents import STRONG_DOCUMENT_ETAG_PATTERN

AgentScope = Literal[
    "documents:write",
    "documents:read",
    "search:read",
    "inventory:read",
    "changes:read",
    "contacts:read",
    "contacts:write",
    "proposals:write",
    "organizations:read",
    "organizations:write",
    "jobs:read",
    "jobs:write",
]


def default_agent_scopes() -> list[AgentScope]:
    return ["documents:write", "documents:read", "search:read"]


def _https_website_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    try:
        address = ip_address(host)
        local_address = address.is_loopback or address.is_unspecified
    except ValueError:
        local_address = False
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or host == "localhost"
        or host.endswith(".localhost")
        or local_address
    ):
        raise ValueError("website_url must be an absolute public HTTPS URL")
    return value


class MarkdownPayload(BaseModel):
    markdown: str = Field(
        min_length=1,
        description="Client Markdown without server-assigned id, owner_id, version, or updated_at fields.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "markdown": "---\nschema: connect.md/profile\nschema_version: 1\nhandle: ada-lovelace\nname: Ada Lovelace\nheadline: Computing pioneer\nlocation: London\nskills: [Mathematics]\nvisibility: public\n---\n# Ada Lovelace\n\n## About\n\n...\n\n## Experience\n\n...\n\n## Skills\n\n- Mathematics\n"
                }
            ]
        }
    }


class PostResponse(BaseModel):
    id: str
    author_profile_handle: str
    title: str
    topics: list[str]
    version: Literal[1]
    published_at: datetime
    updated_at: datetime
    markdown: str
    markdown_url: str
    etag: str


class PostListResponse(BaseModel):
    posts: list[PostResponse]
    next_cursor: str | None


class PublicPostSummary(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    author_profile_handle: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$",
    )
    title: str = Field(min_length=1, max_length=160)
    topics: list[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=50,
                pattern=r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$",
            ),
        ]
    ] = Field(min_length=1, max_length=10)
    version: Literal[1]
    published_at: datetime
    updated_at: datetime
    html_url: str = Field(min_length=8, max_length=128, pattern=r"^/posts/[A-Za-z0-9-]+$")
    markdown_url: str = Field(
        min_length=14, max_length=128, pattern=r"^/v1/posts/[A-Za-z0-9-]+\.md$"
    )
    etag: str = Field(min_length=73, max_length=73, pattern=r'^"sha256-[0-9a-f]{64}"$')

    model_config = {"extra": "forbid"}


class PublicPostInventoryResponse(BaseModel):
    items: list[PublicPostSummary] = Field(max_length=200)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=500)

    model_config = {"extra": "forbid"}


class FollowResponse(BaseModel):
    profile_handle: str
    created_at: datetime


class FollowListResponse(BaseModel):
    follows: list[FollowResponse]
    next_cursor: str | None


class PostControlStateResponse(BaseModel):
    following: bool
    content_blocked: bool


class PostReportCreateRequest(BaseModel):
    reason_code: Literal[
        "spam",
        "harassment",
        "misinformation",
        "privacy",
        "illegal_content",
        "other",
    ]
    narrative: str | None = Field(default=None, max_length=2_000)


class PostReportResponse(BaseModel):
    id: str
    post_id: str
    reason_code: str
    created_at: datetime


class ModerationAppealCreateRequest(BaseModel):
    rationale: str = Field(min_length=1, max_length=2_000)


class ModerationCaseDecisionRequest(BaseModel):
    action: Literal["dismiss", "withhold"]
    reason_code: Literal[
        "spam",
        "harassment",
        "misinformation",
        "privacy",
        "illegal_content",
        "other",
    ]
    subject_explanation: str = Field(min_length=1, max_length=500)

    model_config = {"extra": "forbid"}


class ModerationAppealReviewRequest(BaseModel):
    action: Literal["uphold", "overturn"]
    subject_explanation: str = Field(min_length=1, max_length=500)

    model_config = {"extra": "forbid"}


class ModerationAppealSubjectResponse(BaseModel):
    id: str
    decision_id: str
    status: Literal["submitted", "upheld", "overturned"]
    submitted_at: datetime
    reviewed_at: datetime | None
    subject_explanation: str | None


class ModerationCaseSubjectResponse(BaseModel):
    id: str
    post_id: str
    status: Literal[
        "open",
        "dismissed",
        "withheld",
        "appealed",
        "appeal_upheld",
        "appeal_overturned",
        "legacy_withheld",
        "legacy_withdrawn",
    ]
    reason_code: str | None
    subject_explanation: str | None
    decided_at: datetime | None
    appeal_deadline: datetime | None
    appeal: ModerationAppealSubjectResponse | None
    updated_at: datetime


class ModerationCaseListResponse(BaseModel):
    cases: list[ModerationCaseSubjectResponse]
    next_cursor: str | None


class DocumentResponse(BaseModel):
    id: str
    kind: Literal["profile", "resume"]
    owner_id: str
    identifier: str
    visibility: Literal["public", "private"]
    version: int
    updated_at: datetime
    markdown: str
    markdown_url: str
    etag: str


class VersionResponse(BaseModel):
    version: int
    sha256: str
    actor_id: str
    actor_method: str = "clerk_jwt"
    grant_id: str | None = None
    created_at: datetime
    markdown_url: str
    etag: str


class VersionListResponse(BaseModel):
    id: str
    kind: Literal["profile", "resume"]
    versions: list[VersionResponse]


class ApiKeyCreateRequest(BaseModel):
    scopes: list[AgentScope] = Field(default_factory=default_agent_scopes, min_length=1)


class ApiKeyCreatedResponse(BaseModel):
    id: str
    prefix: str
    scopes: list[str]
    key: str = Field(description="Shown once; it cannot be retrieved again.")
    created_at: datetime
    recovery_required: Literal[False] = False


class ApiKeyRecoveryResponse(BaseModel):
    id: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    recovery_required: Literal[True] = True


ApiKeyCreateResult = Annotated[
    ApiKeyCreatedResponse | ApiKeyRecoveryResponse,
    Field(discriminator="recovery_required"),
]


class ApiKeyResponse(BaseModel):
    id: str
    prefix: str
    scopes: list[str]
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None


class IngestResponse(BaseModel):
    target_schema: Literal["connect.md/profile", "connect.md/resume"] = Field(
        description="Canonical schema identifier; the draft version is in provenance.schema_version."
    )
    draft_markdown: str = Field(description="Validated, unpublished client-write Markdown.")
    warnings: list[str] = Field(description="Non-sensitive conversion warnings.")
    provenance: dict[str, str] = Field(
        description="Source type, converter, and emitted schema version; never a publication receipt."
    )
    published: Literal[False] = False


class AccountDeletionRequestResponse(BaseModel):
    deletion_id: str
    status_receipt: str


class AccountLifecycleStatusResponse(BaseModel):
    contract: Literal["account_lifecycle_status.v1"] = "account_lifecycle_status.v1"
    state: Literal[
        "confirmation_pending",
        "confirmed",
        "erasure_planned",
        "erasing",
        "held",
        "failed",
        "live_erasure_complete",
        "backup_expiry_pending",
        "fully_erased",
    ]
    observed_at: datetime
    requested_at: datetime
    confirmed_at: datetime | None
    live_erased_at: datetime | None
    terminal_at: datetime | None
    policy_version: str
    condition: Literal["hold_active", "retry_exhausted"] | None
    next_check_after_seconds: int
    receipt_expires_at: datetime | None


class AccountDeletionConfirmationResponse(BaseModel):
    deletion_id: str


class AccountExportHeaderDTO(BaseModel):
    record_type: Literal["account_export"] = "account_export"
    cutoff: datetime
    policy_version: str


class AccountExportDocumentVersionDTO(BaseModel):
    id: str
    version: int
    sha256: str
    actor_method: str
    created_at: datetime
    canonical_markdown: str


class AccountExportDocumentDTO(BaseModel):
    record_type: Literal["document"] = "document"
    id: str
    kind: str
    identifier: str
    visibility: str
    current_version: int
    created_at: datetime
    updated_at: datetime
    versions: list[AccountExportDocumentVersionDTO]


class AccountExportPostVersionDTO(BaseModel):
    id: str
    version: int
    sha256: str
    created_at: datetime
    canonical_markdown: str


class AccountExportPostDTO(BaseModel):
    record_type: Literal["post"] = "post"
    id: str
    status: str
    author_profile_handle: str
    published_at: datetime
    created_at: datetime
    updated_at: datetime
    withdrawn_at: datetime | None
    withheld_at: datetime | None
    versions: list[AccountExportPostVersionDTO]


class AccountExportMessageDTO(BaseModel):
    record_type: Literal["message"] = "message"
    id: str
    conversation_id: str
    markdown: str
    content_sha256: str
    status: str
    created_at: datetime
    retention_expires_at: datetime


class AccountExportApplicationDTO(BaseModel):
    record_type: Literal["application"] = "application"
    id: str
    job_id: str
    snapshot_document_id: str
    snapshot_document_kind: str
    snapshot_document_identifier: str
    snapshot_document_version: int
    snapshot_sha256: str
    message: str
    status: str
    confirmed_at: datetime
    retention_policy_version: str
    retention_expires_at: datetime
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None


class AccountExportProposalDTO(BaseModel):
    record_type: Literal["agent_proposal"] = "agent_proposal"
    id: str
    document_id: str
    document_kind: str
    document_identifier: str
    markdown: str
    if_match: str
    status: str
    created_at: datetime
    decided_at: datetime | None


class AccountExportContactRequestDTO(BaseModel):
    record_type: Literal["outbound_contact_request"] = "outbound_contact_request"
    id: str
    target_document_id: str
    purpose: str
    message: str
    status: str
    origin: str
    created_at: datetime
    decided_at: datetime | None
    retention_expires_at: datetime


class AccountExportRelationshipDTO(BaseModel):
    record_type: Literal["connection_request", "connection", "conversation"]
    id: str
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    decided_at: datetime | None = None
    closed_at: datetime | None = None
    retention_expires_at: datetime
    messaging_requested: bool | None = None
    messaging_enabled: bool | None = None


class AccountExportModerationCaseDTO(BaseModel):
    record_type: Literal["moderation_case"] = "moderation_case"
    id: str
    post_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    retention_expires_at: datetime | None
    decision: dict[str, str | datetime] | None


class AccountExportModerationAppealDTO(BaseModel):
    record_type: Literal["moderation_appeal"] = "moderation_appeal"
    id: str
    case_id: str
    decision_id: str
    status: str
    submitted_at: datetime
    reviewed_at: datetime | None
    subject_explanation: str | None


class AccountExportOrganizationVerificationDTO(BaseModel):
    record_type: Literal["organization_verification"] = "organization_verification"
    id: str
    organization_id: str
    state: str
    material_claim_digest: str
    submitted_at: datetime
    reviewed_at: datetime | None
    expires_at: datetime | None
    events: list[dict[str, str | datetime | None]]


def _default_search_agent_capabilities() -> list[Literal["internal_contact_request"]]:
    return ["internal_contact_request"]


TaxonomyName = Literal[
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
]
SearchCompactValue = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]
SearchCanonicalValue = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=336)
]
SearchBoundedText = Annotated[str, StringConstraints(max_length=160)]
SearchTimestamp = Annotated[str, StringConstraints(max_length=40)]


class TaxonomyCatalogEntry(BaseModel):
    taxonomy: TaxonomyName
    parameters: list[str] = Field(min_length=1, max_length=3)
    kind: Literal["reference", "connect.md enum"]
    semantics: Literal["AND", "OR", "singleton"]
    source: str = Field(min_length=1, max_length=200)
    authority: str = Field(min_length=1, max_length=300)
    current_revision: int = Field(ge=0)

    model_config = {"extra": "forbid"}


class TaxonomyTermResponse(BaseModel):
    taxonomy: TaxonomyName
    scheme: str = Field(min_length=1, max_length=80)
    external_id: str = Field(min_length=1, max_length=255)
    canonical_id: str = Field(min_length=1, max_length=336)
    filter_value: str = Field(pattern=r"^tx1_[0-9a-f]{64}$")
    label: str | None = Field(default=None, max_length=280)
    label_conflict: bool
    vocabulary_version: str | None = Field(default=None, max_length=100)
    version_conflict: bool

    model_config = {"extra": "forbid"}


class TaxonomyTermListResponse(BaseModel):
    terms: list[TaxonomyTermResponse] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    revision: int = Field(ge=0)

    model_config = {"extra": "forbid"}


class TaxonomyFacetEntry(BaseModel):
    taxonomy: TaxonomyName
    parameter: str = Field(min_length=1, max_length=40)
    canonical_id: str = Field(min_length=1, max_length=336)
    filter_value: str = Field(pattern=r"^tx1_[0-9a-f]{64}$")
    label: str | None = Field(default=None, max_length=280)
    label_conflict: bool
    vocabulary_version: str | None = Field(default=None, max_length=100)
    version_conflict: bool
    count: int = Field(ge=0)

    model_config = {"extra": "forbid"}


class SearchQueryRequest(BaseModel):
    mode: Literal["projection", "exact"] = "projection"
    q: str = Field(default="", max_length=200)
    kind: Literal["profile", "resume"] | None = None
    skills: list[SearchCompactValue] = Field(default_factory=list, max_length=50)
    location: SearchBoundedText | None = None
    occupation_ids: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    industry_ids: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    skill_ids: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    language_ids: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    location_id: SearchCanonicalValue | None = None
    location_country_code: Annotated[str, StringConstraints(max_length=3)] | None = None
    location_region: SearchBoundedText | None = None
    location_city: SearchBoundedText | None = None
    seniority_ids: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    seniority_id: SearchCanonicalValue | None = None
    work_modes: list[SearchCompactValue] = Field(default_factory=list, max_length=20)
    availability_status: SearchCompactValue | None = None
    availability_from: SearchTimestamp | None = None
    open_to: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    open_to_ids: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    organization_ids: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    representative_ids: list[SearchCanonicalValue] = Field(default_factory=list, max_length=50)
    representation_status: SearchCompactValue | None = None
    contact_disclosure: SearchCompactValue | None = None
    agent_capability: Literal["internal_contact_request"] | None = None
    updated_after: SearchTimestamp | None = None
    updated_before: SearchTimestamp | None = None
    sort_updated: Literal["asc", "desc"] | None = None
    facets: list[SearchCompactValue] = Field(default_factory=list, max_length=30)
    offset: int = Field(default=0, ge=0, le=1000)
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    facet_limit: int = Field(default=100, ge=1, le=500)

    model_config = {"extra": "forbid"}

    @field_validator("cursor")
    @classmethod
    def _reject_blank_cursor(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("cursor must not be blank")
        return value


class SearchAgentIdentityReference(BaseModel):
    handle: str = Field(min_length=1, max_length=100)
    capabilities: list[Literal["internal_contact_request"]] = Field(
        default_factory=_default_search_agent_capabilities,
        min_length=1,
        max_length=1,
    )

    @field_validator("capabilities")
    @classmethod
    def _require_internal_contact_capability(
        cls, value: list[Literal["internal_contact_request"]]
    ) -> list[Literal["internal_contact_request"]]:
        if value != ["internal_contact_request"]:
            raise ValueError("capabilities must contain internal_contact_request exactly once")
        return value


class SearchHit(BaseModel):
    id: str
    kind: Literal["profile", "resume"]
    identifier: str
    name: str
    headline: str
    title: str | None = None
    location: str
    skills: list[str]
    skill_ids: list[str] = Field(default_factory=list)
    skill_filter_values: list[str] = Field(default_factory=list)
    occupation_ids: list[str] = Field(default_factory=list)
    occupation_filter_values: list[str] = Field(default_factory=list)
    occupations: list[str] = Field(default_factory=list)
    industry_ids: list[str] = Field(default_factory=list)
    industry_filter_values: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    language_ids: list[str] = Field(default_factory=list)
    language_filter_values: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    language_proficiencies: list[str] = Field(default_factory=list)
    location_id: str | None = None
    location_filter_value: str | None = None
    location_label: str | None = None
    location_country_code: str | None = None
    location_region: str | None = None
    location_city: str | None = None
    seniority_ids: list[str] = Field(default_factory=list)
    seniority_filter_values: list[str] = Field(default_factory=list)
    seniority_id: str | None = None
    seniority_filter_value: str | None = None
    seniority: str | None = None
    work_modes: list[str] = Field(default_factory=list)
    work_mode_filter_values: list[str] = Field(default_factory=list)
    availability_status: str | None = None
    availability_from: str | None = None
    open_to: list[str] = Field(default_factory=list)
    open_to_ids: list[str] = Field(default_factory=list)
    open_to_filter_values: list[str] = Field(default_factory=list)
    organization_ids: list[str] = Field(default_factory=list)
    organization_filter_values: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    representation_status: str | None = None
    representative: str | None = None
    representative_ids: list[str] = Field(default_factory=list)
    representative_id: str | None = None
    representative_filter_values: list[str] = Field(default_factory=list)
    representative_filter_value: str | None = None
    contact_disclosure: str | None = None
    taxonomy_versions: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None
    schema_version: int | None = None
    version: int
    excerpt: str | None
    html_url: str
    markdown_url: str
    agent_identities: list[SearchAgentIdentityReference] = Field(
        default_factory=list, max_length=10
    )


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    offset: int
    limit: int
    total: int
    indexing_available: bool
    warning: str | None = None
    facets: dict[str, dict[str, int]] = Field(default_factory=dict)
    taxonomy_facets: dict[str, list[TaxonomyFacetEntry]] = Field(default_factory=dict)
    mode: Literal["projection", "exact"] = "projection"
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    search_revision: int | None = Field(default=None, ge=0)
    complete: bool = False
    facet_truncated: dict[str, bool] = Field(default_factory=dict)


class OwnerDocumentSummary(BaseModel):
    id: str
    kind: Literal["profile", "resume"]
    identifier: str
    visibility: Literal["public", "private"]
    version: int
    updated_at: datetime
    markdown_url: str
    etag: str


class OwnerDocumentListResponse(BaseModel):
    documents: list[OwnerDocumentSummary]
    next_cursor: str | None


class PublicDocumentSummary(BaseModel):
    kind: Literal["profile", "resume"]
    slug: str
    updated_at: datetime


class PublicDocumentListResponse(BaseModel):
    items: list[PublicDocumentSummary]
    next_cursor: str | None


class ChangeEventResponse(BaseModel):
    sequence: int
    type: str
    resource_type: str
    resource_id: str
    actor_id: str
    actor_method: str
    grant_id: str | None
    occurred_at: datetime
    data: dict[str, Any]


class ChangeFeedResponse(BaseModel):
    events: list[ChangeEventResponse]
    next_cursor: str | None
    has_more: bool


class RecentChangeRecordResponse(BaseModel):
    events: list[ChangeEventResponse] = Field(max_length=25)


class MeResponse(BaseModel):
    owner_id: str
    actor_id: str
    authentication_method: str
    scopes: list[str]
    grant_id: str | None = None
    grant_name: str | None = None
    grant_mode: Literal["proposal_only", "direct"] | None = None
    resource: dict[str, str | None] | None = None


class AgentGrantResource(BaseModel):
    type: Literal["owner", "document", "organization"] = "owner"
    id: str | None = None


class AgentGrantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["proposal_only", "direct"] = "proposal_only"
    resource: AgentGrantResource = Field(default_factory=AgentGrantResource)
    scopes: list[AgentScope] = Field(default_factory=default_agent_scopes, min_length=1)
    expires_at: datetime | None = None
    expires_in_seconds: int | None = Field(default=2_592_000, ge=60, le=7_776_000)


class AgentGrantCreatedResponse(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    mode: Literal["proposal_only", "direct"]
    resource: AgentGrantResource
    expires_at: datetime
    key: str = Field(description="Shown once; it cannot be retrieved again.")
    created_at: datetime


class AgentGrantRecoveryResponse(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    mode: Literal["proposal_only", "direct"]
    resource: AgentGrantResource
    expires_at: datetime
    recovery_required: Literal[True] = True
    created_at: datetime


class AgentGrantResponse(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    mode: Literal["proposal_only", "direct"]
    resource: AgentGrantResource
    expires_at: datetime
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None


class AgentIdentityCreateRequest(BaseModel):
    handle: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$",
    )
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    profile_handle: str = Field(min_length=1, max_length=100)


class AgentIdentityResponse(BaseModel):
    handle: str
    display_name: str
    description: str
    profile_handle: str
    capabilities: list[Literal["internal_contact_request"]]


class AgentIdentityDirectoryResponse(BaseModel):
    identities: list[AgentIdentityResponse]
    next_cursor: str | None = Field(default=None, min_length=1, max_length=500)


class AgentIdentityOwnerResponse(BaseModel):
    handle: str
    display_name: str
    description: str
    profile_handle: str
    status: Literal["active", "withdrawn", "withheld"]
    created_at: datetime
    updated_at: datetime


class AgentMandateCreateRequest(BaseModel):
    expires_at: datetime


class AgentMandateIssuedResponse(BaseModel):
    id: str
    scope: Literal["internal_contact_request"]
    expires_at: datetime
    grant: AgentGrantCreatedResponse


class AgentMandateRecoveryResponse(BaseModel):
    id: str
    scope: Literal["internal_contact_request"]
    status: Literal["active", "revoked", "expired", "suspended"]
    expires_at: datetime
    grant_prefix: str
    recovery_required: Literal[True]


class AgentMandateInventoryResponse(BaseModel):
    id: str
    scope: Literal["internal_contact_request"]
    status: Literal["active", "revoked", "expired", "suspended"]
    expires_at: datetime
    grant_prefix: str


class ContactPolicyUpdateRequest(BaseModel):
    allow_agent_requests: bool
    daily_request_limit: int = Field(default=5, ge=1, le=20)


class ContactPolicyResponse(BaseModel):
    allow_agent_requests: bool
    daily_request_limit: int
    version: int
    updated_at: datetime | None
    etag: str


class ContactRequestCreate(BaseModel):
    target_profile_handle: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=2_000)


class AgentOutreachCreate(BaseModel):
    target_agent_handle: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=2_000)


class AgentOutreachReceipt(BaseModel):
    id: str
    origin: Literal["agent_outreach"]
    status: Literal["pending"]
    sender_identity_handle: str
    target_identity_handle: str
    created_at: datetime


class AgentOutreachStatusResponse(BaseModel):
    id: str
    origin: Literal["agent_outreach"]
    status: Literal["pending", "accepted", "declined"]
    sender_identity_handle: str
    target_identity_handle: str
    created_at: datetime
    decided_at: datetime | None


class ContactActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1_000)


class ContactRequestResponse(BaseModel):
    id: str
    sender_owner_id: str
    recipient_owner_id: str
    target_document_id: str
    purpose: str
    message: str
    status: Literal["pending", "accepted", "rejected", "blocked", "reported"]
    sender_actor_id: str
    sender_actor_method: str
    sender_grant_id: str | None
    origin: Literal["profile_contact", "agent_outreach"]
    sender_identity_handle: str | None
    sender_identity_display_name: str | None
    target_identity_handle: str | None
    target_identity_display_name: str | None
    sender_mandate_scope: Literal["internal_contact_request"] | None
    created_at: datetime
    decided_at: datetime | None


class ContactInboxResponse(BaseModel):
    requests: list[ContactRequestResponse]
    next_cursor: str | None


class OrganizationCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5_000)
    website_url: str | None = Field(default=None, max_length=2_048)
    visibility: Literal["public", "private"] = "private"

    @field_validator("website_url")
    @classmethod
    def validate_website_url(cls, value: str | None) -> str | None:
        return _https_website_url(value)


class OrganizationUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5_000)
    website_url: str | None = Field(default=None, max_length=2_048)
    visibility: Literal["public", "private"] | None = None

    @field_validator("website_url")
    @classmethod
    def validate_website_url(cls, value: str | None) -> str | None:
        return _https_website_url(value)


class OrganizationResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None
    website_url: str | None
    visibility: Literal["public", "private"]
    recruiting_verification_active: bool
    recruiting_verification_purpose: Literal["recruiting_control"] | None
    recruiting_verification_expires_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    etag: str


class OrganizationListResponse(BaseModel):
    organizations: list[OrganizationResponse]
    next_cursor: str | None


class EmployerOrganizationSummary(BaseModel):
    id: str
    slug: str
    name: str
    management_role: Literal["owner", "admin"]
    visibility: Literal["public", "private"]
    recruiting_verification_active: bool
    recruiting_verification_purpose: Literal["recruiting_control"] | None
    recruiting_verification_expires_at: datetime | None
    updated_at: datetime


class EmployerOrganizationInventoryResponse(BaseModel):
    organizations: list[EmployerOrganizationSummary]
    next_cursor: str | None


class OrganizationVerificationSubmissionRequest(BaseModel):
    evidence_kind: Literal[
        "corporate_registration",
        "domain_control",
        "employment_authority",
        "other",
    ]
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)
    artifact_content_type: Literal["application/pdf", "image/jpeg", "image/png", "text/plain"]
    artifact_base64: str = Field(min_length=4, max_length=349_528)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key or len(key) > 64 or len(item) > 500 for key, item in value.items()):
            raise ValueError("verification metadata keys and values are bounded")
        return value

    @field_validator("artifact_base64")
    @classmethod
    def validate_artifact_base64(cls, value: str) -> str:
        from base64 import b64decode

        try:
            decoded = b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError("artifact_base64 must be valid base64") from exc
        if not decoded or len(decoded) > 262_144:
            raise ValueError("verification artifact must contain at most 262144 bytes")
        return value


class OrganizationVerificationSubmissionResponse(BaseModel):
    verification_id: str
    state: Literal["submitted"]
    evidence_sha256: str
    artifact_content_type: Literal["application/pdf", "image/jpeg", "image/png", "text/plain"]
    artifact_size_bytes: int
    submitted_at: datetime


VerificationStatus = Literal[
    "unverified",
    "submitted",
    "under_review",
    "active",
    "rejected",
    "expired",
    "suspended",
    "revoked",
]
ReviewableVerificationStatus = Literal["submitted", "under_review"]


class OrganizationVerificationOwnerStatusResponse(BaseModel):
    verification_id: str | None
    state: VerificationStatus
    submitted_at: datetime | None
    updated_at: datetime | None
    policy_version: str | None
    expires_at: datetime | None


class OrganizationVerificationReviewerSummaryResponse(BaseModel):
    verification_id: str
    organization_slug: str
    organization_name: str
    state: VerificationStatus
    evidence_kind: Literal[
        "corporate_registration",
        "domain_control",
        "employment_authority",
        "other",
    ]
    evidence_sha256: str
    artifact_content_type: Literal["application/pdf", "image/jpeg", "image/png", "text/plain"]
    artifact_size_bytes: int
    material_claim_digest: str
    submitted_at: datetime
    updated_at: datetime
    policy_version: str | None
    expires_at: datetime | None


class OrganizationVerificationReviewerDetailResponse(
    OrganizationVerificationReviewerSummaryResponse
):
    organization_website_url: str | None
    organization_material_version: int = Field(ge=1)
    evidence_metadata: dict[str, str]
    evidence_retention_expires_at: datetime
    evidence_url: str = Field(min_length=1, max_length=512)
    review_etag: str = Field(pattern=r'^"sha256-[0-9a-f]{64}"$')


class OrganizationVerificationReviewerListResponse(BaseModel):
    verifications: list[OrganizationVerificationReviewerSummaryResponse]
    next_cursor: str | None


class OrganizationVerificationDecisionRequest(BaseModel):
    expected_state: VerificationStatus
    policy_version: str | None = Field(default=None, min_length=1, max_length=80)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expiry_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value


class OrganizationAdminCreateRequest(BaseModel):
    member_profile_handle: str = Field(
        min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )
    role: Literal["admin", "member"] = "member"


class OrganizationAdminResponse(BaseModel):
    id: str
    organization_id: str
    member_profile_handle: str | None
    role: Literal["admin", "member"]
    status: Literal["invited", "active"]
    created_at: datetime


class OrganizationAdminListResponse(BaseModel):
    members: list[OrganizationAdminResponse]
    next_cursor: str | None


class OrganizationMembershipInvitationResponse(BaseModel):
    id: str
    organization_id: str
    organization_slug: str
    organization_name: str
    role: Literal["admin", "member"]
    status: Literal["invited"]
    created_at: datetime


class OrganizationMembershipInvitationListResponse(BaseModel):
    invitations: list[OrganizationMembershipInvitationResponse]
    next_cursor: str | None


class JobCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10_000)
    location: str | None = Field(default=None, max_length=200)
    work_mode: Literal["remote", "hybrid", "onsite"] | None = None
    employment_type: (
        Literal["full_time", "part_time", "contract", "internship", "temporary"] | None
    ) = None


class JobUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=10_000)
    location: str | None = Field(default=None, max_length=200)
    work_mode: Literal["remote", "hybrid", "onsite"] | None = None
    employment_type: (
        Literal["full_time", "part_time", "contract", "internship", "temporary"] | None
    ) = None


class JobResponse(BaseModel):
    id: str
    organization_id: str
    organization_slug: str
    organization_name: str
    slug: str
    title: str
    description: str
    location: str | None
    work_mode: Literal["remote", "hybrid", "onsite"] | None
    employment_type: Literal["full_time", "part_time", "contract", "internship", "temporary"] | None
    status: Literal["draft", "published", "closed"]
    version: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    etag: str


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    next_cursor: str | None


class EmployerJobSummary(BaseModel):
    id: str
    organization_id: str
    organization_slug: str
    organization_name: str
    management_role: Literal["owner", "admin"]
    slug: str
    title: str
    status: Literal["draft", "published", "closed"]
    location: str | None
    work_mode: Literal["remote", "hybrid", "onsite"] | None
    employment_type: Literal["full_time", "part_time", "contract", "internship", "temporary"] | None
    updated_at: datetime


class EmployerJobInventoryResponse(BaseModel):
    jobs: list[EmployerJobSummary]
    next_cursor: str | None


class ApplicationCreateRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2_000)
    snapshot_kind: Literal["profile", "resume"]
    snapshot_identifier: str = Field(min_length=1, max_length=100)
    human_confirmed: Literal[True]


class ApplicationResponse(BaseModel):
    id: str
    job_id: str
    organization_slug: str
    job_slug: str
    status: Literal["submitted", "under_review", "accepted", "rejected", "withdrawn"]
    snapshot_kind: Literal["profile", "resume"]
    snapshot_identifier: str
    snapshot_version: int
    snapshot_sha256: str
    confirmed_at: datetime
    retention_policy_version: str
    retention_expires_at: datetime
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None


class ApplicationListResponse(BaseModel):
    applications: list[ApplicationResponse]
    next_cursor: str | None


class ApplicationDetailResponse(ApplicationResponse):
    message: str


class ApplicationSnapshotResponse(BaseModel):
    application_id: str
    snapshot_kind: Literal["profile", "resume"]
    snapshot_identifier: str
    snapshot_version: int
    snapshot_sha256: str
    markdown: str
    markdown_url: str


class ConnectionRequestCreateRequest(BaseModel):
    recipient_profile_handle: str = Field(min_length=1, max_length=100)
    messaging_requested: bool = False


class ConnectionRequestDecisionRequest(BaseModel):
    messaging_consent: bool | None = None


class ConnectionRequestResponse(BaseModel):
    id: str
    counterparty_owner_id: str
    counterparty_profile_handle: str
    direction: Literal["inbound", "outbound"]
    messaging_requested: bool
    messaging_consent: bool | None
    status: Literal["pending", "accepted", "rejected", "blocked"]
    created_at: datetime
    decided_at: datetime | None
    retention_expires_at: datetime


class ConnectionRequestListResponse(BaseModel):
    requests: list[ConnectionRequestResponse]
    next_cursor: str | None


class ConnectionResponse(BaseModel):
    id: str
    counterparty_owner_id: str
    counterparty_profile_handle: str
    messaging_enabled: bool
    created_at: datetime
    retention_expires_at: datetime


class ConnectionListResponse(BaseModel):
    connections: list[ConnectionResponse]
    next_cursor: str | None


class ConversationCreateRequest(BaseModel):
    connection_id: str = Field(min_length=36, max_length=36)


class ConversationResponse(BaseModel):
    id: str
    connection_id: str
    counterparty_owner_id: str
    counterparty_profile_handle: str
    created_at: datetime
    retention_expires_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    next_cursor: str | None


class MessageCreateRequest(BaseModel):
    markdown: str = Field(min_length=1, max_length=4_000)


class MessageSendResponse(BaseModel):
    id: str
    conversation_id: str
    created_at: datetime
    retention_expires_at: datetime


class MessageResponse(MessageSendResponse):
    sender_owner_id: str
    direction: Literal["sent", "received"]
    markdown: str


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    next_cursor: str | None


class NotificationResponse(BaseModel):
    id: str
    type: str
    actor_owner_id: str | None
    resource_type: str
    resource_id: str
    created_at: datetime
    read_at: datetime | None


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    next_cursor: str | None


class AgentProposalCreateRequest(BaseModel):
    kind: Literal["profile", "resume"]
    identifier: str = Field(min_length=1, max_length=100)
    markdown: str = Field(min_length=1)
    if_match: str = Field(pattern=STRONG_DOCUMENT_ETAG_PATTERN)


class AgentProposalResponse(BaseModel):
    id: str
    document_id: str
    kind: Literal["profile", "resume"]
    identifier: str
    markdown: str
    if_match: str
    status: Literal["pending", "accepted", "rejected"]
    submitter_actor_id: str
    submitter_grant_id: str
    created_at: datetime
    decided_at: datetime | None


class AgentProposalListResponse(BaseModel):
    proposals: list[AgentProposalResponse]
    next_cursor: str | None
