import {
  frontmatterParseIssue,
  normaliseMarkdown,
  PROFILE_RESUME_MAX_UTF8_BYTES,
  splitFrontmatter,
  type DocumentKind,
} from "@/lib/markdown";

export type LocalMarkdownDraft = {
  kind: DocumentKind;
  markdown: string;
};

export function localMarkdownFilenameIssue(filename: string) {
  return /\.md$/iu.test(filename.trim()) ? null : "Choose a Markdown file with a .md extension.";
}

export function localMarkdownFileSizeIssue(byteLength: number) {
  return byteLength > PROFILE_RESUME_MAX_UTF8_BYTES
    ? `The file exceeds the ${PROFILE_RESUME_MAX_UTF8_BYTES.toLocaleString("en-US")} byte limit.`
    : null;
}

export function decodeLocalMarkdownFile(buffer: ArrayBuffer) {
  const sizeIssue = localMarkdownFileSizeIssue(buffer.byteLength);
  if (sizeIssue) throw new Error(sizeIssue);
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    throw new Error("The file must be valid UTF-8 text.");
  }
}

export function parseLocalMarkdownDraft(source: string): LocalMarkdownDraft {
  const parseIssue = frontmatterParseIssue(source);
  if (parseIssue) throw new Error(parseIssue);

  const { attributes } = splitFrontmatter(source);
  const kind = attributes.schema === "connect.md/profile"
    ? "profile"
    : attributes.schema === "connect.md/resume"
      ? "resume"
      : null;
  if (!kind) throw new Error("The file schema must be connect.md/profile or connect.md/resume.");

  return { kind, markdown: normaliseMarkdown(source) };
}
