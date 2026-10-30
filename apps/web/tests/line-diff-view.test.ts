import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  kind: "profile",
  markdown: "",
  masked: false,
  checkpoints: [] as Array<{ id: number; label: string; kind: string; markdown: string }>,
  saveCheckpoint: () => undefined,
  renameCheckpoint: () => undefined,
  restoreCheckpoint: () => undefined,
  removeCheckpoint: () => undefined,
}));
vi.mock("@/components/draft-provider", () => ({ useDraft: () => state }));
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  // Open the checkpoint comparison for the first checkpoint during server rendering.
  return { ...actual, useState: (initial: unknown) => actual.useState(initial === null && state.checkpoints.length ? state.checkpoints[0].id : initial) };
});

const { LineDiffView, hunkHeading } = await import("../components/line-diff-view");
const { DraftCheckpoints } = await import("../components/draft-checkpoints");

const lines = (count: number) => Array.from({ length: count }, (_, index) => `line ${index + 1} text`);

function view(before: string, after: string) {
  return renderToStaticMarkup(createElement(LineDiffView, { before, after, label: "Replacement changes", beforeTitle: "Before replacement", afterTitle: "After replacement" }));
}

describe("multi-hunk comparison view", () => {
  it("renders each separated edit as its own hunk with screen-reader labels", () => {
    const before = lines(30);
    const after = [...before];
    after[4] = "line 5 edited";
    after[24] = "line 25 edited";
    const markup = view(before.join("\n"), after.join("\n"));
    expect(markup).toContain("Changes in 2 places: 2 lines added, 2 removed.");
    expect(markup).toContain("Previous lines 2 to 8; current lines 2 to 8");
    expect(markup).toContain("Previous lines 22 to 28; current lines 22 to 28");
    expect(markup).toContain("Removed line 5: </span>line 5 text");
    expect(markup).toContain("Added line 25: </span>line 25 edited");
    expect(markup).not.toContain("line 15 text");
    expect(markup).toContain('role="group" aria-label="Replacement changes"');
  });

  it("names insertions and deletions in hunk headings", () => {
    expect(hunkHeading({ beforeStart: 3, beforeCount: 0, afterStart: 3, afterCount: 2, lines: [] })).toBe("Previous none (before line 3); current lines 3 to 4");
    expect(hunkHeading({ beforeStart: 7, beforeCount: 1, afterStart: 7, afterCount: 0, lines: [] })).toBe("Previous line 7; current none (before line 7)");
  });

  it("says when sources match exactly", () => {
    expect(view("same\n", "same\n")).toContain("The Markdown bytes match exactly.");
  });

  it("falls back to the changed region, and says so, past the diff bounds", () => {
    const before = Array.from({ length: 65536 }, (_, index) => (index % 2 ? "a" : "b")).join("\n");
    const after = Array.from({ length: 65536 }, (_, index) => (index % 3 ? "a" : "c")).join("\n");
    const markup = view(before, after);
    expect(markup).toContain("There are too many separate changes to list individually.");
    expect(markup).toContain('aria-label="Before replacement changed region"');
    expect(markup).toContain("Display limited to 80 lines");
  });

  it("shows a checkpoint comparison as hunks", () => {
    const original = lines(30);
    const current = [...original];
    current[9] = "line 10 changed";
    state.checkpoints = [{ id: 7, label: "Before edits", kind: "profile", markdown: original.join("\n") }];
    state.markdown = current.join("\n");
    const markup = renderToStaticMarkup(createElement(DraftCheckpoints));
    expect(markup).toContain("Before edits compared with the current draft");
    expect(markup).toContain("Changes in 1 place: 1 line added, 1 removed.");
    expect(markup).toContain("Added line 10: </span>line 10 changed");
    expect(markup).toContain('aria-label="Changes from Before edits to the current draft"');
  });
});
