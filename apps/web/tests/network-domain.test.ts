import { randomBytes, scryptSync } from "node:crypto";
import type postgres from "postgres";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AGENT_SCOPES,
  grantIsLive,
  mintAgentToken,
  scopeAllows,
  validateGrantDefinition,
} from "@/lib/network/agent-grants";
import { AUTH_STATE_PRUNE_BATCH, createSession, pruneExpiredAuthState } from "@/lib/network/auth-service";
import { canRequestContact, contactTransition } from "@/lib/network/contact";
import { normalizeHandleLookup, normalizeHandlePrefix, validateEmail, validateHandle, validatePassword } from "@/lib/network/identity";
import {
  constantTimeEquals,
  generateToken,
  hashPassword,
  scryptParametersAcceptable,
  tokenDigest,
  verifyPassword,
} from "@/lib/network/secrets";

describe("network identity validation", () => {
  it("accepts safe handles and normalizes nothing silently", () => {
    expect(validateHandle("ada-lovelace")).toEqual({ ok: true, handle: "ada-lovelace" });
    expect(validateHandle("a1")).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle("a")).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle("0")).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle("a".repeat(30))).toEqual({ ok: true, handle: "a".repeat(30) });
    expect(validateHandle("a".repeat(31))).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle("Ada")).toEqual({ ok: false, reason: expect.stringContaining("lowercase") });
    expect(validateHandle("-bad")).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle("bad--double")).toEqual({ ok: false, reason: expect.stringContaining("consecutive") });
    expect(validateHandle("network")).toEqual({ ok: false, reason: expect.stringContaining("reserved") });
    expect(validateHandle("md")).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle(42)).toEqual({ ok: false, reason: expect.stringContaining("string") });
  });

  it("normalizes public handle lookups without widening the handle alphabet", () => {
    expect(normalizeHandleLookup(" Ada-Lovelace ")).toBe("ada-lovelace");
    expect(normalizeHandleLookup("ada-lovelace")).toBe("ada-lovelace");
    for (const raw of ["", "a", "Ada!", "%", "ada_lovelace", "-ada", "a".repeat(31), 42, null]) {
      expect(normalizeHandleLookup(raw)).toBeNull();
    }
  });

  it("accepts only literal handle fragments as discovery prefixes", () => {
    expect(normalizeHandlePrefix(" Ada ")).toBe("ada");
    expect(normalizeHandlePrefix("a")).toBe("a");
    expect(normalizeHandlePrefix("-")).toBe("-");
    expect(normalizeHandlePrefix("a".repeat(30))).toBe("a".repeat(30));
    for (const raw of ["", "   ", "_", "%", "a_", "a%", "a b", "a".repeat(31), "x".repeat(200), undefined]) {
      expect(normalizeHandlePrefix(raw)).toBeNull();
    }
  });

  it("validates emails conservatively", () => {
    expect(validateEmail("ada@example.com")).toEqual({ ok: true, email: "ada@example.com" });
    expect(validateEmail("ada@")).toEqual({ ok: false, reason: expect.stringContaining("6-254") });
    expect(validateEmail("ada@example")).toEqual({ ok: false, reason: expect.stringContaining("plain address") });
    expect(validateEmail("a@b.c")).toEqual({ ok: false, reason: expect.stringContaining("6-254") });
  });

  it("requires password length and two character classes", () => {
    expect(validatePassword("short1A")).toEqual({ ok: false, reason: expect.stringContaining("at least 10") });
    expect(validatePassword("alllowercase")).toEqual({ ok: false, reason: expect.stringContaining("two of") });
    expect(validatePassword("GoodPassword123")).toEqual({ ok: true, password: "GoodPassword123" });
    expect(validatePassword("with symbols too")).toEqual({ ok: true, password: "with symbols too" });
  });
});

