import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AGENT_SCOPES, DEFAULT_GRANT_TTL_DAYS, grantIsLive, validateGrantDefinition } from "../lib/network/agent-grants";
import {
  DEFAULT_GRANT_LIFETIME_DAYS,
  DEFAULT_GRANT_SCOPES,
  GRANT_LIFETIME_DAYS,
  GRANT_SCOPE_OPTIONS,
  grantDisplayState,
  grantExpiryFromLifetime,
  grantRequestBody,
} from "../lib/network/grant-form";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
const { GrantList, NetworkDashboard } = await import("../components/network/network-dashboard");

const NOW = new Date("2026-10-31T12:00:00.000Z");

describe("agent grant form helpers", () => {
  it("offers exactly the server's scopes and defaults to least privilege", () => {
    expect(GRANT_SCOPE_OPTIONS.map((option) => option.scope)).toEqual([...AGENT_SCOPES]);
    expect(DEFAULT_GRANT_SCOPES).toEqual(["profile:read"]);
    expect(DEFAULT_GRANT_LIFETIME_DAYS).toBe(DEFAULT_GRANT_TTL_DAYS);
  });

  it("turns every lifetime into an expiry the server accepts", () => {
    for (const days of GRANT_LIFETIME_DAYS) {
      const expiresAt = grantExpiryFromLifetime(days, NOW);
      expect(Date.parse(expiresAt) - NOW.getTime()).toBe(days * 24 * 3600_000);
      const result = validateGrantDefinition({ name: "agent", scopes: ["profile:read"], expiresAt }, NOW);
      expect(result, `${days} days`).toMatchObject({ ok: true, definition: { expiresAt } });
    }
    expect(grantExpiryFromLifetime(7, NOW)).toBe("2026-11-07T12:00:00.000Z");
    // A client clock two days ahead pushes a one-year grant past the server's bound.
    const skewed = grantExpiryFromLifetime(365, new Date(NOW.getTime() + 2 * 24 * 3600_000));
    expect(validateGrantDefinition({ name: "agent", scopes: ["profile:read"], expiresAt: skewed }, NOW)).toEqual({ ok: false, reason: "expiresAt must be in the future and within one year." });
  });

  it("builds a request with trimmed name, canonical scope order, and explicit expiry", () => {
    expect(grantRequestBody("  reader ", ["contacts:read", "profile:read"], 30, NOW)).toEqual({
      name: "reader",
      scopes: ["profile:read", "contacts:read"],
      expiresAt: "2026-11-30T12:00:00.000Z",
    });
  });

  it("labels grants exactly as the server decides liveness, including at the expiry instant", () => {
    const cases = [
      { expiresAt: "2026-10-31T12:00:00.001Z", revokedAt: null },
      { expiresAt: "2026-10-31T12:00:00.000Z", revokedAt: null },
      { expiresAt: "2026-10-01T00:00:00.000Z", revokedAt: null },
      { expiresAt: null, revokedAt: null },
      { expiresAt: "2027-01-01T00:00:00.000Z", revokedAt: "2026-10-30T00:00:00.000Z" },
    ];
    expect(cases.map((grant) => grantDisplayState(grant, NOW))).toEqual(["active", "expired", "expired", "active", "revoked"]);
    for (const grant of cases) expect(grantDisplayState(grant, NOW) === "active").toBe(grantIsLive(grant, NOW));
  });
});

describe("agent access dashboard", () => {
  const grants = [
    { id: "a", name: "reader", tokenPrefix: "cnag_aaaa", scopes: ["profile:read"], createdAt: "2026-10-01T00:00:00.000Z", expiresAt: "2026-12-30T00:00:00.000Z", revokedAt: null, lastUsedAt: "2026-10-20T08:00:00.000Z" },
    { id: "b", name: "old-writer", tokenPrefix: "cnag_bbbb", scopes: ["profile:read", "profile:write"], createdAt: "2026-07-01T00:00:00.000Z", expiresAt: "2026-09-29T00:00:00.000Z", revokedAt: null, lastUsedAt: null },
    { id: "c", name: "gone", tokenPrefix: "cnag_cccc", scopes: ["contacts:read"], createdAt: "2026-09-01T00:00:00.000Z", expiresAt: "2026-11-30T00:00:00.000Z", revokedAt: "2026-09-02T00:00:00.000Z", lastUsedAt: null },
    { id: "d", name: "legacy", tokenPrefix: "cnag_dddd", scopes: ["profile:read"], createdAt: "2026-05-01T00:00:00.000Z", expiresAt: null, revokedAt: null, lastUsedAt: null },
  ];

  it("shows each grant's state, dates, and last use, and offers Revoke only for active grants", () => {
    const markup = renderToStaticMarkup(createElement(GrantList, { grants, busy: false, onRevoke: () => undefined, now: NOW }));
    const items = markup.split("<li").slice(1);
    expect(items[0]).toContain("(active)");
    expect(items[0]).toContain("expires 2026-12-30");
    expect(items[0]).toContain("Created 2026-10-01 · last used 2026-10-20");
    expect(items[0]).toContain(">Revoke</button>");
    expect(items[1]).toContain("(expired)");
    expect(items[1]).toContain("expired 2026-09-29");
    expect(items[1]).toContain("never used");
    expect(items[1]).not.toContain("Revoke");
    expect(items[2]).toContain("(revoked)");
    expect(items[2]).not.toContain("Revoke");
    expect(items[3]).toContain("(active)");
    expect(items[3]).toContain("no expiry");
    expect(renderToStaticMarkup(createElement(GrantList, { grants: [], busy: false, onRevoke: () => undefined }))).toContain("No agent grants yet.");
  });

  it("defaults the form to read-only for 90 days and keeps Create disabled without a name", () => {
    const markup = renderToStaticMarkup(createElement(NetworkDashboard, { handle: "ada" }));
    const checkbox = (scope: string) => markup.match(new RegExp(`<input type="checkbox"[^>]*>(?=<span>[^<]*<code[^>]*>${scope}</code>)`, "u"))?.[0] ?? "";
    expect(checkbox("profile:read")).toContain('checked=""');
    for (const scope of ["profile:write", "contacts:read"]) expect(checkbox(scope)).toContain('type="checkbox"');
    expect(checkbox("profile:write")).not.toContain("checked");
    expect(checkbox("contacts:read")).not.toContain("checked");
    expect(markup).toMatch(/<option value="90" selected="">90 days<\/option>/u);
    expect(markup).toContain('<option value="365">1 year</option>');
    expect(markup).toMatch(/<button type="submit"[^>]*disabled=""[^>]*data-testid="grant-create"/u);
    expect(markup).toContain("agent API reference");
  });

  it("posts the selected scopes and an explicit expiry, and keeps the form on a server error", () => {
    const source = readFileSync(new URL("../components/network/network-dashboard.tsx", import.meta.url), "utf8");
    expect(source).toContain("body: JSON.stringify(grantRequestBody(newGrantName, newGrantScopes, newGrantLifetime)),");
    expect(source).not.toContain('scopes: ["profile:read", "profile:write"]');
    // On failure only the status changes; the name, scopes, and lifetime stay for a retry.
    const failure = source.slice(source.indexOf("async function createGrant"), source.indexOf("async function revokeGrant"));
    expect(failure).toContain('setStatus({ kind: "error", message: body.message ?? "Could not create the grant." });');
    expect(failure.split("} else {")[1]?.split("}")[0]).not.toMatch(/setNewGrant/u);
  });
});
