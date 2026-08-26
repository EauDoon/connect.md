import { afterEach, describe, expect, it, vi } from "vitest";

import { appendCursorPage as appendPrivateCursorPage } from "../lib/cursor-page";
import { createDelegation, decideAgentProposal, emergencyStopDelegations, listAgentProposals, listAgentProposalsForSubject, listDelegationAudit, listDelegations, listOwnedDocumentOptions, listOwnedDocumentPageForSubject, loadProposalBaseMarkdown } from "../lib/agent-api";
import { actOnOutreach, getContactPolicy, listOutreach, listOutreachForSubject, updateContactPolicy } from "../lib/outreach-api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt } from "../lib/logical-mutation";

const currentDelegationRecord = (overrides: Record<string, unknown> = {}) => ({ id: "grant-active", name: "Profile steward", prefix: "cnd_grant", scopes: ["documents:read"], mode: "proposal_only", resource: { type: "owner", id: null }, expires_at: "2099-09-01T00:00:00Z", created_at: "2026-08-03T00:00:00Z", revoked: false, ...overrides });

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("agent and outreach API contracts", () => {
it("requires a caller-owned key and strictly parses the first one-time grant result", async () => {
    expect(createDelegation.length).toBe(4);
    const response = { id: "grant-1", name: "Profile steward", prefix: "cnd_grant", scopes: ["documents:read", "inventory:read", "changes:read", "proposals:write"], mode: "proposal_only", resource: { type: "document", id: "doc-1" }, expires_at: "2026-09-01T00:00:00Z", key: "cnd_secret", created_at: "2026-08-03T00:00:00Z" };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createDelegation({ name: "Profile steward", mode: "proposal", expiresAt: "2026-09-01T00:00:00Z", resourceType: "document", resourceId: "doc-1" }, async () => "token", () => true, "grant-key");
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));

    expect(fetchMock.mock.calls[0][0]).toBe("/v1/agent-grants");
    expect(body).toEqual({ name: "Profile steward", mode: "proposal_only", expires_at: "2026-09-01T00:00:00Z", resource: { type: "document", id: "doc-1" }, scopes: ["documents:read", "inventory:read", "changes:read", "proposals:write"] });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("grant-key");
    expect(result).toMatchObject({ recoveryRequired: false, key: "cnd_secret", delegation: { mode: "proposal", resourceId: "doc-1", prefix: "cnd_grant" } });
    expect(result.delegation).not.toHaveProperty("secret");
  });

  it("strictly distinguishes secretless replay recovery from first creation", async () => {
    const safe = { id: "grant-1", name: "Profile steward", prefix: "cnd_grant", scopes: ["documents:read", "inventory:read", "changes:read", "proposals:write"], mode: "proposal_only", resource: { type: "document", id: "doc-1" }, expires_at: "2026-09-01T00:00:00Z", created_at: "2026-08-03T00:00:00Z" };
    const invalid: Array<{ body: Record<string, unknown>; replayed: boolean }> = [
      { body: { ...safe, recovery_required: true }, replayed: false },
      { body: { ...safe, recovery_required: true, key: null }, replayed: true },
      { body: { ...safe, recovery_required: true, token: "" }, replayed: true },
      { body: { ...safe, recovery_required: true, secret: "" }, replayed: true },
      { body: { ...safe, key: "cnd_secret", recovery_required: false }, replayed: false },
      { body: { ...safe, key: "cnd_secret" }, replayed: true },
      { body: { ...safe, key: "cnd_secret", token: "" }, replayed: false },
    ];
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...safe, recovery_required: true }), { status: 201, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "true" } }));
    for (const invalidResponse of invalid) fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(invalidResponse.body), { status: 201, headers: invalidResponse.replayed ? { "Content-Type": "application/json", "Idempotency-Replayed": "true" } : { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const recovery = await createDelegation({ name: "Profile steward", mode: "proposal", expiresAt: "2026-09-01T00:00:00Z", resourceType: "document", resourceId: "doc-1" }, async () => "token", () => true, "recovery-key");
    expect(recovery).toMatchObject({ recoveryRequired: true, delegation: { id: "grant-1", prefix: "cnd_grant" } });
    expect(recovery).not.toHaveProperty("key");

    for (let index = 0; index < invalid.length; index += 1) {
      let error: unknown;
      try {
        await createDelegation({ name: "Profile steward", mode: "proposal", expiresAt: "2026-09-01T00:00:00Z", resourceType: "document", resourceId: "doc-1" }, async () => "token", () => true, `invalid-${index}`);
      } catch (caught) {
        error = caught;
      }
      expect(error).toMatchObject({ status: 502, code: "server", message: "The agent-grant creation response could not be confirmed. Retry the unchanged creation." });
      expect(String(error)).not.toContain("cnd_secret");
    }
  });

  it("retains ambiguous and malformed grant attempts but clears a definitive 4xx", async () => {
    const safe = { id: "grant-1", name: "Profile steward", prefix: "cnd_grant", scopes: ["documents:read", "inventory:read", "changes:read", "proposals:write"], mode: "proposal_only", resource: { type: "document", id: "doc-1" }, expires_at: "2026-09-01T00:00:00Z", created_at: "2026-08-03T00:00:00Z", recovery_required: true };
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("lost acknowledgement"))
      .mockResolvedValueOnce(new Response(JSON.stringify(safe), { status: 201, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "true" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ key: "private" }), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "conflict" }), { status: 409, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const draft = { name: "Profile steward", mode: "proposal" as const, expiresAt: "2026-09-01T00:00:00Z", resourceType: "document" as const, resourceId: "doc-1" };
    const first = beginLogicalMutationAttempt(null, "human-a", { operation: "create-agent-grant", ...draft, scopes: ["documents:read", "inventory:read", "changes:read", "proposals:write"] }, () => "same-key");
    let lostAcknowledgement: unknown;
    try { await createDelegation(draft, async () => "token", () => true, first.idempotencyKey); } catch (error) { lostAcknowledgement = error; }
    const retained = settleLogicalMutationAttempt(first, lostAcknowledgement);
    expect(retained).toBe(first);
    await expect(createDelegation(draft, async () => "token", () => true, retained!.idempotencyKey)).resolves.toMatchObject({ recoveryRequired: true });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("same-key");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("same-key");

    const malformed = beginLogicalMutationAttempt(null, "human-a", { operation: "create-agent-grant", name: "Malformed", mode: "proposal", resourceType: "document", resourceId: "doc-1", expiresAt: draft.expiresAt, scopes: ["documents:read"] }, () => "malformed-key");
    let malformedError: unknown;
    try { await createDelegation({ ...draft, name: "Malformed" }, async () => "token", () => true, malformed.idempotencyKey); } catch (error) { malformedError = error; }
    expect(malformedError).toMatchObject({ status: 502, code: "server" });
    expect(settleLogicalMutationAttempt(malformed, malformedError)).toBe(malformed);
    expect(String(malformedError)).not.toContain("private");

    const definitive = beginLogicalMutationAttempt(null, "human-a", { operation: "create-agent-grant", name: "Conflict", mode: "proposal" }, () => "conflict-key");
    let conflict: unknown;
    try { await createDelegation({ ...draft, name: "Conflict" }, async () => "token", () => true, definitive.idempotencyKey); } catch (error) { conflict = error; }
    expect(conflict).toMatchObject({ status: 409, code: "request" });
    const replacement = beginLogicalMutationAttempt(settleLogicalMutationAttempt(definitive, conflict), "human-a", { operation: "create-agent-grant", name: "Replacement", mode: "proposal" }, () => "replacement-key");
    expect(replacement.idempotencyKey).toBe("replacement-key");
  });

  it("does not dispatch outreach or grant mutations after the signed-in subject changes during token retrieval", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    let grantCurrent = true;
    await expect(createDelegation({ name: "Profile steward", mode: "proposal", expiresAt: "2026-09-01T00:00:00Z", resourceType: "owner", resourceId: null }, async () => { grantCurrent = false; return "different-user-token"; }, () => grantCurrent, "subject-key")).rejects.toMatchObject({ code: "unauthorized" });
    let outreachCurrent = true;
    await expect(actOnOutreach("request-1", "accepted", null, async () => { outreachCurrent = false; return "different-user-token"; }, () => outreachCurrent, "subject-key")).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("adapts current contact-policy, recent-change, and inbox envelopes", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ allow_agent_requests: true, daily_request_limit: 7, version: 2, updated_at: "2026-08-03T00:00:00Z", etag: '"policy-2"' }), { status: 200, headers: { "Content-Type": "application/json", ETag: '"policy-2"' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ events: [{ sequence: 4, type: "document.updated", resource_type: "document", resource_id: "doc-1", actor_id: "grant-actor", actor_method: "agent_grant", grant_id: "grant-1", occurred_at: "2026-08-03T00:00:00Z", data: {} }], next_cursor: "cursor", has_more: false }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ requests: [{ id: "request-1", sender_owner_id: "owner-public", recipient_owner_id: "recipient-public", target_document_id: "doc-1", purpose: "Partnership", message: "Relevant request", status: "pending", sender_actor_id: "agent-public", sender_actor_method: "agent_grant", sender_grant_id: "grant-1", created_at: "2026-08-03T00:00:00Z", decided_at: null }], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "request-1", status: "accepted" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "token";

    await expect(getContactPolicy(token)).resolves.toMatchObject({ mode: "request", allowAgentMessages: true, dailyRequestLimit: 7, etag: '"policy-2"' });
    await expect(listDelegationAudit(token, () => true)).resolves.toEqual([expect.objectContaining({ id: "4", delegationId: "grant-1", action: "document.updated" })]);
    expect(fetchMock.mock.calls[1][0]).toBe("/v1/changes/recent");
    await expect(listOutreach(token)).resolves.toEqual({ nextCursor: null, threads: [expect.objectContaining({ id: "request-1", senderName: "Connect.md member", senderAgent: "agent-public", subject: "Partnership" })] });
    expect(fetchMock.mock.calls[2][0]).toBe("/v1/contact-requests/inbox?limit=25");
    await actOnOutreach("request-1", "accepted", null, token, () => true, "accept-key");
    expect(fetchMock.mock.calls[3][0]).toBe("/v1/contact-requests/request-1/accept");
    expect(fetchMock.mock.calls[3][1]?.body).toBe("{}");
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("Idempotency-Key")).toBe("accept-key");
  });

  it("uses only the dedicated query-free recent-change endpoint when it fails", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ detail: "recent changes unavailable" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listDelegationAudit(async () => "token", () => true)).rejects.toMatchObject({ status: 503 });
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/changes/recent");
  });

  it("requires explicit mutation keys and sends the canonical policy body", async () => {
    expect(updateContactPolicy.length).toBe(4);
    expect(actOnOutreach.length).toBe(6);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ allow_agent_requests: true, daily_request_limit: 7, version: 3, updated_at: "2026-08-03T00:00:00Z", etag: '"policy-3"' }), { status: 200, headers: { "Content-Type": "application/json", ETag: '"policy-3"' } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(updateContactPolicy({ mode: "request", allowAgentMessages: true, dailyRequestLimit: 7, representativeLabel: null, representativeUrl: null, etag: '"policy-2"' }, async () => "token", () => true, "policy-key")).resolves.toMatchObject({ mode: "request", allowAgentMessages: true, dailyRequestLimit: 7, etag: '"policy-3"' });
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/contact-policy");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ allow_agent_requests: true, daily_request_limit: 7 }));
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Idempotency-Key")).toBe("policy-key");
    expect(headers.get("If-Match")).toBe('"policy-2"');
    expect(headers.get("Authorization")).toBe("Bearer token");
  });

  it("requires a matching exact strong contact-policy ETag from the response header", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ allow_agent_requests: true, daily_request_limit: 7, version: 2, updated_at: "2026-08-03T00:00:00Z", etag: '"policy-2"' }), { status: 200, headers: { "Content-Type": "application/json", ETag: 'W/"policy-2"' } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getContactPolicy(async () => "token")).rejects.toMatchObject({ status: 502, code: "server" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retains the unchanged policy key after ambiguous parsing and clears it on a definitive 4xx", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ unexpected: "private-payload" }), { status: 200, headers: { "Content-Type": "application/json", ETag: '"policy-4"' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ allow_agent_requests: true, daily_request_limit: 7, version: 4, updated_at: "2026-08-03T00:00:00Z", etag: '"policy-4"' }), { status: 200, headers: { "Content-Type": "application/json", ETag: '"policy-4"' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "conflict" }), { status: 409, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ allow_agent_requests: true, daily_request_limit: 8, version: 5, updated_at: "2026-08-03T00:00:00Z", etag: '"policy-5"' }), { status: 200, headers: { "Content-Type": "application/json", ETag: '"policy-5"' } }));
    vi.stubGlobal("fetch", fetchMock);
    const policy = { mode: "request" as const, allowAgentMessages: true, dailyRequestLimit: 7, representativeLabel: null, representativeUrl: null, etag: '"policy-3"' };
    const policyIntent = { operation: "update-contact-policy", mode: policy.mode, allowAgentMessages: policy.allowAgentMessages, dailyRequestLimit: policy.dailyRequestLimit, etag: policy.etag };
    const unchanged = beginLogicalMutationAttempt(null, "subject-1", policyIntent, () => "policy-key");
    let parseError: unknown;
    try {
      await updateContactPolicy(policy, async () => "token", () => true, unchanged.idempotencyKey);
    } catch (error) {
      parseError = error;
    }
    expect(parseError).toMatchObject({ status: 502, code: "server" });
    expect(parseError).toHaveProperty("message", "The contact policy response could not be confirmed. Retry the unchanged update.");
    const retained = settleLogicalMutationAttempt(unchanged, parseError);
    expect(retained).toBe(unchanged);
    expect(String(parseError)).not.toContain("private-payload");
    await expect(updateContactPolicy(policy, async () => "token", () => true, unchanged.idempotencyKey)).resolves.toMatchObject({ dailyRequestLimit: 7 });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("policy-key");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("policy-key");
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("If-Match")).toBe('"policy-3"');
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("If-Match")).toBe('"policy-3"');
    expect(beginLogicalMutationAttempt(retained, "subject-1", { ...policyIntent, etag: '"policy-4"' }, () => "refreshed-policy-key").idempotencyKey).toBe("refreshed-policy-key");

    const definitive = beginLogicalMutationAttempt(null, "subject-1", policyIntent, () => "conflict-key");
    let conflictError: unknown;
    try {
      await updateContactPolicy(policy, async () => "token", () => true, definitive.idempotencyKey);
    } catch (error) {
      conflictError = error;
    }
    expect(conflictError).toMatchObject({ status: 409, code: "request" });
    const cleared = settleLogicalMutationAttempt(definitive, conflictError);
    expect(cleared).toBeNull();
    const replacement = beginLogicalMutationAttempt(cleared, "subject-1", { ...policyIntent, dailyRequestLimit: 8, etag: '"policy-4"' }, () => "replacement-key");
    expect(replacement.idempotencyKey).toBe("replacement-key");
    await expect(updateContactPolicy({ ...policy, dailyRequestLimit: 8, etag: '"policy-4"' }, async () => "token", () => true, replacement.idempotencyKey)).resolves.toMatchObject({ dailyRequestLimit: 8 });
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get("Idempotency-Key")).toBe("conflict-key");
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("Idempotency-Key")).toBe("replacement-key");
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("If-Match")).toBe('"policy-4"');
  });

  it("does not dispatch named private delegation readers after a token switch", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    let current = true;
    const switchingToken = async () => { current = false; return "different-user-token"; };

    await expect(listDelegations(switchingToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    current = true;
    await expect(listDelegationAudit(switchingToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    current = true;
    await expect(loadProposalBaseMarkdown({ kind: "profile", identifier: "ari-chen" }, switchingToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    current = true;
    await expect(listOwnedDocumentOptions(switchingToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    current = true;
    await expect(listAgentProposals(switchingToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("emergency stop uses the current revoked boolean and only deletes active grants", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ grants: [currentDelegationRecord({ id: "grant-revoked", revoked: true }), currentDelegationRecord()] }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await emergencyStopDelegations(async () => "token", () => true, (id) => `emergency-${id}`);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/v1/agent-grants/grant-active");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("emergency-grant-active");
  });

  it("fails closed on malformed or ambiguous delegation status before any emergency delete", async () => {
    for (const grant of [currentDelegationRecord({ revoked: false, status: "unknown" }), currentDelegationRecord({ revoked: "false" })]) {
      const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ grants: [grant] }), { status: 200, headers: { "Content-Type": "application/json" } }));
      vi.stubGlobal("fetch", fetchMock);

      await expect(emergencyStopDelegations(async () => "token", () => true, () => "should-not-send")).rejects.toThrow();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  });

  it("does not dispatch emergency deletes after the subject changes during the private list read", async () => {
    let current = true;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => {
      current = false;
      return new Response(JSON.stringify({ grants: [currentDelegationRecord()] }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(emergencyStopDelegations(async () => "token", () => current, () => "should-not-send")).rejects.toThrow("signed-in account changed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends a bounded report reason and rejects an empty report locally", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: "request-1", status: "reported" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "token";

    await actOnOutreach("request-1", "accepted", null, token, () => true, "accepted-key");
    await actOnOutreach("request-1", "rejected", null, token, () => true, "rejected-key");
    await actOnOutreach("request-1", "blocked", null, token, () => true, "blocked-key");
    await actOnOutreach("request-1", "reported", "Impersonation attempt", token, () => true, "reported-key");
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/contact-requests/request-1/accept");
    expect(fetchMock.mock.calls[0][1]?.body).toBe("{}");
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("accepted-key");
    expect(fetchMock.mock.calls[1][0]).toBe("/v1/contact-requests/request-1/reject");
    expect(fetchMock.mock.calls[1][1]?.body).toBe("{}");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("rejected-key");
    expect(fetchMock.mock.calls[2][0]).toBe("/v1/contact-requests/request-1/block");
    expect(fetchMock.mock.calls[2][1]?.body).toBe("{}");
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get("Idempotency-Key")).toBe("blocked-key");
    expect(fetchMock.mock.calls[3][0]).toBe("/v1/contact-requests/request-1/report");
    expect(fetchMock.mock.calls[3][1]?.body).toBe(JSON.stringify({ reason: "Impersonation attempt" }));
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("Idempotency-Key")).toBe("reported-key");
    await expect(actOnOutreach("request-1", "reported", null, token, () => true, "empty-report-key")).rejects.toThrow("report reason");
  });

  it("lists and decides proposals through the owner-review contract", async () => {
    const proposal = { id: "proposal-1", document_id: "doc-1", kind: "profile", identifier: "ari-chen", markdown: "---\nschema: connect.md/profile\n---\n", if_match: "\"sha256:abc\"", status: "pending", submitter_actor_id: "agent-grant:grant-1", submitter_grant_id: "grant-1", created_at: "2026-08-03T00:00:00Z", decided_at: null };
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ proposals: [proposal], next_cursor: "next-page" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...proposal, status: "accepted", decided_at: "2026-08-03T01:00:00Z" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "token";

    await expect(listAgentProposals(token, () => true)).resolves.toMatchObject({ nextCursor: "next-page", proposals: [expect.objectContaining({ id: "proposal-1", status: "pending" })] });
    await expect(decideAgentProposal("proposal-1", "accepted", token, () => true)).resolves.toMatchObject({ status: "accepted" });
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/proposals?limit=100");
    expect(fetchMock.mock.calls[1][0]).toBe("/v1/proposals/proposal-1/accept");
  });

  it("keeps private outreach and proposal cursor reads bound to the current subject", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    let outreachCurrent = true;
    await expect(listOutreachForSubject(async () => { outreachCurrent = false; return "different-user-token"; }, () => outreachCurrent)).rejects.toMatchObject({ code: "unauthorized" });
    let proposalCurrent = true;
    await expect(listAgentProposalsForSubject(async () => { proposalCurrent = false; return "different-user-token"; }, () => proposalCurrent)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(appendPrivateCursorPage([{ id: "known" }], { items: [{ id: "known" }, { id: "older" }], nextCursor: "delivered-cursor" }, "current-cursor", new Set(["delivered-cursor"]))).toEqual({ items: [{ id: "known" }, { id: "older" }], nextCursor: null, cursorDidNotProgress: true });
  });

  it("loads a strict bounded owned-document page with kind, cursor, signal, and subject binding", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ documents: [{ id: "doc-1", kind: "resume", identifier: "ari-resume", version: 3 }], next_cursor: "next" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await expect(listOwnedDocumentPageForSubject(async () => "token", () => true, { kind: "resume", cursor: "cursor", limit: 25, signal: controller.signal })).resolves.toEqual({ documents: [{ id: "doc-1", kind: "resume", identifier: "ari-resume", version: 3 }], nextCursor: "next" });
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/documents?limit=25&kind=resume&cursor=cursor");
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer token");
  });

  it("rejects malformed owned-document inventory instead of inventing kind, version, or cursor", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ documents: [{ id: "doc-1", kind: "unknown", identifier: "ari", version: 1 }], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ documents: [{ id: "doc-1", kind: "profile", identifier: "ari", version: 0 }], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ documents: [], next_cursor: "" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(listOwnedDocumentPageForSubject(async () => "token", () => true)).rejects.toThrow("document kind");
    await expect(listOwnedDocumentPageForSubject(async () => "token", () => true)).rejects.toThrow("document version");
    await expect(listOwnedDocumentPageForSubject(async () => "token", () => true)).rejects.toThrow("inventory cursor");
  });

  it("does not dispatch inventory after the signed-in subject changes during token retrieval", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    let current = true;
    await expect(listOwnedDocumentPageForSubject(async () => { current = false; return "different-user-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

