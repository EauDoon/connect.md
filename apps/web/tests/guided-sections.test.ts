import { describe, expect, it } from "vitest";

import { appendGuidedEntry, parseGuidedSection, removeGuidedEntry, replaceGuidedEntry, swapGuidedEntries, type GuidedEntryBlock } from "../lib/guided-sections";
import { humanFieldsFromMarkdown, profileStarter, resumeStarter } from "../lib/markdown";


const guided = (source: string) => parseGuidedSection(source, "experience").filter((block): block is GuidedEntryBlock => block.type === "guided");

describe("lossless guided career sections", () => {
  it("recognizes narrow card prefixes but leaves complex Markdown, comments, and fences raw", () => {
    const source = "Intro kept.\n\n### Engineer · Example Co\n\n2022–present\n\n- Shipped safely\n\n```md\n### Hidden role\n```\n\n### Complex role\n\nParagraph one.\n\nParagraph two.\n\n<!-- keep me -->";
    const blocks = parseGuidedSection(source, "experience");
    expect(blocks.filter((block) => block.type === "guided")).toHaveLength(2);
    expect(blocks.filter((block) => block.type === "raw").map((block) => block.source).join("")).toContain("Intro kept.");
    expect(blocks.filter((block) => block.type === "raw").map((block) => block.source).join("")).toContain("### Hidden role");
    expect(blocks.filter((block) => block.type === "raw").map((block) => block.source).join("")).toContain("Paragraph two.");
    expect(blocks.filter((block) => block.type === "raw").map((block) => block.source).join("")).toContain("<!-- keep me -->");
  });

  it("edits and removes only the selected source range", () => {
    const source = "RAW PREFIX\n\n### Engineer · Example Co\n\n- Kept systems safe\n\nRAW SUFFIX";
    const [entry] = guided(source);
    const replaced = replaceGuidedEntry(source, entry, { ...entry.value, primary: "Staff Engineer", highlights: ["Reduced incidents"] });
    expect(replaced).toContain("RAW PREFIX");
    expect(replaced).toContain("RAW SUFFIX");
    expect(replaced).toContain("### Staff Engineer · Example Co");
    expect(removeGuidedEntry(source, entry)).toBe("RAW PREFIX\n\n\n\nRAW SUFFIX");
  });

  it("adds and reorders multiple cards while preserving interleaved raw bytes", () => {
    const first = appendGuidedEntry("RAW", { kind: "experience", primary: "Engineer", secondary: "A", context: "2020", highlights: ["One"] });
    const second = appendGuidedEntry(`${first}\n\n<!-- exact raw anchor -->`, { kind: "experience", primary: "Lead", secondary: "B", context: "2024", highlights: ["Two"] });
    const entries = guided(second);
    const swapped = swapGuidedEntries(second, entries[0], entries[1]);
    expect(swapped.indexOf("### Lead · B")).toBeLessThan(swapped.indexOf("### Engineer · A"));
    expect(swapped).toContain("\n\n<!-- exact raw anchor -->\n\n");
  });

  it("collapses structural newlines supplied through form values", () => {
    const source = appendGuidedEntry("", { kind: "education", primary: "University\n#### Injected", secondary: "Degree", context: "2020\n2024", highlights: ["Research\n- extra"] });
    expect(source).toContain("### University #### Injected · Degree");
    expect(source).not.toContain("\n#### Injected");
    expect(source).toContain("- Research - extra");
  });

  it("keeps context values from creating Markdown block structure", () => {
    for (const context of ["## Skills", "> quote", "- item", "```md"]) {
      const source = appendGuidedEntry("", { kind: "experience", primary: "Engineer", secondary: "Example", context, highlights: [] });
      expect(source).toContain(`Context: ${context}`);
      expect(source.match(/^### /gmu)).toHaveLength(1);
      expect(source).not.toMatch(/^## Skills$/mu);
    }
  });

  it("starts new profile and resume career sections as guided cards without advanced residue", () => {
    const profile = parseGuidedSection(humanFieldsFromMarkdown(profileStarter, "profile").experience, "experience");
    const education = parseGuidedSection(humanFieldsFromMarkdown(resumeStarter, "resume").education, "education");
    expect(profile.filter((block) => block.type === "guided")).toHaveLength(1);
    expect(education.filter((block) => block.type === "guided")).toHaveLength(1);
    expect([...profile, ...education].filter((block) => block.type === "raw" && block.source.trim())).toHaveLength(0);
  });
});
