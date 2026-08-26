import { readFileSync } from "node:fs";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Button } from "../components/ui/button";

vi.stubGlobal("React", React);

describe("button pending-state semantics", () => {
  it("marks a shared button busy when its rendered content includes a spinner", () => {
    const markup = renderToStaticMarkup(
      createElement(
        Button,
        { disabled: true },
        createElement("span", { className: "size-4 animate-spin", "aria-hidden": true }),
        "Save changes",
      ),
    );

    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("Save changes");
  });

  it("does not call an ordinary disabled button busy", () => {
    const markup = renderToStaticMarkup(createElement(Button, { disabled: true }, "Complete required fields"));
    expect(markup).not.toContain("aria-busy");
  });

  it("preserves an explicit busy-state override", () => {
    const markup = renderToStaticMarkup(
      createElement(
        Button,
        { "aria-busy": false },
        createElement("span", { className: "animate-spin", "aria-hidden": true }),
        "Decorative rotation",
      ),
    );
    expect(markup).toContain('aria-busy="false"');
  });

  it("gives the three raw spinner buttons explicit busy conditions", () => {
    const archive = readFileSync(new URL("../components/profile-post-archive.tsx", import.meta.url), "utf8");
    const taxonomy = readFileSync(new URL("../components/taxonomy-filter-panel.tsx", import.meta.url), "utf8");

    expect(archive.match(/<button type="button" aria-busy=\{loading \|\| undefined\}/gu)).toHaveLength(2);
    expect(taxonomy).toContain('<button type="button" aria-busy={(state.status === "loading" || state.status === "loading-more") || undefined}');
  });
});
