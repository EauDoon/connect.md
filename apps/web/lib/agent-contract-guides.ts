import { publicProtocolUrl, type PublicProtocolPath } from "@/lib/api";

export const AGENT_CONTRACT_PATHS = {
  discovery: "/llms.txt",
  fullGuide: "/llms-full.txt",
  openApi: "/openapi.json",
  tools: "/mcp",
} as const;

type AgentGuidePaths = {
  discovery: string;
  fullGuide: string;
  openApi: string;
  tools: string;
  a2aMessage: string;
  agentCard: string;
  agentOutreach: string;
  documents: string;
  changes: string;
};

const relativeAgentGuidePaths: AgentGuidePaths = {
  ...AGENT_CONTRACT_PATHS,
  a2aMessage: "/a2a/message:send",
  agentCard: "/.well-known/agent-card.json",
  agentOutreach: "/v1/agent-outreach",
  documents: "/v1/documents",
  changes: "/v1/changes",
};

function resolvedProtocolPath(path: PublicProtocolPath): string {
  return publicProtocolUrl(path) ?? path;
}

export function resolvedAgentContractPaths(): AgentGuidePaths {
  return {
    discovery: resolvedProtocolPath(AGENT_CONTRACT_PATHS.discovery),
    fullGuide: resolvedProtocolPath(AGENT_CONTRACT_PATHS.fullGuide),
    openApi: resolvedProtocolPath(AGENT_CONTRACT_PATHS.openApi),
    tools: resolvedProtocolPath(AGENT_CONTRACT_PATHS.tools),
    a2aMessage: resolvedProtocolPath(relativeAgentGuidePaths.a2aMessage as PublicProtocolPath),
    agentCard: resolvedProtocolPath(relativeAgentGuidePaths.agentCard as PublicProtocolPath),
    agentOutreach: resolvedProtocolPath(relativeAgentGuidePaths.agentOutreach as PublicProtocolPath),
    documents: resolvedProtocolPath(relativeAgentGuidePaths.documents as PublicProtocolPath),
    changes: resolvedProtocolPath(relativeAgentGuidePaths.changes as PublicProtocolPath),
  };
}

export type ContinuousAgentHandoffGrant = {
  name: string;
  scopes: readonly string[];
  mode: "proposal" | "direct";
  expiresAt: string;
  resourceType: "owner" | "document";
  resourceId: string | null;
};

export function internalAgentOutreachGuide(paths: AgentGuidePaths = resolvedAgentContractPaths()): string {
  return `connect.md internal agent outreach

HTTP: POST ${paths.agentOutreach}
Required: a live mandate-bound Agent Grant in a secure runtime; Idempotency-Key
Body fields: target_agent_handle, purpose, message

A2A: POST ${paths.a2aMessage}
Required: A2A-Version: 1.0; a live mandate-bound Agent Grant; Idempotency-Key
Message: ROLE_USER with one structured data part whose action is agent_outreach
Data fields: action, target_agent_handle, purpose, message

Discovery: ${paths.discovery}
A2A Agent Card: ${paths.agentCard}
OpenAPI: ${paths.openApi}

Each request is internal and consent-gated. A signed-in recipient human decides the request. No external agent URL is called.`;
}

export const INTERNAL_AGENT_OUTREACH_GUIDE = internalAgentOutreachGuide(relativeAgentGuidePaths);

export function continuousAgentHandoffGuide(paths: AgentGuidePaths = resolvedAgentContractPaths()): string {
  return `connect.md continuous agent handoff

Start with the published contracts:
- Discovery: ${paths.discovery}
- Full operating and safety contract: ${paths.fullGuide}
- Canonical HTTP contract: ${paths.openApi}
- Tool protocol: ${paths.tools}

Before any document work:
- The owner creates a named, scoped, expiring Agent Grant and supplies its credential through a separate runtime secret manager. This guide contains no credential.
- Verify the live actor, scopes, resource boundary, mode, and expiry before acting. A grant never expands its own authority.
- Use HTTP or MCP for Profile and Resume management. A2A is limited to implemented discovery and mediated outreach actions; it is not a document-management transport.

Continuous maintenance:
- Use GET ${paths.documents}?kind=&limit=&cursor= for the owner's authoritative inventory.
- Poll GET ${paths.changes}?limit=&cursor= and follow next_cursor. Public search is not a synchronization feed.
- Read the current canonical document and retain its ETag before proposing or updating.
- Use one Idempotency-Key only for retrying the exact same write. Do not blindly retry authorization, validation, policy, conflict, or stale-precondition failures.

Review and stop rules:
- proposal_only grants submit proposals; the signed-in owner explicitly accepts or rejects them. A proposal does not change a canonical document by itself.
- direct grants still require the listed scope, exact resource boundary, current ETag/If-Match precondition, and a fresh Idempotency-Key for each logical write.
- Stop immediately when the grant expires or is revoked. The owner can revoke it from Agent Grants; revocation is immediate.
- Do not publish posts, manage private social/recruitment/moderation data, change contact policy, or perform external delivery under a document-maintenance grant.`;
}

export const CONTINUOUS_AGENT_HANDOFF_GUIDE = continuousAgentHandoffGuide(relativeAgentGuidePaths);

export function continuousAgentHandoff(grant: ContinuousAgentHandoffGrant, paths: AgentGuidePaths = resolvedAgentContractPaths()): string {
  const resourceBoundary = grant.resourceType === "document"
    ? `exact document boundary: ${grant.resourceId ?? "unavailable"}`
    : "owner inventory boundary: current and future documents owned by the grant owner";
  const publicationMode = grant.mode === "proposal"
    ? "proposal_only: submit a candidate proposal and wait for the signed-in owner to accept or reject it; a proposal does not publish by itself."
    : "direct: a direct update still requires the listed scope, this exact resource boundary, the current ETag/If-Match precondition, and a fresh Idempotency-Key for each logical write.";

  return `connect.md grant-specific continuous-agent handoff

Published contracts:
- ${paths.discovery}
- ${paths.fullGuide}
- ${paths.openApi}
- ${paths.tools}

Grant boundary to verify before every action:
- name: ${grant.name}
- ${resourceBoundary}
- mode: ${grant.mode === "proposal" ? "proposal_only" : "direct"}
- scopes: ${grant.scopes.join(", ") || "none"}
- expires_at: ${grant.expiresAt}

How to operate:
- Read the current canonical document before proposing or updating, retain its ETag, and use one Idempotency-Key only for an identical retry.
- Synchronize with GET ${paths.documents}?kind=&limit=&cursor= and poll GET ${paths.changes}?limit=&cursor=; follow next_cursor and never use public search as a synchronization feed.
- ${publicationMode}
- HTTP and MCP manage canonical Profile and Resume documents. A2A is limited to its implemented discovery and mediated-outreach actions.

Stop rules:
- Stop immediately if this grant is expired or revoked, or if the current authority/precondition check fails.
- The owner can revoke this grant immediately from Agent Grants. Do not attempt to recreate, expand, or replace authority.
- This handoff contains no credential, hidden authority identifier, private source material, recipient control, or permission for external delivery.`;
}
