import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ kind: "profile", markdown: "# Current\n", localDownloadReceipt: null as null | { kind: string; markdown: string; filename: string } }));
vi.mock("@/components/draft-provider", () => ({ useDraft: () => state }));
import { DownloadComparison } from "../components/download-comparison";

it("compares exact current bytes to the last download without inventing an earlier receipt", () => {
  expect(renderToStaticMarkup(createElement(DownloadComparison))).toBe("");
  state.localDownloadReceipt = { kind: "resume", markdown: "# Previous\n", filename: "previous.md" };
  const changed = renderToStaticMarkup(createElement(DownloadComparison));
  expect(changed).toContain("Previous");
  expect(changed).toContain("Current");
  expect(changed).toContain("resume to profile");
  state.localDownloadReceipt = { kind: "profile", markdown: state.markdown, filename: "current.md" };
  expect(renderToStaticMarkup(createElement(DownloadComparison))).toContain("match exactly");
});
