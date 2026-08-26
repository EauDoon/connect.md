"use client";

import { AlertTriangle, Clock3, FileWarning, LoaderCircle, Scale, Send, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { createModerationAppeal, isAppealableModerationCase, listModerationCasesForSubject, presentModerationError, type ModerationAppeal, type ModerationCase, type ModerationCasePage } from "@/lib/moderation-api";

type CaseRequest = { cursor: string | null; append: boolean };

export function ModerationCaseManager() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject);
  subjectRef.current = subject;
  const isSubjectCurrent = useCallback(() => subjectRef.current === subject, [subject]);

  if (!configured || !isLoaded || !isSignedIn || !subject) return <ModerationGate configured={configured} loading={!isLoaded} />;
  return <AuthenticatedModerationCaseManager key={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} subject={subject} />;
}

function AuthenticatedModerationCaseManager({ getToken, isSubjectCurrent, subject }: { getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean; subject: string }) {
  const [cases, setCases] = useState<ModerationCase[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState<CaseRequest | null>(null);
  const inFlightRef = useRef<string | null>(null);
  const deliveredCursorsRef = useRef(new Set<string>());
  const mountedRef = useRef(false);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);
  const stillCurrent = useCallback(() => mountedRef.current && isSubjectCurrent(), [isSubjectCurrent]);

  const load = useCallback(async (nextCursor: string | null, append: boolean) => {
    const requestKey = nextCursor ?? "__first_page__";
    if (!stillCurrent() || inFlightRef.current !== null) return;
    if (append && deliveredCursorsRef.current.has(requestKey)) {
      setCursor(null); setRetry(null); setError("The case list returned a cursor that did not advance. Loaded cases remain visible.");
      return;
    }
    inFlightRef.current = requestKey;
    setLoading(true); setError(""); setRetry(null);
    try {
      const page: ModerationCasePage = await listModerationCasesForSubject(getToken, isSubjectCurrent, nextCursor);
      if (!stillCurrent()) return;
      const delivered = append ? new Set(deliveredCursorsRef.current) : new Set<string>();
      delivered.add(requestKey);
      const nonProgress = page.nextCursor !== null && delivered.has(page.nextCursor);
      deliveredCursorsRef.current = delivered;
      setCases((current) => append ? mergeCasesById(current, page.cases) : mergeCasesById([], page.cases));
      setCursor(nonProgress ? null : page.nextCursor);
      if (nonProgress) setError("The case list returned a cursor that did not advance. Loaded cases remain visible.");
    } catch (cause) {
      if (stillCurrent()) { setError(presentModerationError(cause)); setRetry({ cursor: nextCursor, append }); }
    } finally {
      if (inFlightRef.current === requestKey) inFlightRef.current = null;
      if (stillCurrent()) setLoading(false);
    }
  }, [getToken, isSubjectCurrent, stillCurrent]);
  useEffect(() => { void load(null, false); }, [load]);
  const onAppealed = useCallback((caseId: string, appeal: ModerationAppeal) => {
    setCases((current) => current.map((caseRecord) => caseRecord.id === caseId ? { ...caseRecord, status: "appealed", appeal } : caseRecord));
  }, []);

  const retryInitial = retry && !retry.append;
  const nextCursor = retry?.append ? retry.cursor : cursor;
  return <main className="mx-auto max-w-5xl px-5 py-10 pb-16 lg:px-8 lg:py-14"><section className="max-w-3xl"><p className="eyebrow">Private human case status</p><h1 className="mt-3 font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">Post review and appeal status.</h1><p className="mt-4 text-base leading-7 text-mist">This private view shows only case status and the explanation addressed to you. It is not a moderation console, and it does not expose report-source information or internal review material.</p></section>{loading && cases.length === 0 && <p role="status" className="mt-8 text-sm text-mist"><LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />Loading your private case status…</p>}{error && <p role="alert" className="mt-8 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4 text-sm leading-6 text-amber-100/90">{error}</p>}{!loading && cases.length === 0 && !error && <section className="mt-8 rounded-2xl border border-dashed border-white/15 p-7 text-center"><ShieldCheck className="mx-auto size-6 text-acid" aria-hidden /><h2 className="mt-3 text-lg font-semibold text-white">No private post cases</h2><p className="mt-2 text-sm leading-6 text-mist">There are no moderation cases for posts owned by this signed-in account.</p></section>}{cases.length > 0 && <ol className="mt-8 space-y-5">{cases.map((caseRecord) => <li key={caseRecord.id}><ModerationCaseCard key={`${subject}:${caseRecord.id}`} caseRecord={caseRecord} subject={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} onAppealed={onAppealed} /></li>)}</ol>}{retryInitial && <Button variant="secondary" className="mt-7" disabled={loading} onClick={() => void load(null, false)}>{loading && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Retry case status</Button>}{nextCursor && <Button variant="secondary" className="mt-7" disabled={loading} onClick={() => void load(nextCursor, true)}>{loading && <LoaderCircle className="size-4 animate-spin" aria-hidden />}{retry?.append ? "Retry older cases" : "Load older cases"}</Button>}<p className="mt-8 text-xs leading-5 text-mist/75">Private case status is available only to the signed-in human who owns the post. Appeals are one bounded request per current adverse decision, not an external notification.</p></main>;
}

function ModerationCaseCard({ caseRecord, subject, getToken, isSubjectCurrent, onAppealed }: { caseRecord: ModerationCase; subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean; onAppealed: (caseId: string, appeal: ModerationAppeal) => void }) {
  const status = moderationStatus(caseRecord.status);
  return <article className="rounded-[1.4rem] border border-white/10 bg-panel p-5 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="eyebrow">Post case</p><h2 className="mt-2 flex items-center gap-2 text-xl font-semibold text-white"><FileWarning className="size-5 text-acid" aria-hidden />{status.label}</h2><p className="mt-2 text-sm leading-6 text-mist">{status.description}</p></div><span className={`rounded-full border px-3 py-1 text-xs font-semibold ${status.tone}`}>{status.label}</span></div><dl className="mt-6 grid gap-4 border-t border-white/10 pt-5 text-sm sm:grid-cols-2"><Fact label="Post ID" value={caseRecord.postId} mono /><Fact label="Case status" value={status.label} /><Fact label="Reason code" value={caseRecord.reasonCode ?? "Not issued"} /><Fact label="Last updated" value={formatDate(caseRecord.updatedAt)} /><Fact label="Decision time" value={caseRecord.decidedAt ? formatDate(caseRecord.decidedAt) : "No decision issued"} /><Fact label="Appeal deadline" value={caseRecord.appealDeadline ? formatDate(caseRecord.appealDeadline) : "Not appealable"} /></dl>{caseRecord.subjectExplanation && <section className="mt-5 rounded-xl border border-acid/20 bg-acid/[.05] p-4"><h3 className="text-sm font-semibold text-white">Explanation for you</h3><p className="mt-2 text-sm leading-6 text-mist">{caseRecord.subjectExplanation}</p></section>}{caseRecord.appeal && <section className="mt-5 rounded-xl border border-white/10 bg-black/15 p-4"><h3 className="inline-flex items-center gap-2 text-sm font-semibold text-white"><Scale className="size-4 text-acid" aria-hidden />{appealStatus(caseRecord.appeal.status)}</h3><dl className="mt-3 grid gap-3 text-xs sm:grid-cols-2"><Fact label="Submitted" value={formatDate(caseRecord.appeal.submittedAt)} /><Fact label="Reviewed" value={caseRecord.appeal.reviewedAt ? formatDate(caseRecord.appeal.reviewedAt) : "Awaiting independent review"} /></dl>{caseRecord.appeal.subjectExplanation && <p className="mt-3 text-sm leading-6 text-mist">{caseRecord.appeal.subjectExplanation}</p>}</section>}{isAppealableModerationCase(caseRecord) && <AppealForm key={caseRecord.id} caseId={caseRecord.id} subject={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} onAppealed={onAppealed} />}</article>;
}

function AppealForm({ caseId, subject, getToken, isSubjectCurrent, onAppealed }: { caseId: string; subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean; onAppealed: (caseId: string, appeal: ModerationAppeal) => void }) {
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const attemptRef = useRef<LogicalMutationAttempt | null>(null);
  const submit = async () => {
    const trimmed = rationale.trim();
    if (!trimmed || busy || !isSubjectCurrent()) return;
    setBusy(true); setNotice("");
    try {
      const requestSubject = subject;
      attemptRef.current = beginLogicalMutationAttempt(attemptRef.current, requestSubject, { operation: "moderation-appeal", caseId, rationale: trimmed });
      const attempt = attemptRef.current;
      const appeal = await createModerationAppeal(caseId, trimmed, attempt.idempotencyKey, getToken, isSubjectCurrent);
      if (!isSubjectCurrent()) return;
      attemptRef.current = null; setRationale(""); onAppealed(caseId, appeal); setNotice("Your appeal is pending independent review.");
    } catch (error) {
      attemptRef.current = settleLogicalMutationAttempt(attemptRef.current!, error); if (isSubjectCurrent()) setNotice(attemptRef.current ? "The appeal may have been recorded. Retry the unchanged rationale to recover the same result. " + presentModerationError(error) : presentModerationError(error));
    } finally {
      if (isSubjectCurrent()) setBusy(false);
    }
  };
  return <section aria-labelledby={`appeal-${caseId}`} className="mt-5 rounded-xl border border-acid/25 bg-acid/[.05] p-4"><h3 id={`appeal-${caseId}`} className="inline-flex items-center gap-2 text-sm font-semibold text-white"><AlertTriangle className="size-4 text-acid" aria-hidden />Appeal this current adverse decision</h3><p className="mt-2 text-sm leading-6 text-mist">Submit one concise rationale before the displayed deadline. The same unchanged rationale reuses its request key if confirmation is interrupted.</p>{notice && <p role="status" className="mt-3 text-sm leading-6 text-mist">{notice}</p>}<label className="mt-4 block text-sm font-semibold text-white">Appeal rationale<Textarea value={rationale} minLength={1} maxLength={2000} disabled={busy} onChange={(event) => setRationale(event.target.value)} className="mt-1.5 min-h-28" placeholder="Explain why this decision should be independently reviewed." /></label><Button className="mt-4" disabled={busy || !rationale.trim()} onClick={() => void submit()}>{busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Send className="size-4" aria-hidden />}Submit appeal</Button></section>;
}

function ModerationGate({ configured, loading }: { configured: boolean; loading: boolean }) { return <main className="mx-auto max-w-4xl px-5 py-10 lg:px-8 lg:py-14"><section className="rounded-[1.5rem] border border-white/10 bg-panel p-6"><Clock3 className="size-6 text-acid" aria-hidden /><h1 className="mt-4 text-2xl font-semibold text-white">Private post case status</h1><AsyncBoundaryMessage className="mt-3 text-sm leading-6 text-mist" loading={loading}>{loading ? "Checking your signed-in human session…" : configured ? "Sign in as the human post owner to view private case status and eligible appeals." : "Human authentication is not configured for this deployment."}</AsyncBoundaryMessage>{configured && !loading && <Link href="/feed" className="mt-4 inline-flex min-h-11 items-center text-sm font-semibold text-acid underline-offset-4 hover:underline">Open private feed</Link>}</section></main>; }

export function mergeCasesById(existing: ModerationCase[], incoming: ModerationCase[]) { const known = new Set(existing.map((caseRecord) => caseRecord.id)); return [...existing, ...incoming.filter((caseRecord) => { if (known.has(caseRecord.id)) return false; known.add(caseRecord.id); return true; })]; }
function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div><dt className="text-mist/70">{label}</dt><dd className={`mt-1 break-all text-white ${mono ? "font-mono" : ""}`}>{value}</dd></div>; }
function moderationStatus(status: ModerationCase["status"]) { switch (status) { case "open": return { label: "Open review", description: "This case is under review. No decision has been issued.", tone: "border-white/15 text-mist" }; case "dismissed": return { label: "No action", description: "The review closed without withholding the post.", tone: "border-white/15 text-mist" }; case "withheld": return { label: "Post withheld", description: "An adverse decision withheld this post. It may be appealed before the deadline if no appeal exists.", tone: "border-amber-300/35 text-amber-100" }; case "appealed": return { label: "Appeal pending", description: "An appeal is awaiting independent review.", tone: "border-acid/35 text-acid" }; case "appeal_upheld": return { label: "Appeal upheld", description: "The independent appeal review upheld the adverse decision.", tone: "border-amber-300/35 text-amber-100" }; case "appeal_overturned": return { label: "Appeal overturned", description: "The independent appeal review overturned the adverse decision.", tone: "border-acid/35 text-acid" }; case "legacy_withheld": return { label: "Legacy withheld — not appealable", description: "This is a historical withheld disposition, not a current appealable decision.", tone: "border-white/15 text-mist" }; case "legacy_withdrawn": return { label: "Legacy withdrawn — not appealable", description: "This is a historical withdrawn disposition, not a current appealable decision.", tone: "border-white/15 text-mist" }; } }
function appealStatus(status: ModerationAppeal["status"]) { return status === "submitted" ? "Appeal pending" : status === "upheld" ? "Appeal upheld" : "Appeal overturned"; }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date); }
