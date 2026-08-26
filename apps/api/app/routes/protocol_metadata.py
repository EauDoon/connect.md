from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.http.origin import public_base_url

router = APIRouter()


def protected_resource_metadata(request: Request, *, mcp: bool = False) -> dict[str, Any]:
    base = public_base_url(request)
    scopes_supported = [
        "documents:read",
        "documents:write",
        "search:read",
        "inventory:read",
        "changes:read",
        "contacts:read",
        "contacts:write",
        "proposals:write",
    ]
    if request.app.state.settings.recruiting_enabled:
        scopes_supported.extend(
            [
                "organizations:read",
                "organizations:write",
                "jobs:read",
                "jobs:write",
            ]
        )
    metadata: dict[str, Any] = {
        "resource": f"{base}/mcp" if mcp else base,
        "bearer_methods_supported": ["header"],
        "scopes_supported": scopes_supported,
        "resource_documentation": f"{base}/docs",
    }
    # Clerk is the configured authorization authority. connect.md exposes
    # resource metadata only and does not invent token/registration endpoints.
    if request.app.state.settings.clerk_issuer:
        metadata["authorization_servers"] = [request.app.state.settings.clerk_issuer]
    return metadata


@router.get(
    "/.well-known/oauth-protected-resource",
    tags=["protocols"],
    include_in_schema=False,
)
async def oauth_protected_resource(request: Request) -> dict[str, Any]:
    return protected_resource_metadata(request)


@router.get(
    "/.well-known/oauth-protected-resource/mcp",
    tags=["protocols"],
    include_in_schema=False,
)
async def oauth_protected_resource_mcp(request: Request) -> dict[str, Any]:
    return protected_resource_metadata(request, mcp=True)
