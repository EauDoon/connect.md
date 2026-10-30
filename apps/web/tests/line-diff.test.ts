import { describe, expect, it } from "vitest";
import { type DiffHunk, LINE_DIFF_LIMITS, diffLines } from "../lib/line-diff";

function applyHunks(before: string, hunks: DiffHunk[]): string {
  const previous = before.split("\n");
  const output: string[] = [];
  let cursor = 0;
  for (const hunk of hunks) {
    const start = hunk.beforeStart - 1;
    output.push(...previous.slice(cursor, start));
    for (const line of hunk.lines) if (line.type !== "removed") output.push(line.text);
    cursor = start + hunk.beforeCount;
  }
  output.push(...previous.slice(cursor));
  return output.join("\n");
}

function seeded(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

const lines = (count: number, label = "line") => Array.from({ length: count }, (_, index) => `${label} ${index + 1} text`);

describe("bounded line diff", () => {
  it("reports separated edits as separate hunks with exact line numbers", () => {
    const before = lines(30);
    const after = [...before];
    after[4] = "line 5 edited";
    after[14] = "line 15 edited";
    const result = diffLines(before.join("\n"), after.join("\n"));
    expect(result).toMatchObject({ identical: false, exact: true, totalHunks: 2, addedLines: 2, removedLines: 2, truncated: false });
    expect(result.hunks.map(({ beforeStart, beforeCount, afterStart, afterCount }) => [beforeStart, beforeCount, afterStart, afterCount])).toEqual([[2, 7, 2, 7], [12, 7, 12, 7]]);
    expect(result.hunks[0].lines.filter((line) => line.type !== "context")).toEqual([
      { type: "removed", text: "line 5 text", beforeLine: 5 },
      { type: "added", text: "line 5 edited", afterLine: 5 },
    ]);
    expect(result.hunks[0].lines[0]).toEqual({ type: "context", text: "line 2 text", beforeLine: 2, afterLine: 2 });
  });

  it("merges edits whose unchanged gap is within twice the context", () => {
    const before = lines(20);
    const after = [...before];
    after[4] = "edited";
    after[10] = "edited";
    expect(diffLines(before.join("\n"), after.join("\n")).totalHunks).toBe(1);
    expect(diffLines(before.join("\n"), after.join("\n"), { context: 0 }).totalHunks).toBe(2);
  });

  it("handles pure insertion and pure deletion, including at the edges", () => {
    const inserted = diffLines("a\nc", "a\nb\nc");
    expect(inserted).toMatchObject({ addedLines: 1, removedLines: 0, totalHunks: 1 });
    expect(inserted.hunks[0].lines).toContainEqual({ type: "added", text: "b", afterLine: 2 });
    const deleted = diffLines("a\nb\nc", "a\nc");
    expect(deleted).toMatchObject({ addedLines: 0, removedLines: 1 });
    expect(deleted.hunks[0].lines).toContainEqual({ type: "removed", text: "b", beforeLine: 2 });
    expect(applyHunks("x", diffLines("x", "new\nx").hunks)).toBe("new\nx");
    expect(applyHunks("x\ny", diffLines("x\ny", "x").hunks)).toBe("x");
    const large = lines(1500);
    expect(diffLines("start", `start\n${large.join("\n")}`)).toMatchObject({ exact: true, addedLines: 1500, removedLines: 0 });
    // Replacing one line with 1,500 needs more than the edit bound, so callers fall back.
    expect(diffLines("", large.join("\n"))).toMatchObject({ exact: false, hunks: [] });
  });

  it("treats identical and empty input as unchanged", () => {
    expect(diffLines("same\n", "same\n")).toEqual({ identical: true, exact: true, hunks: [], totalHunks: 0, addedLines: 0, removedLines: 0, truncated: false });
    expect(diffLines("", "").identical).toBe(true);
    expect(diffLines("", "a")).toMatchObject({ identical: false, exact: true, addedLines: 1, removedLines: 1 });
  });

  it("reports a final newline change as a real change", () => {
    const result = diffLines("a\nb", "a\nb\n");
    expect(result).toMatchObject({ identical: false, exact: true, addedLines: 1, removedLines: 0 });
    expect(result.hunks[0].lines.at(-1)).toEqual({ type: "added", text: "", afterLine: 3 });
  });

  it("round-trips 200 seeded random edit scripts", () => {
    const random = seeded(20261031);
    const vocabulary = ["alpha", "beta", "gamma", "delta", "", "## Experience", "- item"];
    for (let run = 0; run < 200; run++) {
      const size = Math.floor(random() * 40);
      const before = Array.from({ length: size }, () => vocabulary[Math.floor(random() * vocabulary.length)]);
      const after = [...before];
      const edits = Math.floor(random() * 8);
      for (let edit = 0; edit < edits; edit++) {
        const position = Math.floor(random() * (after.length + 1));
        const choice = random();
        if (choice < 0.34) after.splice(position, 0, vocabulary[Math.floor(random() * vocabulary.length)]);
        else if (choice < 0.67) after.splice(position, 1);
        else if (position < after.length) after[position] = `${after[position]} changed`;
      }
      const source = before.join("\n");
      const target = after.join("\n");
      const result = diffLines(source, target);
      expect(result.exact).toBe(true);
      expect(result.truncated).toBe(false);
      expect(applyHunks(source, result.hunks), `run ${run}`).toBe(target);
      expect(result.removedLines - result.addedLines).toBe(source.split("\n").length - target.split("\n").length);
    }
  });

  it("stops at the edit bound on adversarial input and falls back quickly", () => {
    const before = Array.from({ length: 65536 }, (_, index) => (index % 2 ? "a" : "b")).join("\n");
    const after = Array.from({ length: 65536 }, (_, index) => (index % 3 ? "a" : "c")).join("\n");
    expect(before.length).toBeGreaterThan(127 * 1024);
    const started = performance.now();
    const result = diffLines(before, after);
    expect(performance.now() - started).toBeLessThan(5000);
    expect(result).toMatchObject({ identical: false, exact: false, hunks: [], totalHunks: 0 });
  });

  it("bounds the displayed lines and characters", () => {
    const before = lines(600);
    const after = before.map((line, index) => (index % 2 ? `${line} edited` : line));
    const result = diffLines(before.join("\n"), after.join("\n"));
    expect(result.exact).toBe(true);
    expect(result.truncated).toBe(true);
    expect(result.removedLines).toBe(300);
    expect(result.hunks.reduce((total, hunk) => total + hunk.lines.length, 0)).toBeLessThanOrEqual(LINE_DIFF_LIMITS.displayLines);
    const wide = diffLines("x\n" + "a".repeat(20000), "y\n" + "b".repeat(20000));
    expect(wide.truncated).toBe(true);
    expect(wide.hunks.flatMap((hunk) => hunk.lines).reduce((total, line) => total + line.text.length, 0)).toBeLessThanOrEqual(LINE_DIFF_LIMITS.displayCharacters);
  });

  it("shows both replacements far apart in a long draft, which the region view truncated", () => {
    const before = lines(300);
    const after = [...before];
    after[1] = "line 2 replaced";
    after[289] = "line 290 replaced";
    const result = diffLines(before.join("\n"), after.join("\n"));
    expect(result).toMatchObject({ exact: true, totalHunks: 2, addedLines: 2, removedLines: 2, truncated: false });
    const added = result.hunks.flatMap((hunk) => hunk.lines).filter((line) => line.type === "added");
    expect(added).toEqual([
      { type: "added", text: "line 2 replaced", afterLine: 2 },
      { type: "added", text: "line 290 replaced", afterLine: 290 },
    ]);
  });
});
