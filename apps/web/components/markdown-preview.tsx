"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { markdownBody, scanMarkdownHeadings } from "@/lib/markdown";

type MarkdownHeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;
type MarkdownHeadingOffset = 0 | 1 | 2 | 3 | 4 | 5;

function shiftedHeading(level: MarkdownHeadingLevel, offset: MarkdownHeadingOffset, children: React.ReactNode) {
  const tagName = `h${Math.min(6, level + offset)}` as const;
  return React.createElement(tagName, { "data-markdown-heading-level": level }, children);
}

export function MarkdownPreview({ markdown, className = "", omitTitle = false, headingOffset = 0 }: { markdown: string; className?: string; omitTitle?: boolean; headingOffset?: MarkdownHeadingOffset }) {
  const body = markdownBody(markdown);
  const title = omitTitle ? scanMarkdownHeadings(body).find((heading) => heading.level === 1) : undefined;
  const content = title ? `${body.slice(0, title.start)}${body.slice(title.end).replace(/^\n/u, "")}` : body;
  return (
    <article className={`markdown-prose break-anywhere ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        skipHtml
        components={{
          h1: ({ children }) => shiftedHeading(1, headingOffset, children),
          h2: ({ children }) => shiftedHeading(2, headingOffset, children),
          h3: ({ children }) => shiftedHeading(3, headingOffset, children),
          h4: ({ children }) => shiftedHeading(4, headingOffset, children),
          h5: ({ children }) => shiftedHeading(5, headingOffset, children),
          h6: ({ children }) => shiftedHeading(6, headingOffset, children),
          a: ({ children, href }) => <a href={href} rel="ugc nofollow noreferrer">{children}</a>,
          img: ({ alt }) => <span className="text-sm italic text-slate-500">{alt ? `[Remote image blocked: ${alt}]` : "[Remote image blocked]"}</span>
        }}
      >
        {content}
      </ReactMarkdown>
    </article>
  );
}
