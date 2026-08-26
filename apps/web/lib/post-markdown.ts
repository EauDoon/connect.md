import YAML from "yaml";

export const POST_MAX_BYTES = 10_240;
// The API adds id, author handle, and timestamps before validating canonical bytes.
export const POST_SERVER_FIELD_RESERVE_BYTES = 512;
export const POST_MAX_CLIENT_MARKDOWN_BYTES = POST_MAX_BYTES - POST_SERVER_FIELD_RESERVE_BYTES;

export type PostDraftInput = { title: string; topicsText: string; body: string };

export function postTopicsFromText(value: string) {
  return value.split(/[\n,]/u).map((topic) => topic.trim().toLowerCase()).filter(Boolean);
}

export function createPostMarkdown(input: PostDraftInput) {
  const title = input.title.trim();
  const topics = postTopicsFromText(input.topicsText);
  const body = input.body.trim();
  const frontmatter = { schema: "connect.md/post", schema_version: 1, title, topics, visibility: "public" };
  return `---\n${YAML.stringify(frontmatter).trim()}\n---\n\n# ${title}\n${body ? `\n${body}\n` : ""}`;
}

export function utf8ByteLength(value: string) { return new TextEncoder().encode(value).byteLength; }

export function validatePostDraft(input: PostDraftInput) {
  const title = input.title.trim();
  const topics = postTopicsFromText(input.topicsText);
  const markdown = createPostMarkdown(input);
  const issues: string[] = [];
  if (!title || title.length > 160 || /[\r\n]/u.test(title) || title !== input.title || /#$/u.test(title)) issues.push("Title must be 1–160 characters, with no leading/trailing whitespace, line break, or trailing #.");
  if (topics.length === 0) issues.push("Add at least one topic.");
  if (topics.length > 10) issues.push("Use at most ten topics.");
  if (topics.some((topic) => !/^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$/u.test(topic))) issues.push("Topics must be lowercase labels of up to 50 letters, numbers, or hyphens.");
  if (new Set(topics).size !== topics.length) issues.push("Topics must not repeat.");
  const bytes = utf8ByteLength(markdown);
  if (bytes > POST_MAX_CLIENT_MARKDOWN_BYTES) issues.push(`Keep the client Markdown within ${POST_MAX_CLIENT_MARKDOWN_BYTES.toLocaleString()} bytes so the API can add canonical server fields inside the 10 KiB limit.`);
  return { title, topics, markdown, bytes, issues };
}
