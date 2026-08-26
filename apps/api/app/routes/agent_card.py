from __future__ import annotations

import json
from hashlib import sha256

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.http.origin import public_base_url

router = APIRouter()


def _strong_etag(digest: str) -> str:
    return f'"sha256-{digest}"'


@router.get(
    "/.well-known/agent-card.json",
    tags=["protocols"],
    include_in_schema=False,
)
async def agent_card(request: Request) -> Response:
    base = public_base_url(request)
    card = {
        "name": "connect.md",
        "description": "Consent-aware discovery and management of canonical Markdown profiles and resumes; human-only professional posts are HTTP-only and are not Agent Card skills.",
        "supportedInterfaces": [
            {
                "url": f"{base}/a2a",
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }
        ],
        "provider": {"organization": "connect.md", "url": base},
        "version": request.app.version,
        "documentationUrl": f"{base}/llms-full.txt",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "securitySchemes": {
            "clerk_human": {
                "httpAuthSecurityScheme": {
                    "scheme": "Bearer",
                    "bearerFormat": "Clerk JWT",
                    "description": "A signed-in Clerk human credential. It may create mediated contact requests and read outreach status for requests sent by that human owner.",
                }
            },
            "eligible_agent_contact": {
                "httpAuthSecurityScheme": {
                    "scheme": "Bearer",
                    "bearerFormat": "cnd_ API key or non-mandate direct owner-bound cng_ Agent Grant",
                    "description": "An agent credential with contacts:write. A cng_ grant must be non-mandate, direct, and owner-bound; a mandate credential is not accepted for mediated contact.",
                }
            },
            "mandate_agent_grant": {
                "httpAuthSecurityScheme": {
                    "scheme": "Bearer",
                    "bearerFormat": "live mandate-bound cng_ Agent Grant",
                    "description": "A live cng_ Agent Grant restricted exactly to direct owner scope and the single contacts:write scope, bound to one active internal_contact_request mandate.",
                }
            },
        },
        "skills": [
            {
                "id": "search-public-documents",
                "name": "Search public profiles and resumes",
                "description": "Search the public projection with the bounded structured POST-equivalent q contract. Taxonomy values are discovery-only; the optional agent_capability=internal_contact_request filter does not authorize outreach.",
                "tags": ["search", "profile", "resume"],
                "examples": [
                    '{"action":"search","q":"payments","agent_capability":"internal_contact_request","seniority_ids":["esco:senior","esco:lead"]}'
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "discover-public-taxonomies",
                "name": "Discover public search taxonomies",
                "description": "List current public-v2 PostgreSQL taxonomy types and terms before search. Returned values are discovery-only and never establish identity, mandate, grant, consent, or outreach authority.",
                "tags": ["taxonomy", "search", "discovery"],
                "examples": [
                    '{"action":"list_taxonomies"}',
                    '{"action":"list_taxonomy_terms","taxonomy":"skill","q":"payments"}',
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "list-profile-agents",
                "name": "List active public profile agents",
                "description": "List bounded public Agent Identities for one current public profile. Discovery never authorizes contact or outreach.",
                "tags": ["agent-identity", "profile", "discovery"],
                "examples": ['{"action":"list_profile_agents","profile_handle":"ada-lovelace"}'],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "discover-public-agents",
                "name": "Discover active public agents",
                "description": "Retrieve one or list the bounded global directory of active Agent Identities linked to currently public owner-matched profiles. Discovery never establishes contact, mandate, grant, consent, or outreach authority.",
                "tags": ["agent-identity", "directory", "discovery"],
                "examples": [
                    '{"action":"get_agent_identity","agent_handle":"ada-agent"}',
                    '{"action":"list_agent_directory","q":"research","limit":20}',
                    '{"action":"list_agent_directory","profile_handle":"ada-lovelace"}',
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "request-mediated-contact",
                "name": "Request consent-based contact",
                "description": "Create an internal request under the target profile policy; never calls an external agent URL. Send a 1-128 visible-ASCII Idempotency-Key HTTP header. A signed-in Clerk human may call this skill; an agent needs an eligible non-mandate contacts:write credential, and any cng_ grant must be direct and owner-bound.",
                "tags": ["contact", "consent", "agent-grant"],
                "examples": [
                    '{"action":"contact_request","target_profile_handle":"ada-lovelace","purpose":"Interview","message":"Would you be open to an introduction?"}'
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
                "x-connectmd-required-http-headers": ["Idempotency-Key"],
                "securityRequirements": [
                    {"schemes": {"clerk_human": {"list": []}}},
                    {"schemes": {"eligible_agent_contact": {"list": ["contacts:write"]}}},
                ],
            },
            {
                "id": "send-mandate-bound-agent-outreach",
                "name": "Send mandate-bound agent outreach",
                "description": "Create a consent-gated internal request to an active public Agent Identity; no external agent URL is called. Send a 1-128 visible-ASCII Idempotency-Key HTTP header. This skill requires the exact live mandate-bound cng_ Agent Grant described by mandate_agent_grant; Clerk JWTs, cnd_ API keys, and ordinary cng_ grants are not accepted.",
                "tags": ["agent-identity", "contact", "consent", "mandate"],
                "examples": [
                    '{"action":"agent_outreach","target_agent_handle":"ada-agent","purpose":"Interview","message":"Would you be open to an introduction?"}'
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
                "x-connectmd-required-http-headers": ["Idempotency-Key"],
                "securityRequirements": [
                    {"schemes": {"mandate_agent_grant": {"list": ["contacts:write"]}}}
                ],
            },
            {
                "id": "get-mandate-bound-agent-outreach-status",
                "name": "Get mandate-bound agent outreach status",
                "description": "Read only the privacy-minimal consent status of outreach created by the exact active originating mandate. The sending signed-in Clerk human owner may also read that status; other humans, cnd_ API keys, and ordinary cng_ grants are not accepted.",
                "tags": ["agent-identity", "contact", "consent", "mandate", "status"],
                "examples": ['{"action":"get_agent_outreach_status","request_id":"REQUEST_ID"}'],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
                "securityRequirements": [
                    {"schemes": {"clerk_human": {"list": []}}},
                    {"schemes": {"mandate_agent_grant": {"list": ["contacts:write"]}}},
                ],
            },
        ],
    }
    serialized = json.dumps(card, separators=(",", ":"), sort_keys=True)
    etag = _strong_etag(sha256(serialized.encode("utf-8")).hexdigest())
    headers = {"Cache-Control": "public, max-age=3600", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(serialized, media_type="application/json", headers=headers)
