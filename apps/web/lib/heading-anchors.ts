/**
 * GitHub-style heading anchors, shared by the sanitized preview (which marks
 * rendered headings) and Writing review (which checks "#fragment" links).
 *
 * The slug rule follows github-slugger: lowercase, drop everything except
 * letters, marks, numbers, connector punctuation, spaces and hyphens, turn
 * each space into "-", and suffix repeats with -1, -2, and so on. Other
 * Markdown readers may slug differently, so fragment findings stay advisory.
 */

import { markdownBody } from "@/lib/markdown";

export type AnchorRegistry = Map<string, number>;

/** Slug `text` and record it, suffixing repeats exactly as github-slugger does. */
export function headingSlug(text: string, seen: AnchorRegistry): string {
  const base = text.toLowerCase().replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, "").replace(/ /gu, "-");
  let slug = base;
  while (seen.has(slug)) {
    const count = (seen.get(base) ?? 0) + 1;
    seen.set(base, count);
    slug = `${base}-${count}`;
  }
  seen.set(slug, 0);
  return slug;
}

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'", nbsp: " " };

/** The rendered text of an inline Markdown heading, approximated from source. */
export function headingText(source: string): string {
  return source
    .replace(/(`+)(.*?)(?<!`)\1(?!`)/gu, (_, _ticks: string, code: string) => /^ .* $/u.test(code) ? code.slice(1, -1) : code)
    .replace(/!\[[^\]]*\]\([^)]*\)/gu, "")
    .replace(/\[([^\]]*)\](?:\([^)]*\)|\[[^\]]*\])/gu, "$1")
    .replace(/<[^>\n]+>/gu, "")
    .replace(/\\([!-/:-@[-`{-~])/gu, "$1")
    .replace(/&([a-z]+);/giu, (entity, name: string) => ENTITIES[name.toLowerCase()] ?? entity)
    .replace(/\*+([^*\s](?:[^*]*?[^*\s])?)\*+/gu, "$1")
    .replace(/(^|[^\p{L}\p{N}_])_+([^_]+?)_+(?=$|[^\p{L}\p{N}_])/gu, "$1$2");
}

const FENCE = /^ {0,3}(`{3,}|~{3,})/u;
const ATX = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$/u;
const SETEXT = /^ {0,3}(?:=+|-+)[ \t]*$/u;
const NOT_PARAGRAPH = /^(?: {0,3}(?:[-*+>]|\d{1,9}[.)])(?:[ \t]|$)| {0,3}\|| {4}|\t| {0,3}<| {0,3}(?:[-*_][ \t]*){3,}$)/u;

/** Headings in document order with their approximate rendered text. Fences and HTML comments are skipped. */
export function documentHeadings(markdown: string): Array<{ text: string; line: number }> {
  const headings: Array<{ text: string; line: number }> = [];
  const body = markdownBody(markdown);
  let fence: string | null = null;
  let inComment = false;
  let paragraph: { lines: string[]; line: number } | null = null;
  for (const [index, line] of body.split("\n").entries()) {
    if (fence) {
      const closing = line.match(/^ {0,3}(`{3,}|~{3,})[ \t]*$/u)?.[1];
      if (closing && closing[0] === fence[0] && closing.length >= fence.length) fence = null;
      continue;
    }
    if (inComment) {
      if (line.includes("-->")) inComment = false;
      continue;
    }
    const opening = line.match(FENCE)?.[1];
    if (opening) { fence = opening; paragraph = null; continue; }
    if (/^ {0,3}<!--/u.test(line)) { if (!line.includes("-->")) inComment = true; paragraph = null; continue; }
    const atx = line.match(ATX);
    if (atx) {
      const content = (atx[2] ?? "").replace(/(?:^|[ \t]+)#+$/u, "").trim();
      headings.push({ text: headingText(content), line: index });
      paragraph = null;
      continue;
    }
    if (paragraph && SETEXT.test(line)) {
      headings.push({ text: headingText(paragraph.lines.join("\n").trim()), line: paragraph.line });
      paragraph = null;
      continue;
    }
    if (!line.trim() || NOT_PARAGRAPH.test(line)) { paragraph = null; continue; }
    if (paragraph) paragraph.lines.push(line.trim());
    else paragraph = { lines: [line.trim()], line: index };
  }
  return headings;
}

/** Every anchor the preview creates for this document, title included. */
export function documentAnchors(markdown: string): Set<string> {
  const seen: AnchorRegistry = new Map();
  return new Set(documentHeadings(markdown).map((heading) => headingSlug(heading.text, seen)));
}

type HastNode = { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: HastNode[] };

function hastText(node: HastNode): string {
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(hastText).join("");
}

/**
 * Rehype transform that marks each rendered heading with data-md-anchor. It
 * runs after sanitizing, uses data attributes rather than ids (so two previews
 * on one page cannot collide), and seeds the registry with any omitted title
 * so duplicate suffixes match Writing review.
 */
export function rehypeHeadingAnchors(options: { seed?: string[] } = {}) {
  return (tree: HastNode) => {
    const seen: AnchorRegistry = new Map();
    for (const text of options.seed ?? []) headingSlug(text, seen);
    const visit = (node: HastNode) => {
      if (node.type === "element" && /^h[1-6]$/u.test(node.tagName ?? "")) {
        node.properties = { ...node.properties, dataMdAnchor: headingSlug(hastText(node), seen) };
        return;
      }
      for (const child of node.children ?? []) visit(child);
    };
    visit(tree);
  };
}
