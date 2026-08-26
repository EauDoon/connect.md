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

  it("keeps the auth controls explicit and does not persist or replay a mutation", () => {
    const connect = readFileSync(new URL("../components/profile-connect-control.tsx", import.meta.url), "utf8");
    const post = readFileSync(new URL("../components/profile-post-controls.tsx", import.meta.url), "utf8");
    for (const source of [connect, post]) {
      expect(source).toContain("SignInButton");
      expect(source).toContain("forceRedirectUrl={returnPath}");
      expect(source).toContain("signUpForceRedirectUrl={returnPath}");
      expect(source).not.toMatch(/localStorage|sessionStorage|document\.cookie|window\.location/u);
    }
    const connectSignedOut = connect.slice(connect.indexOf("if (!isSignedIn"), connect.indexOf("const submit"));
    const postSignedOut = post.slice(post.indexOf("if (!isSignedIn"), post.indexOf("return <Authenticated"));
    expect(connectSignedOut).not.toMatch(/createConnectionRequest|followProfile|blockProfileContent/u);
    expect(postSignedOut).not.toMatch(/createConnectionRequest|followProfile|blockProfileContent/u);
    expect(connectSignedOut).toMatch(/<Link href="\/network" className="inline-flex min-h-11 items-center\b/u);
    expect(postSignedOut).toMatch(/<Link href="\/feed" className="inline-flex min-h-11 items-center\b/u);

    const inbox = readFileSync(new URL("../components/outreach-inbox.tsx", import.meta.url), "utf8");
    const inboxPage = readFileSync(new URL("../app/inbox/page.tsx", import.meta.url), "utf8");
    expect(inbox).toContain("buildInboxContactReturnPath");
    expect(inbox).toContain("initialTarget");
    expect(inbox).not.toMatch(/agent_outreach|sendAgentOutreach|setPurpose\([^)]*prefill|setBody\([^)]*prefill/u);
    expect(inboxPage).toContain("parseInboxContactProfileIntent(serverSearchParams(await searchParams))");
    expect(inboxPage).toContain("<OutreachInbox prefillProfileHandle={prefillProfileHandle} />");
  });
});
