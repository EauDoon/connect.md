import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { PublishPanel } from "../components/publish-panel";
import { ValidationPanel } from "../components/validation-panel";

vi.mock("@/components/draft-provider", () => ({
  useDraft: () => ({
    kind: "profile",
    localDownloadReceipt: null,
    markdown: "invalid draft",
    masked: false,
    recordLocalDownload: vi.fn(),
  }),
}));

describe("validation status semantics", () => {
  it("reserves the green ready state for a clean validation result", () => {
    const markup = renderToStaticMarkup(createElement(ValidationPanel, { issues: [
      { level: "success", message: "Client validation passed." }
    ] }));

    expect(markup).toContain("Ready to download");
    expect(markup).toContain("text-acid");
    expect(markup).toContain('<span role="status" aria-live="polite" aria-atomic="true"');
    expect(markup.match(/aria-live="polite"/gu)).toHaveLength(1);
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

  it("describes why a validation-blocked download is disabled", () => {
    const markup = renderToStaticMarkup(createElement(PublishPanel, { issues: [
      { level: "error", message: "name is required." },
    ] }));

    expect(markup).toContain('disabled=""');
    expect(markup).toContain('aria-describedby="download-blocked"');
    expect(markup).toContain('id="download-blocked"');
    expect(markup).toContain("Resolve the validation errors above before downloading.");
  });
});
