import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { profileStarter, resumeStarter } from "../lib/markdown";
import { validateDraft } from "../lib/validation";

vi.mock("@/components/draft-provider", () => ({ useDraft: () => ({ requestSourceLine: vi.fn() }) }));
const { ValidationPanel } = await import("../components/validation-panel");

const lineOf = (markdown: string, text: string) => markdown.split("\n").findIndex((line) => line.startsWith(text)) + 1;
const issueFor = (markdown: string, fragment: string, kind: "profile" | "resume" = "profile") => {
  const issue = validateDraft(markdown, kind).find((candidate) => candidate.message.includes(fragment));
  expect(issue, fragment).toBeDefined();
  return issue!;
};

const v1Profile = `---
schema: connect.md/profile
schema_version: 1
handle: ada
name: Ada
headline: Engineer
location: London
skills:
  - Analysis
visibility: private
---

# Ada

## About

About text.

## Experience

Experience text.

## Skills

- Analysis
`;

describe("validation issue source lines", () => {
  it("leaves a valid draft without line links", () => {
    expect(validateDraft(profileStarter, "profile")).toEqual([{ level: "success", message: "Client validation passed. Download locally; this site does not upload or publish." }]);
  });

  it("points YAML syntax, alias, and duplicate-key errors at their line", () => {
    const syntax = v1Profile.replace("headline: Engineer", "headline: [unclosed");
    const issue = issueFor(syntax, "Frontmatter YAML is invalid");
    expect(issue.message).toContain(`at line ${issue.line},`);
    expect(issue.line).toBeGreaterThanOrEqual(lineOf(syntax, "headline:"));
    const aliased = v1Profile.replace("location: London", "location: *place");
    expect(issueFor(aliased, "YAML aliases").line).toBe(lineOf(aliased, "location:"));
    const duplicate = v1Profile.replace("location: London", "location: London\nlocation: Paris");
    const duplicateIssue = issueFor(duplicate, "duplicate YAML key");
    expect(duplicateIssue.line).toBe(lineOf(duplicate, "location: Paris"));
    expect(duplicateIssue.message).toContain(`at line ${duplicateIssue.line},`);
  });

  it("points missing frontmatter at the first line", () => {
    expect(issueFor("# Ada\n", "Start with YAML frontmatter").line).toBe(1);
  });

  it("points unknown fields at the first unknown key", () => {
    const unknown = v1Profile.replace("visibility: private", "surprise: yes\nvisibility: private\nanother: no");
    const issue = issueFor(unknown, "unknown frontmatter fields: surprise, another");
    expect(issue.line).toBe(lineOf(unknown, "surprise:"));
    expect(lineOf(unknown, "surprise:")).toBe(10);
  });

  it("points field errors at their key line", () => {
    const overlong = v1Profile.replace("headline: Engineer", `headline: ${"x".repeat(281)}`);
    expect(issueFor(overlong, "headline must be 280 characters or fewer").line).toBe(lineOf(overlong, "headline:"));
    const handle = v1Profile.replace("handle: ada", "handle: Not Valid");
    expect(issueFor(handle, "lowercase handle").line).toBe(lineOf(handle, "handle:"));
    const visibility = v1Profile.replace("visibility: private", "visibility: secret");
    expect(issueFor(visibility, "visibility must be").line).toBe(lineOf(visibility, "visibility:"));
    const nested = profileStarter.replace("  disclosure: none", "  disclosure: shout");
    expect(issueFor(nested, "contact.disclosure is invalid").line).toBe(lineOf(nested, "contact:"));
    const server = v1Profile.replace("visibility: private", "visibility: private\nversion: 0");
    expect(issueFor(server, "version must be a positive integer").line).toBe(lineOf(server, "version:"));
    const placeholder = profileStarter.replace("visibility: private", "visibility: public");
    expect(issueFor(placeholder, "Unspecified location").line).toBe(lineOf(placeholder, "location:"));
    expect(issueFor(placeholder, "Unspecified skill").line).toBe(lineOf(placeholder, "skills:"));
  });

  it("gives no line to a missing required field or flow-style frontmatter", () => {
    const missing = v1Profile.replace("headline: Engineer\n", "");
    expect(issueFor(missing, "headline is required").line).toBeUndefined();
    const flow = "---\n{schema: connect.md/profile, schema_version: 1, handle: ada, name: Ada, location: London, skills: [Analysis], visibility: private}\n---\n\n# Ada\n";
    expect(issueFor(flow, "headline is required").line).toBeUndefined();
  });

  it("points heading syntax, title, and required-heading errors at the heading line", () => {
    const syntax = v1Profile.replace("## Experience", "##  Experience");
    expect(issueFor(syntax, "exactly one space after the # markers").line).toBe(lineOf(syntax, "##  Experience"));
    const wrongTitle = v1Profile.replace("# Ada\n", "# Grace\n");
    expect(issueFor(wrongTitle, "exactly one level-one heading").line).toBe(lineOf(wrongTitle, "# Grace"));
    const secondTitle = v1Profile.replace("## Skills", "# Ada\n\n## Skills");
    expect(issueFor(secondTitle, "exactly one level-one heading").line).toBe(secondTitle.split("\n").lastIndexOf("# Ada") + 1);
    const noTitle = v1Profile.replace("# Ada\n\n", "");
    expect(issueFor(noTitle, "exactly one level-one heading").line).toBe(lineOf(noTitle, "## About"));
    const duplicate = v1Profile.replace("## Skills", "## Experience\n\nAgain.\n\n## Skills");
    expect(issueFor(duplicate, "must appear exactly once").line).toBe(duplicate.split("\n").lastIndexOf("## Experience") + 1);
    const missing = v1Profile.replace("## About", "## Overview");
    expect(issueFor(missing, "must appear exactly once").line).toBeUndefined();
    const order = v1Profile.replace("## About\n\nAbout text.\n\n## Experience\n\nExperience text.", "## Experience\n\nExperience text.\n\n## About\n\nAbout text.");
    // Experience now precedes About, so the link points at the heading that came too early.
    expect(issueFor(order, "must follow this order").line).toBe(lineOf(order, "## Experience"));
    const resume = resumeStarter.replace("## Education", "## Schooling");
    expect(issueFor(resume, "must appear exactly once", "resume").line).toBeUndefined();
  });

  it("points the unsafe HTML warning at the first unsafe tag", () => {
    const unsafe = v1Profile.replace("About text.", "About text.\n\n<script>alert(1)</script>");
    const issue = issueFor(unsafe, "Unsafe HTML");
    expect(issue.level).toBe("warning");
    expect(issue.line).toBe(lineOf(unsafe, "<script>"));
  });

  it("maps CRLF sources to the same lines and keeps messages unchanged", () => {
    const lf = v1Profile.replace("## Experience", "##  Experience").replace("handle: ada", "handle: Bad Handle");
    const crlf = lf.replaceAll("\n", "\r\n");
    expect(validateDraft(crlf, "profile")).toEqual(validateDraft(lf, "profile"));
    expect(validateDraft(lf, "profile").map((issue) => issue.message)).toEqual([
      "Profiles need a lowercase handle using letters, numbers, and hyphens.",
      "Markdown headings require exactly one space after the # markers and no surrounding heading whitespace.",
      "Required headings must appear exactly once: About, Experience, Skills.",
    ]);
  });
});

describe("validation panel line links", () => {
  it("renders an edit link only for issues with a line", () => {
    const markup = renderToStaticMarkup(createElement(ValidationPanel, { issues: [
      { level: "error", message: "visibility must be private or public.", line: 9 },
      { level: "error", message: "headline is required in frontmatter." },
    ] }));
    expect(markup).toContain('aria-label="Edit line 9 in Markdown"');
    expect(markup).toContain(">Line 9</a>: visibility must be private or public.");
    expect(markup.match(/Edit line/gu)).toHaveLength(1);
    expect(markup).toContain("2 blocking issues");
  });
});
