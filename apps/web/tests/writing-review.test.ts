import { describe, expect, it } from "vitest";
import { reviewWriting } from "../lib/writing-review";

describe("advisory writing review", () => {
  it("identifies concrete edits with source lines", () => {
    const draft = "# Your Name\n\n## Empty\n\n## Work\n\n- Built a tool\n- Built a tool\n\n[click here](https://example.invalid)\n\n" + "word ".repeat(101);
    const findings = reviewWriting(draft).findings;
    expect(findings.map(({ code, line }) => [code, line])).toEqual([["placeholder", 1], ["empty-section", 3], ["repeated-bullet", 8], ["link-label", 10], ["long-paragraph", 12]]);
  });
  it("does not mark parent sections or fenced bullet examples as empty or repeated", () => {
    expect(reviewWriting("# Name\n\n## Work\n\n### Role\n\nA useful outcome.\n```\n- repeated\n- repeated\n```\n").findings).toEqual([]);
  });
  it("caps rendered suggestions", () => {
    expect(reviewWriting("TODO\n".repeat(30))).toMatchObject({ limited: true });
    expect(reviewWriting("TODO\n".repeat(30)).findings).toHaveLength(20);
  });
});

it("finds skipped heading levels and repeated heading labels outside code", () => {
  const findings = reviewWriting("# Name\ntext\n### Work\ntext\n## Work\ntext\n```\n#### Hidden\n```\n").findings;
  expect(findings.map(({ code, line }) => [code, line])).toEqual([["heading-level", 3], ["repeated-heading", 5]]);
});
