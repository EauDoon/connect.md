from __future__ import annotations

import asyncio
import json
import re
import secrets
from base64 import b64decode, b64encode, urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from inspect import isawaitable, signature
from ipaddress import ip_address
from time import monotonic
from typing import Annotated, Any, Literal, NoReturn, cast
from uuid import UUID, uuid4

import anyio
from fastapi import Depends, FastAPI, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, StringConstraints, ValidationError
from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.auth import (
    AGENT_GRANT_RESOURCE_SCOPES,
    IMPERSONATION_READ_ONLY_CODE,
    AgentGrantManager,
    ApiKeyManager,
    AuthenticationUnavailable,
    ClerkVerifier,
    LifecycleConfirmationClaims,
    Principal,
    agent_grant_definition_is_valid,
    assert_account_access,
    decrypt_lifecycle_provider_session,
    decrypt_lifecycle_provider_subject,
    decrypt_lifecycle_receipt,
    encrypt_lifecycle_provider_session,
    encrypt_lifecycle_provider_subject,
    encrypt_lifecycle_receipt,
    lifecycle_hmac,
    optional_principal,
    require_lifecycle_confirmation_claims,
    require_principal,
)
from app.config import Settings, get_settings
from app.db import (
    RollbackFileCleanup,
    build_engine,
    build_session_factory,
    get_session,
    require_current_database_schema,
)
from app.http.origin import public_base_url
from app.ingest import build_ingest_draft, ingest_capabilities
from app.markdown import (
    PUBLIC_MARKDOWN_VALIDATION_DETAIL,
    MarkdownSizeError,
    MarkdownValidationError,
    MarkdownVersionConflictError,
    canonical_document_max_utf8_bytes,
    prepare_client_document,
    validate_canonical,
)
from app.models import (
    ACCOUNT_BACKUP_AUTHORITY_ID,
    AccountAccessDeny,
    AccountBackupAuthority,
    AccountBackupManifest,
    AccountBackupObligation,
    AccountErasureFileProof,
    AccountErasureItem,
    AccountLifecycle,
    AccountLifecycleReceiptRateLimit,
    AccountLifecycleTombstone,
    AccountReverificationUse,
    AgentGrant,
    AgentIdentity,
    AgentMandate,
    AgentOutreachDirectPeerRateBucket,
    AgentOutreachRecipientRateBucket,
    AgentProposal,
    ApiKey,
    Application,
    ApplicationRateBucket,
    ChangeEvent,
    Connection,
    ConnectionBlock,
    ConnectionRequest,
    ConnectionRequestRateBucket,
    ContactBlock,
    ContactPolicy,
    ContactRateBucket,
    ContactRequest,
    Conversation,
    Document,
    DocumentVersion,
    FollowRateBucket,
    IdempotencyRecord,
    Job,
    JobVersion,
    LifecycleTask,
    Message,
    MessageRateBucket,
    ModerationAppeal,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    Notification,
    Organization,
    OrganizationMembership,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
    Post,
    PostContentBlock,
    PostGraphPairLock,
    PostModerationEvent,
    PostRateBucket,
    PostReport,
    PostReportRateBucket,
    PostVersion,
    ProfileFollow,
    RetentionHold,
    RetentionTombstone,
    SearchProjectionTask,
    new_id,
)
from app.observability import configure_request_logger, emit_request_log
from app.protocol_arguments import (
    IDEMPOTENCY_KEY_PATTERN as _IDEMPOTENCY_KEY_PATTERN,
)
from app.protocol_arguments import (
    IDEMPOTENCY_KEY_RE as _IDEMPOTENCY_KEY_RE,
)
from app.protocol_arguments import (
    canonical_agent_outreach_request_id as _canonical_agent_outreach_request_id,
)
from app.protocol_arguments import (
    mcp_agent_outreach_arguments,
    mcp_agent_outreach_status_argument,
    mcp_create_arguments,
    mcp_get_changes_arguments,
    mcp_list_my_documents_arguments,
    mcp_read_document_arguments,
    mcp_update_arguments,
    protocol_agent_directory_arguments,
    protocol_agent_identity_argument,
    protocol_profile_agents_arguments,
    protocol_search_arguments,
)
from app.routes.agent_card import router as agent_card_router
from app.routes.discovery import router as discovery_router
from app.routes.health import router as health_router
from app.routes.protocol_metadata import router as protocol_metadata_router
from app.routes.schemas import router as schemas_router
from app.routes.taxonomy import router as taxonomy_router
from app.schemas import (
    AccountDeletionConfirmationResponse,
    AccountDeletionRequestResponse,
    AccountExportApplicationDTO,
    AccountExportContactRequestDTO,
    AccountExportDocumentDTO,
    AccountExportDocumentVersionDTO,
    AccountExportHeaderDTO,
    AccountExportMessageDTO,
    AccountExportModerationAppealDTO,
    AccountExportModerationCaseDTO,
    AccountExportOrganizationVerificationDTO,
    AccountExportPostDTO,
    AccountExportPostVersionDTO,
    AccountExportProposalDTO,
    AccountExportRelationshipDTO,
    AccountLifecycleStatusResponse,
    AgentGrantCreatedResponse,
    AgentGrantCreateRequest,
    AgentGrantRecoveryResponse,
    AgentGrantResource,
    AgentGrantResponse,
    AgentIdentityCreateRequest,
    AgentIdentityDirectoryResponse,
    AgentIdentityOwnerResponse,
    AgentIdentityResponse,
    AgentMandateCreateRequest,
    AgentMandateInventoryResponse,
    AgentMandateIssuedResponse,
    AgentMandateRecoveryResponse,
    AgentOutreachCreate,
    AgentOutreachReceipt,
    AgentOutreachStatusResponse,
    AgentProposalCreateRequest,
    AgentProposalListResponse,
    AgentProposalResponse,
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResult,
    ApiKeyRecoveryResponse,
    ApiKeyResponse,
    ApplicationCreateRequest,
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationSnapshotResponse,
    ChangeEventResponse,
    ChangeFeedResponse,
    ConnectionListResponse,
    ConnectionRequestCreateRequest,
    ConnectionRequestDecisionRequest,
    ConnectionRequestListResponse,
    ConnectionRequestResponse,
    ConnectionResponse,
    ContactActionRequest,
    ContactInboxResponse,
    ContactPolicyResponse,
    ContactPolicyUpdateRequest,
    ContactRequestCreate,
    ContactRequestResponse,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    DocumentResponse,
    EmployerJobInventoryResponse,
    EmployerJobSummary,
    EmployerOrganizationInventoryResponse,
    EmployerOrganizationSummary,
    FollowListResponse,
    FollowResponse,
    IngestResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobUpdateRequest,
    MeResponse,
    MessageCreateRequest,
    MessageListResponse,
    MessageResponse,
    MessageSendResponse,
    ModerationAppealCreateRequest,
    ModerationAppealReviewRequest,
    ModerationAppealSubjectResponse,
    ModerationCaseDecisionRequest,
    ModerationCaseListResponse,
    ModerationCaseSubjectResponse,
    NotificationListResponse,
    NotificationResponse,
    OrganizationAdminCreateRequest,
    OrganizationAdminListResponse,
    OrganizationAdminResponse,
    OrganizationCreateRequest,
    OrganizationListResponse,
    OrganizationMembershipInvitationListResponse,
    OrganizationMembershipInvitationResponse,
    OrganizationResponse,
    OrganizationUpdateRequest,
    OrganizationVerificationDecisionRequest,
    OrganizationVerificationOwnerStatusResponse,
    OrganizationVerificationReviewerDetailResponse,
    OrganizationVerificationReviewerListResponse,
    OrganizationVerificationReviewerSummaryResponse,
    OrganizationVerificationSubmissionRequest,
    OrganizationVerificationSubmissionResponse,
    OwnerDocumentListResponse,
    OwnerDocumentSummary,
    PostControlStateResponse,
    PostListResponse,
    PostReportCreateRequest,
    PostReportResponse,
    PostResponse,
    PublicDocumentListResponse,
    PublicDocumentSummary,
    PublicPostInventoryResponse,
    PublicPostSummary,
    RecentChangeRecordResponse,
    SearchQueryRequest,
    SearchResponse,
    VersionListResponse,
    VersionResponse,
)
from app.services.api_key_replay import replay_api_key_receipt
from app.services.artifact_durability import (
    CANONICAL_DOCUMENT_CREATE_TARGET_IDS,
    PROFESSIONAL_POST_CREATE_TARGET_ID,
    ArtifactDescriptor,
    ArtifactDurabilityUnavailable,
    ArtifactReconciler,
    acquire_artifact_intent_lock,
    derive_artifact_intent_uuid,
    descriptor_owner_matches,
    stage_artifact,
)
from app.services.contact_policy_replay import replay_contact_policy_receipt
from app.services.cursors import CursorCodec, CursorError
from app.services.deletion_journal import (
    DeletionCommitmentJournal,
    DeletionJournalError,
    verify_live_deletion_mirror,
)
from app.services.documents import (
    STRONG_DOCUMENT_ETAG_PATTERN,
    DocumentConflictError,
    DocumentForbiddenError,
    DocumentNotFoundError,
    DocumentPreconditionError,
    DocumentService,
    if_match_satisfied,
    public_owner_id,
    strong_etag,
)
from app.services.exact_search import (
    EXACT_SEARCH_CURSOR_MAX_LENGTH,
    EXACT_SEARCH_TOO_BROAD_MESSAGE,
    ExactSearchCursorMalformed,
    ExactSearchCursorStale,
    ExactSearchService,
    ExactSearchTooBroad,
    ExactSearchUnavailable,
)
from app.services.organization_verification import material_claim_digest
from app.services.post_moderation import (
    PostModerationConfigurationError,
    PostModerationConflictError,
    PostModerationError,
    PostModerationInputError,
    PostModerationNotFoundError,
    PostModerationPreconditionError,
    PostModerationStorageError,
    appeal_evidence_snapshot,
    case_evidence_snapshot,
    configured_moderation_authorities,
    decide_case,
    lock_appeal_review_bundle,
    lock_case_review_bundle,
    review_appeal,
)
from app.services.public_search import (
    _AGENT_IDENTITY_SEARCH_CHUNK_SIZE,  # noqa: F401
    _INTERNAL_CONTACT_REQUEST_CAPABILITY,
    _MAX_SEARCH_AGENT_IDENTITIES_PER_PROFILE,  # noqa: F401
    enrich_public_search_hits,  # noqa: F401
    execute_public_search,
    markdown_url,
    public_agent_identity_eligibility_filters,
    rest_search_unavailable,
    sanitized_search_hit,  # noqa: F401
    search_agent_identity_references,  # noqa: F401
)
from app.services.recruiting_evidence import (
    RecruitingEvidenceUnavailable,
    VerifiedRecruitingEvidence,
    artifact_extension,
    claims_from_rows,
    verify_recruiting_evidence,
)
from app.services.reservations import identifier_is_reserved
from app.services.search import MAX_AGENT_SEARCH_RESULTS, MeiliSearchProjection, SearchUnavailable
from app.services.storage import StorageIntegrityError, VersionStore
from app.services.taxonomy import (
    MAX_SEARCH_REPEATED_VALUES,
    TaxonomyCursorMalformed,
    TaxonomyCursorStale,
    TaxonomyInvalidValue,
    TaxonomyService,
    TaxonomyUnavailable,
    TaxonomyUnknown,
    remove_document_projection,
)

MARKDOWN_MEDIA_TYPE = "text/markdown"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_NON_HUMAN_CHANGE_FEED_EXCLUDED_RESOURCE_TYPES = frozenset(
    {
        "account_deletion",
        "agent_grant",
        "agent_grant_recovery",
        "agent_mandate",
        "api_key",
        "application",
        "connection_request",
        "connection",
        "conversation",
        "contact_policy",
        "contact_request",
        "contact_request_decision",
        "message",
        "notification",
        "organization_membership",
        "organization_verification",
        "post",
    }
)
_NON_HUMAN_CHANGE_FEED_EXCLUDED_EVENT_PATTERNS = (
    "organization.member\\_%",
    "organization.membership\\_%",
)
DocumentKind = Literal["profile", "resume"]
Visibility = Literal["public", "private"]
SchemaIdentifier = Literal["connect.md/profile", "connect.md/resume"]
SkillFilter = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
_CONTACT_SENDER_DAILY_LIMIT = 20
_INGEST_TARGETS: dict[str, tuple[DocumentKind, SchemaIdentifier, int]] = {
    "profile": ("profile", "connect.md/profile", 2),
    "connect.md/profile": ("profile", "connect.md/profile", 2),
    "resume": ("resume", "connect.md/resume", 2),
    "connect.md/resume": ("resume", "connect.md/resume", 2),
    "profile-v1": ("profile", "connect.md/profile", 1),
    "connect.md/profile/v1": ("profile", "connect.md/profile", 1),
    "resume-v1": ("resume", "connect.md/resume", 1),
    "connect.md/resume/v1": ("resume", "connect.md/resume", 1),
}
_ERROR_SCHEMA = {
    "type": "object",
    "required": ["type", "title", "status", "detail", "instance", "request_id"],
    "properties": {
        "type": {"type": "string", "format": "uri-reference"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {
            "description": "Human- and agent-readable error detail.",
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {}},
                {"type": "object", "additionalProperties": True},
            ],
        },
        "instance": {"type": "string", "format": "uri-reference"},
        "request_id": {"type": "string"},
    },
}

_PROBLEM_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    412: "Precondition Failed",
    413: "Content Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Content",
    428: "Precondition Required",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}
_PROBLEM_SLUGS = {
    400: "bad-request",
    401: "authentication-required",
    403: "forbidden",
    404: "not-found",
    405: "method-not-allowed",
    409: "conflict",
    412: "precondition-failed",
    413: "content-too-large",
    415: "unsupported-media-type",
    422: "validation-failed",
    428: "precondition-required",
    429: "rate-limit-exceeded",
    500: "internal-error",
    503: "service-unavailable",
}
_PUBLIC_VALIDATION_CONTEXT_KEYS = frozenset(
    {
        "ge",
        "gt",
        "le",
        "lt",
        "max_length",
        "min_length",
        "multiple_of",
    }
)
_SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"
_MCP_MAX_RAW_ENVELOPE_BYTES = 1_048_576
_RECRUITING_DECISION_RESOURCE_TYPE = "recruiting_verification_decision"
_RECRUITING_DECISION_ACTION_STATES = {
    "review": "under_review",
    "activate": "active",
    "reject": "rejected",
    "expire": "expired",
    "suspend": "suspended",
    "revoke": "revoked",
    "restore": "active",
}
_RECRUITING_DECISION_RESOURCE_RE = re.compile(
    r"^recruiting_verification_decision:v1:"
    r"(?P<event_id>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}):"
    r"(?P<action>review|activate|reject|expire|suspend|revoke|restore):"
    r"(?P<digest>[0-9a-f]{64})$"
)
_VERIFICATION_REVIEW_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Vary": "Authorization",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}


class LifecycleReverificationDenied(Exception):
    """A Clerk step-up failure with Clerk's exact wire contract."""


class ConcurrentIdempotencyReplay(Exception):
    """Return the exact receipt committed by a concurrent matching request."""

    def __init__(self, response: Response) -> None:
        self.response = response


_LIFECYCLE_REVERIFICATION_ERROR = {
    "clerk_error": {
        "type": "forbidden",
        "reason": "reverification-error",
        "metadata": {"reverification": {"level": "second_factor", "afterMinutes": 10}},
    }
}


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": _ERROR_SCHEMA}},
    }


def _organization_membership_created_at(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _organization_admin_response(membership: OrganizationMembership) -> OrganizationAdminResponse:
    return OrganizationAdminResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        member_profile_handle=membership.member_profile_handle,
        role=cast(Any, membership.role),
        status=cast(Any, membership.status),
        created_at=_organization_membership_created_at(membership.created_at),
    )


def _organization_membership_generation_digest(membership: OrganizationMembership) -> str:
    facts = {
        "created_at": _organization_membership_created_at(membership.created_at)
        .isoformat()
        .replace("+00:00", "Z"),
        "id": membership.id,
        "invited_by_owner_id": membership.invited_by_owner_id,
        "member_owner_id": membership.member_owner_id,
        "member_profile_handle": membership.member_profile_handle,
        "organization_id": membership.organization_id,
        "role": membership.role,
    }
    return sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _contact_decision_receipt_digest(row: ContactRequest, action: str, response_body: str) -> str:
    def normalized_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return utc_value.isoformat().replace("+00:00", "Z")

    facts = {
        "action": action,
        "decided_at": normalized_datetime(row.decided_at),
        "decision_actor_digest": sha256((row.decision_actor_id or "").encode()).hexdigest(),
        "id": row.id,
        "origin": row.origin,
        "recipient_owner_id": row.recipient_owner_id,
        "report_reason_digest": sha256((row.report_reason or "").encode()).hexdigest(),
        "response_digest": sha256(response_body.encode()).hexdigest(),
        "sender_mandate_digest": sha256((row.sender_mandate_id or "").encode()).hexdigest(),
        "sender_owner_id": row.sender_owner_id,
        "status": row.status,
        "target_document_id": row.target_document_id,
    }
    return sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cursor_encode(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _cursor_decode(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="cursor is malformed") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="cursor is malformed")
    return payload


def _request_fingerprint(operation: str, body: str, conditional: str | None = None) -> str:
    value = f"{operation}\n{conditional or ''}\n{body}".encode()
    return sha256(value).hexdigest()


def _recruiting_decision_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return utc_value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _recruiting_decision_resource_parts(resource_id: str) -> dict[str, str]:
    match = _RECRUITING_DECISION_RESOURCE_RE.fullmatch(resource_id)
    if match is None:
        raise ValueError("invalid recruiting verification decision receipt")
    return match.groupdict()


def _recruiting_decision_receipt_digest(
    event: OrganizationVerificationEvent,
    verification: OrganizationVerification,
    *,
    action: str,
    owner_id: str,
    idempotency_key: str,
    operation: str,
    request_hash: str,
    response_status: int,
    response_body: str,
    response_headers: dict[str, str],
) -> str:
    if action not in _RECRUITING_DECISION_ACTION_STATES:
        raise ValueError("invalid recruiting verification decision action")
    facts = {
        "action": action,
        "event": {
            "actor_id": event.actor_id,
            "actor_role": event.actor_role,
            "expires_at": _recruiting_decision_datetime(event.expires_at),
            "id": event.id,
            "material_claim_digest": event.material_claim_digest,
            "occurred_at": _recruiting_decision_datetime(event.occurred_at),
            "organization_id": event.organization_id,
            "policy_version": event.policy_version,
            "purpose": event.purpose,
            "state": event.to_state,
            "verification_id": event.verification_id,
        },
        "idempotency_key": idempotency_key,
        "operation": operation,
        "owner_id": owner_id,
        "request_hash": request_hash,
        "response_body_sha256": sha256(response_body.encode("utf-8")).hexdigest(),
        "response_headers": response_headers,
        "response_status": response_status,
        "schema": "connect.md/recruiting-verification-decision-receipt",
        "schema_version": 1,
        "verification": {
            "created_at": _recruiting_decision_datetime(verification.created_at),
            "id": verification.id,
            "material_claim_digest": verification.material_claim_digest,
            "organization_id": verification.organization_id,
            "purpose": verification.purpose,
        },
    }
    return sha256(
        json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _recruiting_decision_resource_id(
    event: OrganizationVerificationEvent,
    verification: OrganizationVerification,
    *,
    action: str,
    owner_id: str,
    idempotency_key: str,
    operation: str,
    request_hash: str,
    response_status: int,
    response_body: str,
    response_headers: dict[str, str],
) -> str:
    digest = _recruiting_decision_receipt_digest(
        event,
        verification,
        action=action,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        operation=operation,
        request_hash=request_hash,
        response_status=response_status,
        response_body=response_body,
        response_headers=response_headers,
    )
    resource_id = f"recruiting_verification_decision:v1:{event.id}:{action}:{digest}"
    if len(resource_id) > 255:
        raise ValueError("recruiting verification decision receipt is too long")
    _recruiting_decision_resource_parts(resource_id)
    return resource_id


_APPLICATION_TRANSITION_ACTIONS = frozenset({"withdraw", "review", "accept", "reject"})
_APPLICATION_TRANSITION_ID_PATTERN = r"[A-Za-z0-9_-]{1,64}"
_APPLICATION_TRANSITION_RESOURCE_RE = re.compile(
    rf"^application_transition:v1:(?P<application_id>{_APPLICATION_TRANSITION_ID_PATTERN})"
    rf":(?P<job_id>{_APPLICATION_TRANSITION_ID_PATTERN})"
    rf":(?P<organization_id>{_APPLICATION_TRANSITION_ID_PATTERN})"
    rf":(?P<action>withdraw|review|accept|reject):(?P<digest>[0-9a-f]{{64}})$"
)


def _application_transition_resource_parts(resource_id: str) -> dict[str, str]:
    match = _APPLICATION_TRANSITION_RESOURCE_RE.fullmatch(resource_id)
    if match is None:
        raise ValueError("invalid application transition receipt resource")
    return match.groupdict()


def _application_transition_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


def _application_transition_receipt_digest(
    row: Application,
    job: Job,
    organization: Organization,
    action: str,
    response_body: str,
) -> str:
    facts = {
        "action": action,
        "application_id": row.id,
        "applicant_owner_digest": sha256(row.applicant_owner_id.encode()).hexdigest(),
        "created_at": _application_transition_datetime(row.created_at),
        "decided_at": _application_transition_datetime(row.decided_at),
        "decision_actor_digest": sha256((row.decision_actor_id or "").encode()).hexdigest(),
        "job_id": job.id,
        "job_slug": job.slug,
        "organization_id": organization.id,
        "organization_slug": organization.slug,
        "response_body": response_body,
        "retention_expires_at": _application_transition_datetime(row.retention_expires_at),
        "retention_policy_version": row.retention_policy_version,
        "snapshot_document_id": row.snapshot_document_id,
        "snapshot_document_identifier": row.snapshot_document_identifier,
        "snapshot_document_kind": row.snapshot_document_kind,
        "snapshot_document_version": row.snapshot_document_version,
        "snapshot_sha256": row.snapshot_sha256,
        "snapshot_storage_path_digest": sha256(
            (row.snapshot_storage_path or "").encode()
        ).hexdigest(),
        "status": row.status,
        "updated_at": _application_transition_datetime(row.updated_at),
    }
    return sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _application_transition_resource_id(
    row: Application,
    job: Job,
    organization: Organization,
    action: str,
    response_body: str,
) -> str:
    if action not in _APPLICATION_TRANSITION_ACTIONS:
        raise ValueError("invalid application transition action")
    resource_id = (
        f"application_transition:v1:{row.id}:{job.id}:{organization.id}:{action}:"
        f"{_application_transition_receipt_digest(row, job, organization, action, response_body)}"
    )
    if len(resource_id) > 255:
        raise ValueError("application transition receipt resource is too long")
    _application_transition_resource_parts(resource_id)
    return resource_id


_AGENT_GRANT_RECOVERY_ID_PATTERN = r"[A-Za-z0-9_-]{1,64}"
_AGENT_GRANT_RECOVERY_RESOURCE_RE = re.compile(
    rf"^agent_grant_recovery:v1:(?P<grant_id>{_AGENT_GRANT_RECOVERY_ID_PATTERN})"
    rf":(?P<digest>[0-9a-f]{{64}})$"
)


def _agent_grant_recovery_resource_parts(resource_id: str) -> dict[str, str]:
    match = _AGENT_GRANT_RECOVERY_RESOURCE_RE.fullmatch(resource_id)
    if match is None:
        raise ValueError("invalid agent-grant recovery receipt resource")
    return match.groupdict()


def _agent_grant_recovery_digest(
    row: AgentGrant,
    owner_id: str,
    normalized_scopes: list[str],
    response_body: str,
) -> str:
    facts = {
        "created_at": _application_transition_datetime(row.created_at),
        "expires_at": _application_transition_datetime(row.expires_at),
        "grant_id": row.id,
        "mode": row.mode,
        "owner_digest": sha256(owner_id.encode()).hexdigest(),
        "prefix": row.prefix,
        "resource_id": row.resource_id,
        "resource_type": row.resource_type,
        "response_body": response_body,
        "revoked": row.revoked,
        "scopes": sorted(set(normalized_scopes)),
    }
    return sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _agent_grant_recovery_resource_id(
    row: AgentGrant,
    owner_id: str,
    normalized_scopes: list[str],
    response_body: str,
) -> str:
    resource_id = (
        f"agent_grant_recovery:v1:{row.id}:"
        f"{_agent_grant_recovery_digest(row, owner_id, normalized_scopes, response_body)}"
    )
    if len(resource_id) > 255:
        raise ValueError("agent-grant recovery receipt resource is too long")
    _agent_grant_recovery_resource_parts(resource_id)
    return resource_id


_SOCIAL_RESOURCE_ID_PATTERN = r"[A-Za-z0-9_-]{1,64}"
_SOCIAL_RESOURCE_RE = re.compile(
    rf"^social_graph:v1:(?P<kind>follow|content_block):"
    rf"(?P<action>follow|unfollow|block|unblock):"
    rf"(?P<target_document_id>{_SOCIAL_RESOURCE_ID_PATTERN}):"
    rf"(?P<digest>[0-9a-f]{{64}})$"
)


def _social_resource_parts(resource_id: str) -> dict[str, str]:
    match = _SOCIAL_RESOURCE_RE.fullmatch(resource_id)
    if match is None:
        raise ValueError("invalid social receipt resource")
    parts = match.groupdict()
    if (parts["kind"] == "follow" and parts["action"] not in {"follow", "unfollow"}) or (
        parts["kind"] == "content_block" and parts["action"] not in {"block", "unblock"}
    ):
        raise ValueError("invalid social receipt action")
    return parts


def _social_row_fact(row: ProfileFollow | PostContentBlock) -> dict[str, str | None]:
    def owner_digest(value: str) -> str:
        return sha256(value.encode()).hexdigest()

    def normalized_datetime(value: datetime) -> str:
        utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return utc_value.isoformat().replace("+00:00", "Z")

    if isinstance(row, ProfileFollow):
        return {
            "kind": "follow",
            "id": row.id,
            "follower_owner_digest": owner_digest(row.follower_owner_id),
            "followed_owner_digest": owner_digest(row.followed_owner_id),
            "followed_profile_handle": row.followed_profile_handle,
            "created_at": normalized_datetime(row.created_at),
        }
    return {
        "kind": "content_block",
        "id": row.id,
        "blocker_owner_digest": owner_digest(row.blocker_owner_id),
        "blocked_owner_digest": owner_digest(row.blocked_owner_id),
        "created_at": normalized_datetime(row.created_at),
    }


def _social_receipt_digest(
    operation: str,
    actor_owner_id: str,
    target_owner_id: str,
    target_document_id: str,
    profile_handle: str,
    follows: list[ProfileFollow],
    blocks: list[PostContentBlock],
    response_body: str,
) -> str:
    facts = {
        "actor_owner_digest": sha256(actor_owner_id.encode()).hexdigest(),
        "blocks": sorted(
            (_social_row_fact(row) for row in blocks), key=lambda item: str(item["id"])
        ),
        "follows": sorted(
            (_social_row_fact(row) for row in follows), key=lambda item: str(item["id"])
        ),
        "operation": operation,
        "profile_handle": profile_handle,
        "response_digest": sha256(response_body.encode()).hexdigest(),
        "target_document_id": target_document_id,
        "target_owner_digest": sha256(target_owner_id.encode()).hexdigest(),
    }
    return sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _social_resource_id(
    kind: Literal["follow", "content_block"],
    action: Literal["follow", "unfollow", "block", "unblock"],
    target_document_id: str,
    digest: str,
) -> str:
    resource_id = f"social_graph:v1:{kind}:{action}:{target_document_id}:{digest}"
    if len(resource_id) > 255:
        raise ValueError("social receipt resource is too long")
    _social_resource_parts(resource_id)
    return resource_id


_AGENT_IDENTITY_RESOURCE_ID_PATTERN = r"[A-Za-z0-9_-]{1,64}"
_AGENT_IDENTITY_RESOURCE_RE = re.compile(
    rf"^agent_identity:v1:(?P<action>create|withdraw):"
    rf"(?P<identity_id>{_AGENT_IDENTITY_RESOURCE_ID_PATTERN}):"
    rf"(?P<profile_id>{_AGENT_IDENTITY_RESOURCE_ID_PATTERN}):"
    rf"(?P<digest>[0-9a-f]{{64}})$"
)


def _agent_identity_resource_parts(resource_id: str) -> dict[str, str]:
    match = _AGENT_IDENTITY_RESOURCE_RE.fullmatch(resource_id)
    if match is None:
        raise ValueError("invalid agent identity receipt resource")
    return match.groupdict()


def _agent_identity_receipt_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


def _agent_identity_receipt_digest(
    identity: AgentIdentity,
    profile: Document,
    action: Literal["create", "withdraw"],
    response_body: str,
) -> str:
    facts = {
        "action": action,
        "description": identity.description,
        "display_name": identity.display_name,
        "identity_created_at": _agent_identity_receipt_datetime(identity.created_at),
        "identity_id": identity.id,
        "identity_owner_digest": sha256(identity.owner_id.encode()).hexdigest(),
        "identity_status": identity.status,
        "identity_updated_at": _agent_identity_receipt_datetime(identity.updated_at),
        "identity_withdrawn_at": _agent_identity_receipt_datetime(identity.withdrawn_at),
        "profile_current_version": profile.current_version,
        "profile_document_id": profile.id,
        "profile_handle": profile.public_identifier,
        "profile_kind": profile.kind,
        "profile_owner_digest": sha256(profile.owner_id.encode()).hexdigest(),
        "profile_updated_at": _agent_identity_receipt_datetime(profile.updated_at),
        "profile_visibility": profile.visibility,
        "response_digest": sha256(response_body.encode()).hexdigest(),
    }
    return sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _agent_identity_resource_id(
    identity: AgentIdentity,
    profile: Document,
    action: Literal["create", "withdraw"],
    response_body: str,
) -> str:
    digest = _agent_identity_receipt_digest(identity, profile, action, response_body)
    resource_id = f"agent_identity:v1:{action}:{identity.id}:{profile.id}:{digest}"
    if len(resource_id) > 255:
        raise ValueError("agent identity receipt resource is too long")
    _agent_identity_resource_parts(resource_id)
    return resource_id


_AGENT_OUTREACH_RECEIPT_RE = re.compile(
    r"^(?P<request_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):"
    r"(?P<mandate_digest>[0-9a-f]{64}):"
    r"(?P<source_identity_digest>[0-9a-f]{64}):"
    r"(?P<grant_digest>[0-9a-f]{64})$"
)


def _agent_outreach_receipt_parts(resource_id: str) -> dict[str, str]:
    match = _AGENT_OUTREACH_RECEIPT_RE.fullmatch(resource_id)
    if match is None:
        raise ValueError("invalid agent outreach receipt resource")
    return match.groupdict()


def _agent_outreach_receipt_resource_id(
    request_id: str,
    *,
    mandate_id: str,
    source_identity_handle: str,
    grant_id: str,
) -> str:
    try:
        normalized_request_id = str(UUID(request_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid agent outreach request id") from exc
    resource_id = ":".join(
        (
            normalized_request_id,
            sha256(mandate_id.encode("utf-8")).hexdigest(),
            sha256(source_identity_handle.encode("utf-8")).hexdigest(),
            sha256(grant_id.encode("utf-8")).hexdigest(),
        )
    )
    if len(resource_id) > 255:
        raise ValueError("agent outreach receipt resource is too long")
    _agent_outreach_receipt_parts(resource_id)
    return resource_id


def _social_openapi_extra() -> dict[str, Any]:
    return {
        "x-connectmd-human-only": True,
        "parameters": [_idempotency_openapi_parameter()],
    }


def _idempotency_openapi_parameter() -> dict[str, Any]:
    return {
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "description": "A 1-128 character visible-ASCII key for this logical request.",
        "schema": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": _IDEMPOTENCY_KEY_PATTERN,
        },
    }


def _mutation_openapi_extra(*, if_match: bool = False, human_only: bool = False) -> dict[str, Any]:
    parameters = [_idempotency_openapi_parameter()]
    if if_match:
        parameters.append(
            {
                "name": "If-Match",
                "in": "header",
                "required": True,
                "description": "Require the exact current strong ETag.",
                "schema": {"type": "string", "pattern": STRONG_DOCUMENT_ETAG_PATTERN},
            }
        )
    extra: dict[str, Any] = {"parameters": parameters}
    if human_only:
        extra["x-connectmd-human-only"] = True
    return extra


def _agent_identity_openapi_extra() -> dict[str, Any]:
    return {
        "x-connectmd-human-only": True,
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "description": "A 1-128 character visible-ASCII key for this logical request.",
                "schema": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": _IDEMPOTENCY_KEY_PATTERN,
                },
            }
        ],
    }


_DOCUMENT_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "JSON by default; canonical Markdown when requested with Accept: text/markdown.",
        "content": {MARKDOWN_MEDIA_TYPE: {"schema": {"type": "string"}}},
    },
    404: _error_response("The document is absent or not visible to this caller."),
    401: _error_response("A supplied Bearer credential is invalid."),
    403: _error_response("The supplied agent key lacks document-read scope."),
    503: _error_response("Canonical storage is unavailable or failed integrity verification."),
}
_MARKDOWN_ONLY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Canonical UTF-8/LF Markdown bytes.",
        "content": {MARKDOWN_MEDIA_TYPE: {"schema": {"type": "string"}}},
    },
    404: _error_response("The document is absent or not visible to this caller."),
    401: _error_response("A supplied Bearer credential is invalid."),
    403: _error_response("The supplied agent key lacks document-read scope."),
    503: _error_response("Canonical storage is unavailable or failed integrity verification."),
}
_POST_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "JSON by default; canonical Markdown when requested with Accept: text/markdown.",
        "content": {MARKDOWN_MEDIA_TYPE: {"schema": {"type": "string"}}},
    },
    401: _error_response("A supplied Bearer credential is invalid."),
    404: _error_response("The public post is absent or not visible to this caller."),
    503: _error_response("Canonical storage is unavailable or failed integrity verification."),
}
_POST_MARKDOWN_ONLY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Canonical UTF-8/LF Markdown bytes.",
        "content": {MARKDOWN_MEDIA_TYPE: {"schema": {"type": "string"}}},
    },
    401: _error_response("A supplied Bearer credential is invalid."),
    404: _error_response("The public post is absent or not visible to this caller."),
    503: _error_response("Canonical storage is unavailable or failed integrity verification."),
}


def _document_openapi(kind: str, *, update: bool = False) -> dict[str, Any]:
    canonical_limit = canonical_document_max_utf8_bytes()
    schema = "connect.md/profile" if kind == "profile" else "connect.md/resume"
    identifier = "handle" if kind == "profile" else "slug"
    headings = (
        "## About\n\n...\n\n## Experience"
        if kind == "profile"
        else "## Summary\n\n...\n\n## Experience\n\n...\n\n## Education"
    )
    title = "title: Example role\n" if kind == "resume" else ""
    markdown = (
        f"---\nschema: {schema}\nschema_version: 1\n{identifier}: example\nname: Example Person\n"
        f"{title}headline: Example headline\nlocation: Singapore\nskills: [Python]\nvisibility: private\n---\n"
        f"# Example Person\n\n{headings}\n\n...\n\n## Skills\n\n- Python\n"
    )
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["markdown"],
                        "properties": {
                            "markdown": {
                                "type": "string",
                                "minLength": 1,
                                "description": (
                                    "Client Markdown is canonicalized before validation; "
                                    f"the final Profile/Resume document must be at most {canonical_limit} "
                                    "UTF-8 bytes after LF normalization."
                                ),
                                "x-connectmd-canonical-max-utf8-bytes": canonical_limit,
                            }
                        },
                    },
                    "example": {"markdown": markdown},
                },
                MARKDOWN_MEDIA_TYPE: {
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Final canonical Profile/Resume Markdown is measured after LF "
                            f"normalization and must be at most {canonical_limit} UTF-8 bytes."
                        ),
                        "x-connectmd-canonical-max-utf8-bytes": canonical_limit,
                    },
                    "example": markdown,
                },
            },
        },
        "responses": {
            str(200 if update else 201): {
                "description": "Canonical document saved. Search projection delay does not roll back the save.",
                "headers": {
                    "ETag": {
                        "description": "Strong validator for the canonical Markdown bytes.",
                        "schema": {"type": "string"},
                    },
                    "Idempotency-Replayed": {
                        "description": "True when a durable prior result was replayed.",
                        "schema": {"type": "string", "enum": ["true"]},
                    },
                    "X-Connectmd-Search": {
                        "description": "Set to 'queued' when the canonical save succeeded and its search projection was queued.",
                        "schema": {"type": "string", "enum": ["queued"]},
                    },
                },
            },
            "403": _error_response("The supplied agent key lacks document-write scope."),
            "428": _error_response(
                "Idempotency-Key is required, and updates also require If-Match."
            ),
            **(
                {
                    "404": _error_response("The update target was not found."),
                    "409": _error_response("The supplied canonical version is stale."),
                    "412": _error_response("If-Match does not match the current strong ETag."),
                }
                if update
                else {"409": _error_response("The public identifier is already in use.")}
            ),
            "413": _error_response(
                f"Canonical Profile/Resume Markdown exceeds {canonical_limit} UTF-8 bytes."
            ),
            "415": _error_response("The request must be JSON or Markdown text."),
            "422": _error_response("Strict Markdown contract validation failed."),
            "503": _error_response(
                "Canonical storage or the authentication verifier is unavailable."
            ),
        },
        "parameters": [
            _idempotency_openapi_parameter(),
            *(
                [
                    {
                        "name": "If-Match",
                        "in": "header",
                        "required": True,
                        "description": "Required strong ETag from the latest read.",
                        "schema": {
                            "type": "string",
                            "pattern": STRONG_DOCUMENT_ETAG_PATTERN,
                        },
                    }
                ]
                if update
                else []
            ),
        ],
    }


def _post_openapi() -> dict[str, Any]:
    markdown = (
        "---\nschema: connect.md/post\nschema_version: 1\n"
        "title: Example professional note\ntopics: [engineering]\nvisibility: public\n---\n"
        "# Example professional note\n\nA bounded public professional post.\n"
    )
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["markdown"],
                        "properties": {"markdown": {"type": "string", "minLength": 1}},
                    },
                    "example": {"markdown": markdown},
                },
                MARKDOWN_MEDIA_TYPE: {
                    "schema": {"type": "string", "minLength": 1},
                    "example": markdown,
                },
            },
        },
        "parameters": [_idempotency_openapi_parameter()],
        "responses": {
            "201": {
                "description": "Immutable public professional post published.",
                "headers": {
                    "ETag": {"schema": {"type": "string"}},
                    "Content-Digest": {"schema": {"type": "string"}},
                    "Idempotency-Replayed": {"schema": {"type": "string", "enum": ["true"]}},
                },
            },
            "409": _error_response("A currently public author profile is required."),
            "413": _error_response("Canonical post Markdown exceeds 10 KiB."),
            "422": _error_response("Strict post Markdown validation failed."),
            "428": _error_response("Idempotency-Key is required."),
            "429": _error_response("The post daily limit was reached."),
        },
    }


def _quality(value: str) -> float:
    for parameter in value.split(";")[1:]:
        name, _, raw = parameter.strip().partition("=")
        if name.lower() == "q":
            try:
                return min(1.0, max(0.0, float(raw)))
            except ValueError:
                return 0.0
    return 1.0


def _prefers_markdown(accept: str) -> bool:
    """Honor explicit Markdown media ranges and q-values; JSON remains the default."""
    ranges = [(item.split(";", 1)[0].strip().lower(), _quality(item)) for item in accept.split(",")]
    markdown = max((q for media, q in ranges if media == MARKDOWN_MEDIA_TYPE), default=0.0)
    if markdown <= 0:
        return False
    json_quality = max(
        (q for media, q in ranges if media in {"application/json", "application/*", "*/*"}),
        default=-1.0,
    )
    return markdown >= json_quality


@asynccontextmanager
async def _lifespan(app: FastAPI):
    reconciliation_task: asyncio.Task[None] | None = None
    try:
        if app.state.settings.is_production:
            async with app.state.session_factory() as session:
                await require_current_database_schema(session)
        journal: DeletionCommitmentJournal | None = app.state.deletion_journal
        if journal is not None:
            commitments = journal.verify()
            if commitments and not app.state.settings.account_lifecycle_enabled:
                raise DeletionJournalError(
                    "account lifecycle cannot be disabled while deletion commitments exist"
                )
            async with app.state.session_factory() as session:
                await verify_live_deletion_mirror(session, journal)
            app.state.deletion_journal_consistent = True
        reconciler: ArtifactReconciler = app.state.artifact_reconciler
        if reconciler.enabled:
            with anyio.move_on_after(5) as bounded_startup:
                await reconciler.run_once()
            if bounded_startup.cancel_called:
                reconciler.status = "unavailable"
            reconciliation_task = asyncio.create_task(reconciler.run_forever())
        yield
    finally:
        if reconciliation_task is not None:
            reconciliation_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconciliation_task
        await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.require_api_runtime_configuration()
    app = FastAPI(
        title="connect.md API",
        version="0.3.0",
        description=(
            "Markdown-native profiles, resumes, and human-only professional posts with "
            "organization-owned JSON jobs."
            if settings.recruiting_enabled
            else "Markdown-native profiles, resumes, and human-only professional posts."
        ),
        lifespan=_lifespan,
        openapi_tags=[
            {"name": "documents", "description": "Canonical Markdown profile and resume APIs."},
            {"name": "posts", "description": "Immutable public professional-post Markdown APIs."},
            {
                "name": "follows",
                "description": "Private human-only follows, content blocks, and chronological feed.",
            },
            {
                "name": "moderation",
                "description": "Private human case status and appeals for professional posts.",
            },
            {"name": "ingestion", "description": "Unpublished, bounded local-file conversion."},
            {"name": "agent-keys", "description": "Owner-bound opaque agent API keys."},
            {"name": "agent-grants", "description": "Named, expiring, resource-bound grants."},
            {
                "name": "agent-identities",
                "description": "Public active agent identities linked to current public profiles.",
            },
            {"name": "contacts", "description": "Consent-gated internal agent requests."},
            *(
                [
                    {
                        "name": "organizations",
                        "description": "Owner-attested organizations and membership authority.",
                    },
                    {
                        "name": "jobs",
                        "description": "Verified-organization job lifecycle and public search.",
                    },
                    {
                        "name": "applications",
                        "description": "Human-confirmed private job applications.",
                    },
                ]
                if settings.recruiting_enabled
                else []
            ),
            {
                "name": "connections",
                "description": "Private human-only bilateral connection graph.",
            },
            {
                "name": "conversations",
                "description": "Admitted private human conversations and Markdown messages.",
            },
            {
                "name": "notifications",
                "description": "Recipient-private metadata-only notifications.",
            },
            {"name": "protocols", "description": "Discovery, change feed, A2A, and MCP."},
            {
                "name": "taxonomy",
                "description": "Anonymous current-public-v2 taxonomy discovery for search inputs.",
            },
        ],
    )
    request_logger = configure_request_logger()
    app.state.request_logger = request_logger

    def internal_error_headers(request: Request, request_id: str) -> dict[str, str]:
        headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
        if request.url.path.startswith("/v1/"):
            headers["Vary"] = "Authorization"
        if request.url.path.startswith("/v1/internal/recruiting-verifications"):
            headers["Pragma"] = "no-cache"
            headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return headers

    @app.exception_handler(Exception)
    async def unhandled_exception_problem(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        started_at = getattr(request.state, "request_log_started_at", None)
        duration_ms = (
            (monotonic() - started_at) * 1000 if isinstance(started_at, int | float) else 0.0
        )
        if not getattr(request.state, "request_log_emitted", False):
            emit_request_log(
                request_logger,
                request,
                request_id=request_id,
                status=500,
                duration_ms=duration_ms,
                exception=exc,
            )
            request.state.request_log_emitted = True
        return JSONResponse(
            {
                "type": "https://connect.md/problems/internal-error",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "an unexpected server error occurred",
                "instance": f"urn:connect.md:request:{request_id}",
                "request_id": request_id,
            },
            status_code=500,
            media_type="application/problem+json",
            headers=internal_error_headers(request, request_id),
        )

    @app.exception_handler(LifecycleReverificationDenied)
    async def lifecycle_reverification_problem(
        request: Request, _exc: LifecycleReverificationDenied
    ) -> JSONResponse:
        return JSONResponse(_LIFECYCLE_REVERIFICATION_ERROR, status_code=403)

    @app.exception_handler(ConcurrentIdempotencyReplay)
    async def concurrent_idempotency_replay(
        _request: Request, exc: ConcurrentIdempotencyReplay
    ) -> Response:
        return exc.response

    @app.exception_handler(TaxonomyUnavailable)
    async def taxonomy_unavailable_problem(
        request: Request, _exc: TaxonomyUnavailable
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        return JSONResponse(
            {
                "type": "https://connect.md/problems/service-unavailable",
                "title": "Service Unavailable",
                "status": 503,
                "detail": "public taxonomy projection is unavailable",
                "instance": request.url.path,
                "request_id": request_id,
            },
            status_code=503,
            media_type="application/problem+json",
        )

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, exc: HTTPException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        status_code = exc.status_code
        payload: dict[str, Any] = {
            "type": f"https://connect.md/problems/{_PROBLEM_SLUGS.get(status_code, 'http-error')}",
            "title": _PROBLEM_TITLES.get(status_code, "HTTP Error"),
            "status": status_code,
            # Structured legacy details remain an extension for ingestion callers.
            "detail": exc.detail,
            "instance": request.url.path,
            "request_id": request_id,
        }
        return JSONResponse(
            payload,
            status_code=status_code,
            media_type="application/problem+json",
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        errors: list[dict[str, Any]] = []
        for raw_error in exc.errors():
            error = {key: raw_error[key] for key in ("type", "loc", "msg") if key in raw_error}
            context = raw_error.get("ctx")
            if isinstance(context, dict):
                safe_context = {
                    key: value
                    for key, value in context.items()
                    if key in _PUBLIC_VALIDATION_CONTEXT_KEYS
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                }
                if safe_context:
                    error["ctx"] = safe_context
            errors.append(error)
        return JSONResponse(
            {
                "type": "https://connect.md/problems/validation-failed",
                "title": "Unprocessable Content",
                "status": 422,
                "detail": "request validation failed",
                "instance": request.url.path,
                "request_id": request_id,
                "errors": errors,
            },
            status_code=422,
            media_type="application/problem+json",
        )

    app.state.settings = settings
    app.state.store = VersionStore(settings.storage_path)
    app.state.clerk = ClerkVerifier(settings)
    app.state.api_keys = ApiKeyManager(settings)
    app.state.agent_grants = AgentGrantManager(settings)
    app.state.search = MeiliSearchProjection(settings)
    app.state.deletion_journal = (
        DeletionCommitmentJournal(settings) if settings.deletion_journal_path is not None else None
    )
    if app.state.deletion_journal is not None:
        commitments = app.state.deletion_journal.verify()
        if commitments and not settings.account_lifecycle_enabled:
            raise DeletionJournalError(
                "account lifecycle cannot be disabled while deletion commitments exist"
            )
    app.state.deletion_journal_consistent = True
    app.state.artifact_reconciler = None
    app.state.ingest_limiter = anyio.CapacityLimiter(settings.max_ingest_concurrency)
    # Production requires the API-key pepper. The deterministic fallback keeps
    # development cursors continuous across process restarts without claiming
    # secret-backed integrity when no configured secret exists.
    cursor_key_material = settings.api_key_pepper or (
        f"development:{settings.database_url}:{settings.storage_path}"
    )
    app.state.agent_directory_cursor_secret = hmac_new(
        cursor_key_material.encode("utf-8"),
        b"connect.md:agent-directory-cursor:v1",
        sha256,
    ).digest()
    app.state.employer_inventory_cursor_secret = hmac_new(
        cursor_key_material.encode("utf-8"),
        b"connect.md:employer-inventory-cursor:v1",
        sha256,
    ).digest()
    app.state.taxonomy_cursor_secret = hmac_new(
        cursor_key_material.encode("utf-8"),
        b"connect.md:taxonomy-cursor:v1",
        sha256,
    ).digest()
    app.state.generic_cursor_secret = hmac_new(
        cursor_key_material.encode("utf-8"),
        b"connect.md:generic-cursor:v1",
        sha256,
    ).digest()
    app.state.generic_cursor_codec = CursorCodec(app.state.generic_cursor_secret)
    app.state.taxonomy = TaxonomyService(app.state.taxonomy_cursor_secret)
    app.state.exact_search = ExactSearchService(settings)

    def cursor_principal_bindings(principal: Principal) -> tuple[str, ...]:
        return (
            principal.subject,
            principal.method,
            principal.actor_id or "",
            principal.grant_id or "",
            principal.mandate_id or "",
            principal.resource_type or "",
            principal.resource_id or "",
            ",".join(sorted(principal.scopes)),
        )

    def generic_cursor_encode(
        payload: dict[str, Any], *, scope: str | None = None, bindings: tuple[str, ...] = ()
    ) -> str:
        resolved_scope = scope if scope is not None else payload.get("scope")
        if not isinstance(resolved_scope, str):
            raise CursorError("cursor scope is malformed")
        return app.state.generic_cursor_codec.encode(
            payload, scope=resolved_scope, bindings=bindings
        )

    def generic_cursor_decode(
        cursor: str,
        *,
        scope: str,
        bindings: tuple[str, ...] = (),
        detail: str,
    ) -> dict[str, Any]:
        try:
            return app.state.generic_cursor_codec.decode(cursor, scope=scope, bindings=bindings)
        except CursorError as exc:
            raise HTTPException(status_code=400, detail=detail) from exc

    def persistent_authority_detail(method: str, path: str) -> str | None:
        if path == "/v1/api-keys":
            if method in {"GET", "POST"}:
                return "only an authenticated Clerk user can manage agent API keys"
        elif method == "DELETE" and re.fullmatch(r"/v1/api-keys/[^/]+", path):
            return "only an authenticated Clerk user can manage agent API keys"
        if path == "/v1/agent-grants":
            if method == "POST":
                return "only an authenticated Clerk user can create agent grants"
            if method == "GET":
                return "only an authenticated Clerk user can list agent grants"
        elif method == "DELETE" and re.fullmatch(r"/v1/agent-grants/[^/]+", path):
            return "only an authenticated Clerk user can revoke agent grants"
        if re.fullmatch(r"/v1/agent-identities/[^/]+/mandates", path):
            if method == "POST":
                return "only an authenticated Clerk user can issue an agent mandate"
            if method == "GET":
                return "only an authenticated Clerk user can list agent mandates"
        elif method == "DELETE" and re.fullmatch(
            r"/v1/agent-identities/[^/]+/mandates/[^/]+", path
        ):
            return "only an authenticated Clerk user can revoke an agent mandate"
        return None

    async def persistent_authority_principal(request: Request) -> Principal:
        async with request.app.state.session_factory() as session:
            provider: Any = request.app.dependency_overrides.get(require_principal)
            if provider is not None:
                provider_parameters = signature(provider).parameters
                provider_kwargs: dict[str, Any] = {}
                if "request" in provider_parameters:
                    provider_kwargs["request"] = request
                if "session" in provider_parameters:
                    provider_kwargs["session"] = session
                result = provider(**provider_kwargs)
                if isawaitable(result):
                    result = await result
                return cast(Principal, result)
            optional_provider: Any = request.app.dependency_overrides.get(
                optional_principal, optional_principal
            )
            provider_parameters = signature(optional_provider).parameters
            provider_kwargs = {}
            if "request" in provider_parameters:
                provider_kwargs["request"] = request
            if "session" in provider_parameters:
                provider_kwargs["session"] = session
            result = optional_provider(**provider_kwargs)
            if isawaitable(result):
                result = await result
            if result is None:
                raise HTTPException(
                    status_code=401,
                    detail="authentication is required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return cast(Principal, result)

    @app.middleware("http")
    async def request_id(request: Request, call_next: Any) -> Response:
        request.state.request_log_started_at = monotonic()
        request.state.request_log_emitted = False
        supplied = request.headers.get("X-Request-ID", "")
        identifier = supplied if _REQUEST_ID_RE.fullmatch(supplied) else str(uuid4())
        request.state.request_id = identifier
        authority_detail = persistent_authority_detail(request.method, request.url.path)
        if not request.app.state.deletion_journal_consistent and request.url.path not in {
            "/healthz",
            "/readyz",
        }:
            response: Response = JSONResponse(
                {
                    "type": "https://connect.md/problems/service-unavailable",
                    "title": "Service Unavailable",
                    "status": 503,
                    "detail": "deletion commitment reconciliation is required",
                    "instance": request.url.path,
                    "request_id": identifier,
                },
                status_code=503,
                media_type="application/problem+json",
                headers={"X-Request-ID": identifier},
            )
        elif authority_detail is not None:
            try:
                authority_principal = await persistent_authority_principal(request)
                request.state.persistent_authority_principal = authority_principal
                if authority_principal.method != "clerk_jwt" or authority_principal.is_impersonated:
                    raise HTTPException(status_code=403, detail=authority_detail)
                response = await call_next(request)
            except HTTPException as exc:
                response = await http_problem(request, exc)
            except Exception as exc:
                response = await unhandled_exception_problem(request, exc)
        else:
            try:
                response = await call_next(request)
            except Exception as exc:
                response = await unhandled_exception_problem(request, exc)
        response.headers["X-Request-ID"] = identifier
        if request.url.path.startswith("/v1/"):
            recruiting_review_path = request.url.path.startswith(
                "/v1/internal/recruiting-verifications"
            )
            private_no_store = (
                request.url.path == "/v1/account/lifecycle-status" or recruiting_review_path
            )
            response.headers["Cache-Control"] = (
                "no-store, private" if private_no_store else "no-store"
            )
            if recruiting_review_path:
                response.headers.setdefault("Pragma", "no-cache")
                response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
            vary = {
                item.strip() for item in response.headers.get("Vary", "").split(",") if item.strip()
            }
            vary.add("Authorization")
            response.headers["Vary"] = ", ".join(sorted(vary))
        if not request.state.request_log_emitted:
            emit_request_log(
                request_logger,
                request,
                request_id=identifier,
                status=response.status_code,
                duration_ms=(monotonic() - request.state.request_log_started_at) * 1000,
            )
            request.state.request_log_emitted = True
        return response

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "If-Match",
                "MCP-Protocol-Version",
                "X-Connectmd-Purpose",
                "X-Request-ID",
            ],
            expose_headers=[
                "ETag",
                "Last-Modified",
                "Content-Digest",
                "Content-Disposition",
                "Idempotency-Replayed",
                "MCP-Protocol-Version",
                "X-Request-ID",
                "X-Connectmd-Search",
            ],
            max_age=600,
        )

    app.include_router(health_router)

    app.include_router(discovery_router, include_in_schema=True)
    app.include_router(schemas_router)

    async def request_markdown(request: Request) -> str:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        body = await request.body()
        if not body:
            raise HTTPException(status_code=422, detail="a Markdown request body is required")
        if len(body) > request.app.state.settings.max_upload_bytes:
            raise HTTPException(
                status_code=413, detail="request body exceeds the configured size limit"
            )
        if content_type == "application/json":
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=422, detail="request JSON is malformed") from exc
            markdown = payload.get("markdown") if isinstance(payload, dict) else None
            if not isinstance(markdown, str):
                raise HTTPException(
                    status_code=422, detail="JSON body must contain string field 'markdown'"
                )
            if set(payload) != {"markdown"}:
                raise HTTPException(
                    status_code=422, detail="JSON body may contain only string field 'markdown'"
                )
            return markdown
        if content_type in {MARKDOWN_MEDIA_TYPE, "text/plain", ""}:
            try:
                return body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=422, detail="Markdown must be UTF-8") from exc
        raise HTTPException(status_code=415, detail="use application/json or text/markdown")

    def service(session: AsyncSession, request: Request) -> DocumentService:
        return DocumentService(
            session,
            request.app.state.store,
            settings,
            request.app.state.artifact_reconciler,
        )

    def assert_scope(principal: Principal, scope: str) -> None:
        if principal.method in {"agent_api_key", "agent_grant"} and scope not in principal.scopes:
            raise HTTPException(
                status_code=403, detail=f"agent credential lacks required scope '{scope}'"
            )

    def require_non_impersonated_clerk_human(
        detail: str,
    ) -> Callable[..., Coroutine[Any, Any, Principal]]:
        """Build a route dependency for persistent delegated-authority management."""

        async def dependency(
            request: Request,
        ) -> Principal:
            principal = getattr(request.state, "persistent_authority_principal", None)
            if not isinstance(principal, Principal):
                raise HTTPException(status_code=401, detail="authentication is required")
            if principal.method != "clerk_jwt" or principal.is_impersonated:
                raise HTTPException(status_code=403, detail=detail)
            return principal

        return dependency

    def assert_direct(principal: Principal) -> None:
        if principal.method == "agent_grant" and principal.grant_mode != "direct":
            raise HTTPException(
                status_code=403,
                detail="this proposal-only agent grant cannot perform direct mutations",
            )

    def assert_not_impersonated_clerk(principal: Principal) -> None:
        if principal.method == "clerk_jwt" and principal.is_impersonated:
            raise HTTPException(status_code=403, detail=IMPERSONATION_READ_ONLY_CODE)

    def assert_not_mandate_credential(principal: Principal) -> None:
        if principal.method == "agent_grant" and principal.mandate_id is not None:
            raise HTTPException(
                status_code=403,
                detail="mandate-bound grants are limited to agent outreach",
            )

    def assert_document_resource(principal: Principal, document: Document) -> None:
        if principal.method != "agent_grant" or principal.resource_type == "owner":
            return
        if principal.resource_type == "document" and principal.resource_id == document.id:
            return
        raise HTTPException(status_code=404, detail="document was not found")

    def assert_agent_grant_resource_domain(
        principal: Principal, allowed_resource_types: frozenset[str]
    ) -> None:
        if (
            principal.method == "agent_grant"
            and principal.resource_type not in allowed_resource_types
        ):
            raise HTTPException(
                status_code=403,
                detail="agent grant resource is not authorized for this operation",
            )

    def idempotency_key(request: Request, *, required: bool = False) -> str | None:
        key = request.headers.get("Idempotency-Key")
        if key is None:
            if required:
                raise HTTPException(
                    status_code=428,
                    detail="Idempotency-Key is required for this operation",
                )
            return None
        if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must contain 1-128 visible ASCII characters",
            )
        return key

    def idempotency_replay_json(model: BaseModel) -> str:
        """Serialize live replay state with the same UTC timestamp shape as the initial receipt."""

        def normalize(value: Any) -> Any:
            if isinstance(value, datetime):
                utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
                return utc_value.isoformat().replace("+00:00", "Z")
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        return json.dumps(normalize(model.model_dump(mode="python")), separators=(",", ":"))

    async def agent_identity_replay(
        session: AsyncSession,
        principal: Principal,
        record: IdempotencyRecord,
        operation: str,
    ) -> Response:
        def unavailable(exc: BaseException | None = None) -> NoReturn:
            error = HTTPException(
                status_code=503,
                detail="idempotent agent identity receipt cannot be reconstructed",
            )
            if exc is None:
                raise error
            raise error from exc

        if operation == "POST:/v1/agent-identities":
            action: Literal["create", "withdraw"] = "create"
            expected_status = 201
            expected_handle: str | None = None
        elif operation.startswith("DELETE:/v1/agent-identities/"):
            action = "withdraw"
            expected_status = 204
            expected_handle = operation.removeprefix("DELETE:/v1/agent-identities/")
            if not expected_handle or "/" in expected_handle:
                unavailable()
        else:
            unavailable()
        if (
            record.resource_type != "agent_identity"
            or record.response_status != expected_status
            or record.response_headers != "{}"
            or (action == "withdraw" and record.response_body != "")
            or (action == "create" and not record.response_body)
            or not record.resource_id
        ):
            unavailable()
        try:
            parts = _agent_identity_resource_parts(record.resource_id)
        except (TypeError, ValueError) as exc:
            unavailable(exc)
        if parts["action"] != action:
            unavailable()
        identity = await session.scalar(
            select(AgentIdentity)
            .where(
                AgentIdentity.id == parts["identity_id"],
                AgentIdentity.owner_id == principal.subject,
            )
            .with_for_update()
        )
        if identity is None:
            unavailable()
        profile = await session.scalar(
            select(Document).where(Document.id == parts["profile_id"]).with_for_update()
        )
        if profile is None:
            unavailable()
        if (
            identity.profile_document_id != profile.id
            or identity.owner_id != principal.subject
            or profile.owner_id != principal.subject
            or profile.kind != "profile"
        ):
            unavailable()
        if action == "create":
            if (
                identity.status != "active"
                or identity.withdrawn_at is not None
                or profile.visibility != "public"
            ):
                unavailable()
        else:
            if (
                expected_handle != identity.handle
                or identity.status != "withdrawn"
                or identity.withdrawn_at is None
            ):
                unavailable()
        try:
            if action == "create":
                recovered = agent_identity_response(identity, profile)
                expected_body = idempotency_replay_json(recovered)
                if expected_body != record.response_body:
                    unavailable()
            else:
                expected_body = ""
            expected_digest = _agent_identity_receipt_digest(
                identity, profile, action, expected_body
            )
        except (TypeError, ValueError, ValidationError) as exc:
            unavailable(exc)
        if not compare_digest(parts["digest"], expected_digest):
            unavailable()
        if action == "create":
            return Response(
                content=expected_body,
                status_code=201,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        return Response(status_code=204, headers={"Idempotency-Replayed": "true"})

    async def application_transition_replay(
        session: AsyncSession,
        request: Request,
        principal: Principal,
        record: IdempotencyRecord,
        context: dict[str, str],
    ) -> Response:
        require_application_human(principal)

        def unavailable(exc: BaseException | None = None) -> NoReturn:
            error = HTTPException(
                status_code=503,
                detail="idempotent application transition receipt cannot be reconstructed",
            )
            if exc is None:
                raise error
            raise error from exc

        if (
            record.resource_type != "application_transition"
            or record.response_status != 200
            or record.response_body != ""
            or record.response_headers != "{}"
            or not record.resource_id
        ):
            unavailable()
        try:
            parts = _application_transition_resource_parts(record.resource_id)
        except (TypeError, ValueError) as exc:
            unavailable(exc)
        mode = context.get("mode")
        action = context.get("action")
        if mode not in {"employer", "applicant"} or action not in _APPLICATION_TRANSITION_ACTIONS:
            unavailable()
        if parts["action"] != action:
            unavailable()
        for field in ("application_id", "job_id", "organization_id"):
            expected = context.get(field)
            if expected is not None and expected != parts[field]:
                unavailable()

        organization = await session.scalar(
            select(Organization)
            .where(Organization.id == parts["organization_id"])
            .with_for_update()
        )
        if organization is None:
            unavailable()
        job = await session.scalar(
            select(Job)
            .where(Job.id == parts["job_id"], Job.organization_id == organization.id)
            .with_for_update()
        )
        if job is None:
            unavailable()
        if mode == "employer":
            try:
                await assert_active_employer_application_authority(session, organization, principal)
            except HTTPException as exc:
                # Authority loss is deliberately not replayable, even when the
                # receipt and resulting row are otherwise intact.
                raise exc
        row = await session.scalar(
            select(Application)
            .where(
                Application.id == parts["application_id"],
                Application.job_id == job.id,
            )
            .with_for_update()
        )
        if row is None:
            unavailable()
        if mode == "applicant" and row.applicant_owner_id != principal.subject:
            unavailable()
        if retention_expired(row.retention_expires_at):
            unavailable()
        expected_status = {
            "withdraw": "withdrawn",
            "review": "under_review",
            "accept": "accepted",
            "reject": "rejected",
        }[action]
        if row.status != expected_status or (mode == "employer" and row.status == "withdrawn"):
            unavailable()
        if (
            not isinstance(row.snapshot_sha256, str)
            or re.fullmatch(_SHA256_HEX_PATTERN, row.snapshot_sha256) is None
        ):
            unavailable()
        try:
            # Receipt replay must prove that the immutable applicant snapshot
            # still exists and matches its recorded digest, without returning
            # its Markdown or copying it into the receipt.
            read_application_snapshot(request, row)
            result = application_response(row, job, organization)
            response_body = idempotency_replay_json(result)
        except (HTTPException, OSError, StorageIntegrityError, TypeError, ValueError) as exc:
            unavailable(exc)
        except ValidationError as exc:
            unavailable(exc)
        expected_digest = _application_transition_receipt_digest(
            row, job, organization, action, response_body
        )
        try:
            if not compare_digest(parts["digest"], expected_digest):
                unavailable()
        except (TypeError, ValueError) as exc:
            unavailable(exc)
        return Response(
            content=response_body,
            status_code=200,
            media_type="application/json",
            headers={"Idempotency-Replayed": "true"},
        )

    async def agent_grant_recovery_replay(
        session: AsyncSession,
        principal: Principal,
        record: IdempotencyRecord,
        context: dict[str, str],
    ) -> Response:
        def unavailable(exc: BaseException | None = None) -> NoReturn:
            error = HTTPException(
                status_code=503,
                detail="idempotent agent-grant receipt cannot be reconstructed",
            )
            if exc is None:
                raise error
            raise error from exc

        if (
            record.resource_type != "agent_grant_recovery"
            or record.response_status != 201
            or record.response_body != ""
            or record.response_headers != "{}"
            or not record.resource_id
        ):
            unavailable()
        try:
            parts = _agent_grant_recovery_resource_parts(record.resource_id)
        except (TypeError, ValueError) as exc:
            unavailable(exc)
        resource_type = context.get("resource_type")
        resource_id = context.get("resource_id") or None
        if resource_type not in {"owner", "document", "organization"}:
            unavailable()
        if resource_type == "owner" and resource_id is not None:
            unavailable()
        if resource_type in {"document", "organization"} and not resource_id:
            unavailable()

        if resource_type == "document":
            document = await session.scalar(
                select(Document)
                .where(Document.id == resource_id, Document.owner_id == principal.subject)
                .with_for_update()
            )
            if document is None:
                unavailable()
        elif resource_type == "organization":
            organization = await session.scalar(
                select(Organization).where(Organization.id == resource_id).with_for_update()
            )
            if (
                organization is None
                or await organization_role(session, organization, principal) is None
            ):
                unavailable()

        row = await session.scalar(
            select(AgentGrant)
            .where(
                AgentGrant.id == parts["grant_id"],
                AgentGrant.owner_id == principal.subject,
            )
            .with_for_update()
        )
        if row is None or row.resource_type != resource_type or row.resource_id != resource_id:
            unavailable()
        if (
            not isinstance(row.prefix, str)
            or not row.prefix.startswith("cng_")
            or len(row.prefix) > 20
            or row.mandate_id is not None
            or row.revoked
            or retention_expired(row.expires_at)
        ):
            unavailable()
        try:
            raw_scopes = json.loads(row.scopes)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            unavailable(exc)
        if (
            not isinstance(raw_scopes, list)
            or any(not isinstance(scope, str) or not scope for scope in raw_scopes)
            or raw_scopes != sorted(set(raw_scopes))
            or not agent_grant_definition_is_valid(
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                scopes=frozenset(raw_scopes),
                mode=row.mode,
                mandate_id=row.mandate_id,
            )
        ):
            unavailable()
        try:
            recovery = AgentGrantRecoveryResponse(
                id=row.id,
                name=row.name,
                prefix=row.prefix,
                scopes=raw_scopes,
                mode=cast(Any, row.mode),
                resource=AgentGrantResource(type=cast(Any, row.resource_type), id=row.resource_id),
                expires_at=row.expires_at,
                recovery_required=True,
                created_at=row.created_at,
            )
            response_body = idempotency_replay_json(recovery)
            expected_digest = _agent_grant_recovery_digest(
                row, principal.subject, raw_scopes, response_body
            )
        except (TypeError, ValueError, ValidationError) as exc:
            unavailable(exc)
        if not compare_digest(parts["digest"], expected_digest):
            unavailable()
        return Response(
            content=response_body,
            status_code=201,
            media_type="application/json",
            headers={"Idempotency-Replayed": "true"},
        )

    async def social_graph_replay(
        session: AsyncSession,
        request: Request,
        principal: Principal,
        record: IdempotencyRecord,
        operation: str,
    ) -> Response:
        def unavailable(exc: BaseException | None = None) -> NoReturn:
            error = HTTPException(
                status_code=503,
                detail="idempotent social receipt cannot be reconstructed",
            )
            if exc is None:
                raise error
            raise error from exc

        profile_handle = request.path_params.get("profile_handle")
        if not isinstance(profile_handle, str) or not profile_handle:
            unavailable()
        operation_context = {
            "POST:/v1/follows/": ("follow", "follow", "social_follow"),
            "DELETE:/v1/follows/": ("follow", "unfollow", "social_follow"),
            "POST:/v1/content-blocks/": (
                "content_block",
                "block",
                "social_content_block",
            ),
            "DELETE:/v1/content-blocks/": (
                "content_block",
                "unblock",
                "social_content_block",
            ),
        }
        context = next(
            (
                values
                for prefix, values in operation_context.items()
                if operation == f"{prefix}{profile_handle}"
            ),
            None,
        )
        if context is None:
            unavailable()
        kind, action, expected_resource_type = context
        if (
            record.resource_type != expected_resource_type
            or record.response_headers != "{}"
            or record.response_status != (200 if action == "follow" else 204)
        ):
            unavailable()
        if action == "follow" and not record.response_body:
            unavailable()
        if action != "follow" and record.response_body != "":
            unavailable()
        try:
            parts = _social_resource_parts(record.resource_id or "")
        except (TypeError, ValueError) as exc:
            unavailable(exc)
        if parts["kind"] != kind or parts["action"] != action:
            unavailable()

        try:
            profile = await public_profile_by_handle(session, profile_handle)
        except HTTPException as exc:
            unavailable(exc)
        if profile.id != parts["target_document_id"]:
            unavailable()
        try:
            if profile.owner_id == principal.subject:
                if action in {"follow", "block"}:
                    unavailable()
                locked_profile = await public_profile_by_handle(
                    session, profile_handle, for_update=True
                )
            else:
                await lock_post_graph_pair(session, principal.subject, profile.owner_id)
                locked_profile = await public_profile_by_handle(
                    session, profile_handle, for_update=True
                )
        except (HTTPException, TypeError, ValueError) as exc:
            unavailable(exc)
        if (
            locked_profile.id != profile.id
            or locked_profile.owner_id != profile.owner_id
            or locked_profile.visibility != "public"
            or locked_profile.public_identifier != profile_handle
        ):
            unavailable()
        follows, blocks = await social_graph_pair_rows(
            session, principal.subject, locked_profile.owner_id, for_update=True
        )
        if action == "follow":
            direct_follow = next(
                (
                    row
                    for row in follows
                    if row.follower_owner_id == principal.subject
                    and row.followed_owner_id == locked_profile.owner_id
                ),
                None,
            )
            if direct_follow is None or blocks:
                unavailable()
            try:
                raw_body = json.loads(record.response_body)
                if not isinstance(raw_body, dict) or set(raw_body) != {
                    "profile_handle",
                    "created_at",
                }:
                    unavailable()
                safe_result = FollowResponse.model_validate(raw_body)
                response_body = idempotency_replay_json(safe_result)
            except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
                unavailable(exc)
            expected_body = idempotency_replay_json(
                FollowResponse(
                    profile_handle=direct_follow.followed_profile_handle,
                    created_at=direct_follow.created_at,
                )
            )
            if response_body != record.response_body or response_body != expected_body:
                unavailable()
        elif action == "unfollow":
            if any(
                row.follower_owner_id == principal.subject
                and row.followed_owner_id == locked_profile.owner_id
                for row in follows
            ):
                unavailable()
            response_body = ""
        elif action == "block":
            if (
                not any(
                    row.blocker_owner_id == principal.subject
                    and row.blocked_owner_id == locked_profile.owner_id
                    for row in blocks
                )
                or follows
            ):
                unavailable()
            response_body = ""
        else:
            if any(
                row.blocker_owner_id == principal.subject
                and row.blocked_owner_id == locked_profile.owner_id
                for row in blocks
            ):
                unavailable()
            response_body = ""
        expected_digest = _social_receipt_digest(
            operation,
            principal.subject,
            locked_profile.owner_id,
            locked_profile.id,
            profile_handle,
            follows,
            blocks,
            response_body,
        )
        if not compare_digest(parts["digest"], expected_digest):
            unavailable()
        if action == "follow":
            return Response(
                content=record.response_body,
                status_code=200,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        return Response(status_code=204, headers={"Idempotency-Replayed": "true"})

    async def agent_outreach_replay(
        session: AsyncSession,
        principal: Principal,
        record: IdempotencyRecord,
        context: dict[str, str],
    ) -> Response:
        def unavailable(exc: BaseException | None = None) -> NoReturn:
            error = HTTPException(
                status_code=503,
                detail="idempotent agent outreach receipt cannot be reconstructed",
            )
            if exc is None:
                raise error
            raise error from exc

        if (
            principal.method != "agent_grant"
            or not principal.grant_id
            or record.resource_type != "contact_request"
            or record.response_status != 201
            or record.response_body != ""
            or record.response_headers != "{}"
            or not record.resource_id
        ):
            unavailable()
        required_context = {
            "mandate_id",
            "source_identity_handle",
            "grant_id",
            "target_identity_handle",
            "target_document_id",
        }
        if set(context) != required_context or any(
            not isinstance(value, str) or not value for value in context.values()
        ):
            unavailable()
        if context["grant_id"] != principal.grant_id:
            unavailable()
        try:
            parts = _agent_outreach_receipt_parts(record.resource_id)
            expected_resource_id = _agent_outreach_receipt_resource_id(
                parts["request_id"],
                mandate_id=context["mandate_id"],
                source_identity_handle=context["source_identity_handle"],
                grant_id=context["grant_id"],
            )
        except (TypeError, ValueError) as exc:
            unavailable(exc)
        if not compare_digest(record.resource_id, expected_resource_id):
            unavailable()
        if (
            not compare_digest(
                parts["mandate_digest"], sha256(context["mandate_id"].encode("utf-8")).hexdigest()
            )
            or not compare_digest(
                parts["source_identity_digest"],
                sha256(context["source_identity_handle"].encode("utf-8")).hexdigest(),
            )
            or not compare_digest(
                parts["grant_digest"], sha256(context["grant_id"].encode("utf-8")).hexdigest()
            )
        ):
            unavailable()

        try:
            mandate, source_identity = await mandate_bound_identity(session, principal)
        except HTTPException as exc:
            unavailable(exc)
        if (
            mandate.id != context["mandate_id"]
            or source_identity.handle != context["source_identity_handle"]
        ):
            unavailable()
        row = await session.scalar(
            select(ContactRequest).where(ContactRequest.id == parts["request_id"]).with_for_update()
        )
        if (
            row is None
            or row.sender_owner_id != principal.subject
            or row.sender_grant_id != context["grant_id"]
            or row.sender_mandate_id != context["mandate_id"]
            or row.sender_identity_handle != context["source_identity_handle"]
            or row.target_identity_handle != context["target_identity_handle"]
            or row.target_document_id != context["target_document_id"]
            or row.origin != "agent_outreach"
            or row.status != "pending"
            or row.decided_at is not None
            or retention_expired(row.retention_expires_at)
        ):
            unavailable()
        try:
            sender, _, target, target_profile = await lock_live_outreach_identities(
                session,
                source_identity=source_identity,
                target_handle=context["target_identity_handle"],
                source_owner_id=principal.subject,
            )
        except (HTTPException, TypeError, ValueError) as exc:
            unavailable(exc)
        if (
            sender.handle != row.sender_identity_handle
            or target.handle != row.target_identity_handle
            or target_profile.id != row.target_document_id
            or target.owner_id != row.recipient_owner_id
        ):
            unavailable()
        policy = await session.scalar(
            select(ContactPolicy).where(ContactPolicy.owner_id == target.owner_id)
        )
        blocked = await session.scalar(
            select(ContactBlock).where(
                ContactBlock.blocker_owner_id == target.owner_id,
                ContactBlock.blocked_owner_id == principal.subject,
            )
        )
        if policy is None or not policy.allow_agent_requests or blocked is not None:
            unavailable()
        try:
            response_body = idempotency_replay_json(agent_outreach_receipt(row))
        except (TypeError, ValueError, ValidationError) as exc:
            unavailable(exc)
        if not response_body:
            unavailable()
        return Response(
            content=response_body,
            status_code=201,
            media_type="application/json",
            headers={"Idempotency-Replayed": "true"},
        )

    def moderation_review_forbidden() -> NoReturn:
        raise HTTPException(status_code=403, detail="moderation review access is forbidden")

    def require_configured_moderation_reviewer(
        principal: Principal, authority: Settings, review_kind: Literal["case", "appeal"]
    ) -> tuple[str, str]:
        if principal.method != "clerk_jwt" or principal.is_impersonated:
            moderation_review_forbidden()
        try:
            configured = configured_moderation_authorities(authority)
        except PostModerationConfigurationError:
            moderation_review_forbidden()
        expected_id, expected_role = (
            (configured.moderator_id, configured.moderator_role)
            if review_kind == "case"
            else (configured.appeal_reviewer_id, configured.appeal_reviewer_role)
        )
        if not isinstance(principal.subject, str) or not compare_digest(
            principal.subject, expected_id
        ):
            moderation_review_forbidden()
        return expected_id, expected_role

    def moderation_review_headers() -> dict[str, str]:
        return {"Cache-Control": "no-store, private"}

    def moderation_required_snapshot_etag(request: Request) -> tuple[str, str]:
        etag = request.headers.get("If-Match")
        if etag is None:
            raise HTTPException(status_code=428, detail="If-Match is required for this decision")
        if re.fullmatch(r'"sha256-[0-9a-f]{64}"', etag) is None:
            raise HTTPException(status_code=412, detail="moderation review evidence is stale")
        return etag, etag[8:-1]

    def moderation_normalized_decision_body(
        body: ModerationCaseDecisionRequest | ModerationAppealReviewRequest,
    ) -> tuple[dict[str, str], str]:
        normalized = body.subject_explanation.strip()
        if not normalized:
            raise HTTPException(status_code=422, detail="subject explanation is invalid")
        payload = {"action": body.action, "subject_explanation": normalized}
        if isinstance(body, ModerationCaseDecisionRequest):
            payload["reason_code"] = body.reason_code
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return payload, serialized

    def moderation_receipt_digest(
        *,
        resource_kind: Literal["moderation_decision", "moderation_appeal_review"],
        case: ModerationCase,
        post: Post,
        decision: ModerationDecision,
        appeal: ModerationAppeal | None,
        reports: tuple[PostReport, ...],
        action: str,
    ) -> str:
        def normalized(value: datetime | None) -> str | None:
            if value is None:
                return None
            utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
            return utc_value.astimezone(UTC).isoformat().replace("+00:00", "Z")

        facts: dict[str, Any] = {
            "action": action,
            "case": {
                "closed_at": normalized(case.closed_at),
                "created_at": normalized(case.created_at),
                "id": case.id,
                "post_id": case.post_id,
                "retention_expires_at": normalized(case.retention_expires_at),
                "sensitive_purged_at": normalized(case.sensitive_purged_at),
                "status": case.status,
                "subject_binding": sha256(
                    f"{case.subject_owner_id}\x00{post.owner_id}".encode()
                ).hexdigest(),
                "updated_at": normalized(case.updated_at),
            },
            "decision": {
                "action": decision.action,
                "case_id": decision.case_id,
                "decided_at": normalized(decision.decided_at),
                "evidence_sha256": sha256((decision.evidence or "").encode("utf-8")).hexdigest(),
                "evidence_snapshot_sha256": decision.evidence_snapshot_sha256,
                "id": decision.id,
                "internal_rationale_sha256": sha256(
                    (decision.internal_rationale or "").encode("utf-8")
                ).hexdigest(),
                "moderator_id": decision.moderator_id,
                "moderator_role": decision.moderator_role,
                "post_id": decision.post_id,
                "reason_code": decision.reason_code,
                "subject_explanation_sha256": sha256(
                    decision.subject_explanation.encode("utf-8")
                ).hexdigest(),
            },
            "post": {
                "current_version": post.current_version,
                "id": post.id,
                "published_at": normalized(post.published_at),
                "sha256": post.sha256,
                "status": post.status,
                "updated_at": normalized(post.updated_at),
                "withheld_at": normalized(post.withheld_at),
                "withdrawn_at": normalized(post.withdrawn_at),
            },
            "reports": [
                {
                    "case_id": report.case_id,
                    "created_at": normalized(report.created_at),
                    "id": report.id,
                    "narrative_sha256": sha256(
                        (report.narrative or "").encode("utf-8")
                    ).hexdigest(),
                    "post_id": report.post_id,
                    "reason_code": report.reason_code,
                }
                for report in reports
            ],
            "resource_kind": resource_kind,
        }
        facts["appeal"] = (
            None
            if appeal is None
            else {
                "appeal_reviewer_id": appeal.appeal_reviewer_id,
                "appeal_reviewer_role": appeal.appeal_reviewer_role,
                "case_id": appeal.case_id,
                "decision_id": appeal.decision_id,
                "id": appeal.id,
                "internal_rationale_sha256": sha256(
                    (appeal.internal_rationale or "").encode("utf-8")
                ).hexdigest(),
                "rationale_sha256": sha256((appeal.rationale or "").encode("utf-8")).hexdigest(),
                "review_snapshot_sha256": appeal.review_snapshot_sha256,
                "reviewed_at": normalized(appeal.reviewed_at),
                "status": appeal.status,
                "subject_binding": sha256(
                    f"{appeal.subject_owner_id}\x00{case.subject_owner_id}\x00{post.owner_id}".encode()
                ).hexdigest(),
                "subject_explanation_sha256": sha256(
                    (appeal.subject_explanation or "").encode("utf-8")
                ).hexdigest(),
                "submitted_at": normalized(appeal.submitted_at),
            }
        )
        return sha256(
            json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def moderation_receipt_resource_id(
        *,
        resource_kind: Literal["moderation_decision", "moderation_appeal_review"],
        route_id: str,
        action: str,
        snapshot_sha256: str,
        digest: str,
    ) -> str:
        return ":".join(
            (
                resource_kind,
                "v2",
                sha256(route_id.encode("utf-8")).hexdigest(),
                action,
                snapshot_sha256,
                digest,
            )
        )

    def moderation_receipt_parts(
        resource_id: str | None,
        resource_kind: Literal["moderation_decision", "moderation_appeal_review"],
        actions: frozenset[str],
    ) -> tuple[str, str, str, str]:
        parts = (resource_id or "").split(":")
        if (
            len(parts) != 6
            or parts[0] != resource_kind
            or parts[1] != "v2"
            or parts[3] not in actions
            or any(
                re.fullmatch(_SHA256_HEX_PATTERN, value) is None
                for value in (parts[2], parts[4], parts[5])
            )
        ):
            raise ValueError("moderation decision receipt is malformed")
        return parts[2], parts[3], parts[4], parts[5]

    async def moderation_review_replay(
        session: AsyncSession,
        request: Request,
        principal: Principal,
        record: IdempotencyRecord,
        operation: str,
    ) -> Response:
        def unavailable(exc: BaseException | None = None) -> NoReturn:
            error = HTTPException(
                status_code=503,
                detail="idempotent moderation decision receipt cannot be reconstructed",
            )
            if exc is None:
                raise error
            raise error from exc

        case_prefix = "POST:/v1/internal/post-moderation/cases/"
        appeal_prefix = "POST:/v1/internal/post-moderation/appeals/"
        if operation.startswith(case_prefix) and operation.endswith("/decision"):
            route_id = operation[len(case_prefix) : -len("/decision")]
            if not route_id or "/" in route_id:
                unavailable()
            require_configured_moderation_reviewer(principal, request.app.state.settings, "case")
            if request.path_params.get("case_id") != route_id:
                unavailable()
            if (
                record.resource_type != "moderation_decision"
                or record.response_status != 204
                or record.response_body != ""
                or record.response_headers != "{}"
            ):
                unavailable()
            try:
                route_digest, action, snapshot_sha256, receipt_digest = moderation_receipt_parts(
                    record.resource_id,
                    "moderation_decision",
                    frozenset({"dismiss", "withhold"}),
                )
            except ValueError as exc:
                unavailable(exc)
            if not compare_digest(route_digest, sha256(route_id.encode("utf-8")).hexdigest()):
                unavailable()
            try:
                case_bundle = await lock_case_review_bundle(
                    session, case_id=route_id, allow_existing_decision=True
                )
                case_evidence_snapshot(request.app.state.store, case_bundle)
            except PostModerationError as exc:
                unavailable(exc)
            case, post, decision, appeal = (
                case_bundle.case,
                case_bundle.post,
                case_bundle.decision,
                case_bundle.appeal,
            )
            expected_action = "no_action" if action == "dismiss" else "withhold"
            valid_lineage = (
                action == "dismiss"
                and case.status == "dismissed"
                and appeal is None
                and post.status in {"published", "withdrawn"}
            ) or (
                action == "withhold"
                and (
                    (
                        appeal is None
                        and case.status == "withheld"
                        and post.status in {"withheld", "withdrawn"}
                    )
                    or (
                        appeal is not None
                        and (
                            (
                                appeal.status == "submitted"
                                and case.status == "appealed"
                                and post.status in {"withheld", "withdrawn"}
                            )
                            or (
                                appeal.status == "upheld"
                                and case.status == "appeal_upheld"
                                and post.status in {"withheld", "withdrawn"}
                            )
                            or (
                                appeal.status == "overturned"
                                and case.status == "appeal_overturned"
                                and post.status in {"published", "withdrawn"}
                            )
                        )
                    )
                )
            )
            configured_id, configured_role = require_configured_moderation_reviewer(
                principal, request.app.state.settings, "case"
            )
            if (
                decision is None
                or case.post_id != post.id
                or case.subject_owner_id != post.owner_id
                or not valid_lineage
                or decision.case_id != case.id
                or decision.post_id != post.id
                or decision.action != expected_action
                or decision.moderator_role != configured_role
                or not compare_digest(decision.moderator_id, configured_id)
                or decision.evidence_snapshot_sha256 is None
                or not compare_digest(decision.evidence_snapshot_sha256, snapshot_sha256)
            ):
                unavailable()
            try:
                digest = moderation_receipt_digest(
                    resource_kind="moderation_decision",
                    case=case,
                    post=post,
                    decision=decision,
                    appeal=appeal,
                    reports=case_bundle.reports,
                    action=action,
                )
            except (AttributeError, TypeError, ValueError) as exc:
                unavailable(exc)
            if not compare_digest(receipt_digest, digest):
                unavailable()
        elif operation.startswith(appeal_prefix) and operation.endswith("/decision"):
            route_id = operation[len(appeal_prefix) : -len("/decision")]
            if not route_id or "/" in route_id:
                unavailable()
            require_configured_moderation_reviewer(principal, request.app.state.settings, "appeal")
            if request.path_params.get("appeal_id") != route_id:
                unavailable()
            if (
                record.resource_type != "moderation_appeal_review"
                or record.response_status != 204
                or record.response_body != ""
                or record.response_headers != "{}"
            ):
                unavailable()
            try:
                route_digest, action, snapshot_sha256, receipt_digest = moderation_receipt_parts(
                    record.resource_id,
                    "moderation_appeal_review",
                    frozenset({"uphold", "overturn"}),
                )
            except ValueError as exc:
                unavailable(exc)
            if not compare_digest(route_digest, sha256(route_id.encode("utf-8")).hexdigest()):
                unavailable()
            try:
                appeal_bundle = await lock_appeal_review_bundle(session, appeal_id=route_id)
                appeal_evidence_snapshot(request.app.state.store, appeal_bundle)
            except PostModerationError as exc:
                unavailable(exc)
            appeal, case, decision, post = (
                appeal_bundle.appeal,
                appeal_bundle.case,
                appeal_bundle.decision,
                appeal_bundle.post,
            )
            configured_id, configured_role = require_configured_moderation_reviewer(
                principal, request.app.state.settings, "appeal"
            )
            expected_status = "overturned" if action == "overturn" else "upheld"
            expected_case_status = "appeal_overturned" if action == "overturn" else "appeal_upheld"
            expected_post_statuses = (
                {"published", "withdrawn"} if action == "overturn" else {"withheld", "withdrawn"}
            )
            if (
                case.post_id != post.id
                or case.subject_owner_id != post.owner_id
                or post.status not in expected_post_statuses
                or decision.case_id != case.id
                or decision.post_id != post.id
                or decision.action != "withhold"
                or appeal.case_id != case.id
                or appeal.decision_id != decision.id
                or appeal.subject_owner_id != case.subject_owner_id
                or appeal.status != expected_status
                or case.status != expected_case_status
                or appeal.appeal_reviewer_role != configured_role
                or appeal.appeal_reviewer_id is None
                or not compare_digest(appeal.appeal_reviewer_id, configured_id)
                or appeal.review_snapshot_sha256 is None
                or not compare_digest(appeal.review_snapshot_sha256, snapshot_sha256)
            ):
                unavailable()
            try:
                digest = moderation_receipt_digest(
                    resource_kind="moderation_appeal_review",
                    case=case,
                    post=post,
                    decision=decision,
                    appeal=appeal,
                    reports=appeal_bundle.reports,
                    action=action,
                )
            except (AttributeError, TypeError, ValueError) as exc:
                unavailable(exc)
            if not compare_digest(receipt_digest, digest):
                unavailable()
        else:
            unavailable()
        return Response(
            status_code=204, headers={**moderation_review_headers(), "Idempotency-Replayed": "true"}
        )

    def artifact_receipt_unavailable() -> NoReturn:
        raise HTTPException(
            status_code=503,
            detail="idempotent artifact receipt cannot be reconstructed",
        )

    async def reconstruct_application_creation(
        session: AsyncSession,
        request: Request,
        record: IdempotencyRecord,
    ) -> ApplicationResponse:
        if record.resource_type != "application" or record.resource_id is None:
            artifact_receipt_unavailable()
        bundle = (
            await session.execute(
                select(Application, Job, Organization)
                .join(Job, Application.job_id == Job.id)
                .join(Organization, Job.organization_id == Organization.id)
                .where(Application.id == record.resource_id)
            )
        ).one_or_none()
        if bundle is None:
            artifact_receipt_unavailable()
        row, job, organization = bundle
        expected_path = request.app.state.store.application_snapshot_relative_path(row.id)
        if (
            row.applicant_owner_id != record.owner_id
            or row.job_id != job.id
            or row.snapshot_storage_path != expected_path
            or row.snapshot_size_bytes is None
            or row.snapshot_size_bytes < 1
            or row.snapshot_size_bytes > 131_072
            or record.operation
            != f"POST:/v1/organizations/{organization.slug}/jobs/{job.slug}/applications"
            or record.response_status != 201
            or record.response_headers != "{}"
        ):
            artifact_receipt_unavailable()
        expected_payload = json.dumps({"job_id": job.id, "status": "submitted"}, sort_keys=True)
        changes = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_type == "application",
                    ChangeEvent.resource_id == row.id,
                    ChangeEvent.event_type.in_({"application.received", "application.submitted"}),
                )
            )
        ).all()
        expected_changes = {
            (organization.owner_id, "application.received"),
            (row.applicant_owner_id, "application.submitted"),
        }
        actual_changes = {
            (change.owner_id, change.event_type)
            for change in changes
            if change.payload == expected_payload
            and change.actor_id == row.applicant_actor_id
            and change.actor_method == row.applicant_actor_method
            and change.grant_id == row.applicant_grant_id
            and verification_timestamp(change.occurred_at) == verification_timestamp(row.created_at)
        }
        if len(changes) != 2 or actual_changes != expected_changes:
            artifact_receipt_unavailable()
        try:
            request.app.state.store.read_verified_bytes(
                expected_path,
                row.snapshot_sha256,
                expected_size_bytes=row.snapshot_size_bytes,
                max_size_bytes=131_072,
            )
        except StorageIntegrityError:
            artifact_receipt_unavailable()
        result = application_creation_response(row, job, organization)
        if not compare_digest(record.response_body, result.model_dump_json()):
            artifact_receipt_unavailable()
        return result

    async def reconstruct_verification_submission(
        session: AsyncSession,
        request: Request,
        record: IdempotencyRecord,
    ) -> OrganizationVerificationSubmissionResponse:
        if record.resource_type != "organization_verification" or record.resource_id is None:
            artifact_receipt_unavailable()
        bundle = (
            await session.execute(
                select(
                    OrganizationVerification,
                    OrganizationVerificationEvidence,
                    Organization,
                )
                .join(
                    OrganizationVerificationEvidence,
                    OrganizationVerificationEvidence.verification_id == OrganizationVerification.id,
                )
                .join(
                    Organization,
                    Organization.id == OrganizationVerification.organization_id,
                )
                .where(OrganizationVerification.id == record.resource_id)
            )
        ).one_or_none()
        if bundle is None:
            artifact_receipt_unavailable()
        verification, evidence, organization = bundle
        expected_path = (
            f"verification-evidence/{organization.id}/{verification.id}/"
            f"{evidence.artifact_sha256}.bin"
        )
        if (
            verification.submitted_by_owner_id != record.owner_id
            or evidence.storage_path != expected_path
            or record.operation
            != f"POST:/v1/organizations/{organization.id}/verification-submissions"
            or record.response_status != 201
            or record.response_headers != "{}"
        ):
            artifact_receipt_unavailable()
        submitted_events = (
            await session.scalars(
                select(OrganizationVerificationEvent).where(
                    OrganizationVerificationEvent.verification_id == verification.id,
                    OrganizationVerificationEvent.organization_id == organization.id,
                    OrganizationVerificationEvent.to_state == "submitted",
                )
            )
        ).all()
        if len(submitted_events) != 1:
            artifact_receipt_unavailable()
        submitted = submitted_events[0]
        if (
            submitted.purpose != "recruiting_control"
            or submitted.actor_role != "submitter"
            or submitted.actor_id != verification.submitted_by_owner_id
            or submitted.policy_version is not None
            or submitted.expires_at is not None
            or submitted.material_claim_digest != verification.material_claim_digest
            or verification_timestamp(submitted.occurred_at)
            != verification_timestamp(verification.created_at)
        ):
            artifact_receipt_unavailable()
        try:
            evidence_metadata = json.loads(evidence.metadata_json)
        except json.JSONDecodeError:
            artifact_receipt_unavailable()
        if not isinstance(evidence_metadata, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in evidence_metadata.items()
        ):
            artifact_receipt_unavailable()
        if re.fullmatch(_SHA256_HEX_PATTERN, verification.material_claim_digest) is None:
            artifact_receipt_unavailable()
        expected_change_payload = json.dumps({"state": "submitted"}, sort_keys=True)
        changes = (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.resource_type == "organization_verification",
                    ChangeEvent.resource_id == verification.id,
                    ChangeEvent.event_type == "organization_verification.submitted",
                )
            )
        ).all()
        if len(changes) != 1:
            artifact_receipt_unavailable()
        change = changes[0]
        if (
            change.owner_id != organization.owner_id
            or change.actor_id != submitted.actor_id
            or change.actor_method != "clerk_jwt"
            or change.grant_id is not None
            or change.payload != expected_change_payload
            or verification_timestamp(change.occurred_at)
            != verification_timestamp(verification.created_at)
        ):
            artifact_receipt_unavailable()
        try:
            request.app.state.store.read_verified_bytes(
                expected_path,
                evidence.artifact_sha256,
                expected_size_bytes=evidence.artifact_size_bytes,
                max_size_bytes=262_144,
            )
        except StorageIntegrityError:
            artifact_receipt_unavailable()
        result = OrganizationVerificationSubmissionResponse(
            verification_id=verification.id,
            state="submitted",
            evidence_sha256=evidence.artifact_sha256,
            artifact_content_type=cast(Any, evidence.artifact_content_type),
            artifact_size_bytes=evidence.artifact_size_bytes,
            submitted_at=verification_timestamp(verification.created_at),
        )
        if not compare_digest(record.response_body, result.model_dump_json()):
            artifact_receipt_unavailable()
        return result

    async def recruiting_verification_decision_replay(
        session: AsyncSession,
        request: Request,
        principal: Principal,
        record: IdempotencyRecord,
        operation: str,
    ) -> Response:
        def unavailable() -> NoReturn:
            raise HTTPException(
                status_code=503,
                detail="idempotent recruiting verification decision receipt cannot be reconstructed",
                headers=dict(_VERIFICATION_REVIEW_HEADERS),
            )

        expected_headers_json = json.dumps(_VERIFICATION_REVIEW_HEADERS, sort_keys=True)
        if (
            record.resource_type != _RECRUITING_DECISION_RESOURCE_TYPE
            or record.response_status != 200
            or not record.response_body
            or record.response_headers != expected_headers_json
            or not record.resource_id
        ):
            unavailable()
        try:
            parts = _recruiting_decision_resource_parts(record.resource_id)
            stored_headers = json.loads(record.response_headers)
            result = OrganizationVerificationReviewerSummaryResponse.model_validate_json(
                record.response_body
            )
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
            unavailable()
        if (
            not isinstance(stored_headers, dict)
            or stored_headers != _VERIFICATION_REVIEW_HEADERS
            or result.model_dump_json() != record.response_body
        ):
            unavailable()
        event = await session.scalar(
            select(OrganizationVerificationEvent).where(
                OrganizationVerificationEvent.id == parts["event_id"]
            )
        )
        if event is None:
            unavailable()
        verification = await session.get(OrganizationVerification, event.verification_id)
        if verification is None:
            unavailable()
        action = parts["action"]
        authority = request.app.state.settings
        configured_reviewer_id = authority.verification_reviewer_id
        configured_reviewer_role = authority.verification_reviewer_role
        if (
            principal.method != "clerk_jwt"
            or principal.is_impersonated
            or configured_reviewer_id is None
            or configured_reviewer_role != "recruiting_verifier"
            or record.owner_id != principal.subject
            or not compare_digest(principal.subject, configured_reviewer_id)
            or not compare_digest(event.actor_id, record.owner_id)
            or not compare_digest(event.actor_id, configured_reviewer_id)
            or event.actor_role != configured_reviewer_role
            or event.to_state != _RECRUITING_DECISION_ACTION_STATES[action]
            or event.verification_id != verification.id
            or event.organization_id != verification.organization_id
            or event.purpose != "recruiting_control"
            or verification.purpose != "recruiting_control"
            or event.material_claim_digest != verification.material_claim_digest
            or operation != f"POST:/v1/internal/recruiting-verifications/{verification.id}/{action}"
            or result.verification_id != verification.id
            or result.state != event.to_state
            or result.material_claim_digest != verification.material_claim_digest
            or _recruiting_decision_datetime(result.submitted_at)
            != _recruiting_decision_datetime(verification.created_at)
            or _recruiting_decision_datetime(result.updated_at)
            != _recruiting_decision_datetime(event.occurred_at)
            or result.policy_version != event.policy_version
            or _recruiting_decision_datetime(result.expires_at)
            != _recruiting_decision_datetime(event.expires_at)
        ):
            unavailable()
        try:
            expected_digest = _recruiting_decision_receipt_digest(
                event,
                verification,
                action=action,
                owner_id=record.owner_id,
                idempotency_key=record.idempotency_key,
                operation=record.operation,
                request_hash=record.request_hash,
                response_status=record.response_status,
                response_body=record.response_body,
                response_headers=stored_headers,
            )
        except (TypeError, ValueError):
            unavailable()
        if not compare_digest(parts["digest"], expected_digest):
            unavailable()
        return Response(
            content=record.response_body,
            status_code=200,
            media_type="application/json",
            headers={**_VERIFICATION_REVIEW_HEADERS, "Idempotency-Replayed": "true"},
        )

    def idempotency_receipt_headers(
        record: IdempotencyRecord,
        *,
        detail: str,
    ) -> dict[str, str]:
        try:
            headers = json.loads(record.response_headers)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail=detail) from exc
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
        ):
            raise HTTPException(status_code=503, detail=detail)
        return headers

    async def idempotency_replay(
        session: AsyncSession,
        request: Request,
        principal: Principal,
        key: str | None,
        operation: str,
        fingerprint: str,
        application_context: dict[str, str] | None = None,
        agent_grant_context: dict[str, str] | None = None,
        outreach_context: dict[str, str] | None = None,
    ) -> Response | None:
        if key is None:
            return None
        record = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == principal.subject,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        if record is None:
            return None
        if record.operation != operation or record.request_hash != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used for a different request",
            )
        if operation.startswith("POST:/v1/internal/recruiting-verifications/"):
            return await recruiting_verification_decision_replay(
                session, request, principal, record, operation
            )
        if operation.endswith("/applications") and record.resource_type == "application":
            application_receipt = await reconstruct_application_creation(session, request, record)
            return Response(
                content=application_receipt.model_dump_json(),
                status_code=201,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        if (
            operation.endswith("/verification-submissions")
            and record.resource_type == "organization_verification"
        ):
            verification_receipt = await reconstruct_verification_submission(
                session, request, record
            )
            return Response(
                content=verification_receipt.model_dump_json(),
                status_code=201,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        if operation.startswith("POST:/v1/internal/post-moderation/"):
            return await moderation_review_replay(session, request, principal, record, operation)
        if operation == "POST:/v1/agent-identities" or operation.startswith(
            "DELETE:/v1/agent-identities/"
        ):
            return await agent_identity_replay(session, principal, record, operation)
        if operation == "POST:/v1/agent-grants":
            if record.resource_type != "agent_grant_recovery" or agent_grant_context is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent agent-grant receipt cannot be reconstructed",
                )
            return await agent_grant_recovery_replay(
                session, principal, record, agent_grant_context
            )
        if operation.startswith("POST:/v1/applications/"):
            if record.resource_type != "application_transition" or application_context is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent application transition receipt cannot be reconstructed",
                )
            return await application_transition_replay(
                session, request, principal, record, application_context
            )
        if operation == "POST:/v1/agent-outreach":
            if outreach_context is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent agent outreach receipt cannot be reconstructed",
                )
            return await agent_outreach_replay(session, principal, record, outreach_context)
        if operation.startswith(
            (
                "POST:/v1/follows/",
                "DELETE:/v1/follows/",
                "POST:/v1/content-blocks/",
                "DELETE:/v1/content-blocks/",
            )
        ):
            return await social_graph_replay(session, request, principal, record, operation)
        is_membership_accept = (
            operation.startswith("POST:/v1/organizations/")
            and "/memberships/" in operation
            and operation.endswith("/accept")
        )
        is_membership_remove = (
            operation.startswith("DELETE:/v1/organizations/") and "/memberships/" in operation
        )
        is_membership_invite = (
            operation.startswith("POST:/v1/organizations/")
            and operation.endswith("/admins")
            and "/memberships/" not in operation
        )
        if (
            is_membership_accept or is_membership_remove or is_membership_invite
        ) and record.resource_type != "organization_membership":
            raise HTTPException(
                status_code=503,
                detail="idempotent membership receipt cannot be reconstructed",
            )
        if (
            record.operation == "POST:/v1/posts"
            and record.resource_type == "post"
            and not record.response_body
        ):
            post = await session.scalar(
                select(Post).where(
                    Post.id == record.resource_id,
                    Post.owner_id == principal.subject,
                )
            )
            if post is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent post publication committed but its receipt cannot be reconstructed",
                )
            if (
                post.status != "published"
                or post.withdrawn_at is not None
                or post.withheld_at is not None
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent post publication receipt cannot be reconstructed",
                )
            post_version = await session.scalar(
                select(PostVersion).where(PostVersion.post_id == post.id, PostVersion.version == 1)
            )
            if (
                post_version is None
                or post_version.sha256 != post.sha256
                or post_version.storage_path != post.storage_path
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent post publication receipt cannot be reconstructed",
                )
            try:
                markdown, frontmatter = verified_post_markdown(post, request)
                version_created_at = post_datetime(post_version.created_at).astimezone(UTC)
                canonical_updated_at = canonical_post_datetime(frontmatter.get("updated_at"))
                if post_datetime(post.updated_at).astimezone(UTC) != canonical_updated_at:
                    raise StorageIntegrityError(
                        "canonical post updated timestamp does not match its ledger row"
                    )
                post_recovered = post_response(
                    post,
                    request,
                    markdown=markdown,
                    frontmatter=frontmatter,
                ).model_copy(
                    update={
                        "published_at": version_created_at,
                        "updated_at": canonical_updated_at,
                    }
                )
                stored_headers = json.loads(record.response_headers)
                expected_headers = {
                    **post_representation_headers(post, updated_at=canonical_updated_at),
                    "Location": f"/v1/posts/{post.id}",
                }
                if (
                    record.response_status != 201
                    or not isinstance(stored_headers, dict)
                    or stored_headers != expected_headers
                ):
                    raise ValueError("post receipt metadata is inconsistent")
            except StorageIntegrityError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent post publication receipt cannot verify canonical storage",
                ) from exc
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent post publication receipt cannot be reconstructed",
                ) from exc
            recovered_headers = {**stored_headers, "Idempotency-Replayed": "true"}
            return Response(
                content=post_recovered.model_dump_json(),
                status_code=201,
                media_type="application/json",
                headers=recovered_headers,
            )
        if operation == "POST:/v1/api-keys" or operation.startswith("DELETE:/v1/api-keys/"):
            return await replay_api_key_receipt(
                session,
                principal_subject=principal.subject,
                record=record,
                operation=operation,
                recovery_response_factory=ApiKeyRecoveryResponse,
                serialize_response=idempotency_replay_json,
            )
        if operation == "PUT:/v1/contact-policy":
            return replay_contact_policy_receipt(
                principal_subject=principal.subject,
                record=record,
                owner_id_factory=public_owner_id,
                sha256_hex_pattern=_SHA256_HEX_PATTERN,
                serialize_response=idempotency_replay_json,
            )
        if operation.startswith("POST:/v1/contact-requests/"):
            operation_parts = operation.removeprefix("POST:/v1/contact-requests/").split("/")
            if len(operation_parts) != 2 or operation_parts[1] not in {
                "accept",
                "reject",
                "block",
                "report",
            }:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent contact decision receipt cannot be reconstructed",
                )
            if (
                record.resource_type != "contact_request_decision"
                or record.response_status != 200
                or record.response_body != ""
                or record.response_headers != "{}"
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent contact decision receipt cannot be reconstructed",
                )
            receipt_parts = (record.resource_id or "").split(":")
            if (
                len(receipt_parts) != 4
                or receipt_parts[0] != operation_parts[0]
                or receipt_parts[1] != operation_parts[1]
                or receipt_parts[2] not in {"profile_contact", "agent_outreach"}
                or not re.fullmatch(_SHA256_HEX_PATTERN, receipt_parts[3])
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent contact decision receipt cannot be reconstructed",
                )
            if receipt_parts[2] == "agent_outreach" and principal.method != "clerk_jwt":
                raise HTTPException(
                    status_code=403,
                    detail="agent outreach requests require a Clerk-human decision",
                )
            row = await session.scalar(
                select(ContactRequest).where(
                    ContactRequest.id == receipt_parts[0],
                    ContactRequest.recipient_owner_id == principal.subject,
                )
            )
            if row is None or retention_expired(row.retention_expires_at):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent contact decision receipt cannot be reconstructed",
                )
            expected_status = {
                "accept": "accepted",
                "reject": "rejected",
                "block": "blocked",
                "report": "reported",
            }[operation_parts[1]]
            if (
                row.origin != receipt_parts[2]
                or row.status != expected_status
                or (operation_parts[1] == "report" and not row.report_reason)
                or (operation_parts[1] != "report" and row.report_reason is not None)
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent contact decision receipt cannot be reconstructed",
                )
            try:
                decision_result = contact_response(row, principal)
            except ValidationError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent contact decision receipt cannot be reconstructed",
                ) from exc
            response_body = idempotency_replay_json(decision_result)
            if not compare_digest(
                receipt_parts[3],
                _contact_decision_receipt_digest(row, operation_parts[1], response_body),
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent contact decision receipt cannot be reconstructed",
                )
            return Response(
                content=response_body,
                status_code=200,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        if (
            record.operation == "POST:/v1/contact-requests"
            and record.resource_type == "contact_request"
            and not record.response_body
        ):
            contact = await session.scalar(
                select(ContactRequest).where(
                    ContactRequest.id == record.resource_id,
                    ContactRequest.sender_owner_id == principal.subject,
                    ContactRequest.origin == "profile_contact",
                )
            )
            if contact is None or retention_expired(contact.retention_expires_at):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent contact request committed but its receipt cannot be reconstructed",
                )
            contact_receipt = contact_response(contact, principal).model_copy(
                update={"status": "pending", "decided_at": None}
            )
            return Response(
                content=idempotency_replay_json(contact_receipt),
                status_code=record.response_status,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        if record.resource_type == "proposal_decision":
            if record.response_body or record.response_status != 200:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            parts = (record.resource_id or "").split(":")
            operation_path = operation.removeprefix("POST:/v1/proposals/")
            operation_proposal_id, separator, operation_action = operation_path.rpartition("/")
            if (
                not separator
                or not operation_proposal_id
                or operation_action not in {"accept", "reject"}
                or len(parts) < 2
                or parts[0] != operation_proposal_id
                or parts[1] != operation_action
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            try:
                stored_headers = json.loads(record.response_headers)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                ) from exc
            if not isinstance(stored_headers, dict):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            if len(parts) == 2 and parts[1] == "reject":
                proposal_id = parts[0]
                if not proposal_id or stored_headers:
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent proposal decision receipt cannot be reconstructed",
                    )
                proposal = await session.scalar(
                    select(AgentProposal).where(
                        AgentProposal.id == proposal_id,
                        AgentProposal.owner_id == principal.subject,
                    )
                )
                if proposal is None or proposal.status != "rejected":
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent proposal decision receipt cannot be reconstructed",
                    )
                return Response(
                    content=idempotency_replay_json(proposal_response(proposal)),
                    status_code=record.response_status,
                    media_type="application/json",
                    headers={"Idempotency-Replayed": "true"},
                )
            if (
                len(parts) != 5
                or parts[1] != "accept"
                or not parts[0]
                or not parts[2]
                or not re.fullmatch(_SHA256_HEX_PATTERN, parts[4])
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            try:
                proposal_uuid = UUID(parts[0])
                document_uuid = UUID(parts[2])
            except (AttributeError, ValueError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                ) from exc
            if str(proposal_uuid) != parts[0] or str(document_uuid) != parts[2]:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            if not parts[3].isdigit():
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            try:
                decision_version = int(parts[3])
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                ) from exc
            if decision_version < 1 or str(decision_version) != parts[3]:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            proposal = await session.scalar(
                select(AgentProposal).where(
                    AgentProposal.id == parts[0],
                    AgentProposal.owner_id == principal.subject,
                )
            )
            version_row = await session.scalar(
                select(DocumentVersion)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    DocumentVersion.document_id == parts[2],
                    DocumentVersion.version == decision_version,
                    Document.owner_id == principal.subject,
                )
            )
            if (
                proposal is None
                or proposal.status != "accepted"
                or proposal.document_id != parts[2]
                or version_row is None
                or not compare_digest(version_row.sha256, parts[4])
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            expected_headers = {
                "ETag": strong_etag(version_row.sha256),
                "X-Connectmd-Search": "queued",
            }
            if stored_headers and stored_headers != expected_headers:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            return Response(
                content=idempotency_replay_json(proposal_response(proposal)),
                status_code=record.response_status,
                media_type="application/json",
                headers={**expected_headers, "Idempotency-Replayed": "true"},
            )
        if operation == "POST:/v1/proposals" or operation.startswith(
            "MCP:propose_document_update:"
        ):
            if (
                record.resource_type != "proposal"
                or record.response_status != 201
                or record.response_body
                or record.response_headers != "{}"
                or not record.resource_id
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal receipt cannot be reconstructed",
                )
            proposal = await session.scalar(
                select(AgentProposal).where(
                    AgentProposal.id == record.resource_id,
                    AgentProposal.owner_id == principal.subject,
                )
            )
            if proposal is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal committed but its receipt cannot be reconstructed",
                )
            proposal_receipt = proposal_response(proposal).model_copy(
                update={"status": "pending", "decided_at": None}
            )
            return Response(
                content=idempotency_replay_json(proposal_receipt),
                status_code=record.response_status,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        if record.resource_type == "organization" and not record.response_body:
            raise HTTPException(
                status_code=503,
                detail="idempotent organization receipt cannot be reconstructed",
            )
        if record.resource_type == "organization_membership":
            membership_operation = operation
            accept_prefix = "POST:/v1/organizations/"
            remove_prefix = "DELETE:/v1/organizations/"
            if (
                membership_operation.startswith(accept_prefix)
                and membership_operation.endswith("/admins")
                and "/memberships/" not in membership_operation
            ):
                parts = membership_operation.removeprefix(accept_prefix).split("/")
                if (
                    len(parts) != 2
                    or not parts[0]
                    or parts[1] != "admins"
                    or record.response_status != 201
                    or record.response_body != ""
                    or record.response_headers != "{}"
                    or not record.resource_id
                ):
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership invitation receipt cannot be reconstructed",
                    )
                resource_parts = record.resource_id.split(":")
                if (
                    len(resource_parts) != 2
                    or not resource_parts[0]
                    or not re.fullmatch(_SHA256_HEX_PATTERN, resource_parts[1])
                ):
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership invitation receipt cannot be reconstructed",
                    )
                membership = await session.scalar(
                    select(OrganizationMembership)
                    .join(Organization, Organization.id == OrganizationMembership.organization_id)
                    .where(
                        Organization.id == parts[0],
                        Organization.owner_id == principal.subject,
                        OrganizationMembership.id == resource_parts[0],
                        OrganizationMembership.organization_id == parts[0],
                        OrganizationMembership.invited_by_owner_id == principal.subject,
                        OrganizationMembership.status == "invited",
                    )
                )
                if membership is None or not compare_digest(
                    resource_parts[1], _organization_membership_generation_digest(membership)
                ):
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership invitation committed but its receipt cannot be reconstructed",
                    )
                try:
                    request_payload = await request.json()
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership invitation receipt cannot be reconstructed",
                    ) from exc
                if (
                    not isinstance(request_payload, dict)
                    or request_payload.get("member_profile_handle")
                    != membership.member_profile_handle
                    or request_payload.get("role", "member") != membership.role
                ):
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership invitation receipt cannot be reconstructed",
                    )
                try:
                    result = _organization_admin_response(membership)
                except ValidationError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership invitation receipt cannot be reconstructed",
                    ) from exc
                return Response(
                    content=idempotency_replay_json(result),
                    status_code=201,
                    media_type="application/json",
                    headers={"Idempotency-Replayed": "true"},
                )
            if membership_operation.startswith(accept_prefix) and membership_operation.endswith(
                "/accept"
            ):
                parts = membership_operation.removeprefix(accept_prefix).split("/")
                receipt_digest = record.resource_id or ""
                if (
                    len(parts) != 4
                    or not parts[0]
                    or parts[1] != "memberships"
                    or not parts[2]
                    or parts[3] != "accept"
                    or record.response_status != 200
                    or record.response_body != ""
                    or record.response_headers != "{}"
                    or not re.fullmatch(_SHA256_HEX_PATTERN, receipt_digest)
                ):
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership acceptance receipt cannot be reconstructed",
                    )
                membership = await session.scalar(
                    select(OrganizationMembership)
                    .join(Organization, Organization.id == OrganizationMembership.organization_id)
                    .where(
                        Organization.slug == parts[0],
                        OrganizationMembership.id == parts[2],
                        OrganizationMembership.member_owner_id == principal.subject,
                        OrganizationMembership.status == "active",
                    )
                )
                if membership is None or not compare_digest(
                    receipt_digest,
                    _organization_membership_generation_digest(membership),
                ):
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership acceptance committed but its receipt cannot be reconstructed",
                    )
                try:
                    result = _organization_admin_response(membership)
                except ValidationError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership acceptance receipt cannot be reconstructed",
                    ) from exc
                return Response(
                    content=idempotency_replay_json(result),
                    status_code=200,
                    media_type="application/json",
                    headers={"Idempotency-Replayed": "true"},
                )
            if membership_operation.startswith(remove_prefix):
                parts = membership_operation.removeprefix(remove_prefix).split("/")
                if (
                    len(parts) != 3
                    or not parts[0]
                    or parts[1] != "memberships"
                    or not parts[2]
                    or record.response_status != 204
                    or record.response_body != ""
                    or record.response_headers != "{}"
                    or record.resource_id != parts[2]
                ):
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership removal receipt cannot be reconstructed",
                    )
                reappeared = await session.get(OrganizationMembership, parts[2])
                if reappeared is not None:
                    raise HTTPException(
                        status_code=503,
                        detail="idempotent membership removal receipt cannot be reconstructed",
                    )
                return Response(status_code=204, headers={"Idempotency-Replayed": "true"})
            if record.response_body:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent membership operation receipt cannot be reconstructed",
                )
            membership = await session.get(OrganizationMembership, record.resource_id)
            if membership is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent membership operation committed but its receipt cannot be reconstructed",
                )
            result = OrganizationAdminResponse(
                id=membership.id,
                organization_id=membership.organization_id,
                member_profile_handle=membership.member_profile_handle,
                role=cast(Any, membership.role),
                status=cast(Any, membership.status),
                created_at=_organization_membership_created_at(membership.created_at),
            )
            return Response(
                content=idempotency_replay_json(result),
                status_code=record.response_status,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        if record.resource_type == "job" and not record.response_body:
            job_id, separator, version_text = (record.resource_id or "").partition("@")
            try:
                receipt_version = int(version_text) if separator else 0
            except ValueError:
                receipt_version = 0
            receipt = (
                await session.get(JobVersion, (job_id, receipt_version))
                if job_id and receipt_version >= 1
                else None
            )
            if receipt is None or not receipt.response_body or not receipt.response_sha256:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent job operation committed but its exact receipt is unavailable",
                )
            headers = idempotency_receipt_headers(
                record,
                detail="idempotent job operation receipt failed its integrity check",
            )
            try:
                job_receipt = JobResponse.model_validate_json(receipt.response_body)
            except ValueError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent job operation receipt failed its integrity check",
                ) from exc
            if (
                not compare_digest(
                    receipt.response_sha256,
                    sha256(receipt.response_body.encode()).hexdigest(),
                )
                or job_receipt.id != receipt.job_id
                or job_receipt.version != receipt.version
                or headers.get("ETag") != job_receipt.etag
            ):
                raise HTTPException(
                    status_code=503,
                    detail="idempotent job operation receipt failed its integrity check",
                )
            headers["Idempotency-Replayed"] = "true"
            return Response(
                content=receipt.response_body,
                status_code=record.response_status,
                media_type="application/json",
                headers=headers,
            )
        if record.resource_type == "post_report" and not record.response_body:
            report = await session.scalar(
                select(PostReport).where(
                    PostReport.id == record.resource_id,
                    PostReport.reporter_owner_id == principal.subject,
                )
            )
            if report is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent post report committed but its receipt cannot be reconstructed",
                )
            return Response(
                content=idempotency_replay_json(
                    PostReportResponse(
                        id=report.id,
                        post_id=report.post_id,
                        reason_code=report.reason_code,
                        created_at=report.created_at,
                    )
                ),
                status_code=record.response_status,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        if record.resource_type == "moderation_appeal" and not record.response_body:
            appeal = await session.scalar(
                select(ModerationAppeal).where(
                    ModerationAppeal.id == record.resource_id,
                    ModerationAppeal.subject_owner_id == principal.subject,
                )
            )
            if appeal is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent moderation appeal committed but its receipt cannot be reconstructed",
                )
            return Response(
                content=idempotency_replay_json(
                    ModerationAppealSubjectResponse(
                        id=appeal.id,
                        decision_id=appeal.decision_id,
                        status="submitted",
                        submitted_at=appeal.submitted_at,
                        reviewed_at=None,
                        subject_explanation=None,
                    )
                ),
                status_code=record.response_status,
                media_type="application/json",
                headers={"Idempotency-Replayed": "true"},
            )
        if not record.response_body and record.resource_type in {"profile", "resume"}:
            document_id, separator, version_text = (record.resource_id or "").partition("@")
            version = int(version_text) if separator and version_text.isdigit() else None
            document = await session.scalar(
                select(Document)
                .where(
                    Document.id == document_id,
                    Document.owner_id == principal.subject,
                )
                .options(selectinload(Document.versions))
            )
            if document is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent operation committed but its receipt cannot be reconstructed",
                )
            version_row = (
                next((item for item in document.versions if item.version == version), None)
                if version is not None
                else current_version(document)
            )
            if version_row is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent operation committed but its version receipt cannot be reconstructed",
                )
            try:
                markdown = service(session, request).read_markdown(version_row)
                frontmatter, _ = validate_canonical(document.kind, markdown)
            except StorageIntegrityError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent operation receipt cannot verify canonical storage",
                ) from exc
            document_receipt = DocumentResponse(
                id=document.id,
                kind=cast(DocumentKind, document.kind),
                owner_id=str(frontmatter["owner_id"]),
                identifier=document.public_identifier,
                visibility=cast(Visibility, frontmatter["visibility"]),
                version=version_row.version,
                updated_at=version_row.created_at,
                markdown=markdown,
                markdown_url=markdown_url(document),
                etag=strong_etag(version_row.sha256),
            )
            recovered_headers = representation_headers(version_row, version_row.created_at)
            if record.response_status == 201:
                recovered_headers["Location"] = (
                    f"/v1/profiles/{document.public_identifier}"
                    if document.kind == "profile"
                    else f"/v1/resumes/{document.public_identifier}"
                )
            stored_headers = idempotency_receipt_headers(
                record,
                detail="idempotent operation receipt cannot be reconstructed",
            )
            if stored_headers.get("X-Connectmd-Search") == "queued":
                recovered_headers["X-Connectmd-Search"] = "queued"
            recovered_headers["Idempotency-Replayed"] = "true"
            return Response(
                content=idempotency_replay_json(document_receipt),
                status_code=record.response_status,
                media_type="application/json",
                headers=recovered_headers,
            )
        headers = idempotency_receipt_headers(
            record,
            detail="idempotent operation receipt cannot be reconstructed",
        )
        headers["Idempotency-Replayed"] = "true"
        if record.response_status == 204:
            if record.response_body:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent empty response receipt is inconsistent",
                )
            return Response(status_code=204, headers=headers)
        return Response(
            content=record.response_body,
            status_code=record.response_status,
            media_type="application/json",
            headers=headers,
        )

    async def store_idempotency(
        session: AsyncSession,
        request: Request,
        principal: Principal,
        *,
        key: str | None,
        operation: str,
        fingerprint: str,
        status_code: int,
        body: str,
        headers: dict[str, str],
        resource_type: str,
        resource_id: str,
        provisional_record: IdempotencyRecord | None = None,
        application_context: dict[str, str] | None = None,
        agent_grant_context: dict[str, str] | None = None,
        outreach_context: dict[str, str] | None = None,
    ) -> None:
        if key is None:
            return
        with session.no_autoflush:
            existing = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == principal.subject,
                    IdempotencyRecord.idempotency_key == key,
                )
            )
        if existing is not None:
            if existing.operation != operation or existing.request_hash != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different request",
                )
            if provisional_record is not None and existing is provisional_record:
                # Canonical document writes commit a provisional receipt in the
                # same transaction as the immutable version, then complete its
                # response metadata here. Object identity proves this request
                # owns that receipt; a separately loaded match is a concurrent
                # winner and must never be overwritten.
                existing.response_status = status_code
                existing.response_body = body
                existing.response_headers = json.dumps(headers, sort_keys=True)
                existing.resource_type = resource_type
                existing.resource_id = resource_id
            else:
                # Another matching request committed after this transaction's
                # initial replay check. Discard every pending domain mutation from
                # this loser and return the first request's immutable receipt.
                await session.rollback()
                replay = await idempotency_replay(
                    session,
                    request,
                    principal,
                    key,
                    operation,
                    fingerprint,
                    application_context,
                    agent_grant_context,
                    outreach_context,
                )
                if replay is None:
                    raise HTTPException(
                        status_code=503,
                        detail="concurrent idempotent operation committed but its receipt is unavailable",
                    )
                raise ConcurrentIdempotencyReplay(replay)
        else:
            session.add(
                IdempotencyRecord(
                    owner_id=principal.subject,
                    idempotency_key=key,
                    operation=operation,
                    request_hash=fingerprint,
                    response_status=status_code,
                    response_body=body,
                    response_headers=json.dumps(headers, sort_keys=True),
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
            )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            existing = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == principal.subject,
                    IdempotencyRecord.idempotency_key == key,
                )
            )
            if (
                existing is None
                or existing.operation != operation
                or existing.request_hash != fingerprint
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was concurrently used for a different request",
                ) from exc
            replay = await idempotency_replay(
                session,
                request,
                principal,
                key,
                operation,
                fingerprint,
                application_context,
                agent_grant_context,
                outreach_context,
            )
            if replay is None:
                raise HTTPException(
                    status_code=503,
                    detail="concurrent idempotent operation committed but its receipt is unavailable",
                ) from exc
            raise ConcurrentIdempotencyReplay(replay) from exc

    def organization_etag(row: Organization) -> str:
        return strong_etag(sha256(f"organization:{row.id}:{row.version}".encode()).hexdigest())

    def job_etag(row: Job, organization: Organization) -> str:
        representation_identity = json.dumps(
            {
                "job_id": row.id,
                "job_version": row.version,
                "organization_id": organization.id,
                "organization_name": organization.name,
                "organization_slug": organization.slug,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return strong_etag(sha256(representation_identity.encode()).hexdigest())

    def recruiting_event_has_current_authority(event: OrganizationVerificationEvent) -> bool:
        authority = app.state.settings
        configured_reviewer_id = authority.verification_reviewer_id
        return (
            configured_reviewer_id is not None
            and authority.verification_reviewer_role == "recruiting_verifier"
            and event.actor_role == authority.verification_reviewer_role
            and compare_digest(event.actor_id, configured_reviewer_id)
        )

    async def active_recruiting_verification(
        session: AsyncSession, organization: Organization, now: datetime | None = None
    ) -> OrganizationVerificationEvent | None:
        current = now or datetime.now(UTC)
        row = (
            await session.execute(
                select(
                    OrganizationVerificationEvent,
                    OrganizationVerification,
                    OrganizationVerificationEvidence,
                )
                .join(
                    OrganizationVerification,
                    OrganizationVerificationEvent.verification_id == OrganizationVerification.id,
                )
                .join(
                    OrganizationVerificationEvidence,
                    OrganizationVerificationEvidence.verification_id == OrganizationVerification.id,
                )
                .where(
                    OrganizationVerificationEvent.organization_id == organization.id,
                    OrganizationVerificationEvent.purpose == "recruiting_control",
                )
                .order_by(
                    OrganizationVerificationEvent.occurred_at.desc(),
                    OrganizationVerificationEvent.id.desc(),
                )
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        event, verification, evidence = row
        expires_at = event.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            # SQLite does not round-trip timezone offsets. Treat its stored
            # timestamps as UTC so the trust gate stays deterministic in tests
            # and in deliberately supported local deployments.
            expires_at = expires_at.replace(tzinfo=UTC)
        if (
            event.to_state != "active"
            or expires_at is None
            or expires_at <= current
            or event.policy_version is None
            or not recruiting_event_has_current_authority(event)
            or event.material_claim_digest != verification.material_claim_digest
            or retention_expired(evidence.retention_expires_at, current)
        ):
            return None
        try:
            verify_recruiting_evidence(
                app.state.store,
                claims_from_rows(organization, verification, evidence),
                now=current,
            )
        except RecruitingEvidenceUnavailable:
            return None
        return event

    def active_recruiting_verification_predicate(
        organization_id: Any,
        event: Any,
        verification: Any,
        evidence: Any,
        newer_event: Any,
        current: datetime,
    ) -> Any:
        authority = app.state.settings
        configured_reviewer_id = authority.verification_reviewer_id
        configured_reviewer_role = authority.verification_reviewer_role
        if configured_reviewer_id is None or configured_reviewer_role != "recruiting_verifier":
            return event.id.is_(None)
        newer = (
            select(1)
            .where(
                newer_event.organization_id == organization_id,
                newer_event.purpose == "recruiting_control",
                or_(
                    newer_event.occurred_at > event.occurred_at,
                    and_(
                        newer_event.occurred_at == event.occurred_at,
                        newer_event.id > event.id,
                    ),
                ),
            )
            .exists()
        )
        return and_(
            event.organization_id == organization_id,
            event.purpose == "recruiting_control",
            event.to_state == "active",
            event.expires_at > current,
            event.policy_version.is_not(None),
            event.actor_id == configured_reviewer_id,
            event.actor_role == configured_reviewer_role,
            event.material_claim_digest == verification.material_claim_digest,
            verification.purpose == "recruiting_control",
            evidence.retention_expires_at > current,
            ~newer,
        )

    def active_recruiting_verification_from_join(
        organization: Organization,
        event: OrganizationVerificationEvent,
        verification: OrganizationVerification,
        evidence: OrganizationVerificationEvidence,
        current: datetime,
    ) -> OrganizationVerificationEvent | None:
        expires_at = verification_event_expiry(event)
        if (
            event.to_state != "active"
            or expires_at is None
            or expires_at <= current
            or event.policy_version is None
            or not recruiting_event_has_current_authority(event)
            or event.material_claim_digest != verification.material_claim_digest
            or retention_expired(evidence.retention_expires_at, current)
        ):
            return None
        try:
            verify_recruiting_evidence(
                app.state.store,
                claims_from_rows(organization, verification, evidence),
                now=current,
            )
        except RecruitingEvidenceUnavailable:
            return None
        return event

    def verification_review_headers() -> dict[str, str]:
        return dict(_VERIFICATION_REVIEW_HEADERS)

    def verification_review_error(status_code: int, detail: str) -> NoReturn:
        raise HTTPException(
            status_code=status_code,
            detail=detail,
            headers=verification_review_headers(),
        )

    def require_configured_verification_reviewer(principal: Principal, authority: Settings) -> None:
        if (
            principal.method != "clerk_jwt"
            or principal.is_impersonated
            or authority.verification_reviewer_id is None
            or authority.verification_reviewer_role != "recruiting_verifier"
            or not compare_digest(principal.subject, authority.verification_reviewer_id)
        ):
            verification_review_error(403, "configured recruiting-verifier authority is required")

    def verification_timestamp(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def verification_event_expiry(event: OrganizationVerificationEvent) -> datetime | None:
        if event.expires_at is None:
            return None
        return (
            event.expires_at.replace(tzinfo=UTC)
            if event.expires_at.tzinfo is None
            else event.expires_at
        )

    def verification_effective_state(event: OrganizationVerificationEvent, now: datetime) -> str:
        expires_at = verification_event_expiry(event)
        if event.to_state == "active" and (expires_at is None or expires_at <= now):
            return "expired"
        return event.to_state

    async def current_organization_verification(
        session: AsyncSession,
        verification_id: str,
        *,
        for_update: bool = False,
    ) -> tuple[
        OrganizationVerification,
        Organization,
        OrganizationVerificationEvidence,
        OrganizationVerificationEvent,
    ]:
        verification = await session.get(OrganizationVerification, verification_id)
        if verification is None:
            verification_review_error(404, "verification was not found")
        statement = select(Organization).where(Organization.id == verification.organization_id)
        if for_update:
            statement = statement.with_for_update()
        organization = await session.scalar(statement)
        if organization is None:
            verification_review_error(404, "verification was not found")
        if for_update:
            locked_verification = await session.scalar(
                select(OrganizationVerification)
                .where(OrganizationVerification.id == verification_id)
                .with_for_update()
            )
            if locked_verification is None:
                verification_review_error(404, "verification was not found")
            verification = locked_verification
        latest_statement = (
            select(OrganizationVerificationEvent)
            .where(
                OrganizationVerificationEvent.organization_id == organization.id,
                OrganizationVerificationEvent.purpose == "recruiting_control",
            )
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
        evidence_statement = select(OrganizationVerificationEvidence).where(
            OrganizationVerificationEvidence.verification_id == verification.id
        )
        if for_update:
            latest_statement = latest_statement.with_for_update()
            evidence_statement = evidence_statement.with_for_update()
        latest = await session.scalar(latest_statement)
        evidence = await session.scalar(evidence_statement)
        if latest is None or latest.verification_id != verification.id or evidence is None:
            verification_review_error(404, "verification was not found")
        return verification, organization, evidence, latest

    def reviewer_verification_summary(
        verification: OrganizationVerification,
        organization: Organization,
        evidence: OrganizationVerificationEvidence,
        event: OrganizationVerificationEvent,
        now: datetime,
    ) -> OrganizationVerificationReviewerSummaryResponse:
        return OrganizationVerificationReviewerSummaryResponse(
            verification_id=verification.id,
            organization_slug=organization.slug,
            organization_name=organization.name,
            state=cast(Any, verification_effective_state(event, now)),
            evidence_kind=cast(Any, evidence.evidence_kind),
            evidence_sha256=evidence.artifact_sha256,
            artifact_content_type=cast(Any, evidence.artifact_content_type),
            artifact_size_bytes=evidence.artifact_size_bytes,
            material_claim_digest=verification.material_claim_digest,
            submitted_at=verification_timestamp(verification.created_at),
            updated_at=verification_timestamp(event.occurred_at),
            policy_version=event.policy_version,
            expires_at=verification_event_expiry(event),
        )

    def verified_reviewer_evidence(
        request: Request,
        organization: Organization,
        verification: OrganizationVerification,
        evidence: OrganizationVerificationEvidence,
        *,
        now: datetime,
    ) -> VerifiedRecruitingEvidence:
        try:
            return verify_recruiting_evidence(
                request.app.state.store,
                claims_from_rows(organization, verification, evidence),
                now=now,
            )
        except RecruitingEvidenceUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="verification evidence is unavailable",
                headers=verification_review_headers(),
            ) from exc

    def reviewer_verification_detail(
        verification: OrganizationVerification,
        organization: Organization,
        evidence: OrganizationVerificationEvidence,
        event: OrganizationVerificationEvent,
        verified: VerifiedRecruitingEvidence,
        now: datetime,
    ) -> OrganizationVerificationReviewerDetailResponse:
        return OrganizationVerificationReviewerDetailResponse(
            **reviewer_verification_summary(
                verification, organization, evidence, event, now
            ).model_dump(),
            organization_website_url=organization.website_url,
            organization_material_version=organization.verification_material_version,
            evidence_metadata=dict(verified.metadata),
            evidence_retention_expires_at=verification_timestamp(evidence.retention_expires_at),
            evidence_url=(f"/v1/internal/recruiting-verifications/{verification.id}/evidence"),
            review_etag=strong_etag(verified.review_snapshot_sha256),
        )

    def owner_verification_status_response(
        verification: OrganizationVerification | None,
        event: OrganizationVerificationEvent | None,
        now: datetime,
    ) -> OrganizationVerificationOwnerStatusResponse:
        if verification is None or event is None:
            return OrganizationVerificationOwnerStatusResponse(
                verification_id=None,
                state="unverified",
                submitted_at=None,
                updated_at=None,
                policy_version=None,
                expires_at=None,
            )
        return OrganizationVerificationOwnerStatusResponse(
            verification_id=verification.id,
            state=cast(Any, verification_effective_state(event, now)),
            submitted_at=verification.created_at,
            updated_at=event.occurred_at,
            policy_version=event.policy_version,
            expires_at=verification_event_expiry(event),
        )

    def organization_response(
        row: Organization, active_verification: OrganizationVerificationEvent | None = None
    ) -> OrganizationResponse:
        return OrganizationResponse(
            id=row.id,
            slug=row.slug,
            name=row.name,
            description=row.description,
            website_url=row.website_url,
            visibility=cast(Any, row.visibility),
            recruiting_verification_active=active_verification is not None,
            recruiting_verification_purpose=(
                "recruiting_control" if active_verification is not None else None
            ),
            recruiting_verification_expires_at=(
                (
                    active_verification.expires_at.replace(tzinfo=UTC)
                    if active_verification is not None
                    and active_verification.expires_at is not None
                    and active_verification.expires_at.tzinfo is None
                    else active_verification.expires_at
                )
                if active_verification is not None
                else None
            ),
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
            etag=organization_etag(row),
        )

    def job_response(row: Job, organization: Organization) -> JobResponse:
        return JobResponse(
            id=row.id,
            organization_id=organization.id,
            organization_slug=organization.slug,
            organization_name=organization.name,
            slug=row.slug,
            title=row.title,
            description=row.description,
            location=row.location,
            work_mode=cast(Any, row.work_mode),
            employment_type=cast(Any, row.employment_type),
            status=cast(Any, row.status),
            version=row.version,
            published_at=row.published_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            etag=job_etag(row, organization),
        )

    def job_version(row: Job, organization: Organization, response: JobResponse) -> JobVersion:
        response_body = response.model_dump_json()
        return JobVersion(
            job_id=row.id,
            version=row.version,
            organization_id=organization.id,
            organization_slug=organization.slug,
            organization_name=organization.name,
            slug=row.slug,
            title=row.title,
            description=row.description,
            location=row.location,
            work_mode=row.work_mode,
            employment_type=row.employment_type,
            status=row.status,
            published_at=row.published_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            response_body=response_body,
            response_sha256=sha256(response_body.encode()).hexdigest(),
        )

    def application_response(
        row: Application, job: Job, organization: Organization
    ) -> ApplicationResponse:
        # Applicant owner and actor identifiers stay out of application payloads.
        return ApplicationResponse(
            id=row.id,
            job_id=job.id,
            organization_slug=organization.slug,
            job_slug=job.slug,
            status=cast(Any, row.status),
            snapshot_kind=cast(Any, row.snapshot_document_kind),
            snapshot_identifier=row.snapshot_document_identifier,
            snapshot_version=row.snapshot_document_version,
            snapshot_sha256=row.snapshot_sha256,
            confirmed_at=row.confirmed_at,
            retention_policy_version=row.retention_policy_version,
            retention_expires_at=row.retention_expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            decided_at=row.decided_at,
        )

    def application_creation_response(
        row: Application, job: Job, organization: Organization
    ) -> ApplicationResponse:
        """Reconstruct immutable creation facts after later status changes."""

        return ApplicationResponse(
            id=row.id,
            job_id=job.id,
            organization_slug=organization.slug,
            job_slug=job.slug,
            status="submitted",
            snapshot_kind=cast(Any, row.snapshot_document_kind),
            snapshot_identifier=row.snapshot_document_identifier,
            snapshot_version=row.snapshot_document_version,
            snapshot_sha256=row.snapshot_sha256,
            confirmed_at=verification_timestamp(row.confirmed_at),
            retention_policy_version=row.retention_policy_version,
            retention_expires_at=verification_timestamp(row.retention_expires_at),
            created_at=verification_timestamp(row.created_at),
            updated_at=verification_timestamp(row.created_at),
            decided_at=None,
        )

    def application_detail_response(
        row: Application, job: Job, organization: Organization
    ) -> ApplicationDetailResponse:
        return ApplicationDetailResponse(
            **application_response(row, job, organization).model_dump(), message=row.message
        )

    def application_snapshot_url(row: Application, job: Job, organization: Organization) -> str:
        return f"/v1/organizations/{organization.slug}/jobs/{job.slug}/applications/{row.id}/snapshot.md"

    def application_snapshot_response(
        row: Application, job: Job, organization: Organization, markdown: str
    ) -> ApplicationSnapshotResponse:
        return ApplicationSnapshotResponse(
            application_id=row.id,
            snapshot_kind=cast(Any, row.snapshot_document_kind),
            snapshot_identifier=row.snapshot_document_identifier,
            snapshot_version=row.snapshot_document_version,
            snapshot_sha256=row.snapshot_sha256,
            markdown=markdown,
            markdown_url=application_snapshot_url(row, job, organization),
        )

    def application_snapshot_headers(row: Application) -> dict[str, str]:
        """Representation headers for the immutable Markdown source bytes."""
        digest = b64encode(bytes.fromhex(row.snapshot_sha256)).decode("ascii")
        return {
            "ETag": strong_etag(row.snapshot_sha256),
            "Cache-Control": "no-store",
            "Content-Digest": f"sha-256=:{digest}:",
        }

    def read_application_snapshot(request: Request, row: Application) -> str:
        """Read only the application-owned copy, never a live source document."""
        try:
            expected = request.app.state.store.application_snapshot_relative_path(row.id)
        except ValueError as exc:  # pragma: no cover - ledger ids are UUIDs
            raise HTTPException(
                status_code=404, detail="application snapshot was not found"
            ) from exc
        if row.snapshot_storage_path != expected:
            raise HTTPException(status_code=404, detail="application snapshot was not found")
        if row.snapshot_size_bytes is None:
            raise HTTPException(status_code=404, detail="application snapshot was not found")
        try:
            payload = request.app.state.store.read_verified_bytes(
                expected,
                row.snapshot_sha256,
                expected_size_bytes=row.snapshot_size_bytes,
                max_size_bytes=131_072,
            )
            return payload.decode("utf-8")
        except (StorageIntegrityError, UnicodeDecodeError) as exc:
            # This route is a purpose-bound private read.  Do not disclose whether
            # the backing file is absent, tampered, or otherwise inaccessible.
            raise HTTPException(
                status_code=404, detail="application snapshot was not found"
            ) from exc

    def register_application_snapshot_rollback_cleanup(
        session: AsyncSession, cleanup: RollbackFileCleanup
    ) -> None:
        registered = session.info.setdefault("connectmd_rollback_file_cleanup", set())
        if not isinstance(registered, set):  # pragma: no cover - internal session contract
            raise RuntimeError("rollback cleanup registry is invalid")
        registered.add(cleanup)

    def clear_application_snapshot_rollback_cleanup(
        session: AsyncSession, cleanup: RollbackFileCleanup
    ) -> None:
        registered = session.info.get("connectmd_rollback_file_cleanup")
        if isinstance(registered, set):
            registered.discard(cleanup)

    def artifact_pepper() -> str:
        pepper = settings.api_key_pepper
        if pepper is None or len(pepper.encode("utf-8")) < 16:
            raise HTTPException(
                status_code=503,
                detail="artifact durability authority is unavailable",
            )
        return pepper

    async def classify_artifact_descriptor(
        descriptor: ArtifactDescriptor,
    ) -> Literal["committed", "absent", "uncertain"]:
        """Classify only complete committed authority or proven total absence."""

        pepper = settings.api_key_pepper
        if pepper is None:
            return "uncertain"
        try:
            async with app.state.session_factory() as authority_session:
                await acquire_artifact_intent_lock(authority_session, descriptor.intent_id)
                if descriptor.flow == "canonical_document_version":
                    path_parts = descriptor.canonical_path.split("/")
                    if (
                        len(path_parts) != 4
                        or path_parts[0] not in {"profiles", "resumes"}
                        or path_parts[1] != descriptor.resource_id
                        or path_parts[2] != "versions"
                        or re.fullmatch(r"[0-9]{6}\.md", path_parts[3]) is None
                    ):
                        return "uncertain"
                    version_number = int(path_parts[3].removesuffix(".md"))
                    rows = (
                        await authority_session.scalars(
                            select(DocumentVersion).where(
                                DocumentVersion.document_id == descriptor.resource_id,
                                DocumentVersion.version == version_number,
                            )
                        )
                    ).all()
                    document = await authority_session.get(Document, descriptor.resource_id)
                    receipt_candidates = (
                        await authority_session.scalars(
                            select(IdempotencyRecord).where(
                                IdempotencyRecord.request_hash == descriptor.request_hash,
                                IdempotencyRecord.resource_type.in_(
                                    {"profile", "resume", "proposal_decision"}
                                ),
                            )
                        )
                    ).all()
                    direct_resource_id = f"{descriptor.resource_id}@{version_number}"
                    receipts: list[IdempotencyRecord] = []
                    for candidate in receipt_candidates:
                        if (
                            candidate.resource_type in {"profile", "resume"}
                            and candidate.resource_id == direct_resource_id
                        ):
                            receipts.append(candidate)
                            continue
                        if candidate.resource_type != "proposal_decision":
                            continue
                        proposal_parts = (candidate.resource_id or "").split(":")
                        if (
                            len(proposal_parts) == 5
                            and proposal_parts[1] == "accept"
                            and proposal_parts[2] == descriptor.resource_id
                            and proposal_parts[3] == str(version_number)
                            and proposal_parts[4] == descriptor.payload_sha256
                        ):
                            receipts.append(candidate)
                    path_rows = (
                        await authority_session.scalars(
                            select(DocumentVersion).where(
                                DocumentVersion.storage_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    path_posts = (
                        await authority_session.scalars(
                            select(PostVersion).where(
                                PostVersion.storage_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    erasure_paths = (
                        await authority_session.scalars(
                            select(AccountErasureFileProof).where(
                                AccountErasureFileProof.relative_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    if (
                        not rows
                        and not receipts
                        and not path_rows
                        and not path_posts
                        and not erasure_paths
                    ):
                        create_target_id = CANONICAL_DOCUMENT_CREATE_TARGET_IDS[
                            "profile" if path_parts[0] == "profiles" else "resume"
                        ]
                        if document is None and descriptor.target_id == create_target_id:
                            return "absent"
                        if (
                            document is not None
                            and descriptor.target_id == document.id
                            and descriptor_owner_matches(descriptor, pepper, document.owner_id)
                            and document.kind
                            == ("profile" if path_parts[0] == "profiles" else "resume")
                            and document.current_version + 1 == version_number
                        ):
                            return "absent"
                    if (
                        document is None
                        or len(rows) != 1
                        or len(receipts) != 1
                        or path_rows != rows
                        or path_posts
                        or erasure_paths
                    ):
                        return "uncertain"
                    row = rows[0]
                    receipt = receipts[0]
                    is_proposal_receipt = receipt.resource_type == "proposal_decision"
                    proposal: AgentProposal | None = None
                    proposal_events: list[ChangeEvent] = []
                    proposal_parts = (receipt.resource_id or "").split(":")
                    if is_proposal_receipt and len(proposal_parts) == 5:
                        proposal = await authority_session.get(AgentProposal, proposal_parts[0])
                        proposal_events = (
                            await authority_session.scalars(
                                select(ChangeEvent).where(
                                    ChangeEvent.owner_id == document.owner_id,
                                    ChangeEvent.event_type == "proposal.accepted",
                                    ChangeEvent.resource_type == "proposal",
                                    ChangeEvent.resource_id == proposal_parts[0],
                                )
                            )
                        ).all()
                    try:
                        stored_headers = json.loads(receipt.response_headers)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        return "uncertain"
                    if not isinstance(stored_headers, dict):
                        return "uncertain"
                    if (
                        document.kind not in {"profile", "resume"}
                        or row.document_id != document.id
                        or row.storage_path != descriptor.canonical_path
                        or row.sha256 != descriptor.payload_sha256
                        or receipt.owner_id != document.owner_id
                        or receipt.request_hash != descriptor.request_hash
                        or receipt.response_status != (200 if version_number > 1 else 201)
                        or receipt.response_body
                        or (
                            is_proposal_receipt
                            and (
                                version_number <= 1
                                or receipt.response_status != 200
                                or receipt.operation
                                != f"POST:/v1/proposals/{(receipt.resource_id or '').split(':', 1)[0]}/accept"
                                or proposal is None
                                or proposal.owner_id != document.owner_id
                                or proposal.status != "accepted"
                                or proposal.document_id != document.id
                                or proposal.document_kind != document.kind
                                or proposal.document_identifier != document.public_identifier
                                or proposal.decision_actor_id != row.actor_id
                                or proposal.decided_at is None
                            )
                        )
                        or (
                            not is_proposal_receipt
                            and (
                                receipt.resource_type != document.kind
                                or receipt.operation
                                not in {
                                    (
                                        f"POST:/v1/{document.kind}s"
                                        if version_number == 1
                                        else f"PUT:/v1/{document.kind}s/{document.public_identifier}"
                                    ),
                                    (
                                        f"MCP:create_document:{document.kind}"
                                        if version_number == 1
                                        else f"MCP:update_document:{document.kind}:{document.public_identifier}"
                                    ),
                                }
                            )
                        )
                        or not descriptor_owner_matches(descriptor, pepper, document.owner_id)
                        or descriptor.target_id
                        not in {
                            CANONICAL_DOCUMENT_CREATE_TARGET_IDS[document.kind],
                            document.id,
                        }
                        or (
                            descriptor.target_id
                            == CANONICAL_DOCUMENT_CREATE_TARGET_IDS[document.kind]
                            and (
                                version_number != 1
                                or descriptor.resource_id != descriptor.intent_id
                            )
                        )
                        or (descriptor.target_id == document.id and version_number <= 1)
                        or derive_artifact_intent_uuid(
                            pepper,
                            flow="canonical_document_version",
                            owner_id=document.owner_id,
                            target_id=descriptor.target_id,
                            idempotency_key=receipt.idempotency_key,
                        )
                        != descriptor.intent_id
                    ):
                        return "uncertain"
                    canonical = app.state.store.read_verified_bytes(
                        descriptor.canonical_path,
                        descriptor.payload_sha256,
                        expected_size_bytes=descriptor.payload_size_bytes,
                        max_size_bytes=canonical_document_max_utf8_bytes(),
                    ).decode("utf-8")
                    frontmatter, _ = validate_canonical(document.kind, canonical)
                    event_type = "document.created" if version_number == 1 else "document.updated"
                    events = (
                        await authority_session.scalars(
                            select(ChangeEvent).where(
                                ChangeEvent.owner_id == document.owner_id,
                                ChangeEvent.event_type == event_type,
                                ChangeEvent.resource_type == document.kind,
                                ChangeEvent.resource_id == document.id,
                            )
                        )
                    ).all()
                    expected_payload = json.dumps(
                        {
                            "identifier": document.public_identifier,
                            "version": version_number,
                            "visibility": frontmatter["visibility"],
                            "etag": strong_etag(row.sha256),
                        },
                        sort_keys=True,
                    )
                    matching_events = [
                        event
                        for event in events
                        if event.payload == expected_payload
                        and event.actor_id == row.actor_id
                        and event.actor_method == row.actor_method
                        and event.grant_id == row.grant_id
                        and verification_timestamp(event.occurred_at)
                        == verification_timestamp(row.created_at)
                    ]
                    if is_proposal_receipt:
                        expected_proposal_payload = json.dumps(
                            {"document_id": document.id, "status": "accepted"}, sort_keys=True
                        )
                        matching_proposal_events = [
                            event
                            for event in proposal_events
                            if proposal is not None
                            and event.payload == expected_proposal_payload
                            and event.actor_id == row.actor_id
                            and event.actor_method == "clerk_jwt"
                            and event.grant_id == proposal.submitter_grant_id
                            and proposal.decided_at is not None
                            and verification_timestamp(event.occurred_at)
                            == verification_timestamp(proposal.decided_at)
                        ]
                        if len(matching_proposal_events) != 1:
                            return "uncertain"
                    expected_headers = representation_headers(row, row.created_at)
                    if is_proposal_receipt:
                        expected_headers = {
                            "ETag": strong_etag(row.sha256),
                            "X-Connectmd-Search": "queued",
                        }
                    elif version_number == 1:
                        expected_headers.update(
                            {
                                "Location": (
                                    f"/v1/profiles/{document.public_identifier}"
                                    if document.kind == "profile"
                                    else f"/v1/resumes/{document.public_identifier}"
                                ),
                                "X-Connectmd-Search": "queued",
                            }
                        )
                    else:
                        expected_headers["X-Connectmd-Search"] = "queued"
                    if (
                        len(matching_events) != 1
                        or stored_headers not in ({}, expected_headers)
                        or frontmatter.get("id") != document.id
                        or frontmatter.get("owner_id") != public_owner_id(document.owner_id)
                        or frontmatter.get("version") != version_number
                        or frontmatter.get("handle" if document.kind == "profile" else "slug")
                        != document.public_identifier
                    ):
                        return "uncertain"
                    return "committed"

                if descriptor.flow == "professional_post":
                    rows = (
                        await authority_session.scalars(
                            select(Post).where(Post.id == descriptor.resource_id)
                        )
                    ).all()
                    versions = (
                        await authority_session.scalars(
                            select(PostVersion).where(
                                PostVersion.post_id == descriptor.resource_id,
                                PostVersion.version == 1,
                            )
                        )
                    ).all()
                    receipts = (
                        await authority_session.scalars(
                            select(IdempotencyRecord).where(
                                IdempotencyRecord.resource_type == "post",
                                IdempotencyRecord.resource_id == descriptor.resource_id,
                            )
                        )
                    ).all()
                    path_posts = (
                        await authority_session.scalars(
                            select(PostVersion).where(
                                PostVersion.storage_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    path_documents = (
                        await authority_session.scalars(
                            select(DocumentVersion).where(
                                DocumentVersion.storage_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    erasure_paths = (
                        await authority_session.scalars(
                            select(AccountErasureFileProof).where(
                                AccountErasureFileProof.relative_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    if (
                        not rows
                        and not versions
                        and not receipts
                        and not path_posts
                        and not path_documents
                        and not erasure_paths
                    ):
                        return "absent"
                    if (
                        len(rows) != 1
                        or len(versions) != 1
                        or len(receipts) != 1
                        or path_posts != versions
                        or path_documents
                        or erasure_paths
                    ):
                        return "uncertain"
                    post = rows[0]
                    version_row = versions[0]
                    receipt = receipts[0]
                    try:
                        stored_headers = json.loads(receipt.response_headers)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        return "uncertain"
                    expected_headers = {
                        **post_representation_headers(post),
                        "Location": f"/v1/posts/{post.id}",
                    }
                    if (
                        descriptor.intent_id != descriptor.resource_id
                        or descriptor.target_id != PROFESSIONAL_POST_CREATE_TARGET_ID
                        or post.owner_id != receipt.owner_id
                        or post.current_version != 1
                        or post.storage_path != descriptor.canonical_path
                        or post.sha256 != descriptor.payload_sha256
                        or version_row.storage_path != descriptor.canonical_path
                        or version_row.sha256 != descriptor.payload_sha256
                        or receipt.operation != "POST:/v1/posts"
                        or receipt.response_status != 201
                        or stored_headers != expected_headers
                        or receipt.request_hash != descriptor.request_hash
                        or not descriptor_owner_matches(descriptor, pepper, post.owner_id)
                        or derive_artifact_intent_uuid(
                            pepper,
                            flow="professional_post",
                            owner_id=post.owner_id,
                            target_id=PROFESSIONAL_POST_CREATE_TARGET_ID,
                            idempotency_key=receipt.idempotency_key,
                        )
                        != descriptor.intent_id
                    ):
                        return "uncertain"
                    synthetic_request = Request(
                        {"type": "http", "method": "GET", "path": "/", "headers": [], "app": app}
                    )
                    canonical, frontmatter = verified_post_markdown(post, synthetic_request)
                    expected_response_body = post_response(
                        post,
                        synthetic_request,
                        markdown=canonical,
                        frontmatter=frontmatter,
                    ).model_dump_json()
                    events = (
                        await authority_session.scalars(
                            select(ChangeEvent).where(
                                ChangeEvent.owner_id == post.owner_id,
                                ChangeEvent.event_type == "post.published",
                                ChangeEvent.resource_type == "post",
                                ChangeEvent.resource_id == post.id,
                            )
                        )
                    ).all()
                    expected_payload = json.dumps(
                        {"version": 1, "etag": strong_etag(post.sha256)}, sort_keys=True
                    )
                    if (
                        len(events) != 1
                        or (
                            receipt.response_body
                            and not compare_digest(receipt.response_body, expected_response_body)
                        )
                        or events[0].payload != expected_payload
                        or events[0].actor_id != post.owner_id
                        or events[0].actor_method != "clerk_jwt"
                        or events[0].grant_id is not None
                        or verification_timestamp(events[0].occurred_at)
                        != verification_timestamp(post.created_at)
                        or frontmatter.get("id") != post.id
                        or frontmatter.get("version") != 1
                        or canonical.encode("utf-8")
                        != app.state.store.read_verified_bytes(
                            descriptor.canonical_path,
                            descriptor.payload_sha256,
                            expected_size_bytes=descriptor.payload_size_bytes,
                            max_size_bytes=10_240,
                        )
                    ):
                        return "uncertain"
                    return "committed"

                if descriptor.flow == "application_snapshot":
                    target_job = await authority_session.get(Job, descriptor.target_id)
                    if target_job is None:
                        return "uncertain"
                    organization = await authority_session.scalar(
                        select(Organization)
                        .where(Organization.id == target_job.organization_id)
                        .with_for_update()
                    )
                    job = await authority_session.scalar(
                        select(Job).where(Job.id == descriptor.target_id).with_for_update()
                    )
                    if (
                        organization is None
                        or job is None
                        or job.organization_id != organization.id
                    ):
                        return "uncertain"
                    rows = (
                        await authority_session.scalars(
                            select(Application).where(Application.id == descriptor.resource_id)
                        )
                    ).all()
                    receipts = (
                        await authority_session.scalars(
                            select(IdempotencyRecord).where(
                                IdempotencyRecord.resource_type == "application",
                                IdempotencyRecord.resource_id == descriptor.resource_id,
                            )
                        )
                    ).all()
                    path_applications = (
                        await authority_session.scalars(
                            select(Application).where(
                                Application.snapshot_storage_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    path_evidence = (
                        await authority_session.scalars(
                            select(OrganizationVerificationEvidence).where(
                                OrganizationVerificationEvidence.storage_path
                                == descriptor.canonical_path
                            )
                        )
                    ).all()
                    document_paths = (
                        await authority_session.scalars(
                            select(DocumentVersion).where(
                                DocumentVersion.storage_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    erasure_paths = (
                        await authority_session.scalars(
                            select(AccountErasureFileProof).where(
                                AccountErasureFileProof.relative_path == descriptor.canonical_path
                            )
                        )
                    ).all()
                    if (
                        not rows
                        and not receipts
                        and not path_applications
                        and not path_evidence
                        and not document_paths
                        and not erasure_paths
                    ):
                        return "absent"
                    if (
                        len(rows) != 1
                        or len(receipts) != 1
                        or path_applications != rows
                        or path_evidence
                        or document_paths
                        or erasure_paths
                    ):
                        return "uncertain"
                    row = rows[0]
                    record = receipts[0]
                    if (
                        row.id != descriptor.intent_id
                        or row.job_id != descriptor.target_id
                        or row.snapshot_storage_path != descriptor.canonical_path
                        or row.snapshot_sha256 != descriptor.payload_sha256
                        or row.snapshot_size_bytes != descriptor.payload_size_bytes
                        or record.request_hash != descriptor.request_hash
                        or derive_artifact_intent_uuid(
                            pepper,
                            flow="application_snapshot",
                            owner_id=row.applicant_owner_id,
                            target_id=row.job_id,
                            idempotency_key=record.idempotency_key,
                        )
                        != descriptor.intent_id
                        or not descriptor_owner_matches(descriptor, pepper, row.applicant_owner_id)
                    ):
                        return "uncertain"
                    synthetic_request = Request(
                        {"type": "http", "method": "GET", "path": "/", "headers": [], "app": app}
                    )
                    await reconstruct_application_creation(
                        authority_session, synthetic_request, record
                    )
                    return "committed"

                organization = await authority_session.scalar(
                    select(Organization)
                    .where(Organization.id == descriptor.target_id)
                    .with_for_update()
                )
                if organization is None:
                    return "uncertain"
                rows = (
                    await authority_session.scalars(
                        select(OrganizationVerification).where(
                            OrganizationVerification.id == descriptor.resource_id
                        )
                    )
                ).all()
                receipts = (
                    await authority_session.scalars(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.resource_type == "organization_verification",
                            IdempotencyRecord.resource_id == descriptor.resource_id,
                        )
                    )
                ).all()
                path_evidence = (
                    await authority_session.scalars(
                        select(OrganizationVerificationEvidence).where(
                            OrganizationVerificationEvidence.storage_path
                            == descriptor.canonical_path
                        )
                    )
                ).all()
                path_applications = (
                    await authority_session.scalars(
                        select(Application).where(
                            Application.snapshot_storage_path == descriptor.canonical_path
                        )
                    )
                ).all()
                document_paths = (
                    await authority_session.scalars(
                        select(DocumentVersion).where(
                            DocumentVersion.storage_path == descriptor.canonical_path
                        )
                    )
                ).all()
                erasure_paths = (
                    await authority_session.scalars(
                        select(AccountErasureFileProof).where(
                            AccountErasureFileProof.relative_path == descriptor.canonical_path
                        )
                    )
                ).all()
                if (
                    not rows
                    and not receipts
                    and not path_evidence
                    and not path_applications
                    and not document_paths
                    and not erasure_paths
                ):
                    return "absent"
                if (
                    len(rows) != 1
                    or len(receipts) != 1
                    or len(path_evidence) != 1
                    or path_applications
                    or document_paths
                    or erasure_paths
                ):
                    return "uncertain"
                verification = rows[0]
                evidence = path_evidence[0]
                record = receipts[0]
                if (
                    verification.id != descriptor.intent_id
                    or verification.organization_id != descriptor.target_id
                    or evidence.verification_id != verification.id
                    or evidence.storage_path != descriptor.canonical_path
                    or evidence.artifact_sha256 != descriptor.payload_sha256
                    or evidence.artifact_size_bytes != descriptor.payload_size_bytes
                    or record.request_hash != descriptor.request_hash
                    or derive_artifact_intent_uuid(
                        pepper,
                        flow="organization_verification_evidence",
                        owner_id=verification.submitted_by_owner_id,
                        target_id=verification.organization_id,
                        idempotency_key=record.idempotency_key,
                    )
                    != descriptor.intent_id
                    or not descriptor_owner_matches(
                        descriptor, pepper, verification.submitted_by_owner_id
                    )
                ):
                    return "uncertain"
                synthetic_request = Request(
                    {"type": "http", "method": "GET", "path": "/", "headers": [], "app": app}
                )
                await reconstruct_verification_submission(
                    authority_session, synthetic_request, record
                )
                return "committed"
        except asyncio.CancelledError:
            raise
        except Exception:
            return "uncertain"

    async def reconcile_commit_failure(
        descriptor: ArtifactDescriptor,
        request: Request,
        principal: Principal,
        *,
        key: str,
        operation: str,
        fingerprint: str,
    ) -> Response:
        outcome = await request.app.state.artifact_reconciler.reconcile_descriptor(
            descriptor, respect_grace=False, gate_held=True
        )
        if outcome == "committed":
            async with request.app.state.session_factory() as replay_session:
                replay = await idempotency_replay(
                    replay_session,
                    request,
                    principal,
                    key,
                    operation,
                    fingerprint,
                )
            if replay is not None:
                return replay
        raise HTTPException(
            status_code=503,
            detail="artifact transaction outcome is unavailable",
        )

    async def classify_incomplete_artifact_stage(
        intent_id: str, _payload_path: str
    ) -> Literal["committed", "absent", "uncertain"]:
        """Incomplete payload-only stages predate promotion; total DB absence is authoritative."""

        try:
            async with app.state.session_factory() as authority_session:
                await acquire_artifact_intent_lock(authority_session, intent_id)
                application = await authority_session.get(Application, intent_id)
                verification = await authority_session.get(OrganizationVerification, intent_id)
                document = await authority_session.get(Document, intent_id)
                post = await authority_session.get(Post, intent_id)
                receipts = (
                    await authority_session.scalars(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.resource_id == intent_id,
                            IdempotencyRecord.resource_type.in_(
                                {
                                    "application",
                                    "organization_verification",
                                    "post",
                                }
                            ),
                        )
                    )
                ).all()
                document_receipts = (
                    await authority_session.scalars(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.resource_id.like(f"{intent_id}@%"),
                            IdempotencyRecord.resource_type.in_(
                                {"profile", "resume", "proposal_decision"}
                            ),
                        )
                    )
                ).all()
                return (
                    "absent"
                    if application is None
                    and verification is None
                    and document is None
                    and post is None
                    and not receipts
                    and not document_receipts
                    else "uncertain"
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return "uncertain"

    async def commit_artifact_transaction(
        session: AsyncSession,
        request: Request,
        principal: Principal,
        descriptor: ArtifactDescriptor,
        cleanup: RollbackFileCleanup,
        *,
        key: str,
        operation: str,
        fingerprint: str,
        status_code: int,
        body: str,
        resource_type: str,
        resource_id: str,
    ) -> Response | None:
        session.add(
            IdempotencyRecord(
                owner_id=principal.subject,
                idempotency_key=key,
                operation=operation,
                request_hash=fingerprint,
                response_status=status_code,
                response_body=body,
                response_headers="{}",
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )
        try:
            await session.flush()
        except asyncio.CancelledError:
            raise
        except BaseException:
            await session.rollback()
            return await reconcile_commit_failure(
                descriptor,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
            )
        # The exact cleanup remains armed through graph/receipt flush, but is
        # cleared before commit: an exception after the database commits must
        # never let request-finalization delete committed canonical bytes.
        clear_application_snapshot_rollback_cleanup(session, cleanup)
        try:
            await session.commit()
        except asyncio.CancelledError:
            raise
        except BaseException:
            await session.rollback()
            return await reconcile_commit_failure(
                descriptor,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
            )
        try:
            request.app.state.store.retire_staged_artifact(
                descriptor.staged_payload_path, descriptor.staged_descriptor_path
            )
        except StorageIntegrityError:
            # The committed graph is authoritative; the bounded reconciler may
            # retire this exact signed stage pair later.
            pass
        return None

    async def organization_by_slug(
        session: AsyncSession, slug: str, *, for_update: bool = False
    ) -> Organization:
        statement = select(Organization).where(Organization.slug == slug)
        if for_update:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None:
            raise HTTPException(status_code=404, detail="organization was not found")
        return row

    async def job_by_slug(
        session: AsyncSession,
        organization: Organization,
        slug: str,
        *,
        for_update: bool = False,
    ) -> Job:
        statement = select(Job).where(Job.organization_id == organization.id, Job.slug == slug)
        if for_update:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None:
            raise HTTPException(status_code=404, detail="job was not found")
        return row

    async def organization_role(
        session: AsyncSession, organization: Organization, principal: Principal
    ) -> Literal["owner", "admin"] | None:
        if organization.owner_id == principal.subject:
            return "owner"
        member = await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.member_owner_id == principal.subject,
            )
        )
        return (
            "admin"
            if member is not None and member.role == "admin" and member.status == "active"
            else None
        )

    def assert_organization_grant_resource(
        principal: Principal, organization: Organization
    ) -> None:
        if principal.method != "agent_grant":
            return
        if principal.resource_type == "organization" and principal.resource_id == organization.id:
            return
        if principal.resource_type == "owner" and organization.owner_id == principal.subject:
            return
        raise HTTPException(status_code=404, detail="organization was not found")

    async def assert_organization_authority(
        session: AsyncSession,
        organization: Organization,
        principal: Principal,
        *,
        scope: str,
        owner_only: bool = False,
        mutate: bool = True,
    ) -> Literal["owner", "admin"]:
        assert_scope(principal, scope)
        if mutate:
            assert_not_impersonated_clerk(principal)
            assert_direct(principal)
        role = await organization_role(session, organization, principal)
        if role is None or (owner_only and role != "owner"):
            raise HTTPException(status_code=404, detail="organization was not found")
        assert_organization_grant_resource(principal, organization)
        return role

    def employer_inventory_subject_binding(scope: str, subject: str) -> str:
        return sha256(f"connect.md:{scope}:{subject}".encode()).hexdigest()

    def employer_inventory_cursor_encode(
        *, scope: str, subject: str, updated_at: datetime, row_id: str
    ) -> str:
        payload = _cursor_encode(
            {
                "v": 1,
                "scope": scope,
                "subject": employer_inventory_subject_binding(scope, subject),
                "updated_at": updated_at.isoformat(),
                "id": row_id,
            }
        )
        signature = (
            urlsafe_b64encode(
                hmac_new(
                    app.state.employer_inventory_cursor_secret,
                    payload.encode("utf-8"),
                    sha256,
                ).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        return f"{payload}.{signature}"

    def employer_inventory_cursor_decode(
        cursor: str, *, scope: str, subject: str, label: str
    ) -> tuple[datetime, str]:
        try:
            encoded, supplied_signature = cursor.rsplit(".", 1)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{label} cursor is malformed") from exc
        expected_signature = (
            urlsafe_b64encode(
                hmac_new(
                    app.state.employer_inventory_cursor_secret,
                    encoded.encode("utf-8"),
                    sha256,
                ).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        if not compare_digest(supplied_signature, expected_signature):
            raise HTTPException(status_code=400, detail=f"{label} cursor is malformed")
        try:
            payload = _cursor_decode(encoded)
            if (
                payload["v"] != 1
                or payload["scope"] != scope
                or not compare_digest(
                    str(payload["subject"]), employer_inventory_subject_binding(scope, subject)
                )
            ):
                raise ValueError
            updated_at = datetime.fromisoformat(str(payload["updated_at"]))
            row_id = str(payload["id"])
            if not row_id:
                raise ValueError
        except (HTTPException, KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{label} cursor is malformed") from exc
        return updated_at, row_id

    def employer_inventory_cursor_datetime(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def employer_inventory_membership_join(subject: str) -> Any:
        return and_(
            OrganizationMembership.organization_id == Organization.id,
            OrganizationMembership.member_owner_id == subject,
            OrganizationMembership.role == "admin",
            OrganizationMembership.status == "active",
        )

    def employer_organization_statement(subject: str) -> Any:
        return (
            select(Organization, OrganizationMembership)
            .outerjoin(OrganizationMembership, employer_inventory_membership_join(subject))
            .where(
                or_(
                    Organization.owner_id == subject,
                    OrganizationMembership.id.is_not(None),
                )
            )
        )

    def employer_job_statement(subject: str) -> Any:
        return (
            select(Job, Organization, OrganizationMembership)
            .join(Organization, Job.organization_id == Organization.id)
            .outerjoin(OrganizationMembership, employer_inventory_membership_join(subject))
            .where(
                or_(
                    Organization.owner_id == subject,
                    OrganizationMembership.id.is_not(None),
                )
            )
        )

    async def require_employer_inventory_cursor_boundary(
        session: AsyncSession,
        *,
        subject: str,
        scope: str,
        updated_at: datetime,
        row_id: str,
        entity: Literal["organization", "job"],
    ) -> None:
        if entity == "organization":
            row = (
                await session.execute(
                    employer_organization_statement(subject).where(Organization.id == row_id)
                )
            ).first()
            actual_updated_at = row[0].updated_at if row is not None else None
        else:
            row = (
                await session.execute(employer_job_statement(subject).where(Job.id == row_id))
            ).first()
            actual_updated_at = row[0].updated_at if row is not None else None
        if actual_updated_at is None or employer_inventory_cursor_datetime(actual_updated_at) != (
            employer_inventory_cursor_datetime(updated_at)
        ):
            raise HTTPException(status_code=409, detail=f"{scope} cursor is stale")

    async def assert_active_employer_application_authority(
        session: AsyncSession,
        organization: Organization,
        principal: Principal,
    ) -> Literal["owner", "admin"]:
        """Authorize private application access only while recruiting control is live.

        A former owner or member must not retain private applicant access merely
        because their organization membership still exists after its public
        recruiting authority has expired, been suspended, or otherwise failed
        verification.  Use the same opaque 404 surface as an absent employer.
        """
        require_application_human(principal)
        role = await organization_role(session, organization, principal)
        if role is None:
            raise HTTPException(status_code=404, detail="organization was not found")
        if (
            organization.visibility != "public"
            or await active_recruiting_verification(session, organization) is None
        ):
            raise HTTPException(status_code=404, detail="organization was not found")
        return role

    def require_recruiting_release() -> None:
        if not settings.recruiting_enabled:
            raise HTTPException(status_code=404, detail="recruiting is unavailable")

    async def can_read_organization(
        session: AsyncSession, organization: Organization, principal: Principal | None
    ) -> None:
        if (
            settings.recruiting_enabled
            and organization.visibility == "public"
            and await active_recruiting_verification(session, organization)
        ):
            return
        if principal is None:
            raise HTTPException(status_code=404, detail="organization was not found")
        await assert_organization_authority(
            session, organization, principal, scope="organizations:read", mutate=False
        )

    async def can_read_job(
        session: AsyncSession,
        organization: Organization,
        job: Job,
        principal: Principal | None,
    ) -> None:
        if (
            settings.recruiting_enabled
            and organization.visibility == "public"
            and job.status == "published"
            and await active_recruiting_verification(session, organization)
        ):
            return
        if principal is None:
            raise HTTPException(status_code=404, detail="job was not found")
        await assert_organization_authority(
            session, organization, principal, scope="jobs:read", mutate=False
        )

    def require_if_match(request: Request, current_etag: str, resource: str) -> None:
        supplied = request.headers.get("If-Match")
        if supplied is None:
            raise HTTPException(
                status_code=428, detail=f"If-Match is required to update {resource}"
            )
        if not if_match_satisfied(supplied, current_etag):
            raise HTTPException(status_code=412, detail=f"If-Match does not match {resource}")

    def retention_expired(value: datetime, now: datetime | None = None) -> bool:
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized <= (now or datetime.now(UTC))

    def social_retention_expires_at(now: datetime) -> datetime:
        return now + timedelta(days=365)

    def require_social_human(principal: Principal, scope: str) -> None:
        assert_scope(principal, scope)
        if principal.method != "clerk_jwt" or principal.is_impersonated:
            raise HTTPException(
                status_code=403,
                detail="this private social operation requires a signed-in human",
            )

    def require_application_human(principal: Principal) -> None:
        if principal.method != "clerk_jwt" or principal.is_impersonated:
            raise HTTPException(
                status_code=403,
                detail="application access requires a signed-in human",
            )

    def public_change_event_actor_id(row: ChangeEvent) -> str:
        if row.actor_method == "clerk_jwt":
            return public_owner_id(row.actor_id)
        return row.actor_id

    def public_change_event_payload(
        row: ChangeEvent, *, non_clerk_viewer: bool = False
    ) -> dict[str, Any]:
        """Keep raw owner subjects out of every owner-facing change-feed payload."""
        payload = json.loads(row.payload)
        if not isinstance(payload, dict):
            return {}
        raw_subjects = {row.owner_id}
        if row.actor_method == "clerk_jwt":
            raw_subjects.add(row.actor_id)
        raw_agent_identifiers = {row.actor_id}
        if row.actor_method == "agent_api_key":
            actor_prefix, separator, api_key_id = row.actor_id.partition(":")
            if actor_prefix == "api-key" and separator and api_key_id:
                raw_agent_identifiers.add(api_key_id)
        if row.grant_id is not None:
            raw_agent_identifiers.add(row.grant_id)
        if non_clerk_viewer and row.resource_type == "agent_grant":
            raw_agent_identifiers.add(row.resource_id)

        def redact(value: Any) -> Any:
            if isinstance(value, str):
                if non_clerk_viewer and value in raw_agent_identifiers:
                    return "agent_grant" if row.actor_method == "agent_grant" else "redacted"
                return public_owner_id(value) if value in raw_subjects else value
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            return value

        return cast(dict[str, Any], redact(payload))

    def public_change_event_projection(
        row: ChangeEvent, *, viewer: Principal
    ) -> ChangeEventResponse:
        """Project change events according to the caller's audit visibility."""
        non_clerk_viewer = viewer.method != "clerk_jwt"
        non_clerk_credential_event = non_clerk_viewer and row.actor_method in {
            "agent_api_key",
            "agent_grant",
        }
        resource_id = (
            "agent_grant"
            if non_clerk_viewer and row.resource_type == "agent_grant"
            else row.resource_id
        )
        return ChangeEventResponse(
            sequence=row.sequence,
            type=row.event_type,
            resource_type=row.resource_type,
            resource_id=resource_id,
            actor_id=(
                row.actor_method
                if non_clerk_credential_event
                else public_change_event_actor_id(row)
            ),
            actor_method=row.actor_method,
            grant_id=None if non_clerk_viewer and row.grant_id is not None else row.grant_id,
            occurred_at=row.occurred_at,
            data=public_change_event_payload(row, non_clerk_viewer=non_clerk_viewer),
        )

    def owner_pair(first_owner_id: str, second_owner_id: str) -> tuple[str, str]:
        if first_owner_id == second_owner_id:
            raise HTTPException(
                status_code=409, detail="cannot create a social relationship with yourself"
            )
        pair_owner_low, pair_owner_high = sorted((first_owner_id, second_owner_id))
        return pair_owner_low, pair_owner_high

    async def connection_blocked(
        session: AsyncSession, first_owner_id: str, second_owner_id: str
    ) -> bool:
        blocked = await session.scalar(
            select(ConnectionBlock).where(
                or_(
                    and_(
                        ConnectionBlock.blocker_owner_id == first_owner_id,
                        ConnectionBlock.blocked_owner_id == second_owner_id,
                    ),
                    and_(
                        ConnectionBlock.blocker_owner_id == second_owner_id,
                        ConnectionBlock.blocked_owner_id == first_owner_id,
                    ),
                )
            )
        )
        return blocked is not None

    def connection_pair_is_not_blocked(pair_owner_low: Any, pair_owner_high: Any) -> Any:
        return (
            ~select(ConnectionBlock.id)
            .where(
                or_(
                    and_(
                        ConnectionBlock.blocker_owner_id == pair_owner_low,
                        ConnectionBlock.blocked_owner_id == pair_owner_high,
                    ),
                    and_(
                        ConnectionBlock.blocker_owner_id == pair_owner_high,
                        ConnectionBlock.blocked_owner_id == pair_owner_low,
                    ),
                )
            )
            .exists()
        )

    def connection_request_response(
        row: ConnectionRequest, principal: Principal
    ) -> ConnectionRequestResponse:
        outbound = row.requester_owner_id == principal.subject
        counterparty = row.recipient_owner_id if outbound else row.requester_owner_id
        counterparty_profile_handle = (
            row.recipient_profile_handle if outbound else row.requester_profile_handle
        )
        return ConnectionRequestResponse(
            id=row.id,
            counterparty_owner_id=public_owner_id(counterparty),
            counterparty_profile_handle=counterparty_profile_handle,
            direction="outbound" if outbound else "inbound",
            messaging_requested=row.requested_messaging,
            messaging_consent=row.recipient_messaging_consent,
            status=cast(Any, row.status),
            created_at=row.created_at,
            decided_at=row.decided_at,
            retention_expires_at=row.retention_expires_at,
        )

    def connection_counterparty(row: Connection, principal: Principal) -> str:
        if principal.subject == row.pair_owner_low:
            return row.pair_owner_high
        if principal.subject == row.pair_owner_high:
            return row.pair_owner_low
        raise HTTPException(status_code=404, detail="connection was not found")

    def connection_response(row: Connection, principal: Principal) -> ConnectionResponse:
        counterparty_profile_handle = (
            row.recipient_profile_handle
            if row.requester_owner_id == principal.subject
            else row.requester_profile_handle
        )
        return ConnectionResponse(
            id=row.id,
            counterparty_owner_id=public_owner_id(connection_counterparty(row, principal)),
            counterparty_profile_handle=counterparty_profile_handle,
            messaging_enabled=row.messaging_enabled,
            created_at=row.created_at,
            retention_expires_at=row.retention_expires_at,
        )

    def conversation_counterparty(row: Conversation, principal: Principal) -> str:
        if principal.subject == row.pair_owner_low:
            return row.pair_owner_high
        if principal.subject == row.pair_owner_high:
            return row.pair_owner_low
        raise HTTPException(status_code=404, detail="conversation was not found")

    def conversation_response(
        row: Conversation, connection: Connection, principal: Principal
    ) -> ConversationResponse:
        return ConversationResponse(
            id=row.id,
            connection_id=row.connection_id,
            counterparty_owner_id=public_owner_id(conversation_counterparty(row, principal)),
            counterparty_profile_handle=(
                connection.recipient_profile_handle
                if connection.requester_owner_id == principal.subject
                else connection.requester_profile_handle
            ),
            created_at=row.created_at,
            retention_expires_at=row.retention_expires_at,
        )

    def message_send_response(row: Message) -> MessageSendResponse:
        return MessageSendResponse(
            id=row.id,
            conversation_id=row.conversation_id,
            created_at=row.created_at,
            retention_expires_at=row.retention_expires_at,
        )

    def message_response(row: Message, principal: Principal) -> MessageResponse:
        return MessageResponse(
            **message_send_response(row).model_dump(),
            sender_owner_id=public_owner_id(row.sender_owner_id),
            direction="sent" if row.sender_owner_id == principal.subject else "received",
            markdown=row.markdown,
        )

    def notification_response(row: Notification) -> NotificationResponse:
        return NotificationResponse(
            id=row.id,
            type=row.type,
            actor_owner_id=(public_owner_id(row.actor_owner_id) if row.actor_owner_id else None),
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            created_at=row.created_at,
            read_at=row.read_at,
        )

    def add_notification(
        session: AsyncSession,
        *,
        recipient_owner_id: str,
        type: str,
        actor_owner_id: str | None,
        resource_type: str,
        resource_id: str,
        now: datetime,
    ) -> None:
        session.add(
            Notification(
                id=new_id(),
                recipient_owner_id=recipient_owner_id,
                type=type,
                actor_owner_id=actor_owner_id,
                resource_type=resource_type,
                resource_id=resource_id,
                created_at=now,
                retention_expires_at=social_retention_expires_at(now),
            )
        )

    def add_social_change_event(
        session: AsyncSession,
        *,
        owner_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
        principal: Principal,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        session.add(
            ChangeEvent(
                owner_id=owner_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=principal.grant_id,
                payload=json.dumps(payload, sort_keys=True),
                occurred_at=now,
            )
        )

    async def active_connection_for_participant(
        session: AsyncSession,
        connection_id: str,
        principal: Principal,
        *,
        for_update: bool = False,
    ) -> Connection:
        statement = select(Connection).where(Connection.id == connection_id)
        if for_update:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None or row.status != "active" or retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="connection was not found")
        counterpart = connection_counterparty(row, principal)
        if await connection_blocked(session, principal.subject, counterpart):
            raise HTTPException(status_code=404, detail="connection was not found")
        return row

    async def active_conversation_for_participant(
        session: AsyncSession,
        conversation_id: str,
        principal: Principal,
        *,
        for_update: bool = False,
    ) -> tuple[Conversation, Connection]:
        conversation = await session.scalar(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        if (
            conversation is None
            or conversation.status != "active"
            or retention_expired(conversation.retention_expires_at)
        ):
            raise HTTPException(status_code=404, detail="conversation was not found")
        connection = await active_connection_for_participant(
            session, conversation.connection_id, principal, for_update=for_update
        )
        if for_update:
            conversation = await session.scalar(
                select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            )
            if (
                conversation is None
                or conversation.status != "active"
                or retention_expired(conversation.retention_expires_at)
            ):
                raise HTTPException(status_code=404, detail="conversation was not found")
        if not connection.messaging_enabled:
            raise HTTPException(status_code=404, detail="conversation was not found")
        conversation_counterparty(conversation, principal)
        return conversation, connection

    async def lock_social_admission(
        session: AsyncSession, connection: Connection, conversation: Conversation | None = None
    ) -> None:
        """Acquire a conditional write lock before admitting a new conversation or message."""
        now = datetime.now(UTC)
        connection_guard = await session.execute(
            update(Connection)
            .where(
                Connection.id == connection.id,
                Connection.status == "active",
                Connection.messaging_enabled.is_(True),
                Connection.retention_expires_at > now,
            )
            .values(updated_at=Connection.updated_at)
            .execution_options(synchronize_session=False)
        )
        if getattr(connection_guard, "rowcount", 0) != 1:
            raise HTTPException(status_code=404, detail="connection was not found")
        if conversation is not None:
            conversation_guard = await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation.id,
                    Conversation.connection_id == connection.id,
                    Conversation.status == "active",
                    Conversation.retention_expires_at > now,
                )
                .values(closed_at=Conversation.closed_at)
                .execution_options(synchronize_session=False)
            )
            if getattr(conversation_guard, "rowcount", 0) != 1:
                raise HTTPException(status_code=404, detail="conversation was not found")

    def current_version(document: Document) -> DocumentVersion:
        return next(
            version for version in document.versions if version.version == document.current_version
        )

    def representation_headers(row: DocumentVersion, modified_at: datetime) -> dict[str, str]:
        normalized = (
            modified_at if modified_at.tzinfo is not None else modified_at.replace(tzinfo=UTC)
        )
        digest = b64encode(bytes.fromhex(row.sha256)).decode("ascii")
        return {
            "ETag": strong_etag(row.sha256),
            "Last-Modified": format_datetime(normalized.astimezone(UTC), usegmt=True),
            "Content-Digest": f"sha-256=:{digest}:",
        }

    def document_response(
        document: Document, document_service: DocumentService
    ) -> DocumentResponse:
        markdown = document_service.read_markdown(current_version(document))
        frontmatter, _ = validate_canonical(document.kind, markdown)
        return DocumentResponse(
            id=document.id,
            kind=cast(DocumentKind, document.kind),
            owner_id=str(frontmatter["owner_id"]),
            identifier=document.public_identifier,
            visibility=cast(Visibility, document.visibility),
            version=document.current_version,
            updated_at=document.updated_at,
            markdown=markdown,
            markdown_url=markdown_url(document),
            etag=strong_etag(current_version(document).sha256),
        )

    def can_read(document: Document, principal: Principal | None) -> None:
        if document.visibility == "public":
            return
        if principal is None or principal.subject != document.owner_id:
            raise HTTPException(status_code=404, detail="document was not found")
        assert_scope(principal, "documents:read")
        assert_document_resource(principal, document)

    async def _create_document_write(
        kind: Literal["profile", "resume"],
        request: Request,
        principal: Principal,
        session: AsyncSession,
        response_headers: Response,
        *,
        markdown: str | None = None,
        key: str | None = None,
        operation: str | None = None,
    ) -> DocumentResponse | Response:
        assert_not_impersonated_clerk(principal)
        assert_scope(principal, "documents:write")
        assert_direct(principal)
        if principal.method == "agent_grant" and principal.resource_type != "owner":
            raise HTTPException(
                status_code=403,
                detail="only an owner-bound grant can create a document",
            )
        request_key = idempotency_key(request, required=True) if key is None else key
        assert request_key is not None
        if key is not None and not _IDEMPOTENCY_KEY_RE.fullmatch(key):
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must contain 1-128 visible ASCII characters",
            )
        request_markdown_value = await request_markdown(request) if markdown is None else markdown
        write_operation = operation or f"POST:/v1/{kind}s"
        fingerprint = _request_fingerprint(write_operation, request_markdown_value)
        replay = await idempotency_replay(
            session, request, principal, request_key, write_operation, fingerprint
        )
        if replay is not None:
            return replay
        receipt = IdempotencyRecord(
            owner_id=principal.subject,
            idempotency_key=request_key,
            operation=write_operation,
            request_hash=fingerprint,
            response_status=201,
            response_body="",
            response_headers="{}",
            resource_type=kind,
        )
        document_service = service(session, request)
        try:
            document = await document_service.create(
                kind,
                request_markdown_value,
                principal.subject,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=principal.grant_id,
                idempotency_record=receipt,
            )
        except MarkdownSizeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except MarkdownValidationError as exc:
            raise HTTPException(status_code=422, detail=PUBLIC_MARKDOWN_VALIDATION_DETAIL) from exc
        except DocumentConflictError as exc:
            replay = await idempotency_replay(
                session, request, principal, request_key, write_operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StorageIntegrityError as exc:
            raise HTTPException(status_code=503, detail="canonical storage is unavailable") from exc
        result = document_response(document, document_service)
        response_headers.headers.update(
            representation_headers(current_version(document), document.updated_at)
        )
        response_headers.headers["Location"] = (
            f"/v1/profiles/{document.public_identifier}"
            if kind == "profile"
            else f"/v1/resumes/{document.public_identifier}"
        )
        response_headers.headers["X-Connectmd-Search"] = "queued"
        await store_idempotency(
            session,
            request,
            principal,
            key=request_key,
            operation=write_operation,
            fingerprint=fingerprint,
            status_code=201,
            body="",
            headers={
                **representation_headers(current_version(document), document.updated_at),
                "Location": response_headers.headers["Location"],
                "X-Connectmd-Search": "queued",
            },
            resource_type=kind,
            resource_id=f"{document.id}@{document.current_version}",
            provisional_record=receipt,
        )
        return result

    async def create_document(
        kind: Literal["profile", "resume"],
        request: Request,
        principal: Principal,
        session: AsyncSession,
        response_headers: Response,
    ) -> DocumentResponse | Response:
        return await _create_document_write(kind, request, principal, session, response_headers)

    async def read_document(
        kind: Literal["profile", "resume"],
        identifier: str,
        request: Request,
        principal: Principal | None,
        session: AsyncSession,
        force_markdown: bool,
        response_headers: Response | None = None,
    ) -> Response | DocumentResponse:
        try:
            document = await service(session, request).get(kind, identifier)
            can_read(document, principal)
            version_row = current_version(document)
            markdown = service(session, request).read_markdown(version_row)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StorageIntegrityError as exc:
            raise HTTPException(
                status_code=503, detail="canonical storage integrity check failed"
            ) from exc
        etag = strong_etag(version_row.sha256)
        read_headers = representation_headers(version_row, document.updated_at)
        if_none_match = request.headers.get("If-None-Match")
        if if_none_match is not None:
            candidates = {value.strip().removeprefix("W/") for value in if_none_match.split(",")}
            if "*" in candidates or etag in candidates:
                return Response(status_code=304, headers=read_headers)
        accepts_markdown = _prefers_markdown(request.headers.get("accept", ""))
        if force_markdown or accepts_markdown:
            headers = {"Cache-Control": "no-store", **read_headers}
            if not force_markdown:
                headers["Vary"] = "Accept"
            return Response(markdown, media_type=MARKDOWN_MEDIA_TYPE, headers=headers)
        if response_headers is not None:
            response_headers.headers["Vary"] = "Accept"
            response_headers.headers["Cache-Control"] = "no-store"
            response_headers.headers.update(read_headers)
        return document_response(document, service(session, request))

    async def _update_document_write(
        kind: Literal["profile", "resume"],
        identifier: str,
        request: Request,
        principal: Principal,
        session: AsyncSession,
        response_headers: Response,
        *,
        markdown: str | None = None,
        if_match: str | None = None,
        key: str | None = None,
        operation: str | None = None,
    ) -> DocumentResponse | Response:
        assert_not_impersonated_clerk(principal)
        assert_scope(principal, "documents:write")
        assert_direct(principal)
        assert_agent_grant_resource_domain(principal, frozenset({"owner", "document"}))
        request_key = idempotency_key(request, required=True) if key is None else key
        assert request_key is not None
        if key is not None and not _IDEMPOTENCY_KEY_RE.fullmatch(key):
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must contain 1-128 visible ASCII characters",
            )
        request_if_match = if_match if if_match is not None else request.headers.get("If-Match")
        if request_if_match is None:
            raise HTTPException(
                status_code=428,
                detail=f"If-Match is required to update {kind}",
            )
        request_markdown_value = await request_markdown(request) if markdown is None else markdown
        write_operation = operation or f"PUT:/v1/{kind}s/{identifier}"
        fingerprint = _request_fingerprint(
            write_operation, request_markdown_value, request_if_match
        )
        document_service = service(session, request)
        try:
            authorized_document = await document_service.get(kind, identifier)
            if authorized_document.owner_id != principal.subject:
                raise DocumentNotFoundError("document was not found")
            assert_document_resource(principal, authorized_document)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        replay = await idempotency_replay(
            session, request, principal, request_key, write_operation, fingerprint
        )
        if replay is not None:
            return replay
        receipt = IdempotencyRecord(
            owner_id=principal.subject,
            idempotency_key=request_key,
            operation=write_operation,
            request_hash=fingerprint,
            response_status=200,
            response_body="",
            response_headers="{}",
            resource_type=kind,
        )
        try:
            document = await document_service.update(
                kind,
                identifier,
                request_markdown_value,
                principal.subject,
                if_match=request_if_match,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=principal.grant_id,
                resource_id=(
                    principal.resource_id
                    if principal.method == "agent_grant" and principal.resource_type == "document"
                    else None
                ),
                idempotency_record=receipt,
            )
            result = document_response(document, document_service)
        except DocumentPreconditionError as exc:
            replay = await idempotency_replay(
                session, request, principal, request_key, write_operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=412, detail=str(exc)) from exc
        except MarkdownSizeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except MarkdownVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MarkdownValidationError as exc:
            raise HTTPException(status_code=422, detail=PUBLIC_MARKDOWN_VALIDATION_DETAIL) from exc
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DocumentForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except StorageIntegrityError as exc:
            raise HTTPException(status_code=503, detail="canonical storage is unavailable") from exc
        response_headers.headers.update(
            representation_headers(current_version(document), document.updated_at)
        )
        response_headers.headers["X-Connectmd-Search"] = "queued"
        await store_idempotency(
            session,
            request,
            principal,
            key=request_key,
            operation=write_operation,
            fingerprint=fingerprint,
            status_code=200,
            body="",
            headers={
                **representation_headers(current_version(document), document.updated_at),
                "X-Connectmd-Search": "queued",
            },
            resource_type=kind,
            resource_id=f"{document.id}@{document.current_version}",
            provisional_record=receipt,
        )
        return result

    async def update_document(
        kind: Literal["profile", "resume"],
        identifier: str,
        request: Request,
        principal: Principal,
        session: AsyncSession,
        response_headers: Response,
    ) -> DocumentResponse | Response:
        return await _update_document_write(
            kind, identifier, request, principal, session, response_headers
        )

    async def ensure_post_moderation_case(
        session: AsyncSession, post: Post, now: datetime
    ) -> ModerationCase:
        case = await session.scalar(
            select(ModerationCase)
            .where(ModerationCase.post_id == post.id, ModerationCase.status == "open")
            .with_for_update()
        )
        if case is not None:
            return case
        case_id = new_id()
        values = {
            "id": case_id,
            "post_id": post.id,
            "subject_owner_id": post.owner_id,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            statement: Any = postgresql_insert(ModerationCase).values(**values)
        elif dialect == "sqlite":
            statement = sqlite_insert(ModerationCase).values(**values)
        else:  # pragma: no cover - supported deployments are PostgreSQL and SQLite tests
            raise HTTPException(status_code=503, detail="moderation case storage is unavailable")
        inserted = await session.execute(
            statement.on_conflict_do_nothing(
                index_elements=["post_id"],
                index_where=text("status = 'open'"),
            )
        )
        if getattr(inserted, "rowcount", 0) == 1:
            session.add(
                ModerationAuditEvent(
                    id=new_id(),
                    case_id=case_id,
                    post_id=post.id,
                    event_type="case_opened",
                    actor_id="system:report-intake",
                    actor_role="system",
                    safe_metadata="{}",
                    occurred_at=now,
                )
            )
        case = await session.scalar(
            select(ModerationCase)
            .where(ModerationCase.post_id == post.id, ModerationCase.status == "open")
            .with_for_update()
        )
        if case is None:  # pragma: no cover - guarded by the partial unique index
            raise HTTPException(
                status_code=503, detail="moderation case creation could not be confirmed"
            )
        return case

    @app.post(
        "/v1/profiles",
        response_model=DocumentResponse,
        status_code=201,
        tags=["documents"],
        openapi_extra=_document_openapi("profile"),
    )
    async def create_profile(
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> DocumentResponse | Response:
        return await create_document("profile", request, principal, session, response)

    @app.post(
        "/v1/resumes",
        response_model=DocumentResponse,
        status_code=201,
        tags=["documents"],
        openapi_extra=_document_openapi("resume"),
    )
    async def create_resume(
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> DocumentResponse | Response:
        return await create_document("resume", request, principal, session, response)

    @app.get(
        "/v1/profiles/{handle}.md",
        tags=["documents"],
        response_class=Response,
        responses=_MARKDOWN_ONLY_RESPONSES,
    )
    async def read_profile_markdown(
        handle: str,
        request: Request,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        result = await read_document("profile", handle, request, principal, session, True)
        assert isinstance(result, Response)
        return result

    @app.get(
        "/v1/resumes/{slug}.md",
        tags=["documents"],
        response_class=Response,
        responses=_MARKDOWN_ONLY_RESPONSES,
    )
    async def read_resume_markdown(
        slug: str,
        request: Request,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        result = await read_document("resume", slug, request, principal, session, True)
        assert isinstance(result, Response)
        return result

    @app.get(
        "/v1/profiles/{handle}",
        response_model=DocumentResponse,
        tags=["documents"],
        responses=_DOCUMENT_READ_RESPONSES,
    )
    async def read_profile(
        handle: str,
        request: Request,
        response: Response,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response | DocumentResponse:
        return await read_document("profile", handle, request, principal, session, False, response)

    @app.get(
        "/v1/resumes/{slug}",
        response_model=DocumentResponse,
        tags=["documents"],
        responses=_DOCUMENT_READ_RESPONSES,
    )
    async def read_resume(
        slug: str,
        request: Request,
        response: Response,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response | DocumentResponse:
        return await read_document("resume", slug, request, principal, session, False, response)

    @app.put(
        "/v1/profiles/{handle}",
        response_model=DocumentResponse,
        tags=["documents"],
        openapi_extra=_document_openapi("profile", update=True),
    )
    async def update_profile(
        handle: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> DocumentResponse | Response:
        return await update_document("profile", handle, request, principal, session, response)

    @app.put(
        "/v1/resumes/{slug}",
        response_model=DocumentResponse,
        tags=["documents"],
        openapi_extra=_document_openapi("resume", update=True),
    )
    async def update_resume(
        slug: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> DocumentResponse | Response:
        return await update_document("resume", slug, request, principal, session, response)

    async def list_versions(
        kind: Literal["profile", "resume"],
        identifier: str,
        request: Request,
        principal: Principal,
        session: AsyncSession,
    ) -> VersionListResponse:
        assert_scope(principal, "documents:read")
        try:
            document = await service(session, request).get(kind, identifier)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if document.owner_id != principal.subject:
            raise HTTPException(status_code=404, detail="document was not found")
        assert_document_resource(principal, document)
        return VersionListResponse(
            id=document.id,
            kind=cast(DocumentKind, document.kind),
            versions=[
                VersionResponse(
                    version=row.version,
                    sha256=row.sha256,
                    actor_id=(row.actor_id if row.grant_id else public_owner_id(row.actor_id)),
                    actor_method=row.actor_method,
                    grant_id=row.grant_id,
                    created_at=row.created_at,
                    markdown_url=markdown_url(document, row.version),
                    etag=strong_etag(row.sha256),
                )
                for row in document.versions
            ],
        )

    async def read_version(
        kind: Literal["profile", "resume"],
        identifier: str,
        version: int,
        request: Request,
        principal: Principal,
        session: AsyncSession,
        force_markdown: bool = False,
        response_headers: Response | None = None,
    ) -> Response | DocumentResponse:
        assert_scope(principal, "documents:read")
        try:
            document = await service(session, request).get(kind, identifier)
            if document.owner_id != principal.subject:
                raise HTTPException(status_code=404, detail="document was not found")
            assert_document_resource(principal, document)
            row = await service(session, request).get_version(document, version)
            markdown = service(session, request).read_markdown(row)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StorageIntegrityError as exc:
            raise HTTPException(
                status_code=503, detail="canonical storage integrity check failed"
            ) from exc
        if force_markdown or _prefers_markdown(request.headers.get("accept", "")):
            headers = {
                "Cache-Control": "no-store",
                **representation_headers(row, row.created_at),
            }
            if not force_markdown:
                headers["Vary"] = "Accept"
            return Response(markdown, media_type=MARKDOWN_MEDIA_TYPE, headers=headers)
        if response_headers is not None:
            response_headers.headers["Vary"] = "Accept"
            response_headers.headers["Cache-Control"] = "no-store"
            response_headers.headers.update(representation_headers(row, row.created_at))
        historical_frontmatter, _ = validate_canonical(document.kind, markdown)
        return DocumentResponse(
            id=document.id,
            kind=cast(DocumentKind, document.kind),
            owner_id=str(historical_frontmatter["owner_id"]),
            identifier=document.public_identifier,
            visibility=cast(Visibility, historical_frontmatter["visibility"]),
            version=row.version,
            updated_at=row.created_at,
            markdown=markdown,
            markdown_url=markdown_url(document, row.version),
            etag=strong_etag(row.sha256),
        )

    @app.get(
        "/v1/profiles/{handle}/versions",
        response_model=VersionListResponse,
        tags=["documents"],
        responses={
            403: _error_response("The agent key lacks document-read scope."),
            404: _error_response("The owned document was not found."),
        },
    )
    async def profile_versions(
        handle: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> VersionListResponse:
        return await list_versions("profile", handle, request, principal, session)

    @app.get(
        "/v1/resumes/{slug}/versions",
        response_model=VersionListResponse,
        tags=["documents"],
        responses={
            403: _error_response("The agent key lacks document-read scope."),
            404: _error_response("The owned document was not found."),
        },
    )
    async def resume_versions(
        slug: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> VersionListResponse:
        return await list_versions("resume", slug, request, principal, session)

    @app.get(
        "/v1/profiles/{handle}/versions/{version}.md",
        tags=["documents"],
        response_class=Response,
        responses=_MARKDOWN_ONLY_RESPONSES,
    )
    async def profile_version_markdown(
        handle: str,
        version: int,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        result = await read_version("profile", handle, version, request, principal, session, True)
        assert isinstance(result, Response)
        return result

    @app.get(
        "/v1/resumes/{slug}/versions/{version}.md",
        tags=["documents"],
        response_class=Response,
        responses=_MARKDOWN_ONLY_RESPONSES,
    )
    async def resume_version_markdown(
        slug: str,
        version: int,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        result = await read_version("resume", slug, version, request, principal, session, True)
        assert isinstance(result, Response)
        return result

    @app.get(
        "/v1/profiles/{handle}/versions/{version}",
        response_model=DocumentResponse,
        tags=["documents"],
        responses=_DOCUMENT_READ_RESPONSES,
    )
    async def profile_version(
        handle: str,
        version: int,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response | DocumentResponse:
        return await read_version(
            "profile", handle, version, request, principal, session, False, response
        )

    @app.get(
        "/v1/resumes/{slug}/versions/{version}",
        response_model=DocumentResponse,
        tags=["documents"],
        responses=_DOCUMENT_READ_RESPONSES,
    )
    async def resume_version(
        slug: str,
        version: int,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response | DocumentResponse:
        return await read_version(
            "resume", slug, version, request, principal, session, False, response
        )

    def post_markdown_url(post: Post) -> str:
        return f"/v1/posts/{post.id}.md"

    def post_html_url(post: Post) -> str:
        return f"/posts/{post.id}"

    def post_datetime(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def canonical_post_datetime(value: Any) -> datetime:
        if not isinstance(value, str):
            raise StorageIntegrityError("canonical post timestamp is malformed")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StorageIntegrityError("canonical post timestamp is malformed") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise StorageIntegrityError("canonical post timestamp lacks a timezone")
        return parsed.astimezone(UTC)

    def verified_post_markdown(post: Post, request: Request) -> tuple[str, dict[str, Any]]:
        markdown = request.app.state.store.read_verified(post.storage_path, post.sha256)
        try:
            frontmatter, _ = validate_canonical("post", markdown)
        except MarkdownValidationError as exc:
            raise StorageIntegrityError("canonical post Markdown is invalid") from exc
        if (
            post.current_version != 1
            or frontmatter.get("id") != post.id
            or frontmatter.get("author_profile_handle") != post.author_profile_handle
            or frontmatter.get("version") != 1
            or canonical_post_datetime(frontmatter.get("published_at"))
            != post_datetime(post.published_at).astimezone(UTC)
        ):
            raise StorageIntegrityError("canonical post Markdown does not match its ledger row")
        return markdown, frontmatter

    def post_representation_headers(
        post: Post, *, updated_at: datetime | None = None
    ) -> dict[str, str]:
        normalized = post_datetime(post.updated_at if updated_at is None else updated_at)
        digest = b64encode(bytes.fromhex(post.sha256)).decode("ascii")
        return {
            "ETag": strong_etag(post.sha256),
            "Last-Modified": format_datetime(normalized.astimezone(UTC), usegmt=True),
            "Content-Digest": f"sha-256=:{digest}:",
        }

    def read_post_markdown(post: Post, request: Request) -> str:
        markdown, _ = verified_post_markdown(post, request)
        return markdown

    def post_response(
        post: Post,
        request: Request,
        *,
        markdown: str | None = None,
        frontmatter: dict[str, Any] | None = None,
    ) -> PostResponse:
        if markdown is None or frontmatter is None:
            markdown, frontmatter = verified_post_markdown(post, request)
        return PostResponse(
            id=post.id,
            author_profile_handle=str(frontmatter["author_profile_handle"]),
            title=str(frontmatter["title"]),
            topics=list(frontmatter["topics"]),
            version=1,
            published_at=post_datetime(post.published_at).astimezone(UTC),
            updated_at=post_datetime(post.updated_at).astimezone(UTC),
            markdown=markdown,
            markdown_url=post_markdown_url(post),
            etag=strong_etag(post.sha256),
        )

    async def current_public_post_author(
        session: AsyncSession, post: Post, *, for_update: bool = False
    ) -> Document | None:
        statement = select(Document).where(
            Document.id == post.author_profile_document_id,
            Document.owner_id == post.owner_id,
            Document.kind == "profile",
            Document.visibility == "public",
            Document.public_identifier == post.author_profile_handle,
        )
        if for_update:
            statement = statement.with_for_update()
        return await session.scalar(statement)

    async def post_content_blocked(
        session: AsyncSession, first_owner_id: str, second_owner_id: str
    ) -> bool:
        blocked = await session.scalar(
            select(PostContentBlock.id).where(
                or_(
                    and_(
                        PostContentBlock.blocker_owner_id == first_owner_id,
                        PostContentBlock.blocked_owner_id == second_owner_id,
                    ),
                    and_(
                        PostContentBlock.blocker_owner_id == second_owner_id,
                        PostContentBlock.blocked_owner_id == first_owner_id,
                    ),
                )
            )
        )
        return blocked is not None

    async def lock_post_graph_pair(
        session: AsyncSession, first_owner_id: str, second_owner_id: str
    ) -> None:
        """Serialize all follow/block transitions for one normalized owner pair."""
        pair_owner_low, pair_owner_high = owner_pair(first_owner_id, second_owner_id)
        values = {
            "pair_owner_low": pair_owner_low,
            "pair_owner_high": pair_owner_high,
            "created_at": datetime.now(UTC),
        }
        dialect_name = session.get_bind().dialect.name
        statement: Any
        if dialect_name == "postgresql":
            statement = postgresql_insert(PostGraphPairLock).values(**values)
        elif dialect_name == "sqlite":
            statement = sqlite_insert(PostGraphPairLock).values(**values)
        else:  # pragma: no cover - supported deployments use PostgreSQL or SQLite
            raise HTTPException(status_code=503, detail="post graph lock backend is unsupported")
        await session.execute(
            statement.on_conflict_do_nothing(index_elements=["pair_owner_low", "pair_owner_high"])
        )
        await session.scalar(
            select(PostGraphPairLock)
            .where(
                PostGraphPairLock.pair_owner_low == pair_owner_low,
                PostGraphPairLock.pair_owner_high == pair_owner_high,
            )
            .with_for_update()
        )

    async def social_graph_pair_rows(
        session: AsyncSession,
        first_owner_id: str,
        second_owner_id: str,
        *,
        for_update: bool = False,
    ) -> tuple[list[ProfileFollow], list[PostContentBlock]]:
        follow_statement = select(ProfileFollow).where(
            or_(
                and_(
                    ProfileFollow.follower_owner_id == first_owner_id,
                    ProfileFollow.followed_owner_id == second_owner_id,
                ),
                and_(
                    ProfileFollow.follower_owner_id == second_owner_id,
                    ProfileFollow.followed_owner_id == first_owner_id,
                ),
            )
        )
        block_statement = select(PostContentBlock).where(
            or_(
                and_(
                    PostContentBlock.blocker_owner_id == first_owner_id,
                    PostContentBlock.blocked_owner_id == second_owner_id,
                ),
                and_(
                    PostContentBlock.blocker_owner_id == second_owner_id,
                    PostContentBlock.blocked_owner_id == first_owner_id,
                ),
            )
        )
        if for_update:
            follow_statement = follow_statement.with_for_update()
            block_statement = block_statement.with_for_update()
        follows = list((await session.scalars(follow_statement)).all())
        blocks = list((await session.scalars(block_statement)).all())
        return follows, blocks

    def post_pair_is_not_blocked(first_owner_id: Any, second_owner_id: Any) -> Any:
        return (
            ~select(PostContentBlock.id)
            .where(
                or_(
                    and_(
                        PostContentBlock.blocker_owner_id == first_owner_id,
                        PostContentBlock.blocked_owner_id == second_owner_id,
                    ),
                    and_(
                        PostContentBlock.blocker_owner_id == second_owner_id,
                        PostContentBlock.blocked_owner_id == first_owner_id,
                    ),
                )
            )
            .exists()
        )

    async def visible_published_post(
        session: AsyncSession, post_id: str, principal: Principal | None
    ) -> Post:
        post = await session.scalar(
            select(Post).where(Post.id == post_id, Post.status == "published")
        )
        if post is None or await current_public_post_author(session, post) is None:
            raise HTTPException(status_code=404, detail="post was not found")
        if (
            principal is not None
            and principal.method == "clerk_jwt"
            and await post_content_blocked(session, principal.subject, post.owner_id)
        ):
            raise HTTPException(status_code=404, detail="post was not found")
        return post

    def public_post_is_eligible(post: Post, profile: Document | None) -> bool:
        return bool(
            profile is not None
            and post.status == "published"
            and post.current_version == 1
            and profile.id == post.author_profile_document_id
            and profile.owner_id == post.owner_id
            and profile.kind == "profile"
            and profile.visibility == "public"
            and profile.public_identifier == post.author_profile_handle
        )

    async def locked_public_post_representations(
        session: AsyncSession,
        request: Request,
        candidates: list[Post],
        *,
        expected_profile: Document | None = None,
    ) -> tuple[list[tuple[Post, str, dict[str, Any]]], Document | None]:
        """Reauthorize public posts while retaining document-then-post row locks.

        The candidate query deliberately has no join so cursors advance over its
        raw chronological rows.  Locks are acquired in one global order: the
        already selected candidate IDs, their referenced profile documents, and
        then the posts themselves.  Account concealment takes Documents before
        Posts too, so this never inverts that lifecycle lock order.
        """
        candidate_ids = sorted({post.id for post in candidates})
        profile_ids = {post.author_profile_document_id for post in candidates}
        if expected_profile is not None:
            profile_ids.add(expected_profile.id)
        locked_profiles = {
            profile.id: profile
            for profile in (
                await session.scalars(
                    select(Document)
                    .where(Document.id.in_(sorted(profile_ids)))
                    .order_by(Document.id.asc())
                    .with_for_update(read=True)
                )
            ).all()
        }
        locked_expected_profile: Document | None = None
        if expected_profile is not None:
            locked_expected_profile = locked_profiles.get(expected_profile.id)
            if (
                locked_expected_profile is None
                or locked_expected_profile.owner_id != expected_profile.owner_id
                or locked_expected_profile.kind != "profile"
                or locked_expected_profile.visibility != "public"
                or locked_expected_profile.public_identifier != expected_profile.public_identifier
            ):
                raise HTTPException(status_code=404, detail="profile was not found")
        locked_posts = (
            {
                post.id: post
                for post in (
                    await session.scalars(
                        select(Post)
                        .where(Post.id.in_(candidate_ids))
                        .order_by(Post.id.asc())
                        .with_for_update(read=True)
                    )
                ).all()
            }
            if candidate_ids
            else {}
        )
        representations: list[tuple[Post, str, dict[str, Any]]] = []
        for candidate in candidates:
            post = locked_posts.get(candidate.id)
            if post is None:
                continue
            profile = locked_profiles.get(post.author_profile_document_id)
            if not public_post_is_eligible(post, profile):
                continue
            markdown, frontmatter = verified_post_markdown(post, request)
            representations.append((post, markdown, frontmatter))
        return representations, locked_expected_profile

    def public_post_summary(post: Post, frontmatter: dict[str, Any]) -> PublicPostSummary:
        return PublicPostSummary(
            id=post.id,
            author_profile_handle=str(frontmatter["author_profile_handle"]),
            title=str(frontmatter["title"]),
            topics=list(frontmatter["topics"]),
            version=1,
            published_at=post_datetime(post.published_at),
            updated_at=post_datetime(post.updated_at),
            html_url=post_html_url(post),
            markdown_url=post_markdown_url(post),
            etag=strong_etag(post.sha256),
        )

    def decode_public_post_cursor(cursor: str) -> tuple[datetime, str]:
        payload = generic_cursor_decode(
            cursor, scope="public_posts", detail="public post cursor is malformed"
        )
        published_at = payload.get("published_at")
        post_id = payload.get("id")
        if (
            set(payload) != {"v", "scope", "published_at", "id"}
            or isinstance(payload.get("v"), bool)
            or not isinstance(payload.get("v"), int)
            or payload.get("v") != 1
            or payload.get("scope") != "public_posts"
            or not isinstance(published_at, str)
            or not isinstance(post_id, str)
            or not post_id
            or len(post_id) > 64
        ):
            raise HTTPException(status_code=400, detail="public post cursor is malformed")
        try:
            parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="public post cursor is malformed") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise HTTPException(status_code=400, detail="public post cursor is malformed")
        return parsed.astimezone(UTC), post_id

    def encode_public_post_cursor(post: Post) -> str:
        return generic_cursor_encode(
            {
                "v": 1,
                "scope": "public_posts",
                "published_at": post_datetime(post.published_at).astimezone(UTC).isoformat(),
                "id": post.id,
            },
            scope="public_posts",
        )

    async def public_post_inventory_page(
        session: AsyncSession,
        request: Request,
        *,
        cursor: str | None,
        limit: int,
    ) -> PublicPostInventoryResponse:
        statement = select(Post).where(Post.status == "published")
        if cursor is not None:
            published_at, post_id = decode_public_post_cursor(cursor)
            statement = statement.where(
                or_(
                    Post.published_at < published_at,
                    and_(Post.published_at == published_at, Post.id < post_id),
                )
            )
        rows = list(
            (
                await session.scalars(
                    statement.order_by(Post.published_at.desc(), Post.id.desc()).limit(limit + 1)
                )
            ).all()
        )
        raw_page = rows[:limit]
        representations, _ = await locked_public_post_representations(session, request, raw_page)
        next_cursor = (
            encode_public_post_cursor(raw_page[-1]) if len(rows) > limit and raw_page else None
        )
        return PublicPostInventoryResponse(
            items=[
                public_post_summary(post, frontmatter) for post, _, frontmatter in representations
            ],
            next_cursor=next_cursor,
        )

    async def public_post_archive_page(
        statement: Any,
        *,
        cursor: str | None,
        limit: int,
        session: AsyncSession,
        request: Request,
        scope: str,
        expected_profile: Document,
        bindings: tuple[str, ...] = (),
    ) -> PostListResponse:
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope=scope,
                bindings=bindings,
                detail="post cursor is malformed",
            )
            try:
                published_at_value = payload["published_at"]
                post_id_value = payload["id"]
                if (
                    payload["v"] != 1
                    or payload["scope"] != scope
                    or not isinstance(published_at_value, str)
                    or not isinstance(post_id_value, str)
                    or not post_id_value
                    or len(post_id_value) > 64
                ):
                    raise ValueError
                published_at = datetime.fromisoformat(published_at_value.replace("Z", "+00:00"))
                if published_at.tzinfo is None or published_at.utcoffset() is None:
                    raise ValueError
                post_id = post_id_value
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="post cursor is malformed") from exc
            statement = statement.where(
                or_(
                    Post.published_at < published_at,
                    and_(Post.published_at == published_at, Post.id < post_id),
                )
            )
        rows = list(
            (
                await session.scalars(
                    statement.order_by(Post.published_at.desc(), Post.id.desc()).limit(limit + 1)
                )
            ).all()
        )
        raw_page = rows[:limit]
        representations, _ = await locked_public_post_representations(
            session,
            request,
            raw_page,
            expected_profile=expected_profile,
        )
        next_cursor = None
        if len(rows) > limit and raw_page:
            last = raw_page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": scope,
                    "published_at": post_datetime(last.published_at).isoformat(),
                    "id": last.id,
                },
                scope=scope,
                bindings=bindings,
            )
        return PostListResponse(
            posts=[
                post_response(post, request, markdown=markdown, frontmatter=frontmatter)
                for post, markdown, frontmatter in representations
            ],
            next_cursor=next_cursor,
        )

    async def consume_post_quota(
        session: AsyncSession,
        *,
        model: type[PostRateBucket] | type[FollowRateBucket] | type[PostReportRateBucket],
        owner_id: str,
        count_field: str,
        limit: int,
        now: datetime,
        message: str,
    ) -> None:
        values = {
            "owner_id": owner_id,
            "bucket_date": now.date(),
            count_field: 1,
            "updated_at": now,
        }
        dialect_name = session.get_bind().dialect.name
        quota_insert: Any
        if dialect_name == "postgresql":
            quota_insert = postgresql_insert(model).values(**values)
        elif dialect_name == "sqlite":
            quota_insert = sqlite_insert(model).values(**values)
        else:  # pragma: no cover - supported deployments use PostgreSQL or SQLite
            raise HTTPException(status_code=503, detail="post quota backend is unsupported")
        column = getattr(model, count_field)
        consumed = await session.scalar(
            quota_insert.on_conflict_do_update(
                index_elements=["owner_id", "bucket_date"],
                set_={count_field: column + 1, "updated_at": now},
                where=column < limit,
            ).returning(column)
        )
        if consumed is None:
            raise HTTPException(status_code=429, detail=message, headers={"Retry-After": "86400"})

    @app.post(
        "/v1/posts",
        response_model=PostResponse,
        status_code=201,
        tags=["posts"],
        openapi_extra={**_post_openapi(), "x-connectmd-human-only": True},
    )
    async def create_post(
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> PostResponse | Response:
        require_social_human(principal, "documents:write")
        markdown = await request_markdown(request)
        key = idempotency_key(request, required=True)
        assert key is not None
        operation = "POST:/v1/posts"
        fingerprint = _request_fingerprint(operation, markdown)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        author = await session.scalar(
            select(Document)
            .where(
                Document.kind == "profile",
                Document.owner_id == principal.subject,
                Document.visibility == "public",
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(1)
            .with_for_update()
        )
        if author is None:
            raise HTTPException(
                status_code=409, detail="a currently public profile is required to publish"
            )
        now = datetime.now(UTC)
        reconciler: ArtifactReconciler = request.app.state.artifact_reconciler
        descriptor: ArtifactDescriptor | None = None
        if reconciler.enabled:
            pepper = artifact_pepper()
            post_id = derive_artifact_intent_uuid(
                pepper,
                flow="professional_post",
                owner_id=principal.subject,
                target_id=PROFESSIONAL_POST_CREATE_TARGET_ID,
                idempotency_key=key,
            )
            session.info["connectmd_artifact_intent_gate"] = await reconciler.acquire_intent_gate(
                post_id
            )
            await acquire_artifact_intent_lock(session, post_id)
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
        else:
            pepper = None
            post_id = new_id()
        try:
            canonical, _ = prepare_client_document(
                "post",
                markdown,
                document_id=post_id,
                owner_id="",
                version=1,
                updated_at=now,
                published_at=now,
                author_profile_handle=author.public_identifier,
            )
        except MarkdownSizeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except MarkdownValidationError as exc:
            raise HTTPException(status_code=422, detail=PUBLIC_MARKDOWN_VALIDATION_DETAIL) from exc
        relative_path = request.app.state.store.relative_path("post", post_id, 1)
        try:
            if reconciler.enabled:
                assert pepper is not None
                descriptor = stage_artifact(
                    request.app.state.store,
                    pepper,
                    flow="professional_post",
                    owner_id=principal.subject,
                    target_id=PROFESSIONAL_POST_CREATE_TARGET_ID,
                    idempotency_key=key,
                    request_hash=fingerprint,
                    canonical_path=relative_path,
                    payload=canonical.encode("utf-8"),
                    max_size_bytes=10_240,
                    resource_id=post_id,
                )
                canonical = request.app.state.store.read_verified_bytes(
                    descriptor.canonical_path,
                    descriptor.payload_sha256,
                    expected_size_bytes=descriptor.payload_size_bytes,
                    max_size_bytes=10_240,
                ).decode("utf-8")
                frontmatter, _ = validate_canonical("post", canonical)
                if (
                    frontmatter.get("id") != post_id
                    or frontmatter.get("author_profile_handle") != author.public_identifier
                    or frontmatter.get("version") != 1
                ):
                    raise StorageIntegrityError(
                        "canonical post does not match its staged authority"
                    )
                published_at = canonical_post_datetime(frontmatter.get("published_at"))
                updated_at = canonical_post_datetime(frontmatter.get("updated_at"))
                digest = descriptor.payload_sha256
            else:
                digest = request.app.state.store.write_immutable(relative_path, canonical)
                published_at = now
                updated_at = now
        except (ArtifactDurabilityUnavailable, MarkdownValidationError, UnicodeDecodeError) as exc:
            if descriptor is None:
                reconciler.mark_unavailable()
            else:
                await reconciler.reconcile_descriptor(
                    descriptor,
                    respect_grace=False,
                    gate_held=True,
                )
            raise HTTPException(status_code=503, detail="canonical storage is unavailable") from exc
        except StorageIntegrityError as exc:
            if descriptor is None:
                reconciler.mark_unavailable()
            else:
                await reconciler.reconcile_descriptor(
                    descriptor,
                    respect_grace=False,
                    gate_held=True,
                )
            raise HTTPException(status_code=503, detail="canonical storage is unavailable") from exc
        post = Post(
            id=post_id,
            owner_id=principal.subject,
            author_profile_document_id=author.id,
            author_profile_handle=author.public_identifier,
            status="published",
            current_version=1,
            sha256=digest,
            storage_path=relative_path,
            published_at=published_at,
            created_at=published_at,
            updated_at=updated_at,
        )
        post.versions.append(
            PostVersion(
                version=1,
                sha256=digest,
                storage_path=relative_path,
                created_at=published_at,
            )
        )
        session.add(post)
        try:
            await consume_post_quota(
                session,
                model=PostRateBucket,
                owner_id=principal.subject,
                count_field="post_count",
                limit=10,
                now=published_at,
                message="post daily limit reached",
            )
            session.add(
                ChangeEvent(
                    owner_id=principal.subject,
                    event_type="post.published",
                    resource_type="post",
                    resource_id=post.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=None,
                    payload=json.dumps({"version": 1, "etag": strong_etag(digest)}, sort_keys=True),
                    occurred_at=published_at,
                )
            )
            post_receipt = IdempotencyRecord(
                owner_id=principal.subject,
                idempotency_key=key,
                operation=operation,
                request_hash=fingerprint,
                response_status=201,
                response_body="",
                response_headers=json.dumps(
                    {**post_representation_headers(post), "Location": f"/v1/posts/{post.id}"},
                    sort_keys=True,
                ),
                resource_type="post",
                resource_id=post.id,
            )
            session.add(post_receipt)
        except BaseException:
            await session.rollback()
            if descriptor is None:
                request.app.state.store.remove_new_file(relative_path)
            else:
                await reconciler.reconcile_descriptor(
                    descriptor,
                    respect_grace=False,
                    gate_held=True,
                )
            raise
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                if descriptor is not None:
                    await reconciler.reconcile_descriptor(
                        descriptor,
                        respect_grace=False,
                        gate_held=True,
                    )
                return replay
            if descriptor is None:
                request.app.state.store.remove_new_file(relative_path)
            else:
                await reconciler.reconcile_descriptor(
                    descriptor,
                    respect_grace=False,
                    gate_held=True,
                )
            raise HTTPException(status_code=409, detail="post publication conflicted") from exc
        except BaseException:
            await session.rollback()
            if descriptor is not None:
                await reconciler.reconcile_descriptor(
                    descriptor,
                    respect_grace=False,
                    gate_held=True,
                )
            raise
        if descriptor is not None:
            try:
                request.app.state.store.retire_staged_artifact(
                    descriptor.staged_payload_path,
                    descriptor.staged_descriptor_path,
                )
            except StorageIntegrityError:
                reconciler.mark_unavailable()
        result = post_response(post, request)
        response.headers.update(post_representation_headers(post))
        response.headers["Location"] = f"/v1/posts/{post.id}"
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body=result.model_dump_json(),
            headers={**post_representation_headers(post), "Location": response.headers["Location"]},
            resource_type="post",
            resource_id=post.id,
            provisional_record=post_receipt,
        )
        return result

    @app.get(
        "/v1/posts",
        response_model=PublicPostInventoryResponse,
        tags=["posts"],
        responses={503: _error_response("Canonical post storage integrity could not be verified.")},
    )
    async def list_public_posts(
        request: Request,
        cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 25,
        session: AsyncSession = Depends(get_session),
    ) -> PublicPostInventoryResponse:
        """Anonymous chronological metadata inventory; it is not a ranked feed."""
        try:
            return await public_post_inventory_page(session, request, cursor=cursor, limit=limit)
        except StorageIntegrityError as exc:
            raise HTTPException(
                status_code=503, detail="canonical storage integrity check failed"
            ) from exc

    async def read_post(
        post_id: str,
        request: Request,
        principal: Principal | None,
        session: AsyncSession,
        force_markdown: bool,
        response: Response | None = None,
    ) -> Response | PostResponse:
        try:
            post_candidates = list(
                (await session.scalars(select(Post).where(Post.id == post_id))).all()
            )
            representations, _ = await locked_public_post_representations(
                session, request, post_candidates
            )
        except StorageIntegrityError as exc:
            raise HTTPException(
                status_code=503, detail="canonical storage integrity check failed"
            ) from exc
        if not representations:
            raise HTTPException(status_code=404, detail="post was not found")
        post, markdown, frontmatter = representations[0]
        if (
            principal is not None
            and principal.method == "clerk_jwt"
            and await post_content_blocked(session, principal.subject, post.owner_id)
        ):
            raise HTTPException(status_code=404, detail="post was not found")
        etag = strong_etag(post.sha256)
        headers = post_representation_headers(post)
        if_none_match = request.headers.get("If-None-Match")
        if if_none_match is not None:
            candidates = {value.strip().removeprefix("W/") for value in if_none_match.split(",")}
            if "*" in candidates or etag in candidates:
                return Response(status_code=304, headers=headers)
        if force_markdown or _prefers_markdown(request.headers.get("accept", "")):
            headers = {"Cache-Control": "no-store", **headers}
            if not force_markdown:
                headers["Vary"] = "Accept"
            return Response(markdown, media_type=MARKDOWN_MEDIA_TYPE, headers=headers)
        if response is not None:
            response.headers.update(headers)
            response.headers["Vary"] = "Accept"
        return post_response(post, request, markdown=markdown, frontmatter=frontmatter)

    @app.get(
        "/v1/posts/{post_id}.md",
        tags=["posts"],
        response_class=Response,
        responses=_POST_MARKDOWN_ONLY_RESPONSES,
    )
    async def read_post_markdown_route(
        post_id: str,
        request: Request,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        result = await read_post(post_id, request, principal, session, True)
        assert isinstance(result, Response)
        return result

    @app.get(
        "/v1/posts/{post_id}",
        response_model=PostResponse,
        tags=["posts"],
        responses=_POST_READ_RESPONSES,
    )
    async def read_post_route(
        post_id: str,
        request: Request,
        response: Response,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response | PostResponse:
        return await read_post(post_id, request, principal, session, False, response)

    @app.delete(
        "/v1/posts/{post_id}",
        status_code=204,
        tags=["posts"],
        openapi_extra=_mutation_openapi_extra(if_match=True, human_only=True),
    )
    async def withdraw_post(
        post_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_social_human(principal, "documents:write")
        key = idempotency_key(request, required=True)
        if_match = request.headers.get("If-Match")
        operation = f"DELETE:/v1/posts/{post_id}"
        fingerprint = _request_fingerprint(operation, "", if_match)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        post = await session.scalar(
            select(Post)
            .where(Post.id == post_id, Post.owner_id == principal.subject)
            .with_for_update()
        )
        if post is None or post.status not in {"published", "withheld"}:
            raise HTTPException(status_code=404, detail="post was not found")
        require_if_match(request, strong_etag(post.sha256), "post")
        now = datetime.now(UTC)
        post.status = "withdrawn"
        post.withdrawn_at = now
        post.updated_at = now
        session.add(
            ChangeEvent(
                owner_id=post.owner_id,
                event_type="post.withdrawn",
                resource_type="post",
                resource_id=post.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=None,
                payload="{}",
                occurred_at=now,
            )
        )
        session.add(
            IdempotencyRecord(
                owner_id=principal.subject,
                idempotency_key=key,
                operation=operation,
                request_hash=fingerprint,
                response_status=204,
                response_body="",
                response_headers="{}",
                resource_type="post",
                resource_id=post.id,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="post withdrawal conflicted") from exc
        return Response(status_code=204)

    async def public_profile_by_handle(
        session: AsyncSession, handle: str, *, for_update: bool = False
    ) -> Document:
        statement = select(Document).where(
            Document.kind == "profile",
            Document.public_identifier == handle,
            Document.visibility == "public",
        )
        if for_update:
            statement = statement.with_for_update()
        profile = await session.scalar(statement)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile was not found")
        return profile

    async def lock_social_target(
        session: AsyncSession,
        principal: Principal,
        profile_handle: str,
        initial_profile: Document,
        *,
        allow_self: bool = False,
    ) -> Document:
        if initial_profile.owner_id == principal.subject:
            if not allow_self:
                raise HTTPException(
                    status_code=409,
                    detail="cannot create a social relationship with yourself",
                )
        else:
            await lock_post_graph_pair(session, principal.subject, initial_profile.owner_id)
        current_profile = await public_profile_by_handle(session, profile_handle, for_update=True)
        if (
            current_profile.id != initial_profile.id
            or current_profile.owner_id != initial_profile.owner_id
            or current_profile.public_identifier != profile_handle
        ):
            raise HTTPException(status_code=409, detail="profile changed during this operation")
        return current_profile

    async def post_page(
        statement: Any,
        *,
        cursor: str | None,
        limit: int,
        session: AsyncSession,
        request: Request,
        scope: str,
        bindings: tuple[str, ...] = (),
    ) -> PostListResponse:
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope=scope,
                bindings=bindings,
                detail="post cursor is malformed",
            )
            try:
                if payload["v"] != 1 or payload["scope"] != scope:
                    raise ValueError
                published_at = datetime.fromisoformat(str(payload["published_at"]))
                post_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="post cursor is malformed") from exc
            statement = statement.where(
                or_(
                    Post.published_at < published_at,
                    and_(Post.published_at == published_at, Post.id < post_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(Post.published_at.desc(), Post.id.desc()).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": scope,
                    "published_at": last.published_at.isoformat(),
                    "id": last.id,
                },
                scope=scope,
                bindings=bindings,
            )
        try:
            result = [post_response(row, request) for row in page]
        except StorageIntegrityError as exc:
            raise HTTPException(
                status_code=503, detail="canonical storage integrity check failed"
            ) from exc
        return PostListResponse(posts=result, next_cursor=next_cursor)

    @app.get("/v1/profiles/{handle}/posts", response_model=PostListResponse, tags=["posts"])
    async def list_profile_posts(
        handle: str,
        request: Request,
        cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> PostListResponse:
        profile = await public_profile_by_handle(session, handle)
        if (
            principal is not None
            and principal.method == "clerk_jwt"
            and await post_content_blocked(session, principal.subject, profile.owner_id)
        ):
            raise HTTPException(status_code=404, detail="profile was not found")
        statement = select(Post).where(
            Post.owner_id == profile.owner_id,
            Post.author_profile_document_id == profile.id,
            Post.status == "published",
        )
        try:
            return await public_post_archive_page(
                statement,
                cursor=cursor,
                limit=limit,
                session=session,
                request=request,
                scope=f"profile_posts:{profile.id}",
                expected_profile=profile,
            )
        except StorageIntegrityError as exc:
            raise HTTPException(
                status_code=503, detail="canonical storage integrity check failed"
            ) from exc

    @app.post(
        "/v1/follows/{profile_handle}",
        response_model=FollowResponse,
        tags=["follows"],
        openapi_extra=_social_openapi_extra(),
    )
    async def follow_profile(
        profile_handle: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> FollowResponse | Response:
        require_social_human(principal, "documents:read")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/follows/{profile_handle}"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        initial_profile = await public_profile_by_handle(session, profile_handle)
        if initial_profile.owner_id == principal.subject:
            raise HTTPException(status_code=409, detail="cannot follow your own profile")
        profile = await lock_social_target(session, principal, profile_handle, initial_profile)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        follows, blocks = await social_graph_pair_rows(
            session, principal.subject, profile.owner_id, for_update=True
        )
        if blocks:
            raise HTTPException(status_code=404, detail="profile was not found")
        existing = next(
            (
                row
                for row in follows
                if row.follower_owner_id == principal.subject
                and row.followed_owner_id == profile.owner_id
            ),
            None,
        )
        now = datetime.now(UTC)
        if existing is None:
            row = ProfileFollow(
                follower_owner_id=principal.subject,
                followed_owner_id=profile.owner_id,
                followed_profile_handle=profile.public_identifier,
                created_at=now,
            )
            session.add(row)
            try:
                await consume_post_quota(
                    session,
                    model=FollowRateBucket,
                    owner_id=principal.subject,
                    count_field="follow_count",
                    limit=100,
                    now=now,
                    message="follow daily limit reached",
                )
                await session.flush()
            except Exception:
                await session.rollback()
                raise
        else:
            row = existing
        result = FollowResponse(
            profile_handle=row.followed_profile_handle, created_at=row.created_at
        )
        response_body = idempotency_replay_json(result)
        follows, blocks = await social_graph_pair_rows(
            session, principal.subject, profile.owner_id, for_update=True
        )
        resource_id = _social_resource_id(
            "follow",
            "follow",
            profile.id,
            _social_receipt_digest(
                operation,
                principal.subject,
                profile.owner_id,
                profile.id,
                profile_handle,
                follows,
                blocks,
                response_body,
            ),
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body=response_body,
            headers={},
            resource_type="social_follow",
            resource_id=resource_id,
        )
        return Response(content=response_body, status_code=200, media_type="application/json")

    @app.delete(
        "/v1/follows/{profile_handle}",
        status_code=204,
        tags=["follows"],
        openapi_extra=_social_openapi_extra(),
    )
    async def unfollow_profile(
        profile_handle: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_social_human(principal, "documents:read")
        key = idempotency_key(request, required=True)
        operation = f"DELETE:/v1/follows/{profile_handle}"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        initial_profile = await public_profile_by_handle(session, profile_handle)
        profile = await lock_social_target(
            session, principal, profile_handle, initial_profile, allow_self=True
        )
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        await social_graph_pair_rows(session, principal.subject, profile.owner_id, for_update=True)
        await session.execute(
            delete(ProfileFollow).where(
                ProfileFollow.follower_owner_id == principal.subject,
                ProfileFollow.followed_owner_id == profile.owner_id,
            )
        )
        await session.flush()
        follows, blocks = await social_graph_pair_rows(
            session, principal.subject, profile.owner_id, for_update=True
        )
        resource_id = _social_resource_id(
            "follow",
            "unfollow",
            profile.id,
            _social_receipt_digest(
                operation,
                principal.subject,
                profile.owner_id,
                profile.id,
                profile_handle,
                follows,
                blocks,
                "",
            ),
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=204,
            body="",
            headers={},
            resource_type="social_follow",
            resource_id=resource_id,
        )
        return Response(status_code=204)

    @app.get(
        "/v1/follows",
        response_model=FollowListResponse,
        tags=["follows"],
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def list_follows(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> FollowListResponse:
        require_social_human(principal, "documents:read")
        statement = (
            select(ProfileFollow)
            .join(
                Document,
                and_(
                    Document.owner_id == ProfileFollow.followed_owner_id,
                    Document.public_identifier == ProfileFollow.followed_profile_handle,
                    Document.kind == "profile",
                    Document.visibility == "public",
                ),
            )
            .where(
                ProfileFollow.follower_owner_id == principal.subject,
                post_pair_is_not_blocked(principal.subject, ProfileFollow.followed_owner_id),
            )
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="follows",
                bindings=cursor_principal_bindings(principal),
                detail="follow cursor is malformed",
            )
            try:
                if payload["v"] != 1 or payload["scope"] != "follows":
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="follow cursor is malformed") from exc
            statement = statement.where(
                or_(
                    ProfileFollow.created_at < created_at,
                    and_(ProfileFollow.created_at == created_at, ProfileFollow.id < row_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(ProfileFollow.created_at.desc(), ProfileFollow.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "follows",
                    "created_at": last.created_at.isoformat(),
                    "id": last.id,
                },
                scope="follows",
                bindings=cursor_principal_bindings(principal),
            )
        return FollowListResponse(
            follows=[
                FollowResponse(
                    profile_handle=row.followed_profile_handle, created_at=row.created_at
                )
                for row in page
            ],
            next_cursor=next_cursor,
        )

    @app.post(
        "/v1/content-blocks/{profile_handle}",
        status_code=204,
        tags=["follows"],
        openapi_extra=_social_openapi_extra(),
    )
    async def block_post_content(
        profile_handle: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_social_human(principal, "documents:read")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/content-blocks/{profile_handle}"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        initial_profile = await public_profile_by_handle(session, profile_handle)
        if initial_profile.owner_id == principal.subject:
            raise HTTPException(status_code=409, detail="cannot block your own profile")
        profile = await lock_social_target(session, principal, profile_handle, initial_profile)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        follows, blocks = await social_graph_pair_rows(
            session, principal.subject, profile.owner_id, for_update=True
        )
        existing = next(
            (
                row
                for row in blocks
                if row.blocker_owner_id == principal.subject
                and row.blocked_owner_id == profile.owner_id
            ),
            None,
        )
        if existing is None:
            session.add(
                PostContentBlock(
                    blocker_owner_id=principal.subject,
                    blocked_owner_id=profile.owner_id,
                    created_at=datetime.now(UTC),
                )
            )
        await session.execute(
            delete(ProfileFollow).where(
                or_(
                    and_(
                        ProfileFollow.follower_owner_id == principal.subject,
                        ProfileFollow.followed_owner_id == profile.owner_id,
                    ),
                    and_(
                        ProfileFollow.follower_owner_id == profile.owner_id,
                        ProfileFollow.followed_owner_id == principal.subject,
                    ),
                )
            )
        )
        await session.flush()
        follows, blocks = await social_graph_pair_rows(
            session, principal.subject, profile.owner_id, for_update=True
        )
        resource_id = _social_resource_id(
            "content_block",
            "block",
            profile.id,
            _social_receipt_digest(
                operation,
                principal.subject,
                profile.owner_id,
                profile.id,
                profile_handle,
                follows,
                blocks,
                "",
            ),
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=204,
            body="",
            headers={},
            resource_type="social_content_block",
            resource_id=resource_id,
        )
        return Response(status_code=204)

    @app.delete(
        "/v1/content-blocks/{profile_handle}",
        status_code=204,
        tags=["follows"],
        openapi_extra=_social_openapi_extra(),
    )
    async def unblock_post_content(
        profile_handle: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_social_human(principal, "documents:read")
        key = idempotency_key(request, required=True)
        operation = f"DELETE:/v1/content-blocks/{profile_handle}"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        initial_profile = await public_profile_by_handle(session, profile_handle)
        profile = await lock_social_target(
            session, principal, profile_handle, initial_profile, allow_self=True
        )
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        await social_graph_pair_rows(session, principal.subject, profile.owner_id, for_update=True)
        await session.execute(
            delete(PostContentBlock).where(
                PostContentBlock.blocker_owner_id == principal.subject,
                PostContentBlock.blocked_owner_id == profile.owner_id,
            )
        )
        await session.flush()
        follows, blocks = await social_graph_pair_rows(
            session, principal.subject, profile.owner_id, for_update=True
        )
        resource_id = _social_resource_id(
            "content_block",
            "unblock",
            profile.id,
            _social_receipt_digest(
                operation,
                principal.subject,
                profile.owner_id,
                profile.id,
                profile_handle,
                follows,
                blocks,
                "",
            ),
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=204,
            body="",
            headers={},
            resource_type="social_content_block",
            resource_id=resource_id,
        )
        return Response(status_code=204)

    @app.get(
        "/v1/profile-post-controls/{profile_handle}",
        response_model=PostControlStateResponse,
        tags=["follows"],
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def profile_post_controls(
        profile_handle: str,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> PostControlStateResponse:
        """Return only the caller's two direct controls for one public profile."""
        require_social_human(principal, "documents:read")
        profile = await public_profile_by_handle(session, profile_handle)
        following = await session.scalar(
            select(ProfileFollow.id).where(
                ProfileFollow.follower_owner_id == principal.subject,
                ProfileFollow.followed_owner_id == profile.owner_id,
                ProfileFollow.followed_profile_handle == profile.public_identifier,
            )
        )
        content_blocked = await session.scalar(
            select(PostContentBlock.id).where(
                PostContentBlock.blocker_owner_id == principal.subject,
                PostContentBlock.blocked_owner_id == profile.owner_id,
            )
        )
        return PostControlStateResponse(
            following=following is not None,
            content_blocked=content_blocked is not None,
        )

    @app.get(
        "/v1/feed",
        response_model=PostListResponse,
        tags=["follows"],
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def feed(
        request: Request,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> PostListResponse:
        require_social_human(principal, "documents:read")
        follows_exact_author_profile = (
            select(ProfileFollow.id)
            .where(
                ProfileFollow.follower_owner_id == principal.subject,
                ProfileFollow.followed_owner_id == Post.owner_id,
                ProfileFollow.followed_profile_handle == Post.author_profile_handle,
            )
            .exists()
        )
        statement = (
            select(Post)
            .join(
                Document,
                and_(
                    Document.id == Post.author_profile_document_id,
                    Document.owner_id == Post.owner_id,
                    Document.kind == "profile",
                    Document.visibility == "public",
                    Document.public_identifier == Post.author_profile_handle,
                ),
            )
            .where(
                Post.status == "published",
                or_(Post.owner_id == principal.subject, follows_exact_author_profile),
                post_pair_is_not_blocked(principal.subject, Post.owner_id),
            )
        )
        return await post_page(
            statement,
            cursor=cursor,
            limit=limit,
            session=session,
            request=request,
            scope="feed",
            bindings=cursor_principal_bindings(principal),
        )

    async def moderation_case_subject_response(
        session: AsyncSession, case: ModerationCase
    ) -> ModerationCaseSubjectResponse:
        decision = await session.scalar(
            select(ModerationDecision).where(ModerationDecision.case_id == case.id)
        )
        appeal = await session.scalar(
            select(ModerationAppeal)
            .where(ModerationAppeal.case_id == case.id)
            .order_by(ModerationAppeal.submitted_at.desc(), ModerationAppeal.id.desc())
            .limit(1)
        )
        appeal_response = None
        if appeal is not None:
            appeal_response = ModerationAppealSubjectResponse(
                id=appeal.id,
                decision_id=appeal.decision_id,
                status=cast(Any, appeal.status),
                submitted_at=appeal.submitted_at,
                reviewed_at=appeal.reviewed_at,
                subject_explanation=appeal.subject_explanation,
            )
        appeal_deadline = None
        if decision is not None and decision.action == "withhold":
            decided_at = decision.decided_at
            normalized = (
                decided_at if decided_at.tzinfo is not None else decided_at.replace(tzinfo=UTC)
            )
            appeal_deadline = normalized + timedelta(days=30)
        return ModerationCaseSubjectResponse(
            id=case.id,
            post_id=case.post_id,
            status=cast(Any, case.status),
            reason_code=decision.reason_code if decision is not None else None,
            subject_explanation=decision.subject_explanation if decision is not None else None,
            decided_at=decision.decided_at if decision is not None else None,
            appeal_deadline=appeal_deadline,
            appeal=appeal_response,
            updated_at=case.updated_at,
        )

    @app.get(
        "/v1/moderation/cases",
        response_model=ModerationCaseListResponse,
        tags=["moderation"],
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def list_moderation_cases(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ModerationCaseListResponse:
        require_social_human(principal, "documents:read")
        statement = select(ModerationCase).where(
            ModerationCase.subject_owner_id == principal.subject
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="moderation_cases",
                bindings=cursor_principal_bindings(principal),
                detail="moderation case cursor is malformed",
            )
            try:
                if payload["v"] != 1 or payload["scope"] != "moderation_cases":
                    raise ValueError
                updated_at = datetime.fromisoformat(str(payload["updated_at"]))
                case_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="moderation case cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    ModerationCase.updated_at < updated_at,
                    and_(ModerationCase.updated_at == updated_at, ModerationCase.id < case_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(
                    ModerationCase.updated_at.desc(), ModerationCase.id.desc()
                ).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "moderation_cases",
                    "updated_at": last.updated_at.isoformat(),
                    "id": last.id,
                },
                scope="moderation_cases",
                bindings=cursor_principal_bindings(principal),
            )
        return ModerationCaseListResponse(
            cases=[await moderation_case_subject_response(session, row) for row in page],
            next_cursor=next_cursor,
        )

    @app.post(
        "/v1/moderation/cases/{case_id}/appeals",
        response_model=ModerationAppealSubjectResponse,
        status_code=201,
        tags=["moderation"],
        openapi_extra=_mutation_openapi_extra(human_only=True),
    )
    async def create_moderation_appeal(
        case_id: str,
        body: ModerationAppealCreateRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ModerationAppealSubjectResponse | Response:
        require_social_human(principal, "documents:read")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/moderation/cases/{case_id}/appeals"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        case_probe = await session.get(ModerationCase, case_id)
        if case_probe is None or case_probe.subject_owner_id != principal.subject:
            raise HTTPException(status_code=404, detail="moderation case was not found")
        post = await session.scalar(
            select(Post).where(Post.id == case_probe.post_id).with_for_update()
        )
        case = await session.scalar(
            select(ModerationCase).where(ModerationCase.id == case_id).with_for_update()
        )
        decision = await session.scalar(
            select(ModerationDecision)
            .where(ModerationDecision.case_id == case_id)
            .with_for_update()
        )
        existing = await session.scalar(
            select(ModerationAppeal).where(ModerationAppeal.case_id == case_id).with_for_update()
        )
        if (
            post is None
            or case is None
            or case.post_id != post.id
            or case.subject_owner_id != post.owner_id
            or case.subject_owner_id != principal.subject
            or decision is None
            or decision.case_id != case.id
            or decision.post_id != post.id
            or (existing is not None and existing.decision_id != decision.id)
        ):
            raise HTTPException(status_code=409, detail="moderation case authority is inconsistent")
        now = datetime.now(UTC)
        if decision.action != "withhold" or case.status != "withheld":
            raise HTTPException(
                status_code=409, detail="case does not have an appealable adverse decision"
            )
        decided_at = (
            decision.decided_at
            if decision.decided_at.tzinfo is not None
            else decision.decided_at.replace(tzinfo=UTC)
        )
        if now > decided_at + timedelta(days=30):
            raise HTTPException(status_code=409, detail="appeal deadline has passed")
        if existing is not None:
            raise HTTPException(
                status_code=409, detail="an appeal already exists for this decision"
            )
        appeal = ModerationAppeal(
            id=new_id(),
            case_id=case.id,
            decision_id=decision.id,
            subject_owner_id=principal.subject,
            rationale=body.rationale,
            status="submitted",
            submitted_at=now,
        )
        case.status = "appealed"
        case.closed_at = None
        case.updated_at = now
        case.retention_expires_at = None
        session.add_all(
            [
                appeal,
                ModerationAuditEvent(
                    id=new_id(),
                    case_id=case.id,
                    post_id=case.post_id,
                    event_type="appeal_submitted",
                    actor_id=principal.audit_actor_id,
                    actor_role="subject",
                    safe_metadata="{}",
                    occurred_at=now,
                ),
            ]
        )
        result = ModerationAppealSubjectResponse(
            id=appeal.id,
            decision_id=appeal.decision_id,
            status="submitted",
            submitted_at=appeal.submitted_at,
            reviewed_at=None,
            subject_explanation=None,
        )
        session.add(
            IdempotencyRecord(
                owner_id=principal.subject,
                idempotency_key=key,
                operation=operation,
                request_hash=fingerprint,
                response_status=201,
                response_body="",
                response_headers="{}",
                resource_type="moderation_appeal",
                resource_id=appeal.id,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="moderation appeal conflicted") from exc
        return result

    @app.post(
        "/v1/posts/{post_id}/report",
        response_model=PostReportResponse,
        status_code=201,
        tags=["posts"],
        openapi_extra=_mutation_openapi_extra(human_only=True),
    )
    async def report_post(
        post_id: str,
        body: PostReportCreateRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> PostReportResponse | Response:
        require_social_human(principal, "documents:read")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/posts/{post_id}/report"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        post = await visible_published_post(session, post_id, principal)
        locked_post = await session.scalar(select(Post).where(Post.id == post.id).with_for_update())
        if locked_post is None or locked_post.status != "published":
            raise HTTPException(status_code=404, detail="post was not found")
        post = locked_post
        if await current_public_post_author(session, post) is None or await post_content_blocked(
            session, principal.subject, post.owner_id
        ):
            raise HTTPException(status_code=404, detail="post was not found")
        now = datetime.now(UTC)
        # Probe only to preserve the lock order below. A duplicate linked to a
        # closed case must lock that existing case rather than open a new one.
        report_case_id = await session.scalar(
            select(PostReport.case_id).where(
                PostReport.post_id == post.id,
                PostReport.reporter_owner_id == principal.subject,
            )
        )
        case: ModerationCase | None
        if report_case_id is None:
            case = await ensure_post_moderation_case(session, post, now)
        else:
            case = await session.scalar(
                select(ModerationCase).where(ModerationCase.id == report_case_id).with_for_update()
            )
            if case is None:
                raise HTTPException(
                    status_code=503, detail="moderation report storage is inconsistent"
                )
        if case is None:  # pragma: no cover - retained for type-safe fail-closed handling
            raise HTTPException(status_code=503, detail="moderation report storage is inconsistent")
        existing = await session.scalar(
            select(PostReport)
            .where(PostReport.post_id == post.id, PostReport.reporter_owner_id == principal.subject)
            .with_for_update()
        )
        if existing is not None:
            if (
                existing.case_id is None
                or existing.case_id != case.id
                or case.post_id != post.id
                or case.subject_owner_id != post.owner_id
            ):
                raise HTTPException(
                    status_code=503, detail="moderation report storage is inconsistent"
                )
            result = PostReportResponse(
                id=existing.id,
                post_id=existing.post_id,
                reason_code=existing.reason_code,
                created_at=existing.created_at,
            )
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=200,
                body="",
                headers={},
                resource_type="post_report",
                resource_id=existing.id,
            )
            return Response(
                content=result.model_dump_json(), status_code=200, media_type="application/json"
            )
        if (
            case.status != "open"
            or case.post_id != post.id
            or case.subject_owner_id != post.owner_id
        ):
            raise HTTPException(status_code=503, detail="moderation report storage is inconsistent")
        report_count = await session.scalar(
            select(func.count(PostReport.id)).where(PostReport.case_id == case.id)
        )
        if report_count is None or report_count >= 1_000:
            raise HTTPException(status_code=503, detail="moderation report evidence is unavailable")
        row = PostReport(
            id=new_id(),
            post_id=post.id,
            case_id=case.id,
            reporter_owner_id=principal.subject,
            reason_code=body.reason_code,
            narrative=body.narrative,
            created_at=now,
        )
        session.add(
            ModerationAuditEvent(
                id=new_id(),
                case_id=case.id,
                post_id=post.id,
                event_type="report_linked",
                actor_id="system:report-intake",
                actor_role="system",
                safe_metadata="{}",
                occurred_at=now,
            )
        )
        session.add(row)
        await consume_post_quota(
            session,
            model=PostReportRateBucket,
            owner_id=principal.subject,
            count_field="report_count",
            limit=20,
            now=now,
            message="post report daily limit reached",
        )
        result = PostReportResponse(
            id=row.id, post_id=row.post_id, reason_code=row.reason_code, created_at=row.created_at
        )
        try:
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=201,
                body="",
                headers={},
                resource_type="post_report",
                resource_id=row.id,
            )
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="post report conflicted") from exc
        return result

    def raise_moderation_review_error(exc: PostModerationError) -> NoReturn:
        if isinstance(exc, PostModerationNotFoundError):
            raise HTTPException(
                status_code=404, detail="moderation review record was not found"
            ) from exc
        if isinstance(exc, PostModerationPreconditionError):
            raise HTTPException(
                status_code=412, detail="moderation review evidence is stale"
            ) from exc
        if isinstance(exc, PostModerationInputError):
            raise HTTPException(
                status_code=422, detail="moderation review input is invalid"
            ) from exc
        if isinstance(exc, PostModerationConflictError):
            raise HTTPException(
                status_code=409, detail="moderation review conflicts with current state"
            ) from exc
        if isinstance(exc, PostModerationConfigurationError):
            moderation_review_forbidden()
        if isinstance(exc, PostModerationStorageError):
            raise HTTPException(
                status_code=503, detail="moderation review evidence is unavailable"
            ) from exc
        raise HTTPException(status_code=503, detail="moderation review is unavailable") from exc

    def moderation_post_review_payload(
        post: Post, markdown: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            frontmatter, _ = validate_canonical("post", markdown)
        except MarkdownValidationError as exc:
            raise PostModerationStorageError("canonical post Markdown is invalid") from exc
        if (
            post.current_version != 1
            or frontmatter.get("id") != post.id
            or frontmatter.get("author_profile_handle") != post.author_profile_handle
            or frontmatter.get("version") != 1
            or canonical_post_datetime(frontmatter.get("published_at"))
            != post_datetime(post.published_at).astimezone(UTC)
            or post.status not in {"published", "withdrawn", "withheld"}
        ):
            raise PostModerationStorageError("canonical post evidence is inconsistent")
        return (
            {
                "id": post.id,
                "author_profile_handle": str(frontmatter["author_profile_handle"]),
                "title": str(frontmatter["title"]),
                "topics": list(frontmatter["topics"]),
                "version": 1,
                "published_at": post_datetime(post.published_at),
                "status": post.status,
                "markdown": markdown,
            },
            frontmatter,
        )

    def moderation_reports_review_payload(reports: tuple[PostReport, ...]) -> list[dict[str, Any]]:
        if len(reports) > 1_000:
            raise PostModerationStorageError("moderation report evidence exceeds the review bound")
        payload: list[dict[str, Any]] = []
        for report in reports:
            narrative = report.narrative
            if narrative is not None and not isinstance(narrative, str):
                raise PostModerationStorageError("moderation report evidence is inconsistent")
            payload.append(
                {
                    "id": report.id,
                    "reason_code": report.reason_code,
                    "narrative": None if narrative is None or not narrative.strip() else narrative,
                    "created_at": report.created_at,
                }
            )
        return payload

    def moderation_case_summary_payload(
        case: ModerationCase,
        post: Post,
        frontmatter: dict[str, Any],
        *,
        report_count: int,
        reason_codes: list[str],
    ) -> dict[str, Any]:
        if (
            case.post_id != post.id
            or case.subject_owner_id != post.owner_id
            or report_count < 0
            or any(
                reason
                not in {
                    "spam",
                    "harassment",
                    "misinformation",
                    "privacy",
                    "illegal_content",
                    "other",
                }
                for reason in reason_codes
            )
        ):
            raise PostModerationStorageError("moderation case evidence is inconsistent")
        return {
            "id": case.id,
            "post_id": post.id,
            "status": case.status,
            "author_profile_handle": str(frontmatter["author_profile_handle"]),
            "title": str(frontmatter["title"]),
            "report_count": report_count,
            "reason_codes": sorted(set(reason_codes)),
            "created_at": case.created_at,
            "updated_at": case.updated_at,
        }

    async def moderation_case_queue_summary(
        session: AsyncSession, request: Request, case: ModerationCase, post: Post
    ) -> dict[str, Any]:
        try:
            markdown, frontmatter = verified_post_markdown(post, request)
        except StorageIntegrityError as exc:
            raise PostModerationStorageError("canonical post storage failed verification") from exc
        del markdown
        reasons = list(
            (
                await session.scalars(
                    select(PostReport.reason_code)
                    .where(PostReport.case_id == case.id)
                    .order_by(PostReport.created_at.asc(), PostReport.id.asc())
                    .limit(1_001)
                )
            ).all()
        )
        if len(reasons) > 1_000:
            raise PostModerationStorageError("moderation report evidence exceeds the review bound")
        return moderation_case_summary_payload(
            case, post, frontmatter, report_count=len(reasons), reason_codes=reasons
        )

    def moderation_cursor(
        cursor: str | None,
        *,
        scope: str,
        timestamp_field: str,
        identifier_field: str,
        bindings: tuple[str, ...] = (),
    ) -> tuple[datetime, str] | None:
        if cursor is None:
            return None
        payload = generic_cursor_decode(
            cursor,
            scope=scope,
            bindings=bindings,
            detail="moderation review cursor is malformed",
        )
        try:
            if set(payload) != {"v", "scope", timestamp_field, identifier_field}:
                raise ValueError
            raw_timestamp = payload[timestamp_field]
            identifier = payload[identifier_field]
            if payload["v"] != 1 or payload["scope"] != scope:
                raise ValueError
            if (
                not isinstance(raw_timestamp, str)
                or not isinstance(identifier, str)
                or not identifier
            ):
                raise ValueError
            timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="moderation review cursor is malformed"
            ) from exc
        return timestamp, identifier

    @app.get("/v1/internal/post-moderation/cases", include_in_schema=False)
    async def list_moderation_review_cases(
        request: Request,
        response: Response,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        response.headers.update(moderation_review_headers())
        moderator_id, _ = require_configured_moderation_reviewer(
            principal, request.app.state.settings, "case"
        )
        cursor_values = moderation_cursor(
            cursor,
            scope="moderation_review_cases",
            timestamp_field="updated_at",
            identifier_field="id",
            bindings=(moderator_id,),
        )
        statement = (
            select(ModerationCase, Post)
            .join(Post, Post.id == ModerationCase.post_id)
            .where(ModerationCase.status == "open", ModerationCase.subject_owner_id != moderator_id)
        )
        if cursor_values is not None:
            updated_at, case_id = cursor_values
            statement = statement.where(
                or_(
                    ModerationCase.updated_at < updated_at,
                    and_(ModerationCase.updated_at == updated_at, ModerationCase.id < case_id),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(
                    ModerationCase.updated_at.desc(), ModerationCase.id.desc()
                ).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        try:
            cases = [
                await moderation_case_queue_summary(session, request, case, post)
                for case, post in page
            ]
        except PostModerationError as exc:
            raise_moderation_review_error(exc)
        next_cursor = None
        if len(rows) > limit and page:
            last_case, _ = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "moderation_review_cases",
                    "updated_at": post_datetime(last_case.updated_at).isoformat(),
                    "id": last_case.id,
                },
                scope="moderation_review_cases",
                bindings=(moderator_id,),
            )
        return {"cases": cases, "next_cursor": next_cursor}

    @app.get("/v1/internal/post-moderation/cases/{case_id}", include_in_schema=False)
    async def inspect_moderation_review_case(
        case_id: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        response.headers.update(moderation_review_headers())
        moderator_id, _ = require_configured_moderation_reviewer(
            principal, request.app.state.settings, "case"
        )
        try:
            bundle = await lock_case_review_bundle(session, case_id=case_id, read=True)
            if compare_digest(bundle.case.subject_owner_id, moderator_id):
                moderation_review_forbidden()
            evidence = case_evidence_snapshot(request.app.state.store, bundle)
            post_payload, frontmatter = moderation_post_review_payload(
                bundle.post, evidence.markdown
            )
            report_payload = moderation_reports_review_payload(bundle.reports)
            case_payload = moderation_case_summary_payload(
                bundle.case,
                bundle.post,
                frontmatter,
                report_count=len(bundle.reports),
                reason_codes=[report.reason_code for report in bundle.reports],
            )
        except PostModerationError as exc:
            raise_moderation_review_error(exc)
        etag = strong_etag(evidence.sha256)
        response.headers["ETag"] = etag
        return {"case": case_payload, "post": post_payload, "reports": report_payload, "etag": etag}

    @app.post("/v1/internal/post-moderation/cases/{case_id}/decision", include_in_schema=False)
    async def decide_moderation_review_case(
        case_id: str,
        body: ModerationCaseDecisionRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_configured_moderation_reviewer(principal, request.app.state.settings, "case")
        etag, expected_snapshot_sha256 = moderation_required_snapshot_etag(request)
        normalized_body, normalized_json = moderation_normalized_decision_body(body)
        operation = f"POST:/v1/internal/post-moderation/cases/{case_id}/decision"
        fingerprint = _request_fingerprint(operation, normalized_json, etag)
        key = idempotency_key(request, required=True)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        try:
            await lock_case_review_bundle(session, case_id=case_id, allow_existing_decision=True)
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
            result = await decide_case(
                session,
                request.app.state.store,
                request.app.state.settings,
                case_id=case_id,
                expected_post_id=None,
                action=cast(Any, normalized_body["action"]),
                reason_code=normalized_body["reason_code"],
                subject_explanation=normalized_body["subject_explanation"],
                actor_method="internal_http",
                expected_snapshot_sha256=expected_snapshot_sha256,
            )
            receipt_digest = moderation_receipt_digest(
                resource_kind="moderation_decision",
                case=result.case,
                post=result.post,
                decision=result.decision,
                appeal=None,
                reports=result.reports,
                action=normalized_body["action"],
            )
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=204,
                body="",
                headers={},
                resource_type="moderation_decision",
                resource_id=moderation_receipt_resource_id(
                    resource_kind="moderation_decision",
                    route_id=case_id,
                    action=normalized_body["action"],
                    snapshot_sha256=result.evidence.sha256,
                    digest=receipt_digest,
                ),
            )
        except PostModerationError as exc:
            raise_moderation_review_error(exc)
        return Response(status_code=204, headers=moderation_review_headers())

    @app.get("/v1/internal/post-moderation/appeals", include_in_schema=False)
    async def list_moderation_review_appeals(
        request: Request,
        response: Response,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        response.headers.update(moderation_review_headers())
        reviewer_id, _ = require_configured_moderation_reviewer(
            principal, request.app.state.settings, "appeal"
        )
        cursor_values = moderation_cursor(
            cursor,
            scope="moderation_review_appeals",
            timestamp_field="submitted_at",
            identifier_field="id",
            bindings=(reviewer_id,),
        )
        statement = (
            select(ModerationAppeal, ModerationCase, ModerationDecision, Post)
            .join(ModerationCase, ModerationCase.id == ModerationAppeal.case_id)
            .join(ModerationDecision, ModerationDecision.id == ModerationAppeal.decision_id)
            .join(Post, Post.id == ModerationCase.post_id)
            .where(
                ModerationAppeal.status == "submitted",
                ModerationCase.status == "appealed",
                ModerationDecision.action == "withhold",
                ModerationAppeal.subject_owner_id != reviewer_id,
                ModerationDecision.moderator_id != reviewer_id,
                ModerationDecision.case_id == ModerationCase.id,
                ModerationDecision.post_id == Post.id,
            )
        )
        if cursor_values is not None:
            submitted_at, appeal_id = cursor_values
            statement = statement.where(
                or_(
                    ModerationAppeal.submitted_at < submitted_at,
                    and_(
                        ModerationAppeal.submitted_at == submitted_at,
                        ModerationAppeal.id < appeal_id,
                    ),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(
                    ModerationAppeal.submitted_at.desc(), ModerationAppeal.id.desc()
                ).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        try:
            appeals = []
            for appeal, case, _decision, post in page:
                if (
                    case.subject_owner_id != post.owner_id
                    or appeal.subject_owner_id != case.subject_owner_id
                ):
                    raise PostModerationStorageError("moderation appeal evidence is inconsistent")
                try:
                    _markdown, frontmatter = verified_post_markdown(post, request)
                except StorageIntegrityError as exc:
                    raise PostModerationStorageError(
                        "canonical post storage failed verification"
                    ) from exc
                appeals.append(
                    {
                        "id": appeal.id,
                        "case_id": case.id,
                        "post_id": post.id,
                        "status": appeal.status,
                        "author_profile_handle": str(frontmatter["author_profile_handle"]),
                        "title": str(frontmatter["title"]),
                        "submitted_at": appeal.submitted_at,
                    }
                )
        except PostModerationError as exc:
            raise_moderation_review_error(exc)
        next_cursor = None
        if len(rows) > limit and page:
            last_appeal, _, _, _ = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "moderation_review_appeals",
                    "submitted_at": post_datetime(last_appeal.submitted_at).isoformat(),
                    "id": last_appeal.id,
                },
                scope="moderation_review_appeals",
                bindings=(reviewer_id,),
            )
        return {"appeals": appeals, "next_cursor": next_cursor}

    @app.get("/v1/internal/post-moderation/appeals/{appeal_id}", include_in_schema=False)
    async def inspect_moderation_review_appeal(
        appeal_id: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        response.headers.update(moderation_review_headers())
        reviewer_id, _ = require_configured_moderation_reviewer(
            principal, request.app.state.settings, "appeal"
        )
        try:
            bundle = await lock_appeal_review_bundle(session, appeal_id=appeal_id, read=True)
            if (
                bundle.appeal.status != "submitted"
                or bundle.case.status != "appealed"
                or bundle.decision.action != "withhold"
            ):
                raise PostModerationNotFoundError("moderation appeal was not found")
            if compare_digest(bundle.case.subject_owner_id, reviewer_id) or compare_digest(
                bundle.decision.moderator_id, reviewer_id
            ):
                moderation_review_forbidden()
            evidence = appeal_evidence_snapshot(request.app.state.store, bundle)
            post_payload, _frontmatter = moderation_post_review_payload(
                bundle.post, evidence.markdown
            )
            report_payload = moderation_reports_review_payload(bundle.reports)
            if (
                not isinstance(bundle.appeal.rationale, str)
                or not bundle.appeal.rationale.strip()
                or not isinstance(bundle.decision.subject_explanation, str)
                or not bundle.decision.subject_explanation.strip()
            ):
                raise PostModerationStorageError("moderation appeal evidence is inconsistent")
            appeal_payload = {
                "id": bundle.appeal.id,
                "case_id": bundle.case.id,
                "post_id": bundle.post.id,
                "status": bundle.appeal.status,
                "rationale": bundle.appeal.rationale,
                "submitted_at": bundle.appeal.submitted_at,
            }
            decision_payload = {
                "action": "withhold",
                "reason_code": bundle.decision.reason_code,
                "subject_explanation": bundle.decision.subject_explanation,
                "decided_at": bundle.decision.decided_at,
            }
        except PostModerationError as exc:
            raise_moderation_review_error(exc)
        etag = strong_etag(evidence.sha256)
        response.headers["ETag"] = etag
        return {
            "appeal": appeal_payload,
            "post": post_payload,
            "reports": report_payload,
            "decision": decision_payload,
            "etag": etag,
        }

    @app.post("/v1/internal/post-moderation/appeals/{appeal_id}/decision", include_in_schema=False)
    async def decide_moderation_review_appeal(
        appeal_id: str,
        body: ModerationAppealReviewRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_configured_moderation_reviewer(principal, request.app.state.settings, "appeal")
        etag, expected_snapshot_sha256 = moderation_required_snapshot_etag(request)
        normalized_body, normalized_json = moderation_normalized_decision_body(body)
        operation = f"POST:/v1/internal/post-moderation/appeals/{appeal_id}/decision"
        fingerprint = _request_fingerprint(operation, normalized_json, etag)
        key = idempotency_key(request, required=True)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        try:
            await lock_appeal_review_bundle(session, appeal_id=appeal_id)
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
            result = await review_appeal(
                session,
                request.app.state.store,
                request.app.state.settings,
                appeal_id=appeal_id,
                action=cast(Any, normalized_body["action"]),
                subject_explanation=normalized_body["subject_explanation"],
                actor_method="internal_http",
                expected_snapshot_sha256=expected_snapshot_sha256,
            )
            receipt_digest = moderation_receipt_digest(
                resource_kind="moderation_appeal_review",
                case=result.case,
                post=result.post,
                decision=result.decision,
                appeal=result.appeal,
                reports=result.reports,
                action=normalized_body["action"],
            )
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=204,
                body="",
                headers={},
                resource_type="moderation_appeal_review",
                resource_id=moderation_receipt_resource_id(
                    resource_kind="moderation_appeal_review",
                    route_id=appeal_id,
                    action=normalized_body["action"],
                    snapshot_sha256=result.evidence.sha256,
                    digest=receipt_digest,
                ),
            )
        except PostModerationError as exc:
            raise_moderation_review_error(exc)
        return Response(status_code=204, headers=moderation_review_headers())

    def require_lifecycle_enabled() -> None:
        if not settings.account_lifecycle_enabled:
            # Stage 1 has no confirmation or erasure executor, so the default
            # installation never exposes lifecycle routes accidentally.
            raise HTTPException(status_code=404, detail="account lifecycle is unavailable")

    async def require_lifecycle_human(
        _: None = Depends(require_lifecycle_enabled),
        principal: Principal = Depends(require_principal),
    ) -> Principal:
        if principal.method != "clerk_jwt":
            raise HTTPException(status_code=403, detail="account_lifecycle_clerk_human_required")
        if principal.is_impersonated:
            raise HTTPException(status_code=403, detail="account_lifecycle_impersonation_forbidden")
        return principal

    async def require_lifecycle_confirmation(
        _: None = Depends(require_lifecycle_enabled),
        claims: LifecycleConfirmationClaims = Depends(require_lifecycle_confirmation_claims),
    ) -> LifecycleConfirmationClaims:
        return claims

    def lifecycle_step_up(
        principal: Principal | LifecycleConfirmationClaims,
    ) -> tuple[str, str, str]:
        factor_age = principal.factor_verification_age
        reverification_id = principal.reverification_id
        session_id = principal.session_id
        token_id = principal.token_id
        if (
            not isinstance(factor_age, tuple)
            or len(factor_age) != 2
            or any(isinstance(age, bool) or not isinstance(age, int) for age in factor_age)
            or factor_age[0] < 0
            or factor_age[1] < -1
            or any(
                not isinstance(claim, str) or not 1 <= len(claim) <= 255
                for claim in (reverification_id, session_id, token_id)
            )
        ):
            raise LifecycleReverificationDenied
        assert isinstance(reverification_id, str)
        assert isinstance(session_id, str)
        assert isinstance(token_id, str)
        first_factor_age, second_factor_age = factor_age
        effective_age = first_factor_age if second_factor_age == -1 else second_factor_age
        if effective_age >= 10:
            raise LifecycleReverificationDenied
        return reverification_id, session_id, token_id

    async def lifecycle_empty_request(request: Request) -> str:
        if request.query_params or await request.body():
            raise HTTPException(
                status_code=422, detail="account lifecycle request must not include a body or query"
            )
        return json.dumps(
            {"method": request.method.upper(), "path": request.url.path, "body": {}},
            separators=(",", ":"),
            sort_keys=True,
        )

    def lifecycle_confirmation_hmac(
        *, deletion_id: str, subject_hmac: str, idempotency_key_value: str
    ) -> str:
        canonical = json.dumps(
            {
                "action": "account-delete-confirm.v1",
                "deletion_id": deletion_id,
                "idempotency_key": idempotency_key_value,
                "subject_hmac": subject_hmac,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return lifecycle_hmac(settings, "delete-confirm-key", canonical)

    def lifecycle_terminal_digest(
        *, deletion_id: str, policy_version: str, occurred_at: datetime
    ) -> str:
        utc_occurred_at = (
            occurred_at if occurred_at.tzinfo is not None else occurred_at.replace(tzinfo=UTC)
        ).astimezone(UTC)
        return sha256(
            f"connect.md:lifecycle:terminal:v1:{deletion_id}:{policy_version}:"
            f"{utc_occurred_at.isoformat()}".encode()
        ).hexdigest()

    def lifecycle_confirmation_unavailable() -> NoReturn:
        raise HTTPException(
            status_code=503,
            detail="account lifecycle confirmation replay is unavailable",
        )

    async def validate_lifecycle_confirmation_replay(
        *,
        request: Request,
        session: AsyncSession,
        lifecycle: AccountLifecycle,
        claims: LifecycleConfirmationClaims,
        now: datetime,
    ) -> None:
        if lifecycle.state == "confirmation_pending":
            lifecycle_confirmation_unavailable()
        if lifecycle.request_idempotency_hmac is not None:
            lifecycle_confirmation_unavailable()
        if lifecycle.receipt_ciphertext is not None:
            lifecycle_confirmation_unavailable()
        if lifecycle.receipt_recovery_idempotency_hmac is not None:
            lifecycle_confirmation_unavailable()
        if lifecycle.receipt_hmac is None or not re.fullmatch(
            _SHA256_HEX_PATTERN, lifecycle.receipt_hmac
        ):
            lifecycle_confirmation_unavailable()
        if lifecycle.confirmed_at is None or lifecycle.concealed_at is None:
            lifecycle_confirmation_unavailable()
        confirmed_at = (
            lifecycle.confirmed_at
            if lifecycle.confirmed_at.tzinfo is not None
            else lifecycle.confirmed_at.replace(tzinfo=UTC)
        ).astimezone(UTC)
        concealed_at = (
            lifecycle.concealed_at
            if lifecycle.concealed_at.tzinfo is not None
            else lifecycle.concealed_at.replace(tzinfo=UTC)
        ).astimezone(UTC)
        deny = await session.scalar(
            select(AccountAccessDeny)
            .where(
                AccountAccessDeny.deletion_id == lifecycle.id,
                AccountAccessDeny.subject_hmac == lifecycle.subject_hmac,
            )
            .with_for_update()
        )
        if deny is None:
            lifecycle_confirmation_unavailable()
        denied_at = (
            deny.denied_at
            if deny.denied_at.tzinfo is not None
            else deny.denied_at.replace(tzinfo=UTC)
        ).astimezone(UTC)
        if denied_at != confirmed_at or concealed_at != confirmed_at:
            lifecycle_confirmation_unavailable()

        tombstone = await session.scalar(
            select(AccountLifecycleTombstone)
            .where(AccountLifecycleTombstone.deletion_id == lifecycle.id)
            .with_for_update()
        )
        if lifecycle.terminal_at is not None:
            terminal_at = (
                lifecycle.terminal_at
                if lifecycle.terminal_at.tzinfo is not None
                else lifecycle.terminal_at.replace(tzinfo=UTC)
            ).astimezone(UTC)
            if lifecycle.state != "fully_erased" or tombstone is None:
                lifecycle_confirmation_unavailable()
            tombstone_at = (
                tombstone.occurred_at
                if tombstone.occurred_at.tzinfo is not None
                else tombstone.occurred_at.replace(tzinfo=UTC)
            ).astimezone(UTC)
            if (
                tombstone.phase != "fully_erased"
                or tombstone.policy_version != lifecycle.policy_version
                or tombstone_at != terminal_at
                or tombstone.result_digest
                != lifecycle_terminal_digest(
                    deletion_id=lifecycle.id,
                    policy_version=lifecycle.policy_version,
                    occurred_at=terminal_at,
                )
            ):
                lifecycle_confirmation_unavailable()
            if now >= terminal_at + timedelta(days=30):
                raise HTTPException(
                    status_code=404, detail="account deletion request was not found"
                )
        elif tombstone is not None or lifecycle.state == "fully_erased":
            lifecycle_confirmation_unavailable()

        provider_items = (
            await session.scalars(
                select(AccountErasureItem)
                .where(
                    AccountErasureItem.deletion_id == lifecycle.id,
                    AccountErasureItem.phase == "provider",
                    AccountErasureItem.resource_type.in_(("provider_session", "provider_user")),
                )
                .with_for_update()
            )
        ).all()
        if len(provider_items) != 2 or {item.resource_type for item in provider_items} != {
            "provider_session",
            "provider_user",
        }:
            lifecycle_confirmation_unavailable()
        if any(item.state == "completed" for item in provider_items):
            raise HTTPException(status_code=404, detail="account deletion request was not found")
        if any(
            item.state not in {"queued", "leased", "held", "dead_letter"} for item in provider_items
        ):
            lifecycle_confirmation_unavailable()
        if lifecycle.provider_state == "verified":
            raise HTTPException(status_code=404, detail="account deletion request was not found")
        if lifecycle.provider_state not in {"pending", "failed", "unsupported"}:
            lifecycle_confirmation_unavailable()
        if not isinstance(lifecycle.provider_subject_ciphertext, str) or not isinstance(
            lifecycle.provider_session_ciphertext, str
        ):
            lifecycle_confirmation_unavailable()
        try:
            provider_subject = decrypt_lifecycle_provider_subject(
                settings,
                deletion_id=lifecycle.id,
                ciphertext=lifecycle.provider_subject_ciphertext,
            )
            decrypt_lifecycle_provider_session(
                settings,
                deletion_id=lifecycle.id,
                ciphertext=lifecycle.provider_session_ciphertext,
            )
        except (AuthenticationUnavailable, ValueError, UnicodeError):
            lifecycle_confirmation_unavailable()
        if not compare_digest(provider_subject, claims.subject):
            lifecycle_confirmation_unavailable()

        journal: DeletionCommitmentJournal | None = request.app.state.deletion_journal
        if journal is None or request.app.state.deletion_journal_consistent is not True:
            lifecycle_confirmation_unavailable()
        try:
            if await verify_live_deletion_mirror(session, journal) < 1:
                lifecycle_confirmation_unavailable()
        except (DeletionJournalError, OSError, ValueError, AuthenticationUnavailable):
            lifecycle_confirmation_unavailable()

    async def assert_pending_lifecycle_access(session: AsyncSession, *, subject_hmac: str) -> None:
        denied = await session.scalar(
            select(AccountAccessDeny.id)
            .where(AccountAccessDeny.subject_hmac == subject_hmac)
            .with_for_update()
        )
        if denied is not None:
            raise HTTPException(status_code=403, detail="account_access_denied")

    async def consume_lifecycle_step_up(
        session: AsyncSession,
        principal: Principal | LifecycleConfirmationClaims,
        *,
        purpose: Literal["export", "delete_request", "delete_confirm", "delete_receipt_recover"],
        action: str,
        flush: bool = True,
    ) -> None:
        reverification_id, session_id, token_id = lifecycle_step_up(principal)
        reverification_hmac = lifecycle_hmac(settings, "reverification", reverification_id)
        with session.no_autoflush:
            existing = await session.scalar(
                select(AccountReverificationUse)
                .where(AccountReverificationUse.reverification_id_hmac == reverification_hmac)
                .with_for_update()
            )
        if existing is not None:
            raise HTTPException(status_code=409, detail="reverification_already_used")
        session.add(
            AccountReverificationUse(
                reverification_id_hmac=reverification_hmac,
                subject_hmac=lifecycle_hmac(settings, "subject", principal.subject),
                sid_hmac=lifecycle_hmac(settings, "sid", session_id),
                jti_hmac=lifecycle_hmac(settings, "jti", token_id),
                purpose=purpose,
                action_hmac=lifecycle_hmac(settings, "action", action),
                used_at=datetime.now(UTC),
            )
        )
        if flush:
            try:
                await session.flush()
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="reverification_already_used") from exc

    def new_lifecycle_receipt() -> str:
        return "lr1_" + urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")

    def lifecycle_receipt_response(
        lifecycle: AccountLifecycle, receipt: str
    ) -> AccountDeletionRequestResponse:
        return AccountDeletionRequestResponse(deletion_id=lifecycle.id, status_receipt=receipt)

    lifecycle_status_headers = {
        "Cache-Control": "no-store, private",
        "Pragma": "no-cache",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    }

    def lifecycle_status_not_found() -> NoReturn:
        raise HTTPException(
            status_code=404,
            detail="account lifecycle status was not found",
            headers=lifecycle_status_headers,
        )

    async def validate_terminal_lifecycle_status(
        *, request: Request, session: AsyncSession, lifecycle: AccountLifecycle, now: datetime
    ) -> None:
        """Require the same immutable terminal proof used by the erasure worker.

        This verifier is deliberately non-repairing: every mismatch or unavailable
        external proof is indistinguishable from an unknown receipt, and no rate
        state is touched until it returns successfully.
        """

        def utc(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).astimezone(
                UTC
            )

        if (
            lifecycle.state != "fully_erased"
            or lifecycle.provider_state != "verified"
            or lifecycle.backup_state != "verified"
            or lifecycle.safe_failure_code is not None
            or lifecycle.request_idempotency_hmac is not None
            or lifecycle.confirmation_idempotency_hmac is None
            or not re.fullmatch(_SHA256_HEX_PATTERN, lifecycle.confirmation_idempotency_hmac)
            or lifecycle.receipt_hmac is None
            or not re.fullmatch(_SHA256_HEX_PATTERN, lifecycle.receipt_hmac)
            or lifecycle.receipt_ciphertext is not None
            or lifecycle.receipt_recovery_idempotency_hmac is not None
            or lifecycle.provider_subject_ciphertext is not None
            or lifecycle.provider_session_ciphertext is not None
        ):
            lifecycle_status_not_found()

        requested_at = utc(lifecycle.requested_at)
        confirmed_at = utc(lifecycle.confirmed_at)
        concealed_at = utc(lifecycle.concealed_at)
        live_erased_at = utc(lifecycle.live_erased_at)
        terminal_at = utc(lifecycle.terminal_at)
        if (
            requested_at is None
            or confirmed_at is None
            or concealed_at is None
            or live_erased_at is None
            or terminal_at is None
            or requested_at > confirmed_at
            or confirmed_at != concealed_at
            or concealed_at > live_erased_at
            or live_erased_at > terminal_at
            or terminal_at > now
        ):
            lifecycle_status_not_found()

        deny = await session.scalar(
            select(AccountAccessDeny)
            .where(
                AccountAccessDeny.deletion_id == lifecycle.id,
                AccountAccessDeny.subject_hmac == lifecycle.subject_hmac,
            )
            .with_for_update()
        )
        if deny is None or utc(deny.denied_at) != confirmed_at:
            lifecycle_status_not_found()

        tombstone = await session.scalar(
            select(AccountLifecycleTombstone)
            .where(AccountLifecycleTombstone.deletion_id == lifecycle.id)
            .with_for_update()
        )
        if (
            tombstone is None
            or tombstone.phase != "fully_erased"
            or tombstone.policy_version != lifecycle.policy_version
            or utc(tombstone.occurred_at) != terminal_at
            or tombstone.result_digest
            != lifecycle_terminal_digest(
                deletion_id=lifecycle.id,
                policy_version=lifecycle.policy_version,
                occurred_at=terminal_at,
            )
        ):
            lifecycle_status_not_found()

        journal: DeletionCommitmentJournal | None = request.app.state.deletion_journal
        if journal is None or request.app.state.deletion_journal_consistent is not True:
            lifecycle_status_not_found()
        try:
            if await verify_live_deletion_mirror(session, journal) < 1:
                lifecycle_status_not_found()
        except (DeletionJournalError, OSError, ValueError, AuthenticationUnavailable):
            lifecycle_status_not_found()

        items = (
            await session.scalars(
                select(AccountErasureItem).where(AccountErasureItem.deletion_id == lifecycle.id)
            )
        ).all()
        if not items or any(
            item.state != "completed" or item.completed_at is None for item in items
        ):
            lifecycle_status_not_found()
        provider_items = [item for item in items if item.phase == "provider"]
        if len(provider_items) != 2 or {item.resource_type for item in provider_items} != {
            "provider_session",
            "provider_user",
        }:
            lifecycle_status_not_found()
        erased_document_ids = {
            item.resource_id for item in items if item.resource_type == "document"
        }
        if (
            erased_document_ids
            and await session.scalar(
                select(SearchProjectionTask.document_id)
                .where(SearchProjectionTask.document_id.in_(erased_document_ids))
                .limit(1)
            )
            is not None
        ):
            lifecycle_status_not_found()

        obligations = (
            await session.scalars(
                select(AccountBackupObligation).where(
                    AccountBackupObligation.deletion_id == lifecycle.id
                )
            )
        ).all()
        if not obligations or any(
            obligation.state != "verified"
            or obligation.verified_at is None
            or not isinstance(obligation.generation_id, str)
            or not obligation.generation_id
            or not re.fullmatch(_SHA256_HEX_PATTERN, obligation.db_manifest_digest or "")
            or not re.fullmatch(_SHA256_HEX_PATTERN, obligation.markdown_manifest_digest or "")
            or not re.fullmatch(_SHA256_HEX_PATTERN, obligation.proof_digest or "")
            for obligation in obligations
        ):
            lifecycle_status_not_found()

    async def account_export_payload(
        session: AsyncSession, request: Request, subject: str, cutoff: datetime
    ) -> bytes:
        records: list[BaseModel] = [
            AccountExportHeaderDTO(
                cutoff=cutoff, policy_version=settings.account_lifecycle_policy_version
            )
        ]
        documents = (
            await session.scalars(
                select(Document)
                .where(Document.owner_id == subject, Document.created_at <= cutoff)
                .order_by(Document.created_at, Document.id)
            )
        ).all()
        for document in documents:
            document_versions = (
                await session.scalars(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.created_at <= cutoff,
                    )
                    .order_by(DocumentVersion.version)
                )
            ).all()
            records.append(
                AccountExportDocumentDTO(
                    id=document.id,
                    kind=document.kind,
                    identifier=document.public_identifier,
                    visibility=document.visibility,
                    current_version=document.current_version,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                    versions=[
                        AccountExportDocumentVersionDTO(
                            id=version.id,
                            version=version.version,
                            sha256=version.sha256,
                            actor_method=version.actor_method,
                            created_at=version.created_at,
                            canonical_markdown=service(session, request).read_markdown(version),
                        )
                        for version in document_versions
                    ],
                )
            )
        posts = (
            await session.scalars(
                select(Post)
                .where(Post.owner_id == subject, Post.created_at <= cutoff)
                .order_by(Post.created_at, Post.id)
            )
        ).all()
        for post in posts:
            post_versions = (
                await session.scalars(
                    select(PostVersion)
                    .where(PostVersion.post_id == post.id, PostVersion.created_at <= cutoff)
                    .order_by(PostVersion.version)
                )
            ).all()
            records.append(
                AccountExportPostDTO(
                    id=post.id,
                    status=post.status,
                    author_profile_handle=post.author_profile_handle,
                    published_at=post.published_at,
                    created_at=post.created_at,
                    updated_at=post.updated_at,
                    withdrawn_at=post.withdrawn_at,
                    withheld_at=post.withheld_at,
                    versions=[
                        AccountExportPostVersionDTO(
                            id=version.id,
                            version=version.version,
                            sha256=version.sha256,
                            created_at=version.created_at,
                            canonical_markdown=request.app.state.store.read_verified(
                                version.storage_path, version.sha256
                            ),
                        )
                        for version in post_versions
                    ],
                )
            )
        messages = (
            await session.scalars(
                select(Message)
                .where(Message.sender_owner_id == subject, Message.created_at <= cutoff)
                .order_by(Message.created_at, Message.id)
            )
        ).all()
        records.extend(
            AccountExportMessageDTO(
                id=row.id,
                conversation_id=row.conversation_id,
                markdown=row.markdown,
                content_sha256=row.content_sha256,
                status=row.status,
                created_at=row.created_at,
                retention_expires_at=row.retention_expires_at,
            )
            for row in messages
        )
        applications = (
            await session.scalars(
                select(Application)
                .where(Application.applicant_owner_id == subject, Application.created_at <= cutoff)
                .order_by(Application.created_at, Application.id)
            )
        ).all()
        records.extend(
            AccountExportApplicationDTO(
                id=row.id,
                job_id=row.job_id,
                snapshot_document_id=row.snapshot_document_id,
                snapshot_document_kind=row.snapshot_document_kind,
                snapshot_document_identifier=row.snapshot_document_identifier,
                snapshot_document_version=row.snapshot_document_version,
                snapshot_sha256=row.snapshot_sha256,
                message=row.message,
                status=row.status,
                confirmed_at=row.confirmed_at,
                retention_policy_version=row.retention_policy_version,
                retention_expires_at=row.retention_expires_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
                decided_at=row.decided_at,
            )
            for row in applications
        )
        proposals = (
            await session.scalars(
                select(AgentProposal)
                .where(AgentProposal.owner_id == subject, AgentProposal.created_at <= cutoff)
                .order_by(AgentProposal.created_at, AgentProposal.id)
            )
        ).all()
        records.extend(
            AccountExportProposalDTO(
                id=row.id,
                document_id=row.document_id,
                document_kind=row.document_kind,
                document_identifier=row.document_identifier,
                markdown=row.markdown,
                if_match=row.if_match,
                status=row.status,
                created_at=row.created_at,
                decided_at=row.decided_at,
            )
            for row in proposals
        )
        contact_requests = (
            await session.scalars(
                select(ContactRequest)
                .where(
                    ContactRequest.sender_owner_id == subject, ContactRequest.created_at <= cutoff
                )
                .order_by(ContactRequest.created_at, ContactRequest.id)
            )
        ).all()
        records.extend(
            AccountExportContactRequestDTO(
                id=row.id,
                target_document_id=row.target_document_id,
                purpose=row.purpose,
                message=row.message,
                status=row.status,
                origin=row.origin,
                created_at=row.created_at,
                decided_at=row.decided_at,
                retention_expires_at=row.retention_expires_at,
            )
            for row in contact_requests
        )
        connection_requests = (
            await session.scalars(
                select(ConnectionRequest)
                .where(
                    or_(
                        ConnectionRequest.requester_owner_id == subject,
                        ConnectionRequest.recipient_owner_id == subject,
                    ),
                    ConnectionRequest.created_at <= cutoff,
                )
                .order_by(ConnectionRequest.created_at, ConnectionRequest.id)
            )
        ).all()
        records.extend(
            AccountExportRelationshipDTO(
                record_type="connection_request",
                id=row.id,
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
                decided_at=row.decided_at,
                retention_expires_at=row.retention_expires_at,
                messaging_requested=row.requested_messaging,
            )
            for row in connection_requests
        )
        connections = (
            await session.scalars(
                select(Connection)
                .where(
                    or_(
                        Connection.requester_owner_id == subject,
                        Connection.recipient_owner_id == subject,
                    ),
                    Connection.created_at <= cutoff,
                )
                .order_by(Connection.created_at, Connection.id)
            )
        ).all()
        records.extend(
            AccountExportRelationshipDTO(
                record_type="connection",
                id=row.id,
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
                retention_expires_at=row.retention_expires_at,
                messaging_requested=row.requested_messaging,
                messaging_enabled=row.messaging_enabled,
            )
            for row in connections
        )
        conversations = (
            await session.scalars(
                select(Conversation)
                .where(
                    or_(
                        Conversation.pair_owner_low == subject,
                        Conversation.pair_owner_high == subject,
                    ),
                    Conversation.created_at <= cutoff,
                )
                .order_by(Conversation.created_at, Conversation.id)
            )
        ).all()
        records.extend(
            AccountExportRelationshipDTO(
                record_type="conversation",
                id=row.id,
                status=row.status,
                created_at=row.created_at,
                closed_at=row.closed_at,
                retention_expires_at=row.retention_expires_at,
            )
            for row in conversations
        )
        cases = (
            await session.scalars(
                select(ModerationCase)
                .where(
                    ModerationCase.subject_owner_id == subject, ModerationCase.created_at <= cutoff
                )
                .order_by(ModerationCase.created_at, ModerationCase.id)
            )
        ).all()
        for case in cases:
            decision = await session.scalar(
                select(ModerationDecision).where(ModerationDecision.case_id == case.id)
            )
            decision_at = decision.decided_at if decision is not None else None
            if decision_at is not None and decision_at.tzinfo is None:
                decision_at = decision_at.replace(tzinfo=UTC)
            records.append(
                AccountExportModerationCaseDTO(
                    id=case.id,
                    post_id=case.post_id,
                    status=case.status,
                    created_at=case.created_at,
                    updated_at=case.updated_at,
                    closed_at=case.closed_at,
                    retention_expires_at=case.retention_expires_at,
                    decision=(
                        {
                            "action": decision.action,
                            "subject_explanation": decision.subject_explanation,
                            "decided_at": decision.decided_at,
                        }
                        if decision is not None
                        and decision_at is not None
                        and decision_at <= cutoff
                        else None
                    ),
                )
            )
        appeals = (
            await session.scalars(
                select(ModerationAppeal)
                .where(
                    ModerationAppeal.subject_owner_id == subject,
                    ModerationAppeal.submitted_at <= cutoff,
                )
                .order_by(ModerationAppeal.submitted_at, ModerationAppeal.id)
            )
        ).all()
        records.extend(
            AccountExportModerationAppealDTO(
                id=row.id,
                case_id=row.case_id,
                decision_id=row.decision_id,
                status=row.status,
                submitted_at=row.submitted_at,
                reviewed_at=row.reviewed_at,
                subject_explanation=row.subject_explanation,
            )
            for row in appeals
        )
        verifications = (
            await session.scalars(
                select(OrganizationVerification)
                .join(Organization, OrganizationVerification.organization_id == Organization.id)
                .where(
                    Organization.owner_id == subject,
                    OrganizationVerification.created_at <= cutoff,
                )
                .order_by(OrganizationVerification.created_at, OrganizationVerification.id)
            )
        ).all()
        for verification in verifications:
            events = (
                await session.scalars(
                    select(OrganizationVerificationEvent)
                    .where(
                        OrganizationVerificationEvent.verification_id == verification.id,
                        OrganizationVerificationEvent.occurred_at <= cutoff,
                    )
                    .order_by(
                        OrganizationVerificationEvent.occurred_at, OrganizationVerificationEvent.id
                    )
                )
            ).all()
            last_event = events[-1] if events else None
            records.append(
                AccountExportOrganizationVerificationDTO(
                    id=verification.id,
                    organization_id=verification.organization_id,
                    state=last_event.to_state if last_event is not None else "submitted",
                    material_claim_digest=verification.material_claim_digest,
                    submitted_at=verification.created_at,
                    reviewed_at=None,
                    expires_at=last_event.expires_at if last_event is not None else None,
                    events=[
                        {
                            "state": event.to_state,
                            "policy_version": event.policy_version,
                            "material_claim_digest": event.material_claim_digest,
                            "expires_at": event.expires_at,
                            "occurred_at": event.occurred_at,
                        }
                        for event in events
                    ],
                )
            )
        chunks: list[bytes] = []
        payload_size = 0
        for record in records:
            encoded = (record.model_dump_json() + "\n").encode("utf-8")
            payload_size += len(encoded)
            if payload_size > settings.account_export_max_bytes:
                raise HTTPException(status_code=413, detail="account_export_too_large")
            chunks.append(encoded)
        return b"".join(chunks)

    @app.post("/v1/account/export", include_in_schema=False)
    async def export_account(
        request: Request,
        principal: Principal = Depends(require_lifecycle_human),
    ) -> StreamingResponse:
        action = await lifecycle_empty_request(request)
        lifecycle_step_up(principal)
        cutoff = datetime.now(UTC)
        async with request.app.state.session_factory() as export_session:
            try:
                if export_session.get_bind().dialect.name == "postgresql":
                    await export_session.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    )
                await assert_account_access(
                    export_session, settings, principal.subject, mutation=True
                )
                payload = await account_export_payload(
                    export_session, request, principal.subject, cutoff
                )
                await consume_lifecycle_step_up(
                    export_session, principal, purpose="export", action=action
                )
                await export_session.commit()
            except Exception:
                await export_session.rollback()
                raise
        return StreamingResponse(
            iter([payload]),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=connectmd-account-export.ndjson"},
        )

    @app.post(
        "/v1/account-deletion-requests",
        include_in_schema=False,
        response_model=AccountDeletionRequestResponse,
        status_code=202,
    )
    async def create_account_deletion_request(
        request: Request,
        principal: Principal = Depends(require_lifecycle_human),
        session: AsyncSession = Depends(get_session),
    ) -> AccountDeletionRequestResponse:
        key = idempotency_key(request, required=True)
        assert key is not None
        action = await lifecycle_empty_request(request)
        reverification_id, _, _ = lifecycle_step_up(principal)
        reverification_hmac = lifecycle_hmac(settings, "reverification", reverification_id)
        subject_hmac = lifecycle_hmac(settings, "subject", principal.subject)
        request_idempotency_hmac = lifecycle_hmac(
            settings, "delete-request-key", f"{subject_hmac}:{key}"
        )
        existing = await session.scalar(
            select(AccountLifecycle)
            .where(AccountLifecycle.subject_hmac == subject_hmac)
            .with_for_update()
        )
        if existing is not None:
            if (
                existing.request_idempotency_hmac == request_idempotency_hmac
                and existing.state == "confirmation_pending"
                and existing.receipt_ciphertext is not None
                and existing.receipt_recovery_idempotency_hmac is None
            ):
                return lifecycle_receipt_response(
                    existing,
                    decrypt_lifecycle_receipt(
                        settings, deletion_id=existing.id, ciphertext=existing.receipt_ciphertext
                    ),
                )
            raise HTTPException(status_code=409, detail="account_deletion_request_exists")
        now = datetime.now(UTC)
        await consume_lifecycle_step_up(
            session, principal, purpose="delete_request", action=action, flush=False
        )
        receipt = new_lifecycle_receipt()
        lifecycle = AccountLifecycle(
            id=new_id(),
            subject_hmac=subject_hmac,
            request_idempotency_hmac=request_idempotency_hmac,
            receipt_hmac=lifecycle_hmac(settings, "status-receipt", receipt),
            state="confirmation_pending",
            provider_state="pending",
            backup_state="expiry_pending",
            policy_version=settings.account_lifecycle_policy_version,
            requested_at=now,
        )
        lifecycle.receipt_ciphertext = encrypt_lifecycle_receipt(
            settings, deletion_id=lifecycle.id, receipt=receipt
        )
        session.add(lifecycle)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            replayed = await session.scalar(
                select(AccountLifecycle).where(AccountLifecycle.subject_hmac == subject_hmac)
            )
            if (
                replayed is not None
                and replayed.request_idempotency_hmac == request_idempotency_hmac
                and replayed.receipt_recovery_idempotency_hmac is None
            ):
                if replayed.receipt_ciphertext is None:
                    raise HTTPException(
                        status_code=409, detail="account_deletion_request_exists"
                    ) from exc
                return lifecycle_receipt_response(
                    replayed,
                    decrypt_lifecycle_receipt(
                        settings, deletion_id=replayed.id, ciphertext=replayed.receipt_ciphertext
                    ),
                )
            used = await session.scalar(
                select(AccountReverificationUse.id).where(
                    AccountReverificationUse.reverification_id_hmac == reverification_hmac
                )
            )
            if used is not None:
                raise HTTPException(status_code=409, detail="reverification_already_used") from exc
            raise HTTPException(status_code=409, detail="account_deletion_request_exists") from exc
        return lifecycle_receipt_response(lifecycle, receipt)

    @app.post(
        "/v1/account-deletion-receipts/recover",
        include_in_schema=False,
        response_model=AccountDeletionRequestResponse,
    )
    async def recover_account_deletion_receipt(
        request: Request,
        principal: Principal = Depends(require_lifecycle_human),
        session: AsyncSession = Depends(get_session),
    ) -> AccountDeletionRequestResponse:
        key = idempotency_key(request, required=True)
        assert key is not None
        action = await lifecycle_empty_request(request)
        lifecycle_step_up(principal)
        subject_hmac = lifecycle_hmac(settings, "subject", principal.subject)
        recovery_hmac = lifecycle_hmac(settings, "receipt-recovery-key", f"{subject_hmac}:{key}")
        lifecycle = await session.scalar(
            select(AccountLifecycle)
            .where(
                AccountLifecycle.subject_hmac == subject_hmac,
                AccountLifecycle.state == "confirmation_pending",
            )
            .with_for_update()
        )
        if lifecycle is None or lifecycle.receipt_ciphertext is None:
            raise HTTPException(status_code=404, detail="account deletion receipt was not found")
        if lifecycle.receipt_recovery_idempotency_hmac == recovery_hmac:
            return lifecycle_receipt_response(
                lifecycle,
                decrypt_lifecycle_receipt(
                    settings, deletion_id=lifecycle.id, ciphertext=lifecycle.receipt_ciphertext
                ),
            )
        await consume_lifecycle_step_up(
            session,
            principal,
            purpose="delete_receipt_recover",
            action=action,
            flush=False,
        )
        receipt = new_lifecycle_receipt()
        lifecycle.receipt_hmac = lifecycle_hmac(settings, "status-receipt", receipt)
        lifecycle.receipt_ciphertext = encrypt_lifecycle_receipt(
            settings, deletion_id=lifecycle.id, receipt=receipt
        )
        lifecycle.receipt_recovery_idempotency_hmac = recovery_hmac
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=409, detail="account deletion receipt recovery conflicted"
            ) from exc
        return lifecycle_receipt_response(lifecycle, receipt)

    @app.post(
        "/v1/account/lifecycle-status",
        include_in_schema=False,
        response_model=AccountLifecycleStatusResponse,
    )
    async def account_lifecycle_status(
        request: Request,
    ) -> JSONResponse:
        if not settings.account_lifecycle_enabled:
            lifecycle_status_not_found()
        await lifecycle_empty_request(request)
        authorization = request.headers.get("Authorization")
        if authorization is None or not authorization.startswith("LifecycleReceipt "):
            lifecycle_status_not_found()
        receipt = authorization.removeprefix("LifecycleReceipt ")
        if not re.fullmatch(r"lr1_[A-Za-z0-9_-]{43}", receipt):
            lifecycle_status_not_found()
        receipt_hmac = lifecycle_hmac(settings, "status-receipt", receipt)
        async with request.app.state.session_factory() as session:
            lifecycle = await session.scalar(
                select(AccountLifecycle)
                .where(AccountLifecycle.receipt_hmac == receipt_hmac)
                .with_for_update()
            )
            if lifecycle is None:
                lifecycle_status_not_found()
            now = datetime.now(UTC)
            terminal_at = (
                lifecycle.terminal_at
                if lifecycle.terminal_at is None or lifecycle.terminal_at.tzinfo is not None
                else lifecycle.terminal_at.replace(tzinfo=UTC)
            )
            if lifecycle.state == "fully_erased":
                await validate_terminal_lifecycle_status(
                    request=request,
                    session=session,
                    lifecycle=lifecycle,
                    now=now,
                )
            receipt_expires_at = (
                terminal_at + timedelta(days=30) if terminal_at is not None else None
            )
            if receipt_expires_at is not None and now >= receipt_expires_at:
                lifecycle_status_not_found()
            ip = request.client.host if request.client is not None else "unavailable"
            ip_hmac = lifecycle_hmac(settings, "status-receipt-ip", ip)
            window = now.replace(second=0, microsecond=0)
            rate = await session.scalar(
                select(AccountLifecycleReceiptRateLimit)
                .where(
                    AccountLifecycleReceiptRateLimit.receipt_hmac == receipt_hmac,
                    AccountLifecycleReceiptRateLimit.ip_hmac == ip_hmac,
                    AccountLifecycleReceiptRateLimit.window_started_at == window,
                )
                .with_for_update()
            )
            if rate is None:
                rate = AccountLifecycleReceiptRateLimit(
                    deletion_id=lifecycle.id,
                    receipt_hmac=receipt_hmac,
                    ip_hmac=ip_hmac,
                    window_started_at=window,
                    request_count=1,
                    updated_at=now,
                )
                session.add(rate)
            elif rate.request_count >= 20:
                raise HTTPException(
                    status_code=429,
                    detail="rate limit exceeded",
                    headers={**lifecycle_status_headers, "Retry-After": "60"},
                )
            else:
                rate.request_count += 1
                rate.updated_at = now
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise HTTPException(
                    status_code=429,
                    detail="rate limit exceeded",
                    headers={**lifecycle_status_headers, "Retry-After": "60"},
                ) from exc
            state = "confirmed" if lifecycle.state == "concealed" else lifecycle.state
            payload = AccountLifecycleStatusResponse(
                state=cast(Any, state),
                observed_at=now,
                requested_at=lifecycle.requested_at,
                confirmed_at=lifecycle.confirmed_at,
                live_erased_at=lifecycle.live_erased_at,
                terminal_at=terminal_at,
                policy_version=lifecycle.policy_version,
                condition=(
                    "hold_active"
                    if lifecycle.state == "held"
                    else "retry_exhausted"
                    if lifecycle.state == "failed"
                    else None
                ),
                next_check_after_seconds=0 if lifecycle.state == "fully_erased" else 30,
                receipt_expires_at=receipt_expires_at,
            )
        return JSONResponse(
            content=payload.model_dump(mode="json"),
            headers=lifecycle_status_headers,
        )

    @app.post(
        "/v1/account-deletion-requests/{deletion_id}/confirm",
        include_in_schema=settings.account_lifecycle_enabled,
        openapi_extra=_social_openapi_extra(),
        response_model=AccountDeletionConfirmationResponse,
        status_code=202,
    )
    async def confirm_account_deletion_request(
        deletion_id: str,
        request: Request,
        claims: LifecycleConfirmationClaims = Depends(require_lifecycle_confirmation),
        session: AsyncSession = Depends(get_session),
    ) -> AccountDeletionConfirmationResponse | Response:
        action = await lifecycle_empty_request(request)
        key = idempotency_key(request, required=True)
        assert key is not None
        reverification_id, _, _ = lifecycle_step_up(claims)
        subject_hmac = lifecycle_hmac(settings, "subject", claims.subject)
        # This local alias contains only the route-private Clerk claims; it is
        # never passed through the general Principal/authentication pipeline.
        principal = claims
        # Authority is always acquired before a lifecycle row. This is shared
        # with backup registration, reconciliation, and hold admission.
        await session.execute(
            update(AccountBackupAuthority)
            .where(AccountBackupAuthority.id == ACCOUNT_BACKUP_AUTHORITY_ID)
            .values(updated_at=AccountBackupAuthority.updated_at)
        )
        backup_authority = await session.get(
            AccountBackupAuthority, ACCOUNT_BACKUP_AUTHORITY_ID, with_for_update=True
        )
        if backup_authority is None:
            raise HTTPException(
                status_code=503, detail="account lifecycle backup generation is not registered"
            )
        lifecycle = await session.scalar(
            select(AccountLifecycle)
            .where(
                AccountLifecycle.id == deletion_id,
                AccountLifecycle.subject_hmac == subject_hmac,
            )
            .with_for_update()
        )
        if lifecycle is None:
            raise HTTPException(status_code=404, detail="account deletion request was not found")
        confirmation_hmac = lifecycle_confirmation_hmac(
            deletion_id=deletion_id,
            subject_hmac=subject_hmac,
            idempotency_key_value=key,
        )
        stored_confirmation_hmac = lifecycle.confirmation_idempotency_hmac
        if stored_confirmation_hmac is not None:
            if not re.fullmatch(_SHA256_HEX_PATTERN, stored_confirmation_hmac):
                lifecycle_confirmation_unavailable()
            if not compare_digest(stored_confirmation_hmac, confirmation_hmac):
                raise HTTPException(
                    status_code=409,
                    detail="account deletion confirmation conflicted",
                )
            await validate_lifecycle_confirmation_replay(
                request=request,
                session=session,
                lifecycle=lifecycle,
                claims=claims,
                now=datetime.now(UTC),
            )
            return JSONResponse(
                content={"deletion_id": lifecycle.id},
                status_code=202,
                headers={"Idempotency-Replayed": "true"},
            )
        if lifecycle.state != "confirmation_pending":
            raise HTTPException(status_code=404, detail="account deletion request was not found")
        await assert_pending_lifecycle_access(session, subject_hmac=subject_hmac)
        claimed = await session.execute(
            update(AccountLifecycle)
            .where(
                AccountLifecycle.id == deletion_id,
                AccountLifecycle.subject_hmac == subject_hmac,
                AccountLifecycle.state == "confirmation_pending",
            )
            .values(state="erasure_planned")
            .execution_options(synchronize_session=False)
        )
        if getattr(claimed, "rowcount", 0) != 1:
            raise HTTPException(status_code=409, detail="account deletion request is not pending")
        lifecycle.state = "erasure_planned"
        existing_deny = await session.scalar(
            select(AccountAccessDeny)
            .where(AccountAccessDeny.subject_hmac == subject_hmac)
            .with_for_update()
        )
        if existing_deny is not None:
            raise HTTPException(status_code=409, detail="account deletion request is not pending")
        now = datetime.now(UTC)
        active_retention_holds = (
            await session.scalars(
                select(RetentionHold).where(RetentionHold.released_at.is_(None)).with_for_update()
            )
        ).all()
        holds_by_resource = {
            (retention_hold.resource_type, retention_hold.resource_id): retention_hold
            for retention_hold in active_retention_holds
        }
        # A hold on canonical content covers its immutable version rows too.
        # The lookup is completed before planning so both the planner and the
        # persisted inventory record the same retained-evidence boundary.
        held_document_ids = [
            hold.resource_id for hold in active_retention_holds if hold.resource_type == "document"
        ]
        if held_document_ids:
            document_versions = (
                await session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id.in_(held_document_ids)
                    )
                )
            ).all()
            for version in document_versions:
                document_hold = holds_by_resource[("document", version.document_id)]
                holds_by_resource.setdefault(("document_version", version.id), document_hold)
        held_post_ids = [
            hold.resource_id for hold in active_retention_holds if hold.resource_type == "post"
        ]
        if held_post_ids:
            held_post_versions = (
                await session.scalars(
                    select(PostVersion).where(PostVersion.post_id.in_(held_post_ids))
                )
            ).all()
            for post_version in held_post_versions:
                post_hold = holds_by_resource[("post", post_version.post_id)]
                holds_by_resource.setdefault(("post_version", post_version.id), post_hold)
        held_resources = set(holds_by_resource)
        planned_items: list[dict[str, Any]] = []
        document_versions_by_document: dict[str, set[str]] = {}
        post_versions_by_post: dict[str, set[str]] = {}

        def resource_is_held(resource_type: str, resource_id: str) -> bool:
            return (resource_type, resource_id) in held_resources

        def plan(
            resource_type: str,
            resource_id: str,
            phase: Literal[
                "conceal",
                "revoke",
                "detach",
                "delete_row",
                "delete_file",
                "unindex",
                "provider",
                "postcheck",
                "backup",
            ],
            *,
            disposition: Literal["delete", "detach", "hold"] = "delete",
            state: Literal["queued", "held", "completed"] = "queued",
        ) -> None:
            held_by: RetentionHold | None = None
            # A legal hold preserves canonical data, not an independently
            # derived public projection.  Concealment and exact unindexing
            # therefore continue while destructive row/file work is held.
            if resource_is_held(resource_type, resource_id) and phase in {
                "delete_row",
                "delete_file",
            }:
                held_by = holds_by_resource[(resource_type, resource_id)]
                disposition = "hold"
                state = "held"
            hold_kind = (
                "retention" if held_by is not None else ("policy" if state == "held" else None)
            )
            planned_items.append(
                {
                    "id": new_id(),
                    "deletion_id": lifecycle.id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "phase": phase,
                    "disposition": disposition,
                    "state": state,
                    "attempts": 0,
                    "hold_kind": hold_kind,
                    "hold_id": held_by.id if held_by is not None else None,
                    "hold_review_at": held_by.review_at if held_by is not None else None,
                    "available_at": now if state == "queued" else None,
                    "created_at": now,
                    "updated_at": now,
                    "completed_at": now if state == "completed" else None,
                }
            )

        def opaque_resource_id(resource_type: str, raw_reference: str) -> str:
            return lifecycle_hmac(settings, "erasure-resource", f"{resource_type}:{raw_reference}")

        def detached_reference(resource_type: str, resource_id: str, field: str) -> str:
            return lifecycle_hmac(
                settings,
                "detached-reference",
                f"{lifecycle.id}:{resource_type}:{resource_id}:{field}",
            )

        def hold_planned(resource_type: str, resource_id: str, phase: str) -> None:
            targets = {(resource_type, resource_id, phase)}
            if resource_type == "document":
                targets.update(
                    ("document_version", version_id, child_phase)
                    for version_id in document_versions_by_document.get(resource_id, set())
                    for child_phase in ("delete_file", "delete_row")
                )
            if resource_type == "post":
                targets.update(
                    ("post_version", version_id, child_phase)
                    for version_id in post_versions_by_post.get(resource_id, set())
                    for child_phase in ("delete_file", "delete_row")
                )
            for item in planned_items:
                if (
                    str(item["resource_type"]),
                    str(item["resource_id"]),
                    str(item["phase"]),
                ) in targets:
                    item["disposition"] = "hold"
                    item["state"] = "held"
                    item["available_at"] = None
                    item["completed_at"] = None
                    item["hold_kind"] = "policy"
                    item["hold_id"] = None
                    item["hold_review_at"] = None

        def retain_shared_record(resource_type: str, resource_id: str) -> None:
            """Keep a detached shared record without leaving destructive work blocked."""
            planned_items[:] = [
                item
                for item in planned_items
                if not (
                    item["resource_type"] == resource_type
                    and item["resource_id"] == resource_id
                    and item["phase"] == "delete_row"
                    and item["hold_kind"] != "retention"
                )
            ]

        def scrub_deleted_account_references(value: str, handles: set[str]) -> str:
            scrubbed = value.replace(principal.subject, "[deleted account]")
            for handle in sorted(handles, key=len, reverse=True):
                scrubbed = scrubbed.replace(handle, "[deleted account]")
            return scrubbed

        async def delete_if_unheld(resource_type: str, resource_id: str, row: Any) -> None:
            if not resource_is_held(resource_type, resource_id):
                await session.delete(row)

        async def persist_inventory() -> None:
            dialect_name = session.get_bind().dialect.name
            for values in planned_items:
                statement: Any
                if dialect_name == "postgresql":
                    statement = postgresql_insert(AccountErasureItem).values(**values)
                elif dialect_name == "sqlite":
                    statement = sqlite_insert(AccountErasureItem).values(**values)
                else:  # pragma: no cover - locked deployments use PostgreSQL or SQLite
                    raise HTTPException(
                        status_code=503, detail="account lifecycle inventory backend is unsupported"
                    )
                await session.execute(
                    statement.on_conflict_do_nothing(
                        index_elements=["deletion_id", "resource_type", "resource_id", "phase"]
                    )
                )

        async def persist_backup_obligation(manifest: AccountBackupManifest) -> None:
            values = {
                "id": new_id(),
                "deletion_id": lifecycle.id,
                "generation_id": manifest.generation_id,
                "generation_created_at": manifest.created_at,
                "generation_expires_at": manifest.expires_at,
                "db_manifest_digest": manifest.db_manifest_digest,
                "markdown_manifest_digest": manifest.markdown_manifest_digest,
                "state": "pending",
            }
            dialect_name = session.get_bind().dialect.name
            statement: Any
            if dialect_name == "postgresql":
                statement = postgresql_insert(AccountBackupObligation).values(**values)
            elif dialect_name == "sqlite":
                statement = sqlite_insert(AccountBackupObligation).values(**values)
            else:  # pragma: no cover - locked deployments use PostgreSQL or SQLite
                raise HTTPException(
                    status_code=503, detail="account lifecycle backup backend is unsupported"
                )
            await session.execute(
                statement.on_conflict_do_nothing(index_elements=["deletion_id", "generation_id"])
            )

        documents = (
            await session.scalars(
                select(Document).where(Document.owner_id == principal.subject).with_for_update()
            )
        ).all()
        for document in documents:
            document.visibility = "private"
            document.updated_at = now
            await remove_document_projection(session, document.id)
            await request.app.state.exact_search.remove_document(session, document.id)
            plan("document", document.id, "conceal", disposition="detach", state="completed")
            plan("document", document.id, "delete_row")
            plan("document", document.id, "unindex")
            versions = (
                await session.scalars(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document.id)
                    .with_for_update()
                )
            ).all()
            for document_version in versions:
                document_versions_by_document.setdefault(document.id, set()).add(
                    document_version.id
                )
                plan("document_version", document_version.id, "delete_file")
                plan("document_version", document_version.id, "delete_row")

        posts = (
            await session.scalars(
                select(Post).where(Post.owner_id == principal.subject).with_for_update()
            )
        ).all()
        for post in posts:
            post.status = "withdrawn"
            post.withdrawn_at = now
            post.updated_at = now
            plan("post", post.id, "conceal", disposition="detach", state="completed")
            plan("post", post.id, "delete_row")
            plan("post", post.id, "unindex")
            post_versions = (
                await session.scalars(
                    select(PostVersion).where(PostVersion.post_id == post.id).with_for_update()
                )
            ).all()
            for post_version in post_versions:
                post_versions_by_post.setdefault(post.id, set()).add(post_version.id)
                plan("post_version", post_version.id, "delete_file")
                plan("post_version", post_version.id, "delete_row")

        identities = (
            await session.scalars(
                select(AgentIdentity)
                .where(AgentIdentity.owner_id == principal.subject)
                .with_for_update()
            )
        ).all()
        for identity in identities:
            identity.status = "withdrawn"
            identity.withdrawn_at = now
            identity.updated_at = now
            plan("agent_identity", identity.id, "conceal", disposition="detach", state="completed")
            plan("agent_identity", identity.id, "delete_row")

        api_keys = (
            await session.scalars(
                select(ApiKey).where(ApiKey.owner_id == principal.subject).with_for_update()
            )
        ).all()
        for api_key in api_keys:
            api_key.revoked = True
            plan("api_key", api_key.id, "revoke", state="completed")
            plan("api_key", api_key.id, "delete_row")

        grants = (
            await session.scalars(
                select(AgentGrant).where(AgentGrant.owner_id == principal.subject).with_for_update()
            )
        ).all()
        for grant in grants:
            grant.revoked = True
            plan("agent_grant", grant.id, "revoke", state="completed")
            plan("agent_grant", grant.id, "delete_row")

        mandates = (
            await session.scalars(
                select(AgentMandate)
                .where(AgentMandate.owner_id == principal.subject)
                .with_for_update()
            )
        ).all()
        for mandate in mandates:
            if mandate.status == "active":
                mandate.status = "revoked"
                mandate.revoked_at = now
            plan("agent_mandate", mandate.id, "revoke", state="completed")
            plan("agent_mandate", mandate.id, "delete_row")

        policy = await session.get(ContactPolicy, principal.subject, with_for_update=True)
        if policy is None:
            policy = ContactPolicy(
                owner_id=principal.subject,
                allow_agent_requests=False,
                daily_request_limit=0,
                version=1,
                updated_at=now,
            )
            session.add(policy)
        else:
            policy.allow_agent_requests = False
            policy.daily_request_limit = 0
            policy.version += 1
            policy.updated_at = now
        plan("contact_policy", lifecycle.id, "revoke", disposition="detach", state="completed")
        plan("contact_policy", lifecycle.id, "delete_row")

        contact_requests = (
            await session.scalars(
                select(ContactRequest)
                .where(
                    or_(
                        ContactRequest.sender_owner_id == principal.subject,
                        ContactRequest.recipient_owner_id == principal.subject,
                        ContactRequest.sender_actor_id == principal.subject,
                        ContactRequest.decision_actor_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for contact_request in contact_requests:
            directly_related = (
                contact_request.sender_owner_id == principal.subject
                or contact_request.recipient_owner_id == principal.subject
            )
            if not directly_related:
                if contact_request.sender_actor_id == principal.subject:
                    contact_request.sender_actor_id = detached_reference(
                        "contact_request", contact_request.id, "sender_actor_id"
                    )
                if contact_request.decision_actor_id == principal.subject:
                    contact_request.decision_actor_id = None
                plan(
                    "contact_request",
                    contact_request.id,
                    "detach",
                    disposition="detach",
                    state="completed",
                )
            elif resource_is_held("contact_request", contact_request.id):
                contact_request.status = "blocked"
                contact_request.decided_at = now
                if contact_request.sender_owner_id == principal.subject:
                    contact_request.sender_owner_id = detached_reference(
                        "contact_request", contact_request.id, "sender_owner_id"
                    )
                if contact_request.recipient_owner_id == principal.subject:
                    contact_request.recipient_owner_id = detached_reference(
                        "contact_request", contact_request.id, "recipient_owner_id"
                    )
                if contact_request.sender_actor_id == principal.subject:
                    contact_request.sender_actor_id = detached_reference(
                        "contact_request", contact_request.id, "sender_actor_id"
                    )
                contact_request.sender_grant_id = None
                contact_request.sender_mandate_id = None
                contact_request.sender_identity_handle = None
                contact_request.sender_identity_display_name = None
                contact_request.target_identity_handle = None
                contact_request.target_identity_display_name = None
                contact_request.target_document_id = detached_reference(
                    "contact_request", contact_request.id, "target_document_id"
                )[:36]
                contact_request.purpose = "Deleted account"
                contact_request.message = "Deleted account"
                contact_request.decision_actor_id = None
                contact_request.report_reason = None
                plan(
                    "contact_request",
                    contact_request.id,
                    "detach",
                    disposition="hold",
                    state="held",
                )
                plan(
                    "contact_request",
                    contact_request.id,
                    "delete_row",
                    disposition="hold",
                    state="held",
                )
            else:
                plan(
                    "contact_request",
                    contact_request.id,
                    "detach",
                    disposition="detach",
                    state="completed",
                )
                plan("contact_request", contact_request.id, "delete_row", state="completed")
                await session.delete(contact_request)

        connection_requests = (
            await session.scalars(
                select(ConnectionRequest)
                .where(
                    or_(
                        ConnectionRequest.requester_owner_id == principal.subject,
                        ConnectionRequest.recipient_owner_id == principal.subject,
                        ConnectionRequest.requester_actor_id == principal.subject,
                        ConnectionRequest.decision_actor_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for connection_request in connection_requests:
            directly_related = (
                connection_request.requester_owner_id == principal.subject
                or connection_request.recipient_owner_id == principal.subject
            )
            if directly_related:
                connection_request.status = "blocked"
                connection_request.recipient_messaging_consent = False
                connection_request.decided_at = now
                connection_request.updated_at = now
                plan(
                    "connection_request",
                    connection_request.id,
                    "detach",
                    disposition="detach",
                    state="completed",
                )
                plan("connection_request", connection_request.id, "delete_row")
            else:
                if connection_request.requester_actor_id == principal.subject:
                    connection_request.requester_actor_id = detached_reference(
                        "connection_request", connection_request.id, "requester_actor_id"
                    )
                if connection_request.decision_actor_id == principal.subject:
                    connection_request.decision_actor_id = None
                plan(
                    "connection_request",
                    connection_request.id,
                    "detach",
                    disposition="detach",
                    state="completed",
                )

        connections = (
            await session.scalars(
                select(Connection)
                .where(
                    or_(
                        Connection.requester_owner_id == principal.subject,
                        Connection.recipient_owner_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for connection in connections:
            connection.status = "removed"
            connection.messaging_enabled = False
            connection.ended_at = now
            connection.ended_by_owner_id = principal.subject
            connection.updated_at = now
            plan("connection", connection.id, "detach", disposition="detach", state="completed")
            plan("connection", connection.id, "delete_row")
        connections_by_id = {connection.id: connection for connection in connections}
        connection_requests_by_id = {
            connection_request.id: connection_request for connection_request in connection_requests
        }
        deleted_profile_handles = {
            document.public_identifier for document in documents if document.kind == "profile"
        }
        deleted_profile_handles.update(identity.handle for identity in identities)
        deleted_profile_handles.update(
            request.requester_profile_handle
            for request in connection_requests
            if request.requester_owner_id == principal.subject
        )
        deleted_profile_handles.update(
            request.recipient_profile_handle
            for request in connection_requests
            if request.recipient_owner_id == principal.subject
        )
        deleted_profile_handles.update(
            connection.requester_profile_handle
            for connection in connections
            if connection.requester_owner_id == principal.subject
        )
        deleted_profile_handles.update(
            connection.recipient_profile_handle
            for connection in connections
            if connection.recipient_owner_id == principal.subject
        )

        conversations = (
            await session.scalars(
                select(Conversation)
                .where(
                    or_(
                        Conversation.pair_owner_low == principal.subject,
                        Conversation.pair_owner_high == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        conversation_message_ids: set[str] = set()
        for conversation in conversations:
            conversation.status = "closed"
            conversation.closed_at = now
            plan("conversation", conversation.id, "detach", disposition="detach", state="completed")
            plan("conversation", conversation.id, "delete_row")
            messages = (
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .with_for_update()
                )
            ).all()
            retained_counterparty_message = False
            for message in messages:
                conversation_message_ids.add(message.id)
                if message.sender_owner_id == principal.subject:
                    plan("message", message.id, "delete_row")
                else:
                    retained_counterparty_message = True
                    if message.sender_actor_id == principal.subject:
                        message.sender_actor_id = detached_reference(
                            "message", message.id, "sender_actor_id"
                        )
                    message.markdown = scrub_deleted_account_references(
                        message.markdown, deleted_profile_handles
                    )
                    message.content_sha256 = sha256(message.markdown.encode("utf-8")).hexdigest()
                    plan(
                        "message",
                        message.id,
                        "detach",
                        disposition="detach",
                        state="completed",
                    )
            if retained_counterparty_message:
                if conversation.pair_owner_low == principal.subject:
                    conversation.pair_owner_low = detached_reference(
                        "conversation", conversation.id, "pair_owner_low"
                    )
                if conversation.pair_owner_high == principal.subject:
                    conversation.pair_owner_high = detached_reference(
                        "conversation", conversation.id, "pair_owner_high"
                    )
                conversation.pair_owner_low, conversation.pair_owner_high = sorted(
                    (conversation.pair_owner_low, conversation.pair_owner_high)
                )
                if conversation.created_by_owner_id == principal.subject:
                    conversation.created_by_owner_id = detached_reference(
                        "conversation", conversation.id, "created_by_owner_id"
                    )
                retain_shared_record("conversation", conversation.id)
                retained_connection = connections_by_id.get(conversation.connection_id)
                if retained_connection is not None:
                    if retained_connection.requester_owner_id == principal.subject:
                        deleted_profile_handles.add(retained_connection.requester_profile_handle)
                        retained_connection.requester_owner_id = detached_reference(
                            "connection", retained_connection.id, "requester_owner_id"
                        )
                        retained_connection.requester_profile_handle = "deleted-account"
                    if retained_connection.recipient_owner_id == principal.subject:
                        deleted_profile_handles.add(retained_connection.recipient_profile_handle)
                        retained_connection.recipient_owner_id = detached_reference(
                            "connection", retained_connection.id, "recipient_owner_id"
                        )
                        retained_connection.recipient_profile_handle = "deleted-account"
                    retained_connection.pair_owner_low, retained_connection.pair_owner_high = (
                        sorted(
                            (
                                retained_connection.requester_owner_id,
                                retained_connection.recipient_owner_id,
                            )
                        )
                    )
                    if retained_connection.ended_by_owner_id == principal.subject:
                        retained_connection.ended_by_owner_id = detached_reference(
                            "connection", retained_connection.id, "ended_by_owner_id"
                        )
                    retain_shared_record("connection", retained_connection.id)
                    if retained_connection.connection_request_id in connection_requests_by_id:
                        retained_request = connection_requests_by_id[
                            retained_connection.connection_request_id
                        ]
                        if retained_request.requester_owner_id == principal.subject:
                            deleted_profile_handles.add(retained_request.requester_profile_handle)
                            retained_request.requester_owner_id = detached_reference(
                                "connection_request",
                                retained_request.id,
                                "requester_owner_id",
                            )
                            retained_request.requester_profile_handle = "deleted-account"
                        if retained_request.recipient_owner_id == principal.subject:
                            deleted_profile_handles.add(retained_request.recipient_profile_handle)
                            retained_request.recipient_owner_id = detached_reference(
                                "connection_request",
                                retained_request.id,
                                "recipient_owner_id",
                            )
                            retained_request.recipient_profile_handle = "deleted-account"
                        retained_request.pair_owner_low, retained_request.pair_owner_high = sorted(
                            (
                                retained_request.requester_owner_id,
                                retained_request.recipient_owner_id,
                            )
                        )
                        if retained_request.requester_actor_id == principal.subject:
                            retained_request.requester_actor_id = detached_reference(
                                "connection_request",
                                retained_request.id,
                                "requester_actor_id",
                            )
                        if retained_request.decision_actor_id == principal.subject:
                            retained_request.decision_actor_id = None
                        retain_shared_record("connection_request", retained_request.id)

        actor_messages = (
            await session.scalars(
                select(Message)
                .where(Message.sender_actor_id == principal.subject)
                .with_for_update()
            )
        ).all()
        for actor_message in actor_messages:
            if actor_message.id in conversation_message_ids:
                continue
            actor_message.sender_actor_id = detached_reference(
                "message", actor_message.id, "sender_actor_id"
            )
            plan("message", actor_message.id, "detach", disposition="detach", state="completed")

        for model, resource_type, condition in (
            (
                ProfileFollow,
                "profile_follow",
                or_(
                    ProfileFollow.follower_owner_id == principal.subject,
                    ProfileFollow.followed_owner_id == principal.subject,
                ),
            ),
            (
                ContactBlock,
                "contact_block",
                or_(
                    ContactBlock.blocker_owner_id == principal.subject,
                    ContactBlock.blocked_owner_id == principal.subject,
                ),
            ),
            (
                ConnectionBlock,
                "connection_block",
                or_(
                    ConnectionBlock.blocker_owner_id == principal.subject,
                    ConnectionBlock.blocked_owner_id == principal.subject,
                ),
            ),
            (
                PostContentBlock,
                "post_content_block",
                or_(
                    PostContentBlock.blocker_owner_id == principal.subject,
                    PostContentBlock.blocked_owner_id == principal.subject,
                ),
            ),
        ):
            rows = (await session.scalars(select(model).where(condition).with_for_update())).all()
            for row in cast(list[Any], rows):
                resource_id = cast(str, row.id)
                plan(resource_type, resource_id, "detach", disposition="detach", state="completed")
                if resource_is_held(resource_type, resource_id):
                    plan(resource_type, resource_id, "delete_row", disposition="hold", state="held")
                    if isinstance(row, ProfileFollow):
                        if row.follower_owner_id == principal.subject:
                            row.follower_owner_id = detached_reference(
                                resource_type, resource_id, "follower_owner_id"
                            )
                        if row.followed_owner_id == principal.subject:
                            row.followed_owner_id = detached_reference(
                                resource_type, resource_id, "followed_owner_id"
                            )
                        row.followed_profile_handle = "deleted-account"
                    else:
                        if row.blocker_owner_id == principal.subject:
                            row.blocker_owner_id = detached_reference(
                                resource_type, resource_id, "blocker_owner_id"
                            )
                        if row.blocked_owner_id == principal.subject:
                            row.blocked_owner_id = detached_reference(
                                resource_type, resource_id, "blocked_owner_id"
                            )
                else:
                    plan(resource_type, resource_id, "delete_row", state="completed")
                await delete_if_unheld(resource_type, resource_id, row)

        notifications = (
            await session.scalars(
                select(Notification)
                .where(
                    or_(
                        Notification.recipient_owner_id == principal.subject,
                        Notification.actor_owner_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for notification in notifications:
            plan("notification", notification.id, "detach", disposition="detach", state="completed")
            if resource_is_held("notification", notification.id):
                plan(
                    "notification", notification.id, "delete_row", disposition="hold", state="held"
                )
                notification.type = "account.removed"
                notification.actor_owner_id = None
                notification.resource_type = "account_deletion"
                notification.resource_id = detached_reference(
                    "notification", notification.id, "resource_id"
                )
            else:
                plan("notification", notification.id, "delete_row", state="completed")
            await delete_if_unheld("notification", notification.id, notification)

        graph_pair_locks = (
            await session.scalars(
                select(PostGraphPairLock)
                .where(
                    or_(
                        PostGraphPairLock.pair_owner_low == principal.subject,
                        PostGraphPairLock.pair_owner_high == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for pair_lock in graph_pair_locks:
            plan(
                "post_graph_pair_lock",
                opaque_resource_id(
                    "post_graph_pair_lock",
                    f"{pair_lock.pair_owner_low}:{pair_lock.pair_owner_high}",
                ),
                "delete_row",
                state="completed",
            )
            await delete_if_unheld(
                "post_graph_pair_lock",
                opaque_resource_id(
                    "post_graph_pair_lock",
                    f"{pair_lock.pair_owner_low}:{pair_lock.pair_owner_high}",
                ),
                pair_lock,
            )

        organizations = (
            await session.scalars(
                select(Organization)
                .where(Organization.owner_id == principal.subject)
                .with_for_update()
            )
        ).all()
        owned_job_ids: set[str] = set()
        owned_verification_ids: set[str] = set()
        for organization in organizations:
            organization.visibility = "private"
            organization.updated_at = now
            plan(
                "organization", organization.id, "conceal", disposition="detach", state="completed"
            )
            plan("organization", organization.id, "delete_row", disposition="hold", state="held")
            jobs = (
                await session.scalars(
                    select(Job).where(Job.organization_id == organization.id).with_for_update()
                )
            ).all()
            for job in jobs:
                owned_job_ids.add(job.id)
                job.status = "closed"
                job.published_at = None
                job.updated_at = now
                plan("job", job.id, "conceal", disposition="detach", state="completed")
                plan("job", job.id, "delete_row", disposition="hold", state="held")
                organization_applications = (
                    await session.scalars(
                        select(Application).where(Application.job_id == job.id).with_for_update()
                    )
                ).all()
                for application in organization_applications:
                    plan("application", application.id, "detach", disposition="hold", state="held")
                if organization_applications:
                    hold_planned("job", job.id, "delete_row")

            organization_memberships = (
                await session.scalars(
                    select(OrganizationMembership)
                    .where(OrganizationMembership.organization_id == organization.id)
                    .with_for_update()
                )
            ).all()
            if organization_memberships:
                hold_planned("organization", organization.id, "delete_row")

            verifications = (
                await session.scalars(
                    select(OrganizationVerification)
                    .where(OrganizationVerification.organization_id == organization.id)
                    .with_for_update()
                )
            ).all()
            for verification in verifications:
                owned_verification_ids.add(verification.id)
                plan(
                    "organization_verification",
                    verification.id,
                    "detach",
                    disposition="hold",
                    state="held",
                )
                evidence_rows = (
                    await session.scalars(
                        select(OrganizationVerificationEvidence)
                        .where(OrganizationVerificationEvidence.verification_id == verification.id)
                        .with_for_update()
                    )
                ).all()
                for evidence in evidence_rows:
                    plan(
                        "organization_verification_evidence",
                        evidence.id,
                        "delete_file",
                        disposition="hold",
                        state="held",
                    )
                verification_events = (
                    await session.scalars(
                        select(OrganizationVerificationEvent)
                        .where(OrganizationVerificationEvent.verification_id == verification.id)
                        .with_for_update()
                    )
                ).all()
                for verification_event in verification_events:
                    plan(
                        "organization_verification_event",
                        verification_event.id,
                        "detach",
                        disposition="hold",
                        state="held",
                    )

        submitted_verifications = (
            await session.scalars(
                select(OrganizationVerification)
                .where(OrganizationVerification.submitted_by_owner_id == principal.subject)
                .with_for_update()
            )
        ).all()
        for submitted_verification in submitted_verifications:
            if submitted_verification.id in owned_verification_ids:
                continue
            plan(
                "organization_verification",
                submitted_verification.id,
                "detach",
                disposition="hold",
                state="held",
            )
            submitted_evidence_rows = (
                await session.scalars(
                    select(OrganizationVerificationEvidence)
                    .where(
                        OrganizationVerificationEvidence.verification_id
                        == submitted_verification.id
                    )
                    .with_for_update()
                )
            ).all()
            for submitted_evidence in submitted_evidence_rows:
                plan(
                    "organization_verification_evidence",
                    submitted_evidence.id,
                    "delete_file",
                    disposition="hold",
                    state="held",
                )
            submitted_verification_events = (
                await session.scalars(
                    select(OrganizationVerificationEvent)
                    .where(
                        OrganizationVerificationEvent.verification_id == submitted_verification.id
                    )
                    .with_for_update()
                )
            ).all()
            for submitted_verification_event in submitted_verification_events:
                plan(
                    "organization_verification_event",
                    submitted_verification_event.id,
                    "detach",
                    disposition="hold",
                    state="held",
                )

        actor_verification_events = (
            await session.scalars(
                select(OrganizationVerificationEvent)
                .where(OrganizationVerificationEvent.actor_id == principal.subject)
                .with_for_update()
            )
        ).all()
        for actor_verification_event in actor_verification_events:
            plan(
                "organization_verification_event",
                actor_verification_event.id,
                "detach",
                disposition="hold",
                state="held",
            )

        applications = (
            await session.scalars(
                select(Application)
                .where(
                    or_(
                        Application.applicant_owner_id == principal.subject,
                        Application.applicant_actor_id == principal.subject,
                        Application.confirmed_by_owner_id == principal.subject,
                        Application.decision_actor_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for application in applications:
            if application.job_id in owned_job_ids:
                continue
            if application.applicant_owner_id == principal.subject:
                application.status = "withdrawn"
                application.decided_at = now
                application.updated_at = now
                plan(
                    "application", application.id, "detach", disposition="detach", state="completed"
                )
                if application.snapshot_storage_path is not None:
                    plan("application", application.id, "delete_file")
                plan("application", application.id, "delete_row")
            else:
                if application.applicant_actor_id == principal.subject:
                    application.applicant_actor_id = detached_reference(
                        "application", application.id, "applicant_actor_id"
                    )
                if application.confirmed_by_owner_id == principal.subject:
                    application.confirmed_by_owner_id = detached_reference(
                        "application", application.id, "confirmed_by_owner_id"
                    )
                if application.decision_actor_id == principal.subject:
                    application.decision_actor_id = None
                plan(
                    "application", application.id, "detach", disposition="detach", state="completed"
                )

        memberships = (
            await session.scalars(
                select(OrganizationMembership)
                .where(
                    or_(
                        OrganizationMembership.member_owner_id == principal.subject,
                        OrganizationMembership.invited_by_owner_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for membership in memberships:
            if membership.member_owner_id == principal.subject:
                plan(
                    "organization_membership",
                    membership.id,
                    "detach",
                    disposition="detach",
                    state="completed",
                )
                plan("organization_membership", membership.id, "delete_row", state="completed")
                await delete_if_unheld("organization_membership", membership.id, membership)
            else:
                membership.invited_by_owner_id = detached_reference(
                    "organization_membership", membership.id, "invited_by_owner_id"
                )
                plan(
                    "organization_membership",
                    membership.id,
                    "detach",
                    disposition="detach",
                    state="completed",
                )

        proposals = (
            await session.scalars(
                select(AgentProposal)
                .where(
                    or_(
                        AgentProposal.owner_id == principal.subject,
                        AgentProposal.submitter_actor_id == principal.subject,
                        AgentProposal.decision_actor_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for proposal in proposals:
            if proposal.owner_id == principal.subject:
                plan("agent_proposal", proposal.id, "delete_row", state="completed")
                await delete_if_unheld("agent_proposal", proposal.id, proposal)
            else:
                if proposal.submitter_actor_id == principal.subject:
                    proposal.submitter_actor_id = detached_reference(
                        "agent_proposal", proposal.id, "submitter_actor_id"
                    )
                if proposal.decision_actor_id == principal.subject:
                    proposal.decision_actor_id = None
                plan(
                    "agent_proposal", proposal.id, "detach", disposition="detach", state="completed"
                )

        change_events = (
            await session.scalars(
                select(ChangeEvent)
                .where(
                    or_(
                        ChangeEvent.owner_id == principal.subject,
                        ChangeEvent.actor_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for change_event in change_events:
            plan(
                "change_event",
                opaque_resource_id("change_event", str(change_event.sequence)),
                "delete_row",
                state="completed",
            )
            await delete_if_unheld(
                "change_event",
                opaque_resource_id("change_event", str(change_event.sequence)),
                change_event,
            )

        idempotency_records = (
            await session.scalars(
                select(IdempotencyRecord)
                .where(IdempotencyRecord.owner_id == principal.subject)
                .with_for_update()
            )
        ).all()
        for idempotency_record in idempotency_records:
            plan("idempotency_record", idempotency_record.id, "delete_row", state="completed")
            await delete_if_unheld("idempotency_record", idempotency_record.id, idempotency_record)

        post_reports = (
            await session.scalars(
                select(PostReport)
                .where(
                    or_(
                        PostReport.reporter_owner_id == principal.subject,
                        PostReport.post_id.in_([post.id for post in posts]),
                    )
                )
                .with_for_update()
            )
        ).all()
        moderation_cases = (
            await session.scalars(
                select(ModerationCase)
                .where(
                    or_(
                        ModerationCase.subject_owner_id == principal.subject,
                        ModerationCase.post_id.in_([post.id for post in posts]),
                    )
                )
                .with_for_update()
            )
        ).all()
        case_ids = [case.id for case in moderation_cases]
        for post_report in post_reports:
            plan("post_report", post_report.id, "detach", disposition="hold", state="held")
        for moderation_case in moderation_cases:
            plan("moderation_case", moderation_case.id, "detach", disposition="hold", state="held")
        moderation_decisions = (
            await session.scalars(
                select(ModerationDecision)
                .where(
                    or_(
                        ModerationDecision.case_id.in_(case_ids),
                        ModerationDecision.post_id.in_([post.id for post in posts]),
                        ModerationDecision.moderator_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        decision_ids = [decision.id for decision in moderation_decisions]
        for moderation_decision in moderation_decisions:
            plan(
                "moderation_decision",
                moderation_decision.id,
                "detach",
                disposition="hold",
                state="held",
            )
        moderation_appeals = (
            await session.scalars(
                select(ModerationAppeal)
                .where(
                    or_(
                        ModerationAppeal.subject_owner_id == principal.subject,
                        ModerationAppeal.case_id.in_(case_ids),
                        ModerationAppeal.decision_id.in_(decision_ids),
                        ModerationAppeal.appeal_reviewer_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for moderation_appeal in moderation_appeals:
            plan(
                "moderation_appeal",
                moderation_appeal.id,
                "detach",
                disposition="hold",
                state="held",
            )
        moderation_audits = (
            await session.scalars(
                select(ModerationAuditEvent)
                .where(
                    or_(
                        ModerationAuditEvent.case_id.in_(case_ids),
                        ModerationAuditEvent.post_id.in_([post.id for post in posts]),
                        ModerationAuditEvent.actor_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for moderation_audit in moderation_audits:
            plan(
                "moderation_audit_event",
                moderation_audit.id,
                "detach",
                disposition="hold",
                state="held",
            )
        post_moderation_events = (
            await session.scalars(
                select(PostModerationEvent)
                .where(
                    or_(
                        PostModerationEvent.post_id.in_([post.id for post in posts]),
                        PostModerationEvent.actor_id == principal.subject,
                    )
                )
                .with_for_update()
            )
        ).all()
        for post_moderation_event in post_moderation_events:
            plan(
                "post_moderation_event",
                post_moderation_event.id,
                "detach",
                disposition="hold",
                state="held",
            )

        held_moderation_post_ids = (
            {post_report.post_id for post_report in post_reports}
            | {moderation_case.post_id for moderation_case in moderation_cases}
            | {moderation_decision.post_id for moderation_decision in moderation_decisions}
            | {moderation_audit.post_id for moderation_audit in moderation_audits}
            | {post_moderation_event.post_id for post_moderation_event in post_moderation_events}
        )
        case_post_ids = {
            moderation_case.id: moderation_case.post_id for moderation_case in moderation_cases
        }
        held_moderation_post_ids.update(
            case_post_ids[moderation_appeal.case_id]
            for moderation_appeal in moderation_appeals
            if moderation_appeal.case_id in case_post_ids
        )
        for held_post_id in held_moderation_post_ids:
            hold_planned("post", held_post_id, "delete_row")

        for rate_model, resource_type, owner_column in (
            (PostRateBucket, "post_rate_bucket", PostRateBucket.owner_id),
            (FollowRateBucket, "follow_rate_bucket", FollowRateBucket.owner_id),
            (PostReportRateBucket, "post_report_rate_bucket", PostReportRateBucket.owner_id),
            (ContactRateBucket, "contact_rate_bucket", ContactRateBucket.sender_owner_id),
            (
                AgentOutreachRecipientRateBucket,
                "agent_outreach_recipient_rate_bucket",
                AgentOutreachRecipientRateBucket.recipient_owner_id,
            ),
            (
                ApplicationRateBucket,
                "application_rate_bucket",
                ApplicationRateBucket.applicant_owner_id,
            ),
            (
                ConnectionRequestRateBucket,
                "connection_request_rate_bucket",
                ConnectionRequestRateBucket.requester_owner_id,
            ),
            (MessageRateBucket, "message_rate_bucket", MessageRateBucket.sender_owner_id),
        ):
            rate_rows = (
                await session.scalars(
                    select(rate_model).where(owner_column == principal.subject).with_for_update()
                )
            ).all()
            for rate_row in cast(list[Any], rate_rows):
                plan(
                    resource_type,
                    opaque_resource_id(resource_type, str(rate_row.bucket_date)),
                    "delete_row",
                    state="completed",
                )
                await delete_if_unheld(
                    resource_type,
                    opaque_resource_id(resource_type, str(rate_row.bucket_date)),
                    rate_row,
                )

        inventory_resources = {
            (str(item["resource_type"]), str(item["resource_id"])) for item in planned_items
        }
        for retention_hold in active_retention_holds:
            if (
                retention_hold.resource_type,
                retention_hold.resource_id,
            ) in inventory_resources or retention_hold.authority == principal.subject:
                # The authority record is preserved in place.  It is not an
                # account-owned row to erase, and must not itself keep a
                # released account-erasure lifecycle permanently held.
                plan(
                    "retention_hold",
                    retention_hold.id,
                    "detach",
                    disposition="detach",
                    state="completed",
                )

        resource_conditions = [
            and_(
                LifecycleTask.resource_type == resource_type,
                LifecycleTask.resource_id == resource_id,
            )
            for resource_type, resource_id in inventory_resources
        ]
        if resource_conditions:
            lifecycle_tasks = (
                await session.scalars(
                    select(LifecycleTask).where(or_(*resource_conditions)).with_for_update()
                )
            ).all()
            for lifecycle_task in lifecycle_tasks:
                plan(
                    "lifecycle_task", lifecycle_task.id, "detach", disposition="hold", state="held"
                )
            tombstone_conditions = [
                and_(
                    RetentionTombstone.resource_type == resource_type,
                    RetentionTombstone.resource_id == resource_id,
                )
                for resource_type, resource_id in inventory_resources
            ]
            retention_tombstones = (
                await session.scalars(
                    select(RetentionTombstone).where(or_(*tombstone_conditions)).with_for_update()
                )
            ).all()
            for retention_tombstone in retention_tombstones:
                plan(
                    "retention_tombstone",
                    retention_tombstone.id,
                    "detach",
                    disposition="hold",
                    state="held",
                )

        lifecycle_tombstone = await session.scalar(
            select(AccountLifecycleTombstone)
            .where(AccountLifecycleTombstone.deletion_id == lifecycle.id)
            .with_for_update()
        )
        if lifecycle_tombstone is not None:
            plan(
                "account_lifecycle_tombstone",
                lifecycle_tombstone.id,
                "detach",
                disposition="hold",
                state="held",
            )

        active_manifests = (
            await session.scalars(
                select(AccountBackupManifest)
                .where(AccountBackupManifest.state == "active")
                .with_for_update()
            )
        ).all()
        current_manifest = next(
            (
                manifest
                for manifest in active_manifests
                if manifest.generation_id == backup_authority.current_generation_id
            ),
            None,
        )
        if current_manifest is None:
            raise HTTPException(
                status_code=503, detail="account lifecycle backup generation is not current"
            )
        for manifest in active_manifests:
            await persist_backup_obligation(manifest)
            plan("backup_manifest", manifest.id, "backup", disposition="hold")

        plan("provider_session", lifecycle.id, "provider", disposition="delete")
        plan("provider_user", lifecycle.id, "provider", disposition="delete")
        # The lifecycle and deny authorities remain durable; only the
        # post-check inventory projection is detached after mirror validation.
        plan("account_lifecycle", lifecycle.id, "postcheck", disposition="detach")
        plan("provider_subject_ciphertext", lifecycle.id, "delete_row")
        await persist_inventory()
        journal_appended = False
        try:
            await consume_lifecycle_step_up(
                session, principal, purpose="delete_confirm", action=action, flush=False
            )
            access_deny = AccountAccessDeny(
                subject_hmac=subject_hmac,
                deletion_id=lifecycle.id,
                denied_at=now,
            )
            session.add(access_deny)
            lifecycle.confirmed_at = now
            lifecycle.concealed_at = now
            lifecycle.provider_subject_ciphertext = encrypt_lifecycle_provider_subject(
                settings, deletion_id=lifecycle.id, subject=principal.subject
            )
            lifecycle.provider_session_ciphertext = encrypt_lifecycle_provider_session(
                settings, deletion_id=lifecycle.id, session_id=lifecycle_step_up(principal)[1]
            )
            lifecycle.receipt_ciphertext = None
            lifecycle.receipt_recovery_idempotency_hmac = None
            lifecycle.confirmation_idempotency_hmac = confirmation_hmac
            lifecycle.request_idempotency_hmac = None
            lifecycle.state = "concealed"
            # Resolve every database constraint before crossing the external,
            # irreversible commitment boundary. From this point until commit,
            # readiness remains closed and every restart rechecks DB parity.
            await session.flush()
            journal: DeletionCommitmentJournal | None = request.app.state.deletion_journal
            if journal is None:
                raise DeletionJournalError("deletion journal is unavailable")
            request.app.state.deletion_journal_consistent = False
            commitment = journal.append(
                deletion_id=lifecycle.id,
                subject=principal.subject,
                subject_hmac=subject_hmac,
                backup_generation_id=current_manifest.generation_id,
                backup_generation_created_at=current_manifest.created_at,
                committed_at=now,
                policy_version=lifecycle.policy_version,
            )
            journal_appended = True
            # A retry after a lost DB acknowledgement reuses the original
            # external commitment time so both authorities remain exact.
            lifecycle.confirmed_at = commitment.committed_at
            lifecycle.concealed_at = commitment.committed_at
            access_deny.denied_at = commitment.committed_at
            await session.commit()
            request.app.state.deletion_journal_consistent = True
        except DeletionJournalError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=503, detail="deletion commitment journal requires recovery"
            ) from exc
        except (IntegrityError, StaleDataError) as exc:
            await session.rollback()
            if journal_appended:
                raise HTTPException(
                    status_code=503, detail="deletion commitment journal requires recovery"
                ) from exc
            reverification_hmac = lifecycle_hmac(settings, "reverification", reverification_id)
            if (
                await session.scalar(
                    select(AccountReverificationUse.id).where(
                        AccountReverificationUse.reverification_id_hmac == reverification_hmac
                    )
                )
                is not None
            ):
                raise HTTPException(status_code=409, detail="reverification_already_used") from exc
            current = await session.scalar(
                select(AccountLifecycle).where(AccountLifecycle.subject_hmac == subject_hmac)
            )
            if current is None:
                raise HTTPException(
                    status_code=404, detail="account deletion request was not found"
                ) from exc
            if current is not None and current.state != "confirmation_pending":
                raise HTTPException(
                    status_code=409, detail="account deletion request is not pending"
                ) from exc
            raise HTTPException(
                status_code=409, detail="account deletion confirmation conflicted"
            ) from exc
        except Exception as exc:
            await session.rollback()
            if journal_appended:
                raise HTTPException(
                    status_code=503, detail="deletion commitment journal requires recovery"
                ) from exc
            raise
        return AccountDeletionConfirmationResponse(deletion_id=lifecycle.id)

    @app.post(
        "/v1/account-deletion-requests/{deletion_id}/cancel",
        include_in_schema=False,
        status_code=204,
    )
    async def cancel_account_deletion_request(
        deletion_id: str,
        request: Request,
        principal: Principal = Depends(require_lifecycle_human),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        await lifecycle_empty_request(request)
        await session.execute(
            delete(AccountLifecycleReceiptRateLimit).where(
                AccountLifecycleReceiptRateLimit.deletion_id == deletion_id
            )
        )
        deleted = await session.execute(
            delete(AccountLifecycle)
            .where(
                AccountLifecycle.id == deletion_id,
                AccountLifecycle.subject_hmac
                == lifecycle_hmac(settings, "subject", principal.subject),
                AccountLifecycle.state == "confirmation_pending",
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(deleted, "rowcount", 0) != 1:
            raise HTTPException(status_code=404, detail="account deletion request was not found")
        try:
            await session.commit()
        except StaleDataError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=404, detail="account deletion request was not found"
            ) from exc
        return Response(status_code=204)

    @app.get("/v1/capabilities", tags=["protocols"])
    async def capabilities() -> dict[str, Any]:
        capability_payload: dict[str, Any] = {
            "api_version": "v1",
            "document_formats": ["connect.md/profile", "connect.md/resume"],
            "post_formats": ["connect.md/post"],
            "canonical_markdown": {
                "profile_resume_max_utf8_bytes": canonical_document_max_utf8_bytes(),
                "measurement": "final_rendered_utf8_bytes_after_lf_canonicalization",
                "json_schema_max_length_is_not_byte_proof": True,
            },
            "ingestion": {
                "endpoint": "/v1/ingest",
                "published": False,
                "default_schema_version": 2,
                "legacy_v1_targets": ["connect.md/profile/v1", "connect.md/resume/v1"],
                "source_enrichment": False,
                "neutral_reference_scheme": "connectmd-user",
                "absent_work_modes": "empty_array",
                **ingest_capabilities(settings),
            },
            "authentication": {
                "bearer": ["clerk_jwt", "legacy_api_key", "agent_grant"],
                "oauth_authorization_server_implemented": False,
                "protected_resource_metadata": "/.well-known/oauth-protected-resource",
            },
            "conditional_writes": {
                "strong_etag": True,
                "if_match": True,
                "if_match_required": True,
            },
            "idempotency": {
                "durable": True,
                "header": "Idempotency-Key",
                "document_writes_required": True,
                "operations": [
                    "document.create",
                    "document.update",
                    "proposal.submit",
                    "proposal.decide",
                    "contact.request",
                    "organization.create",
                    "organization.update",
                    "organization.member_invite",
                    "job.create",
                    "job.update",
                    "job.lifecycle",
                    "application.submit",
                    "application.withdraw",
                    "application.decide",
                    "connection_request.create",
                    "connection_request.decide",
                    "connection.block",
                    "conversation.create",
                    "message.send",
                    "notification.mark_read",
                    "post.publish",
                    "post.withdraw",
                    "post.report",
                    "post.moderation_appeal",
                ],
            },
            "inventory": {"cursor": True, "endpoint": "/v1/documents"},
            "public_inventory": {
                "cursor": True,
                "endpoint": "/v1/public-documents",
                "search_independent": True,
            },
            "employer_inventory": {
                "human_only": True,
                "authentication": "clerk_jwt",
                "organizations_endpoint": "/v1/employer/organizations",
                "jobs_endpoint": "/v1/employer/jobs",
                "includes": ["manageable_organizations", "draft_published_closed_jobs"],
                "cursor": {
                    "signed": True,
                    "subject_bound": True,
                    "endpoint_bound": True,
                },
                "agent_access": False,
                "mcp": False,
                "a2a": False,
                "public_search": False,
                "sitemap": False,
            },
            "taxonomy_discovery": {
                "catalog_endpoint": "/v1/taxonomies",
                "terms_endpoint": "/v1/taxonomies/{taxonomy}",
                "authority": "current_public_v2_postgresql_projection",
                "anonymous": True,
                "no_store": True,
                "cursor": {"signed": True, "revision_bound": True, "max_length": 2048},
                "term_limit": {"minimum": 1, "maximum": 100, "default": 50},
                "query_max_length": 100,
                "discovery_only": True,
                "outreach_authority": False,
                "agent_tools": ["list_taxonomies", "list_taxonomy_terms"],
                "a2a_actions": ["list_taxonomies", "list_taxonomy_terms"],
            },
            "public_search": {
                "endpoint": "/v1/search",
                "json_endpoint": "/v1/search/query",
                "modes": {
                    "projection": {
                        "default": True,
                        "candidate_window": MAX_AGENT_SEARCH_RESULTS,
                        "complete": False,
                    },
                    "exact": {
                        "authority": "canonical_current_public_postgresql_projection",
                        "untyped_v1_compatibility": [
                            "q",
                            "kind",
                            "skills",
                            "location",
                            "updated_after",
                            "updated_before",
                        ],
                        "typed_taxonomy_requires_schema_version": 2,
                        "postgresql_required": True,
                        "max_complete_documents": 50_000,
                        "materialization_limit": 50_001,
                        "complete": True,
                        "cursor_max_length": EXACT_SEARCH_CURSOR_MAX_LENGTH,
                        "offset": 0,
                        "facet_limit": {"minimum": 1, "maximum": 500, "default": 100},
                        "no_meilisearch_fallback": True,
                    },
                },
                "canonical_field": "q",
                "legacy_protocol_query_alias": "query",
                "legacy_query_dual_supply": "rejected",
                "structured_canonical_max_length": 336,
                "get_compact_max_length": 80,
                "aggregate_repeated_value_cap": 50,
                "taxonomy_filters": [
                    "occupation_ids",
                    "industry_ids",
                    "skill_ids",
                    "language_ids",
                    "location_id",
                    "seniority_ids",
                    "seniority_id",
                    "work_modes",
                    "open_to",
                    "open_to_ids",
                    "organization_ids",
                    "representative_ids",
                ],
                "facets": {"max_items": 30, "taxonomy_authoritative": True},
                "taxonomy_authority": "current_public_v2_postgresql_projection",
                "taxonomy_discovery_only": True,
                "candidate_window": MAX_AGENT_SEARCH_RESULTS,
                "agent_capability_filter": {
                    "accepted": [_INTERNAL_CONTACT_REQUEST_CAPABILITY],
                    "sql_only": True,
                    "discovery_only": True,
                    "completeness": "bounded_to_candidate_window",
                },
                "agent_identity_reference": {
                    "fields": ["handle", "capabilities"],
                    "capabilities": [_INTERNAL_CONTACT_REQUEST_CAPABILITY],
                    "max_per_profile": _MAX_SEARCH_AGENT_IDENTITIES_PER_PROFILE,
                    "profiles_only": True,
                },
            },
            "change_feed": {"durable": True, "endpoint": "/v1/changes"},
            "agent_grants": {
                "named": True,
                "expiring": True,
                "resource_bound": True,
                "modes": ["proposal_only", "direct"],
                "resource_scope_matrix": {
                    resource_type: sorted(scopes)
                    for resource_type, scopes in AGENT_GRANT_RESOURCE_SCOPES.items()
                },
                "mandate_restriction": {
                    "resource_type": "owner",
                    "resource_id": None,
                    "mode": "direct",
                    "scopes": ["contacts:write"],
                    "scope_match": "exact",
                },
            },
            "agent_identities": {
                "public_endpoint": "/v1/agent-identities/{handle}",
                "directory_endpoint": "/v1/agent-directory",
                "profile_inventory_endpoint": "/v1/profiles/{handle}/agent-identities",
                "agent_tools": [
                    "get_agent_identity",
                    "list_agent_directory",
                    "list_profile_agents",
                ],
                "a2a_actions": [
                    "get_agent_identity",
                    "list_agent_directory",
                    "list_profile_agents",
                ],
                "cursor_pagination": {"bounded": True, "signed": True},
                "linked_public_profile_required": True,
                "public_fields": [
                    "handle",
                    "display_name",
                    "description",
                    "profile_handle",
                    "capabilities",
                ],
                "mandate_disclosure": False,
                "public_exclusions": [
                    "owner_id",
                    "grant",
                    "mandate",
                    "status",
                    "presence",
                    "external_endpoint",
                ],
            },
            "agent_outreach": {
                "endpoint": "/v1/agent-outreach",
                "status_endpoint": "/v1/agent-outreach/{request_id}",
                "mcp_tools": ["send_agent_outreach", "get_agent_outreach_status"],
                "external_statuses": ["pending", "accepted", "declined"],
                "internal_only": True,
                "idempotency_required": True,
                "mandate_bound_direct_grant": True,
                "human_recipient_decision_required": True,
                "rate_controls": {
                    "sender_daily": True,
                    "recipient_inbox_daily": "contact_policy.daily_request_limit",
                    "direct_peer_daily_limit": settings.agent_outreach_direct_peer_daily_limit,
                    "forwarded_client_ip_headers_trusted": True,
                    "end_user_ip_protection": True,
                    "trusted_proxy_topology": {
                        "proxy": "Nginx",
                        "allowlisted_source": "172.31.254.2",
                        "rightmost_untrusted": True,
                        "api_host_port_published": False,
                        "live_deployment_verified": False,
                    },
                },
                "external_delivery": False,
            },
            "contact_requests": {
                "internal_only": True,
                "consent_policy": True,
                "actions": ["accept", "reject", "block", "report"],
                "max_daily_requests": 20,
            },
            "organizations": {
                "representation": "json_only",
                "canonical_markdown": False,
                "recruiting_verification": {
                    "purpose": "recruiting_control",
                    "public_fields": ["active", "purpose", "expires_at"],
                    "owner_submission": "private_evidence_only",
                    "decisions": "configured_human_reviewer_only",
                    "url_fetch": False,
                },
                "membership": {
                    "invite_accept_required": True,
                    "roles": ["owner", "admin", "member"],
                    "human_only": True,
                    "invite_by": "public_profile_handle",
                    "recipient_inbox": "/v1/organization-membership-invitations",
                    "owner_inventory": "/v1/organizations/{organization_slug}/members",
                    "revoke_by": "membership_id",
                    "raw_owner_ids_exposed": False,
                    "agent_management": False,
                },
            },
            "jobs": {
                "public_search": "/v1/jobs",
                "lifecycle": ["draft", "published", "closed"],
                "publish_requires": [
                    "active_recruiting_control_decision",
                    "authorized_signed_in_human",
                ],
            },
            "applications": {
                "idempotency_required": True,
                "human_confirmation_required": True,
                "immutable_document_snapshot": {
                    "materialized_application_owned_markdown": True,
                    "binding": ["document_id", "version", "sha256"],
                    "formats": ["application/json", "text/markdown"],
                },
                "notes_in_list_responses": False,
                "applicant_list_and_detail": "signed_in_human_only",
                "employer_purpose_header": "X-Connectmd-Purpose: job_application_review",
                "employer_list_detail_decision_and_snapshot": "signed_in_human_only",
                "employer_access_requires": [
                    "active_recruiting_control_decision",
                    "authorized_organization_reviewer",
                ],
                "access_closes_on": [
                    "withdrawal",
                    "retention_expiry",
                    "recruiting_authority_loss",
                    "snapshot_integrity_failure",
                ],
                "application_change_events": {
                    "clerk_human": True,
                    "legacy_api_key": False,
                    "agent_grant": False,
                },
                "agent_read_or_decision": False,
                "public_search": False,
                "daily_limit": 20,
                "retention_policy_version": "application-retention-v1",
            },
            "connections": {
                "private": True,
                "human_only": True,
                "contact_request_separate": True,
                "pair_normalized": True,
                "messaging_consent_required": True,
                "request_daily_limit": 20,
                "public_counts_or_lists": False,
                "a2a_or_mcp_tools": False,
            },
            "conversations": {
                "private": True,
                "human_only": True,
                "one_per_connection": True,
                "requires_bilateral_messaging_consent": True,
                "message_format": "bounded_markdown",
                "message_daily_limit": 100,
                "presence": False,
                "read_receipts": False,
                "url_fetch_or_relay": False,
            },
            "notifications": {
                "recipient_private": True,
                "metadata_only": ["type", "actor_ref", "resource_ref", "timestamp", "read_at"],
                "social_change_feed": False,
                "webhook_delivery": False,
            },
            "posts": {
                "canonical_markdown": "connect.md/post",
                "separate_from_documents": True,
                "immutable_versions": [1],
                "public_visibility_only": True,
                "human_publication_only": True,
                "publication_idempotency_required": True,
                "daily_publication_limit": 10,
                "author_public_profile_required": True,
                "edits_replies_reactions_reposts_media": False,
                "direct_read": "/v1/posts/{post_id}",
                "profile_archive": "/v1/profiles/{handle}/posts",
                "public_inventory": {
                    "endpoint": "/v1/posts",
                    "anonymous": True,
                    "metadata_only": True,
                    "order": ["published_at DESC", "id DESC"],
                    "cursor": {"scope": "public_posts", "max_length": 500},
                    "page_size": {"minimum": 1, "maximum": 200, "default": 25},
                    "ranking_private_feed_or_total": False,
                    "post_markdown_body_in_meilisearch": False,
                    "mcp_or_a2a_actions": False,
                },
                "global_timeline": False,
                "search_or_meili_index": False,
                "mcp_or_a2a_actions": False,
                "crawler_inventory": True,
                "moderation": {
                    "reports_do_not_auto_sanction": True,
                    "case_linked_reports": True,
                    "subject_case_status": "/v1/moderation/cases",
                    "subject_appeal": "/v1/moderation/cases/{case_id}/appeals",
                    "appeal_window_days": 30,
                    "one_appeal_per_decision": True,
                    "human_only": True,
                    "agent_mcp_a2a_actions": False,
                    "authoritative_audit_ledger": "moderation_audit_events",
                    "pre_case_post_moderation_events": {
                        "preserved": True,
                        "appealable": False,
                        "current_decision": False,
                    },
                    "legacy_report_dispositions": {
                        "published": "open",
                        "withheld": "legacy_withheld",
                        "withdrawn": "legacy_withdrawn",
                    },
                    "sensitive_narrative_retention_policy": "post-moderation-case-retention-v1",
                    "public_report_counts": False,
                },
            },
            "follows": {
                "private": True,
                "human_only": True,
                "directed": True,
                "daily_follow_limit": 100,
                "public_counts_or_enumeration": False,
                "mutation_idempotency": {
                    "required_header": "Idempotency-Key",
                    "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    "operations": [
                        "POST /v1/follows/{profile_handle}",
                        "DELETE /v1/follows/{profile_handle}",
                        "POST /v1/content-blocks/{profile_handle}",
                        "DELETE /v1/content-blocks/{profile_handle}",
                    ],
                    "replay": "exact safe response only; state or authority mismatch fails closed",
                    "mcp_or_a2a_actions": False,
                },
                "feed": {
                    "endpoint": "/v1/feed",
                    "order": ["published_at DESC", "id DESC"],
                    "ranking_recommendation_tracking_presence": False,
                    "max_page_size": 50,
                },
                "content_blocks": {
                    "separate_from_connection_blocks": True,
                    "signed_in_feed_and_archive_suppression": "either_direction",
                    "removes_follows": "either_direction",
                    "anonymous_public_reads_may_remain_visible": True,
                },
                "exact_state": {
                    "endpoint": "/v1/profile-post-controls/{profile_handle}",
                    "human_only": True,
                    "fields": ["following", "content_blocked"],
                    "counts_or_reverse_graph": False,
                },
            },
            "protocols": {
                "a2a_agent_card": "/.well-known/agent-card.json",
                "a2a_http_json": "/a2a",
                "a2a_protocol_version": "1.0",
                "mcp": "/mcp",
            },
            "webhooks": {
                "registration": False,
                "outbound_delivery": False,
                "reason": "outbound delivery is intentionally disabled; poll the durable change feed",
            },
        }
        capability_payload["release_gates"] = {"verified_recruitment": settings.recruiting_enabled}
        if not settings.recruiting_enabled:
            for capability in ("employer_inventory", "organizations", "jobs", "applications"):
                capability_payload.pop(capability)
            capability_payload["agent_grants"]["resource_scope_matrix"].pop("organization")
            idempotent_operations = capability_payload["idempotency"]["operations"]
            capability_payload["idempotency"]["operations"] = [
                operation
                for operation in idempotent_operations
                if not operation.startswith(("organization.", "job.", "application."))
            ]
        return capability_payload

    @app.get("/v1/me", response_model=MeResponse, tags=["protocols"])
    async def me(principal: Principal = Depends(require_principal)) -> MeResponse:
        resource = None
        if principal.resource_type is not None:
            resource = {"type": principal.resource_type, "id": principal.resource_id}
        return MeResponse(
            owner_id=public_owner_id(principal.subject),
            actor_id=(
                principal.audit_actor_id
                if principal.grant_id
                else public_owner_id(principal.audit_actor_id)
            ),
            authentication_method=principal.method,
            scopes=sorted(principal.scopes),
            grant_id=principal.grant_id,
            grant_name=principal.grant_name,
            grant_mode=cast(Any, principal.grant_mode),
            resource=resource,
        )

    @app.get(
        "/v1/documents",
        response_model=OwnerDocumentListResponse,
        tags=["documents"],
    )
    async def list_owner_documents(
        request: Request,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        kind: Literal["profile", "resume"] | None = None,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OwnerDocumentListResponse:
        if principal.method in {"agent_api_key", "agent_grant"} and not (
            {"inventory:read", "documents:read"} & principal.scopes
        ):
            raise HTTPException(status_code=403, detail="agent credential lacks inventory scope")
        assert_agent_grant_resource_domain(principal, frozenset({"owner", "document"}))
        statement = (
            select(Document)
            .where(Document.owner_id == principal.subject)
            .options(selectinload(Document.versions))
        )
        if kind is not None:
            statement = statement.where(Document.kind == kind)
        if principal.method == "agent_grant" and principal.resource_type == "document":
            statement = statement.where(Document.id == principal.resource_id)
        document_cursor_bindings = cursor_principal_bindings(principal) + (
            kind or "",
            principal.resource_type or "",
            principal.resource_id or "",
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="documents",
                bindings=document_cursor_bindings,
                detail="document cursor is malformed",
            )
            try:
                updated_at = datetime.fromisoformat(str(payload["updated_at"]))
                document_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="document cursor is malformed") from exc
            statement = statement.where(
                or_(
                    Document.updated_at < updated_at,
                    and_(Document.updated_at == updated_at, Document.id < document_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(Document.updated_at.desc(), Document.id.desc()).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {"v": 1, "updated_at": last.updated_at.isoformat(), "id": last.id},
                scope="documents",
                bindings=document_cursor_bindings,
            )
        return OwnerDocumentListResponse(
            documents=[
                OwnerDocumentSummary(
                    id=row.id,
                    kind=cast(DocumentKind, row.kind),
                    identifier=row.public_identifier,
                    visibility=cast(Visibility, row.visibility),
                    version=row.current_version,
                    updated_at=row.updated_at,
                    markdown_url=markdown_url(row),
                    etag=strong_etag(current_version(row).sha256),
                )
                for row in page
            ],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/public-documents",
        response_model=PublicDocumentListResponse,
        tags=["documents"],
        summary="List public profile and resume URLs for crawlers",
    )
    async def list_public_documents(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        session: AsyncSession = Depends(get_session),
    ) -> PublicDocumentListResponse:
        statement = select(Document).where(Document.visibility == "public")
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="public_documents",
                detail="public document cursor is malformed",
            )
            try:
                updated_at = datetime.fromisoformat(str(payload["updated_at"]))
                document_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="public document cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    Document.updated_at < updated_at,
                    and_(Document.updated_at == updated_at, Document.id < document_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(Document.updated_at.desc(), Document.id.desc()).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {"v": 1, "updated_at": last.updated_at.isoformat(), "id": last.id},
                scope="public_documents",
            )
        return PublicDocumentListResponse(
            items=[
                PublicDocumentSummary(
                    kind=cast(DocumentKind, row.kind),
                    slug=row.public_identifier,
                    updated_at=row.updated_at,
                )
                for row in page
            ],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/changes",
        response_model=ChangeFeedResponse,
        tags=["protocols"],
    )
    async def changes(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ChangeFeedResponse:
        if principal.method in {"agent_api_key", "agent_grant"} and not (
            {"changes:read", "documents:read"} & principal.scopes
        ):
            raise HTTPException(status_code=403, detail="agent credential lacks change-feed scope")
        assert_agent_grant_resource_domain(principal, frozenset({"owner", "document"}))
        change_cursor_bindings = cursor_principal_bindings(principal)
        sequence = 0
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="changes",
                bindings=change_cursor_bindings,
                detail="change cursor is malformed",
            )
            value = payload.get("sequence")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise HTTPException(status_code=400, detail="change cursor is malformed")
            sequence = value
        statement = select(ChangeEvent).where(
            ChangeEvent.owner_id == principal.subject,
            ChangeEvent.sequence > sequence,
        )
        if principal.method != "clerk_jwt":
            statement = statement.where(
                ChangeEvent.resource_type.not_in(_NON_HUMAN_CHANGE_FEED_EXCLUDED_RESOURCE_TYPES)
            )
            for event_pattern in _NON_HUMAN_CHANGE_FEED_EXCLUDED_EVENT_PATTERNS:
                statement = statement.where(
                    ChangeEvent.event_type.not_like(event_pattern, escape="\\")
                )
        if principal.method == "agent_grant" and principal.resource_type == "document":
            statement = statement.where(ChangeEvent.resource_id == principal.resource_id)
        rows = (
            await session.scalars(statement.order_by(ChangeEvent.sequence.asc()).limit(limit + 1))
        ).all()
        page = rows[:limit]
        next_sequence = page[-1].sequence if page else sequence
        return ChangeFeedResponse(
            events=[public_change_event_projection(row, viewer=principal) for row in page],
            next_cursor=generic_cursor_encode(
                {"v": 1, "sequence": next_sequence},
                scope="changes",
                bindings=change_cursor_bindings,
            ),
            has_more=len(rows) > limit,
        )

    @app.get(
        "/v1/changes/recent",
        response_model=RecentChangeRecordResponse,
        tags=["protocols"],
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def recent_changes(
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> RecentChangeRecordResponse:
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="this private change operation requires a signed-in human",
            )
        rows = (
            await session.scalars(
                select(ChangeEvent)
                .where(ChangeEvent.owner_id == principal.subject)
                .order_by(ChangeEvent.sequence.desc())
                .limit(25)
            )
        ).all()
        return RecentChangeRecordResponse(
            events=[public_change_event_projection(row, viewer=principal) for row in rows]
        )

    @app.post(
        "/v1/ingest",
        response_model=IngestResponse,
        tags=["ingestion"],
        responses={
            403: _error_response("The agent key lacks document-write scope."),
            413: _error_response("The upload exceeds the configured byte limit."),
            415: _error_response("The upload type or MIME is unsupported."),
            422: _error_response(
                "The upload is invalid or conversion could not produce a valid draft."
            ),
            503: _error_response("The isolated conversion worker is unavailable."),
        },
    )
    async def ingest(
        request: Request,
        file: UploadFile = File(...),
        target_schema: str = Form(
            "connect.md/profile",
            description=(
                "Defaults to a v2 profile draft. Use connect.md/profile/v1 or "
                "connect.md/resume/v1 only for explicit legacy compatibility."
            ),
        ),
        principal: Principal = Depends(require_principal),
    ) -> IngestResponse:
        assert_scope(principal, "documents:write")
        normalized = _INGEST_TARGETS.get(target_schema)
        if normalized is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "target_schema must be connect.md/profile or connect.md/resume; "
                    "use the explicit /v1 suffix for legacy drafts (short aliases are also accepted)"
                ),
            )
        kind, canonical_schema, schema_version = normalized
        draft, warnings, provenance = await build_ingest_draft(
            file, kind, schema_version, request.app.state.settings, request.app.state.ingest_limiter
        )
        return IngestResponse(
            target_schema=canonical_schema,
            draft_markdown=draft,
            warnings=warnings,
            provenance=provenance,
        )

    app.include_router(taxonomy_router)

    @app.get(
        "/v1/search",
        response_model=SearchResponse,
        tags=["documents"],
        summary="Search public current profiles and resumes",
        responses={
            400: _error_response("The exact search cursor or mode is invalid."),
            401: _error_response("A supplied Bearer credential is invalid."),
            403: _error_response("The agent key lacks search-read scope."),
            409: _error_response("The exact search cursor is stale."),
            422: _error_response("The exact search candidate set is too broad."),
            503: _error_response("The exact search projection is temporarily unavailable."),
        },
    )
    async def search(
        request: Request,
        query: Annotated[str, Query(alias="q", max_length=200)] = "",
        mode: Literal["projection", "exact"] = Query(default="projection"),
        kind: Literal["profile", "resume"] | None = None,
        skills: list[SkillFilter] = Query(default=[], max_length=50),
        location: Annotated[str | None, Query(max_length=160)] = None,
        occupation_ids: list[SkillFilter] = Query(default=[], max_length=50),
        industry_ids: list[SkillFilter] = Query(default=[], max_length=50),
        skill_ids: list[SkillFilter] = Query(default=[], max_length=50),
        language_ids: list[SkillFilter] = Query(default=[], max_length=50),
        location_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        location_country_code: Annotated[str | None, Query(max_length=3)] = None,
        location_region: Annotated[str | None, Query(max_length=160)] = None,
        location_city: Annotated[str | None, Query(max_length=160)] = None,
        seniority_ids: list[SkillFilter] = Query(default=[], max_length=50),
        seniority_id: Annotated[str | None, Query(max_length=80)] = None,
        work_modes: list[SkillFilter] = Query(default=[], max_length=20),
        availability_status: Annotated[str | None, Query(max_length=80)] = None,
        availability_from: Annotated[str | None, Query(max_length=40)] = None,
        open_to: list[SkillFilter] = Query(default=[], max_length=50),
        open_to_ids: list[SkillFilter] = Query(default=[], max_length=50),
        organization_ids: list[SkillFilter] = Query(default=[], max_length=50),
        representative_ids: list[SkillFilter] = Query(default=[], max_length=50),
        representation_status: Annotated[str | None, Query(max_length=80)] = None,
        contact_disclosure: Annotated[str | None, Query(max_length=80)] = None,
        agent_capability: Literal["internal_contact_request"] | None = Query(
            default=None,
            description=(
                "Discovery-only filter for public profiles with an eligible internal-contact "
                "Agent Identity."
            ),
        ),
        updated_after: Annotated[str | None, Query(max_length=40)] = None,
        updated_before: Annotated[str | None, Query(max_length=40)] = None,
        sort_updated: Literal["asc", "desc"] | None = None,
        facets: list[SkillFilter] = Query(default=[], max_length=30),
        offset: Annotated[int, Query(ge=0, le=1000)] = 0,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
        facet_limit: Annotated[int, Query(ge=1, le=500)] = 100,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> SearchResponse:
        if principal is not None:
            assert_scope(principal, "search:read")
        reject_duplicate_cursor_query_parameter(request)
        if len(request.query_params.getlist("location_id")) > 1:
            raise HTTPException(status_code=422, detail="location_id accepts one value")
        normalized_location_id = None if location_id is None else location_id.strip()
        if location_id is not None and not normalized_location_id:
            raise HTTPException(status_code=422, detail="location_id accepts one value")
        arguments = {
            "q": query,
            "mode": mode,
            "kind": kind,
            "skills": skills,
            "location": location,
            "occupation_ids": occupation_ids,
            "industry_ids": industry_ids,
            "skill_ids": skill_ids,
            "language_ids": language_ids,
            "location_id": normalized_location_id,
            "location_country_code": location_country_code,
            "location_region": location_region,
            "location_city": location_city,
            "seniority_ids": seniority_ids,
            "seniority_id": seniority_id,
            "work_modes": work_modes,
            "availability_status": availability_status,
            "availability_from": availability_from,
            "open_to": open_to,
            "open_to_ids": open_to_ids,
            "organization_ids": organization_ids,
            "representative_ids": representative_ids,
            "representation_status": representation_status,
            "contact_disclosure": contact_disclosure,
            "agent_capability": agent_capability,
            "updated_after": updated_after,
            "updated_before": updated_before,
            "sort_updated": sort_updated,
            "facets": facets,
            "offset": offset,
            "limit": limit,
            "cursor": cursor,
            "facet_limit": facet_limit,
        }
        try:
            return await execute_public_search(
                request, session, arguments, allow_long_canonical=False
            )
        except SearchUnavailable as exc:
            return rest_search_unavailable(exc, offset=offset, limit=limit)
        except ExactSearchCursorMalformed as exc:
            raise HTTPException(status_code=400, detail="exact search cursor is malformed") from exc
        except ExactSearchCursorStale as exc:
            raise HTTPException(status_code=409, detail="exact search cursor is stale") from exc
        except ExactSearchTooBroad as exc:
            raise HTTPException(status_code=422, detail=EXACT_SEARCH_TOO_BROAD_MESSAGE) from exc
        except ExactSearchUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="exact search is temporarily unavailable"
            ) from exc
        except TaxonomyUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="public taxonomy is temporarily unavailable"
            ) from exc
        except (TaxonomyInvalidValue, TaxonomyUnknown) as exc:
            raise HTTPException(status_code=422, detail="search filters are invalid") from exc

    @app.post(
        "/v1/search/query",
        response_model=SearchResponse,
        tags=["documents"],
        summary="Search public profiles and resumes with a JSON query",
        responses={
            400: _error_response("The exact search cursor or mode is invalid."),
            401: _error_response("A supplied Bearer credential is invalid."),
            403: _error_response("The agent key lacks search-read scope."),
            409: _error_response("The exact search cursor is stale."),
            422: _error_response("The exact search candidate set is too broad."),
            503: _error_response("The exact search projection is temporarily unavailable."),
        },
    )
    async def search_query(
        body: SearchQueryRequest,
        request: Request,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> SearchResponse:
        if principal is not None:
            assert_scope(principal, "search:read")
        arguments = body.model_dump()
        try:
            return await execute_public_search(
                request, session, arguments, allow_long_canonical=True
            )
        except SearchUnavailable as exc:
            return rest_search_unavailable(
                exc,
                offset=body.offset,
                limit=body.limit,
            )
        except ExactSearchCursorMalformed as exc:
            raise HTTPException(status_code=400, detail="exact search cursor is malformed") from exc
        except ExactSearchCursorStale as exc:
            raise HTTPException(status_code=409, detail="exact search cursor is stale") from exc
        except ExactSearchTooBroad as exc:
            raise HTTPException(status_code=422, detail=EXACT_SEARCH_TOO_BROAD_MESSAGE) from exc
        except ExactSearchUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="exact search is temporarily unavailable"
            ) from exc
        except TaxonomyUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="public taxonomy is temporarily unavailable"
            ) from exc
        except (TaxonomyInvalidValue, TaxonomyUnknown) as exc:
            raise HTTPException(status_code=422, detail="search filters are invalid") from exc

    def grant_response(
        row: AgentGrant, *, key: str | None = None
    ) -> AgentGrantResponse | AgentGrantCreatedResponse:
        resource = AgentGrantResource(type=cast(Any, row.resource_type), id=row.resource_id)
        common = {
            "id": row.id,
            "name": row.name,
            "prefix": row.prefix,
            "scopes": json.loads(row.scopes),
            "mode": cast(Any, row.mode),
            "resource": resource,
            "expires_at": row.expires_at,
            "created_at": row.created_at,
        }
        if key is not None:
            return AgentGrantCreatedResponse(**common, key=key)
        return AgentGrantResponse(
            **common,
            revoked=row.revoked,
            last_used_at=row.last_used_at,
        )

    def grant_recovery_response(row: AgentGrant) -> AgentGrantRecoveryResponse:
        return AgentGrantRecoveryResponse(
            id=row.id,
            name=row.name,
            prefix=row.prefix,
            scopes=json.loads(row.scopes),
            mode=cast(Any, row.mode),
            resource=AgentGrantResource(type=cast(Any, row.resource_type), id=row.resource_id),
            expires_at=row.expires_at,
            recovery_required=True,
            created_at=row.created_at,
        )

    @app.post(
        "/v1/agent-grants",
        response_model=AgentGrantCreatedResponse | AgentGrantRecoveryResponse,
        status_code=201,
        tags=["agent-grants"],
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response("Only an authenticated Clerk human can create agent grants."),
            404: _error_response("The selected grant resource was not found."),
            409: _error_response("The idempotency key conflicts with an existing operation."),
            422: _error_response("The grant intent or expiry is invalid."),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("The durable agent-grant receipt is unavailable."),
        },
        openapi_extra={
            "x-connectmd-human-only": True,
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                }
            ],
        },
    )
    async def create_agent_grant(
        body: AgentGrantCreateRequest,
        request: Request,
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can create agent grants"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> AgentGrantCreatedResponse | AgentGrantRecoveryResponse | Response:
        key = idempotency_key(request, required=True)
        assert key is not None
        operation = "POST:/v1/agent-grants"
        normalized_name = body.name.strip()
        normalized_scopes = sorted({str(scope) for scope in body.scopes})
        if body.expires_at is not None:
            if body.expires_at.tzinfo is None:
                raise HTTPException(status_code=422, detail="expires_at must include a timezone")
            expiry_intent: dict[str, Any] = {
                "kind": "absolute",
                "value": body.expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            }
        else:
            expiry_intent = {
                "kind": "relative_seconds",
                "value": body.expires_in_seconds
                if body.expires_in_seconds is not None
                else 2_592_000,
            }
        resource_intent = {
            "id": body.resource.id,
            "type": body.resource.type,
        }
        fingerprint = _request_fingerprint(
            operation,
            json.dumps(
                {
                    "endpoint": operation,
                    "expires": expiry_intent,
                    "mode": body.mode,
                    "name": normalized_name,
                    "resource": resource_intent,
                    "scopes": normalized_scopes,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        with session.no_autoflush:
            existing = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == principal.subject,
                    IdempotencyRecord.idempotency_key == key,
                )
            )
        if existing is not None and (
            existing.operation != operation or existing.request_hash != fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used for a different request",
            )
        grant_context = {
            "resource_type": body.resource.type,
            "resource_id": body.resource.id or "",
        }
        if existing is not None:
            return await agent_grant_recovery_replay(session, principal, existing, grant_context)
        if body.resource.type == "document":
            if not body.resource.id:
                raise HTTPException(
                    status_code=422, detail="document resources require resource.id"
                )
            document = await session.scalar(
                select(Document)
                .where(Document.id == body.resource.id, Document.owner_id == principal.subject)
                .with_for_update()
            )
            if document is None:
                raise HTTPException(status_code=404, detail="document resource was not found")
            resource_id = document.id
        elif body.resource.type == "organization":
            if not body.resource.id:
                raise HTTPException(
                    status_code=422, detail="organization resources require resource.id"
                )
            organization = await session.scalar(
                select(Organization).where(Organization.id == body.resource.id).with_for_update()
            )
            if organization is None:
                raise HTTPException(status_code=404, detail="organization resource was not found")
            role = await organization_role(session, organization, principal)
            if role is None:
                raise HTTPException(status_code=404, detail="organization resource was not found")
            resource_id = organization.id
        else:
            if body.resource.id is not None:
                raise HTTPException(status_code=422, detail="owner resources do not accept an id")
            resource_id = None
        if not agent_grant_definition_is_valid(
            resource_type=body.resource.type,
            resource_id=resource_id,
            scopes=frozenset(normalized_scopes),
            mode=body.mode,
            mandate_id=None,
        ):
            raise HTTPException(
                status_code=422,
                detail="agent grant scopes are incompatible with the selected resource",
            )
        now = datetime.now(UTC)
        if body.expires_at is not None:
            expires_at = body.expires_at
            if expires_at.tzinfo is None:
                raise HTTPException(status_code=422, detail="expires_at must include a timezone")
            expires_at = expires_at.astimezone(UTC)
        else:
            expires_at = now + timedelta(
                seconds=body.expires_in_seconds
                if body.expires_in_seconds is not None
                else 2_592_000
            )
        if expires_at <= now or expires_at > now + timedelta(days=90):
            raise HTTPException(
                status_code=422,
                detail="agent grant expiry must be in the future and no more than 90 days away",
            )
        replay = await idempotency_replay(
            session,
            request,
            principal,
            key,
            operation,
            fingerprint,
            agent_grant_context=grant_context,
        )
        if replay is not None:
            return replay
        try:
            row, raw_key = await request.app.state.agent_grants.create(
                session,
                owner_id=principal.subject,
                actor_id=principal.audit_actor_id,
                name=normalized_name,
                scopes=normalized_scopes,
                mode=body.mode,
                resource_type=body.resource.type,
                resource_id=resource_id,
                expires_at=expires_at,
                commit=False,
            )
        except AuthenticationUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        result = grant_response(row, key=raw_key)
        assert isinstance(result, AgentGrantCreatedResponse)
        safe_recovery = grant_recovery_response(row)
        recovery_body = idempotency_replay_json(safe_recovery)
        try:
            receipt_resource_id = _agent_grant_recovery_resource_id(
                row, principal.subject, normalized_scopes, recovery_body
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=503, detail="agent-grant receipt is unavailable"
            ) from exc
        try:
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=201,
                body="",
                headers={},
                resource_type="agent_grant_recovery",
                resource_id=receipt_resource_id,
                agent_grant_context=grant_context,
            )
        except ConcurrentIdempotencyReplay as exc:
            return exc.response
        return result

    @app.get(
        "/v1/agent-grants",
        response_model=list[AgentGrantResponse],
        tags=["agent-grants"],
    )
    async def list_agent_grants(
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can list agent grants"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> list[AgentGrantResponse]:
        rows = (
            await session.scalars(
                select(AgentGrant)
                .where(AgentGrant.owner_id == principal.subject)
                .order_by(AgentGrant.created_at.desc(), AgentGrant.id.desc())
            )
        ).all()
        results: list[AgentGrantResponse] = []
        for row in rows:
            value = grant_response(row)
            assert isinstance(value, AgentGrantResponse)
            results.append(value)
        return results

    @app.delete(
        "/v1/agent-grants/{grant_id}",
        status_code=204,
        tags=["agent-grants"],
    )
    async def revoke_agent_grant(
        grant_id: str,
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can revoke agent grants"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        row = await session.scalar(
            select(AgentGrant).where(
                AgentGrant.id == grant_id, AgentGrant.owner_id == principal.subject
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="agent grant was not found")
        if not row.revoked:
            row.revoked = True
            session.add(
                ChangeEvent(
                    owner_id=principal.subject,
                    event_type="agent_grant.revoked",
                    resource_type="agent_grant",
                    resource_id=row.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    payload=json.dumps({"name": row.name}, sort_keys=True),
                )
            )
            await session.commit()
        return Response(status_code=204)

    def agent_identity_response(
        identity: AgentIdentity, profile: Document
    ) -> AgentIdentityResponse:
        return AgentIdentityResponse(
            handle=identity.handle,
            display_name=identity.display_name,
            description=identity.description,
            profile_handle=profile.public_identifier,
            capabilities=["internal_contact_request"],
        )

    async def live_agent_identity(
        session: AsyncSession, handle: str
    ) -> tuple[AgentIdentity, Document] | None:
        result = await session.execute(
            select(AgentIdentity, Document)
            .join(Document, Document.id == AgentIdentity.profile_document_id)
            .where(
                AgentIdentity.handle == handle,
                AgentIdentity.status == "active",
                Document.kind == "profile",
                Document.visibility == "public",
                Document.owner_id == AgentIdentity.owner_id,
            )
        )
        row = result.first()
        if row is None:
            return None
        return cast(AgentIdentity, row[0]), cast(Document, row[1])

    def agent_directory_cursor_encode(
        *, created_at: datetime, identity_id: str, query: str, profile_handle: str | None
    ) -> str:
        payload = _cursor_encode(
            {
                "v": 1,
                "s": "ad",
                "c": created_at.isoformat(),
                "i": identity_id,
                "b": sha256(f"{query}\n{profile_handle or ''}".encode()).hexdigest(),
            }
        )
        signature = (
            urlsafe_b64encode(
                hmac_new(
                    app.state.agent_directory_cursor_secret,
                    payload.encode("ascii"),
                    sha256,
                ).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        return f"{payload}.{signature}"

    def agent_directory_cursor_decode(
        cursor: str, *, query: str, profile_handle: str | None
    ) -> tuple[datetime, str]:
        if not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 500:
            raise HTTPException(status_code=400, detail="agent directory cursor is malformed")
        try:
            encoded, supplied_signature = cursor.rsplit(".", 1)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="agent directory cursor is malformed"
            ) from exc
        expected_signature = (
            urlsafe_b64encode(
                hmac_new(
                    app.state.agent_directory_cursor_secret,
                    encoded.encode("ascii"),
                    sha256,
                ).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        if not compare_digest(supplied_signature, expected_signature):
            raise HTTPException(status_code=400, detail="agent directory cursor is malformed")
        payload = _cursor_decode(encoded)
        try:
            if (
                payload["v"] != 1
                or payload["s"] != "ad"
                or not compare_digest(
                    str(payload["b"]),
                    sha256(f"{query}\n{profile_handle or ''}".encode()).hexdigest(),
                )
            ):
                raise ValueError
            created_at = datetime.fromisoformat(str(payload["c"]))
            identity_id = str(payload["i"])
            if not identity_id:
                raise ValueError
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="agent directory cursor is malformed"
            ) from exc
        return created_at, identity_id

    def agent_directory_statement(*, query: str, profile_handle: str | None) -> Any:
        statement = (
            select(AgentIdentity, Document)
            .join(Document, Document.id == AgentIdentity.profile_document_id)
            .where(*public_agent_identity_eligibility_filters())
        )
        if profile_handle is not None:
            statement = statement.where(Document.public_identifier == profile_handle)
        if query:
            escaped_query = (
                query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            statement = statement.where(
                or_(
                    func.lower(AgentIdentity.handle).like(f"%{escaped_query}%", escape="\\"),
                    func.lower(AgentIdentity.display_name).like(f"%{escaped_query}%", escape="\\"),
                    func.lower(AgentIdentity.description).like(f"%{escaped_query}%", escape="\\"),
                )
            )
        return statement

    def reject_duplicate_cursor_query_parameter(request: Request) -> None:
        if len(request.query_params.getlist("cursor")) > 1:
            raise HTTPException(status_code=422, detail="cursor accepts one value")

    async def list_public_agent_identities(
        session: AsyncSession,
        *,
        query: str,
        profile_handle: str | None,
        limit: int,
        cursor: str | None,
    ) -> AgentIdentityDirectoryResponse:
        statement = agent_directory_statement(query=query, profile_handle=profile_handle)
        if cursor is not None:
            created_at, identity_id = agent_directory_cursor_decode(
                cursor, query=query, profile_handle=profile_handle
            )
            anchor = (
                await session.execute(
                    statement.where(
                        AgentIdentity.id == identity_id,
                        AgentIdentity.created_at == created_at,
                    )
                    .limit(1)
                    .with_for_update()
                )
            ).first()
            if anchor is None:
                raise HTTPException(status_code=400, detail="agent directory cursor is malformed")
            statement = statement.where(
                or_(
                    AgentIdentity.created_at < created_at,
                    and_(
                        AgentIdentity.created_at == created_at,
                        AgentIdentity.id < identity_id,
                    ),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(AgentIdentity.created_at.desc(), AgentIdentity.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last_identity = cast(AgentIdentity, page[-1][0])
            next_cursor = agent_directory_cursor_encode(
                created_at=last_identity.created_at,
                identity_id=last_identity.id,
                query=query,
                profile_handle=profile_handle,
            )
        return AgentIdentityDirectoryResponse(
            identities=[
                agent_identity_response(cast(AgentIdentity, identity), cast(Document, profile))
                for identity, profile in page
            ],
            next_cursor=next_cursor,
        )

    def reject_unknown_directory_query_parameters(
        request: Request, allowed: frozenset[str]
    ) -> None:
        if set(request.query_params) - allowed:
            raise HTTPException(
                status_code=422, detail="directory request has unknown query parameters"
            )

    @app.get(
        "/v1/agent-directory",
        response_model=AgentIdentityDirectoryResponse,
        tags=["agent-identities"],
    )
    async def list_agent_directory(
        request: Request,
        query: Annotated[str, Query(alias="q", max_length=100)] = "",
        profile_handle: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        session: AsyncSession = Depends(get_session),
    ) -> AgentIdentityDirectoryResponse:
        reject_unknown_directory_query_parameters(
            request, frozenset({"q", "profile_handle", "limit", "cursor"})
        )
        reject_duplicate_cursor_query_parameter(request)
        normalized_query = query.strip()
        normalized_profile_handle = profile_handle.strip() if profile_handle is not None else None
        if normalized_profile_handle == "":
            raise HTTPException(status_code=422, detail="profile_handle must not be empty")
        return await list_public_agent_identities(
            session,
            query=normalized_query,
            profile_handle=normalized_profile_handle,
            limit=limit,
            cursor=cursor,
        )

    @app.get(
        "/v1/profiles/{handle}/agent-identities",
        response_model=AgentIdentityDirectoryResponse,
        tags=["agent-identities"],
    )
    async def list_profile_agent_identities(
        handle: Annotated[str, Path(min_length=1, max_length=100)],
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        session: AsyncSession = Depends(get_session),
    ) -> AgentIdentityDirectoryResponse:
        reject_unknown_directory_query_parameters(request, frozenset({"limit", "cursor"}))
        reject_duplicate_cursor_query_parameter(request)
        normalized_handle = handle.strip()
        if not normalized_handle:
            raise HTTPException(status_code=422, detail="profile handle must not be empty")
        await public_profile_by_handle(session, normalized_handle)
        return await list_public_agent_identities(
            session,
            query="",
            profile_handle=normalized_handle,
            limit=limit,
            cursor=cursor,
        )

    @app.post(
        "/v1/agent-identities",
        response_model=AgentIdentityResponse,
        status_code=201,
        tags=["agent-identities"],
        openapi_extra=_agent_identity_openapi_extra(),
    )
    async def create_agent_identity(
        body: AgentIdentityCreateRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> AgentIdentityResponse | Response:
        assert_not_impersonated_clerk(principal)
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only an authenticated Clerk user can create an agent identity",
            )
        key = idempotency_key(request, required=True)
        assert key is not None
        operation = "POST:/v1/agent-identities"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        profile = await session.scalar(
            select(Document)
            .where(
                Document.kind == "profile",
                Document.public_identifier == body.profile_handle,
                Document.owner_id == principal.subject,
                Document.visibility == "public",
            )
            .with_for_update()
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="public profile was not found")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        active_identity_count = await session.scalar(
            select(func.count(AgentIdentity.id)).where(
                AgentIdentity.owner_id == principal.subject,
                AgentIdentity.profile_document_id == profile.id,
                AgentIdentity.status == "active",
            )
        )
        if active_identity_count is not None and active_identity_count >= 10:
            raise HTTPException(
                status_code=429,
                detail="a profile can have at most ten active agent identities",
            )
        if await identifier_is_reserved(
            session, settings, namespace="agent_identity", identifier=body.handle
        ):
            raise HTTPException(status_code=409, detail="agent identity already exists")
        now = datetime.now(UTC)
        identity = AgentIdentity(
            id=new_id(),
            owner_id=principal.subject,
            handle=body.handle,
            display_name=body.display_name,
            description=body.description,
            profile_document_id=profile.id,
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(identity)
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type="agent_identity.created",
                resource_type="agent_identity",
                resource_id=identity.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                payload=json.dumps({"handle": identity.handle}, sort_keys=True),
                occurred_at=now,
            )
        )
        try:
            await session.flush()
            if await identifier_is_reserved(
                session, settings, namespace="agent_identity", identifier=body.handle
            ):
                raise HTTPException(status_code=409, detail="agent identity already exists")
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="agent identity already exists") from exc
        response = agent_identity_response(identity, profile)
        response_body = idempotency_replay_json(response)
        resource_id = _agent_identity_resource_id(identity, profile, "create", response_body)
        try:
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=201,
                body=response_body,
                headers={},
                resource_type="agent_identity",
                resource_id=resource_id,
            )
        except HTTPException:
            await session.rollback()
            raise
        return Response(content=response_body, status_code=201, media_type="application/json")

    @app.get(
        "/v1/agent-identities",
        response_model=list[AgentIdentityOwnerResponse],
        tags=["agent-identities"],
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def list_agent_identities(
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> list[AgentIdentityOwnerResponse]:
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only an authenticated Clerk user can list agent identities",
            )
        rows = (
            await session.execute(
                select(AgentIdentity, Document)
                .join(Document, Document.id == AgentIdentity.profile_document_id)
                .where(AgentIdentity.owner_id == principal.subject)
                .order_by(AgentIdentity.created_at.desc(), AgentIdentity.id.desc())
                .limit(limit)
            )
        ).all()
        return [
            AgentIdentityOwnerResponse(
                handle=identity.handle,
                display_name=identity.display_name,
                description=identity.description,
                profile_handle=profile.public_identifier,
                status=cast(Any, identity.status),
                created_at=identity.created_at,
                updated_at=identity.updated_at,
            )
            for identity, profile in rows
        ]

    @app.get(
        "/v1/agent-identities/{agent_handle}",
        response_model=AgentIdentityResponse,
        tags=["agent-identities"],
    )
    async def get_agent_identity(
        agent_handle: Annotated[
            str,
            Path(
                min_length=1,
                max_length=100,
                pattern=r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$",
            ),
        ],
        session: AsyncSession = Depends(get_session),
    ) -> AgentIdentityResponse:
        live = await live_agent_identity(session, agent_handle)
        if live is None:
            raise HTTPException(status_code=404, detail="agent identity was not found")
        identity, profile = live
        return agent_identity_response(identity, profile)

    @app.delete(
        "/v1/agent-identities/{agent_handle}",
        status_code=204,
        tags=["agent-identities"],
        openapi_extra=_agent_identity_openapi_extra(),
    )
    async def withdraw_agent_identity(
        agent_handle: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        assert_not_impersonated_clerk(principal)
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only an authenticated Clerk user can withdraw an agent identity",
            )
        key = idempotency_key(request, required=True)
        assert key is not None
        operation = f"DELETE:/v1/agent-identities/{agent_handle}"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        identity = await session.scalar(
            select(AgentIdentity)
            .where(
                AgentIdentity.handle == agent_handle,
                AgentIdentity.owner_id == principal.subject,
            )
            .with_for_update()
        )
        if identity is None:
            raise HTTPException(status_code=404, detail="agent identity was not found")
        profile = await session.scalar(
            select(Document)
            .where(
                Document.id == identity.profile_document_id,
                Document.owner_id == principal.subject,
                Document.kind == "profile",
            )
            .with_for_update()
        )
        if profile is None:
            raise HTTPException(status_code=503, detail="agent identity state is unavailable")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        if identity.status != "active":
            raise HTTPException(status_code=404, detail="agent identity was not found")
        now = datetime.now(UTC)
        identity.status = "withdrawn"
        identity.withdrawn_at = now
        identity.updated_at = now
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type="agent_identity.withdrawn",
                resource_type="agent_identity",
                resource_id=identity.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                payload=json.dumps({"handle": identity.handle}, sort_keys=True),
                occurred_at=now,
            )
        )
        resource_id = _agent_identity_resource_id(identity, profile, "withdraw", "")
        try:
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=204,
                body="",
                headers={},
                resource_type="agent_identity",
                resource_id=resource_id,
            )
        except HTTPException:
            await session.rollback()
            raise
        return Response(status_code=204)

    def effective_mandate_status(
        mandate: AgentMandate,
    ) -> Literal["active", "revoked", "expired", "suspended"]:
        if mandate.status == "active" and retention_expired(mandate.expires_at):
            return "expired"
        return cast(Any, mandate.status)

    async def mandate_summary(
        session: AsyncSession, mandate: AgentMandate
    ) -> AgentMandateInventoryResponse:
        grant = await session.scalar(select(AgentGrant).where(AgentGrant.mandate_id == mandate.id))
        if grant is None:
            raise HTTPException(status_code=503, detail="agent mandate grant record is unavailable")
        return AgentMandateInventoryResponse(
            id=mandate.id,
            scope="internal_contact_request",
            status=effective_mandate_status(mandate),
            expires_at=mandate.expires_at,
            grant_prefix=grant.prefix,
        )

    async def mandate_issue_replay(
        session: AsyncSession,
        *,
        principal: Principal,
        key: str,
        operation: str,
        fingerprint: str,
    ) -> AgentMandateRecoveryResponse | None:
        record = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.owner_id == principal.subject,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        if record is None:
            return None
        if record.operation != operation or record.request_hash != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used for a different request",
            )
        mandate = await session.scalar(
            select(AgentMandate).where(
                AgentMandate.id == record.resource_id,
                AgentMandate.owner_id == principal.subject,
            )
        )
        if mandate is None:
            raise HTTPException(
                status_code=503,
                detail="mandate issuance committed but its recovery record cannot be reconstructed",
            )
        summary = await mandate_summary(session, mandate)
        return AgentMandateRecoveryResponse(
            **summary.model_dump(),
            recovery_required=True,
        )

    @app.post(
        "/v1/agent-identities/{agent_handle}/mandates",
        response_model=AgentMandateIssuedResponse | AgentMandateRecoveryResponse,
        status_code=201,
        tags=["agent-mandates"],
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            428: _error_response("Idempotency-Key is required."),
        },
        openapi_extra=_agent_identity_openapi_extra(),
    )
    async def create_agent_mandate(
        agent_handle: str,
        body: AgentMandateCreateRequest,
        request: Request,
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can issue an agent mandate"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> AgentMandateIssuedResponse | AgentMandateRecoveryResponse:
        key = idempotency_key(request, required=True)
        assert key is not None
        operation = f"POST:/v1/agent-identities/{agent_handle}/mandates"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await mandate_issue_replay(
            session,
            principal=principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        expires_at = body.expires_at
        if expires_at.tzinfo is None:
            raise HTTPException(status_code=422, detail="expires_at must include a timezone")
        expires_at = expires_at.astimezone(UTC)
        now = datetime.now(UTC)
        if expires_at <= now or expires_at > now + timedelta(days=30):
            raise HTTPException(
                status_code=422,
                detail="mandate expiry must be in the future and no more than 30 days away",
            )
        identity = await session.scalar(
            select(AgentIdentity)
            .where(
                AgentIdentity.handle == agent_handle,
                AgentIdentity.owner_id == principal.subject,
                AgentIdentity.status == "active",
            )
            .with_for_update()
        )
        if identity is None:
            raise HTTPException(status_code=404, detail="agent identity was not found")
        profile = await session.scalar(
            select(Document)
            .where(
                Document.id == identity.profile_document_id,
                Document.owner_id == identity.owner_id,
                Document.kind == "profile",
                Document.visibility == "public",
            )
            .with_for_update()
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="agent identity was not found")
        replay = await mandate_issue_replay(
            session,
            principal=principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        existing = await session.scalar(
            select(AgentMandate)
            .where(
                AgentMandate.identity_id == identity.id,
                AgentMandate.scope == "internal_contact_request",
                AgentMandate.status == "active",
            )
            .with_for_update()
        )
        if existing is not None:
            if retention_expired(existing.expires_at, now):
                existing.status = "expired"
            else:
                raise HTTPException(
                    status_code=409, detail="an active agent mandate already exists"
                )
        mandate = AgentMandate(
            id=new_id(),
            identity_id=identity.id,
            owner_id=principal.subject,
            scope="internal_contact_request",
            status="active",
            expires_at=expires_at,
            created_at=now,
        )
        session.add(mandate)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            replay = await mandate_issue_replay(
                session,
                principal=principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="agent mandate conflicted") from exc
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type="agent_mandate.created",
                resource_type="agent_mandate",
                resource_id=mandate.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                payload=json.dumps({"scope": mandate.scope}, sort_keys=True),
                occurred_at=now,
            )
        )
        try:
            grant, raw_key = await request.app.state.agent_grants.create(
                session,
                owner_id=principal.subject,
                actor_id=principal.audit_actor_id,
                name=f"Mandate: {identity.handle}",
                scopes=["contacts:write"],
                mode="direct",
                resource_type="owner",
                resource_id=None,
                expires_at=expires_at,
                mandate_id=mandate.id,
                commit=False,
            )
        except AuthenticationUnavailable as exc:
            await session.rollback()
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        session.add(
            IdempotencyRecord(
                owner_id=principal.subject,
                idempotency_key=key,
                operation=operation,
                request_hash=fingerprint,
                response_status=201,
                response_body=json.dumps({"id": mandate.id, "status": "active"}, sort_keys=True),
                response_headers="{}",
                resource_type="agent_mandate",
                resource_id=mandate.id,
                created_at=now,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            replay = await mandate_issue_replay(
                session,
                principal=principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="agent mandate conflicted") from exc
        issued = grant_response(grant, key=raw_key)
        assert isinstance(issued, AgentGrantCreatedResponse)
        return AgentMandateIssuedResponse(
            id=mandate.id,
            scope="internal_contact_request",
            expires_at=expires_at,
            grant=issued,
        )

    @app.get(
        "/v1/agent-identities/{agent_handle}/mandates",
        response_model=list[AgentMandateInventoryResponse],
        tags=["agent-mandates"],
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def list_agent_mandates(
        agent_handle: str,
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can list agent mandates"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> list[AgentMandateInventoryResponse]:
        identity = await session.scalar(
            select(AgentIdentity).where(
                AgentIdentity.handle == agent_handle,
                AgentIdentity.owner_id == principal.subject,
            )
        )
        if identity is None:
            raise HTTPException(status_code=404, detail="agent identity was not found")
        mandates = (
            await session.scalars(
                select(AgentMandate)
                .where(
                    AgentMandate.identity_id == identity.id,
                    AgentMandate.owner_id == principal.subject,
                )
                .order_by(AgentMandate.created_at.desc(), AgentMandate.id.desc())
            )
        ).all()
        return [await mandate_summary(session, mandate) for mandate in mandates]

    @app.delete(
        "/v1/agent-identities/{agent_handle}/mandates/{mandate_id}",
        status_code=204,
        tags=["agent-mandates"],
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def revoke_agent_mandate(
        agent_handle: str,
        mandate_id: str,
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can revoke an agent mandate"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        identity = await session.scalar(
            select(AgentIdentity).where(
                AgentIdentity.handle == agent_handle,
                AgentIdentity.owner_id == principal.subject,
            )
        )
        mandate = await session.scalar(
            select(AgentMandate)
            .where(
                AgentMandate.id == mandate_id,
                AgentMandate.owner_id == principal.subject,
                AgentMandate.identity_id == (identity.id if identity is not None else ""),
            )
            .with_for_update()
        )
        if mandate is None:
            raise HTTPException(status_code=404, detail="agent mandate was not found")
        if mandate.status != "revoked":
            now = datetime.now(UTC)
            mandate.status = "revoked"
            mandate.revoked_at = now
            grant = await session.scalar(
                select(AgentGrant).where(AgentGrant.mandate_id == mandate.id).with_for_update()
            )
            if grant is not None:
                grant.revoked = True
            session.add(
                ChangeEvent(
                    owner_id=principal.subject,
                    event_type="agent_mandate.revoked",
                    resource_type="agent_mandate",
                    resource_id=mandate.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    payload=json.dumps({"scope": mandate.scope}, sort_keys=True),
                    occurred_at=now,
                )
            )
            await session.commit()
        return Response(status_code=204)

    def contact_policy_response(policy: ContactPolicy | None) -> ContactPolicyResponse:
        if policy is None:
            return ContactPolicyResponse(
                allow_agent_requests=False,
                daily_request_limit=5,
                version=0,
                updated_at=None,
                etag='"policy-0"',
            )
        return ContactPolicyResponse(
            allow_agent_requests=policy.allow_agent_requests,
            daily_request_limit=policy.daily_request_limit,
            version=policy.version,
            updated_at=policy.updated_at,
            etag=f'"policy-{policy.version}"',
        )

    async def lock_contact_policy_owner(session: AsyncSession, owner_id: str) -> None:
        if session.get_bind().dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"contact-policy:{owner_id}"},
            )
        elif session.get_bind().dialect.name == "sqlite":
            # Preserve credential usage before restarting the initial deferred
            # transaction, then recheck lifecycle denial while the immediate
            # write lock prevents a concurrent deletion from passing it.
            await session.commit()
            await session.execute(text("BEGIN IMMEDIATE"))
            await assert_account_access(session, settings, owner_id, mutation=True)

    @app.get(
        "/v1/contact-policy",
        response_model=ContactPolicyResponse,
        tags=["contacts"],
    )
    async def get_contact_policy(
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ContactPolicyResponse:
        if principal.method in {"agent_api_key", "agent_grant"}:
            assert_scope(principal, "contacts:read")
            if principal.method == "agent_grant":
                assert_not_mandate_credential(principal)
                assert_direct(principal)
                if principal.resource_type != "owner":
                    raise HTTPException(status_code=403, detail="owner-bound grant is required")
        policy = await session.get(ContactPolicy, principal.subject)
        result = contact_policy_response(policy)
        response.headers["ETag"] = result.etag
        return result

    @app.put(
        "/v1/contact-policy",
        response_model=ContactPolicyResponse,
        tags=["contacts"],
        responses={
            200: {
                "description": "Contact policy with the current ETag.",
                "headers": {
                    "ETag": {
                        "description": "The validator for this policy version.",
                        "schema": {"type": "string"},
                    }
                },
            },
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response("The contact-policy authority is not available."),
            409: _error_response("The idempotency key conflicts with an existing operation."),
            412: _error_response("If-Match does not match contact policy."),
            428: _error_response("Idempotency-Key and an exact strong If-Match are required."),
            503: _error_response("The durable contact-policy receipt is unavailable."),
        },
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "description": "A 1-128 character visible-ASCII key for this logical request.",
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                },
                {
                    "name": "If-Match",
                    "in": "header",
                    "required": True,
                    "description": "Require the exact current strong contact-policy ETag.",
                    "schema": {
                        "type": "string",
                        "pattern": r'^"policy-(0|[1-9][0-9]*)"$',
                    },
                },
            ]
        },
    )
    async def update_contact_policy(
        body: ContactPolicyUpdateRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ContactPolicyResponse | Response:
        assert_not_impersonated_clerk(principal)
        if principal.method in {"agent_api_key", "agent_grant"}:
            assert_not_mandate_credential(principal)
            assert_scope(principal, "contacts:write")
        assert_direct(principal)
        if principal.method == "agent_grant" and principal.resource_type != "owner":
            raise HTTPException(status_code=403, detail="owner-bound grant is required")
        key = idempotency_key(request, required=True)
        operation = "PUT:/v1/contact-policy"
        supplied = request.headers.get("If-Match")
        if supplied is None:
            raise HTTPException(
                status_code=428, detail="If-Match is required to update contact policy"
            )
        conditional_fingerprint = json.dumps(
            {"if_match": supplied}, sort_keys=True, separators=(",", ":")
        )
        fingerprint = _request_fingerprint(
            operation, body.model_dump_json(), conditional_fingerprint
        )
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        await lock_contact_policy_owner(session, principal.subject)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        policy = await session.scalar(
            select(ContactPolicy)
            .where(ContactPolicy.owner_id == principal.subject)
            .with_for_update()
        )
        current = contact_policy_response(policy)
        if not compare_digest(supplied, current.etag):
            raise HTTPException(status_code=412, detail="If-Match does not match contact policy")
        now = datetime.now(UTC)
        if policy is None:
            policy = ContactPolicy(
                owner_id=principal.subject,
                allow_agent_requests=body.allow_agent_requests,
                daily_request_limit=body.daily_request_limit,
                version=1,
                updated_at=now,
            )
            session.add(policy)
        else:
            policy.allow_agent_requests = body.allow_agent_requests
            policy.daily_request_limit = body.daily_request_limit
            policy.version += 1
            policy.updated_at = now
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type="contact_policy.updated",
                resource_type="contact_policy",
                resource_id=public_owner_id(principal.subject),
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=principal.grant_id,
                payload=json.dumps(
                    {
                        "allow_agent_requests": body.allow_agent_requests,
                        "daily_request_limit": body.daily_request_limit,
                        "version": policy.version,
                    },
                    sort_keys=True,
                ),
                occurred_at=now,
            )
        )
        result = contact_policy_response(policy)
        response_body = idempotency_replay_json(result)
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body=response_body,
            headers={"ETag": result.etag},
            resource_type="contact_policy",
            resource_id=f"{public_owner_id(principal.subject)}:{sha256(response_body.encode()).hexdigest()}",
        )
        return Response(
            content=response_body,
            status_code=200,
            media_type="application/json",
            headers={"ETag": result.etag},
        )

    def contact_response(
        row: ContactRequest, viewer: Principal | None = None
    ) -> ContactRequestResponse:
        direct_agent_grant = row.origin == "profile_contact" and (
            row.sender_actor_method == "agent_grant" or row.sender_grant_id is not None
        )
        sender_is_viewer = (
            viewer is not None
            and viewer.subject == row.sender_owner_id
            and (
                not direct_agent_grant
                or (
                    viewer.grant_id is not None
                    and row.sender_grant_id is not None
                    and viewer.grant_id == row.sender_grant_id
                )
            )
        )
        if row.origin == "agent_outreach":
            projected_sender_actor_id = row.sender_identity_handle
        elif direct_agent_grant and not sender_is_viewer:
            projected_sender_actor_id = "agent_grant"
        elif row.sender_grant_id and sender_is_viewer:
            projected_sender_actor_id = row.sender_actor_id
        else:
            projected_sender_actor_id = public_owner_id(row.sender_actor_id)
        return ContactRequestResponse(
            id=row.id,
            sender_owner_id=public_owner_id(row.sender_owner_id),
            recipient_owner_id=public_owner_id(row.recipient_owner_id),
            target_document_id=row.target_document_id,
            purpose=row.purpose,
            message=row.message,
            status=cast(Any, row.status),
            sender_actor_id=projected_sender_actor_id or "agent",
            sender_actor_method=row.sender_actor_method,
            sender_grant_id=(
                row.sender_grant_id if row.origin != "agent_outreach" and sender_is_viewer else None
            ),
            origin=cast(Any, row.origin),
            sender_identity_handle=row.sender_identity_handle,
            sender_identity_display_name=row.sender_identity_display_name,
            target_identity_handle=row.target_identity_handle,
            target_identity_display_name=row.target_identity_display_name,
            sender_mandate_scope=(
                "internal_contact_request" if row.origin == "agent_outreach" else None
            ),
            created_at=row.created_at,
            decided_at=row.decided_at,
        )

    def agent_outreach_receipt(row: ContactRequest) -> AgentOutreachReceipt:
        if (
            row.origin != "agent_outreach"
            or row.sender_identity_handle is None
            or row.target_identity_handle is None
        ):
            raise HTTPException(status_code=503, detail="agent outreach receipt is unavailable")
        return AgentOutreachReceipt(
            id=row.id,
            origin="agent_outreach",
            status="pending",
            sender_identity_handle=row.sender_identity_handle,
            target_identity_handle=row.target_identity_handle,
            created_at=(
                row.created_at
                if row.created_at.tzinfo is not None
                else row.created_at.replace(tzinfo=UTC)
            ),
        )

    def agent_outreach_status(row: ContactRequest) -> AgentOutreachStatusResponse:
        if (
            row.origin != "agent_outreach"
            or row.sender_identity_handle is None
            or row.target_identity_handle is None
        ):
            raise HTTPException(status_code=503, detail="agent outreach status is unavailable")
        if row.status == "pending":
            external_status: Literal["pending", "accepted", "declined"] = "pending"
        elif row.status == "accepted":
            external_status = "accepted"
        elif row.status in {"rejected", "blocked", "reported"}:
            external_status = "declined"
        else:
            raise HTTPException(status_code=503, detail="agent outreach status is unavailable")
        return AgentOutreachStatusResponse(
            id=row.id,
            origin="agent_outreach",
            status=external_status,
            sender_identity_handle=row.sender_identity_handle,
            target_identity_handle=row.target_identity_handle,
            created_at=(
                row.created_at
                if row.created_at.tzinfo is not None
                else row.created_at.replace(tzinfo=UTC)
            ),
            decided_at=(
                None
                if row.decided_at is None
                else (
                    row.decided_at
                    if row.decided_at.tzinfo is not None
                    else row.decided_at.replace(tzinfo=UTC)
                )
            ),
        )

    def agent_outreach_direct_peer_hmac(request: Request) -> str:
        peer = request.client.host if request.client is not None else None
        pepper = settings.api_key_pepper
        if peer is None or pepper is None:
            raise HTTPException(
                status_code=503,
                detail="agent outreach direct-peer rate control is unavailable",
            )
        try:
            normalized_peer = ip_address(peer).compressed
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail="agent outreach direct-peer rate control is unavailable",
            ) from exc
        return hmac_new(
            pepper.encode("utf-8"),
            f"connect.md:agent-outreach-direct-peer:v1:{normalized_peer}".encode(),
            sha256,
        ).hexdigest()

    async def place_contact_request(
        body: ContactRequestCreate,
        request: Request,
        *,
        principal: Principal,
        session: AsyncSession,
        operation: str,
        request_payload: str,
        target: Document | None = None,
        origin: Literal["profile_contact", "agent_outreach"] = "profile_contact",
        sender_mandate_id: str | None = None,
        sender_identity: AgentIdentity | None = None,
        target_identity: AgentIdentity | None = None,
        outreach_context: dict[str, str] | None = None,
        idempotency_key_value: str | None = None,
    ) -> ContactRequestResponse | AgentOutreachReceipt | Response:
        if origin == "agent_outreach" and outreach_context is None:
            raise HTTPException(
                status_code=503,
                detail="agent outreach authority context is unavailable",
            )
        if idempotency_key_value is not None and not _IDEMPOTENCY_KEY_RE.fullmatch(
            idempotency_key_value
        ):
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must contain 1-128 visible ASCII characters",
            )
        key = (
            idempotency_key_value
            if idempotency_key_value is not None
            else idempotency_key(request, required=True)
        )
        fingerprint = _request_fingerprint(operation, request_payload)
        replay = await idempotency_replay(
            session,
            request,
            principal,
            key,
            operation,
            fingerprint,
            outreach_context=outreach_context,
        )
        if replay is not None:
            return replay
        if target is None:
            target = await session.scalar(
                select(Document).where(
                    Document.kind == "profile",
                    Document.public_identifier == body.target_profile_handle,
                    Document.visibility == "public",
                )
            )
        if target is None:
            raise HTTPException(status_code=404, detail="contact target was not found")
        if target.owner_id == principal.subject:
            raise HTTPException(status_code=409, detail="cannot send a contact request to yourself")
        policy = await session.scalar(
            select(ContactPolicy).where(ContactPolicy.owner_id == target.owner_id).with_for_update()
        )
        if policy is None or not policy.allow_agent_requests:
            raise HTTPException(status_code=403, detail="recipient is not accepting agent requests")
        blocked = await session.scalar(
            select(ContactBlock).where(
                ContactBlock.blocker_owner_id == target.owner_id,
                ContactBlock.blocked_owner_id == principal.subject,
            )
        )
        if blocked is not None:
            raise HTTPException(status_code=404, detail="contact target was not found")
        now = datetime.now(UTC)
        rate_limit = _CONTACT_SENDER_DAILY_LIMIT
        quota_values = {
            "sender_owner_id": principal.subject,
            "bucket_date": now.date(),
            "request_count": 1,
            "updated_at": now,
        }
        dialect_name = session.get_bind().dialect.name
        quota_insert: Any
        if dialect_name == "postgresql":
            quota_insert = postgresql_insert(ContactRateBucket).values(**quota_values)
        elif dialect_name == "sqlite":
            quota_insert = sqlite_insert(ContactRateBucket).values(**quota_values)
        else:  # pragma: no cover - the locked stack and tests use PostgreSQL/SQLite
            raise HTTPException(status_code=503, detail="contact quota backend is unsupported")
        quota_statement = quota_insert.on_conflict_do_update(
            index_elements=["sender_owner_id", "bucket_date"],
            set_={
                "request_count": ContactRateBucket.request_count + 1,
                "updated_at": now,
            },
            where=ContactRateBucket.request_count < rate_limit,
        ).returning(ContactRateBucket.request_count)
        consumed = await session.scalar(quota_statement)
        if consumed is None:
            raise HTTPException(
                status_code=429,
                detail="contact request daily limit reached",
                headers={"Retry-After": "86400"},
            )
        pending = await session.scalar(
            select(ContactRequest).where(
                ContactRequest.sender_owner_id == principal.subject,
                ContactRequest.recipient_owner_id == target.owner_id,
                ContactRequest.status == "pending",
            )
        )
        if pending is not None:
            raise HTTPException(
                status_code=409,
                detail="a contact request to this recipient is already pending",
            )
        recipient_quota_values = {
            "recipient_owner_id": target.owner_id,
            "bucket_date": now.date(),
            "request_count": 1,
            "updated_at": now,
        }
        recipient_quota_insert: Any
        if dialect_name == "postgresql":
            recipient_quota_insert = postgresql_insert(AgentOutreachRecipientRateBucket).values(
                **recipient_quota_values
            )
        elif dialect_name == "sqlite":
            recipient_quota_insert = sqlite_insert(AgentOutreachRecipientRateBucket).values(
                **recipient_quota_values
            )
        else:  # pragma: no cover - checked by the sender quota above
            raise HTTPException(status_code=503, detail="contact quota backend is unsupported")
        recipient_consumed = await session.scalar(
            recipient_quota_insert.on_conflict_do_update(
                index_elements=["recipient_owner_id", "bucket_date"],
                set_={
                    "request_count": AgentOutreachRecipientRateBucket.request_count + 1,
                    "updated_at": now,
                },
                where=(AgentOutreachRecipientRateBucket.request_count < policy.daily_request_limit),
            ).returning(AgentOutreachRecipientRateBucket.request_count)
        )
        if recipient_consumed is None:
            raise HTTPException(
                status_code=429,
                detail=(
                    "agent outreach rate limit reached"
                    if origin == "agent_outreach"
                    else "contact recipient inbox daily limit reached"
                ),
                headers={"Retry-After": "86400"},
            )
        if origin == "agent_outreach":
            direct_peer_hmac = agent_outreach_direct_peer_hmac(request)
            direct_peer_quota_values = {
                "direct_peer_hmac": direct_peer_hmac,
                "bucket_date": now.date(),
                "request_count": 1,
                "updated_at": now,
            }
            direct_peer_quota_insert: Any
            if dialect_name == "postgresql":
                direct_peer_quota_insert = postgresql_insert(
                    AgentOutreachDirectPeerRateBucket
                ).values(**direct_peer_quota_values)
            elif dialect_name == "sqlite":
                direct_peer_quota_insert = sqlite_insert(AgentOutreachDirectPeerRateBucket).values(
                    **direct_peer_quota_values
                )
            else:  # pragma: no cover - checked by the sender quota above
                raise HTTPException(status_code=503, detail="contact quota backend is unsupported")
            direct_peer_consumed = await session.scalar(
                direct_peer_quota_insert.on_conflict_do_update(
                    index_elements=["direct_peer_hmac", "bucket_date"],
                    set_={
                        "request_count": AgentOutreachDirectPeerRateBucket.request_count + 1,
                        "updated_at": now,
                    },
                    where=(
                        AgentOutreachDirectPeerRateBucket.request_count
                        < settings.agent_outreach_direct_peer_daily_limit
                    ),
                ).returning(AgentOutreachDirectPeerRateBucket.request_count)
            )
            if direct_peer_consumed is None:
                raise HTTPException(
                    status_code=429,
                    detail="agent outreach rate limit reached",
                    headers={"Retry-After": "86400"},
                )
        row = ContactRequest(
            id=new_id(),
            sender_owner_id=principal.subject,
            recipient_owner_id=target.owner_id,
            sender_actor_id=principal.audit_actor_id,
            sender_actor_method=principal.method,
            sender_grant_id=principal.grant_id,
            sender_mandate_id=sender_mandate_id,
            origin=origin,
            sender_identity_handle=sender_identity.handle if sender_identity is not None else None,
            sender_identity_display_name=(
                sender_identity.display_name if sender_identity is not None else None
            ),
            target_identity_handle=target_identity.handle if target_identity is not None else None,
            target_identity_display_name=(
                target_identity.display_name if target_identity is not None else None
            ),
            target_document_id=target.id,
            purpose=body.purpose,
            message=body.message,
            status="pending",
            created_at=now,
            retention_expires_at=now + timedelta(days=365),
        )
        result: ContactRequestResponse | AgentOutreachReceipt = (
            agent_outreach_receipt(row)
            if origin == "agent_outreach"
            else contact_response(row, principal)
        )
        session.add(row)
        for owner_id, event_type in (
            (target.owner_id, "contact_request.received"),
            (principal.subject, "contact_request.sent"),
        ):
            recipient_event = origin == "agent_outreach" and owner_id == target.owner_id
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type=event_type,
                    resource_type="contact_request",
                    resource_id=row.id,
                    actor_id=(
                        sender_identity.handle
                        if recipient_event and sender_identity is not None
                        else principal.audit_actor_id
                    ),
                    actor_method=principal.method,
                    grant_id=None if recipient_event else principal.grant_id,
                    payload=json.dumps({"origin": origin, "status": "pending"}, sort_keys=True),
                    occurred_at=now,
                )
            )
        session.add(
            IdempotencyRecord(
                owner_id=principal.subject,
                idempotency_key=cast(str, key),
                operation=operation,
                request_hash=fingerprint,
                response_status=201,
                response_body="",
                response_headers="{}",
                resource_type="contact_request",
                resource_id=(
                    _agent_outreach_receipt_resource_id(
                        row.id,
                        mandate_id=outreach_context["mandate_id"],
                        source_identity_handle=outreach_context["source_identity_handle"],
                        grant_id=outreach_context["grant_id"],
                    )
                    if origin == "agent_outreach" and outreach_context is not None
                    else row.id
                ),
                created_at=now,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session,
                request,
                principal,
                key,
                operation,
                fingerprint,
                outreach_context=outreach_context,
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="contact request conflicted") from exc
        return result

    async def mandate_bound_identity(
        session: AsyncSession, principal: Principal
    ) -> tuple[AgentMandate, AgentIdentity]:
        if principal.method != "agent_grant" or principal.grant_id is None:
            raise HTTPException(status_code=403, detail="a mandate-bound agent grant is required")
        grant_reference = await session.scalar(
            select(AgentGrant).where(
                AgentGrant.id == principal.grant_id,
                AgentGrant.owner_id == principal.subject,
                AgentGrant.mandate_id.is_not(None),
            )
        )
        if grant_reference is None:
            raise HTTPException(status_code=403, detail="agent mandate is not active")
        mandate = await session.scalar(
            select(AgentMandate)
            .where(
                AgentMandate.id == grant_reference.mandate_id,
                AgentMandate.owner_id == principal.subject,
            )
            .with_for_update()
        )
        grant = await session.scalar(
            select(AgentGrant)
            .where(
                AgentGrant.id == principal.grant_id,
                AgentGrant.owner_id == principal.subject,
                AgentGrant.mandate_id == (mandate.id if mandate is not None else ""),
                AgentGrant.revoked.is_(False),
            )
            .with_for_update()
        )
        if (
            grant is None
            or mandate is None
            or grant.mode != "direct"
            or grant.resource_type != "owner"
            or grant.resource_id is not None
            or retention_expired(grant.expires_at)
        ):
            raise HTTPException(status_code=403, detail="agent mandate is not active")
        try:
            grant_scopes = json.loads(grant.scopes)
        except json.JSONDecodeError:
            grant_scopes = None
        if grant_scopes != ["contacts:write"]:
            raise HTTPException(status_code=403, detail="agent mandate is not active")
        if (
            mandate.scope != "internal_contact_request"
            or mandate.status != "active"
            or retention_expired(mandate.expires_at)
            or retention_expired(grant.expires_at)
            or grant.expires_at > mandate.expires_at
        ):
            raise HTTPException(status_code=403, detail="agent mandate is not active")
        identity = await session.get(AgentIdentity, mandate.identity_id)
        if (
            identity is None
            or identity.owner_id != principal.subject
            or identity.status != "active"
        ):
            raise HTTPException(status_code=403, detail="agent mandate is not active")
        return mandate, identity

    async def lock_live_outreach_identities(
        session: AsyncSession,
        *,
        source_identity: AgentIdentity,
        target_handle: str,
        source_owner_id: str,
    ) -> tuple[AgentIdentity, Document, AgentIdentity, Document]:
        target_reference = await session.scalar(
            select(AgentIdentity).where(AgentIdentity.handle == target_handle)
        )
        if target_reference is None:
            raise HTTPException(status_code=404, detail="agent identity was not found")
        identity_ids = sorted({source_identity.id, target_reference.id})
        identities = (
            await session.scalars(
                select(AgentIdentity)
                .where(AgentIdentity.id.in_(identity_ids))
                .order_by(AgentIdentity.id.asc())
                .with_for_update()
            )
        ).all()
        locked = {identity.id: identity for identity in identities}
        source = locked.get(source_identity.id)
        target = locked.get(target_reference.id)
        if source is None or source.owner_id != source_owner_id or source.status != "active":
            raise HTTPException(status_code=403, detail="agent mandate is not active")
        if target is None or target.handle != target_handle or target.status != "active":
            raise HTTPException(status_code=404, detail="agent identity was not found")
        profile_ids = sorted({source.profile_document_id, target.profile_document_id})
        profiles = (
            await session.scalars(
                select(Document)
                .where(Document.id.in_(profile_ids))
                .order_by(Document.id.asc())
                .with_for_update()
            )
        ).all()
        locked_profiles = {profile.id: profile for profile in profiles}
        source_profile = locked_profiles.get(source.profile_document_id)
        target_profile = locked_profiles.get(target.profile_document_id)
        if (
            source_profile is None
            or source_profile.owner_id != source.owner_id
            or source_profile.kind != "profile"
            or source_profile.visibility != "public"
        ):
            raise HTTPException(status_code=403, detail="agent mandate is not active")
        if (
            target_profile is None
            or target_profile.owner_id != target.owner_id
            or target_profile.kind != "profile"
            or target_profile.visibility != "public"
        ):
            raise HTTPException(status_code=404, detail="agent identity was not found")
        return source, source_profile, target, target_profile

    @app.post(
        "/v1/contact-requests",
        response_model=ContactRequestResponse,
        status_code=201,
        tags=["contacts"],
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "description": "A 1-128 character visible-ASCII key for this logical request.",
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": r"^[\x21-\x7E]{1,128}$",
                    },
                }
            ]
        },
    )
    async def create_contact_request(
        body: ContactRequestCreate,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ContactRequestResponse | Response:
        assert_not_impersonated_clerk(principal)
        if principal.method in {"agent_api_key", "agent_grant"}:
            assert_not_mandate_credential(principal)
            assert_scope(principal, "contacts:write")
        if principal.method == "agent_grant":
            assert_direct(principal)
            if principal.resource_type != "owner":
                raise HTTPException(
                    status_code=403,
                    detail="owner-bound direct grant is required for outreach",
                )
        result = await place_contact_request(
            body,
            request,
            principal=principal,
            session=session,
            operation="POST:/v1/contact-requests",
            request_payload=body.model_dump_json(),
        )
        assert not isinstance(result, AgentOutreachReceipt)
        return result

    @app.post(
        "/v1/agent-outreach",
        response_model=AgentOutreachReceipt,
        status_code=201,
        tags=["agent-outreach"],
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "description": "A 1-128 character visible-ASCII key for this logical request.",
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": r"^[\x21-\x7E]{1,128}$",
                    },
                }
            ]
        },
    )
    async def create_agent_outreach(
        body: AgentOutreachCreate,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> AgentOutreachReceipt | Response:
        idempotency_key_value = getattr(request.state, "mcp_idempotency_key", None)
        mandate, source_identity = await mandate_bound_identity(session, principal)
        sender_identity, _, target_identity, target_profile = await lock_live_outreach_identities(
            session,
            source_identity=source_identity,
            target_handle=body.target_agent_handle,
            source_owner_id=principal.subject,
        )
        contact_body = ContactRequestCreate(
            target_profile_handle=target_profile.public_identifier,
            purpose=body.purpose,
            message=body.message,
        )
        result = await place_contact_request(
            contact_body,
            request,
            principal=principal,
            session=session,
            operation="POST:/v1/agent-outreach",
            request_payload=body.model_dump_json(),
            target=target_profile,
            origin="agent_outreach",
            sender_mandate_id=mandate.id,
            sender_identity=sender_identity,
            target_identity=target_identity,
            outreach_context={
                "mandate_id": mandate.id,
                "source_identity_handle": sender_identity.handle,
                "grant_id": cast(str, principal.grant_id),
                "target_identity_handle": target_identity.handle,
                "target_document_id": target_profile.id,
            },
            idempotency_key_value=idempotency_key_value,
        )
        if isinstance(result, ContactRequestResponse):
            raise HTTPException(status_code=503, detail="agent outreach receipt is unavailable")
        return result

    @app.get(
        "/v1/agent-outreach/{request_id}",
        response_model=AgentOutreachStatusResponse,
        tags=["agent-outreach"],
    )
    async def get_agent_outreach_status(
        request_id: str,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> AgentOutreachStatusResponse:
        criteria = [
            ContactRequest.id == request_id,
            ContactRequest.origin == "agent_outreach",
            ContactRequest.sender_owner_id == principal.subject,
            ContactRequest.retention_expires_at > datetime.now(UTC),
        ]
        if principal.method == "clerk_jwt":
            pass
        elif principal.method == "agent_grant":
            try:
                mandate, source_identity = await mandate_bound_identity(session, principal)
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                raise HTTPException(
                    status_code=404, detail="agent outreach was not found"
                ) from None
            criteria.extend(
                [
                    ContactRequest.sender_mandate_id == mandate.id,
                    ContactRequest.sender_identity_handle == source_identity.handle,
                ]
            )
        else:
            raise HTTPException(status_code=404, detail="agent outreach was not found")
        row = await session.scalar(select(ContactRequest).where(*criteria))
        if row is None or retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="agent outreach was not found")
        return agent_outreach_status(row)

    @app.get(
        "/v1/contact-requests/inbox",
        response_model=ContactInboxResponse,
        tags=["contacts"],
    )
    async def contact_inbox(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        status_filter: Annotated[str | None, Query(alias="status")] = None,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ContactInboxResponse:
        if principal.method in {"agent_api_key", "agent_grant"}:
            assert_not_mandate_credential(principal)
            assert_scope(principal, "contacts:read")
        assert_direct(principal)
        if principal.method == "agent_grant" and principal.resource_type != "owner":
            raise HTTPException(status_code=403, detail="owner-bound grant is required")
        contact_cursor_bindings = cursor_principal_bindings(principal) + (status_filter or "",)
        statement = select(ContactRequest).where(
            ContactRequest.recipient_owner_id == principal.subject,
            ContactRequest.retention_expires_at > datetime.now(UTC),
            ContactRequest.status != "blocked",
        )
        if status_filter is not None:
            if status_filter not in {"pending", "accepted", "rejected", "blocked", "reported"}:
                raise HTTPException(status_code=400, detail="unknown contact request status")
            statement = statement.where(ContactRequest.status == status_filter)
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="contact_inbox",
                bindings=contact_cursor_bindings,
                detail="contact cursor is malformed",
            )
            try:
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                request_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="contact cursor is malformed") from exc
            statement = statement.where(
                or_(
                    ContactRequest.created_at < created_at,
                    and_(
                        ContactRequest.created_at == created_at,
                        ContactRequest.id < request_id,
                    ),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(
                    ContactRequest.created_at.desc(), ContactRequest.id.desc()
                ).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {"v": 1, "created_at": last.created_at.isoformat(), "id": last.id},
                scope="contact_inbox",
                bindings=contact_cursor_bindings,
            )
        return ContactInboxResponse(
            requests=[contact_response(row, principal) for row in page], next_cursor=next_cursor
        )

    @app.post(
        "/v1/contact-requests/{contact_request_id}/{action}",
        response_model=ContactRequestResponse,
        tags=["contacts"],
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response("The contact decision authority is not available."),
            404: _error_response("The contact request was not found."),
            409: _error_response("The contact request or idempotency key conflicts."),
            422: _error_response("A report reason is required."),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("The durable contact decision receipt is unavailable."),
        },
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "description": "A 1-128 character visible-ASCII key for this logical request.",
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                }
            ]
        },
    )
    async def decide_contact_request(
        contact_request_id: str,
        action: str,
        request: Request,
        body: ContactActionRequest | None = None,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ContactRequestResponse | Response:
        assert_not_impersonated_clerk(principal)
        if action not in {"accept", "reject", "block", "report"}:
            raise HTTPException(status_code=404, detail="contact action was not found")
        if principal.method in {"agent_api_key", "agent_grant"}:
            assert_not_mandate_credential(principal)
            assert_scope(principal, "contacts:write")
        assert_direct(principal)
        if principal.method == "agent_grant" and principal.resource_type != "owner":
            raise HTTPException(status_code=403, detail="owner-bound grant is required")
        normalized_body = body if body is not None else ContactActionRequest()
        reason = normalized_body.reason
        if action == "report" and not reason:
            raise HTTPException(status_code=422, detail="report requires a reason")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/contact-requests/{contact_request_id}/{action}"
        fingerprint = _request_fingerprint(operation, normalized_body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        row_conditions = [
            ContactRequest.id == contact_request_id,
            ContactRequest.recipient_owner_id == principal.subject,
        ]
        if principal.method != "clerk_jwt":
            row_conditions.append(ContactRequest.origin != "agent_outreach")
        row = await session.scalar(select(ContactRequest).where(*row_conditions).with_for_update())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        if row is None or retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="contact request was not found")
        if row.origin == "agent_outreach" and principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="agent outreach requests require a Clerk-human decision",
            )
        if row.status != "pending":
            raise HTTPException(status_code=409, detail="contact request is already decided")
        if action in {"block", "report"}:
            await session.scalar(
                select(ContactPolicy)
                .where(ContactPolicy.owner_id == principal.subject)
                .with_for_update()
            )
        now = datetime.now(UTC)
        row.status = {
            "accept": "accepted",
            "reject": "rejected",
            "block": "blocked",
            "report": "reported",
        }[action]
        row.decision_actor_id = principal.audit_actor_id
        row.decided_at = now
        row.report_reason = reason if action == "report" else None
        if action in {"block", "report"}:
            existing = await session.scalar(
                select(ContactBlock)
                .where(
                    ContactBlock.blocker_owner_id == principal.subject,
                    ContactBlock.blocked_owner_id == row.sender_owner_id,
                )
                .with_for_update()
            )
            if existing is None:
                session.add(
                    ContactBlock(
                        blocker_owner_id=principal.subject,
                        blocked_owner_id=row.sender_owner_id,
                        created_at=now,
                    )
                )
        for owner_id in {principal.subject, row.sender_owner_id}:
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type=f"contact_request.{row.status}",
                    resource_type="contact_request",
                    resource_id=row.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps({"status": row.status}, sort_keys=True),
                    occurred_at=now,
                )
            )
        result = contact_response(row, principal)
        response_body = idempotency_replay_json(result)
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body="",
            headers={},
            resource_type="contact_request_decision",
            resource_id=f"{row.id}:{action}:{row.origin}:{_contact_decision_receipt_digest(row, action, response_body)}",
        )
        return Response(content=response_body, status_code=200, media_type="application/json")

    @app.post(
        "/v1/organizations",
        response_model=OrganizationResponse,
        status_code=201,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra=_mutation_openapi_extra(),
    )
    async def create_organization(
        body: OrganizationCreateRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationResponse | Response:
        assert_not_impersonated_clerk(principal)
        assert_scope(principal, "organizations:write")
        assert_direct(principal)
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only a signed-in human can establish an organization mandate",
            )
        if body.visibility == "public":
            raise HTTPException(
                status_code=422,
                detail="an unverified organization must be created with private visibility",
            )
        key = idempotency_key(request, required=True)
        operation = "POST:/v1/organizations"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        if await identifier_is_reserved(
            session, settings, namespace="organization", identifier=body.slug
        ):
            raise HTTPException(status_code=409, detail="organization already exists")
        now = datetime.now(UTC)
        row = Organization(
            id=new_id(),
            owner_id=principal.subject,
            slug=body.slug,
            name=body.name,
            description=body.description,
            website_url=body.website_url,
            visibility=body.visibility,
            version=1,
            created_at=now,
            updated_at=now,
        )
        result = organization_response(row)
        headers = {"ETag": result.etag, "Location": f"/v1/organizations/{row.slug}"}
        session.add(row)
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type="organization.created",
                resource_type="organization",
                resource_id=row.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=principal.grant_id,
                payload=json.dumps({"slug": row.slug, "version": row.version}, sort_keys=True),
                occurred_at=now,
            )
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="organization already exists") from exc
        if await identifier_is_reserved(
            session, settings, namespace="organization", identifier=body.slug
        ):
            raise HTTPException(status_code=409, detail="organization already exists")
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body=result.model_dump_json(),
            headers=headers,
            resource_type="organization",
            resource_id=row.id,
        )
        response.headers.update(headers)
        return result

    @app.get(
        "/v1/organizations",
        response_model=OrganizationListResponse,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
    )
    async def list_organizations(
        q: Annotated[str | None, Query(max_length=200)] = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationListResponse:
        if not settings.recruiting_enabled:
            return OrganizationListResponse(organizations=[], next_cursor=None)
        current = datetime.now(UTC)
        event = aliased(OrganizationVerificationEvent)
        verification = aliased(OrganizationVerification)
        evidence = aliased(OrganizationVerificationEvidence)
        newer_event = aliased(OrganizationVerificationEvent)
        organization_query = q.strip() if q is not None else None
        statement = (
            select(Organization, event, verification, evidence)
            .join(event, event.organization_id == Organization.id)
            .join(verification, event.verification_id == verification.id)
            .join(evidence, evidence.verification_id == verification.id)
            .where(
                Organization.visibility == "public",
                active_recruiting_verification_predicate(
                    Organization.id,
                    event,
                    verification,
                    evidence,
                    newer_event,
                    current,
                ),
            )
        )
        if organization_query is not None:
            term = organization_query
            if not term:
                raise HTTPException(status_code=400, detail="organization query must not be blank")
            pattern = f"%{term}%"
            statement = statement.where(
                or_(Organization.name.ilike(pattern), Organization.slug.ilike(pattern))
            )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="organizations",
                bindings=(organization_query or "",),
                detail="organization cursor is malformed",
            )
            try:
                if payload["scope"] != "organizations" or payload["v"] != 1:
                    raise ValueError
                updated_at = datetime.fromisoformat(str(payload["updated_at"]))
                row_id = str(payload["id"])
                cursor_mode = payload.get("mode", "eligible")
                if cursor_mode not in {"eligible", "raw"}:
                    raise ValueError
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="organization cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    Organization.updated_at < updated_at,
                    and_(Organization.updated_at == updated_at, Organization.id < row_id),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(
                    Organization.updated_at.desc(),
                    Organization.id.desc(),
                    evidence.id.asc(),
                ).limit(limit + 1)
            )
        ).all()
        grouped_rows: dict[str, list[Any]] = {}
        for row in rows:
            grouped_rows.setdefault(row[0].id, []).append(row)
        verified_rows: list[tuple[Organization, OrganizationVerificationEvent]] = []
        for candidates in grouped_rows.values():
            organization = candidates[0][0]
            for _, event_row, verification_row, evidence_row in candidates:
                active = active_recruiting_verification_from_join(
                    organization,
                    event_row,
                    verification_row,
                    evidence_row,
                    current,
                )
                if active is not None:
                    verified_rows.append((organization, active))
                    break
        page = verified_rows[:limit]
        next_cursor = None
        cursor_row: Organization | None = None
        cursor_mode = None
        if len(verified_rows) > limit and page:
            cursor_row = page[-1][0]
            cursor_mode = "eligible"
        elif len(rows) > limit and rows:
            cursor_row = rows[-1][0]
            cursor_mode = "raw"
        if cursor_row is not None and cursor_mode is not None:
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "organizations",
                    "updated_at": cursor_row.updated_at.isoformat(),
                    "id": cursor_row.id,
                    "mode": cursor_mode,
                },
                scope="organizations",
                bindings=(organization_query or "",),
            )
        return OrganizationListResponse(
            organizations=[organization_response(row, active) for row, active in page],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/employer/organizations",
        response_model=EmployerOrganizationInventoryResponse,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def list_employer_organizations(
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> EmployerOrganizationInventoryResponse:
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only a signed-in Clerk human can list employer organizations",
            )
        scope = "employer-organizations"
        boundary: tuple[datetime, str] | None = None
        if cursor:
            boundary = employer_inventory_cursor_decode(
                cursor, scope=scope, subject=principal.subject, label="organization"
            )
            await require_employer_inventory_cursor_boundary(
                session,
                subject=principal.subject,
                scope=scope,
                updated_at=boundary[0],
                row_id=boundary[1],
                entity="organization",
            )
        statement = employer_organization_statement(principal.subject)
        if boundary is not None:
            updated_at, row_id = boundary
            statement = statement.where(
                or_(
                    Organization.updated_at < updated_at,
                    and_(Organization.updated_at == updated_at, Organization.id < row_id),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(Organization.updated_at.desc(), Organization.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        now = datetime.now(UTC)
        organizations: list[EmployerOrganizationSummary] = []
        for row in page:
            organization = cast(Organization, row[0])
            active_verification = await active_recruiting_verification(session, organization, now)
            organizations.append(
                EmployerOrganizationSummary(
                    id=organization.id,
                    slug=organization.slug,
                    name=organization.name,
                    management_role=cast(
                        Any,
                        "owner" if organization.owner_id == principal.subject else "admin",
                    ),
                    visibility=cast(Any, organization.visibility),
                    recruiting_verification_active=active_verification is not None,
                    recruiting_verification_purpose=(
                        "recruiting_control" if active_verification is not None else None
                    ),
                    recruiting_verification_expires_at=(
                        verification_event_expiry(active_verification)
                        if active_verification is not None
                        else None
                    ),
                    updated_at=organization.updated_at,
                )
            )
        next_cursor = None
        if len(rows) > limit and page:
            last_organization = cast(Organization, page[-1][0])
            next_cursor = employer_inventory_cursor_encode(
                scope=scope,
                subject=principal.subject,
                updated_at=last_organization.updated_at,
                row_id=last_organization.id,
            )
        return EmployerOrganizationInventoryResponse(
            organizations=organizations,
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/organizations/{organization_slug}",
        response_model=OrganizationResponse,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
    )
    async def get_organization(
        organization_slug: str,
        response: Response,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationResponse:
        organization = await organization_by_slug(session, organization_slug)
        await can_read_organization(session, organization, principal)
        result = organization_response(
            organization, await active_recruiting_verification(session, organization)
        )
        response.headers["ETag"] = result.etag
        return result

    @app.put(
        "/v1/organizations/{organization_slug}",
        response_model=OrganizationResponse,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra=_mutation_openapi_extra(if_match=True),
    )
    async def update_organization(
        organization_slug: str,
        body: OrganizationUpdateRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationResponse | Response:
        assert_not_impersonated_clerk(principal)
        if body.visibility == "public":
            require_recruiting_release()
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        if session.get_bind().dialect.name == "sqlite":
            # SQLite ignores SELECT ... FOR UPDATE.  This no-op write provides
            # its equivalent transaction-serialization point so a matching
            # same-key submit rechecks the winner before it can materialize a
            # second snapshot. PostgreSQL uses the row lock above instead.
            await session.execute(
                update(Organization)
                .where(Organization.id == organization.id)
                .values(version=Organization.version)
                .execution_options(synchronize_session=False)
            )
        await assert_organization_authority(
            session, organization, principal, scope="organizations:write"
        )
        key = idempotency_key(request, required=True)
        operation = f"PUT:/v1/organizations/{organization.id}"
        supplied = request.headers.get("If-Match")
        fingerprint = _request_fingerprint(operation, body.model_dump_json(), supplied)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        require_if_match(request, organization_etag(organization), "organization")
        fields = body.model_fields_set
        if not fields:
            raise HTTPException(
                status_code=422, detail="organization update requires at least one field"
            )
        if (
            body.visibility == "public"
            and await active_recruiting_verification(session, organization) is None
        ):
            raise HTTPException(
                status_code=409,
                detail="organization verification is required before public visibility",
            )
        for field in fields:
            setattr(organization, field, getattr(body, field))
        if fields.intersection({"name", "website_url"}):
            organization.verification_material_version += 1
        now = datetime.now(UTC)
        organization.version += 1
        organization.updated_at = now
        result = organization_response(
            organization, await active_recruiting_verification(session, organization)
        )
        headers = {"ETag": result.etag}
        session.add(
            ChangeEvent(
                owner_id=organization.owner_id,
                event_type="organization.updated",
                resource_type="organization",
                resource_id=organization.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=principal.grant_id,
                payload=json.dumps(
                    {"fields": sorted(fields), "version": organization.version}, sort_keys=True
                ),
                occurred_at=now,
            )
        )
        if principal.subject != organization.owner_id:
            session.add(
                ChangeEvent(
                    owner_id=principal.subject,
                    event_type="organization.updated",
                    resource_type="organization",
                    resource_id=organization.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps(
                        {"fields": sorted(fields), "version": organization.version}, sort_keys=True
                    ),
                    occurred_at=now,
                )
            )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body=result.model_dump_json(),
            headers=headers,
            resource_type="organization",
            resource_id=organization.id,
        )
        response.headers.update(headers)
        return result

    @app.get(
        "/v1/organizations/{organization_slug}/verification-status",
        response_model=OrganizationVerificationOwnerStatusResponse,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def get_organization_verification_status(
        organization_slug: str,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationVerificationOwnerStatusResponse:
        organization = await organization_by_slug(session, organization_slug)
        await assert_organization_authority(
            session,
            organization,
            principal,
            scope="organizations:read",
            owner_only=True,
            mutate=False,
        )
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only the signed-in organization owner can read verification status",
            )
        latest = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(
                OrganizationVerificationEvent.organization_id == organization.id,
                OrganizationVerificationEvent.purpose == "recruiting_control",
            )
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
        if latest is None:
            return owner_verification_status_response(None, None, datetime.now(UTC))
        verification = await session.get(OrganizationVerification, latest.verification_id)
        if verification is None:
            raise HTTPException(status_code=503, detail="verification status is unavailable")
        return owner_verification_status_response(verification, latest, datetime.now(UTC))

    @app.post(
        "/v1/organizations/{organization_slug}/verification-submissions",
        response_model=OrganizationVerificationSubmissionResponse,
        status_code=201,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra=_mutation_openapi_extra(),
    )
    async def submit_organization_verification(
        organization_slug: str,
        body: OrganizationVerificationSubmissionRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationVerificationSubmissionResponse | Response:
        assert_not_impersonated_clerk(principal)
        organization = await organization_by_slug(session, organization_slug)
        await assert_organization_authority(
            session,
            organization,
            principal,
            scope="organizations:write",
            owner_only=True,
        )
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only the signed-in organization owner can submit verification evidence",
            )
        key = idempotency_key(request, required=True)
        assert key is not None
        operation = f"POST:/v1/organizations/{organization.id}/verification-submissions"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        verification_id = derive_artifact_intent_uuid(
            artifact_pepper(),
            flow="organization_verification_evidence",
            owner_id=principal.subject,
            target_id=organization.id,
            idempotency_key=key,
        )
        session.info[
            "connectmd_artifact_intent_gate"
        ] = await request.app.state.artifact_reconciler.acquire_intent_gate(verification_id)
        await acquire_artifact_intent_lock(session, verification_id)
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        await assert_organization_authority(
            session,
            organization,
            principal,
            scope="organizations:write",
            owner_only=True,
        )
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        latest = await session.scalar(
            select(OrganizationVerificationEvent)
            .where(
                OrganizationVerificationEvent.organization_id == organization.id,
                OrganizationVerificationEvent.purpose == "recruiting_control",
            )
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
        )
        now = datetime.now(UTC)
        if latest is not None and (
            latest.to_state in {"submitted", "under_review"}
            or (
                latest.to_state == "active"
                and await active_recruiting_verification(session, organization, now) is not None
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="an active or pending recruiting verification already exists",
            )
        artifact = b64decode(body.artifact_base64, validate=True)
        artifact_sha256 = sha256(artifact).hexdigest()
        verification = OrganizationVerification(
            id=verification_id,
            organization_id=organization.id,
            purpose="recruiting_control",
            submitted_by_owner_id=principal.subject,
            material_claim_digest=material_claim_digest(
                organization_id=organization.id,
                organization_name=organization.name,
                organization_website_url=organization.website_url,
                organization_material_version=organization.verification_material_version,
                evidence_kind=body.evidence_kind,
                metadata=body.metadata,
                artifact_content_type=body.artifact_content_type,
                artifact_sha256=artifact_sha256,
                artifact_size_bytes=len(artifact),
            ),
            created_at=now,
        )
        relative_path = (
            f"verification-evidence/{organization.id}/{verification.id}/{artifact_sha256}.bin"
        )
        try:
            descriptor = stage_artifact(
                request.app.state.store,
                artifact_pepper(),
                flow="organization_verification_evidence",
                owner_id=principal.subject,
                target_id=organization.id,
                idempotency_key=key,
                request_hash=fingerprint,
                canonical_path=relative_path,
                payload=artifact,
                max_size_bytes=262_144,
            )
        except ArtifactDurabilityUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="verification evidence storage is unavailable"
            ) from exc
        cleanup = RollbackFileCleanup(
            relative_path=relative_path,
            sha256=artifact_sha256,
            size_bytes=len(artifact),
            max_size_bytes=262_144,
        )
        register_application_snapshot_rollback_cleanup(session, cleanup)
        evidence = OrganizationVerificationEvidence(
            id=new_id(),
            verification_id=verification.id,
            evidence_kind=body.evidence_kind,
            metadata_json=json.dumps(body.metadata, sort_keys=True, separators=(",", ":")),
            artifact_content_type=body.artifact_content_type,
            artifact_sha256=artifact_sha256,
            artifact_size_bytes=len(artifact),
            storage_path=relative_path,
            created_at=now,
            retention_expires_at=now + timedelta(days=365),
        )
        event = OrganizationVerificationEvent(
            id=new_id(),
            verification_id=verification.id,
            organization_id=organization.id,
            purpose="recruiting_control",
            to_state="submitted",
            actor_id=principal.audit_actor_id,
            actor_role="submitter",
            policy_version=None,
            material_claim_digest=verification.material_claim_digest,
            expires_at=None,
            occurred_at=now,
        )
        result = OrganizationVerificationSubmissionResponse(
            verification_id=verification.id,
            state="submitted",
            evidence_sha256=artifact_sha256,
            artifact_content_type=body.artifact_content_type,
            artifact_size_bytes=len(artifact),
            submitted_at=now,
        )
        session.add_all((verification, evidence, event))
        session.add(
            ChangeEvent(
                owner_id=organization.owner_id,
                event_type="organization_verification.submitted",
                resource_type="organization_verification",
                resource_id=verification.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=None,
                payload=json.dumps({"state": "submitted"}, sort_keys=True),
                occurred_at=now,
            )
        )
        replay_after_commit = await commit_artifact_transaction(
            session,
            request,
            principal,
            descriptor,
            cleanup,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body=result.model_dump_json(),
            resource_type="organization_verification",
            resource_id=verification.id,
        )
        if replay_after_commit is not None:
            return replay_after_commit
        return result

    @app.get(
        "/v1/internal/recruiting-verifications",
        response_model=OrganizationVerificationReviewerListResponse,
        tags=["internal"],
        include_in_schema=False,
    )
    async def list_recruiting_verification_queue(
        request: Request,
        response: Response,
        state: Literal["submitted", "under_review"] | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationVerificationReviewerListResponse:
        authority = request.app.state.settings
        require_configured_verification_reviewer(principal, authority)
        response.headers.update(verification_review_headers())
        states = [state] if state is not None else ["submitted", "under_review"]
        cursor_scope = f"recruiting_verification_queue:{state or 'open'}"
        verification_cursor_bindings = cursor_principal_bindings(principal)
        latest_event_id = (
            select(OrganizationVerificationEvent.id)
            .where(
                OrganizationVerificationEvent.organization_id == Organization.id,
                OrganizationVerificationEvent.purpose == "recruiting_control",
            )
            .order_by(
                OrganizationVerificationEvent.occurred_at.desc(),
                OrganizationVerificationEvent.id.desc(),
            )
            .limit(1)
            .correlate(Organization)
            .scalar_subquery()
        )
        statement = (
            select(
                OrganizationVerification,
                Organization,
                OrganizationVerificationEvidence,
                OrganizationVerificationEvent,
            )
            .join(Organization, Organization.id == OrganizationVerification.organization_id)
            .join(
                OrganizationVerificationEvidence,
                OrganizationVerificationEvidence.verification_id == OrganizationVerification.id,
            )
            .join(
                OrganizationVerificationEvent,
                OrganizationVerificationEvent.verification_id == OrganizationVerification.id,
            )
            .where(
                OrganizationVerificationEvent.id == latest_event_id,
                OrganizationVerificationEvent.to_state.in_(states),
            )
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope=cursor_scope,
                bindings=verification_cursor_bindings,
                detail="verification cursor is malformed",
            )
            try:
                if payload["scope"] != cursor_scope or payload["v"] != 1:
                    raise ValueError
                occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))
                verification_id = str(payload["verification_id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail="verification cursor is malformed",
                    headers=verification_review_headers(),
                ) from exc
            statement = statement.where(
                or_(
                    OrganizationVerificationEvent.occurred_at < occurred_at,
                    and_(
                        OrganizationVerificationEvent.occurred_at == occurred_at,
                        OrganizationVerification.id < verification_id,
                    ),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(
                    OrganizationVerificationEvent.occurred_at.desc(),
                    OrganizationVerification.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            verification, _, _, event = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": cursor_scope,
                    "occurred_at": event.occurred_at.isoformat(),
                    "verification_id": verification.id,
                },
                scope=cursor_scope,
                bindings=verification_cursor_bindings,
            )
        now = datetime.now(UTC)
        return OrganizationVerificationReviewerListResponse(
            verifications=[
                reviewer_verification_summary(verification, organization, evidence, event, now)
                for verification, organization, evidence, event in page
            ],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/internal/recruiting-verifications/{verification_id}",
        response_model=OrganizationVerificationReviewerDetailResponse,
        tags=["internal"],
        include_in_schema=False,
    )
    async def inspect_recruiting_verification(
        verification_id: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationVerificationReviewerDetailResponse:
        require_configured_verification_reviewer(principal, request.app.state.settings)
        verification, organization, evidence, event = await current_organization_verification(
            session, verification_id
        )
        now = datetime.now(UTC)
        verified = verified_reviewer_evidence(
            request, organization, verification, evidence, now=now
        )
        result = reviewer_verification_detail(
            verification, organization, evidence, event, verified, now
        )
        response.headers.update({**verification_review_headers(), "ETag": result.review_etag})
        return result

    @app.get(
        "/v1/internal/recruiting-verifications/{verification_id}/evidence",
        tags=["internal"],
        include_in_schema=False,
    )
    async def read_recruiting_verification_evidence(
        verification_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_configured_verification_reviewer(principal, request.app.state.settings)
        verification, organization, evidence, _ = await current_organization_verification(
            session, verification_id
        )
        verified = verified_reviewer_evidence(
            request,
            organization,
            verification,
            evidence,
            now=datetime.now(UTC),
        )
        artifact_digest = b64encode(bytes.fromhex(verified.artifact_sha256)).decode("ascii")
        extension = artifact_extension(evidence.artifact_content_type)
        return Response(
            content=verified.payload,
            headers={
                **verification_review_headers(),
                "Content-Type": evidence.artifact_content_type,
                "Content-Length": str(verified.artifact_size_bytes),
                "Content-Disposition": (
                    f'attachment; filename="connectmd-verification-evidence.{extension}"'
                ),
                "ETag": strong_etag(verified.artifact_sha256),
                "Content-Digest": f"sha-256=:{artifact_digest}:",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
            },
        )

    @app.post(
        "/v1/internal/recruiting-verifications/{verification_id}/{action}",
        response_model=OrganizationVerificationReviewerSummaryResponse,
        tags=["internal"],
        include_in_schema=False,
    )
    async def decide_recruiting_verification(
        verification_id: str,
        action: str,
        body: OrganizationVerificationDecisionRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationVerificationReviewerSummaryResponse | Response:
        authority = request.app.state.settings
        require_configured_verification_reviewer(principal, authority)
        response.headers.update(verification_review_headers())
        transitions = {
            "review": {"submitted"},
            "activate": {"under_review"},
            "reject": {"under_review"},
            "expire": {"active", "expired"},
            "suspend": {"active"},
            "revoke": {"active"},
            "restore": {"suspended"},
        }
        target_states = {
            "review": "under_review",
            "activate": "active",
            "reject": "rejected",
            "expire": "expired",
            "suspend": "suspended",
            "revoke": "revoked",
            "restore": "active",
        }
        if action not in transitions:
            verification_review_error(404, "verification action was not found")
        if action in {"activate", "restore"}:
            require_recruiting_release()
        evidence_bound_actions = {"review", "activate", "reject", "restore"}
        review_if_match = request.headers.get("If-Match")
        if action in evidence_bound_actions:
            if review_if_match is None:
                verification_review_error(
                    428, "If-Match is required for this verification decision"
                )
            if re.fullmatch(STRONG_DOCUMENT_ETAG_PATTERN, review_if_match) is None:
                verification_review_error(412, "If-Match must be the exact current review ETag")
        else:
            review_if_match = None
        try:
            key = idempotency_key(request, required=True)
        except HTTPException as exc:
            exc.headers = {**verification_review_headers(), **(exc.headers or {})}
            raise
        operation = f"POST:/v1/internal/recruiting-verifications/{verification_id}/{action}"
        fingerprint = _request_fingerprint(operation, body.model_dump_json(), review_if_match)
        try:
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
        except HTTPException as exc:
            exc.headers = {**verification_review_headers(), **(exc.headers or {})}
            raise
        if replay is not None:
            return replay
        verification, organization, evidence, latest = await current_organization_verification(
            session, verification_id, for_update=True
        )
        try:
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
        except HTTPException as exc:
            exc.headers = {**verification_review_headers(), **(exc.headers or {})}
            raise
        if replay is not None:
            return replay
        now = datetime.now(UTC)
        current_state = verification_effective_state(latest, now)
        if body.expected_state != current_state:
            verification_review_error(412, "verification state is stale")
        if current_state not in transitions[action]:
            verification_review_error(
                409, "verification transition is not allowed from its current state"
            )
        target_state = target_states[action]
        policy_version = latest.policy_version
        expires_at = verification_event_expiry(latest)
        if target_state == "active":
            if body.policy_version is None or body.expires_at is None:
                verification_review_error(
                    422, "active verification requires policy version and expiry"
                )
            evidence_expires_at = evidence.retention_expires_at
            if evidence_expires_at.tzinfo is None:
                evidence_expires_at = evidence_expires_at.replace(tzinfo=UTC)
            expires_at = body.expires_at.astimezone(UTC)
            if expires_at <= now:
                verification_review_error(422, "expiry must be in the future")
            if expires_at > evidence_expires_at:
                verification_review_error(
                    422, "active decision expiry cannot outlive retained evidence"
                )
            policy_version = body.policy_version
        verified: VerifiedRecruitingEvidence | None = None
        if action in evidence_bound_actions:
            verified = verified_reviewer_evidence(
                request,
                organization,
                verification,
                evidence,
                now=now,
            )
            assert review_if_match is not None
            current_review_etag = strong_etag(verified.review_snapshot_sha256)
            if not compare_digest(review_if_match, current_review_etag):
                verification_review_error(412, "If-Match does not match the current review ETag")
        assert authority.verification_reviewer_id is not None
        assert authority.verification_reviewer_role is not None
        event = OrganizationVerificationEvent(
            id=new_id(),
            verification_id=verification.id,
            organization_id=organization.id,
            purpose="recruiting_control",
            to_state=target_state,
            actor_id=authority.verification_reviewer_id,
            actor_role=authority.verification_reviewer_role,
            policy_version=policy_version,
            material_claim_digest=(
                verified.material_claim_digest
                if verified is not None
                else verification.material_claim_digest
            ),
            expires_at=expires_at if target_state in {"active", "suspended"} else None,
            occurred_at=now,
        )
        result = reviewer_verification_summary(verification, organization, evidence, event, now)
        response_body = result.model_dump_json()
        response_headers = verification_review_headers()
        assert key is not None
        decision_resource_id = _recruiting_decision_resource_id(
            event,
            verification,
            action=action,
            owner_id=principal.subject,
            idempotency_key=key,
            operation=operation,
            request_hash=fingerprint,
            response_status=200,
            response_body=response_body,
            response_headers=response_headers,
        )
        session.add(event)
        session.add(
            ChangeEvent(
                owner_id=organization.owner_id,
                event_type=f"organization_verification.{target_state}",
                resource_type="organization_verification",
                resource_id=verification.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=None,
                payload=json.dumps({"state": target_state}, sort_keys=True),
                occurred_at=now,
            )
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body=response_body,
            headers=response_headers,
            resource_type=_RECRUITING_DECISION_RESOURCE_TYPE,
            resource_id=decision_resource_id,
        )
        return result

    @app.get(
        "/v1/organization-membership-invitations",
        response_model=OrganizationMembershipInvitationListResponse,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def list_organization_membership_invitations(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationMembershipInvitationListResponse:
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only a signed-in human can list organization invitations",
            )
        assert_scope(principal, "organizations:read")
        subject_binding = sha256(
            f"organization-membership-invitations:{principal.subject}".encode()
        ).hexdigest()
        invitation_cursor_bindings = cursor_principal_bindings(principal)
        statement = (
            select(OrganizationMembership, Organization)
            .join(Organization, OrganizationMembership.organization_id == Organization.id)
            .where(
                OrganizationMembership.member_owner_id == principal.subject,
                OrganizationMembership.status == "invited",
            )
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="organization-membership-invitations",
                bindings=invitation_cursor_bindings,
                detail="organization invitation cursor is malformed",
            )
            try:
                if (
                    payload["scope"] != "organization-membership-invitations"
                    or payload["v"] != 1
                    or not compare_digest(str(payload["subject"]), subject_binding)
                ):
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="organization invitation cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    OrganizationMembership.created_at < created_at,
                    and_(
                        OrganizationMembership.created_at == created_at,
                        OrganizationMembership.id < row_id,
                    ),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(
                    OrganizationMembership.created_at.desc(),
                    OrganizationMembership.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last_membership, _ = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "organization-membership-invitations",
                    "subject": subject_binding,
                    "created_at": last_membership.created_at.isoformat(),
                    "id": last_membership.id,
                },
                scope="organization-membership-invitations",
                bindings=invitation_cursor_bindings,
            )
        return OrganizationMembershipInvitationListResponse(
            invitations=[
                OrganizationMembershipInvitationResponse(
                    id=membership.id,
                    organization_id=organization.id,
                    organization_slug=organization.slug,
                    organization_name=organization.name,
                    role=cast(Any, membership.role),
                    status="invited",
                    created_at=membership.created_at,
                )
                for membership, organization in page
            ],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/organizations/{organization_slug}/members",
        response_model=OrganizationAdminListResponse,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def list_organization_members(
        organization_slug: str,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationAdminListResponse:
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only a signed-in human organization owner can list members",
            )
        organization = await organization_by_slug(session, organization_slug)
        await assert_organization_authority(
            session,
            organization,
            principal,
            scope="organizations:read",
            owner_only=True,
            mutate=False,
        )
        member_cursor_bindings = cursor_principal_bindings(principal) + (organization.id,)
        statement = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="organization-members",
                bindings=member_cursor_bindings,
                detail="organization member cursor is malformed",
            )
            try:
                if (
                    payload["scope"] != "organization-members"
                    or payload["organization_id"] != organization.id
                    or payload["v"] != 1
                ):
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="organization member cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    OrganizationMembership.created_at < created_at,
                    and_(
                        OrganizationMembership.created_at == created_at,
                        OrganizationMembership.id < row_id,
                    ),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(
                    OrganizationMembership.created_at.desc(),
                    OrganizationMembership.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "organization-members",
                    "organization_id": organization.id,
                    "created_at": last.created_at.isoformat(),
                    "id": last.id,
                },
                scope="organization-members",
                bindings=member_cursor_bindings,
            )
        return OrganizationAdminListResponse(
            members=[
                OrganizationAdminResponse(
                    id=membership.id,
                    organization_id=organization.id,
                    member_profile_handle=membership.member_profile_handle,
                    role=cast(Any, membership.role),
                    status=cast(Any, membership.status),
                    created_at=membership.created_at,
                )
                for membership in page
            ],
            next_cursor=next_cursor,
        )

    @app.post(
        "/v1/organizations/{organization_slug}/admins",
        response_model=OrganizationAdminResponse,
        status_code=201,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response(
                "Only a signed-in Clerk human organization owner can invite members."
            ),
            404: _error_response("The organization, profile, or membership was not found."),
            409: _error_response("The key or membership conflicts with an existing operation."),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("The durable invitation receipt is unavailable."),
        },
        openapi_extra={
            "x-connectmd-human-only": True,
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                }
            ],
        },
    )
    async def add_organization_admin(
        organization_slug: str,
        body: OrganizationAdminCreateRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationAdminResponse | Response:
        assert_not_impersonated_clerk(principal)
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403, detail="only a signed-in human can invite organization members"
            )
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        await assert_organization_authority(
            session,
            organization,
            principal,
            scope="organizations:write",
            owner_only=True,
        )
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/organizations/{organization.id}/admins"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        member_profile = await public_profile_by_handle(session, body.member_profile_handle)
        member_owner_id = member_profile.owner_id
        if member_owner_id == organization.owner_id:
            raise HTTPException(status_code=409, detail="organization owner is already a member")
        existing = await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.member_owner_id == member_owner_id,
            )
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="organization administrator already exists")
        now = datetime.now(UTC)
        member = OrganizationMembership(
            id=new_id(),
            organization_id=organization.id,
            member_owner_id=member_owner_id,
            member_profile_handle=member_profile.public_identifier,
            role=body.role,
            status="invited",
            invited_by_owner_id=principal.subject,
            created_at=now,
        )
        result = _organization_admin_response(member)
        session.add(member)
        for owner_id in {organization.owner_id, member_owner_id}:
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type="organization.member_invited",
                    resource_type="organization_membership",
                    resource_id=organization.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps({"role": member.role}, sort_keys=True),
                    occurred_at=now,
                )
            )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body="",
            headers={},
            resource_type="organization_membership",
            resource_id=f"{member.id}:{_organization_membership_generation_digest(member)}",
        )
        return result

    @app.post(
        "/v1/organizations/{organization_slug}/memberships/{membership_id}/accept",
        response_model=OrganizationAdminResponse,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response("Only a signed-in Clerk human can accept an invitation."),
            404: _error_response("The organization invitation was not found."),
            409: _error_response(
                "The invitation or idempotency key conflicts with existing state."
            ),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("The durable acceptance receipt is unavailable."),
        },
        openapi_extra={
            "x-connectmd-human-only": True,
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                }
            ],
        },
    )
    async def accept_organization_membership(
        organization_slug: str,
        membership_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> OrganizationAdminResponse | Response:
        assert_not_impersonated_clerk(principal)
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only a signed-in human can accept an organization invitation",
            )
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/organizations/{organization_slug}/memberships/{membership_id}/accept"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        # The first replay avoids an ordinary lock; this second check is the
        # organization-lock boundary for concurrent matching requests.
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        member = await session.scalar(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.id == membership_id,
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.member_owner_id == principal.subject,
            )
            .with_for_update()
        )
        if member is None:
            raise HTTPException(status_code=404, detail="organization invitation was not found")
        if member.status != "invited":
            raise HTTPException(
                status_code=409, detail="organization invitation is already accepted"
            )
        now = datetime.now(UTC)
        member.status = "active"
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type="organization.membership_accepted",
                resource_type="organization_membership",
                resource_id=organization.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                payload=json.dumps({"role": member.role}, sort_keys=True),
                occurred_at=now,
            )
        )
        if organization.owner_id != principal.subject:
            session.add(
                ChangeEvent(
                    owner_id=organization.owner_id,
                    event_type="organization.membership_accepted",
                    resource_type="organization_membership",
                    resource_id=organization.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    payload=json.dumps({"role": member.role}, sort_keys=True),
                    occurred_at=now,
                )
            )
        generation_digest = _organization_membership_generation_digest(member)
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body="",
            headers={},
            resource_type="organization_membership",
            resource_id=generation_digest,
        )
        await session.refresh(member)
        return _organization_admin_response(member)

    @app.delete(
        "/v1/organizations/{organization_slug}/memberships/{membership_id}",
        status_code=204,
        tags=["organizations"],
        include_in_schema=settings.recruiting_enabled,
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response(
                "Only a signed-in Clerk human organization owner can remove members."
            ),
            404: _error_response("The organization member was not found."),
            409: _error_response("The idempotency key conflicts with an existing operation."),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("The durable removal receipt is unavailable."),
        },
        openapi_extra={
            "x-connectmd-human-only": True,
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                }
            ],
        },
    )
    async def remove_organization_admin(
        organization_slug: str,
        membership_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        assert_not_impersonated_clerk(principal)
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403, detail="only a signed-in human can remove organization members"
            )
        key = idempotency_key(request, required=True)
        operation = f"DELETE:/v1/organizations/{organization_slug}/memberships/{membership_id}"
        fingerprint = _request_fingerprint(operation, "")
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        await assert_organization_authority(
            session,
            organization,
            principal,
            scope="organizations:write",
            owner_only=True,
        )
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        member = await session.scalar(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.id == membership_id,
                OrganizationMembership.organization_id == organization.id,
            )
            .with_for_update()
        )
        if member is None:
            raise HTTPException(status_code=404, detail="organization member was not found")
        member_owner_id = member.member_owner_id
        now = datetime.now(UTC)
        await session.delete(member)
        for owner_id in {organization.owner_id, member_owner_id}:
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type="organization.member_removed",
                    resource_type="organization_membership",
                    resource_id=organization.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps({"role": member.role}, sort_keys=True),
                    occurred_at=now,
                )
            )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=204,
            body="",
            headers={},
            resource_type="organization_membership",
            resource_id=membership_id,
        )
        return Response(status_code=204)

    @app.post(
        "/v1/organizations/{organization_slug}/jobs",
        response_model=JobResponse,
        status_code=201,
        tags=["jobs"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra=_mutation_openapi_extra(),
    )
    async def create_job(
        organization_slug: str,
        body: JobCreateRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> JobResponse | Response:
        assert_not_impersonated_clerk(principal)
        # PostgreSQL job mutations serialize in one order: organization, then job if present.
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        await assert_organization_authority(session, organization, principal, scope="jobs:write")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/organizations/{organization.id}/jobs"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        if await identifier_is_reserved(
            session,
            settings,
            namespace=f"job:{organization.id}",
            identifier=body.slug,
        ):
            raise HTTPException(status_code=409, detail="job already exists")
        now = datetime.now(UTC)
        row = Job(
            id=new_id(),
            organization_id=organization.id,
            slug=body.slug,
            title=body.title,
            description=body.description,
            location=body.location,
            work_mode=body.work_mode,
            employment_type=body.employment_type,
            status="draft",
            version=1,
            created_at=now,
            updated_at=now,
        )
        result = job_response(row, organization)
        headers = {
            "ETag": result.etag,
            "Location": f"/v1/organizations/{organization.slug}/jobs/{row.slug}",
        }
        session.add(row)
        session.add(job_version(row, organization, result))
        for owner_id in {organization.owner_id, principal.subject}:
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type="job.created",
                    resource_type="job",
                    resource_id=row.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps({"status": row.status, "title": row.title}, sort_keys=True),
                    occurred_at=now,
                )
            )
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session, request, principal, key, operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="job already exists") from exc
        if await identifier_is_reserved(
            session,
            settings,
            namespace=f"job:{organization.id}",
            identifier=body.slug,
        ):
            raise HTTPException(status_code=409, detail="job already exists")
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body="",
            headers=headers,
            resource_type="job",
            resource_id=f"{row.id}@{row.version}",
        )
        response.headers.update(headers)
        return result

    @app.get(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}",
        response_model=JobResponse,
        tags=["jobs"],
        include_in_schema=settings.recruiting_enabled,
    )
    async def get_job(
        organization_slug: str,
        job_slug: str,
        response: Response,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> JobResponse:
        organization = await organization_by_slug(session, organization_slug)
        job = await job_by_slug(session, organization, job_slug)
        await can_read_job(session, organization, job, principal)
        result = job_response(job, organization)
        response.headers["ETag"] = result.etag
        return result

    @app.put(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}",
        response_model=JobResponse,
        tags=["jobs"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra=_mutation_openapi_extra(if_match=True),
    )
    async def update_job(
        organization_slug: str,
        job_slug: str,
        body: JobUpdateRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> JobResponse | Response:
        """Update a job.

        Direct organization Agent Grants may update drafts. Updating a published or closed
        job requires an authenticated Clerk human.
        """
        assert_not_impersonated_clerk(principal)
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        job = await job_by_slug(session, organization, job_slug, for_update=True)
        await assert_organization_authority(session, organization, principal, scope="jobs:write")
        key = idempotency_key(request, required=True)
        operation = f"PUT:/v1/jobs/{job.id}"
        supplied = request.headers.get("If-Match")
        fingerprint = _request_fingerprint(operation, body.model_dump_json(), supplied)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        if job.status != "draft" and principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only an authorized signed-in human can update a non-draft job",
            )
        require_if_match(request, job_etag(job, organization), "job")
        fields = body.model_fields_set
        if not fields:
            raise HTTPException(status_code=422, detail="job update requires at least one field")
        for field in fields:
            setattr(job, field, getattr(body, field))
        now = datetime.now(UTC)
        job.version += 1
        job.updated_at = now
        result = job_response(job, organization)
        headers = {"ETag": result.etag}
        session.add(job_version(job, organization, result))
        for owner_id in {organization.owner_id, principal.subject}:
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type="job.updated",
                    resource_type="job",
                    resource_id=job.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps(
                        {"fields": sorted(fields), "version": job.version}, sort_keys=True
                    ),
                    occurred_at=now,
                )
            )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body="",
            headers=headers,
            resource_type="job",
            resource_id=f"{job.id}@{job.version}",
        )
        response.headers.update(headers)
        return result

    @app.post(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/lifecycle/{action}",
        response_model=JobResponse,
        tags=["jobs"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra=_mutation_openapi_extra(if_match=True),
    )
    async def change_job_lifecycle(
        organization_slug: str,
        job_slug: str,
        action: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> JobResponse | Response:
        assert_not_impersonated_clerk(principal)
        if action not in {"publish", "close"}:
            raise HTTPException(status_code=404, detail="job action was not found")
        if action == "publish":
            require_recruiting_release()
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        job = await job_by_slug(session, organization, job_slug, for_update=True)
        await assert_organization_authority(session, organization, principal, scope="jobs:write")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/jobs/{job.id}/{action}"
        supplied = request.headers.get("If-Match")
        fingerprint = _request_fingerprint(operation, "", supplied)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only an authorized signed-in human can publish or close a job",
            )
        require_if_match(request, job_etag(job, organization), "job")
        if (
            action == "publish"
            and await active_recruiting_verification(session, organization) is None
        ):
            raise HTTPException(
                status_code=409,
                detail="organization verification is required before job publication",
            )
        if action == "publish" and job.status != "draft":
            raise HTTPException(status_code=409, detail="only draft jobs can be published")
        if action == "close" and job.status != "published":
            raise HTTPException(status_code=409, detail="only published jobs can be closed")
        now = datetime.now(UTC)
        job.status = "published" if action == "publish" else "closed"
        if action == "publish":
            job.published_at = now
        job.version += 1
        job.updated_at = now
        result = job_response(job, organization)
        headers = {"ETag": result.etag}
        session.add(job_version(job, organization, result))
        for owner_id in {organization.owner_id, principal.subject}:
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type="job.closed" if action == "close" else "job.published",
                    resource_type="job",
                    resource_id=job.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps(
                        {"status": job.status, "version": job.version}, sort_keys=True
                    ),
                    occurred_at=now,
                )
            )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body="",
            headers=headers,
            resource_type="job",
            resource_id=f"{job.id}@{job.version}",
        )
        response.headers.update(headers)
        return result

    @app.get(
        "/v1/jobs",
        response_model=JobListResponse,
        tags=["jobs"],
        include_in_schema=settings.recruiting_enabled,
    )
    async def search_jobs(
        q: Annotated[str | None, Query(max_length=200)] = None,
        organization_slug: Annotated[str | None, Query(max_length=80)] = None,
        location: Annotated[str | None, Query(max_length=200)] = None,
        work_mode: str | None = None,
        employment_type: str | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        session: AsyncSession = Depends(get_session),
    ) -> JobListResponse:
        if not settings.recruiting_enabled:
            return JobListResponse(jobs=[], next_cursor=None)
        job_query = q.strip() if q is not None else None
        job_location = location.strip() if location is not None else None
        if work_mode is not None and work_mode not in {"remote", "hybrid", "onsite"}:
            raise HTTPException(status_code=400, detail="unknown work_mode")
        if employment_type is not None and employment_type not in {
            "full_time",
            "part_time",
            "contract",
            "internship",
            "temporary",
        }:
            raise HTTPException(status_code=400, detail="unknown employment_type")
        current = datetime.now(UTC)
        event = aliased(OrganizationVerificationEvent)
        verification = aliased(OrganizationVerification)
        evidence = aliased(OrganizationVerificationEvidence)
        newer_event = aliased(OrganizationVerificationEvent)
        statement = (
            select(Job, Organization, event, verification, evidence)
            .join(Organization, Job.organization_id == Organization.id)
            .join(event, event.organization_id == Organization.id)
            .join(verification, event.verification_id == verification.id)
            .join(evidence, evidence.verification_id == verification.id)
            .where(
                Job.status == "published",
                Organization.visibility == "public",
                active_recruiting_verification_predicate(
                    Organization.id,
                    event,
                    verification,
                    evidence,
                    newer_event,
                    current,
                ),
            )
        )
        if job_query is not None:
            term = job_query
            if not term:
                raise HTTPException(status_code=400, detail="job query must not be blank")
            pattern = f"%{term}%"
            statement = statement.where(
                or_(
                    Job.title.ilike(pattern),
                    Job.description.ilike(pattern),
                    Organization.name.ilike(pattern),
                )
            )
        if organization_slug is not None:
            statement = statement.where(Organization.slug == organization_slug)
        if job_location is not None:
            statement = statement.where(Job.location.ilike(f"%{job_location}%"))
        if work_mode is not None:
            statement = statement.where(Job.work_mode == work_mode)
        if employment_type is not None:
            statement = statement.where(Job.employment_type == employment_type)
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="jobs",
                bindings=(
                    job_query or "",
                    organization_slug or "",
                    job_location or "",
                    work_mode or "",
                    employment_type or "",
                ),
                detail="job cursor is malformed",
            )
            try:
                if payload["scope"] != "jobs" or payload["v"] != 1:
                    raise ValueError
                updated_at = datetime.fromisoformat(str(payload["updated_at"]))
                row_id = str(payload["id"])
                cursor_mode = payload.get("mode", "eligible")
                if cursor_mode not in {"eligible", "raw"}:
                    raise ValueError
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="job cursor is malformed") from exc
            statement = statement.where(
                or_(
                    Job.updated_at < updated_at,
                    and_(Job.updated_at == updated_at, Job.id < row_id),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(
                    Job.updated_at.desc(),
                    Job.id.desc(),
                    evidence.id.asc(),
                ).limit(limit + 1)
            )
        ).all()
        grouped_rows: dict[str, list[Any]] = {}
        for row in rows:
            grouped_rows.setdefault(row[0].id, []).append(row)
        verified_rows: list[tuple[Job, Organization]] = []
        for candidates in grouped_rows.values():
            job, organization = candidates[0][0], candidates[0][1]
            for _, _, event_row, verification_row, evidence_row in candidates:
                if (
                    active_recruiting_verification_from_join(
                        organization,
                        event_row,
                        verification_row,
                        evidence_row,
                        current,
                    )
                    is not None
                ):
                    verified_rows.append((job, organization))
                    break
        page = verified_rows[:limit]
        next_cursor = None
        cursor_job: Job | None = None
        cursor_mode = None
        if len(verified_rows) > limit and page:
            cursor_job = page[-1][0]
            cursor_mode = "eligible"
        elif len(rows) > limit and rows:
            cursor_job = rows[-1][0]
            cursor_mode = "raw"
        if cursor_job is not None and cursor_mode is not None:
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "jobs",
                    "updated_at": cursor_job.updated_at.isoformat(),
                    "id": cursor_job.id,
                    "mode": cursor_mode,
                },
                scope="jobs",
                bindings=(
                    job_query or "",
                    organization_slug or "",
                    job_location or "",
                    work_mode or "",
                    employment_type or "",
                ),
            )
        return JobListResponse(
            jobs=[job_response(job, organization) for job, organization in page],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/employer/jobs",
        response_model=EmployerJobInventoryResponse,
        tags=["jobs"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra={"x-connectmd-human-only": True},
    )
    async def list_employer_jobs(
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> EmployerJobInventoryResponse:
        if principal.method != "clerk_jwt":
            raise HTTPException(
                status_code=403,
                detail="only a signed-in Clerk human can list employer jobs",
            )
        scope = "employer-jobs"
        boundary: tuple[datetime, str] | None = None
        if cursor:
            boundary = employer_inventory_cursor_decode(
                cursor, scope=scope, subject=principal.subject, label="job"
            )
            await require_employer_inventory_cursor_boundary(
                session,
                subject=principal.subject,
                scope=scope,
                updated_at=boundary[0],
                row_id=boundary[1],
                entity="job",
            )
        statement = employer_job_statement(principal.subject)
        if boundary is not None:
            updated_at, row_id = boundary
            statement = statement.where(
                or_(
                    Job.updated_at < updated_at,
                    and_(Job.updated_at == updated_at, Job.id < row_id),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(Job.updated_at.desc(), Job.id.desc()).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        jobs = [
            EmployerJobSummary(
                id=job.id,
                organization_id=organization.id,
                organization_slug=organization.slug,
                organization_name=organization.name,
                management_role=cast(
                    Any,
                    "owner" if organization.owner_id == principal.subject else "admin",
                ),
                slug=job.slug,
                title=job.title,
                status=cast(Any, job.status),
                location=job.location,
                work_mode=cast(Any, job.work_mode),
                employment_type=cast(Any, job.employment_type),
                updated_at=job.updated_at,
            )
            for job, organization, _membership in page
        ]
        next_cursor = None
        if len(rows) > limit and page:
            last_job = cast(Job, page[-1][0])
            next_cursor = employer_inventory_cursor_encode(
                scope=scope,
                subject=principal.subject,
                updated_at=last_job.updated_at,
                row_id=last_job.id,
            )
        return EmployerJobInventoryResponse(jobs=jobs, next_cursor=next_cursor)

    @app.post(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
        response_model=ApplicationResponse,
        status_code=201,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
        openapi_extra=_mutation_openapi_extra(),
    )
    async def submit_application(
        organization_slug: str,
        job_slug: str,
        body: ApplicationCreateRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ApplicationResponse | Response:
        require_recruiting_release()
        require_application_human(principal)
        key = idempotency_key(request, required=True)
        assert key is not None
        operation = f"POST:/v1/organizations/{organization_slug}/jobs/{job_slug}/applications"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        organization = await organization_by_slug(session, organization_slug)
        job = await job_by_slug(session, organization, job_slug)
        application_id = derive_artifact_intent_uuid(
            artifact_pepper(),
            flow="application_snapshot",
            owner_id=principal.subject,
            target_id=job.id,
            idempotency_key=key,
        )
        session.info[
            "connectmd_artifact_intent_gate"
        ] = await request.app.state.artifact_reconciler.acquire_intent_gate(application_id)
        await acquire_artifact_intent_lock(session, application_id)
        # Intent lock precedes domain rows everywhere. The organization is
        # always locked before its Job, and authority is then rechecked.
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        job = await job_by_slug(session, organization, job_slug, for_update=True)
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        if (
            organization.visibility != "public"
            or await active_recruiting_verification(session, organization) is None
            or job.status != "published"
        ):
            raise HTTPException(status_code=404, detail="job was not found")
        if organization.owner_id == principal.subject:
            raise HTTPException(
                status_code=409, detail="organization owner cannot apply to its own job"
            )
        snapshot_document = await session.scalar(
            select(Document)
            .where(
                Document.kind == body.snapshot_kind,
                Document.public_identifier == body.snapshot_identifier,
                Document.owner_id == principal.subject,
                Document.visibility == "public",
            )
            .options(selectinload(Document.versions))
        )
        if snapshot_document is None:
            raise HTTPException(
                status_code=422,
                detail="application snapshot must reference one of your public canonical documents",
            )
        snapshot_version = current_version(snapshot_document)
        try:
            snapshot_markdown = request.app.state.store.read_verified(
                snapshot_version.storage_path, snapshot_version.sha256
            )
        except StorageIntegrityError as exc:
            raise HTTPException(
                status_code=503,
                detail="application snapshot source could not be verified",
            ) from exc
        snapshot_payload = snapshot_markdown.encode("utf-8")
        if len(snapshot_payload) > 131_072:
            raise HTTPException(
                status_code=503,
                detail="application snapshot source could not be verified",
            )
        duplicate = await session.scalar(
            select(Application).where(
                Application.job_id == job.id,
                Application.applicant_owner_id == principal.subject,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409, detail="an application for this job already exists"
            )
        now = datetime.now(UTC)
        quota_values = {
            "applicant_owner_id": principal.subject,
            "bucket_date": now.date(),
            "application_count": 1,
            "updated_at": now,
        }
        dialect_name = session.get_bind().dialect.name
        quota_insert: Any
        if dialect_name == "postgresql":
            quota_insert = postgresql_insert(ApplicationRateBucket).values(**quota_values)
        elif dialect_name == "sqlite":
            quota_insert = sqlite_insert(ApplicationRateBucket).values(**quota_values)
        else:  # pragma: no cover - the locked stack and tests use PostgreSQL/SQLite
            raise HTTPException(status_code=503, detail="application quota backend is unsupported")
        consumed = await session.scalar(
            quota_insert.on_conflict_do_update(
                index_elements=["applicant_owner_id", "bucket_date"],
                set_={
                    "application_count": ApplicationRateBucket.application_count + 1,
                    "updated_at": now,
                },
                where=ApplicationRateBucket.application_count < 20,
            ).returning(ApplicationRateBucket.application_count)
        )
        if consumed is None:
            raise HTTPException(
                status_code=429,
                detail="application daily limit reached",
                headers={"Retry-After": "86400"},
            )
        snapshot_storage_path = request.app.state.store.application_snapshot_relative_path(
            application_id
        )
        try:
            descriptor = stage_artifact(
                request.app.state.store,
                artifact_pepper(),
                flow="application_snapshot",
                owner_id=principal.subject,
                target_id=job.id,
                idempotency_key=key,
                request_hash=fingerprint,
                canonical_path=snapshot_storage_path,
                payload=snapshot_payload,
                max_size_bytes=131_072,
            )
        except ArtifactDurabilityUnavailable as exc:
            # Nothing has been committed yet.  The request dependency rolls back
            # the quota mutation and no application record is made visible.
            raise HTTPException(
                status_code=503,
                detail="application snapshot storage is unavailable",
            ) from exc
        cleanup = RollbackFileCleanup(
            relative_path=snapshot_storage_path,
            sha256=snapshot_version.sha256,
            size_bytes=len(snapshot_payload),
            max_size_bytes=131_072,
        )
        register_application_snapshot_rollback_cleanup(session, cleanup)
        if not compare_digest(
            descriptor.payload_sha256, snapshot_version.sha256
        ):  # pragma: no cover
            raise HTTPException(
                status_code=503,
                detail="application snapshot storage integrity check failed",
            )
        row = Application(
            id=application_id,
            job_id=job.id,
            applicant_owner_id=principal.subject,
            applicant_actor_id=principal.audit_actor_id,
            applicant_actor_method=principal.method,
            applicant_grant_id=principal.grant_id,
            snapshot_document_id=snapshot_document.id,
            snapshot_document_kind=snapshot_document.kind,
            snapshot_document_identifier=snapshot_document.public_identifier,
            snapshot_document_version=snapshot_version.version,
            snapshot_sha256=snapshot_version.sha256,
            snapshot_size_bytes=len(snapshot_payload),
            snapshot_storage_path=snapshot_storage_path,
            message=body.message,
            status="submitted",
            confirmed_by_owner_id=principal.subject,
            confirmed_at=now,
            retention_policy_version="application-retention-v1",
            retention_expires_at=now + timedelta(days=365),
            created_at=now,
            updated_at=now,
        )
        result = application_response(row, job, organization)
        session.add(row)
        for owner_id, event_type in {
            organization.owner_id: "application.received",
            principal.subject: "application.submitted",
        }.items():
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type=event_type,
                    resource_type="application",
                    resource_id=row.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps({"job_id": job.id, "status": row.status}, sort_keys=True),
                    occurred_at=now,
                )
            )
        replay_after_commit = await commit_artifact_transaction(
            session,
            request,
            principal,
            descriptor,
            cleanup,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body=result.model_dump_json(),
            resource_type="application",
            resource_id=row.id,
        )
        if replay_after_commit is not None:
            return replay_after_commit
        return result

    @app.get(
        "/v1/applications",
        response_model=ApplicationListResponse,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
    )
    async def list_my_applications(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ApplicationListResponse:
        require_application_human(principal)
        application_cursor_bindings = cursor_principal_bindings(principal)
        statement = (
            select(Application, Job, Organization)
            .join(Job, Application.job_id == Job.id)
            .join(Organization, Job.organization_id == Organization.id)
            .where(
                Application.applicant_owner_id == principal.subject,
                Application.retention_expires_at > datetime.now(UTC),
            )
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="my_applications",
                bindings=application_cursor_bindings,
                detail="application cursor is malformed",
            )
            try:
                if payload["scope"] != "my_applications" or payload["v"] != 1:
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="application cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    Application.created_at < created_at,
                    and_(Application.created_at == created_at, Application.id < row_id),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(Application.created_at.desc(), Application.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last, _, _ = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "my_applications",
                    "created_at": last.created_at.isoformat(),
                    "id": last.id,
                },
                scope="my_applications",
                bindings=application_cursor_bindings,
            )
        return ApplicationListResponse(
            applications=[
                application_response(application, job, organization)
                for application, job, organization in page
            ],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
        response_model=ApplicationListResponse,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
    )
    async def list_job_applications(
        organization_slug: str,
        job_slug: str,
        request: Request,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ApplicationListResponse:
        require_application_human(principal)
        organization = await organization_by_slug(session, organization_slug)
        job = await job_by_slug(session, organization, job_slug)
        await assert_active_employer_application_authority(session, organization, principal)
        if request.headers.get("X-Connectmd-Purpose") != "job_application_review":
            raise HTTPException(
                status_code=403,
                detail="X-Connectmd-Purpose: job_application_review is required to list applications",
            )
        statement = select(Application).where(
            Application.job_id == job.id,
            Application.retention_expires_at > datetime.now(UTC),
            Application.status != "withdrawn",
        )
        application_cursor_bindings = cursor_principal_bindings(principal) + (
            organization.id,
            job.id,
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="job_applications",
                bindings=application_cursor_bindings,
                detail="application cursor is malformed",
            )
            try:
                if payload["scope"] != "job_applications" or payload["job_id"] != job.id:
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="application cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    Application.created_at < created_at,
                    and_(Application.created_at == created_at, Application.id < row_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(Application.created_at.desc(), Application.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "job_applications",
                    "job_id": job.id,
                    "created_at": last.created_at.isoformat(),
                    "id": last.id,
                },
                scope="job_applications",
                bindings=application_cursor_bindings,
            )
        return ApplicationListResponse(
            applications=[
                application_response(application, job, organization) for application in page
            ],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/applications/{application_id}",
        response_model=ApplicationDetailResponse,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
    )
    async def get_my_application_detail(
        application_id: str,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ApplicationDetailResponse:
        require_application_human(principal)
        row = await session.scalar(
            select(Application).where(
                Application.id == application_id,
                Application.applicant_owner_id == principal.subject,
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="application was not found")
        if retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="application was not found")
        job = await session.get(Job, row.job_id)
        assert job is not None
        organization = await session.get(Organization, job.organization_id)
        assert organization is not None
        return application_detail_response(row, job, organization)

    @app.get(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}",
        response_model=ApplicationDetailResponse,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
    )
    async def get_job_application_detail(
        organization_slug: str,
        job_slug: str,
        application_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ApplicationDetailResponse:
        require_application_human(principal)
        organization = await organization_by_slug(session, organization_slug)
        job = await job_by_slug(session, organization, job_slug)
        await assert_active_employer_application_authority(session, organization, principal)
        if request.headers.get("X-Connectmd-Purpose") != "job_application_review":
            raise HTTPException(
                status_code=403,
                detail="X-Connectmd-Purpose: job_application_review is required to read application content",
            )
        row = await session.scalar(
            select(Application).where(
                Application.id == application_id, Application.job_id == job.id
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="application was not found")
        if retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="application was not found")
        if row.status == "withdrawn":
            raise HTTPException(
                status_code=404,
                detail="withdrawn application content is no longer available to the organization",
            )
        return application_detail_response(row, job, organization)

    async def employer_application_snapshot(
        organization_slug: str,
        job_slug: str,
        application_id: str,
        request: Request,
        principal: Principal,
        session: AsyncSession,
    ) -> tuple[Application, Job, Organization, str]:
        require_application_human(principal)
        organization = await organization_by_slug(session, organization_slug)
        job = await job_by_slug(session, organization, job_slug)
        await assert_active_employer_application_authority(session, organization, principal)
        if request.headers.get("X-Connectmd-Purpose") != "job_application_review":
            raise HTTPException(
                status_code=403,
                detail="X-Connectmd-Purpose: job_application_review is required to read application content",
            )
        row = await session.scalar(
            select(Application).where(
                Application.id == application_id, Application.job_id == job.id
            )
        )
        if row is None or row.status == "withdrawn" or retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="application snapshot was not found")
        return row, job, organization, read_application_snapshot(request, row)

    @app.get(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/snapshot",
        response_model=ApplicationSnapshotResponse,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
        responses={
            403: _error_response(
                "A signed-in organization reviewer and review purpose are required."
            ),
            404: _error_response("The application snapshot was not found."),
        },
    )
    async def get_job_application_snapshot(
        organization_slug: str,
        job_slug: str,
        application_id: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ApplicationSnapshotResponse | Response:
        row, job, organization, markdown = await employer_application_snapshot(
            organization_slug, job_slug, application_id, request, principal, session
        )
        headers = application_snapshot_headers(row)
        if _prefers_markdown(request.headers.get("accept", "")):
            headers["Vary"] = "Accept"
            return Response(markdown, media_type=MARKDOWN_MEDIA_TYPE, headers=headers)
        response.headers.update(headers)
        response.headers["Vary"] = "Accept"
        return application_snapshot_response(row, job, organization, markdown)

    @app.get(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/snapshot.md",
        response_class=Response,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
        responses={
            403: _error_response(
                "A signed-in organization reviewer and review purpose are required."
            ),
            404: _error_response("The application snapshot was not found."),
        },
    )
    async def get_job_application_snapshot_markdown(
        organization_slug: str,
        job_slug: str,
        application_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        row, _, _, markdown = await employer_application_snapshot(
            organization_slug, job_slug, application_id, request, principal, session
        )
        return Response(
            markdown,
            media_type=MARKDOWN_MEDIA_TYPE,
            headers=application_snapshot_headers(row),
        )

    @app.post(
        "/v1/applications/{application_id}/withdraw",
        response_model=ApplicationResponse,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response("Only the applicant Clerk human can withdraw an application."),
            404: _error_response("The application was not found."),
            409: _error_response(
                "The application or idempotency key conflicts with existing state."
            ),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("The application transition receipt is unavailable."),
        },
        openapi_extra={
            "x-connectmd-human-only": True,
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                }
            ],
        },
    )
    async def withdraw_application(
        application_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ApplicationResponse | Response:
        require_application_human(principal)
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/applications/{application_id}/withdraw"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(
            session,
            request,
            principal,
            key,
            operation,
            fingerprint,
            {"mode": "applicant", "application_id": application_id, "action": "withdraw"},
        )
        if replay is not None:
            return replay
        probe = await session.scalar(
            select(Application).where(
                Application.id == application_id,
                Application.applicant_owner_id == principal.subject,
            )
        )
        if probe is None:
            raise HTTPException(status_code=404, detail="application was not found")
        probe_job = await session.get(Job, probe.job_id)
        if probe_job is None:
            raise HTTPException(status_code=503, detail="application transition is unavailable")
        organization = await session.scalar(
            select(Organization)
            .where(Organization.id == probe_job.organization_id)
            .with_for_update()
        )
        if organization is None:
            raise HTTPException(status_code=503, detail="application transition is unavailable")
        job = await session.scalar(
            select(Job)
            .where(Job.id == probe_job.id, Job.organization_id == organization.id)
            .with_for_update()
        )
        if job is None:
            raise HTTPException(status_code=503, detail="application transition is unavailable")
        row = await session.scalar(
            select(Application)
            .where(
                Application.id == application_id,
                Application.job_id == job.id,
                Application.applicant_owner_id == principal.subject,
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(status_code=503, detail="application transition is unavailable")
        transition_context = {
            "mode": "applicant",
            "application_id": row.id,
            "job_id": job.id,
            "organization_id": organization.id,
            "action": "withdraw",
        }
        replay = await idempotency_replay(
            session,
            request,
            principal,
            key,
            operation,
            fingerprint,
            transition_context,
        )
        if replay is not None:
            return replay
        if row.status not in {"submitted", "under_review"}:
            raise HTTPException(status_code=409, detail="application cannot be withdrawn")
        if retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="application was not found")
        now = datetime.now(UTC)
        row.status = "withdrawn"
        row.decision_actor_id = principal.audit_actor_id
        row.updated_at = now
        row.decided_at = now
        result = application_response(row, job, organization)
        response_body = idempotency_replay_json(result)
        try:
            resource_id = _application_transition_resource_id(
                row, job, organization, "withdraw", response_body
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="application transition is unavailable"
            ) from exc
        for owner_id in {organization.owner_id, principal.subject}:
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type="application.withdrawn",
                    resource_type="application",
                    resource_id=row.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps({"status": row.status}, sort_keys=True),
                    occurred_at=now,
                )
            )
        try:
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=200,
                body="",
                headers={},
                resource_type="application_transition",
                resource_id=resource_id,
                application_context=transition_context,
            )
        except ConcurrentIdempotencyReplay as exc:
            return exc.response
        return Response(content=response_body, status_code=200, media_type="application/json")

    @app.post(
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/{action}",
        response_model=ApplicationResponse,
        tags=["applications"],
        include_in_schema=settings.recruiting_enabled,
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response("Only an authorized Clerk human can decide an application."),
            404: _error_response("The application or action was not found."),
            409: _error_response(
                "The application or idempotency key conflicts with existing state."
            ),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("The application transition receipt is unavailable."),
        },
        openapi_extra={
            "x-connectmd-human-only": True,
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                }
            ],
        },
    )
    async def decide_application(
        organization_slug: str,
        job_slug: str,
        application_id: str,
        action: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ApplicationResponse | Response:
        require_application_human(principal)
        if action not in {"review", "accept", "reject"}:
            raise HTTPException(status_code=404, detail="application action was not found")
        if action == "accept":
            require_recruiting_release()
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/applications/{application_id}/{action}"
        fingerprint = _request_fingerprint(
            operation,
            json.dumps(
                {"job_slug": job_slug, "organization_slug": organization_slug},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        organization = await organization_by_slug(session, organization_slug, for_update=True)
        job = await job_by_slug(session, organization, job_slug, for_update=True)
        await assert_active_employer_application_authority(session, organization, principal)
        transition_context = {
            "mode": "employer",
            "application_id": application_id,
            "job_id": job.id,
            "organization_id": organization.id,
            "action": action,
        }
        replay = await idempotency_replay(
            session,
            request,
            principal,
            key,
            operation,
            fingerprint,
            transition_context,
        )
        if replay is not None:
            return replay
        row = await session.scalar(
            select(Application)
            .where(Application.id == application_id, Application.job_id == job.id)
            .with_for_update()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="application was not found")
        transition_context["application_id"] = row.id
        replay = await idempotency_replay(
            session,
            request,
            principal,
            key,
            operation,
            fingerprint,
            transition_context,
        )
        if replay is not None:
            return replay
        if retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="application was not found")
        if action == "review" and row.status != "submitted":
            raise HTTPException(
                status_code=409, detail="only submitted applications can enter review"
            )
        if action in {"accept", "reject"} and row.status not in {"submitted", "under_review"}:
            raise HTTPException(status_code=409, detail="application is already decided")
        now = datetime.now(UTC)
        row.status = {"review": "under_review", "accept": "accepted", "reject": "rejected"}[action]
        row.decision_actor_id = principal.audit_actor_id
        row.updated_at = now
        if action in {"accept", "reject"}:
            row.decided_at = now
        result = application_response(row, job, organization)
        response_body = idempotency_replay_json(result)
        try:
            resource_id = _application_transition_resource_id(
                row, job, organization, action, response_body
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="application transition is unavailable"
            ) from exc
        for owner_id in {organization.owner_id, row.applicant_owner_id}:
            session.add(
                ChangeEvent(
                    owner_id=owner_id,
                    event_type=f"application.{row.status}",
                    resource_type="application",
                    resource_id=row.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=principal.grant_id,
                    payload=json.dumps({"status": row.status}, sort_keys=True),
                    occurred_at=now,
                )
            )
        add_notification(
            session,
            recipient_owner_id=row.applicant_owner_id,
            type=f"application.{row.status}",
            actor_owner_id=None,
            resource_type="application",
            resource_id=row.id,
            now=now,
        )
        try:
            await store_idempotency(
                session,
                request,
                principal,
                key=key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=200,
                body="",
                headers={},
                resource_type="application_transition",
                resource_id=resource_id,
                application_context=transition_context,
            )
        except ConcurrentIdempotencyReplay as exc:
            return exc.response
        return Response(content=response_body, status_code=200, media_type="application/json")

    @app.post(
        "/v1/connection-requests",
        response_model=ConnectionRequestResponse,
        status_code=201,
        tags=["connections"],
        openapi_extra=_social_openapi_extra(),
    )
    async def create_connection_request(
        body: ConnectionRequestCreateRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ConnectionRequestResponse | Response:
        require_social_human(principal, "connections:write")
        key = idempotency_key(request, required=True)
        operation = "POST:/v1/connection-requests"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        target = await session.scalar(
            select(Document).where(
                Document.kind == "profile",
                Document.public_identifier == body.recipient_profile_handle,
                Document.visibility == "public",
            )
        )
        if target is None:
            raise HTTPException(status_code=404, detail="connection target was not found")
        requester_profile = await session.scalar(
            select(Document)
            .where(
                Document.kind == "profile",
                Document.owner_id == principal.subject,
                Document.visibility == "public",
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
        )
        if requester_profile is None:
            raise HTTPException(
                status_code=409, detail="a public profile is required to request a connection"
            )
        pair_low, pair_high = owner_pair(principal.subject, target.owner_id)
        if await connection_blocked(session, principal.subject, target.owner_id):
            raise HTTPException(status_code=404, detail="connection target was not found")
        now = datetime.now(UTC)
        existing = await session.scalar(
            select(ConnectionRequest).where(
                ConnectionRequest.pair_owner_low == pair_low,
                ConnectionRequest.pair_owner_high == pair_high,
                ConnectionRequest.status.in_(("pending", "accepted")),
                ConnectionRequest.retention_expires_at > now,
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=409, detail="an active connection request already exists"
            )
        expired_request = await session.scalar(
            select(ConnectionRequest)
            .where(
                ConnectionRequest.pair_owner_low == pair_low,
                ConnectionRequest.pair_owner_high == pair_high,
                ConnectionRequest.status.in_(("pending", "accepted")),
                ConnectionRequest.retention_expires_at <= now,
            )
            .with_for_update()
        )
        if expired_request is not None:
            expired_request.status = "rejected"
            expired_request.decision_actor_id = "system:retention"
            expired_request.decided_at = now
            expired_request.updated_at = now
        quota_values = {
            "requester_owner_id": principal.subject,
            "bucket_date": now.date(),
            "request_count": 1,
            "updated_at": now,
        }
        dialect_name = session.get_bind().dialect.name
        quota_insert: Any
        if dialect_name == "postgresql":
            quota_insert = postgresql_insert(ConnectionRequestRateBucket).values(**quota_values)
        elif dialect_name == "sqlite":
            quota_insert = sqlite_insert(ConnectionRequestRateBucket).values(**quota_values)
        else:  # pragma: no cover - the locked stack and tests use PostgreSQL/SQLite
            raise HTTPException(
                status_code=503, detail="connection request quota backend is unsupported"
            )
        consumed = await session.scalar(
            quota_insert.on_conflict_do_update(
                index_elements=["requester_owner_id", "bucket_date"],
                set_={
                    "request_count": ConnectionRequestRateBucket.request_count + 1,
                    "updated_at": now,
                },
                where=ConnectionRequestRateBucket.request_count < 20,
            ).returning(ConnectionRequestRateBucket.request_count)
        )
        if consumed is None:
            raise HTTPException(
                status_code=429,
                detail="connection request daily limit reached",
                headers={"Retry-After": "86400"},
            )
        row = ConnectionRequest(
            id=new_id(),
            pair_owner_low=pair_low,
            pair_owner_high=pair_high,
            requester_owner_id=principal.subject,
            recipient_owner_id=target.owner_id,
            requester_profile_handle=requester_profile.public_identifier,
            recipient_profile_handle=target.public_identifier,
            requested_messaging=body.messaging_requested,
            status="pending",
            requester_actor_id=principal.audit_actor_id,
            requester_actor_method=principal.method,
            created_at=now,
            updated_at=now,
            retention_expires_at=social_retention_expires_at(now),
        )
        result = connection_request_response(row, principal)
        session.add(row)
        add_notification(
            session,
            recipient_owner_id=target.owner_id,
            type="connection_request.received",
            actor_owner_id=principal.subject,
            resource_type="connection_request",
            resource_id=row.id,
            now=now,
        )
        add_social_change_event(
            session,
            owner_id=principal.subject,
            event_type="connection_request.created",
            resource_type="connection_request",
            resource_id=row.id,
            principal=principal,
            payload={"messaging_requested": row.requested_messaging, "status": row.status},
            now=now,
        )
        add_social_change_event(
            session,
            owner_id=target.owner_id,
            event_type="connection_request.received",
            resource_type="connection_request",
            resource_id=row.id,
            principal=principal,
            payload={"messaging_requested": row.requested_messaging, "status": row.status},
            now=now,
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body=result.model_dump_json(),
            headers={},
            resource_type="connection_request",
            resource_id=row.id,
        )
        return result

    @app.get(
        "/v1/connection-requests/inbox",
        response_model=ConnectionRequestListResponse,
        tags=["connections"],
    )
    async def connection_request_inbox(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ConnectionRequestListResponse:
        require_social_human(principal, "connections:read")
        connection_request_cursor_bindings = cursor_principal_bindings(principal)
        statement = select(ConnectionRequest).where(
            ConnectionRequest.recipient_owner_id == principal.subject,
            ConnectionRequest.status == "pending",
            ConnectionRequest.retention_expires_at > datetime.now(UTC),
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="connection_request_inbox",
                bindings=connection_request_cursor_bindings,
                detail="connection request cursor is malformed",
            )
            try:
                if payload["scope"] != "connection_request_inbox" or payload["v"] != 1:
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="connection request cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    ConnectionRequest.created_at < created_at,
                    and_(
                        ConnectionRequest.created_at == created_at,
                        ConnectionRequest.id < row_id,
                    ),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(
                    ConnectionRequest.created_at.desc(), ConnectionRequest.id.desc()
                ).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "connection_request_inbox",
                    "created_at": last.created_at.isoformat(),
                    "id": last.id,
                },
                scope="connection_request_inbox",
                bindings=connection_request_cursor_bindings,
            )
        return ConnectionRequestListResponse(
            requests=[connection_request_response(row, principal) for row in page],
            next_cursor=next_cursor,
        )

    @app.post(
        "/v1/connection-requests/{connection_request_id}/{action}",
        response_model=ConnectionRequestResponse,
        tags=["connections"],
        openapi_extra=_social_openapi_extra(),
    )
    async def decide_connection_request(
        connection_request_id: str,
        action: str,
        request: Request,
        body: ConnectionRequestDecisionRequest | None = None,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ConnectionRequestResponse | Response:
        if action not in {"accept", "reject", "block"}:
            raise HTTPException(status_code=404, detail="connection request action was not found")
        require_social_human(principal, "connections:write")
        row = await session.scalar(
            select(ConnectionRequest)
            .where(
                ConnectionRequest.id == connection_request_id,
                ConnectionRequest.recipient_owner_id == principal.subject,
            )
            .with_for_update()
        )
        if row is None or retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="connection request was not found")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/connection-requests/{row.id}/{action}"
        fingerprint = _request_fingerprint(
            operation, body.model_dump_json() if body is not None else ""
        )
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        if row.status != "pending":
            raise HTTPException(status_code=409, detail="connection request is already decided")
        if action == "accept" and (body is None or body.messaging_consent is None):
            raise HTTPException(
                status_code=422,
                detail="accepting a connection request requires explicit messaging_consent",
            )
        if action != "accept" and body is not None and body.messaging_consent is not None:
            raise HTTPException(
                status_code=422,
                detail="messaging_consent is valid only when accepting a connection request",
            )
        now = datetime.now(UTC)
        row.status = {"accept": "accepted", "reject": "rejected", "block": "blocked"}[action]
        row.decision_actor_id = principal.audit_actor_id
        row.decided_at = now
        row.updated_at = now
        if action == "accept":
            row.recipient_messaging_consent = cast(bool, body.messaging_consent if body else False)
            expired_connection = await session.scalar(
                select(Connection)
                .where(
                    Connection.pair_owner_low == row.pair_owner_low,
                    Connection.pair_owner_high == row.pair_owner_high,
                    Connection.status == "active",
                )
                .with_for_update()
            )
            if expired_connection is not None:
                if not retention_expired(expired_connection.retention_expires_at, now):
                    raise HTTPException(
                        status_code=409, detail="an active connection already exists"
                    )
                expired_connection.status = "removed"
                expired_connection.ended_at = now
                expired_connection.ended_by_owner_id = "system:retention"
                expired_connection.updated_at = now
                expired_conversation = await session.scalar(
                    select(Conversation)
                    .where(
                        Conversation.connection_id == expired_connection.id,
                        Conversation.status == "active",
                    )
                    .with_for_update()
                )
                if expired_conversation is not None:
                    expired_conversation.status = "closed"
                    expired_conversation.closed_at = now
            connection = Connection(
                id=new_id(),
                connection_request_id=row.id,
                pair_owner_low=row.pair_owner_low,
                pair_owner_high=row.pair_owner_high,
                requester_owner_id=row.requester_owner_id,
                recipient_owner_id=row.recipient_owner_id,
                requester_profile_handle=row.requester_profile_handle,
                recipient_profile_handle=row.recipient_profile_handle,
                requested_messaging=row.requested_messaging,
                recipient_messaging_consent=cast(bool, row.recipient_messaging_consent),
                messaging_enabled=(
                    row.requested_messaging and cast(bool, row.recipient_messaging_consent)
                ),
                status="active",
                created_at=now,
                updated_at=now,
                retention_expires_at=social_retention_expires_at(now),
            )
            session.add(connection)
            add_notification(
                session,
                recipient_owner_id=row.requester_owner_id,
                type="connection_request.accepted",
                actor_owner_id=principal.subject,
                resource_type="connection",
                resource_id=connection.id,
                now=now,
            )
        elif action == "reject":
            add_notification(
                session,
                recipient_owner_id=row.requester_owner_id,
                type="connection_request.rejected",
                actor_owner_id=principal.subject,
                resource_type="connection_request",
                resource_id=row.id,
                now=now,
            )
        else:
            existing_block = await session.scalar(
                select(ConnectionBlock).where(
                    ConnectionBlock.blocker_owner_id == principal.subject,
                    ConnectionBlock.blocked_owner_id == row.requester_owner_id,
                )
            )
            if existing_block is None:
                session.add(
                    ConnectionBlock(
                        id=new_id(),
                        blocker_owner_id=principal.subject,
                        blocked_owner_id=row.requester_owner_id,
                        created_at=now,
                    )
                )
        add_social_change_event(
            session,
            owner_id=principal.subject,
            event_type=f"connection_request.{row.status}",
            resource_type="connection_request",
            resource_id=row.id,
            principal=principal,
            payload={
                "messaging_enabled": row.requested_messaging
                and bool(row.recipient_messaging_consent)
            },
            now=now,
        )
        if action in {"accept", "reject"}:
            add_social_change_event(
                session,
                owner_id=row.requester_owner_id,
                event_type=f"connection_request.{row.status}",
                resource_type="connection_request",
                resource_id=row.id,
                principal=principal,
                payload={
                    "messaging_enabled": row.requested_messaging
                    and bool(row.recipient_messaging_consent)
                },
                now=now,
            )
        result = connection_request_response(row, principal)
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body=result.model_dump_json(),
            headers={},
            resource_type="connection_request",
            resource_id=row.id,
        )
        return result

    @app.get("/v1/connections", response_model=ConnectionListResponse, tags=["connections"])
    async def list_connections(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ConnectionListResponse:
        require_social_human(principal, "connections:read")
        connection_cursor_bindings = cursor_principal_bindings(principal)
        statement = select(Connection).where(
            Connection.status == "active",
            Connection.retention_expires_at > datetime.now(UTC),
            or_(
                Connection.pair_owner_low == principal.subject,
                Connection.pair_owner_high == principal.subject,
            ),
            connection_pair_is_not_blocked(Connection.pair_owner_low, Connection.pair_owner_high),
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="connections",
                bindings=connection_cursor_bindings,
                detail="connection cursor is malformed",
            )
            try:
                if payload["scope"] != "connections" or payload["v"] != 1:
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="connection cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    Connection.created_at < created_at,
                    and_(Connection.created_at == created_at, Connection.id < row_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(Connection.created_at.desc(), Connection.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "connections",
                    "created_at": last.created_at.isoformat(),
                    "id": last.id,
                },
                scope="connections",
                bindings=connection_cursor_bindings,
            )
        return ConnectionListResponse(
            connections=[connection_response(row, principal) for row in page],
            next_cursor=next_cursor,
        )

    @app.delete(
        "/v1/connections/{connection_id}",
        status_code=204,
        tags=["connections"],
        openapi_extra=_social_openapi_extra(),
    )
    async def remove_connection(
        connection_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_social_human(principal, "connections:write")
        key = idempotency_key(request, required=True)
        operation = f"DELETE:/v1/connections/{connection_id}"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        row = await active_connection_for_participant(
            session, connection_id, principal, for_update=True
        )
        now = datetime.now(UTC)
        row.status = "removed"
        row.ended_at = now
        row.ended_by_owner_id = principal.subject
        row.updated_at = now
        conversation = await session.scalar(
            select(Conversation).where(Conversation.connection_id == row.id).with_for_update()
        )
        if conversation is not None and conversation.status == "active":
            conversation.status = "closed"
            conversation.closed_at = now
        add_social_change_event(
            session,
            owner_id=principal.subject,
            event_type="connection.removed",
            resource_type="connection",
            resource_id=row.id,
            principal=principal,
            payload={"status": row.status},
            now=now,
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=204,
            body="",
            headers={},
            resource_type="connection",
            resource_id=row.id,
        )
        return Response(status_code=204)

    @app.post(
        "/v1/connections/{connection_id}/block",
        status_code=204,
        tags=["connections"],
        openapi_extra=_social_openapi_extra(),
    )
    async def block_connection(
        connection_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        require_social_human(principal, "connections:write")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/connections/{connection_id}/block"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        row = await active_connection_for_participant(
            session, connection_id, principal, for_update=True
        )
        now = datetime.now(UTC)
        counterpart = connection_counterparty(row, principal)
        existing_block = await session.scalar(
            select(ConnectionBlock).where(
                ConnectionBlock.blocker_owner_id == principal.subject,
                ConnectionBlock.blocked_owner_id == counterpart,
            )
        )
        if existing_block is None:
            session.add(
                ConnectionBlock(
                    id=new_id(),
                    blocker_owner_id=principal.subject,
                    blocked_owner_id=counterpart,
                    created_at=now,
                )
            )
        row.status = "blocked"
        row.ended_at = now
        row.ended_by_owner_id = principal.subject
        row.updated_at = now
        accepted_request = await session.get(ConnectionRequest, row.connection_request_id)
        if accepted_request is not None:
            accepted_request.status = "blocked"
            accepted_request.updated_at = now
            accepted_request.decided_at = now
        conversation = await session.scalar(
            select(Conversation).where(Conversation.connection_id == row.id).with_for_update()
        )
        if conversation is not None and conversation.status == "active":
            conversation.status = "blocked"
            conversation.closed_at = now
        add_social_change_event(
            session,
            owner_id=principal.subject,
            event_type="connection.blocked",
            resource_type="connection",
            resource_id=row.id,
            principal=principal,
            payload={"status": row.status},
            now=now,
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=204,
            body="",
            headers={},
            resource_type="connection",
            resource_id=row.id,
        )
        return Response(status_code=204)

    @app.post(
        "/v1/conversations",
        response_model=ConversationResponse,
        status_code=201,
        tags=["conversations"],
        openapi_extra=_social_openapi_extra(),
    )
    async def create_conversation(
        body: ConversationCreateRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ConversationResponse | Response:
        require_social_human(principal, "conversations:write")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/conversations/{body.connection_id}"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        connection = await active_connection_for_participant(
            session, body.connection_id, principal, for_update=True
        )
        if not connection.messaging_enabled:
            raise HTTPException(status_code=409, detail="bilateral messaging consent is required")
        await lock_social_admission(session, connection)
        existing = await session.scalar(
            select(Conversation)
            .where(Conversation.connection_id == connection.id)
            .with_for_update()
        )
        if existing is not None:
            raise HTTPException(
                status_code=409, detail="conversation already exists for this connection"
            )
        now = datetime.now(UTC)
        row = Conversation(
            id=new_id(),
            connection_id=connection.id,
            pair_owner_low=connection.pair_owner_low,
            pair_owner_high=connection.pair_owner_high,
            status="active",
            created_by_owner_id=principal.subject,
            created_at=now,
            retention_expires_at=social_retention_expires_at(now),
        )
        result = conversation_response(row, connection, principal)
        session.add(row)
        add_notification(
            session,
            recipient_owner_id=connection_counterparty(connection, principal),
            type="conversation.created",
            actor_owner_id=principal.subject,
            resource_type="conversation",
            resource_id=row.id,
            now=now,
        )
        add_social_change_event(
            session,
            owner_id=principal.subject,
            event_type="conversation.created",
            resource_type="conversation",
            resource_id=row.id,
            principal=principal,
            payload={"connection_id": connection.id},
            now=now,
        )
        add_social_change_event(
            session,
            owner_id=connection_counterparty(connection, principal),
            event_type="conversation.created",
            resource_type="conversation",
            resource_id=row.id,
            principal=principal,
            payload={"connection_id": connection.id},
            now=now,
        )
        location = f"/v1/conversations/{row.id}/messages"
        response.headers["Location"] = location
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body=result.model_dump_json(),
            headers={"Location": location},
            resource_type="conversation",
            resource_id=row.id,
        )
        return result

    @app.get("/v1/conversations", response_model=ConversationListResponse, tags=["conversations"])
    async def list_conversations(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> ConversationListResponse:
        require_social_human(principal, "conversations:read")
        conversation_cursor_bindings = cursor_principal_bindings(principal)
        statement = (
            select(Conversation, Connection)
            .join(Connection, Conversation.connection_id == Connection.id)
            .where(
                Conversation.status == "active",
                Conversation.retention_expires_at > datetime.now(UTC),
                Connection.status == "active",
                Connection.messaging_enabled.is_(True),
                or_(
                    Conversation.pair_owner_low == principal.subject,
                    Conversation.pair_owner_high == principal.subject,
                ),
                connection_pair_is_not_blocked(
                    Conversation.pair_owner_low, Conversation.pair_owner_high
                ),
            )
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="conversations",
                bindings=conversation_cursor_bindings,
                detail="conversation cursor is malformed",
            )
            try:
                if payload["scope"] != "conversations" or payload["v"] != 1:
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="conversation cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    Conversation.created_at < created_at,
                    and_(Conversation.created_at == created_at, Conversation.id < row_id),
                )
            )
        rows = (
            await session.execute(
                statement.order_by(Conversation.created_at.desc(), Conversation.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last_conversation, _ = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "conversations",
                    "created_at": last_conversation.created_at.isoformat(),
                    "id": last_conversation.id,
                },
                scope="conversations",
                bindings=conversation_cursor_bindings,
            )
        return ConversationListResponse(
            conversations=[
                conversation_response(row, connection, principal) for row, connection in page
            ],
            next_cursor=next_cursor,
        )

    @app.post(
        "/v1/conversations/{conversation_id}/messages",
        response_model=MessageSendResponse,
        status_code=201,
        tags=["conversations"],
        openapi_extra=_social_openapi_extra(),
    )
    async def send_message(
        conversation_id: str,
        body: MessageCreateRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> MessageSendResponse | Response:
        require_social_human(principal, "conversations:write")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/conversations/{conversation_id}/messages"
        fingerprint = _request_fingerprint(operation, body.model_dump_json())
        conversation, connection = await active_conversation_for_participant(
            session, conversation_id, principal, for_update=True
        )
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        await lock_social_admission(session, connection, conversation)
        now = datetime.now(UTC)
        quota_values = {
            "sender_owner_id": principal.subject,
            "bucket_date": now.date(),
            "message_count": 1,
            "updated_at": now,
        }
        dialect_name = session.get_bind().dialect.name
        quota_insert: Any
        if dialect_name == "postgresql":
            quota_insert = postgresql_insert(MessageRateBucket).values(**quota_values)
        elif dialect_name == "sqlite":
            quota_insert = sqlite_insert(MessageRateBucket).values(**quota_values)
        else:  # pragma: no cover - the locked stack and tests use PostgreSQL/SQLite
            raise HTTPException(status_code=503, detail="message quota backend is unsupported")
        consumed = await session.scalar(
            quota_insert.on_conflict_do_update(
                index_elements=["sender_owner_id", "bucket_date"],
                set_={
                    "message_count": MessageRateBucket.message_count + 1,
                    "updated_at": now,
                },
                where=MessageRateBucket.message_count < 100,
            ).returning(MessageRateBucket.message_count)
        )
        if consumed is None:
            raise HTTPException(
                status_code=429,
                detail="message daily limit reached",
                headers={"Retry-After": "86400"},
            )
        row = Message(
            id=new_id(),
            conversation_id=conversation.id,
            sender_owner_id=principal.subject,
            sender_actor_id=principal.audit_actor_id,
            sender_actor_method=principal.method,
            markdown=body.markdown,
            content_sha256=sha256(body.markdown.encode()).hexdigest(),
            status="active",
            created_at=now,
            retention_expires_at=social_retention_expires_at(now),
        )
        result = message_send_response(row)
        session.add(row)
        add_notification(
            session,
            recipient_owner_id=connection_counterparty(connection, principal),
            type="message.received",
            actor_owner_id=principal.subject,
            resource_type="conversation",
            resource_id=conversation.id,
            now=now,
        )
        add_social_change_event(
            session,
            owner_id=principal.subject,
            event_type="message.sent",
            resource_type="message",
            resource_id=row.id,
            principal=principal,
            payload={"conversation_id": conversation.id},
            now=now,
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body=result.model_dump_json(),
            headers={},
            resource_type="message",
            resource_id=row.id,
        )
        return result

    @app.get(
        "/v1/conversations/{conversation_id}/messages",
        response_model=MessageListResponse,
        tags=["conversations"],
    )
    async def list_messages(
        conversation_id: str,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> MessageListResponse:
        require_social_human(principal, "conversations:read")
        conversation, _ = await active_conversation_for_participant(
            session, conversation_id, principal
        )
        message_cursor_bindings = cursor_principal_bindings(principal) + (conversation.id,)
        statement = select(Message).where(
            Message.conversation_id == conversation.id,
            Message.status == "active",
            Message.retention_expires_at > datetime.now(UTC),
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="messages",
                bindings=message_cursor_bindings,
                detail="message cursor is malformed",
            )
            try:
                if (
                    payload["scope"] != "messages"
                    or payload["conversation_id"] != conversation.id
                    or payload["v"] != 1
                ):
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="message cursor is malformed") from exc
            statement = statement.where(
                or_(
                    Message.created_at > created_at,
                    and_(Message.created_at == created_at, Message.id > row_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(Message.created_at.asc(), Message.id.asc()).limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "messages",
                    "conversation_id": conversation.id,
                    "created_at": last.created_at.isoformat(),
                    "id": last.id,
                },
                scope="messages",
                bindings=message_cursor_bindings,
            )
        return MessageListResponse(
            messages=[message_response(row, principal) for row in page], next_cursor=next_cursor
        )

    @app.get("/v1/notifications", response_model=NotificationListResponse, tags=["notifications"])
    async def list_notifications(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> NotificationListResponse:
        require_social_human(principal, "notifications:read")
        notification_cursor_bindings = cursor_principal_bindings(principal)
        statement = select(Notification).where(
            Notification.recipient_owner_id == principal.subject,
            Notification.retention_expires_at > datetime.now(UTC),
        )
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="notifications",
                bindings=notification_cursor_bindings,
                detail="notification cursor is malformed",
            )
            try:
                if payload["scope"] != "notifications" or payload["v"] != 1:
                    raise ValueError
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                row_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="notification cursor is malformed"
                ) from exc
            statement = statement.where(
                or_(
                    Notification.created_at < created_at,
                    and_(Notification.created_at == created_at, Notification.id < row_id),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {
                    "v": 1,
                    "scope": "notifications",
                    "created_at": last.created_at.isoformat(),
                    "id": last.id,
                },
                scope="notifications",
                bindings=notification_cursor_bindings,
            )
        return NotificationListResponse(
            notifications=[notification_response(row) for row in page], next_cursor=next_cursor
        )

    @app.post(
        "/v1/notifications/{notification_id}/read",
        response_model=NotificationResponse,
        tags=["notifications"],
        openapi_extra=_social_openapi_extra(),
    )
    async def mark_notification_read(
        notification_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> NotificationResponse | Response:
        require_social_human(principal, "notifications:write")
        row = await session.scalar(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient_owner_id == principal.subject,
            )
            .with_for_update()
        )
        if row is None or retention_expired(row.retention_expires_at):
            raise HTTPException(status_code=404, detail="notification was not found")
        key = idempotency_key(request, required=True)
        operation = f"POST:/v1/notifications/{row.id}/read"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        now = datetime.now(UTC)
        if row.read_at is None:
            row.read_at = now
        result = notification_response(row)
        add_social_change_event(
            session,
            owner_id=principal.subject,
            event_type="notification.read",
            resource_type="notification",
            resource_id=row.id,
            principal=principal,
            payload={"type": row.type},
            now=now,
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=200,
            body=result.model_dump_json(),
            headers={},
            resource_type="notification",
            resource_id=row.id,
        )
        return result

    def proposal_response(row: AgentProposal) -> AgentProposalResponse:
        return AgentProposalResponse(
            id=row.id,
            document_id=row.document_id,
            kind=cast(Any, row.document_kind),
            identifier=row.document_identifier,
            markdown=row.markdown,
            if_match=row.if_match,
            status=cast(Any, row.status),
            submitter_actor_id=row.submitter_actor_id,
            submitter_grant_id=row.submitter_grant_id,
            created_at=row.created_at,
            decided_at=row.decided_at,
        )

    async def _submit_proposal_write(
        body: AgentProposalCreateRequest,
        request: Request,
        principal: Principal,
        session: AsyncSession,
        *,
        key: str | None = None,
        operation: str = "POST:/v1/proposals",
    ) -> AgentProposalResponse | Response:
        if principal.method != "agent_grant":
            raise HTTPException(status_code=403, detail="an agent grant is required")
        if not ({"proposals:write", "documents:write"} & principal.scopes):
            raise HTTPException(status_code=403, detail="agent grant lacks proposal scope")
        request_key = idempotency_key(request, required=True) if key is None else key
        assert request_key is not None
        if key is not None and not _IDEMPOTENCY_KEY_RE.fullmatch(key):
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must contain 1-128 visible ASCII characters",
            )
        fingerprint = _request_fingerprint(operation, body.model_dump_json(), body.if_match)
        document_service = service(session, request)
        document = await document_service.get(body.kind, body.identifier)
        if document.owner_id != principal.subject:
            raise HTTPException(status_code=404, detail="document was not found")
        assert_document_resource(principal, document)
        replay = await idempotency_replay(
            session, request, principal, request_key, operation, fingerprint
        )
        if replay is not None:
            return replay
        current = current_version(document)
        if not if_match_satisfied(body.if_match, strong_etag(current.sha256)):
            raise HTTPException(status_code=412, detail="proposal If-Match is stale")
        # Validate now so the owner's inbox cannot be filled with unusable payloads.
        now = datetime.now(UTC)
        try:
            current_markdown = document_service.read_markdown(current)
            current_frontmatter, _ = validate_canonical(body.kind, current_markdown)
            _, proposed_frontmatter = prepare_client_document(
                body.kind,
                body.markdown,
                document_id=document.id,
                owner_id=public_owner_id(principal.subject),
                version=document.current_version + 1,
                updated_at=now,
                expected_server_fields=current_frontmatter,
            )
            identity_field = "handle" if body.kind == "profile" else "slug"
            if proposed_frontmatter[identity_field] != document.public_identifier:
                raise MarkdownValidationError(f"{body.kind} {identity_field} is immutable")
        except MarkdownSizeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except MarkdownVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MarkdownValidationError as exc:
            raise HTTPException(status_code=422, detail=PUBLIC_MARKDOWN_VALIDATION_DETAIL) from exc
        row = AgentProposal(
            id=new_id(),
            owner_id=principal.subject,
            submitter_actor_id=principal.audit_actor_id,
            submitter_grant_id=cast(str, principal.grant_id),
            document_id=document.id,
            document_kind=document.kind,
            document_identifier=document.public_identifier,
            markdown=body.markdown,
            if_match=body.if_match,
            status="pending",
            created_at=now,
        )
        result = proposal_response(row)
        session.add(row)
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type="proposal.submitted",
                resource_type="proposal",
                resource_id=row.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=principal.grant_id,
                payload=json.dumps(
                    {"document_id": document.id, "status": "pending"}, sort_keys=True
                ),
                occurred_at=now,
            )
        )
        session.add(
            IdempotencyRecord(
                owner_id=principal.subject,
                idempotency_key=request_key,
                operation=operation,
                request_hash=fingerprint,
                response_status=201,
                response_body="",
                response_headers="{}",
                resource_type="proposal",
                resource_id=row.id,
                created_at=now,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            replay = await idempotency_replay(
                session, request, principal, request_key, operation, fingerprint
            )
            if replay is not None:
                return replay
            raise HTTPException(status_code=409, detail="proposal conflicted") from exc
        return result

    @app.post(
        "/v1/proposals",
        response_model=AgentProposalResponse,
        status_code=201,
        tags=["agent-grants"],
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            428: _error_response("Idempotency-Key is required."),
        },
        openapi_extra={"parameters": [_idempotency_openapi_parameter()]},
    )
    async def submit_proposal(
        body: AgentProposalCreateRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> AgentProposalResponse | Response:
        return await _submit_proposal_write(body, request, principal, session)

    @app.get(
        "/v1/proposals",
        response_model=AgentProposalListResponse,
        tags=["agent-grants"],
    )
    async def list_proposals(
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> AgentProposalListResponse:
        if principal.method != "clerk_jwt":
            raise HTTPException(status_code=403, detail="only the owner can review proposals")
        proposal_cursor_bindings = cursor_principal_bindings(principal)
        statement = select(AgentProposal).where(AgentProposal.owner_id == principal.subject)
        if cursor:
            payload = generic_cursor_decode(
                cursor,
                scope="proposals",
                bindings=proposal_cursor_bindings,
                detail="proposal cursor is malformed",
            )
            try:
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                proposal_id = str(payload["id"])
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="proposal cursor is malformed") from exc
            statement = statement.where(
                or_(
                    AgentProposal.created_at < created_at,
                    and_(
                        AgentProposal.created_at == created_at,
                        AgentProposal.id < proposal_id,
                    ),
                )
            )
        rows = (
            await session.scalars(
                statement.order_by(AgentProposal.created_at.desc(), AgentProposal.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = generic_cursor_encode(
                {"v": 1, "created_at": last.created_at.isoformat(), "id": last.id},
                scope="proposals",
                bindings=proposal_cursor_bindings,
            )
        return AgentProposalListResponse(
            proposals=[proposal_response(row) for row in page], next_cursor=next_cursor
        )

    @app.post(
        "/v1/proposals/{proposal_id}/{action}",
        response_model=AgentProposalResponse,
        tags=["agent-grants"],
        responses={
            403: _error_response("Only the owner can decide a proposal."),
            404: _error_response("The proposal or action was not found."),
            409: _error_response("The proposal is already decided or the key conflicts."),
            412: _error_response("The proposal's document version is stale."),
            422: _error_response("The proposed Markdown failed canonical validation."),
            428: _error_response("Idempotency-Key is required."),
            413: _error_response(
                f"Canonical Profile/Resume Markdown exceeds {canonical_document_max_utf8_bytes()} UTF-8 bytes."
            ),
            503: _error_response("The accepted document or its durable receipt is unavailable."),
        },
        openapi_extra={
            "x-connectmd-canonical-max-utf8-bytes": canonical_document_max_utf8_bytes(),
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_PATTERN,
                    },
                }
            ],
        },
    )
    async def decide_proposal(
        proposal_id: str,
        action: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
        session: AsyncSession = Depends(get_session),
    ) -> AgentProposalResponse | Response:
        assert_not_impersonated_clerk(principal)
        if action not in {"accept", "reject"}:
            raise HTTPException(status_code=404, detail="proposal action was not found")
        if principal.method != "clerk_jwt":
            raise HTTPException(status_code=403, detail="only the owner can decide proposals")
        request_key = idempotency_key(request, required=True)
        assert request_key is not None
        operation = f"POST:/v1/proposals/{proposal_id}/{action}"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(
            session, request, principal, request_key, operation, fingerprint
        )
        if replay is not None:
            return replay
        row = await session.scalar(
            select(AgentProposal)
            .where(
                AgentProposal.id == proposal_id,
                AgentProposal.owner_id == principal.subject,
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="proposal was not found")
        replay = await idempotency_replay(
            session, request, principal, request_key, operation, fingerprint
        )
        if replay is not None:
            return replay
        if row.status != "pending":
            raise HTTPException(status_code=409, detail="proposal is already decided")
        decision_receipt: IdempotencyRecord | None = None
        if action == "accept":
            decision_receipt = IdempotencyRecord(
                owner_id=principal.subject,
                idempotency_key=request_key,
                operation=operation,
                request_hash=fingerprint,
                response_status=200,
                response_body="",
                response_headers="{}",
                resource_type="proposal_decision",
                resource_id=f"{proposal_id}:{action}",
            )
            session.add(decision_receipt)
        now = datetime.now(UTC)
        row.status = "accepted" if action == "accept" else "rejected"
        row.decision_actor_id = principal.audit_actor_id
        row.decided_at = now
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type=f"proposal.{row.status}",
                resource_type="proposal",
                resource_id=row.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=row.submitter_grant_id,
                payload=json.dumps(
                    {"document_id": row.document_id, "status": row.status}, sort_keys=True
                ),
                occurred_at=now,
            )
        )
        if action == "accept":
            assert decision_receipt is not None
            try:
                document = await service(session, request).update(
                    row.document_kind,
                    row.document_identifier,
                    row.markdown,
                    principal.subject,
                    if_match=row.if_match,
                    actor_id=principal.audit_actor_id,
                    actor_method="proposal_accept",
                    grant_id=row.submitter_grant_id,
                    resource_id=row.document_id,
                    idempotency_record=decision_receipt,
                )
            except DocumentPreconditionError as exc:
                await session.rollback()
                replay = await idempotency_replay(
                    session, request, principal, request_key, operation, fingerprint
                )
                if replay is not None:
                    return replay
                raise HTTPException(status_code=412, detail="proposal is stale") from exc
            except MarkdownSizeError as exc:
                await session.rollback()
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            except MarkdownValidationError as exc:
                await session.rollback()
                raise HTTPException(
                    status_code=422, detail=PUBLIC_MARKDOWN_VALIDATION_DETAIL
                ) from exc
            except DocumentNotFoundError as exc:
                await session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except DocumentConflictError as exc:
                await session.rollback()
                replay = await idempotency_replay(
                    session, request, principal, request_key, operation, fingerprint
                )
                if replay is not None:
                    return replay
                raise HTTPException(status_code=409, detail="proposal update conflicted") from exc
            except StorageIntegrityError as exc:
                await session.rollback()
                raise HTTPException(
                    status_code=503, detail="canonical storage is unavailable"
                ) from exc
            result_document = document_response(document, service(session, request))
            response.headers["ETag"] = result_document.etag
            response.headers["X-Connectmd-Search"] = "queued"
            if decision_receipt.resource_id is None:
                raise HTTPException(
                    status_code=503,
                    detail="idempotent proposal decision receipt cannot be reconstructed",
                )
            await store_idempotency(
                session,
                request,
                principal,
                key=request_key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=200,
                body="",
                headers={
                    "ETag": result_document.etag,
                    "X-Connectmd-Search": "queued",
                },
                resource_type="proposal_decision",
                resource_id=decision_receipt.resource_id,
                provisional_record=decision_receipt,
            )
            row = await session.get(AgentProposal, proposal_id)
            assert row is not None
        else:
            await store_idempotency(
                session,
                request,
                principal,
                key=request_key,
                operation=operation,
                fingerprint=fingerprint,
                status_code=200,
                body="",
                headers={},
                resource_type="proposal_decision",
                resource_id=f"{proposal_id}:{action}",
            )
        await session.refresh(row)
        result = proposal_response(row)
        return Response(
            content=idempotency_replay_json(result),
            status_code=200,
            media_type="application/json",
            headers=dict(response.headers),
        )

    @app.post(
        "/v1/api-keys",
        response_model=ApiKeyCreateResult,
        status_code=201,
        tags=["agent-keys"],
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_RE.pattern,
                    },
                }
            ]
        },
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response("Only a Clerk-authenticated human can create an agent key."),
            409: _error_response("Idempotency-Key was already used for a different request."),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("Agent-key hashing is unavailable."),
        },
    )
    async def create_api_key(
        body: ApiKeyCreateRequest,
        request: Request,
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can manage agent API keys"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> ApiKeyCreateResult | Response:
        key = idempotency_key(request, required=True)
        normalized_scopes = sorted({str(scope) for scope in body.scopes})
        operation = "POST:/v1/api-keys"
        fingerprint = _request_fingerprint(
            operation,
            json.dumps({"scopes": normalized_scopes}, sort_keys=True, separators=(",", ":")),
        )
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        try:
            record, raw_key = await request.app.state.api_keys.create(
                session, principal.subject, normalized_scopes, commit=False
            )
            await session.refresh(record)
        except AuthenticationUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        now = datetime.now(UTC)
        session.add(
            ChangeEvent(
                owner_id=principal.subject,
                event_type="api_key.created",
                resource_type="api_key",
                resource_id=record.id,
                actor_id=principal.audit_actor_id,
                actor_method=principal.method,
                grant_id=None,
                payload=json.dumps({"scopes": normalized_scopes}, sort_keys=True),
                occurred_at=now,
            )
        )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=201,
            body="",
            headers={},
            resource_type="api_key",
            resource_id=record.id,
        )
        return ApiKeyCreatedResponse(
            id=record.id,
            prefix=record.prefix,
            scopes=normalized_scopes,
            key=raw_key,
            created_at=(
                record.created_at
                if record.created_at.tzinfo is not None
                else record.created_at.replace(tzinfo=UTC)
            ),
        )

    @app.get(
        "/v1/api-keys",
        response_model=list[ApiKeyResponse],
        tags=["agent-keys"],
        responses={403: _error_response("Only a Clerk-authenticated human can list agent keys.")},
    )
    async def list_api_keys(
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can manage agent API keys"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> list[ApiKeyResponse]:
        rows = (
            await session.scalars(
                select(ApiKey)
                .where(ApiKey.owner_id == principal.subject)
                .order_by(ApiKey.created_at.desc())
            )
        ).all()
        return [
            ApiKeyResponse(
                id=row.id,
                prefix=row.prefix,
                scopes=json.loads(row.scopes),
                revoked=row.revoked,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
            )
            for row in rows
        ]

    @app.delete(
        "/v1/api-keys/{key_id}",
        status_code=204,
        tags=["agent-keys"],
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _IDEMPOTENCY_KEY_RE.pattern,
                    },
                }
            ]
        },
        responses={
            400: _error_response("Idempotency-Key is malformed."),
            403: _error_response("Only a Clerk-authenticated human can revoke an agent key."),
            404: _error_response("The agent key was not found."),
            409: _error_response("Idempotency-Key was already used for a different request."),
            428: _error_response("Idempotency-Key is required."),
            503: _error_response("The API-key revocation receipt is unavailable."),
        },
    )
    async def revoke_api_key(
        key_id: str,
        request: Request,
        principal: Principal = Depends(
            require_non_impersonated_clerk_human(
                "only an authenticated Clerk user can manage agent API keys"
            )
        ),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        key = idempotency_key(request, required=True)
        operation = f"DELETE:/v1/api-keys/{key_id}"
        fingerprint = _request_fingerprint(operation, "")
        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)
        if replay is not None:
            return replay
        record = await session.scalar(
            select(ApiKey)
            .where(ApiKey.id == key_id, ApiKey.owner_id == principal.subject)
            .with_for_update()
        )
        if record is None:
            raise HTTPException(status_code=404, detail="API key was not found")
        now = datetime.now(UTC)
        if not record.revoked:
            record.revoked = True
            session.add(
                ChangeEvent(
                    owner_id=principal.subject,
                    event_type="api_key.revoked",
                    resource_type="api_key",
                    resource_id=record.id,
                    actor_id=principal.audit_actor_id,
                    actor_method=principal.method,
                    grant_id=None,
                    payload="{}",
                    occurred_at=now,
                )
            )
        await store_idempotency(
            session,
            request,
            principal,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            status_code=204,
            body="",
            headers={},
            resource_type="api_key",
            resource_id=record.id,
        )
        return Response(status_code=204)

    app.include_router(protocol_metadata_router)
    app.include_router(agent_card_router)

    async def protocol_public_search(
        request: Request, session: AsyncSession, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        search_arguments = protocol_search_arguments(
            arguments,
            internal_contact_request_capability=_INTERNAL_CONTACT_REQUEST_CAPABILITY,
            max_repeated_values=MAX_SEARCH_REPEATED_VALUES,
        )
        result = await execute_public_search(
            request,
            session,
            search_arguments,
            allow_long_canonical=True,
        )
        return result.model_dump(mode="json")

    def a2a_task(
        source_message: dict[str, Any],
        *,
        state: str,
        summary: str,
        result: Any = None,
        include_source_history: bool = True,
    ) -> dict[str, Any]:
        task_id = str(uuid4())
        context_id = source_message.get("contextId")
        if not isinstance(context_id, str) or not context_id:
            context_id = str(uuid4())
        agent_message = {
            "messageId": str(uuid4()),
            "contextId": context_id,
            "taskId": task_id,
            "role": "ROLE_AGENT",
            "parts": [{"text": summary, "mediaType": "text/plain"}],
        }
        task: dict[str, Any] = {
            "id": task_id,
            "contextId": context_id,
            "status": {
                "state": state,
                "message": agent_message,
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
        }
        if include_source_history and state != "TASK_STATE_FAILED":
            task["history"] = [source_message, agent_message]
        if result is not None:
            task["artifacts"] = [
                {
                    "artifactId": str(uuid4()),
                    "name": "connect.md result",
                    "parts": [{"data": result, "mediaType": "application/json"}],
                }
            ]
        return {"task": task}

    def a2a_action_error(
        source_message: dict[str, Any], *, status_code: int, summary: str
    ) -> JSONResponse:
        if status_code == 401:
            state = "TASK_STATE_AUTH_REQUIRED"
            code = "auth_required"
            error_message = "authentication is required for this action"
        elif status_code in {400, 422, 428}:
            state = "TASK_STATE_REJECTED"
            code = "invalid_params"
            error_message = "the action parameters are invalid"
        elif status_code in {403, 404}:
            state = "TASK_STATE_REJECTED"
            code = "request_rejected"
            error_message = "the action request was not accepted"
        elif status_code == 409:
            state = "TASK_STATE_REJECTED"
            code = "conflict"
            error_message = "the action request conflicted with current state"
        elif status_code == 429:
            state = "TASK_STATE_REJECTED"
            code = "rate_limited"
            error_message = "the action request was rate limited"
        else:
            state = "TASK_STATE_FAILED"
            code = "service_unavailable"
            error_message = "the action service is temporarily unavailable"
        return JSONResponse(
            a2a_task(
                source_message,
                state=state,
                summary=summary,
                result={"error": {"code": code, "message": error_message}},
                include_source_history=False,
            ),
            media_type="application/a2a+json",
        )

    @app.post("/a2a/message:send", include_in_schema=False)
    async def a2a_send_message(
        request: Request,
        principal: Principal | None = Depends(optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"application/a2a+json", "application/json"}:
            raise HTTPException(status_code=415, detail="A2A requests require application/a2a+json")
        if request.headers.get("a2a-version") != "1.0":
            return JSONResponse(
                {
                    "type": "https://a2a-protocol.org/errors/version-not-supported",
                    "title": "Protocol Version Not Supported",
                    "status": 400,
                    "detail": "connect.md supports A2A-Version 1.0 on this interface",
                    "supportedVersions": ["1.0"],
                },
                status_code=400,
                media_type="application/problem+json",
            )
        raw_body = await request.body()
        if len(raw_body) > 65_536:
            raise HTTPException(status_code=413, detail="A2A message exceeds 64 KiB")
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="A2A request JSON is malformed") from None
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise HTTPException(status_code=422, detail="A2A message is required")
        message_id = message.get("messageId")
        parts = message.get("parts")
        if (
            not isinstance(message_id, str)
            or not message_id
            or message.get("role") != "ROLE_USER"
            or not isinstance(parts, list)
            or not parts
        ):
            raise HTTPException(status_code=422, detail="A2A message fields are invalid")
        data_parts = [part.get("data") for part in parts if isinstance(part, dict)]
        data = next((item for item in data_parts if isinstance(item, dict)), None)
        if data is None:
            result = a2a_task(
                message,
                state="TASK_STATE_INPUT_REQUIRED",
                summary="Provide one application/json data part with action search, list_taxonomies, list_taxonomy_terms, get_agent_identity, list_agent_directory, list_profile_agents, contact_request, agent_outreach, or get_agent_outreach_status.",
            )
            return JSONResponse(result, media_type="application/a2a+json")
        action = data.get("action")
        if action == "list_taxonomies":
            if set(data) != {"action"}:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The public taxonomy request is invalid.",
                )
            try:
                catalog = await request.app.state.taxonomy.catalog(session)
            except TaxonomyUnavailable:
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="Public taxonomy discovery is temporarily unavailable.",
                    result={
                        "error": {
                            "code": "service_unavailable",
                            "message": "public taxonomy is temporarily unavailable",
                        }
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary="Current public search taxonomies retrieved.",
                result={"taxonomies": catalog},
            )
            return JSONResponse(result, media_type="application/a2a+json")
        if action == "list_taxonomy_terms":
            allowed = {"action", "taxonomy", "q", "cursor", "limit"}
            if set(data) - allowed or not isinstance(data.get("taxonomy"), str):
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The public taxonomy term request is invalid.",
                )
            query = data.get("q", "")
            cursor = data.get("cursor")
            limit = data.get("limit", 50)
            if (
                not isinstance(query, str)
                or len(query) > 100
                or (
                    cursor is not None
                    and (not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048)
                )
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= 100
            ):
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The public taxonomy term request is invalid.",
                )
            try:
                terms, next_cursor, revision = await request.app.state.taxonomy.terms(
                    session,
                    taxonomy=data["taxonomy"],
                    query=query,
                    cursor=cursor,
                    limit=limit,
                )
            except TaxonomyUnavailable:
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="Public taxonomy discovery is temporarily unavailable.",
                    result={
                        "error": {
                            "code": "service_unavailable",
                            "message": "public taxonomy is temporarily unavailable",
                        }
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            except TaxonomyUnknown:
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="The requested taxonomy was not found.",
                    result={"error": {"code": "not_found", "message": "taxonomy was not found"}},
                )
                return JSONResponse(result, media_type="application/a2a+json")
            except TaxonomyCursorStale:
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="The taxonomy cursor must be restarted.",
                    result={
                        "error": {
                            "code": "restart_required",
                            "message": "taxonomy cursor is stale",
                        }
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            except (TaxonomyCursorMalformed, TaxonomyInvalidValue):
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="The taxonomy term request is invalid.",
                    result={
                        "error": {"code": "bad_request", "message": "invalid taxonomy request"}
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary=f"Found {len(terms)} current public taxonomy terms.",
                result={"terms": terms, "next_cursor": next_cursor, "revision": revision},
            )
            return JSONResponse(result, media_type="application/a2a+json")
        if action == "search":
            try:
                search_result = await protocol_public_search(
                    request, session, {key: value for key, value in data.items() if key != "action"}
                )
            except ExactSearchTooBroad:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The public exact search request is too broad.",
                )
            except ExactSearchCursorMalformed:
                return a2a_action_error(
                    message,
                    status_code=400,
                    summary="The public exact search cursor is invalid.",
                )
            except ExactSearchCursorStale:
                return a2a_action_error(
                    message,
                    status_code=409,
                    summary="The public exact search cursor must be restarted.",
                )
            except ExactSearchUnavailable:
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="Public exact search is temporarily unavailable.",
                    result={
                        "error": {
                            "code": "service_unavailable",
                            "message": "public exact search is temporarily unavailable",
                        }
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            except ValueError:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The public search request is invalid.",
                )
            except TaxonomyUnavailable:
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="Public taxonomy is temporarily unavailable.",
                    result={
                        "error": {
                            "code": "service_unavailable",
                            "message": "public taxonomy is temporarily unavailable",
                        }
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            except SearchUnavailable:
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="Public search is temporarily unavailable.",
                    result={
                        "error": {
                            "code": "search_unavailable",
                            "message": "public search is temporarily unavailable",
                        }
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary=f"Found {search_result['total']} public connect.md documents.",
                result=search_result,
            )
            return JSONResponse(result, media_type="application/a2a+json")
        if action == "list_agent_directory":
            try:
                query, profile_handle, limit, cursor = protocol_agent_directory_arguments(
                    {key: value for key, value in data.items() if key != "action"}
                )
                identities = await list_public_agent_identities(
                    session,
                    query=query,
                    profile_handle=profile_handle,
                    limit=limit,
                    cursor=cursor,
                )
            except ValueError:
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="The public agent directory request is invalid.",
                    result={
                        "error": {
                            "code": "bad_request",
                            "message": "invalid public agent directory request",
                        }
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            except HTTPException as exc:
                if exc.status_code not in {400, 422}:
                    raise
                result = a2a_task(
                    message,
                    state="TASK_STATE_FAILED",
                    summary="The public agent directory request is invalid.",
                    result={
                        "error": {
                            "code": "bad_request",
                            "message": "invalid public agent directory request",
                        }
                    },
                )
                return JSONResponse(result, media_type="application/a2a+json")
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary=f"Found {len(identities.identities)} active public Agent Identities.",
                result=identities.model_dump(mode="json"),
            )
            return JSONResponse(result, media_type="application/a2a+json")
        if action == "list_profile_agents":
            try:
                profile_handle, limit, cursor = protocol_profile_agents_arguments(
                    {key: value for key, value in data.items() if key != "action"}
                )
                await public_profile_by_handle(session, profile_handle)
                identities = await list_public_agent_identities(
                    session,
                    query="",
                    profile_handle=profile_handle,
                    limit=limit,
                    cursor=cursor,
                )
            except ValueError:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The public profile agent request is invalid.",
                )
            except HTTPException as exc:
                return a2a_action_error(
                    message,
                    status_code=exc.status_code,
                    summary="The public profile agent request was not accepted.",
                )
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary=f"Found {len(identities.identities)} active public Agent Identities.",
                result=identities.model_dump(mode="json"),
            )
            return JSONResponse(result, media_type="application/a2a+json")
        if action == "get_agent_identity":
            try:
                agent_handle = protocol_agent_identity_argument(
                    {key: value for key, value in data.items() if key != "action"}
                )
                live = await live_agent_identity(session, agent_handle)
                if live is None:
                    raise HTTPException(status_code=404, detail="agent identity was not found")
            except ValueError:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The public agent identity request is invalid.",
                )
            except HTTPException as exc:
                return a2a_action_error(
                    message,
                    status_code=exc.status_code,
                    summary="The public agent identity request was not accepted.",
                )
            identity, profile = live
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary="The public Agent Identity was retrieved.",
                result=agent_identity_response(identity, profile).model_dump(mode="json"),
            )
            return JSONResponse(result, media_type="application/a2a+json")
        if action == "contact_request":
            allowed = {"action", "target_profile_handle", "purpose", "message"}
            if set(data) - allowed:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The mediated contact request was not accepted.",
                )
            if principal is None:
                return a2a_action_error(
                    message,
                    summary="An authenticated owner credential authorized for contact requests is required.",
                    status_code=401,
                )
            try:
                try:
                    contact_body = ContactRequestCreate.model_validate(data)
                except ValidationError as exc:
                    raise HTTPException(
                        status_code=422, detail="A2A contact request fields are invalid"
                    ) from exc
                contact = await create_contact_request(
                    contact_body, request, principal=principal, session=session
                )
                contact_result = (
                    json.loads(contact.body)
                    if isinstance(contact, Response)
                    else contact.model_dump(mode="json")
                )
            except HTTPException as exc:
                await session.rollback()
                return a2a_action_error(
                    message,
                    status_code=exc.status_code,
                    summary="The mediated contact request was not accepted.",
                )
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary="The request was placed in the recipient's connect.md inbox.",
                result={"contact_request": contact_result},
            )
            return JSONResponse(result, media_type="application/a2a+json")
        if action == "agent_outreach":
            allowed = {"action", "target_agent_handle", "purpose", "message"}
            if set(data) - allowed:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The mandate-bound agent outreach was not accepted.",
                )
            if principal is None:
                return a2a_action_error(
                    message,
                    summary="A mandate-bound agent grant is required for agent outreach.",
                    status_code=401,
                )
            try:
                try:
                    outreach_body = AgentOutreachCreate.model_validate(data)
                except ValidationError as exc:
                    raise HTTPException(
                        status_code=422, detail="A2A agent outreach fields are invalid"
                    ) from exc
                outreach = await create_agent_outreach(
                    outreach_body, request, principal=principal, session=session
                )
                outreach_result = (
                    json.loads(outreach.body)
                    if isinstance(outreach, Response)
                    else outreach.model_dump(mode="json")
                )
            except HTTPException as exc:
                await session.rollback()
                return a2a_action_error(
                    message,
                    status_code=exc.status_code,
                    summary="The mandate-bound agent outreach was not accepted.",
                )
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary="The agent outreach was placed in the recipient's connect.md inbox.",
                result={"contact_request": outreach_result},
            )
            return JSONResponse(result, media_type="application/a2a+json")
        if action == "get_agent_outreach_status":
            allowed = {"action", "request_id"}
            if set(data) - allowed:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The agent outreach status request was not accepted.",
                )
            try:
                outreach_request_id = _canonical_agent_outreach_request_id(data.get("request_id"))
            except ValueError:
                return a2a_action_error(
                    message,
                    status_code=422,
                    summary="The agent outreach status request was not accepted.",
                )
            if principal is None:
                return a2a_action_error(
                    message,
                    summary="The originating mandate or sender's signed-in human owner is required.",
                    status_code=401,
                )
            try:
                outreach_status = await get_agent_outreach_status(
                    outreach_request_id, principal=principal, session=session
                )
            except HTTPException as exc:
                return a2a_action_error(
                    message,
                    status_code=exc.status_code,
                    summary="The agent outreach status is unavailable.",
                )
            result = a2a_task(
                message,
                state="TASK_STATE_COMPLETED",
                summary="The privacy-minimal agent outreach status was retrieved.",
                result={"agent_outreach": outreach_status.model_dump(mode="json")},
            )
            return JSONResponse(result, media_type="application/a2a+json")
        return a2a_action_error(
            message,
            status_code=422,
            summary="The requested A2A action is unsupported.",
        )

    def mcp_tool_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
        serialized = json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": serialized}],
            "structuredContent": value,
        }
        if is_error:
            result["isError"] = True
        return result

    def mcp_authenticated_principal(request: Request, principal: Principal | None) -> Principal:
        if principal is None:
            metadata = public_base_url(request) + "/.well-known/oauth-protected-resource/mcp"
            raise HTTPException(
                status_code=401,
                detail="Bearer authentication is required for this MCP tool",
                headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
            )
        return principal

    async def mcp_optional_principal(
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> Principal | None:
        provider: Any = request.app.dependency_overrides.get(optional_principal, optional_principal)
        try:
            provider_parameters = signature(provider).parameters
            provider_kwargs: dict[str, Any] = {}
            if "request" in provider_parameters:
                provider_kwargs["request"] = request
            if "session" in provider_parameters:
                provider_kwargs["session"] = session
            result = provider(**provider_kwargs)
            if isawaitable(result):
                result = await result
            return cast(Principal | None, result)
        except HTTPException as exc:
            request.state.mcp_auth_error = exc
            return None

    def mcp_response_value(response: Response) -> Any:
        try:
            return json.loads(bytes(response.body))
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=503,
                detail="idempotent write receipt is unavailable",
            ) from exc

    def mcp_error_value(exc: BaseException) -> dict[str, str]:
        status_codes = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            412: "precondition_failed",
            413: "payload_too_large",
            415: "unsupported_media_type",
            422: "validation_failed",
            428: "precondition_required",
            503: "service_unavailable",
        }
        if isinstance(exc, ExactSearchTooBroad):
            return {
                "code": "query_too_broad",
                "message": EXACT_SEARCH_TOO_BROAD_MESSAGE,
            }
        if isinstance(exc, ExactSearchCursorStale):
            return {"code": "restart_required", "message": "exact search cursor is stale"}
        if isinstance(exc, ExactSearchCursorMalformed):
            return {"code": "bad_request", "message": "exact search cursor is malformed"}
        if isinstance(exc, ExactSearchUnavailable):
            return {
                "code": "service_unavailable",
                "message": "exact search is temporarily unavailable",
            }
        if isinstance(exc, TaxonomyUnavailable):
            return {
                "code": "service_unavailable",
                "message": "public taxonomy is temporarily unavailable",
            }
        if isinstance(exc, TaxonomyUnknown):
            return {"code": "not_found", "message": "taxonomy was not found"}
        if isinstance(exc, TaxonomyCursorStale):
            return {"code": "restart_required", "message": "taxonomy cursor is stale"}
        if isinstance(exc, (TaxonomyCursorMalformed, TaxonomyInvalidValue)):
            return {"code": "bad_request", "message": "the taxonomy request is invalid"}
        if isinstance(exc, HTTPException):
            code = status_codes.get(exc.status_code, "tool_error")
            if exc.status_code in {401, 503}:
                message = (
                    "Bearer authentication is required or invalid"
                    if exc.status_code == 401
                    else "the service is temporarily unavailable"
                )
            elif isinstance(exc.detail, str) and exc.detail:
                message = exc.detail[:512]
            else:
                message = "the MCP tool request was rejected"
            return {"code": code, "message": message}
        if isinstance(exc, MarkdownSizeError):
            return {
                "code": "payload_too_large",
                "message": (
                    "canonical Profile/Resume Markdown exceeds "
                    f"{canonical_document_max_utf8_bytes()} UTF-8 bytes"
                ),
            }
        if isinstance(exc, SearchUnavailable):
            return {
                "code": "search_unavailable",
                "message": "public search is temporarily unavailable",
            }
        if isinstance(exc, DocumentPreconditionError):
            return {"code": "precondition_failed", "message": str(exc)[:512]}
        if isinstance(exc, DocumentNotFoundError):
            return {"code": "not_found", "message": "document was not found"}
        if isinstance(exc, DocumentForbiddenError):
            return {"code": "forbidden", "message": "document access was denied"}
        if isinstance(exc, (DocumentConflictError, MarkdownVersionConflictError, IntegrityError)):
            return {"code": "conflict", "message": "the document write conflicted"}
        if isinstance(exc, MarkdownValidationError):
            return {
                "code": "validation_failed",
                "message": PUBLIC_MARKDOWN_VALIDATION_DETAIL,
            }
        if isinstance(exc, ValidationError):
            return {
                "code": "validation_failed",
                "message": "the MCP tool arguments are invalid",
            }
        if isinstance(exc, StorageIntegrityError):
            return {"code": "service_unavailable", "message": "canonical storage is unavailable"}
        if isinstance(exc, ValueError):
            return {"code": "validation_failed", "message": "the MCP tool arguments are invalid"}
        return {"code": "tool_error", "message": "the MCP tool failed"}

    def mcp_public_agent_error_value(exc: BaseException) -> dict[str, str]:
        if isinstance(exc, ValueError) or (
            isinstance(exc, HTTPException) and exc.status_code in {400, 422}
        ):
            return {
                "code": "validation_failed",
                "message": "the public agent identity request is invalid",
            }
        if isinstance(exc, HTTPException) and exc.status_code == 404:
            return {
                "code": "not_found",
                "message": "the public agent identity or profile was not found",
            }
        return {
            "code": "service_unavailable",
            "message": "the public agent identity is temporarily unavailable",
        }

    def mcp_agent_outreach_error_value(exc: BaseException) -> dict[str, str]:
        if not isinstance(exc, HTTPException):
            return {"code": "tool_error", "message": "the MCP tool failed"}
        if exc.status_code == 401:
            return {
                "code": "unauthorized",
                "message": "Bearer authentication is required or invalid",
            }
        if exc.status_code == 400:
            return {
                "code": "bad_request",
                "message": "the agent outreach request is invalid",
            }
        if exc.status_code in {422, 428}:
            return {
                "code": "validation_failed" if exc.status_code == 422 else "precondition_required",
                "message": "the agent outreach request parameters are invalid"
                if exc.status_code == 422
                else "Idempotency-Key is required for this operation",
            }
        if exc.status_code in {403, 404}:
            return {
                "code": "request_rejected",
                "message": "the agent outreach request was not accepted",
            }
        if exc.status_code == 409:
            return {
                "code": "conflict",
                "message": "the agent outreach request conflicted with current state",
            }
        if exc.status_code == 429:
            return {
                "code": "rate_limited",
                "message": "the agent outreach request was rate limited",
            }
        if exc.status_code == 503:
            return {
                "code": "service_unavailable",
                "message": "the service is temporarily unavailable",
            }
        return {"code": "tool_error", "message": "the MCP tool failed"}

    def mcp_tools() -> list[dict[str, Any]]:
        canonical_limit = canonical_document_max_utf8_bytes()
        return [
            {
                "name": "list_taxonomies",
                "description": "List the current public-v2 PostgreSQL taxonomies accepted by public search.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "list_taxonomy_terms",
                "description": "List current public-v2 terms for one taxonomy; results are discovery-only and do not authorize outreach.",
                "inputSchema": {
                    "type": "object",
                    "required": ["taxonomy"],
                    "properties": {
                        "taxonomy": {
                            "type": "string",
                            "enum": [
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
                            ],
                        },
                        "q": {"type": "string", "maxLength": 100},
                        "cursor": {"type": "string", "minLength": 1, "maxLength": 2048},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "search_documents",
                "description": "Search public connect.md profiles and resumes with the structured POST-equivalent q contract. Returned Agent Identity references are discovery-only and contain only handle plus the fixed internal-contact capability.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["projection", "exact"],
                            "default": "projection",
                            "description": "Projection uses bounded Meilisearch candidates; exact uses the complete ready PostgreSQL corpus up to 50,000 documents.",
                        },
                        "q": {"type": "string", "maxLength": 200},
                        "query": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "Deprecated alias for q; do not send both fields.",
                        },
                        "kind": {"type": "string", "enum": ["profile", "resume"]},
                        "skills": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 80},
                        },
                        "location": {"type": "string", "maxLength": 160},
                        "occupation_ids": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "industry_ids": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "skill_ids": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "language_ids": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "seniority_ids": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "seniority_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 336,
                            "description": "Legacy singular alias; merged into seniority_ids.",
                        },
                        "location_country_code": {"type": "string", "maxLength": 3},
                        "location_region": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "location_city": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "location_id": {"type": "string", "minLength": 1, "maxLength": 336},
                        "work_modes": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string", "minLength": 1, "maxLength": 80},
                        },
                        "availability_status": {"type": "string", "maxLength": 80},
                        "availability_from": {"type": "string", "maxLength": 40},
                        "open_to": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "open_to_ids": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "organization_ids": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "representative_ids": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 336},
                        },
                        "representation_status": {"type": "string", "maxLength": 80},
                        "contact_disclosure": {"type": "string", "maxLength": 80},
                        "updated_after": {"type": "string", "maxLength": 40},
                        "updated_before": {"type": "string", "maxLength": 40},
                        "sort_updated": {"type": "string", "enum": ["asc", "desc"]},
                        "agent_capability": {
                            "type": "string",
                            "enum": [_INTERNAL_CONTACT_REQUEST_CAPABILITY],
                            "description": "Discovery-only profile filter; never proves outreach authority.",
                        },
                        "facets": {
                            "type": "array",
                            "maxItems": 30,
                            "items": {"type": "string", "minLength": 1, "maxLength": 80},
                        },
                        "offset": {"type": "integer", "minimum": 0, "maximum": 1000},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                        "cursor": {"type": "string", "minLength": 1, "maxLength": 2048},
                        "facet_limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                            "default": 100,
                        },
                    },
                    "not": {"required": ["q", "query"]},
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "get_agent_identity",
                "description": "Read one active public Agent Identity linked to a current public owner-matched profile; discovery never authorizes contact or outreach.",
                "inputSchema": {
                    "type": "object",
                    "required": ["agent_handle"],
                    "properties": {
                        "agent_handle": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 100,
                            "pattern": r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$",
                        }
                    },
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "list_profile_agents",
                "description": "List active public Agent Identities for one current public profile; discovery never authorizes contact or outreach.",
                "inputSchema": {
                    "type": "object",
                    "required": ["profile_handle"],
                    "properties": {
                        "profile_handle": {"type": "string", "minLength": 1, "maxLength": 100},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                        "cursor": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "list_agent_directory",
                "description": "List the bounded global directory of active public Agent Identities; discovery never authorizes contact or outreach.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "maxLength": 100},
                        "profile_handle": {"type": "string", "minLength": 1, "maxLength": 100},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                        "cursor": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "send_agent_outreach",
                "description": "Send mandate-bound internal outreach through the canonical consent-gated HTTP operation; this never calls an external endpoint and exposes only the safe receipt.",
                "inputSchema": {
                    "type": "object",
                    "required": [
                        "target_agent_handle",
                        "purpose",
                        "message",
                        "idempotency_key",
                    ],
                    "properties": {
                        "target_agent_handle": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 100,
                        },
                        "purpose": {"type": "string", "minLength": 1, "maxLength": 160},
                        "message": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "idempotency_key": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "pattern": r"^[\x21-\x7e]{1,128}$",
                        },
                    },
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "get_agent_outreach_status",
                "description": "Read the privacy-minimal status of outreach created by the exact active originating mandate or its sender's signed-in human owner.",
                "inputSchema": {
                    "type": "object",
                    "required": ["request_id"],
                    "properties": {
                        "request_id": {
                            "type": "string",
                            "minLength": 36,
                            "maxLength": 36,
                            "format": "uuid",
                            "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                        }
                    },
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "read_document",
                "description": "Read a public canonical profile or resume.",
                "inputSchema": {
                    "type": "object",
                    "required": ["kind", "identifier"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["profile", "resume"]},
                        "identifier": {"type": "string", "minLength": 1, "maxLength": 100},
                    },
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "list_my_documents",
                "description": "List documents authorized by the supplied Bearer credential.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["profile", "resume"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        "cursor": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "get_changes",
                "description": "Read durable owner change events after a sequence cursor.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "after_sequence": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": False},
            },
            {
                "name": "update_document",
                "description": "Conditionally update an authorized document with a signed-in owner, scoped API key, or direct Agent Grant.",
                "inputSchema": {
                    "type": "object",
                    "required": [
                        "kind",
                        "identifier",
                        "markdown",
                        "if_match",
                        "idempotency_key",
                    ],
                    "properties": {
                        "kind": {"type": "string", "enum": ["profile", "resume"]},
                        "identifier": {"type": "string", "minLength": 1, "maxLength": 100},
                        "markdown": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": settings.max_upload_bytes,
                            "description": (
                                "Raw MCP argument is bounded by the transport upload limit; "
                                f"final canonical Profile/Resume Markdown must be at most {canonical_limit} UTF-8 bytes "
                                "after LF normalization. JSON Schema maxLength is not a byte proof."
                            ),
                            "x-connectmd-canonical-max-utf8-bytes": canonical_limit,
                        },
                        "if_match": {
                            "type": "string",
                            "pattern": STRONG_DOCUMENT_ETAG_PATTERN,
                        },
                        "idempotency_key": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "pattern": r"^[\x21-\x7e]{1,128}$",
                        },
                    },
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "create_document",
                "description": "Create a canonical profile or resume for the authorized owner.",
                "inputSchema": {
                    "type": "object",
                    "required": ["kind", "markdown", "idempotency_key"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["profile", "resume"]},
                        "markdown": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": settings.max_upload_bytes,
                            "description": (
                                "Raw MCP argument is bounded by the transport upload limit; "
                                f"final canonical Profile/Resume Markdown must be at most {canonical_limit} UTF-8 bytes "
                                "after LF normalization. JSON Schema maxLength is not a byte proof."
                            ),
                            "x-connectmd-canonical-max-utf8-bytes": canonical_limit,
                        },
                        "idempotency_key": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "pattern": r"^[\x21-\x7e]{1,128}$",
                        },
                    },
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "propose_document_update",
                "description": "Submit a conditional proposal-only update for an authorized document.",
                "inputSchema": {
                    "type": "object",
                    "required": [
                        "kind",
                        "identifier",
                        "markdown",
                        "if_match",
                        "idempotency_key",
                    ],
                    "properties": {
                        "kind": {"type": "string", "enum": ["profile", "resume"]},
                        "identifier": {"type": "string", "minLength": 1, "maxLength": 100},
                        "markdown": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": settings.max_upload_bytes,
                            "description": (
                                "Raw MCP argument is bounded by the transport upload limit; "
                                f"final canonical Profile/Resume Markdown must be at most {canonical_limit} UTF-8 bytes "
                                "after LF normalization. JSON Schema maxLength is not a byte proof."
                            ),
                            "x-connectmd-canonical-max-utf8-bytes": canonical_limit,
                        },
                        "if_match": {
                            "type": "string",
                            "pattern": STRONG_DOCUMENT_ETAG_PATTERN,
                        },
                        "idempotency_key": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "pattern": r"^[\x21-\x7e]{1,128}$",
                        },
                    },
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
        ]

    @app.get("/mcp", include_in_schema=False)
    async def mcp_get() -> Response:
        raise HTTPException(
            status_code=405,
            detail="this stateless MCP endpoint accepts POST with application/json",
            headers={"Allow": "POST"},
        )

    @app.post("/mcp", include_in_schema=False)
    async def mcp(
        request: Request,
        principal: Principal | None = Depends(mcp_optional_principal),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        raw_body = await request.body()
        if len(raw_body) > _MCP_MAX_RAW_ENVELOPE_BYTES:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "MCP request exceeds 1 MiB"},
                },
                status_code=413,
            )
        try:
            message = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request"},
                },
                status_code=400,
            )
        request_id_value = message.get("id")
        method = message.get("method")
        params = message.get("params", {})
        headers = {"MCP-Protocol-Version": "2025-06-18"}
        auth_error = getattr(request.state, "mcp_auth_error", None)
        if auth_error is not None:
            auth_tool = params.get("name") if isinstance(params, dict) else None
            if method == "tools/call" and auth_tool in {
                "send_agent_outreach",
                "get_agent_outreach_status",
            }:
                auth_value = mcp_agent_outreach_error_value(auth_error)
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id_value,
                        "result": mcp_tool_result(auth_value, is_error=True),
                    },
                    headers=headers,
                )
            raise auth_error
        if method == "notifications/initialized" and request_id_value is None:
            return Response(status_code=202, headers=headers)
        if method == "initialize":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id_value,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "connect.md", "version": app.version},
                        "instructions": "Public search and public reads are anonymous. Management tools require an authenticated Bearer credential with applicable scopes; proposal submission requires a proposal-only Agent Grant.",
                    },
                },
                headers=headers,
            )
        if method == "ping":
            return JSONResponse(
                {"jsonrpc": "2.0", "id": request_id_value, "result": {}}, headers=headers
            )
        if method == "tools/list":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id_value,
                    "result": {"tools": mcp_tools()},
                },
                headers=headers,
            )
        if method != "tools/call" or not isinstance(params, dict):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id_value,
                    "error": {"code": -32601, "message": "Method not found"},
                },
                headers=headers,
            )
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id_value,
                    "error": {"code": -32602, "message": "Invalid params"},
                },
                headers=headers,
            )
        value: Any = None
        try:
            if name == "list_taxonomies":
                if arguments:
                    raise HTTPException(status_code=422, detail="taxonomy arguments are invalid")
                value = {
                    "taxonomies": await request.app.state.taxonomy.catalog(session),
                }
            elif name == "list_taxonomy_terms":
                allowed = {"taxonomy", "q", "cursor", "limit"}
                if set(arguments) - allowed or not isinstance(arguments.get("taxonomy"), str):
                    raise HTTPException(
                        status_code=422, detail="taxonomy term arguments are invalid"
                    )
                query = arguments.get("q", "")
                cursor = arguments.get("cursor")
                term_limit = arguments.get("limit", 50)
                if (
                    not isinstance(query, str)
                    or len(query) > 100
                    or (
                        cursor is not None
                        and (
                            not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048
                        )
                    )
                    or isinstance(term_limit, bool)
                    or not isinstance(term_limit, int)
                    or not 1 <= term_limit <= 100
                ):
                    raise HTTPException(
                        status_code=422, detail="taxonomy term arguments are invalid"
                    )
                terms, next_cursor, revision = await request.app.state.taxonomy.terms(
                    session,
                    taxonomy=arguments["taxonomy"],
                    query=query,
                    cursor=cursor,
                    limit=term_limit,
                )
                value = {"terms": terms, "next_cursor": next_cursor, "revision": revision}
            elif name == "search_documents":
                value = await protocol_public_search(request, session, arguments)
            elif name == "get_agent_identity":
                agent_handle = protocol_agent_identity_argument(arguments)
                live = await live_agent_identity(session, agent_handle)
                if live is None:
                    raise HTTPException(status_code=404, detail="agent identity was not found")
                identity, profile = live
                value = agent_identity_response(identity, profile).model_dump(mode="json")
            elif name == "list_profile_agents":
                profile_handle, limit, cursor = protocol_profile_agents_arguments(arguments)
                await public_profile_by_handle(session, profile_handle)
                value = (
                    await list_public_agent_identities(
                        session,
                        query="",
                        profile_handle=profile_handle,
                        limit=limit,
                        cursor=cursor,
                    )
                ).model_dump(mode="json")
            elif name == "list_agent_directory":
                directory_query, directory_profile_handle, directory_limit, directory_cursor = (
                    protocol_agent_directory_arguments(arguments)
                )
                value = (
                    await list_public_agent_identities(
                        session,
                        query=directory_query,
                        profile_handle=directory_profile_handle,
                        limit=directory_limit,
                        cursor=directory_cursor,
                    )
                ).model_dump(mode="json")
            elif name == "send_agent_outreach":
                current_principal = mcp_authenticated_principal(request, principal)
                outreach_body, outreach_key = mcp_agent_outreach_arguments(arguments)
                request.state.mcp_idempotency_key = outreach_key
                try:
                    outreach = await create_agent_outreach(
                        outreach_body,
                        request,
                        principal=current_principal,
                        session=session,
                    )
                finally:
                    request.state.mcp_idempotency_key = None
                if isinstance(outreach, Response):
                    value = mcp_response_value(outreach)
                    if outreach.headers.get("Idempotency-Replayed") == "true":
                        headers["Idempotency-Replayed"] = "true"
                else:
                    value = outreach.model_dump(mode="json")
            elif name == "get_agent_outreach_status":
                current_principal = mcp_authenticated_principal(request, principal)
                outreach_request_id = mcp_agent_outreach_status_argument(arguments)
                value = (
                    await get_agent_outreach_status(
                        outreach_request_id,
                        principal=current_principal,
                        session=session,
                    )
                ).model_dump(mode="json")
            elif name == "read_document":
                kind, identifier = mcp_read_document_arguments(arguments)
                document = await service(session, request).get(kind, identifier)
                can_read(document, principal)
                value = document_response(document, service(session, request)).model_dump(
                    mode="json"
                )
            elif name == "list_my_documents":
                if principal is None:
                    metadata = (
                        public_base_url(request) + "/.well-known/oauth-protected-resource/mcp"
                    )
                    raise HTTPException(
                        status_code=401,
                        detail="Bearer authentication is required for this MCP tool",
                        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
                    )
                if principal.method in {"agent_api_key", "agent_grant"} and not (
                    {"inventory:read", "documents:read"} & principal.scopes
                ):
                    raise HTTPException(
                        status_code=403, detail="agent credential lacks inventory scope"
                    )
                assert_agent_grant_resource_domain(principal, frozenset({"owner", "document"}))
                document_kind, document_limit, document_cursor = mcp_list_my_documents_arguments(
                    arguments
                )
                document_statement = select(Document).where(Document.owner_id == principal.subject)
                if document_kind is not None:
                    document_statement = document_statement.where(Document.kind == document_kind)
                if principal.method == "agent_grant" and principal.resource_type == "document":
                    document_statement = document_statement.where(
                        Document.id == principal.resource_id
                    )
                expected_resource_type = (
                    principal.resource_type if principal.method == "agent_grant" else "owner"
                )
                expected_resource_id = (
                    principal.resource_id
                    if principal.method == "agent_grant" and principal.resource_type == "document"
                    else None
                )
                document_cursor_bindings = cursor_principal_bindings(principal) + (
                    document_kind or "",
                    expected_resource_type or "",
                    expected_resource_id or "",
                )
                if document_cursor:
                    payload = generic_cursor_decode(
                        document_cursor,
                        scope="my_documents",
                        bindings=document_cursor_bindings,
                        detail="document cursor is malformed",
                    )
                    try:
                        if payload.get("v") != 1 or payload.get("scope") != "my_documents":
                            raise ValueError
                        if payload.get("kind") != document_kind:
                            raise ValueError
                        if payload.get("resource_type") != expected_resource_type:
                            raise ValueError
                        if payload.get("resource_id") != expected_resource_id:
                            raise ValueError
                        updated_at = datetime.fromisoformat(str(payload["updated_at"]))
                        document_id = str(payload["id"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise HTTPException(
                            status_code=400, detail="document cursor is malformed"
                        ) from exc
                    document_statement = document_statement.where(
                        or_(
                            Document.updated_at < updated_at,
                            and_(
                                Document.updated_at == updated_at,
                                Document.id < document_id,
                            ),
                        )
                    )
                documents = (
                    await session.scalars(
                        document_statement.options(selectinload(Document.versions))
                        .order_by(Document.updated_at.desc(), Document.id.desc())
                        .limit(document_limit + 1)
                    )
                ).all()
                page = documents[:document_limit]
                next_cursor = None
                if len(documents) > document_limit and page:
                    last = page[-1]
                    next_cursor = generic_cursor_encode(
                        {
                            "v": 1,
                            "scope": "my_documents",
                            "kind": document_kind,
                            "resource_type": expected_resource_type,
                            "resource_id": expected_resource_id,
                            "updated_at": last.updated_at.isoformat(),
                            "id": last.id,
                        },
                        scope="my_documents",
                        bindings=document_cursor_bindings,
                    )
                value = {
                    "documents": [
                        OwnerDocumentSummary(
                            id=row.id,
                            kind=cast(DocumentKind, row.kind),
                            identifier=row.public_identifier,
                            visibility=cast(Visibility, row.visibility),
                            version=row.current_version,
                            updated_at=row.updated_at,
                            markdown_url=markdown_url(row),
                            etag=strong_etag(current_version(row).sha256),
                        ).model_dump(mode="json")
                        for row in page
                    ],
                    "next_cursor": next_cursor,
                }
            elif name == "get_changes":
                if principal is None:
                    metadata = (
                        public_base_url(request) + "/.well-known/oauth-protected-resource/mcp"
                    )
                    raise HTTPException(
                        status_code=401,
                        detail="Bearer authentication is required for this MCP tool",
                        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
                    )
                if principal.method in {"agent_api_key", "agent_grant"} and not (
                    {"changes:read", "documents:read"} & principal.scopes
                ):
                    raise HTTPException(
                        status_code=403, detail="agent credential lacks change-feed scope"
                    )
                assert_agent_grant_resource_domain(principal, frozenset({"owner", "document"}))
                after, change_limit = mcp_get_changes_arguments(arguments)
                change_statement = select(ChangeEvent).where(
                    ChangeEvent.owner_id == principal.subject, ChangeEvent.sequence > after
                )
                if principal.method != "clerk_jwt":
                    change_statement = change_statement.where(
                        ChangeEvent.resource_type.not_in(
                            _NON_HUMAN_CHANGE_FEED_EXCLUDED_RESOURCE_TYPES
                        )
                    )
                    for event_pattern in _NON_HUMAN_CHANGE_FEED_EXCLUDED_EVENT_PATTERNS:
                        change_statement = change_statement.where(
                            ChangeEvent.event_type.not_like(event_pattern, escape="\\")
                        )
                if principal.method == "agent_grant" and principal.resource_type == "document":
                    change_statement = change_statement.where(
                        ChangeEvent.resource_id == principal.resource_id
                    )
                event_rows = (
                    await session.scalars(
                        change_statement.order_by(ChangeEvent.sequence.asc()).limit(change_limit)
                    )
                ).all()
                value = [
                    public_change_event_projection(row, viewer=principal).model_dump(mode="json")
                    for row in event_rows
                ]
            elif name == "create_document":
                current_principal = mcp_authenticated_principal(request, principal)
                kind, markdown, key = mcp_create_arguments(
                    arguments, max_upload_bytes=request.app.state.settings.max_upload_bytes
                )
                response_headers = Response()
                write_result = await _create_document_write(
                    kind,
                    request,
                    current_principal,
                    session,
                    response_headers,
                    markdown=markdown,
                    key=key,
                    operation=f"MCP:create_document:{kind}",
                )
                if isinstance(write_result, Response):
                    value = mcp_response_value(write_result)
                    if write_result.headers.get("Idempotency-Replayed") == "true":
                        headers["Idempotency-Replayed"] = "true"
                else:
                    value = write_result.model_dump(mode="json")
                headers["X-Connectmd-Search"] = "queued"
            elif name == "update_document":
                current_principal = mcp_authenticated_principal(request, principal)
                kind, identifier, markdown, if_match, key = mcp_update_arguments(
                    arguments,
                    operation="update_document",
                    max_upload_bytes=request.app.state.settings.max_upload_bytes,
                )
                response_headers = Response()
                write_result = await _update_document_write(
                    kind,
                    identifier,
                    request,
                    current_principal,
                    session,
                    response_headers,
                    markdown=markdown,
                    if_match=if_match,
                    key=key,
                    operation=f"MCP:update_document:{kind}:{identifier}",
                )
                if isinstance(write_result, Response):
                    value = mcp_response_value(write_result)
                    if write_result.headers.get("Idempotency-Replayed") == "true":
                        headers["Idempotency-Replayed"] = "true"
                else:
                    value = write_result.model_dump(mode="json")
                headers["X-Connectmd-Search"] = "queued"
            elif name == "propose_document_update":
                current_principal = mcp_authenticated_principal(request, principal)
                kind, identifier, markdown, if_match, key = mcp_update_arguments(
                    arguments,
                    operation="propose_document_update",
                    max_upload_bytes=request.app.state.settings.max_upload_bytes,
                )
                proposal_body = AgentProposalCreateRequest(
                    kind=kind,
                    identifier=identifier,
                    markdown=markdown,
                    if_match=if_match,
                )
                proposal_result = await _submit_proposal_write(
                    proposal_body,
                    request,
                    current_principal,
                    session,
                    key=key,
                    operation=f"MCP:propose_document_update:{kind}:{identifier}",
                )
                if isinstance(proposal_result, Response):
                    value = mcp_response_value(proposal_result)
                    if proposal_result.headers.get("Idempotency-Replayed") == "true":
                        headers["Idempotency-Replayed"] = "true"
                else:
                    value = proposal_result.model_dump(mode="json")
            else:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id_value,
                        "error": {"code": -32602, "message": "Unknown tool"},
                    },
                    headers=headers,
                )
        except ConcurrentIdempotencyReplay as exc:
            value = mcp_response_value(exc.response)
            headers["Idempotency-Replayed"] = "true"
            result = mcp_tool_result(value)
        except HTTPException as exc:
            if name not in {
                "create_document",
                "update_document",
                "propose_document_update",
                "search_documents",
                "list_taxonomies",
                "list_taxonomy_terms",
                "get_agent_identity",
                "list_agent_directory",
                "list_profile_agents",
                "list_my_documents",
                "read_document",
                "get_changes",
                "send_agent_outreach",
                "get_agent_outreach_status",
            }:
                raise
            await session.rollback()
            value = (
                mcp_agent_outreach_error_value(exc)
                if name in {"send_agent_outreach", "get_agent_outreach_status"}
                else mcp_public_agent_error_value(exc)
                if name in {"get_agent_identity", "list_profile_agents"}
                else mcp_error_value(exc)
            )
            result = mcp_tool_result(value, is_error=True)
        except Exception as exc:
            await session.rollback()
            value = (
                mcp_public_agent_error_value(exc)
                if name in {"get_agent_identity", "list_profile_agents"}
                else mcp_error_value(exc)
            )
            result = mcp_tool_result(value, is_error=True)
        else:
            result = mcp_tool_result(value)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id_value, "result": result}, headers=headers
        )

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "Clerk JWT, cng AgentGrant, or legacy cnd API key",
            "description": "Clerk session JWT, named cng_ AgentGrant, or legacy cnd_ agent key.",
        }
        security_schemes["ClerkBearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "Clerk session JWT",
            "description": "A Clerk session JWT; agent API keys cannot manage API keys.",
        }
        security_schemes["AgentGrantAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "mandate-bound cng_ Agent Grant",
            "description": "A live dedicated Agent Grant issued by a human for internal agent outreach.",
        }
        canonical_limit = canonical_document_max_utf8_bytes()
        canonical_write_paths = {
            "/v1/profiles",
            "/v1/resumes",
            "/v1/profiles/{handle}",
            "/v1/resumes/{slug}",
            "/v1/proposals",
        }
        canonical_description = (
            "Final canonical Profile/Resume Markdown is measured after LF normalization and "
            f"must be at most {canonical_limit} UTF-8 bytes; JSON Schema maxLength is not a byte proof."
        )
        for path, operations in schema.get("paths", {}).items():
            if path not in canonical_write_paths:
                continue
            for method in ("post", "put"):
                operation = operations.get(method)
                if not isinstance(operation, dict):
                    continue
                operation["x-connectmd-canonical-max-utf8-bytes"] = canonical_limit
                request_body = operation.get("requestBody")
                if not isinstance(request_body, dict):
                    continue
                for media in request_body.get("content", {}).values():
                    if not isinstance(media, dict):
                        continue
                    request_schema = media.get("schema")
                    if not isinstance(request_schema, dict):
                        continue
                    resolved_schemas: list[dict[str, Any]] = [request_schema]
                    reference = request_schema.get("$ref")
                    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                        component_name = reference.rsplit("/", 1)[-1]
                        component = components.get("schemas", {}).get(component_name)
                        if isinstance(component, dict):
                            resolved_schemas.append(component)
                    for resolved_schema in resolved_schemas:
                        markdown_schema = resolved_schema.get("properties", {}).get("markdown")
                        if isinstance(markdown_schema, dict):
                            markdown_schema["description"] = canonical_description
                            markdown_schema["x-connectmd-canonical-max-utf8-bytes"] = (
                                canonical_limit
                            )
        protected = {"post", "put", "delete"}
        protected_gets = {
            "/v1/me",
            "/v1/documents",
            "/v1/changes",
            "/v1/changes/recent",
            "/v1/contact-policy",
            "/v1/contact-requests/inbox",
            "/v1/agent-outreach/{request_id}",
            "/v1/proposals",
            "/v1/agent-grants",
            "/v1/applications",
            "/v1/applications/{application_id}",
            "/v1/organization-membership-invitations",
            "/v1/organizations/{organization_slug}/members",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/snapshot",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/snapshot.md",
            "/v1/connection-requests/inbox",
            "/v1/connections",
            "/v1/conversations",
            "/v1/conversations/{conversation_id}/messages",
            "/v1/notifications",
        }
        private_social_prefixes = (
            "/v1/connection-requests",
            "/v1/connections",
            "/v1/conversations",
            "/v1/notifications",
        )
        private_human_paths = {
            "/v1/applications",
            "/v1/applications/{application_id}",
            "/v1/applications/{application_id}/withdraw",
            "/v1/organizations/{organization_slug}/verification-submissions",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/{action}",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/snapshot",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/snapshot.md",
        }
        for path, operations in schema.get("paths", {}).items():
            if not path.startswith("/v1/"):
                continue
            for method, operation in operations.items():
                private_social = path.startswith(private_social_prefixes)
                private_human = (
                    private_social
                    or path in private_human_paths
                    or operation.get("x-connectmd-human-only") is True
                )
                optional_bearer = (
                    method == "get"
                    and (
                        path == "/v1/search"
                        or path in {"/v1/posts/{post_id}", "/v1/posts/{post_id}.md"}
                        or (
                            (path.startswith("/v1/profiles/") or path.startswith("/v1/resumes/"))
                            and "/versions" not in path
                        )
                    )
                ) or (method == "post" and path == "/v1/search/query")
                if optional_bearer:
                    operation["security"] = [{}, {"BearerAuth": []}]
                if not (method == "post" and path == "/v1/search/query") and (
                    method in protected
                    or "/versions" in path
                    or path == "/v1/api-keys"
                    or (method == "get" and path in protected_gets)
                ):
                    operation["security"] = (
                        [{"ClerkBearerAuth": []}]
                        if private_human or path.startswith(("/v1/api-keys", "/v1/agent-grants"))
                        else [{"BearerAuth": []}]
                    )
                    operation.setdefault("responses", {}).setdefault(
                        "401",
                        {
                            "description": (
                                "A signed-in Clerk human session is required for this private social operation."
                                if private_social
                                else (
                                    "A signed-in Clerk human session is required for this operation."
                                    if private_human
                                    else "Authentication is required or the credential is invalid."
                                )
                            )
                        },
                    )
                if private_human:
                    operation["security"] = [{"ClerkBearerAuth": []}]
                    operation["x-connectmd-human-only"] = True
                    operation.setdefault("responses", {}).setdefault(
                        "401",
                        {
                            "description": (
                                "A signed-in Clerk human session is required for this private social operation."
                                if private_social
                                else "A signed-in Clerk human session is required for this operation."
                            )
                        },
                    )
                if path == "/v1/agent-outreach" and method == "post":
                    operation["security"] = [{"AgentGrantAuth": []}]
                    operation["x-connectmd-mandate-bound-agent-grant"] = True
                if path == "/v1/agent-outreach/{request_id}" and method == "get":
                    operation["security"] = [
                        {"ClerkBearerAuth": []},
                        {"AgentGrantAuth": []},
                    ]
                    operation["x-connectmd-exact-origin-or-sender-human"] = True
                if operation.get("security"):
                    operation.setdefault("responses", {}).setdefault(
                        "503",
                        _error_response("Authentication verification is temporarily unavailable."),
                    )
        app.openapi_schema = schema
        return app.openapi_schema

    explicitly_configured_artifact_authority = settings.is_production or {
        "database_url",
        "storage_path",
    }.issubset(settings.model_fields_set)
    app.state.artifact_reconciler = ArtifactReconciler(
        app.state.store,
        settings.api_key_pepper or "",
        classify_artifact_descriptor,
        enabled=(
            explicitly_configured_artifact_authority
            and settings.api_key_pepper is not None
            and len(settings.api_key_pepper.encode("utf-8")) >= 16
        ),
        classify_incomplete=classify_incomplete_artifact_stage,
    )
    app.openapi = custom_openapi  # type: ignore[method-assign]
    app.state.engine = build_engine(settings)
    app.state.session_factory = build_session_factory(settings, app.state.engine)
    return app


app = create_app()
