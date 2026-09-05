import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { AUTH_RETURN_ACTIONS, buildInboxContactReturnPath, buildProfileActionReturnPath, isCanonicalProfileHandle, parseInboxContactProfileIntent, parseSafeAuthReturnPath } from "../lib/auth-return-intent";

describe("auth return intent", () => {
  it("allows only canonical public profile paths for known private actions", () => {
    expect(AUTH_RETURN_ACTIONS).toEqual(["connect", "follow", "block"]);
    expect(buildProfileActionReturnPath("ari-chen", "connect")).toBe("/p/ari-chen");
    expect(buildProfileActionReturnPath("ari-chen", "follow")).toBe("/p/ari-chen");
    expect(buildInboxContactReturnPath("ari-chen")).toBe("/inbox?profile=ari-chen");
    expect(parseSafeAuthReturnPath("/p/ari-chen")).toBe("/p/ari-chen");
    expect(parseSafeAuthReturnPath("/p/a-1")).toBe("/p/a-1");
    expect(parseSafeAuthReturnPath("/inbox?profile=ari-chen")).toBe("/inbox?profile=ari-chen");
    expect(parseInboxContactProfileIntent(new URLSearchParams("profile=ari-chen"))).toBe("ari-chen");
    expect(isCanonicalProfileHandle("ari-chen")).toBe(true);
  });

  it("round-trips canonical profile and inbox returns through the 63/64-character boundary only", () => {
    const handles = [
      `a${"b".repeat(61)}c`,
      `a${"b".repeat(62)}c`,
    ];
    const tooLongHandle = `a${"b".repeat(63)}c`;

    expect(handles.map((handle) => handle.length)).toEqual([63, 64]);
    for (const handle of handles) {
      const profilePath = buildProfileActionReturnPath(handle, "connect");
      const inboxPath = buildInboxContactReturnPath(handle);
      expect(profilePath).toBe(`/p/${handle}`);
      expect(inboxPath).toBe(`/inbox?profile=${handle}`);
      expect(parseSafeAuthReturnPath(profilePath)).toBe(profilePath);
      expect(parseSafeAuthReturnPath(inboxPath)).toBe(inboxPath);
    }
    expect(tooLongHandle).toHaveLength(65);
    expect(isCanonicalProfileHandle(tooLongHandle)).toBe(false);
    expect(buildProfileActionReturnPath(tooLongHandle, "connect")).toBeNull();
    expect(buildInboxContactReturnPath(tooLongHandle)).toBeNull();
    expect(parseSafeAuthReturnPath(`/p/${tooLongHandle}`)).toBeNull();
    expect(parseSafeAuthReturnPath(`/inbox?profile=${tooLongHandle}`)).toBeNull();
  });

  it("rejects external, encoded, query-bearing, and malformed return candidates", () => {
    for (const candidate of [
      "https://evil.example/",
      "//evil.example/",
      "/p/%2F%2Fevil.example",
      "/p/%252f%252fevil.example",
      "/p/%2e%2e",
      "/p/..",
      "/p/ari-chen/..",
      "/p/ari%5cchen",
      "/p/ari-chen?next=https://evil.example",
      "/p/ari-chen#private",
      "/p/ari-chen/",
      "/p//evil.example",
      "/p/ari\\chen",
      "/p/ari\nchen",
      "/inbox",
      "/inbox?profile=ari-chen&purpose=smuggled",
      "/inbox?profile=ari-chen#private",
      "/network",
      "/r/ari-chen",
      "javascript:alert(1)",
    ]) expect(parseSafeAuthReturnPath(candidate)).toBeNull();
    expect(buildProfileActionReturnPath("https://evil.example", "connect")).toBeNull();
    expect(buildProfileActionReturnPath("ari-chen", "message")).toBeNull();
    expect(buildInboxContactReturnPath("Ari-Chen")).toBeNull();
    expect(buildInboxContactReturnPath("ari_agent")).toBeNull();
    expect(isCanonicalProfileHandle("ari agent")).toBe(false);
    expect(parseInboxContactProfileIntent(new URLSearchParams("profile=ari-chen&profile=other"))).toBeNull();
    expect(parseInboxContactProfileIntent(new URLSearchParams("profile=ari-chen&action=agent_outreach"))).toBeNull();
  });

  it("keeps the network auth controls explicit and session-scoped without persisting drafts", () => {
    const accountPageSource = readFileSync(new URL("../app/account/page.tsx", import.meta.url), "utf8");
    const authPanelSource = readFileSync(new URL("../components/network/account-auth-panel.tsx", import.meta.url), "utf8");
    // The account page is dynamic and renders the auth panel client-side.
    expect(accountPageSource).toContain('export const dynamic = "force-dynamic";');
    expect(accountPageSource).toContain("<AccountAuthPanel />");
    // Auth mutations go to the versioned network API and never touch local drafts.
    expect(authPanelSource).toContain("`/api/network/v1/accounts/${mode}`");
    expect(authPanelSource).toContain('autoComplete={mode === "register" ? "new-password" : "current-password"}');
    expect(authPanelSource).not.toContain("localStorage");
    expect(authPanelSource).not.toContain("sessionStorage");
  });
});
