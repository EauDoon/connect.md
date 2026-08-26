"""Shared canonical-resource ancestry for retention-hold admission and erasure."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentGrant,
    AgentIdentity,
    AgentMandate,
    Application,
    Connection,
    Conversation,
    DocumentVersion,
    Job,
    Message,
    ModerationAppeal,
    ModerationAuditEvent,
    ModerationCase,
    ModerationDecision,
    OrganizationMembership,
    OrganizationVerification,
    OrganizationVerificationEvent,
    OrganizationVerificationEvidence,
    PostModerationEvent,
    PostReport,
    PostVersion,
)

Resource = tuple[str, str]


async def hold_ancestors(
    session: AsyncSession, resource_type: str, resource_id: str
) -> set[Resource]:
    """Return all canonical parents whose accepted hold preserves this row."""
    ancestors: set[Resource] = set()
    current: Resource | None = (resource_type, resource_id)
    row: Any
    while current is not None:
        resource_type, resource_id = current
        current = None
        if resource_type == "document_version":
            row = await session.get(DocumentVersion, resource_id)
            current = None if row is None else ("document", row.document_id)
        elif resource_type == "post_version":
            row = await session.get(PostVersion, resource_id)
            current = None if row is None else ("post", row.post_id)
        elif resource_type == "agent_mandate":
            row = await session.get(AgentMandate, resource_id)
            current = None if row is None else ("agent_identity", row.identity_id)
        elif resource_type == "agent_identity":
            row = await session.get(AgentIdentity, resource_id)
            current = None if row is None else ("document", row.profile_document_id)
        elif resource_type == "agent_grant":
            row = await session.get(AgentGrant, resource_id)
            if row is not None and row.mandate_id is not None:
                current = ("agent_mandate", row.mandate_id)
            elif (
                row is not None
                and row.resource_type in {"document", "organization"}
                and row.resource_id
            ):
                current = (row.resource_type, row.resource_id)
        elif resource_type == "connection":
            row = await session.get(Connection, resource_id)
            current = None if row is None else ("connection_request", row.connection_request_id)
        elif resource_type == "conversation":
            row = await session.get(Conversation, resource_id)
            current = None if row is None else ("connection", row.connection_id)
        elif resource_type == "message":
            row = await session.get(Message, resource_id)
            current = None if row is None else ("conversation", row.conversation_id)
        elif resource_type == "job":
            row = await session.get(Job, resource_id)
            current = None if row is None else ("organization", row.organization_id)
        elif resource_type == "application":
            row = await session.get(Application, resource_id)
            current = None if row is None else ("job", row.job_id)
        elif resource_type in {
            "organization_verification_evidence",
            "organization_verification_event",
        }:
            model: type[Any] = (
                OrganizationVerificationEvidence
                if resource_type == "organization_verification_evidence"
                else OrganizationVerificationEvent
            )
            row = await session.get(model, resource_id)
            current = None if row is None else ("organization_verification", row.verification_id)
        elif resource_type == "organization_verification":
            row = await session.get(OrganizationVerification, resource_id)
            current = None if row is None else ("organization", row.organization_id)
        elif resource_type == "organization_membership":
            row = await session.get(OrganizationMembership, resource_id)
            current = None if row is None else ("organization", row.organization_id)
        elif resource_type in {
            "moderation_decision",
            "moderation_appeal",
            "moderation_audit_event",
            "post_report",
        }:
            model = {
                "moderation_decision": ModerationDecision,
                "moderation_appeal": ModerationAppeal,
                "moderation_audit_event": ModerationAuditEvent,
                "post_report": PostReport,
            }[resource_type]
            row = await session.get(model, resource_id)
            current = None if row is None else ("moderation_case", row.case_id)
        elif resource_type == "moderation_case":
            row = await session.get(ModerationCase, resource_id)
            current = None if row is None else ("post", row.post_id)
        elif resource_type == "post_moderation_event":
            row = await session.get(PostModerationEvent, resource_id)
            current = None if row is None else ("post", row.post_id)
        if current is not None and current not in ancestors:
            ancestors.add(current)
        elif current is not None:
            break
    return ancestors


async def hold_descendants(
    session: AsyncSession, resource_type: str, resource_id: str
) -> set[Resource]:
    """Enumerate canonical descendants which must be locked before accepting a parent hold."""
    protected: set[Resource] = {(resource_type, resource_id)}
    pending = [(resource_type, resource_id)]
    while pending:
        parent_type, parent_id = pending.pop()
        children: list[Resource] = []
        if parent_type == "document":
            children += [
                ("document_version", row.id)
                for row in (
                    await session.scalars(
                        select(DocumentVersion).where(DocumentVersion.document_id == parent_id)
                    )
                ).all()
            ]
            children += [
                ("agent_identity", row.id)
                for row in (
                    await session.scalars(
                        select(AgentIdentity).where(AgentIdentity.profile_document_id == parent_id)
                    )
                ).all()
            ]
            children += [
                ("agent_grant", row.id)
                for row in (
                    await session.scalars(
                        select(AgentGrant).where(
                            AgentGrant.resource_type == "document",
                            AgentGrant.resource_id == parent_id,
                        )
                    )
                ).all()
            ]
        elif parent_type == "post":
            children += [
                ("post_version", row.id)
                for row in (
                    await session.scalars(
                        select(PostVersion).where(PostVersion.post_id == parent_id)
                    )
                ).all()
            ]
            children += [
                ("moderation_case", row.id)
                for row in (
                    await session.scalars(
                        select(ModerationCase).where(ModerationCase.post_id == parent_id)
                    )
                ).all()
            ]
            children += [
                ("post_moderation_event", row.id)
                for row in (
                    await session.scalars(
                        select(PostModerationEvent).where(PostModerationEvent.post_id == parent_id)
                    )
                ).all()
            ]
        elif parent_type == "agent_identity":
            children += [
                ("agent_mandate", row.id)
                for row in (
                    await session.scalars(
                        select(AgentMandate).where(AgentMandate.identity_id == parent_id)
                    )
                ).all()
            ]
        elif parent_type == "agent_mandate":
            children += [
                ("agent_grant", row.id)
                for row in (
                    await session.scalars(
                        select(AgentGrant).where(AgentGrant.mandate_id == parent_id)
                    )
                ).all()
            ]
        elif parent_type == "connection_request":
            children += [
                ("connection", row.id)
                for row in (
                    await session.scalars(
                        select(Connection).where(Connection.connection_request_id == parent_id)
                    )
                ).all()
            ]
        elif parent_type == "connection":
            children += [
                ("conversation", row.id)
                for row in (
                    await session.scalars(
                        select(Conversation).where(Conversation.connection_id == parent_id)
                    )
                ).all()
            ]
        elif parent_type == "conversation":
            children += [
                ("message", row.id)
                for row in (
                    await session.scalars(
                        select(Message).where(Message.conversation_id == parent_id)
                    )
                ).all()
            ]
        elif parent_type == "organization":
            children += [
                ("job", row.id)
                for row in (
                    await session.scalars(select(Job).where(Job.organization_id == parent_id))
                ).all()
            ]
            children += [
                ("organization_membership", row.id)
                for row in (
                    await session.scalars(
                        select(OrganizationMembership).where(
                            OrganizationMembership.organization_id == parent_id
                        )
                    )
                ).all()
            ]
            children += [
                ("organization_verification", row.id)
                for row in (
                    await session.scalars(
                        select(OrganizationVerification).where(
                            OrganizationVerification.organization_id == parent_id
                        )
                    )
                ).all()
            ]
            children += [
                ("agent_grant", row.id)
                for row in (
                    await session.scalars(
                        select(AgentGrant).where(
                            AgentGrant.resource_type == "organization",
                            AgentGrant.resource_id == parent_id,
                        )
                    )
                ).all()
            ]
        elif parent_type == "job":
            children += [
                ("application", row.id)
                for row in (
                    await session.scalars(
                        select(Application).where(Application.job_id == parent_id)
                    )
                ).all()
            ]
        elif parent_type == "organization_verification":
            children += [
                ("organization_verification_evidence", row.id)
                for row in (
                    await session.scalars(
                        select(OrganizationVerificationEvidence).where(
                            OrganizationVerificationEvidence.verification_id == parent_id
                        )
                    )
                ).all()
            ]
            children += [
                ("organization_verification_event", row.id)
                for row in (
                    await session.scalars(
                        select(OrganizationVerificationEvent).where(
                            OrganizationVerificationEvent.verification_id == parent_id
                        )
                    )
                ).all()
            ]
        elif parent_type == "moderation_case":
            children += [
                ("moderation_decision", row.id)
                for row in (
                    await session.scalars(
                        select(ModerationDecision).where(ModerationDecision.case_id == parent_id)
                    )
                ).all()
            ]
            children += [
                ("moderation_appeal", row.id)
                for row in (
                    await session.scalars(
                        select(ModerationAppeal).where(ModerationAppeal.case_id == parent_id)
                    )
                ).all()
            ]
            children += [
                ("moderation_audit_event", row.id)
                for row in (
                    await session.scalars(
                        select(ModerationAuditEvent).where(
                            ModerationAuditEvent.case_id == parent_id
                        )
                    )
                ).all()
            ]
            children += [
                ("post_report", row.id)
                for row in (
                    await session.scalars(select(PostReport).where(PostReport.case_id == parent_id))
                ).all()
            ]
        for child in children:
            if child not in protected:
                protected.add(child)
                pending.append(child)
    return protected
