import { markdownBody, normaliseMarkdown, PROFILE_RESUME_MAX_UTF8_BYTES, scanMarkdownHeadings } from "@/lib/markdown";

export function documentMetrics(markdown: string) {
  const bytes = new TextEncoder().encode(markdown).length;
  if (bytes > PROFILE_RESUME_MAX_UTF8_BYTES) return { bytes, words: 0, readingMinutes: 0, headings: [], limited: true };
  const canonical = normaliseMarkdown(markdown);
  const body = markdownBody(canonical);
  const offset = canonical.length - body.length;
  let cursor = 0;
  let line = 1;
  const headings = scanMarkdownHeadings(body).map((heading) => {
    const start = offset + heading.start;
    while (cursor < start) { if (canonical[cursor] === "\n") line++; cursor++; }
    return { ...heading, start, line };
  });
  const words = (body.match(/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu) ?? []).length;
  return { bytes, words, readingMinutes: Math.ceil(words / 200), headings, limited: false };
}