describe("password hashing", () => {
  it("round-trips a password and never stores it", async () => {
    const stored = await hashPassword("GoodPassword123");
    expect(stored).not.toContain("GoodPassword123");
    expect(stored.startsWith("scrypt$32768$8$1$")).toBe(true);
    expect(await verifyPassword("GoodPassword123", stored)).toBe(true);
    expect(await verifyPassword("WrongPassword123", stored)).toBe(false);
    expect(await verifyPassword("goodpassword123", stored)).toBe(false);
  });

  it("produces unique salts for equal passwords", async () => {
    const first = await hashPassword("GoodPassword123");
    const second = await hashPassword("GoodPassword123");
    expect(first).not.toBe(second);
  });

  it("keeps verifying hashes stored by the synchronous implementation", async () => {
    // Byte-for-byte what the previous scryptSync code stored, so existing
    // accounts keep signing in after the move to async derivation.
    const salt = randomBytes(16);
    const key = scryptSync("Légacy Password 1".normalize("NFKC"), salt, 64, { N: 2 ** 15, r: 8, p: 1, maxmem: 64 * 1024 * 1024 });
    const legacy = ["scrypt", "32768", "8", "1", salt.toString("base64"), key.toString("base64")].join("$");
    expect(await verifyPassword("Légacy Password 1", legacy)).toBe(true);
    expect(await verifyPassword("Legacy Password 1", legacy)).toBe(false);
  });

  it("rejects tampered stored hashes", async () => {
    const stored = await hashPassword("GoodPassword123");
    const parts = stored.split("$");
    parts[4] = Buffer.from("tampered-salt-bits").toString("base64");
    expect(await verifyPassword("GoodPassword123", parts.join("$"))).toBe(false);
    expect(await verifyPassword("GoodPassword123", "nonsense")).toBe(false);
  });

  it("refuses out-of-bounds stored parameters without deriving or throwing", async () => {
    const stored = await hashPassword("GoodPassword123");
    const withParams = (n: string, r: string, p: string) => {
      const parts = stored.split("$");
      parts[1] = n;
      parts[2] = r;
      parts[3] = p;
      return parts.join("$");
    };
    for (const [n, r, p] of [
      ["3", "8", "1"],
      [String(2 ** 30), "8", "1"],
      ["32768", "99", "1"],
      ["32768", "8", "99"],
      ["16384", "0", "1"],
      ["8192", "8", "1"],
      ["65536", "16", "1"],
      ["32768abc", "8", "1"],
      ["-32768", "8", "1"],
    ]) {
      await expect(verifyPassword("GoodPassword123", withParams(n!, r!, p!))).resolves.toBe(false);
    }
    expect(scryptParametersAcceptable(2 ** 15, 8, 1)).toBe(true);
    expect(scryptParametersAcceptable(2 ** 14, 16, 4)).toBe(true);
    expect(scryptParametersAcceptable(2 ** 16, 8, 1)).toBe(true);
    expect(scryptParametersAcceptable(2 ** 17, 8, 1)).toBe(false);
    expect(scryptParametersAcceptable(3 * 2 ** 14, 8, 1)).toBe(false);
  });
});

describe("tokens", () => {
  it("generates url-safe tokens with stable digests", () => {
    const token = generateToken();
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(tokenDigest(token)).toMatch(/^[0-9a-f]{64}$/);
    expect(tokenDigest(token)).toBe(tokenDigest(token));
    expect(constantTimeEquals(token, token)).toBe(true);
    expect(constantTimeEquals(token, generateToken())).toBe(false);
  });
});

describe("contact state machine", () => {
  it("allows only the documented transitions", () => {
    expect(contactTransition("pending", "recipient", "accept")).toEqual({ ok: true, status: "accepted" });
    expect(contactTransition("pending", "recipient", "reject")).toEqual({ ok: true, status: "rejected" });
    expect(contactTransition("pending", "requester", "revoke")).toEqual({ ok: true, status: "revoked" });
    expect(contactTransition("pending", "recipient", "block")).toEqual({ ok: true, status: "blocked" });
    expect(contactTransition("accepted", "recipient", "block")).toEqual({ ok: true, status: "blocked" });
    expect(contactTransition("accepted", "requester", "block")).toEqual({ ok: true, status: "blocked" });
    expect(contactTransition("accepted", "requester", "revoke")).toEqual({ ok: true, status: "revoked" });
    expect(contactTransition("accepted", "recipient", "revoke")).toEqual({ ok: true, status: "revoked" });

    expect(contactTransition("pending", "requester", "accept")).toEqual({ ok: false, reason: "wrong-actor" });
    expect(contactTransition("pending", "recipient", "revoke")).toEqual({ ok: false, reason: "wrong-actor" });
    expect(contactTransition("rejected", "recipient", "block")).toEqual({ ok: false, reason: "already-terminal" });
    expect(contactTransition("blocked", "recipient", "accept")).toEqual({ ok: false, reason: "already-terminal" });
    expect(contactTransition("accepted", "recipient", "accept")).toEqual({ ok: false, reason: "not-pending" });
  });

  it("blocks forbid any new request in either direction", () => {
    expect(canRequestContact(null, true)).toEqual({ ok: false, reason: "blocked" });
    expect(canRequestContact("rejected", true)).toEqual({ ok: false, reason: "blocked" });
    expect(canRequestContact("pending", false)).toEqual({ ok: false, reason: "pending-exists" });
    expect(canRequestContact("accepted", false)).toEqual({ ok: false, reason: "accepted-exists" });
    expect(canRequestContact("rejected", false)).toEqual({ ok: true });
    expect(canRequestContact("revoked", false)).toEqual({ ok: true });
  });
});

