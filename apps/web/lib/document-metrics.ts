import { bodyHeadings } from "@/lib/heading-anchors";
import { markdownBody, normaliseMarkdown, splitFrontmatter, PROFILE_RESUME_MAX_UTF8_BYTES } from "@/lib/markdown";

export function documentMetrics(markdown: string) {
  const bytes = new TextEncoder().encode(markdown).length;
  if (bytes > PROFILE_RESUME_MAX_UTF8_BYTES) return { bytes, words: 0, readingMinutes: 0, headings: [], limited: true };
  const canonical = normaliseMarkdown(markdown);
  const body = markdownBody(canonical);
  const offset = canonical.length - body.length;
  const firstBodyLine = canonical.slice(0, offset).split("\n").length;
  const headings = bodyHeadings(body).map((heading) => ({
    ...heading,
    line: firstBodyLine + heading.line,
    start: offset + heading.start,
    end: offset + heading.end,
  }));
  const words = (body.match(/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu) ?? []).length;
  return { bytes, words, readingMinutes: Math.ceil(words / 200), headings, limited: false };
}

export function filterOutline(headings: ReturnType<typeof documentMetrics>["headings"], query: string) {
  const normalize = (text: string) => text.trim().replace(/\s+/gu, " ").toLocaleLowerCase("en-US");
  const needle = normalize(query);
  return headings.filter((heading) => normalize(heading.text).includes(needle));
}

export function sectionExcerpt(markdown: string, start: number, includeChildren = true) {
  const metrics = documentMetrics(markdown);
  if (metrics.limited) throw new Error("Section export is limited to 128 KiB drafts.");
  const source = normaliseMarkdown(markdown);
  if (/^(?:\uFEFF)?---(?:\n|$)/u.test(source) && !splitFrontmatter(source).hasFrontmatter) throw new Error("Close frontmatter before exporting a body section.");
  const index = metrics.headings.findIndex((heading) => heading.start === start);
  if (index < 0) throw new Error("Choose an available body heading.");
  const heading = metrics.headings[index];
  const end = metrics.headings.slice(index + 1).find((next) => !includeChildren || next.level <= heading.level)?.start ?? source.length;
  return { title: heading.text, markdown: source.slice(start, end) };
}
