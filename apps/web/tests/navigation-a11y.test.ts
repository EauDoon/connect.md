import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import { bindEscapeToCloseMobileNavigation, closeMobileNavigationAndRestoreFocus } from "../lib/mobile-navigation";

vi.mock("@/components/draft-provider", () => ({ useDraft: () => ({ markdown: "# Draft" }) }));

describe("navigation accessibility", () => {
  it("prevents explicit application touch targets from shrinking below 44 pixels", () => {
    const webRoot = fileURLToPath(new URL("..", import.meta.url));
    const roots = [join(webRoot, "app"), join(webRoot, "components")];
    const violations = roots.flatMap(walkTsx).flatMap((file) => {
      const source = readFileSync(file, "utf8");
      return [...source.matchAll(/\bmin-h-(?:8|9|10)\b/gu)].map((match) => `${relative(webRoot, file)}:${source.slice(0, match.index).split("\n").length}:${match[0]}`);
    });

    expect(violations).toEqual([]);
  });

  it("requires every native disclosure target to declare the 44 pixel minimum", () => {
    const webRoot = fileURLToPath(new URL("..", import.meta.url));
    const roots = [join(webRoot, "app"), join(webRoot, "components")];
    const violations = roots.flatMap(walkTsx).flatMap((file) => {
      const source = readFileSync(file, "utf8");
      return [...source.matchAll(/<summary\b([^>]*)>/gu)]
        .filter((match) => !/\bmin-h-11\b/u.test(match[1] ?? ""))
        .map((match) => `${relative(webRoot, file)}:${source.slice(0, match.index).split("\n").length}`);
    });

    expect(violations).toEqual([]);
  });

  it("keeps the global skip link at least 44 CSS pixels tall", () => {
    const globalsSource = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
    expect(globalsSource).toMatch(/\.skip-link\s*\{[^}]*display:\s*inline-flex;[^}]*min-height:\s*44px;[^}]*align-items:\s*center;/u);
  });

  it("closes mobile navigation only on Escape and removes the exact listener during cleanup", () => {
    const target = {
      addEventListener: vi.fn(),
      removeEventListener: vi.fn()
    };
    const close = vi.fn();
    const cleanup = bindEscapeToCloseMobileNavigation(target, close);
    const listener = target.addEventListener.mock.calls[0]?.[1] as EventListener;

    listener({ key: "Enter" } as unknown as Event);
    listener({ key: "Escape" } as unknown as Event);
    expect(close).toHaveBeenCalledTimes(1);
    cleanup();
    expect(target.removeEventListener).toHaveBeenCalledWith("keydown", listener);
  });

  it("restores focus to the navigation toggle after Escape closes the menu", () => {
    const close = vi.fn(); const focus = vi.fn();
    closeMobileNavigationAndRestoreFocus(close, { focus });
    expect(close).toHaveBeenCalledOnce();
    expect(focus).toHaveBeenCalledOnce();
    expect(close.mock.invocationCallOrder[0]).toBeLessThan(focus.mock.invocationCallOrder[0]);
  });

  it("uses ordinary current-page navigation semantics for cross-route editing modes", async () => {
    const { ModeSwitch } = await import("../components/mode-switch");
    const markup = renderToStaticMarkup(createElement(ModeSwitch, { mode: "human" }));
    expect(markup).toContain('<nav class="mt-2 inline-flex');
    expect(markup).toContain('aria-label="Editing mode. Switching views keeps the current canonical draft."');
    expect(markup).toContain('href="/human"');
    expect(markup).toContain('aria-current="page"');
    expect(markup).not.toContain('role="tab"');
    expect(markup).not.toContain('role="tablist"');
    expect(markup).not.toContain('aria-selected');
  });

  it("uses a non-linear, button-based Human Mode chapter stepper rather than tab semantics", () => {
    const source = readFileSync(new URL("../components/human-builder.tsx", import.meta.url), "utf8");
    expect(source).toContain('<nav aria-label="Human Mode chapter navigation"');
    expect(source).toContain("<ol className=");
    expect(source).toContain('aria-current={active ? "step" : undefined}');
    expect(source).toContain('onClick={() => activateStage(step.id)}');
    expect(source).not.toContain('role="tablist"');
    expect(source).not.toContain('role="tab"');
    expect(source).not.toContain('aria-selected');
  });

  it("keeps public trust navigation globally reachable and preserves skip-target orientation", () => {
    const headerSource = readFileSync(new URL("../components/site-header.tsx", import.meta.url), "utf8");
    const globalsSource = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

    expect(headerSource).toContain("PUBLIC_UTILITY_NAVIGATION");
    expect(headerSource).toContain("PUBLIC_PRIMARY_NAVIGATION");
    expect(headerSource.match(/PUBLIC_PRIMARY_NAVIGATION\.map/gu)).toHaveLength(2);
    expect(headerSource.match(/role="group" aria-label="Trust and data navigation"/gu)).toHaveLength(2);
    expect(headerSource).not.toMatch(/privateNavigationEnabled|privateWorkspacesEnabled|useConnectmdAuth/u);
    expect(globalsSource).toMatch(/#main-content:focus\s*\{[^}]*outline:\s*2px/gu);
    expect(globalsSource).not.toContain("#main-content:focus { outline: none; }");
  });

  it("balances standard mobile header gutters without account controls", () => {
    const headerSource = readFileSync(new URL("../components/site-header.tsx", import.meta.url), "utf8");

    expect(headerSource).toContain("px-0 min-[300px]:px-3 sm:px-5 lg:px-8");
    expect(headerSource).toContain("min-h-11 min-w-11 items-center justify-center");
    expect(headerSource).toContain("min-[240px]:justify-start");
    expect(headerSource).toContain('<span className="max-[239px]:sr-only">connect.md</span>');
    expect(headerSource).toContain('aria-label={mobileOpen ? "Close navigation" : "Open navigation"}');
    expect(headerSource).toContain('className="inline-flex size-11');
    expect(headerSource).not.toMatch(/SignInButton|UserButton|href="\/workspace"|href="\/account"/u);
  });

  it("provides a global visible keyboard-focus fallback without overriding component-specific rings", () => {
    const globalsSource = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
    expect(globalsSource).toMatch(/:where\(a\[href\], button, input, select, textarea, summary, \[tabindex\]:not\(\[tabindex="-1"\]\)\):focus-visible\s*\{[^}]*outline:\s*2px solid #d7ff5f;[^}]*outline-offset:\s*3px;/u);
    expect(globalsSource).not.toMatch(/:focus-visible\s*\{[^}]*outline:\s*(?:0|none)/u);
  });
});

function walkTsx(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) return walkTsx(path);
    return entry.isFile() && entry.name.endsWith(".tsx") ? [path] : [];
  });
}
