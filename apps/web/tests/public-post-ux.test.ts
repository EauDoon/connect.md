import { createElement } from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarkdownPreview } from "../components/markdown-preview";

function source(relativePath: string) {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

describe("public post touch and overflow UX", () => {
  it("keeps page-level post links usable and canonical", () => {
    const page = source("../components/public-post-page.tsx");
    const card = source("../components/professional-post-card.tsx");

    expect(page).toContain('className="inline-flex min-h-11 items-center gap-2 rounded-lg');
    expect(page).toContain('href={markdownHref} type="text/markdown" className="inline-flex min-h-11 items-center');
    expect(page).toContain('className="inline-flex min-h-11 items-center gap-2 font-semibold text-acid');
    expect(page).toContain('className="mt-5 max-w-4xl break-anywhere font-display');
    expect(card).toContain('className="inline-flex min-h-11 max-w-full items-center break-anywhere hover:text-acid');
    expect(card).toContain('className="inline-flex min-h-11 max-w-full items-center break-anywhere font-semibold');
    expect(card).toContain('href={markdownHref} type="text/markdown" className="inline-flex min-h-11 items-center');
  });

  it("contains long Markdown without enlarging inline links into touch controls", () => {
    const markdownSource = source("../components/markdown-preview.tsx");
    const longToken = "x".repeat(500);
    const markup = renderToStaticMarkup(createElement(MarkdownPreview, {
      markdown: `# Title\n\n${longToken}\n\n[Inline link](https://example.test/${longToken})`,
    }));

    expect(markdownSource).toContain("markdown-prose break-anywhere");
    expect(markdownSource).toContain('rel="ugc nofollow noreferrer"');
    expect(markdownSource).not.toContain('a href={href} rel="ugc nofollow noreferrer" className="min-h-11');
    expect(markup).toContain('class="markdown-prose break-anywhere ');
    expect(markup).toContain(longToken);
    expect(markup).toContain('rel="ugc nofollow noreferrer"');
    expect(markup).not.toContain("min-h-11");
  });
});
