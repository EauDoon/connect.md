import { afterEach, describe, expect, it, vi } from "vitest";

import { continuousAgentHandoff as componentContinuousAgentHandoff } from "@/components/agent-delegation-manager";
import { CONTINUOUS_AGENT_HANDOFF_GUIDE as componentContinuousGuide } from "@/components/agent-integration-panel";
import {
  AGENT_CONTRACT_PATHS,
  CONTINUOUS_AGENT_HANDOFF_GUIDE,
  continuousAgentHandoff,
  continuousAgentHandoffGuide,
  INTERNAL_AGENT_OUTREACH_GUIDE,
  internalAgentOutreachGuide,
  resolvedAgentContractPaths,
} from "@/lib/agent-contract-guides";
import type { AgentDelegation } from "@/lib/agent-api";

const proposalGrant: AgentDelegation = {
  id: "grant-private-id",
  name: "Profile steward",
  prefix: "cnd_private_prefix",
  scopes: ["documents:read", "inventory:read", "changes:read", "proposals:write"],
  mode: "proposal",
  status: "active",
  expiresAt: "2026-09-01T00:00:00Z",
  resourceType: "document",
  resourceId: "doc-private-boundary",
  createdAt: "2026-08-03T00:00:00Z",
  lastUsedAt: null,
};

const goldenOutreachGuide = `connect.md internal agent outreach

HTTP: POST /v1/agent-outreach
Required: a live mandate-bound Agent Grant in a secure runtime; Idempotency-Key
Body fields: target_agent_handle, purpose, message

A2A: POST /a2a/message:send
Required: A2A-Version: 1.0; a live mandate-bound Agent Grant; Idempotency-Key
Message: ROLE_USER with one structured data part whose action is agent_outreach
Data fields: action, target_agent_handle, purpose, message

Discovery: /llms.txt
A2A Agent Card: /.well-known/agent-card.json
OpenAPI: /openapi.json

Each request is internal and consent-gated. A signed-in recipient human decides the request. No external agent URL is called.`;

const goldenContinuousGuide = `connect.md continuous agent handoff

Start with the published contracts:
- Discovery: /llms.txt
- Full operating and safety contract: /llms-full.txt
- Canonical HTTP contract: /openapi.json
- Tool protocol: /mcp

Before any document work:
- The owner creates a named, scoped, expiring Agent Grant and supplies its credential through a separate runtime secret manager. This guide contains no credential.
- Verify the live actor, scopes, resource boundary, mode, and expiry before acting. A grant never expands its own authority.
- Use HTTP or MCP for Profile and Resume management. A2A is limited to implemented discovery and mediated outreach actions; it is not a document-management transport.

Continuous maintenance:
- Use GET /v1/documents?kind=&limit=&cursor= for the owner's authoritative inventory.
- Poll GET /v1/changes?limit=&cursor= and follow next_cursor. Public search is not a synchronization feed.
- Read the current canonical document and retain its ETag before proposing or updating.
- Use one Idempotency-Key only for retrying the exact same write. Do not blindly retry authorization, validation, policy, conflict, or stale-precondition failures.

Review and stop rules:
- proposal_only grants submit proposals; the signed-in owner explicitly accepts or rejects them. A proposal does not change a canonical document by itself.
- direct grants still require the listed scope, exact resource boundary, current ETag/If-Match precondition, and a fresh Idempotency-Key for each logical write.
- Stop immediately when the grant expires or is revoked. The owner can revoke it from Agent Grants; revocation is immediate.
- Do not publish posts, manage private social/recruitment/moderation data, change contact policy, or perform external delivery under a document-maintenance grant.`;

