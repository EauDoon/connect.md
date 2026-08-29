import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import { agentHandoffPresets } from "@/components/agent-handoff";

const pageSource = readFileSync(resolve(process.cwd(), "app/page.tsx"), "utf8");
const handoffSource = readFileSync(resolve(process.cwd(), "components/agent-handoff.tsx"), "utf8");

vi.mock("server-only", () => ({}));
vi.stubGlobal("React", React);

describe("standalone agent-first landing", () => {
  it("puts the agent handoff before the human workflow on narrow screens", () => {
    const handoffCall = '<AgentHandoff agentReadmeUrl={absoluteSiteUrl("/agent-readme.md")} />';
    expect(pageSource).toContain(handoffCall);
    expect(pageSource).not.toContain("<h1");
    expect(pageSource).toContain('className="order-1 min-w-0 lg:order-2"');
    expect(pageSource).toContain('className="order-2 min-w-0 max-w-xl lg:order-1"');
    expect(handoffSource).toContain('<h1 id="agent-handoff-title"');
  });

  it("offers bounded profile, resume, and maintenance instructions", () => {
    const readmeUrl = "https://preview.connect.test/agent-readme.md";
    const presets = agentHandoffPresets(readmeUrl);
    expect(presets.map((preset) => preset.id)).toEqual(["profile", "resume", "maintain"]);

    for (const preset of presets) {
      expect(preset.prompt).toContain(readmeUrl);
      expect(preset.prompt).toContain("ask me to paste it and stop; do not infer the format");
      expect(preset.prompt.toLowerCase()).toContain("markdown");
      expect(preset.prompt.toLowerCase()).toContain("do not publish");
      expect(preset.prompt.toLowerCase()).toContain("upload");
    }
    expect(presets[0].prompt).toContain("flag unsupported or uncertain claims");
    expect(presets[1].prompt).toContain("identify anything uncertain instead of inventing details");
    expect(presets[2].prompt).toContain("propose the smallest factual update with a clear diff");
  });

  it("keeps copy state deterministic and accessible", () => {
    expect(handoffSource).toContain("navigator.clipboard?.writeText");
    expect(handoffSource).toContain("copyAttempt.current === attempt");
    expect(handoffSource).toContain('role="group" aria-label="Agent task presets"');
    expect(handoffSource).toContain('aria-pressed={active}');
    expect(handoffSource).toContain('aria-live="polite"');
    expect(handoffSource).toContain('aria-atomic="true"');
    expect(handoffSource).toContain('type="text/markdown"');
    expect(handoffSource).toContain('href="/llms.txt" type="text/plain"');
  });

  it("renders only the standalone public workflow", () => {
    const markup = renderToStaticMarkup(createElement(HomePage));
    for (const href of ["/human", "/md", "/trust", "/agent-readme.md", "/llms.txt"]) {
      expect(markup).toContain(href);
    }
    for (const href of ["/discover", "/agent-directory", "/agents", "/workspace", "/employer", "/organizations", "/jobs"]) {
      expect(markup).not.toContain(`href="${href}"`);
    }
    expect(markup).toContain("No account, database, or upload required.");
    expect(markup).toContain("Nothing is uploaded.");
    expect(pageSource).not.toMatch(/force-dynamic|recruitingReleaseEnabled|privateWorkspaceConfiguredFromEnvironment|NEXT_PUBLIC_API_BASE_URL/u);
  });
});
