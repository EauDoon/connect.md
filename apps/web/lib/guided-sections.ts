import { scanMarkdownHeadings } from "@/lib/markdown";


export type GuidedEntryKind = "experience" | "education";

export type GuidedEntryValue = {
  kind: GuidedEntryKind;
  primary: string;
  secondary: string;
  context: string;
  highlights: string[];
};

export type GuidedSectionBlock =
  | { type: "guided"; start: number; end: number; source: string; value: GuidedEntryValue }
  | { type: "raw"; start: number; end: number; source: string };

export type GuidedEntryBlock = Extract<GuidedSectionBlock, { type: "guided" }>;

function oneLine(value: string) {
  return value.replace(/[\r\n]+/gu, " ").replace(/\s+/gu, " ").trim();
}

function parseCandidate(source: string, kind: GuidedEntryKind): { value: GuidedEntryValue; consumed: number } | null {
  const lines = [...source.matchAll(/[^\n]*(?:\n|$)/gu)].filter((match) => match[0].length > 0);
  const headingLine = lines[0]?.[0].replace(/\n$/u, "") ?? "";
  const heading = headingLine.match(/^### ([^\n]+)$/u);
  if (!heading) return null;
  const [primaryPart, ...secondaryParts] = heading[1].split(" · ");
  const primary = primaryPart.trim();
  const secondary = secondaryParts.join(" · ").trim();
  if (!primary) return null;
  let index = 1;
  let consumed = headingLine.length;
  const lineText = (lineIndex: number) => lines[lineIndex]?.[0].replace(/\n$/u, "") ?? "";
  while (index < lines.length && lineText(index).trim() === "") index += 1;
  let context = "";
  const candidateContext = lineText(index);
  if (candidateContext && !candidateContext.startsWith("- ")) {
    if (/^(?:#|>|`|~|<|\d+[.)]\s|\s)/u.test(candidateContext)) return null;
    context = candidateContext.startsWith("Context: ") ? candidateContext.slice("Context: ".length).trim() : candidateContext.trim();
    consumed = (lines[index].index ?? 0) + candidateContext.length;
    index += 1;
    while (index < lines.length && lineText(index).trim() === "") index += 1;
  }
  const highlights: string[] = [];
  while (index < lines.length) {
    const line = lineText(index);
    if (!line.startsWith("- ") || !line.slice(2).trim()) break;
    highlights.push(line.slice(2).trim());
    consumed = (lines[index].index ?? 0) + line.length;
    index += 1;
  }
  return { value: { kind, primary, secondary, context, highlights }, consumed };
}

export function parseGuidedSection(source: string, kind: GuidedEntryKind): GuidedSectionBlock[] {
  const headings = scanMarkdownHeadings(source).filter((heading) => heading.level === 3);
  const blocks: GuidedSectionBlock[] = [];
  let cursor = 0;
  for (const [index, heading] of headings.entries()) {
    const candidateLimit = headings[index + 1]?.start ?? source.length;
    const candidateSource = source.slice(heading.start, candidateLimit);
    if (heading.start > cursor) blocks.push({ type: "raw", start: cursor, end: heading.start, source: source.slice(cursor, heading.start) });
    const parsed = parseCandidate(candidateSource, kind);
    if (parsed) {
      const candidateEnd = heading.start + parsed.consumed;
      blocks.push({ type: "guided", start: heading.start, end: candidateEnd, source: source.slice(heading.start, candidateEnd), value: parsed.value });
      cursor = candidateEnd;
    } else {
      blocks.push({ type: "raw", start: heading.start, end: candidateLimit, source: candidateSource });
      cursor = candidateLimit;
    }
  }
  if (cursor < source.length) blocks.push({ type: "raw", start: cursor, end: source.length, source: source.slice(cursor) });
  if (blocks.length === 0 && source) blocks.push({ type: "raw", start: 0, end: source.length, source });
  return blocks;
}

export function serializeGuidedEntry(value: GuidedEntryValue) {
  const primary = oneLine(value.primary) || (value.kind === "experience" ? "Role" : "Institution");
  const secondary = oneLine(value.secondary);
  const context = oneLine(value.context);
  const heading = `### ${primary}${secondary ? ` · ${secondary}` : ""}`;
  const highlights = value.highlights.map(oneLine).filter(Boolean).map((item) => `- ${item}`).join("\n");
  return [heading, context ? `Context: ${context}` : "", highlights].filter(Boolean).join("\n\n");
}

export function replaceGuidedEntry(source: string, block: GuidedEntryBlock, value: GuidedEntryValue) {
  return `${source.slice(0, block.start)}${serializeGuidedEntry(value)}${source.slice(block.end)}`;
}

export function appendGuidedEntry(source: string, value: GuidedEntryValue) {
  const separator = source.length === 0 || source.endsWith("\n\n") ? "" : source.endsWith("\n") ? "\n" : "\n\n";
  return `${source}${separator}${serializeGuidedEntry(value)}`;
}

export function removeGuidedEntry(source: string, block: GuidedEntryBlock) {
  return `${source.slice(0, block.start)}${source.slice(block.end)}`;
}

export function swapGuidedEntries(source: string, first: GuidedEntryBlock, second: GuidedEntryBlock) {
  const [earlier, later] = first.start < second.start ? [first, second] : [second, first];
  return `${source.slice(0, earlier.start)}${serializeGuidedEntry(later.value)}${source.slice(earlier.end, later.start)}${serializeGuidedEntry(earlier.value)}${source.slice(later.end)}`;
}
