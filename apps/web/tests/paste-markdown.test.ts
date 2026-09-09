import { describe, expect, it } from "vitest";
import { parseLocalMarkdownDraft, LOCAL_MARKDOWN_FILE_MAX_BYTES } from "../lib/local-markdown-file";
import { profileStarter, resumeStarter } from "../lib/markdown";

describe("pasted draft import", () => {
  it("detects both kinds and normalizes line endings without converting content", () => {
    expect(parseLocalMarkdownDraft(profileStarter.replaceAll("\n", "\r\n"))).toEqual({ kind: "profile", markdown: profileStarter });
    expect(parseLocalMarkdownDraft(resumeStarter)).toEqual({ kind: "resume", markdown: resumeStarter });
  });
  it("rejects unsupported schemas, duplicate keys, NUL and excessive UTF-8 data", () => {
    expect(() => parseLocalMarkdownDraft("---\nschema: other\n---\n# Draft")).toThrow("schema");
    expect(() => parseLocalMarkdownDraft(profileStarter.replace("name: Your Name", "name: One\nname: Two"))).toThrow();
    expect(() => parseLocalMarkdownDraft(profileStarter + "\0")).toThrow("NUL");
    expect(() => parseLocalMarkdownDraft("😀".repeat(LOCAL_MARKDOWN_FILE_MAX_BYTES / 4 + 1))).toThrow("local-open limit");
  });
});
