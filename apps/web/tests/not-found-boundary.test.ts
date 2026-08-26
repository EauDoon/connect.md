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
    expect(markup).toContain("It may have moved, been withdrawn, or never been published.");
    expect(markup).toContain("Private and unpublished records are never exposed through this page.");
    expect(markup).toContain('href="/discover"');
    expect(markup).toContain("Explore public records");
    expect(markup).toContain('href="/"');
    expect(markup).toContain(">Home</a>");
    expect(markup.match(/min-h-11/gu)).toHaveLength(2);
    expect(markup).not.toMatch(/\bAPI\b|server-side configuration|sign in|requested route/iu);
  });
});
