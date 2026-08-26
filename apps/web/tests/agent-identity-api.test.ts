import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";

import { createAgentIdentity, fetchPublicAgentIdentity, issueAgentMandate, listAgentIdentities, listAgentMandates, listPublicAgentDirectory, listPublicProfileAgentIdentities, revokeAgentMandate, withdrawAgentIdentity } from "../lib/agent-identity-api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt } from "../lib/logical-mutation";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

const publicIdentity = { handle: "ari-agent", display_name: "Ari's agent", description: "Owner-attested representative for internal contact requests.", profile_handle: "ari-chen", capabilities: ["internal_contact_request"] };
const ownedIdentity = { ...publicIdentity, status: "active", created_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z" };
const mandate = { id: "mandate-1", scope: "internal_contact_request", status: "active", expires_at: "2026-08-10T00:00:00Z", grant_prefix: "cng_mandate" };

function configure(response: unknown, status = 200, extraHeaders: Record<string, string> = {}) {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
  vi.stubGlobal("crypto", { randomUUID: () => "request-1" });
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response), { status, headers: { "Content-Type": "application/json", ...extraHeaders } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Agent Identity API contracts", () => {
  it("reads the public identity without credentials or mandate state", async () => {
    const fetchMock = configure(publicIdentity);
    await expect(fetchPublicAgentIdentity("ari-agent")).resolves.toEqual({ handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen", capabilities: ["internal_contact_request"] });
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.connect.test/v1/agent-identities/ari-agent");
  });

  it("reads bounded public directory and profile identity pages using only safe fields", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const directoryIdentity = { ...publicIdentity, owner_id: "must-not-reach-ui", status: "active", mandate: "must-not-reach-ui" };
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ identities: [directoryIdentity], next_cursor: "signed-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ identities: [directoryIdentity], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listPublicAgentDirectory({ q: "payments", profileHandle: "ari-chen", cursor: "signed-current" })).resolves.toEqual({ identities: [{ handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen", capabilities: ["internal_contact_request"] }], nextCursor: "signed-next" });
    await expect(listPublicProfileAgentIdentities("ari-chen")).resolves.toMatchObject({ identities: [expect.objectContaining({ handle: "ari-agent" })], nextCursor: null });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.connect.test/v1/agent-directory?q=payments&profile_handle=ari-chen&limit=20&cursor=signed-current",
      "https://api.connect.test/v1/profiles/ari-chen/agent-identities?limit=20"
    ]);
  });

  it.each(["", "   ", "x".repeat(501)])("rejects an invalid caller Agent Directory cursor before fetch", async (cursor) => {
    const fetchMock = configure({ identities: [], next_cursor: null });

    await expect(listPublicAgentDirectory({ cursor })).rejects.toMatchObject({ status: 422, code: "request" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["", "   ", "x".repeat(501)])("rejects an invalid service Agent Directory continuation cursor", async (nextCursor) => {
    configure({ identities: [], next_cursor: nextCursor });

    await expect(listPublicAgentDirectory()).rejects.toMatchObject({ code: "server" });
  });

  it("uses the human owner list, public-profile create, and private inventory contracts", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    vi.stubGlobal("crypto", { randomUUID: () => "request-1" });
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify([ownedIdentity]), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(publicIdentity), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify([mandate]), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";

    await expect(listAgentIdentities(token, () => true)).resolves.toEqual([expect.objectContaining({ handle: "ari-agent", status: "active" })]);
    await expect(createAgentIdentity({ handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen" }, token, () => true, "identity-create-0001")).resolves.toMatchObject({ handle: "ari-agent", profileHandle: "ari-chen" });
    await expect(listAgentMandates("ari-agent", token, () => true)).resolves.toEqual([expect.objectContaining({ id: "mandate-1", grantPrefix: "cng_mandate", status: "active" })]);

    expect(fetchMock.mock.calls[0][0]).toBe("https://api.connect.test/v1/agent-identities?limit=50");
    const [createUrl, createInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(createUrl).toBe("https://api.connect.test/v1/agent-identities");
    expect(new Headers(createInit.headers).get("Idempotency-Key")).toBe("identity-create-0001");
    expect(JSON.parse(String(createInit.body))).toEqual({ handle: "ari-agent", display_name: "Ari's agent", description: publicIdentity.description, profile_handle: "ari-chen" });
    expect(fetchMock.mock.calls[2][0]).toBe("https://api.connect.test/v1/agent-identities/ari-agent/mandates");
  });

  it("requires an explicit visible-ASCII key and never generates one for create or withdraw", async () => {
    const fetchMock = configure(publicIdentity, 201);
    const token = async () => "clerk-token";
    await expect(createAgentIdentity({ handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen" }, token, () => true, undefined as never)).rejects.toMatchObject({ status: 400, code: "request" });
    await expect(withdrawAgentIdentity("ari-agent", token, () => true, "bad key")).rejects.toMatchObject({ status: 400, code: "request" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["owner identifier", { ...publicIdentity, owner_id: "private-owner" }],
    ["credential field", { ...publicIdentity, key: "cng_secret" }],
    ["recovery field", { ...publicIdentity, recovery_required: true }],
    ["invalid handle", { ...publicIdentity, handle: "Ari-Agent" }],
    ["oversized display name", { ...publicIdentity, display_name: "x".repeat(101) }],
    ["wrong capability", { ...publicIdentity, capabilities: ["contacts:write"] }],
  ] as const)("rejects %s from a successful create response as server ambiguity", async (_label, body) => {
    configure(body, 201);
    await expect(createAgentIdentity({ handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen" }, async () => "clerk-token", () => true, "identity-strict-0001")).rejects.toMatchObject({ status: 502, code: "server" });
  });

  it("accepts only the exact 201 create contract and an absent or literal replay marker", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(publicIdentity), { status: 201, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "true" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(publicIdentity), { status: 201, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "false" } }));
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    vi.stubGlobal("fetch", fetchMock);
    const input = { handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen" };
    await expect(createAgentIdentity(input, async () => "clerk-token", () => true, "identity-replay-0001")).resolves.toMatchObject({ handle: "ari-agent" });
    await expect(createAgentIdentity(input, async () => "clerk-token", () => true, "identity-replay-0001")).rejects.toMatchObject({ status: 502, code: "server" });
  });

  it("rejects a valid-looking create body under a non-201 success status", async () => {
    configure(publicIdentity, 200);
    await expect(createAgentIdentity({ handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen" }, async () => "clerk-token", () => true, "identity-status-0001")).rejects.toMatchObject({ status: 502, code: "server" });
  });

  it("retains the same logical key after malformed 201 success and reuses it on retry", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...publicIdentity, recovery_required: true }), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(publicIdentity), { status: 201, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "true" } }));
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    vi.stubGlobal("fetch", fetchMock);
    const input = { handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen" };
    const attempt = beginLogicalMutationAttempt(null, "human-a", { operation: "create-agent-identity", ...input }, () => "identity-retry-0001");
    let failure: unknown;
    try {
      await createAgentIdentity(input, async () => "clerk-token", () => true, attempt.idempotencyKey);
    } catch (error) {
      failure = error;
    }
    expect(failure).toMatchObject({ status: 502, code: "server" });
    const retry = settleLogicalMutationAttempt(attempt, failure);
    expect(retry?.idempotencyKey).toBe("identity-retry-0001");
    await expect(createAgentIdentity(input, async () => "clerk-token", () => true, retry!.idempotencyKey)).resolves.toMatchObject({ handle: "ari-agent" });
    expect(new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers).get("Idempotency-Key")).toBe("identity-retry-0001");
    expect(new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers).get("Idempotency-Key")).toBe("identity-retry-0001");
  });

  it("clears a logical key after a definitive create rejection", async () => {
    configure({ detail: "rejected" }, 409);
    const attempt = beginLogicalMutationAttempt(null, "human-a", { operation: "create-agent-identity", handle: "ari-agent" }, () => "identity-definitive-0001");
    let failure: unknown;
    try {
      await createAgentIdentity({ handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen" }, async () => "clerk-token", () => true, attempt.idempotencyKey);
    } catch (error) {
      failure = error;
    }
    expect(failure).toMatchObject({ status: 409 });
    expect(settleLogicalMutationAttempt(attempt, failure)).toBeNull();
  });

  it("returns a mandate secret only from a new issuance and treats idempotent recovery as secretless", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    vi.stubGlobal("crypto", { randomUUID: () => "request-1" });
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "mandate-1", scope: "internal_contact_request", expires_at: mandate.expires_at, grant: { prefix: "cng_mandate", key: "cng_secret_returned_once" } }), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...mandate, recovery_required: true }), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";

    await expect(issueAgentMandate("ari-agent", mandate.expires_at, token, () => true)).resolves.toEqual({ kind: "issued", mandate: { id: "mandate-1", scope: "internal_contact_request", status: "active", expiresAt: mandate.expires_at, grantPrefix: "cng_mandate" }, secret: "cng_secret_returned_once" });
    await expect(issueAgentMandate("ari-agent", mandate.expires_at, token, () => true)).resolves.toEqual({ kind: "recovery", mandate: { id: "mandate-1", scope: "internal_contact_request", status: "active", expiresAt: mandate.expires_at, grantPrefix: "cng_mandate" }, recoveryRequired: true });
    const [, issueInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(issueInit.headers).get("Idempotency-Key")).toBe("request-1");
    expect(JSON.parse(String(issueInit.body))).toEqual({ expires_at: mandate.expires_at });
  });

  it("does not dispatch identity or mandate mutations after an account transition", async () => {
    const fetchMock = configure({});
    let current = true;
    await expect(createAgentIdentity({ handle: "ari-agent", displayName: "Ari's agent", description: publicIdentity.description, profileHandle: "ari-chen" }, async () => { current = false; return "different-user-token"; }, () => current, "identity-subject-0001")).rejects.toMatchObject({ code: "unauthorized" });
    current = true;
    await expect(withdrawAgentIdentity("ari-agent", async () => { current = false; return "different-user-token"; }, () => current, "withdraw-subject-0001")).rejects.toMatchObject({ code: "unauthorized" });
    current = true;
    await expect(issueAgentMandate("ari-agent", mandate.expires_at, async () => { current = false; return "different-user-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not dispatch private identity or mandate readers after an account transition", async () => {
    const fetchMock = configure({});
    let current = true;
    const switchingToken = async () => { current = false; return "different-user-token"; };

    await expect(listAgentIdentities(switchingToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    current = true;
    await expect(listAgentMandates("ari-agent", switchingToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses guarded destructive routes for withdrawal and mandate revocation", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";
    await withdrawAgentIdentity("ari-agent", token, () => true, "identity-withdraw-0001");
    await revokeAgentMandate("ari-agent", "mandate-1", token, () => true);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.connect.test/v1/agent-identities/ari-agent",
      "https://api.connect.test/v1/agent-identities/ari-agent/mandates/mandate-1"
    ]);
    expect(new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers).get("Idempotency-Key")).toBe("identity-withdraw-0001");
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBeUndefined();
    expect(new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers).get("Content-Type")).toBeNull();
  });

  it("accepts only an empty 204 withdrawal and retains ambiguity for malformed success", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 204, headers: { "Idempotency-Replayed": "true" } }))
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204, headers: { "Idempotency-Replayed": "false" } }));
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";
    await expect(withdrawAgentIdentity("ari-agent", token, () => true, "withdraw-replay-0001")).resolves.toBeUndefined();
    await expect(withdrawAgentIdentity("ari-agent", token, () => true, "withdraw-invalid-0001")).rejects.toMatchObject({ status: 502, code: "server" });
    await expect(withdrawAgentIdentity("ari-agent", token, () => true, "withdraw-invalid-0002")).rejects.toMatchObject({ status: 502, code: "server" });
    expect(new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers).get("Idempotency-Key")).toBe("withdraw-replay-0001");
  });

  it("keeps mandate secrets out of browser persistence, URLs, and logs", () => {
    const manager = readFileSync(new URL("../components/agent-identity-manager.tsx", import.meta.url), "utf8");
    const integration = readFileSync(new URL("../components/agent-integration-panel.tsx", import.meta.url), "utf8");
    expect(manager).toContain("setSecret(null)");
    expect(manager).toContain("const identityMutationClaimSlotRef");
    expect(manager).toContain("claimLogicalMutation(identityMutationClaimSlotRef.current)");
    expect(manager).toContain("const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent() && identityClaim.isCurrent();");
    expect(manager).toContain("setIdentities((current) => current.filter((item) => item.handle !== identity.handle))");
    expect(manager).toContain("disabled={busy !== null}");
    expect(manager).not.toMatch(/localStorage|sessionStorage|URLSearchParams|console\./u);
    expect(integration).not.toMatch(/Authorization:\s*Bearer|cng_/u);
  });
});
