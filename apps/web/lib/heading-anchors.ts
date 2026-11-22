/**
 * GitHub-style heading anchors, shared by the sanitized preview (which marks
 * rendered headings) and Writing review (which checks "#fragment" links).
 *
 * The slug rule follows github-slugger: lowercase, drop everything except
 * letters, marks, numbers, connector punctuation, spaces and hyphens, turn
 * each space into "-", and suffix repeats with -1, -2, and so on. Other
 * Markdown readers may slug differently, so fragment findings stay advisory.
 */

import { toString } from "mdast-util-to-string";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";
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

// Match the parser and extensions used by MarkdownPreview. Only document-level
// headings start outline sections; headings nested in lists or quotes do not.
const parser = unified().use(remarkParse).use(remarkGfm).freeze();

export function bodyHeadings(body: string) {
  return parser.parse(body).children.flatMap((node) => {
    if (node.type !== "heading" || !node.position) return [];
    const { start, end } = node.position;
    if (start.offset === undefined || end.offset === undefined) return [];
    return [{
      text: toString(node, { includeHtml: false, includeImageAlt: false }),
      level: node.depth,
      line: start.line - 1,
      // The parser starts at the marker. Source selections include indentation.
      start: start.offset - (start.column - 1),
      end: end.offset,
    }];
  });
}

export function documentHeadings(markdown: string) {
  return bodyHeadings(markdownBody(markdown));
}

export function anchorsForHeadings(headings: ReadonlyArray<{ text: string }>): Set<string> {
  const seen: AnchorRegistry = new Map();
  return new Set(headings.map((heading) => headingSlug(heading.text, seen)));
}

/** Every top-level heading anchor, title included. */
export function documentAnchors(markdown: string): Set<string> {
  return anchorsForHeadings(documentHeadings(markdown));
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
