import { readFileSync } from "node:fs";

import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest, type NextFetchEvent } from "next/server";
import { describe, expect, it } from "vitest";

import middleware, { BOUNDED_NOT_FOUND_BODY, config } from "../middleware";

function matches(pathname: string) {
  return unstable_doesMiddlewareMatch({ config, url: "https://connect.md" + pathname });
}

describe("standalone route boundary", () => {
  it("matches every retired backend-backed route and its subroutes", () => {
    for (const pathname of [
      "/agent-directory",
      "/agents",
      "/agents/ari-agent",
      "/appeal-review",
      "/applications",
      "/employer",
      "/feed",
      "/jobs/acme/engineer",
      "/messages/conversation-1",
      "/moderation",
      "/moderation-review",
      "/organizations/acme",
      "/posts/post-1",
      "/r/ari-resume",
      "/representatives",
      "/search",
      "/verification-review",
      "/workspace",
    ]) expect(matches(pathname), pathname).toBe(true);
  });

  it("leaves the standalone pages, the network MVP routes, and static assets outside middleware", () => {
    for (const pathname of [
      "/",
      "/human",
      "/md",
      "/trust",
      "/account",
      "/network",
      "/discover",
      "/inbox",
      "/conversations/9f0f2b7e-0000-4000-8000-000000000000",
      "/p/ari-chen",
      "/api/network/v1/session",
      "/agent-readme.md",
      "/llms.txt",
      "/robots.txt",
      "/sitemap.xml",
      "/_next/static/app.js",
      "/favicon.ico",
    ]) expect(matches(pathname), pathname).toBe(false);
  });

  it("returns the same bounded no-store 404 for every retired route", async () => {
    for (const pathname of ["/agents/ari-agent", "/workspace", "/jobs/acme/engineer", "/feed"]) {
      const response = await middleware(
        new NextRequest("https://connect.md" + pathname),
        {} as NextFetchEvent,
      );
      expect(response.status).toBe(404);
      expect(response.headers.get("Cache-Control")).toBe("private, no-store, max-age=0");
      expect(response.headers.get("Content-Type")).toContain("text/html");
      expect(response.headers.get("X-Robots-Tag")).toBe("noindex, nofollow");
      expect(await response.text()).toBe(BOUNDED_NOT_FOUND_BODY);
    }
  });

  it("does not import auth, recruiting, API, or secret configuration", () => {
    const source = readFileSync(new URL("../middleware.ts", import.meta.url), "utf8");
    expect(source).not.toMatch(/clerk|recruiting|API|process\.env|SECRET|fetch\(/iu);
    expect(source).toContain('"/workspace/:path*"');
    expect(source).toContain("ADR 0002");
  });
});
