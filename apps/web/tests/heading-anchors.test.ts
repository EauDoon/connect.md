import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarkdownPreview } from "../components/markdown-preview";
import { documentAnchors, headingSlug, headingText } from "../lib/heading-anchors";
import { reviewWriting } from "../lib/writing-review";

const slug = (text: string, seen = new Map<string, number>()) => headingSlug(text, seen);

describe("GitHub-style heading slugs", () => {
  it("lowercases ASCII, keeps hyphens and underscores, and drops punctuation", () => {
    expect(slug("Experience")).toBe("experience");
    expect(slug("Work & Projects: 2024!")).toBe("work--projects-2024");
    expect(slug("snake_case and-dash")).toBe("snake_case-and-dash");
  });

  it("keeps Unicode letters, marks, and numbers", () => {
    expect(slug("Café Résumé")).toBe("café-résumé");
    expect(slug("経歴 2024")).toBe("経歴-2024");
    expect(slug("Ελληνικά")).toBe("ελληνικά");
  });

  it("suffixes repeats in order, as github-slugger does", () => {
    const seen = new Map<string, number>();
    expect(["Work", "Work", "Work", "Work 1"].map((text) => headingSlug(text, seen))).toEqual(["work", "work-1", "work-2", "work-1-1"]);
  });

  it("strips inline markup to its rendered text", () => {
    expect(headingText("*Bold* _emph_ `code` [link](https://example.invalid) ![img](x.png)")).toBe("Bold emph code link ");
    expect(headingText("snake_case \\# escaped &amp; done")).toBe("snake_case # escaped & done");
  });
});

describe("document anchors", () => {
  it("ignores fenced headings and frontmatter", () => {
    const markdown = "---\nname: Ada\nheadline: x\n---\n# Ada\n\n```md\n## Fenced\n```\n\n~~~\n# Tilde\n~~~\n\n<!--\n## Hidden\n-->\n\n## Work\n";
    expect([...documentAnchors(markdown)]).toEqual(["ada", "work"]);
  });

  it("matches the preview's rendered anchors, with and without the title", () => {
    const markdown = [
      "---", "schema: connect.md/profile", "name: Ada", "---", "",
      "# Ada", "", "## Experience", "", "Text.", "",
      "## Café *Bold* `code` [link](https://example.invalid)", "",
      "Setext heading", "--------------", "",
      "## Experience ##", "", "  ### Indented heading", "",
      "## snake_case _emph_", "", "- a list item", "---", "",
      "| a | b |", "|---|---|", "| 1 | 2 |", "",
      "# Ada", "",
    ].join("\n");
    const rendered = (omitTitle: boolean) => [...renderToStaticMarkup(createElement(MarkdownPreview, { markdown, omitTitle })).matchAll(/data-md-anchor="([^"]*)"/gu)].map((match) => match[1]);
    const anchors = [...documentAnchors(markdown)];
    expect(anchors).toEqual(["ada", "experience", "café-bold-code-link", "setext-heading", "experience-1", "indented-heading", "snake_case-emph", "ada-1"]);
    expect(rendered(false)).toEqual(anchors);
    expect(rendered(true)).toEqual(anchors.slice(1));
  });

  it("marks headings focusable without ids and keeps external links unchanged", () => {
    const markup = renderToStaticMarkup(createElement(MarkdownPreview, { markdown: "# Ada\n\nSee [work](#work) and [site](https://example.invalid).\n\n## Work\n" }));
    expect(markup).toContain('<h2 data-markdown-heading-level="2" data-md-anchor="work" tabindex="-1">Work</h2>');
    expect(markup).not.toContain(" id=");
    expect(markup).toContain('<a href="#work" rel="ugc nofollow noreferrer">work</a>');
    expect(markup).toContain('<a href="https://example.invalid" rel="ugc nofollow noreferrer">site</a>');
  });
});

describe("in-document link review", () => {
  const codes = (markdown: string) => reviewWriting(markdown).findings.map(({ code, line }) => [code, line]);

  it("accepts fragment links to existing headings and flags missing ones", () => {
    const markdown = "# Ada\n\n[Jump](#work)\n[Missing](#nowhere)\n[Encoded](#caf%C3%A9)\n\n## Work\n\nText.\n\n## Café\n\nText.\n";
    expect(codes(markdown)).toEqual([["fragment-link", 4]]);
    expect(reviewWriting(markdown).findings[0].message).toBe("No heading in this document creates #nowhere. Check the heading text or the link.");
  });

  it("flags full and collapsed references to undefined labels, never plain shortcut text", () => {
    const markdown = "# Ada\n\n[Site][home] and [Home][] and [Other][missing] and [missing][] and [plain text].\n\n[home]: https://example.invalid\n";
    expect(codes(markdown)).toEqual([["undefined-reference", 3], ["undefined-reference", 3]]);
  });

  it("matches labels case-insensitively with collapsed whitespace", () => {
    expect(codes("# Ada\n\n[Site][My  Home]\n\n[my home]: https://example.invalid\n")).toEqual([]);
  });

  it("checks definition destinations on the definition line", () => {
    const markdown = "# Ada\n\n[a][] [b][] [c][] [d][] [e][] [f][]\n\n[a]: <>\n[b]: javascript:alert(1)\n[c]: ./resume.pdf\n[d]: #missing\n[e]: <https://example.invalid> \"Title\"\n[f]: #ada\n";
    expect(codes(markdown)).toEqual([["empty-link", 5], ["link-scheme", 6], ["relative-link", 7], ["fragment-link", 8]]);
    expect(reviewWriting(markdown).findings[0].message).toBe("This link reference definition has no destination. Supply an address or remove the link markup.");
  });

  it("counts shortcut references as uses and flags unused definitions", () => {
    expect(codes("# Ada\n\nSee [home].\n\n[home]: https://example.invalid\n[spare]: https://example.invalid/spare\n")).toEqual([["unused-reference", 6]]);
  });

  it("ignores links and references inside fences and inline code", () => {
    const markdown = "# Ada\n\nUse `[x](javascript:alert)` and `[y][nope]` literally.\n\n```md\n[z](./file.md)\n[q][undefined]\n[unused]: https://example.invalid\n```\n";
    expect(codes(markdown)).toEqual([]);
  });

  it("keeps the existing inline link checks unchanged", () => {
    const markdown = "# Name\n[Empty]()\n[File](./resume.pdf)\n[Action](javascript:alert)\n";
    expect(reviewWriting(markdown).findings.map((finding) => finding.message)).toEqual([
      "This inline link has no destination. Supply an address or remove the link markup.",
      "This inline link is relative or local. It may stop working when this Markdown file is shared on its own.",
      "This inline link uses an unsupported destination scheme. Prefer an HTTPS address that readers can open.",
    ]);
  });
});
