from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.auth import (
    IMPERSONATION_READ_ONLY_CODE,
    Principal,
    optional_principal,
    require_principal,
)
from app.models import (
    AgentGrant,
    AgentIdentity,
    AgentMandate,
    ApiKey,
    ChangeEvent,
    Document,
    IdempotencyRecord,
    Job,
    Organization,
)

from .helpers import profile_markdown


def clerk(*, impersonated: bool = False) -> Principal:
    return Principal(
        subject="user_test",
        method="clerk_jwt",
        scopes=frozenset({"*"}),
        is_impersonated=impersonated,
    )


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def test_impersonated_clerk_is_denied_before_lookup_or_persistence(
    api_client,
) -> None:
    app, client = api_client
    as_principal(app, clerk(impersonated=True))

    denied_creates = (
        (
            "/v1/api-keys",
            "only an authenticated Clerk user can manage agent API keys",
        ),
        (
            "/v1/agent-grants",
            "only an authenticated Clerk user can create agent grants",
        ),
        (
            "/v1/agent-identities/missing-agent/mandates",
            "only an authenticated Clerk user can issue an agent mandate",
        ),
    )
    for path, detail in denied_creates:
        response = await client.post(
            path,
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == detail

    denied_inventory = (
        (
            "/v1/api-keys",
            "only an authenticated Clerk user can manage agent API keys",
        ),
        (
            "/v1/agent-grants",
            "only an authenticated Clerk user can list agent grants",
        ),
        (
            "/v1/agent-identities/missing-agent/mandates",
            "only an authenticated Clerk user can list agent mandates",
        ),
    )
    for path, detail in denied_inventory:
        response = await client.get(path)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == detail

    denied_revocations = (
        (
            "/v1/api-keys/missing-key",
            "only an authenticated Clerk user can manage agent API keys",
        ),
        (
            "/v1/agent-grants/missing-grant",
            "only an authenticated Clerk user can revoke agent grants",
        ),
        (
            "/v1/agent-identities/missing-agent/mandates/missing-mandate",
            "only an authenticated Clerk user can revoke an agent mandate",
        ),
    )
    for path, detail in denied_revocations:
        response = await client.delete(path)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == detail

    async with app.state.session_factory() as session:
        assert (await session.scalars(select(ApiKey))).all() == []
        assert (await session.scalars(select(AgentGrant))).all() == []
        assert (await session.scalars(select(AgentMandate))).all() == []
        assert (await session.scalars(select(IdempotencyRecord))).all() == []
        assert (await session.scalars(select(ChangeEvent))).all() == []


async def test_non_impersonated_clerk_can_issue_and_use_credentials_while_impersonation_cannot_retain_them(
    api_client,
) -> None:
    app, client = api_client
    as_principal(app, clerk())

    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "impersonation-profile-create-0001"},
    )
    assert profile.status_code == 201, profile.text
    identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "owner-agent",
            "display_name": "Owner Agent",
            "description": "Exercises bounded delegated authority.",
            "profile_handle": "ada-lovelace",
        },
        headers={"Idempotency-Key": "impersonation-identity-create-0001"},
    )
    assert identity.status_code == 201, identity.text

    api_key = await client.post(
        "/v1/api-keys",
        json={"scopes": ["documents:read"]},
        headers={"Idempotency-Key": "impersonation-api-key-create-0001"},
    )
    assert api_key.status_code == 201, api_key.text
    api_key_payload = api_key.json()
    assert api_key_payload["key"].startswith("cnd_")

    grant = await client.post(
        "/v1/agent-grants",
        json={
            "name": "Document reader",
            "mode": "direct",
            "resource": {"type": "owner"},
            "scopes": ["documents:read"],
            "expires_in_seconds": 86_400,
        },
        headers={"Idempotency-Key": "impersonation-agent-grant-create-0001"},
    )
    assert grant.status_code == 201, grant.text
    grant_payload = grant.json()
    assert grant_payload["key"].startswith("cng_")

    mandate = await client.post(
        "/v1/agent-identities/owner-agent/mandates",
        json={"expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        headers={"Idempotency-Key": "impersonation-mandate-create-0001"},
    )
    assert mandate.status_code == 201, mandate.text
    mandate_payload = mandate.json()
    mandate_grant = mandate_payload["grant"]
    assert mandate_grant["key"].startswith("cng_")

    as_principal(app, clerk(impersonated=True))
    denied_requests = (
        (
            "get",
            "/v1/api-keys",
            "only an authenticated Clerk user can manage agent API keys",
        ),
        (
            "delete",
            f"/v1/api-keys/{api_key_payload['id']}",
            "only an authenticated Clerk user can manage agent API keys",
        ),
        (
            "get",
            "/v1/agent-grants",
            "only an authenticated Clerk user can list agent grants",
        ),
        (
            "delete",
            f"/v1/agent-grants/{grant_payload['id']}",
            "only an authenticated Clerk user can revoke agent grants",
        ),
        (
            "get",
            "/v1/agent-identities/owner-agent/mandates",
            "only an authenticated Clerk user can list agent mandates",
        ),
        (
            "delete",
            f"/v1/agent-identities/owner-agent/mandates/{mandate_payload['id']}",
            "only an authenticated Clerk user can revoke an agent mandate",
        ),
    )
    for method, path, detail in denied_requests:
        response = await client.request(method, path)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == detail

    async with app.state.session_factory() as session:
        api_key_row = await session.get(ApiKey, api_key_payload["id"])
        grant_row = await session.get(AgentGrant, grant_payload["id"])
        mandate_row = await session.get(AgentMandate, mandate_payload["id"])
        mandate_grant_row = await session.get(AgentGrant, mandate_grant["id"])
        assert api_key_row is not None and api_key_row.revoked is False
        assert grant_row is not None and grant_row.revoked is False
        assert mandate_row is not None and mandate_row.status == "active"
        assert mandate_grant_row is not None and mandate_grant_row.revoked is False

    app.dependency_overrides.clear()
    credentials = (
        (api_key_payload["key"], "agent_api_key", None),
        (grant_payload["key"], "agent_grant", grant_payload["id"]),
        (mandate_grant["key"], "agent_grant", mandate_grant["id"]),
    )
    for raw_key, method, grant_id in credentials:
        response = await client.get(
            "/v1/me",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["authentication_method"] == method
        assert payload["grant_id"] == grant_id


async def test_real_clerk_impersonation_is_read_only_at_http_boundary(
    api_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = api_client

    async def verify_clerk(credential: str) -> Principal:
        if credential == "impersonated-clerk-token":
            return clerk(impersonated=True)
        if credential == "ordinary-clerk-token":
            return clerk()
        raise AssertionError(f"unexpected Clerk credential: {credential}")

    app.dependency_overrides.clear()
    monkeypatch.setattr(app.state.clerk, "verify", verify_clerk)

    impersonated_headers = {"Authorization": "Bearer impersonated-clerk-token"}
    read_response = await client.get("/v1/me", headers=impersonated_headers)
    assert read_response.status_code == 200, read_response.text
    assert read_response.json()["authentication_method"] == "clerk_jwt"

    mutation_requests = (
        ("post", "/v1/profiles", b"{", {"Content-Type": "application/json"}),
        ("put", "/v1/profiles/missing-profile", b"{", {"Content-Type": "application/json"}),
        ("delete", "/v1/api-keys/missing-key", None, {}),
    )
    for method, path, content, extra_headers in mutation_requests:
        headers = {**impersonated_headers, **extra_headers}
        response = await client.request(method, path, content=content, headers=headers)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    organization = await client.post(
        "/v1/organizations",
        json={"slug": "impersonated-org", "name": "Impersonated Org", "visibility": "private"},
        headers={**impersonated_headers, "Idempotency-Key": "impersonated-organization-0001"},
    )
    assert organization.status_code == 403, organization.text
    assert organization.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    agent_identity = await client.post(
        "/v1/agent-identities",
        json={
            "handle": "impersonated-agent",
            "display_name": "Impersonated Agent",
            "description": "This valid request must be denied at authentication.",
            "profile_handle": "ada-lovelace",
        },
        headers={**impersonated_headers, "Idempotency-Key": "impersonated-agent-0001"},
    )
    assert agent_identity.status_code == 403, agent_identity.text
    assert agent_identity.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    structured_search = await client.post(
        "/v1/search/query",
        json={},
        headers=impersonated_headers,
    )
    assert structured_search.status_code == 403, structured_search.text
    assert structured_search.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    mcp = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": "impersonated-mcp", "method": "initialize"},
        headers=impersonated_headers,
    )
    assert mcp.status_code == 403, mcp.text
    assert mcp.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    a2a = await client.post(
        "/a2a/message:send",
        json={"message": {"messageId": "impersonated-a2a", "parts": []}},
        headers={
            **impersonated_headers,
            "Content-Type": "application/a2a+json",
            "A2A-Version": "1.0",
        },
    )
    assert a2a.status_code == 403, a2a.text
    assert a2a.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    async with app.state.session_factory() as session:
        assert (await session.scalars(select(Organization))).all() == []
        assert (await session.scalars(select(AgentIdentity))).all() == []
        assert (await session.scalars(select(Document))).all() == []
        assert (await session.scalars(select(IdempotencyRecord))).all() == []
        assert (await session.scalars(select(ChangeEvent))).all() == []

    ordinary_headers = {"Authorization": "Bearer ordinary-clerk-token"}
    ordinary_read = await client.get("/v1/me", headers=ordinary_headers)
    assert ordinary_read.status_code == 200, ordinary_read.text
    ordinary_create = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={**ordinary_headers, "Idempotency-Key": "ordinary-clerk-profile-0001"},
    )
    assert ordinary_create.status_code == 201, ordinary_create.text


async def test_route_writes_remain_read_only_under_principal_override(api_client) -> None:
    app, client = api_client
    as_principal(app, clerk(impersonated=True))
    common_headers = {"Idempotency-Key": "route-impersonation-0001"}
    requests = (
        (
            "post",
            "/v1/profiles",
            {
                "json": {"markdown": profile_markdown(visibility="public")},
                "headers": common_headers,
            },
        ),
        (
            "post",
            "/v1/organizations",
            {
                "json": {
                    "slug": "route-impersonated-org",
                    "name": "Route Impersonated Org",
                    "visibility": "private",
                },
                "headers": {**common_headers, "Idempotency-Key": "route-impersonation-0002"},
            },
        ),
        (
            "post",
            "/v1/organizations/missing/verification-submissions",
            {
                "json": {
                    "evidence_kind": "other",
                    "artifact_content_type": "text/plain",
                    "artifact_base64": "dGVzdA==",
                },
                "headers": {**common_headers, "Idempotency-Key": "route-impersonation-0003"},
            },
        ),
        (
            "post",
            "/v1/organizations/missing/admins",
            {
                "json": {"member_profile_handle": "missing-profile", "role": "member"},
                "headers": {**common_headers, "Idempotency-Key": "route-impersonation-0004"},
            },
        ),
        (
            "post",
            "/v1/organizations/missing/memberships/missing/accept",
            {"headers": {**common_headers, "Idempotency-Key": "route-impersonation-0005"}},
        ),
        (
            "delete",
            "/v1/organizations/missing/memberships/missing",
            {"headers": {**common_headers, "Idempotency-Key": "route-impersonation-0006"}},
        ),
        (
            "put",
            "/v1/contact-policy",
            {
                "json": {"allow_agent_requests": True, "daily_request_limit": 5},
                "headers": {
                    **common_headers,
                    "Idempotency-Key": "route-impersonation-0007",
                    "If-Match": '"policy-0"',
                },
            },
        ),
        (
            "post",
            "/v1/contact-requests/missing/accept",
            {"headers": {**common_headers, "Idempotency-Key": "route-impersonation-0008"}},
        ),
        (
            "post",
            "/v1/proposals/missing/reject",
            {"headers": {**common_headers, "Idempotency-Key": "route-impersonation-0009"}},
        ),
    )
    for method, path, kwargs in requests:
        response = await client.request(method, path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    async with app.state.session_factory() as session:
        assert (await session.scalars(select(Organization))).all() == []
        assert (await session.scalars(select(AgentIdentity))).all() == []
        assert (await session.scalars(select(Document))).all() == []
        assert (await session.scalars(select(IdempotencyRecord))).all() == []
        assert (await session.scalars(select(ChangeEvent))).all() == []


async def test_organization_and_job_writes_reject_impersonation_before_lookup_or_mutation(
    api_client,
) -> None:
    app, client = api_client
    as_principal(app, clerk())
    organization = await client.post(
        "/v1/organizations",
        json={
            "slug": "impersonation-route-guard",
            "name": "Impersonation Route Guard",
            "visibility": "private",
        },
        headers={"Idempotency-Key": "impersonation-route-guard-org-0001"},
    )
    assert organization.status_code == 201, organization.text
    job = await client.post(
        "/v1/organizations/impersonation-route-guard/jobs",
        json={
            "slug": "existing-role",
            "title": "Existing Role",
            "description": "A durable job used to prove the impersonation guard runs first.",
        },
        headers={"Idempotency-Key": "impersonation-route-guard-job-0001"},
    )
    assert job.status_code == 201, job.text

    organization_id = organization.json()["id"]
    job_id = job.json()["id"]
    async with app.state.session_factory() as session:
        organization_before = await session.get(Organization, organization_id)
        job_before = await session.get(Job, job_id)
        assert organization_before is not None
        assert job_before is not None
        organization_state_before = (
            organization_before.name,
            organization_before.visibility,
            organization_before.version,
            organization_before.updated_at,
        )
        job_state_before = (
            job_before.title,
            job_before.status,
            job_before.version,
            job_before.updated_at,
        )
        change_event_ids_before = (
            await session.scalars(select(ChangeEvent.sequence).order_by(ChangeEvent.sequence))
        ).all()

    as_principal(app, clerk(impersonated=True))
    denied_keys = (
        "impersonation-existing-org-update-0001",
        "impersonation-missing-org-update-0001",
        "impersonation-existing-job-create-0001",
        "impersonation-missing-job-create-0001",
        "impersonation-existing-job-update-0001",
        "impersonation-missing-job-update-0001",
        "impersonation-existing-job-close-0001",
        "impersonation-missing-job-close-0001",
    )
    requests = (
        (
            "put",
            "/v1/organizations/impersonation-route-guard",
            {
                "json": {"name": "Impersonated Existing Organization Update"},
                "headers": {
                    "Idempotency-Key": denied_keys[0],
                    "If-Match": organization.headers["etag"],
                },
            },
        ),
        (
            "put",
            "/v1/organizations/missing-impersonation-route-guard",
            {
                "json": {"name": "Impersonated Missing Organization Update"},
                "headers": {"Idempotency-Key": denied_keys[1], "If-Match": '"missing"'},
            },
        ),
        (
            "post",
            "/v1/organizations/impersonation-route-guard/jobs",
            {
                "json": {
                    "slug": "impersonated-existing-create",
                    "title": "Impersonated Existing Create",
                    "description": "This must be rejected before organization lookup.",
                },
                "headers": {"Idempotency-Key": denied_keys[2]},
            },
        ),
        (
            "post",
            "/v1/organizations/missing-impersonation-route-guard/jobs",
            {
                "json": {
                    "slug": "impersonated-missing-create",
                    "title": "Impersonated Missing Create",
                    "description": "This must be rejected before organization lookup.",
                },
                "headers": {"Idempotency-Key": denied_keys[3]},
            },
        ),
        (
            "put",
            "/v1/organizations/impersonation-route-guard/jobs/existing-role",
            {
                "json": {"title": "Impersonated Existing Job Update"},
                "headers": {"Idempotency-Key": denied_keys[4], "If-Match": job.headers["etag"]},
            },
        ),
        (
            "put",
            "/v1/organizations/missing-impersonation-route-guard/jobs/missing-role",
            {
                "json": {"title": "Impersonated Missing Job Update"},
                "headers": {"Idempotency-Key": denied_keys[5], "If-Match": '"missing"'},
            },
        ),
        (
            "post",
            "/v1/organizations/impersonation-route-guard/jobs/existing-role/lifecycle/close",
            {
                "headers": {"Idempotency-Key": denied_keys[6], "If-Match": job.headers["etag"]},
            },
        ),
        (
            "post",
            "/v1/organizations/missing-impersonation-route-guard/jobs/missing-role/lifecycle/close",
            {
                "headers": {"Idempotency-Key": denied_keys[7], "If-Match": '"missing"'},
            },
        ),
    )
    for method, path, kwargs in requests:
        response = await client.request(method, path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"] == IMPERSONATION_READ_ONLY_CODE

    async with app.state.session_factory() as session:
        organization_after = await session.get(Organization, organization_id)
        job_after = await session.get(Job, job_id)
        assert organization_after is not None
        assert job_after is not None
        assert (
            organization_after.name,
            organization_after.visibility,
            organization_after.version,
            organization_after.updated_at,
        ) == organization_state_before
        assert (
            job_after.title,
            job_after.status,
            job_after.version,
            job_after.updated_at,
        ) == job_state_before
        assert (
            await session.scalars(
                select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key.in_(denied_keys))
            )
        ).all() == []
        assert (
            await session.scalars(select(ChangeEvent.sequence).order_by(ChangeEvent.sequence))
        ).all() == change_event_ids_before