describe("agent grants", () => {
  it("validates scope subsets without wildcards", () => {
    expect(validateGrantDefinition({ name: "reader", scopes: ["profile:read"] })).toEqual({
      ok: true,
      definition: { name: "reader", scopes: ["profile:read"], expiresAt: null },
    });
    expect(validateGrantDefinition({ name: "x", scopes: [] }).ok).toBe(false);
    expect(validateGrantDefinition({ name: "x", scopes: ["*"] }).ok).toBe(false);
    expect(validateGrantDefinition({ name: "x", scopes: ["contacts:write"] }).ok).toBe(false);
    expect(validateGrantDefinition({ name: "", scopes: ["profile:read"] }).ok).toBe(false);
    expect(validateGrantDefinition({ name: "x", scopes: ["profile:read"], expiresAt: "not-a-date" }).ok).toBe(false);
    expect(validateGrantDefinition({ name: "x", scopes: ["profile:read"], expiresAt: "2000-01-01T00:00:00Z" }).ok).toBe(false);
  });

  it("grants live only while unrevoked and unexpired", () => {
    const now = new Date("2026-09-06T00:00:00Z");
    expect(grantIsLive({ expiresAt: null, revokedAt: null }, now)).toBe(true);
    expect(grantIsLive({ expiresAt: "2026-09-01T00:00:00Z", revokedAt: null }, now)).toBe(false);
    expect(grantIsLive({ expiresAt: null, revokedAt: "2026-09-01T00:00:00Z" }, now)).toBe(false);
  });

  it("checks scope membership exactly", () => {
    expect(scopeAllows({ scopes: ["profile:read", "profile:write"] }, "profile:read")).toBe(true);
    expect(scopeAllows({ scopes: ["profile:read"] }, "profile:write")).toBe(false);
    expect(scopeAllows({ scopes: ["contacts:read"] }, "profile:read")).toBe(false);
  });

  it("mints prefixed, single-use tokens", () => {
    const token = mintAgentToken();
    expect(token.startsWith("cnag_")).toBe(true);
    expect(token.length).toBeGreaterThan(40);
  });

  it("never offers messaging scopes to agents", () => {
    expect(AGENT_SCOPES).not.toContain("contacts:write");
    expect(AGENT_SCOPES).not.toContain("messages:send");
  });
});

describe("auth state pruning", () => {
  afterEach(() => vi.restoreAllMocks());

  function stubSql(failDeletes: boolean) {
    const statements: { text: string; values: unknown[] }[] = [];
    const sql = ((strings: TemplateStringsArray, ...values: unknown[]) => {
      const text = strings.join("$");
      statements.push({ text, values });
      if (failDeletes && text.includes("DELETE")) {
        return Promise.reject(Object.assign(new Error("lock timeout for ada@example.test"), { code: "55P03" }));
      }
      return Promise.resolve(Object.assign([], { count: text.includes("DELETE") ? 2 : 1 }));
    }) as unknown as postgres.Sql;
    return { sql, statements };
  }

  it("deletes only state past a one-day grace, in bounded batches", async () => {
    const { sql, statements } = stubSql(false);
    expect(await pruneExpiredAuthState(sql)).toEqual({ buckets: 2, sessions: 2 });
    expect(statements).toHaveLength(2);
    const [buckets, sessions] = statements;
    expect(buckets!.text).toContain("DELETE FROM network_auth_buckets");
    expect(buckets!.text.match(/window_started_at < now\(\) - INTERVAL '1 day'/g)).toHaveLength(2);
    expect(sessions!.text).toContain("DELETE FROM network_sessions");
    expect(sessions!.text.match(/expires_at < now\(\) - INTERVAL '1 day' OR revoked_at < now\(\) - INTERVAL '1 day'/g)).toHaveLength(2);
    expect(buckets!.values).toEqual([AUTH_STATE_PRUNE_BATCH]);
    expect(sessions!.values).toEqual([AUTH_STATE_PRUNE_BATCH]);
  });

  it("prunes after creating a session and never fails a sign-in because pruning failed", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { sql, statements } = stubSql(true);
    const token = await createSession(sql, "00000000-0000-4000-8000-000000000001");
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(statements[0]!.text).toContain("INSERT INTO network_sessions");
    expect(statements.slice(1).every((statement) => statement.text.includes("DELETE"))).toBe(true);
    expect(log).toHaveBeenCalledTimes(1);
    expect(log.mock.calls[0]).toEqual(["[connectmd-network] prune failed", { name: "Error", code: "55P03" }]);
    expect(JSON.stringify(log.mock.calls)).not.toContain("ada@example.test");
  });
});
