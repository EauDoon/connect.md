import { describe, expect, it } from "vitest";
import { documentMetrics } from "../lib/document-metrics";

describe("document outline", () => {
  it("reports canonical source offsets while skipping metadata, code and comments", () => {
    const source = "---\nname: metadata words\n---\n\n# Zoë\n```md\n# Code\n```\n<!--\n# Hidden\n-->\n## Work\nTwo words.\n";
    const metrics = documentMetrics(source);
    expect(metrics.headings.map(({ text, line }) => ({ text, line }))).toEqual([{ text: "Zoë", line: 5 }, { text: "Work", line: 12 }]);
    expect(source.slice(metrics.headings[1].start)).toMatch(/^## Work/);
    expect(metrics.bytes).toBe(new TextEncoder().encode(source).length);
    expect(metrics.readingMinutes).toBe(1);
  });
  it("skips analysis for oversized input and handles empty drafts", () => {
    expect(documentMetrics("x".repeat(131073))).toMatchObject({ limited: true, headings: [] });
    expect(documentMetrics("")).toMatchObject({ words: 0, readingMinutes: 0, headings: [] });
  });
});
