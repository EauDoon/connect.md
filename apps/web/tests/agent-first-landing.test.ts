import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { afterEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import { agentHandoffPresets } from "@/components/agent-handoff";

const pageSource = readFileSync(resolve(process.cwd(), "app/page.tsx"), "utf8");
const handoffSource = readFileSync(resolve(process.cwd(), "components/agent-handoff.tsx"), "utf8");
const originalRecruitingRelease = process.env.CONNECTMD_RECRUITING_ENABLED;
const originalApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
const originalClerkPublishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const originalClerkSecretKey = process.env.CLERK_SECRET_KEY;

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
});

type AuthState = "absent" | "public-only" | "secret-only" | "both";

function landingMarkup(recruitingEnabled: boolean, authState: AuthState = "absent") {
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
  return renderToStaticMarkup(createElement(HomePage));
}

describe("agent-first landing", () => {
  it("makes the agent handoff the first semantic and visual mobile action", () => {
    const handoffCall = "<AgentHandoff agentReadmeUrl={agentReadmeUrl} />";
    expect(pageSource).toContain("Give your agent");
    expect(pageSource).toContain(handoffCall);
    expect(pageSource.indexOf(handoffCall)).toBeLessThan(pageSource.indexOf("Give your agent"));
    expect(pageSource.indexOf(handoffCall)).toBeLessThan(pageSource.indexOf('href="/human"'));
    expect(pageSource.indexOf(handoffCall)).toBeLessThan(pageSource.indexOf('href="/md"'));
    expect(pageSource).toContain('className="order-1 min-w-0 lg:order-2"');
    expect(pageSource).toContain('className="order-2 min-w-0 max-w-xl lg:order-1"');
    expect(handoffSource).toContain('<h1 id="agent-handoff-title"');
    expect(pageSource).not.toContain("<h1");
  });

  it("offers bounded profile, resume, and maintenance instructions", () => {
    const configuredReadmeUrl = "https://preview.connect.test/agent-readme.md";
    const presets = agentHandoffPresets(configuredReadmeUrl);
    expect(presets.map((preset) => preset.id)).toEqual(["profile", "resume", "maintain"]);

    for (const preset of presets) {
      expect(preset.prompt).toContain(configuredReadmeUrl);
      expect(preset.prompt).toContain("ask me to paste /agent-readme.md and stop; do not infer the contract");
      expect(preset.prompt).not.toContain("ask me to paste it");
      expect(preset.prompt.toLowerCase()).toContain("markdown");
      expect(preset.prompt.toLowerCase()).toContain("explicit");
      expect(preset.prompt.toLowerCase()).toContain("publish");
    }

    expect(presets[0].prompt).toContain("Do not publish, contact anyone, or create ongoing agent access");
    expect(presets[1].prompt).toContain("identify anything uncertain instead of inventing details");
    expect(presets[2].prompt).toContain("Do not overwrite a newer version");
    expect(pageSource).toContain('absoluteSiteUrl("/agent-readme.md")');
    expect(handoffSource).not.toContain("https://connect.md/agent-readme.md");
  });

  it("uses the configured API origin for split-origin first-visit links and prompts", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");
    const markup = landingMarkup(false);

    expect(markup).toContain('href="https://api.connect.test/agent-readme.md"');
    expect(markup).toContain('href="https://api.connect.test/llms.txt"');
    expect(markup).not.toContain('href="/agent-readme.md"');
    expect(markup).not.toContain('href="/llms.txt"');
    expect(markup).toContain("Open https://api.connect.test/agent-readme.md and follow it");
  });

  it("exposes canonical discovery and an accessible deterministic copy control", () => {
    expect(handoffSource).toContain('href={publicDiscoveryUrl("/agent-readme.md")}');
    expect(handoffSource).toContain('publicProtocolUrl("/llms.txt")');
    expect(handoffSource).not.toContain('href="/llms.txt"');
    expect(pageSource).toContain('publicDiscoveryUrl("/agent-readme.md", absoluteSiteUrl("/agent-readme.md"))');
    expect(pageSource).toContain('href={publicDiscoveryUrl("/agent-readme.md")}');
    expect(handoffSource).toContain('type="text/markdown"');
    expect(handoffSource).toMatch(/<a href=\{publicProtocolUrl\("\/llms\.txt"\) \?\? "\/llms\.txt"\} type="text\/plain"/u);
    expect(handoffSource).not.toContain('from "next/link"');
    expect(handoffSource).toContain("navigator.clipboard?.writeText");
    expect(handoffSource).toContain('aria-live="polite"');
    expect(handoffSource).toContain('aria-atomic="true"');
    expect(handoffSource).toContain("copyAttempt.current === attempt");
    expect(handoffSource).toContain("motion-reduce:transition-none");
    expect(handoffSource).toContain('aria-pressed={active}');
    expect(handoffSource).toContain('role="group" aria-label="Agent task presets"');
    expect(handoffSource).toContain('id="agent-handoff-instruction-label"');
    expect(handoffSource).toContain('aria-labelledby="agent-handoff-instruction-label"');
    expect(handoffSource).toContain("max-w-full min-w-0 break-anywhere select-all");
    expect(handoffSource).toContain("focus-visible:ring-2 focus-visible:ring-acid/70");
    expect(handoffSource).toContain('className="-mx-2 inline-flex min-h-11 items-center rounded-md px-2 align-middle font-mono');
    expect(handoffSource).toContain('`${selected.label} selected. Ready to copy.`');
    expect(handoffSource).not.toContain("Copied —");
    expect(handoffSource).toContain("Paste it into ChatGPT, Claude, OpenClaw, or another agent.");
    expect(handoffSource).toContain("Works with ChatGPT, Claude, OpenClaw, and other web-capable agents.");
  });

  it("explains .md for novices without advertising unavailable private routes", () => {
    const markup = landingMarkup(false);
    expect(pageSource).toContain("A <code");
    expect(pageSource).toContain("file is portable plain-text Markdown, readable by people and agents");
    expect(pageSource).toContain('export const dynamic = "force-dynamic"');
    expect(pageSource).toContain("recruitingReleaseEnabled()");
    expect(pageSource).toContain("privateWorkspaceConfiguredFromEnvironment()");
    expect(markup).toContain('href="/trust"');
    expect(markup).toContain("Privacy &amp; agent data");
    expect(markup).toContain('href="/agent-directory"');
    expect(markup).toContain("Explore published agents");
    expect(markup).toContain("Recruiting is not available in this release");
    expect(markup).toContain("Private employer preparation appears only in deployments with authenticated workspaces");
    expect(markup).not.toContain('href="/agents"');
    expect(markup).not.toContain('href="/employer"');
    expect(markup).not.toContain('href="/organizations"');
    expect(markup).not.toContain('href="/jobs"');
    expect(markup).not.toContain("Public recruiting is enabled");
  });

  it("restores private agent and employer actions only with complete route authentication", () => {
    const markup = landingMarkup(false, "both");
    expect(markup).toContain('href="/agents"');
    expect(markup).toContain("Manage agent access");
    expect(markup).toContain('href="/employer"');
    expect(markup).toContain("Private employer preparation");
    expect(markup).not.toContain('href="/agent-directory"');
    expect(markup).not.toContain("Recruiting is not available in this release");
  });

  it("shows public recruiting without inventing an unavailable private workspace", () => {
    const markup = landingMarkup(true);
    expect(markup).toContain("Public recruiting is enabled");
    expect(markup).toContain("Browse service-gated organizations and published jobs");
    expect(markup).toContain('href="/organizations"');
    expect(markup).toContain('href="/jobs"');
    expect(markup).not.toContain('href="/employer"');
    expect(markup).not.toContain("Private employer preparation");
  });

  it("adds the private employer action when recruiting and route authentication are both enabled", () => {
    const markup = landingMarkup(true, "both");
    expect(markup).toContain("Public recruiting is enabled");
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
  ] as const)("gates private landing actions for the %s Clerk state", (authState, privateWorkspacesEnabled) => {
    const markup = landingMarkup(false, authState);

    if (privateWorkspacesEnabled) {
      expect(markup).toContain('href="/agents"');
      expect(markup).toContain('href="/employer"');
      expect(markup).not.toContain('href="/agent-directory"');
    } else {
      expect(markup).toContain('href="/agent-directory"');
      expect(markup).not.toContain('href="/agents"');
      expect(markup).not.toContain('href="/employer"');
    }
  });
});
