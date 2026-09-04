import { describe, expect, it } from "vitest";

import {
  decodeLocalMarkdownFile,
  LOCAL_MARKDOWN_FILE_MAX_BYTES,
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

  it("accepts bounded CRLF input when its normalized Markdown fits the canonical limit", () => {
    const source = profileStarter.replace(/\n/gu, "\r\n") + "x\r\n".repeat(45_000);
    const buffer = new TextEncoder().encode(source).buffer;
    expect(buffer.byteLength).toBeGreaterThan(PROFILE_RESUME_MAX_UTF8_BYTES);
    const imported = parseLocalMarkdownDraft(decodeLocalMarkdownFile(buffer));
    expect(imported.kind).toBe("profile");
    expect(new TextEncoder().encode(imported.markdown).byteLength).toBeLessThanOrEqual(PROFILE_RESUME_MAX_UTF8_BYTES);
  });

  it("rejects unrelated schemas, malformed YAML, and non-Markdown filenames", () => {
    expect(() => parseLocalMarkdownDraft(profileStarter.replace("connect.md/profile", "example/profile"))).toThrow("schema must be connect.md/profile or connect.md/resume");
    expect(() => parseLocalMarkdownDraft(profileStarter.replace("name: Your Name", "name: ["))).toThrow(/YAML/u);
    expect(localMarkdownFilenameIssue("profile.txt")).toBe("Choose a Markdown file with a .md extension.");
    expect(localMarkdownFilenameIssue("PROFILE.MD")).toBeNull();
  });

  it("rejects invalid UTF-8 and files above the bounded raw-input limit", () => {
    expect(() => decodeLocalMarkdownFile(new Uint8Array([0xc3, 0x28]).buffer)).toThrow("valid UTF-8");
    expect(localMarkdownFileSizeIssue(LOCAL_MARKDOWN_FILE_MAX_BYTES)).toBeNull();
    expect(localMarkdownFileSizeIssue(LOCAL_MARKDOWN_FILE_MAX_BYTES + 1)).toContain("local-open limit");
    expect(() => decodeLocalMarkdownFile(new ArrayBuffer(LOCAL_MARKDOWN_FILE_MAX_BYTES + 1))).toThrow("local-open limit");
  });
});
