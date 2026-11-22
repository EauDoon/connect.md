import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MarkdownPreview } from "../components/markdown-preview";
import { documentMetrics, filterOutline, sectionExcerpt } from "../lib/document-metrics";
import { findSourceMatches } from "../lib/source-search";

const frontmatter = "---\nname: Example\n---\n\n";

it.each([
  { syntax: "canonical ATX", block: "## Work", level: 2 },
  { syntax: "three-space ATX indentation", block: "   ### Work", level: 3 },
  { syntax: "tab after ATX markers", block: "####\tWork", level: 4 },
  { syntax: "padded ATX closing sequence", block: "#####   Work ###  ", level: 5 },
  { syntax: "Setext level one", block: "Work\n===", level: 1 },
  { syntax: "multiline Setext level two", block: "Recent\nWork\n---", level: 2, text: "Recent\nWork" },
])("finds the preview's $syntax heading and its full source section", ({ block, level, text = "Work" }) => {
  const section = `${block}\n\nSection detail.\n\n`;
  const source = `${frontmatter}Introduction.\n\n${section}# Next\n`;
  const preview = renderToStaticMarkup(createElement(MarkdownPreview, { markdown: source }));
  const [match] = filterOutline(documentMetrics(source).headings, text.replace("\n", " ").toLowerCase());

  expect(preview).toContain(`data-markdown-heading-level="${level}" data-md-anchor="${text.toLowerCase().replace("\n", "")}"`);
  expect(match).toMatchObject({ text, level, start: source.indexOf(block) });
  expect(source.slice(match.start, match.end)).toBe(block);
  expect(sectionExcerpt(source, match.start).markdown).toBe(section);
});

it.each([
  { syntax: "shorter backtick run", opener: "````md", middle: "```", closer: "````" },
  { syntax: "opposite fence marker", opener: "~~~md", middle: "```", closer: "~~~~" },
  { syntax: "trailing words on a fence", opener: "```md", middle: "``` example", closer: "```" },
  { syntax: "four-space indented fence", opener: "~~~md", middle: "    ~~~", closer: "~~~" },
])("keeps code searchable without treating a $syntax as a closing fence", ({ opener, middle, closer }) => {
  const section = `## Work\n${opener}\n${middle}\n## Work example\n${closer}\n\n`;
  const source = `${frontmatter}${section}## Next\n`;
  const preview = renderToStaticMarkup(createElement(MarkdownPreview, { markdown: source }));
  const headings = documentMetrics(source).headings;

  expect(preview).toContain('data-md-anchor="next"');
  expect(preview).not.toContain('data-md-anchor="work-example"');
  expect(findSourceMatches(source, "Work example", { bodyOnly: true }).matches).toEqual([source.indexOf("Work example")]);
  expect(filterOutline(headings, "Work").map((heading) => heading.text)).toEqual(["Work"]);
  expect(filterOutline(headings, "Next")).toHaveLength(1);
  expect(sectionExcerpt(source, headings[0].start).markdown).toBe(section);
});
