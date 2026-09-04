import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ValidationPanel } from "../components/validation-panel";

describe("validation status semantics", () => {
  it("reserves the green ready state for a clean validation result", () => {
    const markup = renderToStaticMarkup(createElement(ValidationPanel, { issues: [
      { level: "success", message: "Client validation passed." }
    ] }));

    expect(markup).toContain("Ready to download");
    expect(markup).toContain("text-acid");
    expect(markup).not.toContain("Ready with");
  });

  it("shows a distinct amber state for a downloadable warning", () => {
    const markup = renderToStaticMarkup(createElement(ValidationPanel, { issues: [
      { level: "warning", message: "Unsafe HTML is removed in the preview." }
    ] }));

    expect(markup).toContain("Ready with 1 warning");
    expect(markup).toContain("text-amber-100");
    expect(markup).not.toContain("Ready to download");
  });

  it("counts blocking errors without presenting a ready state", () => {
    const markup = renderToStaticMarkup(createElement(ValidationPanel, { issues: [
      { level: "error", message: "name is required." },
      { level: "error", message: "headline is required." }
    ] }));

    expect(markup).toContain("2 blocking issues");
    expect(markup).toContain("text-red-200");
    expect(markup).not.toContain("Ready");
  });
});
