import { readFileSync } from "node:fs";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import TrustPage, { metadata } from "../app/trust/page";

const originalRecruitingRelease = process.env.CONNECTMD_RECRUITING_ENABLED;
const originalApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
const originalClerkPublishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const originalClerkSecretKey = process.env.CLERK_SECRET_KEY;
const source = readFileSync(new URL("../app/trust/page.tsx", import.meta.url), "utf8");

vi.mock("server-only", () => ({}));
vi.stubGlobal("React", React);

afterEach(() => {
  if (originalRecruitingRelease === undefined) delete process.env.CONNECTMD_RECRUITING_ENABLED;
  else process.env.CONNECTMD_RECRUITING_ENABLED = originalRecruitingRelease;
  if (originalApiBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = originalApiBase;
  if (originalClerkPublishableKey === undefined) delete process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  else process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = originalClerkPublishableKey;
  if (originalClerkSecretKey === undefined) delete process.env.CLERK_SECRET_KEY;
  else process.env.CLERK_SECRET_KEY = originalClerkSecretKey;
  vi.unstubAllEnvs();
});

type AuthState = "absent" | "public-only" | "secret-only" | "both";

function trustMarkup(recruitingEnabled: boolean, authState: AuthState = "absent") {
  if (recruitingEnabled) process.env.CONNECTMD_RECRUITING_ENABLED = "true";
  else delete process.env.CONNECTMD_RECRUITING_ENABLED;
  if (authState === "public-only" || authState === "both") {
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = "pk_test_connectmd";
  } else {
    delete process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  }
  if (authState === "secret-only" || authState === "both") {
    process.env.CLERK_SECRET_KEY = "sk_test_connectmd";
  } else {
    delete process.env.CLERK_SECRET_KEY;
  }
  return renderToStaticMarkup(createElement(TrustPage));
}

describe("public privacy and agent-data explanation", () => {
  it("is an indexable canonical public explanation rather than an invented legal policy", () => {
    const markup = trustMarkup(false);
    expect(metadata.alternates).toEqual({ canonical: "/trust" });
    expect(metadata).not.toHaveProperty("robots");
    expect(markup).toContain("plain-language description of current product visibility, not a legal privacy policy");
    expect(markup).toContain("does not set legal terms or promise a retention or deletion outcome");
    expect(markup).not.toContain('href="/account"');
  });

  it("states the anonymous public inventory and the private workspace boundary", () => {
    const markup = trustMarkup(false);
    for (const statement of [
      "Public without sign-in",
      "Profiles and resumes their owners chose to publish",
      "Published professional posts",
      "active Agent Identity labels",
      "Private and permission-gated",
      "Connections, conversations, messages, notifications",
      "Applications, application notes and snapshots",
      "API keys, Agent Grants, mandates, proposals",
    ]) expect(markup).toContain(statement);
  });

  it("explains Markdown and agent authority without requiring technical knowledge", () => {
    const markup = trustMarkup(false);
    expect(markup).toContain("An <code");
    expect(markup).toContain("file is plain text with simple headings and lists");
    expect(markup).toContain("You do not need to code or learn Markdown");
    expect(markup).toContain("Finding a profile or Agent Identity does not grant contact, publishing, application, or maintenance authority");
    expect(markup).toContain("does not publish ownership, availability, grants, mandates, presence, credentials, or an external agent endpoint");
  });

  it("links only to truthful current public contracts when private workspaces are unavailable", () => {
    const markup = trustMarkup(false);
    for (const href of [
      "/discover",
      "/agent-readme.md",
      "/llms.txt",
      "/llms-full.txt",
      "/openapi.json",
    ]) expect(markup).toContain(`href="${href}"`);
    expect(markup).not.toContain('href="/employer"');
    expect(markup).not.toContain('href="/organizations"');
    expect(markup).not.toContain('href="/jobs"');
    expect(markup).toContain("Recruiting is not available in this release");
    expect(markup).toContain("Private employer preparation appears only in deployments with authenticated workspaces");
    expect(markup).toContain("this public page does not claim that those controls are currently available");
    expect(markup).not.toContain("Service-gated public organization records and published jobs");
    expect(source).toContain('export const dynamic = "force-dynamic"');
    expect(source).toContain("recruitingReleaseEnabled()");
    expect(source).toContain("privateWorkspaceConfiguredFromEnvironment()");
    expect(source).toContain('href={publicDiscoveryUrl("/agent-readme.md")}');
    expect(source).toContain('publicProtocolUrl("/llms.txt")');
    expect(source).toContain('publicProtocolUrl("/llms-full.txt")');
    expect(source).toContain('publicProtocolUrl("/openapi.json")');
    expect(source).not.toMatch(/<a href="\/(?:llms(?:-full)?\.txt|openapi\.json)"/u);
    expect(source).not.toMatch(/<Link href="\/(?:agent-readme\.md|llms(?:-full)?\.txt|openapi\.json)"/u);
  });

  it("gives every public platform contract a 44 pixel touch target without disabling narrow-screen wrapping", () => {
    const markup = trustMarkup(false);
    const navigation = markup.match(/<nav aria-label="Public platform contracts"[^>]*>[\s\S]*?<\/nav>/u)?.[0] ?? "";
    const links = [...navigation.matchAll(/<a\b(?=[^>]*\bhref="([^"]+)")(?=[^>]*\btype="([^"]+)")(?=[^>]*\bclass="([^"]*)")[^>]*>([^<]+)<\/a>/gu)];

    expect(navigation).toContain("flex-wrap");
    expect(links.map(([, href, type, , label]) => [label, href, type])).toEqual([
      ["Agent onboarding README", "/agent-readme.md", "text/markdown"],
      ["llms.txt", "/llms.txt", "text/plain"],
      ["Complete agent guide", "/llms-full.txt", "text/plain"],
      ["OpenAPI", "/openapi.json", "application/json"],
    ]);
    for (const [, , , className] of links) {
      expect(className.split(/\s+/u)).toEqual(
        expect.arrayContaining(["inline-flex", "min-h-11", "items-center"]),
      );
    }
  });

  it("exposes private employer preparation only with complete route authentication", () => {
    const markup = trustMarkup(false, "both");
    expect(markup).toContain("Private employer preparation");
    expect(markup).toContain("Prepare in the private employer workspace");
    expect(markup).toContain('href="/employer"');
    expect(markup).not.toContain("Recruiting is not available in this release");
  });

  it("makes trust-page protocol links absolute only for a valid split API origin", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");
    const markup = trustMarkup(false);

    expect(markup).toContain('href="https://api.connect.test/agent-readme.md"');
    expect(markup).toContain('href="https://api.connect.test/llms.txt"');
    expect(markup).toContain('href="https://api.connect.test/llms-full.txt"');
    expect(markup).toContain('href="https://api.connect.test/openapi.json"');
    expect(markup).not.toContain('href="/llms.txt"');
  });

  it("describes public recruiting without advertising a missing private workspace", () => {
    const markup = trustMarkup(true);
    expect(markup).toContain("Service-gated public organization records and published jobs");
    expect(markup).toContain("Public recruiting");
    expect(markup).toContain("available only through the service&#x27;s active recruiting-control gate");
    expect(markup).toContain('href="/organizations"');
    expect(markup).toContain('href="/jobs"');
    expect(markup).not.toContain('href="/employer"');
    expect(markup).not.toContain("Public recruiting and private preparation");
  });

  it("adds private preparation only when recruiting and route authentication are both enabled", () => {
    const markup = trustMarkup(true, "both");
    expect(markup).toContain("Public recruiting and private preparation");
    expect(markup).toContain('href="/organizations"');
    expect(markup).toContain('href="/jobs"');
    expect(markup).toContain('href="/employer"');
    expect(markup).not.toContain("Public recruiting and applicant intake are disabled until the release gate is explicitly enabled");
  });

  it.each([
    ["absent", false],
    ["public-only", false],
    ["secret-only", false],
    ["both", true],
  ] as const)("gates private employer preparation for the %s Clerk state", (authState, privateWorkspacesEnabled) => {
    const markup = trustMarkup(false, authState);

    if (privateWorkspacesEnabled) {
      expect(markup).toContain('href="/employer"');
      expect(markup).toContain("Private employer preparation");
    } else {
      expect(markup).not.toContain('href="/employer"');
      expect(markup).toContain("Recruiting is not available in this release");
    }
  });
});
