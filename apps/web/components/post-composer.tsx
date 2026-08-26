"use client";

import { Eye, FileText, LoaderCircle, Send } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { MarkdownPreview } from "@/components/markdown-preview";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { createPostMarkdown, POST_MAX_BYTES, POST_MAX_CLIENT_MARKDOWN_BYTES, validatePostDraft } from "@/lib/post-markdown";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { presentPostsError, publishPost, type ProfessionalPost } from "@/lib/posts-api";

export function PostComposer({ onPublished }: { onPublished: (post: ProfessionalPost) => void }) {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  if (!configured) return <ComposerGate body="Human authentication is not configured for this deployment." />;
  if (!isLoaded) return <ComposerGate body="Checking your signed-in session…" loading />;
  if (!isSignedIn || !subject) return <ComposerGate body="Sign in as a human to publish an immutable professional post. Agents and API keys cannot publish posts." />;
  return <AuthenticatedPostComposer key={subject} subject={subject} getToken={getToken} isSubjectCurrent={() => subjectRef.current === subject} onPublished={onPublished} />;
}

function AuthenticatedPostComposer({ subject, getToken, isSubjectCurrent, onPublished }: { subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean; onPublished: (post: ProfessionalPost) => void }) {
  const [title, setTitle] = useState(""); const [topicsText, setTopicsText] = useState(""); const [body, setBody] = useState(""); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState(""); const publicationRef = useRef<LogicalMutationAttempt | null>(null);
  const draft = useMemo(() => validatePostDraft({ title, topicsText, body }), [title, topicsText, body]);

  async function publish() {
    if (busy || draft.issues.length > 0 || !draft.title || !isSubjectCurrent()) return;
    setBusy(true); setNotice("");
    try {
      const requestSubject = subject;
      publicationRef.current = beginLogicalMutationAttempt(publicationRef.current, requestSubject, { operation: "publish-post", markdown: draft.markdown });
      const attempt = publicationRef.current;
      const post = await publishPost(draft.markdown, attempt.idempotencyKey, getToken, isSubjectCurrent);
      if (!isSubjectCurrent()) return;
      publicationRef.current = null; setTitle(""); setTopicsText(""); setBody(""); onPublished(post);
      setNotice("Published an immutable public professional post.");
    } catch (error) { publicationRef.current = settleLogicalMutationAttempt(publicationRef.current!, error); if (isSubjectCurrent()) setNotice(publicationRef.current ? "Publication may have succeeded but its acknowledgement was not received. Retry the unchanged draft to recover the same idempotent result, or edit it to begin a new publication. " + presentPostsError(error) : presentPostsError(error)); }
    finally { if (isSubjectCurrent()) setBusy(false); }
  }

  return <section aria-labelledby="post-composer-title" className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"><div className="flex gap-3"><FileText className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden /><div><p className="eyebrow">Human-only publishing</p><h2 id="post-composer-title" className="mt-2 text-2xl font-semibold text-white">Publish a canonical professional post.</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-mist">The API assigns the author profile, ID, version, and timestamps. Posts are public and immutable—there are no edits, reactions, reposts, or comments. The platform provides no media upload.</p></div></div>{notice && <p role="status" className="mt-5 rounded-xl border border-white/10 bg-black/20 p-3 text-sm leading-6 text-mist">{notice}</p>}<div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,.85fr)]"><form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void publish(); }}><label className="block text-sm font-semibold text-white">Title<Input value={title} maxLength={160} disabled={busy} onChange={(event) => setTitle(event.target.value)} placeholder="A clear professional point of view" /></label><label className="block text-sm font-semibold text-white">Topics <span className="font-normal text-mist">(comma-separated, at least one)</span><Input value={topicsText} maxLength={509} disabled={busy} onChange={(event) => setTopicsText(event.target.value)} placeholder="payments, product-strategy" /></label><label className="block text-sm font-semibold text-white">Post body<Textarea value={body} maxLength={POST_MAX_CLIENT_MARKDOWN_BYTES} disabled={busy} onChange={(event) => setBody(event.target.value)} placeholder="Share the evidence, context, and conclusion." /></label><div className="rounded-xl border border-white/10 bg-black/15 p-3 text-xs leading-5 text-mist"><p><span className={draft.bytes > POST_MAX_CLIENT_MARKDOWN_BYTES ? "font-semibold text-amber-100" : "font-semibold text-white"}>{draft.bytes.toLocaleString()} / {POST_MAX_CLIENT_MARKDOWN_BYTES.toLocaleString()} client bytes</span> · 512 bytes are reserved for server-owned canonical fields inside the {POST_MAX_BYTES.toLocaleString()} byte limit.</p>{draft.issues.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 text-amber-100/90">{draft.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}</div><Button type="submit" disabled={busy || !draft.title || draft.issues.length > 0}>{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}<Send className="size-4" aria-hidden /> Publish immutable post</Button></form><aside aria-label="Safe post preview"><div className="flex items-center gap-2 text-sm font-semibold text-white"><Eye className="size-4 text-acid" aria-hidden /> Safe preview</div><p className="mt-2 text-xs leading-5 text-mist">Markdown is sanitized. Remote images are blocked in the preview, and the platform has no media upload.</p><div className="mt-3 max-h-[28rem] overflow-auto rounded-2xl border border-white/10 bg-[#f6f7f3] p-5 text-slate-950"><MarkdownPreview markdown={createPostMarkdown({ title, topicsText, body })} className="light-preview" headingOffset={2} /></div><details className="mt-4 rounded-xl border border-white/10 bg-black/15 p-3"><summary className="flex min-h-11 cursor-pointer items-center text-sm font-semibold text-white">Client-write Markdown</summary><pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words text-xs leading-5 text-mist"><code>{draft.markdown}</code></pre></details></aside></div></section>;
}

function ComposerGate({ body, loading = false }: { body: string; loading?: boolean }) { return <section className="rounded-[1.5rem] border border-white/10 bg-panel p-5"><h2 className="text-xl font-semibold text-white">Professional publishing is human-only</h2><AsyncBoundaryMessage className="mt-2 text-sm leading-6 text-mist" loading={loading}>{loading && <LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />}{body}</AsyncBoundaryMessage></section>; }
