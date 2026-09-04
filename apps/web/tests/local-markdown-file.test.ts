import { describe, expect, it } from "vitest";

import {
  decodeLocalMarkdownFile,
  localMarkdownFilenameIssue,
  localMarkdownFileSizeIssue,
  parseLocalMarkdownDraft,
} from "../lib/local-markdown-file";
import { PROFILE_RESUME_MAX_UTF8_BYTES, profileStarter, resumeStarter } from "../lib/markdown";

describe("local Markdown file opening", () => {
  it("recognizes profile and resume files from canonical frontmatter", () => {
    expect(parseLocalMarkdownDraft(profileStarter)).toEqual({ kind: "profile", markdown: profileStarter });
    expect(parseLocalMarkdownDraft(resumeStarter)).toEqual({ kind: "resume", markdown: resumeStarter });
  });

  it("normalizes CRLF input without changing the detected document kind", () => {
    const imported = parseLocalMarkdownDraft(resumeStarter.replace(/\n/gu, "\r\n"));
    expect(imported.kind).toBe("resume");
    expect(imported.markdown).toBe(resumeStarter);
  });

  it("rejects unrelated schemas, malformed YAML, and non-Markdown filenames", () => {
    expect(() => parseLocalMarkdownDraft(profileStarter.replace("connect.md/profile", "example/profile"))).toThrow("schema must be connect.md/profile or connect.md/resume");
    expect(() => parseLocalMarkdownDraft(profileStarter.replace("name: Your Name", "name: ["))).toThrow(/YAML/u);
    expect(localMarkdownFilenameIssue("profile.txt")).toBe("Choose a Markdown file with a .md extension.");
    expect(localMarkdownFilenameIssue("PROFILE.MD")).toBeNull();
  });

  it("rejects invalid UTF-8 and files above the canonical byte limit", () => {
    expect(() => decodeLocalMarkdownFile(new Uint8Array([0xc3, 0x28]).buffer)).toThrow("valid UTF-8");
    expect(localMarkdownFileSizeIssue(PROFILE_RESUME_MAX_UTF8_BYTES)).toBeNull();
    expect(localMarkdownFileSizeIssue(PROFILE_RESUME_MAX_UTF8_BYTES + 1)).toContain("byte limit");
    expect(() => decodeLocalMarkdownFile(new ArrayBuffer(PROFILE_RESUME_MAX_UTF8_BYTES + 1))).toThrow("byte limit");
  });
});
