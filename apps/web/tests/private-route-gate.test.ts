import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest, type NextFetchEvent } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parse } from "yaml";

import middleware, { BOUNDED_NOT_FOUND_BODY, config, privateRouteAuthConfigured } from "../middleware";

const webRoot = resolve(process.cwd());
const repoRoot = resolve(webRoot, "..", "..");

function source(relativePath: string): string {
  return readFileSync(resolve(repoRoot, relativePath), "utf8");
}

function sourceFilesUnder(relativeDirectory: string, excludedRelativePaths: readonly string[] = []): string[] {
  const directory = resolve(repoRoot, relativeDirectory);
  const excludedPaths = new Set(excludedRelativePaths.map((relativePath) => resolve(repoRoot, relativePath)));
  return readdirSync(directory, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.[cm]?[jt]sx?$/u.test(entry.name) && !excludedPaths.has(resolve(entry.parentPath, entry.name)))
    .map((entry) => readFileSync(resolve(entry.parentPath, entry.name), "utf8"));
}

function matches(pathname: string): boolean {
  return unstable_doesMiddlewareMatch({
    config,
    url: `https://connect.md${pathname}`,
  });
}

describe("server-side private route gate", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("matches every private human route and its bounded subroutes", () => {
    for (const pathname of [
      "/account",
      "/account/export",
      "/agents",
      "/appeal-review",
      "/applications",
      "/employer",
      "/feed",
      "/inbox",
      "/inbox?profile=ari-chen",
      "/messages/conversation-1",
      "/moderation",
      "/moderation-review",
      "/network",
      "/verification-review",
      "/workspace",
    ]) expect(matches(pathname), pathname).toBe(true);
  });

  it("matches public recruiting routes for the request-boundary release gate", () => {
    for (const pathname of [
      "/organizations",
      "/organizations/acme",
      "/jobs",
      "/jobs/acme/engineer",
    ]) expect(matches(pathname), pathname).toBe(true);
  });

  it("leaves local-first editing and public discovery routes outside Clerk middleware", () => {
    for (const pathname of [
      "/",
      "/agent-directory",
      "/agents/ari-agent",
      "/discover",
      "/human",
      "/md",
      "/jobsx",
      "/organizationsx",
      "/p/ari-chen",
      "/posts/post-1",
      "/r/ari-chen-resume",
      "/representatives",
      "/robots.txt",
      "/search",
      "/sitemap/0.xml",
      "/trust",
    ]) expect(matches(pathname), pathname).toBe(false);
  });

  it("checks only for a human session and leaves all resource and role authority to the API", () => {
    const middleware = source("apps/web/middleware.ts");

    expect(middleware).toContain('auth.protect({ token: "session_token" })');
    expect(middleware).toContain('process.env.CONNECTMD_RECRUITING_ENABLED !== "true"');
    expect(middleware).toContain('"/organizations/:path*"');
    expect(middleware).toContain('"/jobs/:path*"');
    expect(middleware).not.toMatch(/has\s*\(|permission|role:|REVIEWER_ID|MODERATOR_ID|owner_id/u);
    expect(middleware).not.toMatch(/console\.|logger|JSON\.stringify/u);
  });

  it.each([
    [undefined, undefined, false],
    ["publishable", undefined, false],
    [undefined, "secret", false],
    ["publishable", "secret", true],
  ] as const)(
    "requires both Clerk configuration inputs",
    (publishableKey, secretKey, expected) => {
      expect(privateRouteAuthConfigured(publishableKey, secretKey)).toBe(expected);
    },
  );

  it.each([
    ["", ""],
    ["publishable", ""],
    ["", "secret"],
  ] as const)("returns a bounded no-store 404 when Clerk configuration is incomplete", async (publishableKey, secretKey) => {
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", publishableKey);
    vi.stubEnv("CLERK_SECRET_KEY", secretKey);

    const response = await middleware(
      new NextRequest("https://connect.md/workspace"),
      {} as NextFetchEvent,
    );
    if (!response) throw new Error("incomplete Clerk configuration did not return a response");

    expect(response.status).toBe(404);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store, max-age=0");
    expect(response.headers.get("Content-Type")).toContain("text/html");
    expect(response.headers.get("X-Robots-Tag")).toBe("noindex, nofollow");
    expect(await response.text()).toBe(BOUNDED_NOT_FOUND_BODY);
  });

  it.each(["", "false", "TRUE", " true "])(
    "returns a bounded non-enumerating 404 for recruiting routes unless the flag is exactly true (%j)",
    async (releaseFlag) => {
      vi.stubEnv("CONNECTMD_RECRUITING_ENABLED", releaseFlag);
      const response = await middleware(
        new NextRequest("https://connect.md/organizations/acme"),
        {} as NextFetchEvent,
      );
      if (!response) throw new Error("disabled recruiting route did not return a response");

      expect(response.status).toBe(404);
      expect(response.headers.get("Cache-Control")).toBe("private, no-store, max-age=0");
      expect(response.headers.get("X-Robots-Tag")).toBe("noindex, nofollow");
      expect(await response.text()).toBe(BOUNDED_NOT_FOUND_BODY);
    },
  );

  it("passes enabled recruiting routes through without invoking Clerk", async () => {
    vi.stubEnv("CONNECTMD_RECRUITING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");

    const response = await middleware(
      new NextRequest("https://connect.md/jobs/acme/engineer"),
      {} as NextFetchEvent,
    );
    if (!response) throw new Error("enabled recruiting route did not return a response");

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("keeps the optional Clerk secret in permitted server runtime sources only", () => {
    const compose = parse(source("compose.yaml"));
    const frontend = compose.services.frontend;
    const dockerfile = source("apps/web/Dockerfile");
    const rootLayout = source("apps/web/app/layout.tsx");
    const middleware = source("apps/web/middleware.ts");
    const privateWorkspaceConfig = source("apps/web/lib/private-workspace-config.ts");

    expect(frontend.environment.CLERK_SECRET_KEY).toBe("${CLERK_SECRET_KEY:-}");
    expect(frontend.build.args).not.toHaveProperty("CLERK_SECRET_KEY");
    expect(dockerfile).not.toContain("CLERK_SECRET_KEY");
    expect(rootLayout).toContain('export const dynamic = "force-dynamic";');
    expect(rootLayout).toContain("privateWorkspaceConfiguredFromEnvironment()");
    expect(middleware).toContain("CLERK_SECRET_KEY");
    expect(privateWorkspaceConfig).toContain('import "server-only";');
    expect(privateWorkspaceConfig).toContain("privateRouteAuthConfigured(");
    expect(privateWorkspaceConfig).toContain("process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
    expect(privateWorkspaceConfig).toContain("process.env.CLERK_SECRET_KEY");
    for (const directory of ["apps/web/app", "apps/web/components", "apps/web/lib"]) {
      for (const file of sourceFilesUnder(directory, ["apps/web/lib/private-workspace-config.ts"])) expect(file).not.toContain("CLERK_SECRET_KEY");
    }
    expect(source(".env.example")).not.toContain("NEXT_PUBLIC_CLERK_SECRET_KEY");
    expect(source("apps/web/.env.example")).not.toContain("NEXT_PUBLIC_CLERK_SECRET_KEY");
  });
});
