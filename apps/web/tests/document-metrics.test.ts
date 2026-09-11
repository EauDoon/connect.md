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

it("filters all headings before the display cap while retaining source offsets", async () => {
  const { filterOutline } = await import("../lib/document-metrics");
  const metrics = documentMetrics(Array.from({ length: 80 }, (_, i) => `## Section ${i}\n`).join(""));
  const selected = filterOutline(metrics.headings, " SECTION 79 ");
  expect(selected).toHaveLength(1);
  expect(selected[0]).toEqual(metrics.headings[79]);
  expect(filterOutline(metrics.headings, "missing")).toEqual([]);
});