const goldenProposalHandoff = `connect.md grant-specific continuous-agent handoff

Published contracts:
- /llms.txt
- /llms-full.txt
- /openapi.json
- /mcp

Grant boundary to verify before every action:
- name: Profile steward
- exact document boundary: doc-private-boundary
- mode: proposal_only
- scopes: documents:read, inventory:read, changes:read, proposals:write
- expires_at: 2026-09-01T00:00:00Z

How to operate:
- Read the current canonical document before proposing or updating, retain its ETag, and use one Idempotency-Key only for an identical retry.
- Synchronize with GET /v1/documents?kind=&limit=&cursor= and poll GET /v1/changes?limit=&cursor=; follow next_cursor and never use public search as a synchronization feed.
- proposal_only: submit a candidate proposal and wait for the signed-in owner to accept or reject it; a proposal does not publish by itself.
- HTTP and MCP manage canonical Profile and Resume documents. A2A is limited to its implemented discovery and mediated-outreach actions.

Stop rules:
- Stop immediately if this grant is expired or revoked, or if the current authority/precondition check fails.
- The owner can revoke this grant immediately from Agent Grants. Do not attempt to recreate, expand, or replace authority.
- This handoff contains no credential, hidden authority identifier, private source material, recipient control, or permission for external delivery.`;

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("shared agent contract guides", () => {
  it("preserves the rendered and copied outreach guide while sharing its contract paths", () => {
    expect(INTERNAL_AGENT_OUTREACH_GUIDE).toBe(goldenOutreachGuide);
  });

  it("preserves the static maintenance guide and its existing component export byte for byte", () => {
    expect(AGENT_CONTRACT_PATHS).toEqual({
      discovery: "/llms.txt",
      fullGuide: "/llms-full.txt",
      openApi: "/openapi.json",
      tools: "/mcp",
    });
    expect(CONTINUOUS_AGENT_HANDOFF_GUIDE).toBe(goldenContinuousGuide);
    expect(componentContinuousGuide).toBe(goldenContinuousGuide);
  });

  it("preserves the proposal handoff builder and component export byte for byte", () => {
    expect(continuousAgentHandoff(proposalGrant)).toBe(goldenProposalHandoff);
    expect(componentContinuousAgentHandoff(proposalGrant)).toBe(goldenProposalHandoff);
  });

  it("keeps direct and missing-boundary fallbacks bounded without exposing grant secrets", () => {
    const handoff = componentContinuousAgentHandoff({
      ...proposalGrant,
      id: "never-render-this-id",
      prefix: "never-render-this-prefix",
      name: "Direct steward",
      scopes: [],
      mode: "direct",
      resourceId: null,
    });

    expect(handoff).toContain("exact document boundary: unavailable");
    expect(handoff).toContain("mode: direct");
    expect(handoff).toContain("scopes: none");
    expect(handoff).toContain("direct: a direct update still requires the listed scope");
    expect(handoff).toContain("current ETag/If-Match precondition");
    expect(handoff).toContain("fresh Idempotency-Key");
    expect(handoff).not.toContain("never-render-this-id");
    expect(handoff).not.toContain("never-render-this-prefix");
  });

  it("keeps owner-wide boundaries explicit without adding document or contact authority", () => {
    const handoff = continuousAgentHandoff({
      name: "Owner inventory steward",
      scopes: ["documents:read"],
      mode: "proposal",
      expiresAt: "2026-09-01T00:00:00Z",
      resourceType: "owner",
      resourceId: "ignored-document-id",
    });

    expect(handoff).toContain("owner inventory boundary: current and future documents owned by the grant owner");
    expect(handoff).not.toContain("ignored-document-id");
    expect(handoff).toContain("This handoff contains no credential");
    expect(handoff).not.toContain("This handoff grants permission for external delivery");
    expect(handoff).toContain("no credential, hidden authority identifier, private source material, recipient control, or permission for external delivery");
  });

  it("makes copied protocol guides absolute on a valid split API origin while keeping paths exact", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");
    const paths = resolvedAgentContractPaths();
    const outreach = internalAgentOutreachGuide();
    const continuous = continuousAgentHandoffGuide();

    expect(paths).toEqual({
      discovery: "https://api.connect.test/llms.txt",
      fullGuide: "https://api.connect.test/llms-full.txt",
      openApi: "https://api.connect.test/openapi.json",
      tools: "https://api.connect.test/mcp",
      a2aMessage: "https://api.connect.test/a2a/message:send",
      agentCard: "https://api.connect.test/.well-known/agent-card.json",
      agentOutreach: "https://api.connect.test/v1/agent-outreach",
      documents: "https://api.connect.test/v1/documents",
      changes: "https://api.connect.test/v1/changes",
    });
    expect(outreach).toContain("A2A: POST https://api.connect.test/a2a/message:send");
    expect(outreach).toContain("Discovery: https://api.connect.test/llms.txt");
    expect(outreach).toContain("HTTP: POST https://api.connect.test/v1/agent-outreach");
    expect(continuous).toContain("- Tool protocol: https://api.connect.test/mcp");
    expect(continuous).toContain("GET https://api.connect.test/v1/documents?kind=&limit=&cursor=");
    expect(continuous).toContain("GET https://api.connect.test/v1/changes?limit=&cursor=");
    expect(continuousAgentHandoff(proposalGrant)).toContain("https://api.connect.test/openapi.json");
    expect(continuousAgentHandoff(proposalGrant)).toContain("https://api.connect.test/v1/documents?kind=&limit=&cursor=");
  });

  it("falls back to relative guide routes for malformed API configuration", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/v1");

    expect(resolvedAgentContractPaths().discovery).toBe("/llms.txt");
    expect(internalAgentOutreachGuide()).toContain("A2A: POST /a2a/message:send");
    expect(continuousAgentHandoff(proposalGrant)).toContain("- /mcp");
  });
});
