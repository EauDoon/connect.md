import { readFileSync } from "node:fs";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));
vi.stubGlobal("React", React);

import { SiteHeader } from "../components/site-header";

function renderHeader(pathname = "/") {
  navigation.pathname = pathname;
  return renderToStaticMarkup(createElement(SiteHeader));
}

describe("standalone global header", () => {
  it("exposes only create, Markdown, and trust navigation", () => {
    const markup = renderHeader();
    for (const href of ["/human", "/md", "/trust"]) expect(markup).toContain('href="' + href + '"');
    for (const href of ["/discover", "/agent-directory", "/network", "/agents", "/workspace", "/account", "/employer"]) {
      expect(markup).not.toContain('href="' + href + '"');
    }
    expect(markup).not.toMatch(/Sign in|Sign out/u);
  });

  it("uses exact current-page semantics in the rendered navigation", () => {
    const markup = renderHeader("/md");
    expect(markup.match(/href="\/md"/gu)).toHaveLength(1);
    expect(markup.match(/aria-current="page"/gu)).toHaveLength(1);
    expect(markup).not.toContain('href="/human" aria-current="page"');
    expect(markup).not.toContain('href="/trust" aria-current="page"');
  });

  it("requires no server auth decision in the root layout", () => {
    const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
    expect(layout).toContain("<SiteHeader />");
    expect(layout).not.toMatch(/force-dynamic|privateWorkspaceConfiguredFromEnvironment|CLERK_SECRET_KEY/u);
  });
});
