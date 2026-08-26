import { readFileSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AsyncBoundaryMessage } from "../components/async-boundary-message";

vi.stubGlobal("React", React);

const wiredBoundaries = [
  ["account-privacy-center.tsx", "loading={loading}"],
  ["agent-delegation-manager.tsx", "loading={loading}"],
  ["agent-identity-manager.tsx", "loading={loading}"],
  ["job-application-panel.tsx", "loading={loading}"],
  ["markdown-editor.tsx", "loading>Loading Markdown editor"],
  ["moderation-appeal-review-queue.tsx", "descriptionLoading={loading}"],
  ["moderation-case-manager.tsx", "loading={loading}"],
  ["moderation-case-review-queue.tsx", "descriptionLoading={loading}"],
  ["outreach-inbox.tsx", "loading={!isLoaded}"],
  ["post-composer.tsx", "loading={loading}"],
  ["professional-feed.tsx", "loading>Loading follows"],
  ["verification-review-queue.tsx", "loading={loading}"],
  ["workspace-hub.tsx", "loading={loading}"],
] as const;

describe("authenticated async boundary messaging", () => {
  it("announces active work politely and atomically", () => {
    const markup = renderToStaticMarkup(
      AsyncBoundaryMessage({
        className: "feedback",
        loading: true,
        children: "Checking the signed-in session.",
      }),
    );

    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('aria-atomic="true"');
    expect(markup).toContain("Checking the signed-in session.");
  });

  it("keeps stable guidance out of the live region", () => {
    const markup = renderToStaticMarkup(
      AsyncBoundaryMessage({
        className: "feedback",
        loading: false,
        children: "Sign in to continue.",
      }),
    );

    expect(markup).toBe('<p class="feedback">Sign in to continue.</p>');
  });

  it.each(wiredBoundaries)("wires the shared contract into %s", (filename, loadingAnchor) => {
    const source = readFileSync(new URL(`../components/${filename}`, import.meta.url), "utf8");

    expect(source).toContain('import { AsyncBoundaryMessage } from "@/components/async-boundary-message";');
    expect(source).toContain("<AsyncBoundaryMessage");
    expect(source).toContain(loadingAnchor);
  });
});
