import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import NotFound from "../app/not-found";

vi.stubGlobal("React", React);

describe("global not-found recovery", () => {
  it("renders a branded privacy-preserving 404 with two touch-safe recovery paths", () => {
    const markup = renderToStaticMarkup(createElement(NotFound));

    expect(markup).toContain("404 · Not found");
    expect(markup).toContain("This page is not available.");
    expect(markup).toContain("The address may be outdated or incorrect.");
    expect(markup).toContain("This standalone site has no public profile directory. Drafts stay in your browser.");
    expect(markup).toContain('href="/human"');
    expect(markup).toContain("Build a local draft");
    expect(markup).not.toContain('href="/discover"');
    expect(markup).toContain('href="/"');
    expect(markup).toContain(">Home</a>");
    expect(markup.match(/min-h-11/gu)).toHaveLength(2);
    expect(markup).not.toMatch(/\bAPI\b|server-side configuration|sign in|requested route/iu);
  });
});
