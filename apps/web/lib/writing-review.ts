import { documentMetrics } from "@/lib/document-metrics";
import { markdownBody, normaliseMarkdown } from "@/lib/markdown";

export type WritingFinding = { code: string; line: number; message: string };
export function reviewWriting(markdown: string) {
  const metrics = documentMetrics(markdown);
  if (metrics.limited) return { findings: [] as WritingFinding[], limited: true };
  const source = normaliseMarkdown(markdown);
  const findings: WritingFinding[] = [];
  const add = (code: string, line: number, message: string) => findings.push({ code, line, message });
  for (const [index, line] of source.split("\n").entries()) {
    if (/\b(?:TODO|TBD|Your Name|Your professional headline|Unspecified skill|Unspecified occupation)\b/iu.test(line)) add("placeholder", index + 1, "Replace starter or unfinished wording with facts you can support, or remove it.");
    if (/\[(?:click here|here|link)\]\(/iu.test(line)) add("link-label", index + 1, "Give this link a descriptive label so readers know its destination.");
  }
  const headingLabels = new Set<string>();
  for (const [index, heading] of metrics.headings.entries()) {
    const previous = metrics.headings[index - 1];
    if (previous && heading.level > previous.level + 1) add("heading-level", heading.line, "This heading skips a level. Consider a consistent hierarchy for readers and assistive navigation.");
    const label = heading.text.trim().toLocaleLowerCase("en-US");
    if (headingLabels.has(label)) add("repeated-heading", heading.line, "This heading label appears earlier. Consider a more specific label if these sections serve different purposes.");
    headingLabels.add(label);
    const next = metrics.headings[index + 1];
    const lineEnd = source.indexOf("\n", heading.start);
    const content = source.slice(lineEnd < 0 ? source.length : lineEnd, next?.start ?? source.length).trim();
    if (!content && (!next || next.level <= heading.level)) add("empty-section", heading.line, "This heading has no content. Add a useful detail or remove the empty section.");
  }
  const body = markdownBody(source);
  const bodyLine = source.slice(0, source.length - body.length).split("\n").length;
  let fence: string | null = null;
  let paragraphWords = 0;
  let paragraphLine = bodyLine;
  const bullets = new Set<string>();
  const flush = () => {
    if (paragraphWords > 100) add("long-paragraph", paragraphLine, "This paragraph exceeds 100 space-separated words. Consider splitting it into shorter, concrete points.");
    paragraphWords = 0;
  };
  for (const [index, line] of body.split("\n").entries()) {
    const marker = line.match(/^\s{0,3}(`{3,}|~{3,})/u)?.[1];
    if (marker) { flush(); if (!fence) fence = marker; else if (marker[0] === fence[0] && marker.length >= fence.length) fence = null; continue; }
    if (fence) continue;
    if (!line.trim() || /^#{1,6}\s/u.test(line)) { flush(); continue; }
    const bullet = line.match(/^\s*[-*+]\s+(.+)$/u)?.[1].trim().toLocaleLowerCase("en-US");
    if (bullet) {
      flush();
      if (bullets.has(bullet)) add("repeated-bullet", bodyLine + index, "This bullet repeats an earlier point. Keep it only if the repetition helps this document.");
      bullets.add(bullet);
    } else {
      if (!paragraphWords) paragraphLine = bodyLine + index;
      paragraphWords += line.trim().split(/\s+/u).length;
    }
  }
  flush();
  findings.sort((a, b) => a.line - b.line || a.code.localeCompare(b.code));
  return { findings: findings.slice(0, 20), limited: findings.length > 20 };
}
