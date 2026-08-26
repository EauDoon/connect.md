import { Braces, CalendarClock, FileText } from "lucide-react";
import Link from "next/link";

import { MarkdownPreview } from "@/components/markdown-preview";
import { PostReportControl } from "@/components/post-report-control";
import { publicApiMarkdownUrl } from "@/lib/api";
import type { ProfessionalPost } from "@/lib/posts-api";

export function ProfessionalPostCard({ post, reportable = true }: { post: ProfessionalPost; reportable?: boolean }) {
  const markdownHref = publicApiMarkdownUrl(post.markdownUrl);
  return <article className="rounded-[1.4rem] border border-white/10 bg-panel p-5 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><p className="eyebrow">Professional post</p><h2 className="mt-2 text-2xl font-semibold tracking-[-.03em] text-white"><Link href={`/posts/${encodeURIComponent(post.id)}`} className="inline-flex min-h-11 max-w-full items-center break-anywhere hover:text-acid hover:underline">{post.title}</Link></h2><p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-mist"><Link href={`/p/${encodeURIComponent(post.authorProfileHandle)}`} className="inline-flex min-h-11 max-w-full items-center break-anywhere font-semibold text-acid underline-offset-4 hover:underline">@{post.authorProfileHandle}</Link><span className="inline-flex items-center gap-1.5"><CalendarClock className="size-3.5 text-acid" aria-hidden /> <time dateTime={post.publishedAt}>{formatDate(post.publishedAt)}</time></span></p></div><Link href={`/posts/${encodeURIComponent(post.id)}`} className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 text-xs font-semibold text-white hover:border-acid/40"><FileText className="size-4 text-acid" aria-hidden /> Open post</Link></div>{post.topics.length > 0 && <ul className="mt-5 flex flex-wrap gap-2" aria-label="Post topics">{post.topics.map((topic) => <li key={topic} className="rounded-full border border-acid/20 bg-acid/[.07] px-3 py-1 text-xs text-acid">{topic}</li>)}</ul>}<div className="mt-6"><MarkdownPreview markdown={post.markdown} omitTitle headingOffset={1} /></div>{markdownHref && <div className="mt-5 flex flex-wrap items-center gap-4 border-t border-white/10 pt-4"><a href={markdownHref} type="text/markdown" className="inline-flex min-h-11 items-center gap-2 text-xs font-semibold text-mist underline-offset-4 hover:text-white hover:underline"><Braces className="size-4 text-acid" aria-hidden /> Canonical Markdown</a></div>}{reportable && <PostReportControl postId={post.id} />}</article>;
}

function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date); }
