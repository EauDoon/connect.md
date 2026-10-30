"use client";

import React from "react";
import ReactMarkdown, { type Options } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { headingText, rehypeHeadingAnchors } from "@/lib/heading-anchors";
import { markdownBody, scanMarkdownHeadings } from "@/lib/markdown";

type MarkdownHeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;
type MarkdownHeadingOffset = 0 | 1 | 2 | 3 | 4 | 5;
type HeadingNode = { properties?: Record<string, unknown> } | undefined;

function shiftedHeading(level: MarkdownHeadingLevel, offset: MarkdownHeadingOffset, children: React.ReactNode, node: HeadingNode) {
  const tagName = `h${Math.min(6, level + offset)}` as const;
  const anchor = node?.properties?.dataMdAnchor;
  return React.createElement(tagName, { "data-markdown-heading-level": level, ...(typeof anchor === "string" ? { "data-md-anchor": anchor, tabIndex: -1 } : {}) }, children);
}

/**
 * In-document links move focus to the matching heading inside the same
 * preview. The address bar and history are never written, so document text
 * never reaches a URL.
 */
function followFragment(event: React.MouseEvent<HTMLAnchorElement>, href: string) {
  event.preventDefault();
  let slug = href.slice(1);
  try { slug = decodeURIComponent(slug); } catch { /* keep the raw fragment */ }
  const target = Array.from(event.currentTarget.closest("article")?.querySelectorAll<HTMLElement>("[data-md-anchor]") ?? [])
    .find((heading) => heading.dataset.mdAnchor === slug);
  if (!target) return;
  target.scrollIntoView({ block: "start" });
  target.focus({ preventScroll: true });
}

export function MarkdownPreview({ markdown, className = "", omitTitle = false, headingOffset = 0 }: { markdown: string; className?: string; omitTitle?: boolean; headingOffset?: MarkdownHeadingOffset }) {
  const body = markdownBody(markdown);
  const title = omitTitle ? scanMarkdownHeadings(body).find((heading) => heading.level === 1) : undefined;
  const content = title ? `${body.slice(0, title.start)}${body.slice(title.end).replace(/^\n/u, "")}` : body;
  const rehypePlugins: NonNullable<Options["rehypePlugins"]> = [rehypeSanitize, [rehypeHeadingAnchors, { seed: title ? [headingText(title.text.replace(/(?:^|[ \t]+)#+$/u, "").trim())] : [] }]];
  return (
    <article className={`markdown-prose break-anywhere ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={rehypePlugins}
        skipHtml
        components={{
          h1: ({ children, node }) => shiftedHeading(1, headingOffset, children, node),
          h2: ({ children, node }) => shiftedHeading(2, headingOffset, children, node),
          h3: ({ children, node }) => shiftedHeading(3, headingOffset, children, node),
          h4: ({ children, node }) => shiftedHeading(4, headingOffset, children, node),
          h5: ({ children, node }) => shiftedHeading(5, headingOffset, children, node),
          h6: ({ children, node }) => shiftedHeading(6, headingOffset, children, node),
          a: ({ children, href }) => href?.startsWith("#")
            ? <a href={href} rel="ugc nofollow noreferrer" onClick={(event) => followFragment(event, href)}>{children}</a>
            : <a href={href} rel="ugc nofollow noreferrer">{children}</a>,
          img: ({ alt }) => <span className="text-sm italic text-slate-500">{alt ? `[Remote image blocked: ${alt}]` : "[Remote image blocked]"}</span>
        }}
      >
        {content}
      </ReactMarkdown>
    </article>
  );
}
