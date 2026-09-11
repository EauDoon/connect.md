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

export function filterOutline(headings: ReturnType<typeof documentMetrics>["headings"], query: string) {
  const needle = query.trim().toLocaleLowerCase("en-US");
  return headings.filter((heading) => heading.text.toLocaleLowerCase("en-US").includes(needle));
}

export function sectionExcerpt(markdown: string, start: number) {
  const source = normaliseMarkdown(markdown);
  const metrics = documentMetrics(source);
  if (metrics.limited) throw new Error("Section export is limited to 128 KiB drafts.");
  const index = metrics.headings.findIndex((heading) => heading.start === start);
  if (index < 0) throw new Error("Choose an available body heading.");
  const heading = metrics.headings[index];
  const end = metrics.headings.slice(index + 1).find((next) => next.level <= heading.level)?.start ?? source.length;
  return { title: heading.text, markdown: source.slice(start, end) };
}
