"use client";

import { Flag, LoaderCircle } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { POST_REPORT_REASONS, presentPostsError, reportPost, type PostReportReason } from "@/lib/posts-api";

export function PostReportControl({ postId }: { postId: string }) {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject);
  subjectRef.current = subject;
  const isSubjectCurrent = useCallback(() => subjectRef.current === subject, [subject]);
  if (!configured || !isLoaded || !isSignedIn || !subject) return null;
  return <AuthenticatedPostReportControl key={`${subject}:${postId}`} postId={postId} subject={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} />;
}

function AuthenticatedPostReportControl({ postId, subject, getToken, isSubjectCurrent }: { postId: string; subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<PostReportReason | "">("");
  const [narrative, setNarrative] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const reportRef = useRef<LogicalMutationAttempt | null>(null);

  async function submit() {
    if (busy || !reason || !isSubjectCurrent()) return;
    setBusy(true); setNotice("");
    try {
      const requestSubject = subject;
      reportRef.current = beginLogicalMutationAttempt(reportRef.current, requestSubject, { operation: "report-post", postId, reason, narrative: narrative.trim() || null });
      const attempt = reportRef.current;
      await reportPost(postId, { reason, narrative }, attempt.idempotencyKey, getToken, isSubjectCurrent);
      if (!isSubjectCurrent()) return;
      reportRef.current = null; setReason(""); setNarrative(""); setOpen(false); setNotice("Private report recorded. Reports do not automatically sanction a post.");
    } catch (error) {
      reportRef.current = settleLogicalMutationAttempt(reportRef.current!, error); if (isSubjectCurrent()) setNotice(reportRef.current ? "The report may have been recorded. Retry the unchanged report to recover the same result. " + presentPostsError(error) : presentPostsError(error));
    } finally {
      if (isSubjectCurrent()) setBusy(false);
    }
  }

  return <section aria-label="Report this post" className="mt-5 border-t border-white/10 pt-4"><Button variant="ghost" className="min-h-11 px-2 text-xs" onClick={() => setOpen((current) => !current)}><Flag className="size-4" aria-hidden /> Report post</Button>{notice && <p role="status" className="mt-3 text-xs leading-5 text-mist">{notice}</p>}{open && <form className="mt-3 rounded-xl border border-white/10 bg-black/15 p-3" onSubmit={(event) => { event.preventDefault(); void submit(); }}><label className="block text-xs font-semibold text-white">Reason<select value={reason} disabled={busy} onChange={(event) => setReason(event.target.value as PostReportReason | "")} className={selectClass}><option value="">Choose a reason</option>{POST_REPORT_REASONS.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label><label className="mt-3 block text-xs font-semibold text-white">Narrative <span className="font-normal text-mist">(optional, private)</span><Textarea value={narrative} maxLength={2000} disabled={busy} onChange={(event) => setNarrative(event.target.value)} className="mt-1.5 min-h-24" placeholder="Give the moderation team useful context." /></label><div className="mt-3 flex gap-2"><Button type="submit" className="min-h-11 px-3 text-xs" disabled={busy || !reason}>{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}Submit private report</Button><Button type="button" variant="ghost" className="min-h-11 px-3 text-xs" disabled={busy} onClick={() => { setOpen(false); setReason(""); setNarrative(""); }}>Cancel</Button></div></form>}</section>;
}

const selectClass = "mt-1.5 min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:opacity-50";
