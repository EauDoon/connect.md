import { describe, expect, it } from "vitest";

import {
  AGENT_SCOPES,
  grantIsLive,
  mintAgentToken,
  scopeAllows,
  validateGrantDefinition,
} from "@/lib/network/agent-grants";
import { canRequestContact, contactTransition } from "@/lib/network/contact";
import { validateEmail, validateHandle, validatePassword } from "@/lib/network/identity";
import {
  constantTimeEquals,
  generateToken,
  hashPassword,
  tokenDigest,
  verifyPassword,
} from "@/lib/network/secrets";

describe("network identity validation", () => {
  it("accepts safe handles and normalizes nothing silently", () => {
    expect(validateHandle("ada-lovelace")).toEqual({ ok: true, handle: "ada-lovelace" });
    expect(validateHandle("a1")).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle("Ada")).toEqual({ ok: false, reason: expect.stringContaining("lowercase") });
    expect(validateHandle("-bad")).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle("bad--double")).toEqual({ ok: false, reason: expect.stringContaining("consecutive") });
    expect(validateHandle("network")).toEqual({ ok: false, reason: expect.stringContaining("reserved") });
    expect(validateHandle("md")).toEqual({ ok: false, reason: expect.stringContaining("3-30") });
    expect(validateHandle(42)).toEqual({ ok: false, reason: expect.stringContaining("string") });
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
  it("round-trips a password and never stores it", () => {
    const stored = hashPassword("GoodPassword123");
    expect(stored).not.toContain("GoodPassword123");
    expect(stored.startsWith("scrypt$")).toBe(true);
    expect(verifyPassword("GoodPassword123", stored)).toBe(true);
    expect(verifyPassword("WrongPassword123", stored)).toBe(false);
    expect(verifyPassword("goodpassword123", stored)).toBe(false);
  });

  it("produces unique salts for equal passwords", () => {
    const first = hashPassword("GoodPassword123");
    const second = hashPassword("GoodPassword123");
    expect(first).not.toBe(second);
  });

  it("rejects tampered stored hashes", () => {
    const stored = hashPassword("GoodPassword123");
    const parts = stored.split("$");
    parts[4] = Buffer.from("tampered-salt-bits").toString("base64");
    expect(verifyPassword("GoodPassword123", parts.join("$"))).toBe(false);
    expect(verifyPassword("GoodPassword123", "nonsense")).toBe(false);
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
