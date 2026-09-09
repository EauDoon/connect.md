import { describe, expect, it } from "vitest";
import { compareDrafts } from "../lib/draft-comparison";

describe("checkpoint comparison", () => {
  it("finds the changed region including separated edits without claiming a minimal diff", () => {
    expect(compareDrafts("head\nold\nsame\nold\nend", "head\nnew\nsame\nnew\nend")).toMatchObject({ firstChangedLine: 2, removedLines: 3, addedLines: 3, before: "old\nsame\nold", after: "new\nsame\nnew", truncated: false });
  });
  it("handles insertion, removal, identical bytes and final newline differences", () => {
    expect(compareDrafts("a\nc", "a\nb\nc")).toMatchObject({ before: "", after: "b", removedLines: 0 });
    expect(compareDrafts("a\nb\nc", "a\nc")).toMatchObject({ before: "b", after: "", addedLines: 0 });
    expect(compareDrafts("", "").identical).toBe(true);
    expect(compareDrafts("a", "a\n").identical).toBe(false);
  });
  it("bounds adversarial long lines and many-line displays", () => {
    const result = compareDrafts("x".repeat(100000), "b\n".repeat(50000));
    expect(result.truncated).toBe(true);
    expect(result.before.length).toBe(12000);
    expect(result.after.split("\n")).toHaveLength(80);
  });
});
