import { readFileSync } from "node:fs";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import TrustPage, { metadata } from "../app/trust/page";

const source = readFileSync(new URL("../app/trust/page.tsx", import.meta.url), "utf8");

vi.stubGlobal("React", React);

describe("standalone privacy boundary", () => {
  it("publishes canonical metadata for the public explanation", () => {
    expect(metadata).toMatchObject({
      title: "Privacy and data",
      alternates: { canonical: "/trust" },
    });
    expect(metadata).not.toHaveProperty("robots");
  });

  it("states exactly what remains local and what Vercel serves", () => {
    const markup = renderToStaticMarkup(createElement(TrustPage));
    for (const statement of [
      "Your draft stays in your browser.",
      "Held in this browser session",
      "Served publicly by Vercel",
      "No hidden persistence",
      "Download is local",
      "Save before leaving",
      "The file remains yours",
    ]) expect(markup).toContain(statement);
    expect(markup).toContain("no account, publishing API, database, file-upload service, messaging system, or analytics code");
    expect(markup).toContain("localStorage, sessionStorage, IndexedDB, cookies, a server action, or an API route");
    expect(markup).toContain("immediately revokes the temporary object URL");
  });

  it("links only to the standalone workflow with accessible touch targets", () => {
    const markup = renderToStaticMarkup(createElement(TrustPage));
    for (const href of ["/human", "/md", "/agent-readme.md", "/llms.txt"]) {
      expect(markup).toContain(`href="${href}"`);
    }
    for (const href of ["/discover", "/account", "/agents", "/workspace", "/employer", "/organizations", "/jobs", "/openapi.json"]) {
      expect(markup).not.toContain(`href="${href}"`);
    }
    const links = [...markup.matchAll(/<(?:a|link)\b[^>]*class="([^"]*)"[^>]*>/gu)];
    for (const [, className] of links) expect(className.split(/\s+/u)).toContain("min-h-11");
  });

  it("has no auth, recruiting, split-origin, or backend branches", () => {
    expect(source).not.toMatch(/force-dynamic|CLERK|recruiting|NEXT_PUBLIC_API_BASE_URL|publicProtocolUrl|privateWorkspaceConfiguredFromEnvironment/u);
    expect(source).toContain('href="/agent-readme.md" type="text/markdown"');
    expect(source).toContain('href="/llms.txt" type="text/plain"');
  });
});
