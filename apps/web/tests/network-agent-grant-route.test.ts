import { beforeEach, describe, expect, it, vi } from "vitest";

// GET /api/network/v1/agent/grant lets an agent introspect its own grant. It
// needs no scope, answers 401 with a Bearer challenge for any token that does
// not resolve, and never echoes token material.
const state = vi.hoisted(() => ({ agent: null as null | Record<string, unknown>, headers: [] as Array<string | null> }));

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("@/lib/network/db", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/network/db")>();
  return { ...actual, database: () => ({}) };
});
vi.mock("@/lib/network/agent-service", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/network/agent-service")>();
  return {
    ...actual,
    resolveAgentToken: async (_sql: unknown, header: string | null) => {
      state.headers.push(header);
      return state.agent;
    },
  };
});

const { GET } = await import("@/app/api/network/v1/agent/grant/route");
const TOKEN = "cnag_secret-token-value-0123456789abcdef";

function get(authorization?: string) {
  return GET(new Request("https://network.example/api/network/v1/agent/grant", { headers: authorization ? { authorization } : {} }));
}

beforeEach(() => {
  state.agent = null;
  state.headers = [];
});

describe("agent grant introspection", () => {
  it("challenges a missing, unknown, revoked, or expired token with 401", async () => {
    for (const header of [undefined, "Bearer cnag_unknown-token", `Basic ${TOKEN}`]) {
      const response = await get(header);
      expect(response.status).toBe(401);
      expect(response.headers.get("www-authenticate")).toBe("Bearer");
      expect(await response.json()).toEqual({ ok: false, reason: "agent-unauthorized" });
    }
    expect(state.headers).toEqual([null, "Bearer cnag_unknown-token", `Basic ${TOKEN}`]);
  });

  it("describes the grant to any live token without returning token material", async () => {
    state.agent = {
      grantId: "grant-1", accountId: "acct-1", accountHandle: "ada", scopes: ["contacts:read"],
      name: "inbox-reader", createdAt: "2026-10-01T00:00:00.000Z", expiresAt: "2026-12-30T00:00:00.000Z",
    };
    const response = await get(`Bearer ${TOKEN}`);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const body = await response.json();
    expect(body).toEqual({
      ok: true,
      account: { handle: "ada" },
      grant: { name: "inbox-reader", scopes: ["contacts:read"], createdAt: "2026-10-01T00:00:00.000Z", expiresAt: "2026-12-30T00:00:00.000Z" },
    });
    const text = JSON.stringify(body);
    for (const secret of [TOKEN, "cnag_", "grant-1", "acct-1"]) expect(text).not.toContain(secret);
  });

  it("reports a legacy grant without expiry as null", async () => {
    state.agent = { grantId: "g", accountId: "a", accountHandle: "ada", scopes: ["profile:read"], name: "legacy", createdAt: "2026-05-01T00:00:00.000Z", expiresAt: null };
    expect((await (await get(`Bearer ${TOKEN}`)).json()).grant.expiresAt).toBeNull();
  });
});
