import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The account security routes are cookie-session only: they refuse cross-site
// and signed-out callers before touching the database, and a successful
// password change rotates this browser's cookie to the new session token.
const state = vi.hoisted(() => ({
  cookie: undefined as string | undefined,
  set: [] as Array<[string, string, Record<string, unknown>]>,
  session: null as null | { account: { id: string; email: string; handle: string; status: string; created_at: string }; sessionId: string },
  changeCalls: [] as unknown[],
  revokeCalls: [] as unknown[],
  changeError: null as Error | null,
}));

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (name === "connectmd_network_session" && state.cookie !== undefined ? { value: state.cookie } : undefined),
    set: (name: string, value: string, options: Record<string, unknown>) => { state.set.push([name, value, options]); },
  }),
}));
vi.mock("@/lib/network/db", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/network/db")>();
  return { ...actual, database: () => ({}) };
});
vi.mock("@/lib/network/auth-service", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/network/auth-service")>();
  return {
    ...actual,
    accountForSessionToken: async () => state.session,
    changePassword: async (_sql: unknown, session: unknown, input: unknown) => {
      state.changeCalls.push([session, input]);
      if (state.changeError) throw state.changeError;
      return { sessionToken: "rotated-session-token-0123456789", revokedSessions: 2 };
    },
    revokeOtherSessions: async (_sql: unknown, accountId: string, sessionId: string) => {
      state.revokeCalls.push([accountId, sessionId]);
      return 3;
    },
  };
});

const { AccountActionError } = await import("@/lib/network/auth-service");
const { POST: changePasswordRoute } = await import("@/app/api/network/v1/accounts/password/route");
const { POST: revokeOthersRoute } = await import("@/app/api/network/v1/sessions/revoke-others/route");

const ORIGIN = "https://network.example";
const signedIn = { account: { id: "acct-1", email: "ada@example.com", handle: "ada", status: "active", created_at: "2026-10-01T00:00:00.000Z" }, sessionId: "session-1" };

function post(url: string, body: unknown, headers: Record<string, string> = {}) {
  return new Request(`${ORIGIN}${url}`, {
    method: "POST",
    headers: { origin: ORIGIN, "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body),
  });
}

beforeEach(() => {
  vi.stubEnv("CONNECTMD_NETWORK_ORIGIN", ORIGIN);
  vi.stubEnv("CONNECTMD_NETWORK_DATABASE_URL", "postgresql://user:pass@127.0.0.1:5432/connectmd_test");
  state.cookie = "existing-session-token-0123456789";
  state.set = [];
  state.session = signedIn;
  state.changeCalls = [];
  state.revokeCalls = [];
  state.changeError = null;
});
afterEach(() => vi.unstubAllEnvs());

describe("POST /api/network/v1/accounts/password", () => {
  const valid = { currentPassword: "OldPassword123", newPassword: "NewPassword456" };

  it("refuses a cross-origin request before reading the session", async () => {
    const response = await changePasswordRoute(post("/api/network/v1/accounts/password", valid, { origin: "https://evil.example" }));
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ ok: false, reason: "origin-denied" });
    expect(state.changeCalls).toHaveLength(0);
  });

  it("answers 401 when signed out", async () => {
    state.session = null;
    const response = await changePasswordRoute(post("/api/network/v1/accounts/password", valid));
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ ok: false, reason: "unauthenticated" });
    expect(state.changeCalls).toHaveLength(0);
  });

  it("rejects an invalid body without changing anything", async () => {
    for (const body of [{ ...valid, extra: "x" }, "not json", { currentPassword: { nested: true }, newPassword: "x" }]) {
      const response = await changePasswordRoute(post("/api/network/v1/accounts/password", body));
      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ ok: false, reason: "request-body-invalid" });
    }
    expect(state.changeCalls).toHaveLength(0);
  });

  it("rotates the session cookie to the new token on success", async () => {
    const response = await changePasswordRoute(post("/api/network/v1/accounts/password", valid));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true, revokedSessions: 2 });
    expect(state.changeCalls).toEqual([[signedIn, valid]]);
    expect(state.set).toHaveLength(1);
    expect(state.set[0][0]).toBe("connectmd_network_session");
    expect(state.set[0][1]).toBe("rotated-session-token-0123456789");
    expect(state.set[0][2]).toMatchObject({ httpOnly: true, sameSite: "lax", path: "/" });
  });

  it("maps account errors to their status and keeps the cookie", async () => {
    for (const [code, status] of [["credentials", 401], ["invalid", 400], ["rate-limited", 429], ["conflict", 409]] as const) {
      state.changeError = new AccountActionError(code, `message for ${code}`);
      const response = await changePasswordRoute(post("/api/network/v1/accounts/password", valid));
      expect(response.status).toBe(status);
      expect(await response.json()).toEqual({ ok: false, reason: code, message: `message for ${code}` });
    }
    expect(state.set).toHaveLength(0);
  });
});

describe("POST /api/network/v1/sessions/revoke-others", () => {
  it("refuses cross-origin and signed-out callers", async () => {
    expect((await revokeOthersRoute(post("/api/network/v1/sessions/revoke-others", undefined, { origin: "https://evil.example" }))).status).toBe(403);
    state.session = null;
    const response = await revokeOthersRoute(post("/api/network/v1/sessions/revoke-others", undefined));
    expect(response.status).toBe(401);
    expect(state.revokeCalls).toHaveLength(0);
  });

  it("revokes every other session of the caller's account and reports the count", async () => {
    const response = await revokeOthersRoute(post("/api/network/v1/sessions/revoke-others", undefined));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true, revoked: 3 });
    expect(state.revokeCalls).toEqual([["acct-1", "session-1"]]);
    expect(state.set).toHaveLength(0);
  });
});
