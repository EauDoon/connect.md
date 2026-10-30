import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ kind: "profile", markdown: "# Current\n", localDownloadReceipt: null as null | { kind: string; markdown: string; filename: string } }));
vi.mock("@/components/draft-provider", () => ({ useDraft: () => state }));
import { DownloadComparison } from "../components/download-comparison";

it("compares exact current bytes to the last download without inventing an earlier receipt", () => {
  expect(renderToStaticMarkup(createElement(DownloadComparison, { defaultOpen: true }))).toBe("");
  state.localDownloadReceipt = { kind: "resume", markdown: "# Previous\n", filename: "previous.md" };
  const changed = renderToStaticMarkup(createElement(DownloadComparison, { defaultOpen: true }));
  expect(changed).toContain("Previous");
  expect(changed).toContain("Current");
  expect(changed).toContain("resume to profile");
  expect(changed).toContain("Removed line 1: </span># Previous");
  expect(changed).toContain("Added line 1: </span># Current");
  state.localDownloadReceipt = { kind: "profile", markdown: state.markdown, filename: "current.md" };
  expect(renderToStaticMarkup(createElement(DownloadComparison, { defaultOpen: true }))).toContain("match exactly");
});

it("computes nothing while the comparison is collapsed", () => {
  state.localDownloadReceipt = { kind: "profile", markdown: "# Previous\n", filename: "previous.md" };
  const collapsed = renderToStaticMarkup(createElement(DownloadComparison));
  expect(collapsed).toContain("Changes since last Markdown download");
  expect(collapsed).not.toContain("Last download comparison");
  expect(collapsed).not.toContain("# Previous");
});
