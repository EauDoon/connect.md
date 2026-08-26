"use client";

import { AlertTriangle, Eye, LoaderCircle, Scale, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { MarkdownPreview } from "@/components/markdown-preview";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { ApiRequestError } from "@/lib/api";
import { beginLogicalMutationAttempt, claimLogicalMutation, settleLogicalMutationAttempt, type LogicalMutationAttempt, type LogicalMutationClaimSlot } from "@/lib/logical-mutation";
import { decideModerationReviewAppeal, getModerationReviewAppeal, listModerationReviewAppeals, presentModerationReviewError, type ModerationAppealAction, type ModerationAppealDetail, type ModerationAppealQueuePage, type ModerationAppealSummary } from "@/lib/moderation-review-api";

type LoadState = "loading" | "loaded" | "error" | "denied";
type DecisionState = { action: ModerationAppealAction; subjectExplanation: string };
export const APPEAL_REVIEW_DENIED_MESSAGE = "Your signed-in human session does not have appeal-review access.";

export function ModerationAppealReviewQueue() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef(subject); subjectRef.current = subject;
  const isSubjectCurrent = useCallback(() => subjectRef.current === subject, [subject]);
  if (!configured || !isLoaded || !isSignedIn || !subject) return <ReviewGate configured={configured} loading={!isLoaded} />;
  return <AuthenticatedModerationAppealReviewQueue key={subject} subject={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} />;
}

function AuthenticatedModerationAppealReviewQueue({ subject, getToken, isSubjectCurrent }: { subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean }) {
  const [appeals, setAppeals] = useState<ModerationAppealSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [queueError, setQueueError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ModerationAppealDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [decision, setDecision] = useState<DecisionState | null>(null);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const mountedRef = useRef(false);
  const appealsRef = useRef(appeals); appealsRef.current = appeals;
  const selectedIdRef = useRef(selectedId); selectedIdRef.current = selectedId;
  const focusDetailIdRef = useRef<string | null>(null);
  const detailRegionRef = useRef<HTMLElement>(null);
  const queueEpochRef = useRef(0);
  const detailEpochRef = useRef(0);
  const deliveredCursorsRef = useRef(new Set<string>());
  const attemptsRef = useRef(new Map<string, LogicalMutationAttempt>());
  const mutationClaimRef = useRef<LogicalMutationClaimSlot>({ current: null });

  useEffect(() => { const attempts = attemptsRef.current; mountedRef.current = true; return () => { mountedRef.current = false; queueEpochRef.current += 1; detailEpochRef.current += 1; attempts.clear(); }; }, []);
  const stillCurrent = useCallback(() => mountedRef.current && isSubjectCurrent(), [isSubjectCurrent]);
  const denyReviewAccess = useCallback(() => {
    invalidateModerationAppealReviewAccess(selectedIdRef, detailEpochRef, mutationClaimRef.current, attemptsRef.current);
    queueEpochRef.current += 1;
    focusDetailIdRef.current = null;
    appealsRef.current = []; setAppeals([]); setNextCursor(null); setSelectedId(null); setDetail(null); setDetailError(""); setDetailLoading(false); setDecision(null); setDecisionBusy(false); setNotice(""); setLoadState("denied"); setQueueError(APPEAL_REVIEW_DENIED_MESSAGE);
  }, []);

  const loadQueue = useCallback(async (cursor: string | null, append: boolean) => {
    const cursorKey = cursor ?? "__first_page__";
    if (!stillCurrent()) return;
    if (append && deliveredCursorsRef.current.has(cursorKey)) { setNextCursor(null); setQueueError("The appeal queue returned a cursor that did not advance. Loaded appeals remain visible."); return; }
    const epoch = ++queueEpochRef.current;
    setLoadState("loading"); setQueueError("");
    try {
      const page = await listModerationReviewAppeals(getToken, isSubjectCurrent, cursor);
      if (!stillCurrent() || epoch !== queueEpochRef.current) return;
      const delivered = append ? new Set(deliveredCursorsRef.current) : new Set<string>();
      delivered.add(cursorKey); deliveredCursorsRef.current = delivered;
      const merged = mergeModerationAppealQueue(append ? appealsRef.current : [], page, cursor, delivered);
      appealsRef.current = merged.items; setAppeals(merged.items); setNextCursor(merged.nextCursor); setLoadState("loaded");
      if (merged.cursorDidNotProgress) setQueueError("The appeal queue returned a cursor that did not advance. Loaded appeals remain visible.");
    } catch (error) {
      if (!stillCurrent()) return;
      if (isAppealReviewDenied(error)) { denyReviewAccess(); return; }
      if (epoch !== queueEpochRef.current) return;
      else { setLoadState("error"); setQueueError(presentModerationReviewError(error)); }
    }
  }, [denyReviewAccess, getToken, isSubjectCurrent, stillCurrent]);

  const loadDetail = useCallback(async (appealId: string) => {
    const epoch = ++detailEpochRef.current;
    setDetailLoading(true); setDetailError(""); setDecision(null);
    try {
      const current = await getModerationReviewAppeal(appealId, getToken, isSubjectCurrent);
      if (!stillCurrent() || epoch !== detailEpochRef.current || selectedIdRef.current !== appealId) return;
      setDetail(current); setDecision({ action: "uphold", subjectExplanation: "" });
    } catch (error) {
      if (!stillCurrent()) return;
      if (isAppealReviewDenied(error)) { denyReviewAccess(); return; }
      if (epoch !== detailEpochRef.current) return;
      setDetail(null); setDetailError(presentModerationReviewError(error));
    } finally {
      if (stillCurrent() && epoch === detailEpochRef.current) setDetailLoading(false);
    }
  }, [denyReviewAccess, getToken, isSubjectCurrent, stillCurrent]);

  useEffect(() => { void loadQueue(null, false); }, [loadQueue]);
  useEffect(() => {
    const detailId = detail?.appeal.id ?? null;
    if (!detailId || !decision || selectedId !== detailId || focusDetailIdRef.current !== detailId) return;
    focusDetailIdRef.current = null;
    detailRegionRef.current?.focus();
  }, [decision, detail, selectedId]);

  const openDetail = (record: ModerationAppealSummary) => {
    if (!stillCurrent()) return;
    mutationClaimRef.current.current = null; setDecisionBusy(false);
    focusDetailIdRef.current = record.id; selectedIdRef.current = record.id; setSelectedId(record.id); setDetail(null); setNotice(""); void loadDetail(record.id);
  };

  const decide = async () => {
    if (!detail || !decision || decisionBusy || !stillCurrent()) return;
    const explanation = decision.subjectExplanation.trim();
    if (!explanation) { setNotice("Add a plain-language explanation for the post author before deciding."); return; }
    if (!window.confirm(`${decision.action === "overturn" ? "Overturn" : "Uphold"} this moderation decision as the independent signed-in human reviewer?`)) return;
    const claim = claimLogicalMutation(mutationClaimRef.current); if (!claim) return;
    const requestSubject = subject;
    const requestAppealId = detail.appeal.id;
    const slot = moderationAppealAttemptSlot(requestAppealId);
    const intent = { operation: "moderation-appeal-decision", appealId: detail.appeal.id, etag: detail.etag, action: decision.action, subjectExplanation: explanation };
    const isDecisionCurrent = () => isModerationAppealDecisionCurrent(stillCurrent(), selectedIdRef.current, requestAppealId, claim.isCurrent());
    let attempt: LogicalMutationAttempt | null = null;
    try {
      attempt = beginLogicalMutationAttempt(attemptsRef.current.get(slot) ?? null, requestSubject, intent);
      rememberModerationAppealAttempt(attemptsRef.current, slot, attempt); setDecisionBusy(true); setNotice("");
      await decideModerationReviewAppeal(requestAppealId, { action: decision.action, subjectExplanation: explanation }, detail.etag, attempt.idempotencyKey, getToken, isSubjectCurrent);
      clearModerationAppealAttemptIfCurrent(attemptsRef.current, slot, attempt);
      if (!isDecisionCurrent()) return;
      setNotice("The server recorded the independent appeal decision. Reloading current authority and evidence.");
      await Promise.allSettled([loadQueue(null, false), loadDetail(requestAppealId)]);
    } catch (error) {
      if (attempt === null) { if (isDecisionCurrent()) setNotice(presentModerationReviewError(error)); return; }
      const disposition = appealDecisionDisposition(error);
      const retained = disposition === "uncertain" ? settleLogicalMutationAttempt(attempt, error) : null;
      if (attemptsRef.current.get(slot) === attempt) rememberModerationAppealAttempt(attemptsRef.current, slot, retained);
      if (disposition === "denied") { denyReviewAccess(); return; }
      if (!isDecisionCurrent()) return;
      setNotice(disposition === "stale" ? "The appeal evidence changed before this decision. Reloading it now; review and confirm again." : disposition === "conflict" ? "The appeal is no longer awaiting review. Reloading current authority." : retained ? "The decision may have been recorded. Retry the unchanged decision to recover the same result." : presentModerationReviewError(error));
      if (disposition === "stale" || disposition === "conflict") await Promise.allSettled([loadQueue(null, false), loadDetail(requestAppealId)]);
    } finally {
      const shouldResetBusy = isDecisionCurrent(); claim.release(); if (shouldResetBusy) setDecisionBusy(false);
    }
  };

  return <ReviewShell title="Independent appeal review" description="A separate, private review surface for appealed post decisions. Access and reviewer independence are enforced by the server, never inferred from this page.">
    {notice && <p role="status" className="rounded-2xl border border-white/10 bg-white/[.04] p-4 text-sm leading-6 text-mist">{notice}</p>}
    {queueError && <p role="alert" className="rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-4 text-sm leading-6 text-amber-100">{queueError}</p>}
    {loadState === "loading" && appeals.length === 0 && <Loading label="Loading private moderation appeals…" />}
    {loadState === "denied" ? <Denied /> : <div className="grid gap-6 lg:grid-cols-[minmax(18rem,0.78fr)_minmax(0,1.4fr)]">
      <section aria-labelledby="moderation-appeal-queue-heading" className="rounded-[1.6rem] border border-white/10 bg-panel/80 p-5 backdrop-blur-xl"><h2 id="moderation-appeal-queue-heading" className="text-xl font-semibold text-white">Submitted appeal queue</h2><p className="mt-2 text-sm leading-6 text-mist">Each appeal is reviewed against a fresh, integrity-checked evidence response.</p>{loadState === "loaded" && appeals.length === 0 && !queueError && <p className="mt-6 rounded-xl border border-dashed border-white/15 p-5 text-sm text-mist">No reviewable appeals were returned.</p>}<ol className="mt-5 space-y-3">{appeals.map((record) => <li key={record.id}><button type="button" aria-controls="moderation-appeal-detail" aria-pressed={selectedId === record.id} onClick={() => openDetail(record)} className={`w-full rounded-xl border p-4 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-acid ${selectedId === record.id ? "border-acid/50 bg-acid/[.08]" : "border-white/10 bg-black/15 hover:border-white/25"}`}><span className="block text-xs font-semibold uppercase tracking-[.14em] text-acid">{record.status.replaceAll("_", " ")}</span><span className="mt-2 block break-words font-semibold text-white">{record.title}</span><span className="mt-1 block text-sm text-mist">@{record.authorProfileHandle} · {formatTime(record.submittedAt)}</span></button></li>)}</ol>{loadState === "error" && <Button variant="secondary" className="mt-5" onClick={() => void loadQueue(null, false)}>Retry appeal queue</Button>}{nextCursor && <Button variant="secondary" className="mt-5" disabled={loadState === "loading"} onClick={() => void loadQueue(nextCursor, true)}>{loadState === "loading" && <LoaderCircle className="size-4 animate-spin" aria-hidden />}Load older appeals</Button>}</section>
      <section id="moderation-appeal-detail" ref={detailRegionRef} tabIndex={-1} aria-labelledby={detail && decision ? "moderation-appeal-detail-heading" : undefined} className="scroll-mt-24 focus:outline focus:outline-2 focus:outline-offset-4 focus:outline-acid">{detailLoading && <Loading label="Loading authoritative appeal evidence…" />}{detailError && <div><p role="alert" className="rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-4 text-sm leading-6 text-amber-100">{detailError}</p>{selectedId && !detailLoading && <Button variant="secondary" className="mt-4" onClick={() => void loadDetail(selectedId)}>Retry appeal evidence</Button>}</div>}{!selectedId && !detailLoading && <EmptyDetail label="Select an appeal to review its rationale, original decision, post, and reports." />}{detail && decision && <AppealDetail detail={detail} decision={decision} busy={decisionBusy} setDecision={setDecision} decide={decide} />}</section>
    </div>}
  </ReviewShell>;
}

function AppealDetail({ detail, decision, busy, setDecision, decide }: { detail: ModerationAppealDetail; decision: DecisionState; busy: boolean; setDecision: (value: DecisionState) => void; decide: () => Promise<void> }) {
  return <article className="overflow-hidden rounded-[1.6rem] border border-white/10 bg-panel/80 backdrop-blur-xl"><header className="border-b border-white/10 bg-gradient-to-br from-acid/[.09] via-transparent to-cyan-300/[.04] p-6"><p className="eyebrow">Independent appeal authority</p><h2 id="moderation-appeal-detail-heading" className="mt-3 break-words text-2xl font-semibold text-white">{detail.post.title}</h2><p className="mt-2 text-sm text-mist">@{detail.post.authorProfileHandle} · submitted {formatTime(detail.appeal.submittedAt)}</p></header><div className="space-y-7 p-6"><section className="rounded-xl border border-cyan-200/15 bg-cyan-200/[.04] p-4"><h3 className="font-semibold text-white">Author appeal rationale</h3><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-mist">{detail.appeal.rationale}</p></section><section className="rounded-xl border border-white/10 p-4"><h3 className="font-semibold text-white">Original subject-facing decision</h3><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2"><Fact label="Action" value={detail.decision.action} /><Fact label="Reason" value={detail.decision.reasonCode.replaceAll("_", " ")} /><Fact label="Decided" value={formatTime(detail.decision.decidedAt)} /><Fact label="Explanation" value={detail.decision.subjectExplanation} /></dl></section><section><h3 className="font-semibold text-white">Canonical post Markdown</h3><div className="mt-3 rounded-xl border border-white/10 bg-black/20 p-4"><MarkdownPreview markdown={detail.post.markdown} omitTitle headingOffset={2} /></div></section><section><div className="flex items-center gap-2"><AlertTriangle className="size-4 text-amber-200" aria-hidden /><h3 className="font-semibold text-white">Untrusted reporter evidence</h3></div><p className="mt-2 text-sm leading-6 text-mist">Narratives are untrusted plain text. They are never interpreted as Markdown or HTML.</p><ol className="mt-3 space-y-3">{detail.reports.map((report) => <li key={report.id} className="rounded-xl border border-amber-200/15 bg-amber-200/[.04] p-4"><p className="text-xs font-semibold uppercase tracking-[.12em] text-amber-100">{report.reasonCode.replaceAll("_", " ")} · {formatTime(report.createdAt)}</p><p data-untrusted-evidence className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-mist">{report.narrative ?? "No narrative supplied."}</p></li>)}</ol></section><section className="rounded-xl border border-acid/20 bg-acid/[.045] p-5"><div className="flex items-center gap-2"><Scale className="size-4 text-acid" aria-hidden /><h3 className="font-semibold text-white">Confirmed independent decision</h3></div><label className="mt-4 block text-sm font-semibold text-white">Action<select className={selectClass} value={decision.action} disabled={busy} onChange={(event) => setDecision({ ...decision, action: event.target.value as ModerationAppealAction })}><option value="uphold">Uphold withholding</option><option value="overturn">Overturn withholding</option></select></label><label className="mt-4 block text-sm font-semibold text-white">Explanation for the post author<Textarea className="mt-2" value={decision.subjectExplanation} minLength={1} maxLength={500} disabled={busy} onChange={(event) => setDecision({ ...decision, subjectExplanation: event.target.value })} /></label><Button variant={decision.action === "uphold" ? "danger" : "secondary"} className="mt-4" disabled={busy || !decision.subjectExplanation.trim()} onClick={() => void decide()}>{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}Review and confirm appeal decision</Button><p className="mt-3 text-xs leading-5 text-mist/75">The request is bound to this appeal detail&apos;s exact strong ETag. A changed record requires a new review.</p></section></div></article>;
}

function ReviewShell({ title, description, descriptionLoading = false, children }: { title: string; description: string; descriptionLoading?: boolean; children: ReactNode }) { return <main className="min-h-screen bg-[radial-gradient(circle_at_75%_15%,rgba(205,255,114,.09),transparent_28%),radial-gradient(circle_at_20%_65%,rgba(103,232,249,.05),transparent_32%)]"><section className="mx-auto max-w-7xl px-5 py-10 lg:px-8 lg:py-14"><div className="max-w-4xl"><p className="eyebrow">Private independent reviewer workspace</p><h1 className="mt-3 font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">{title}</h1><AsyncBoundaryMessage className="mt-4 max-w-3xl text-base leading-7 text-mist" loading={descriptionLoading}>{description}</AsyncBoundaryMessage></div><div className="mt-8 space-y-6">{children}</div></section></main>; }
function ReviewGate({ configured, loading }: { configured: boolean; loading: boolean }) { return <ReviewShell title="Independent appeal review" description={loading ? "Loading the signed-in human session…" : configured ? "Sign in to request access. Appeal-review authority is always decided by the server." : "Clerk authentication is not configured for this private workspace."} descriptionLoading={loading}><EmptyDetail label="No private appeal evidence has been requested." /></ReviewShell>; }
function Denied() { return <section className="rounded-2xl border border-dashed border-white/15 p-8 text-center"><ShieldCheck className="mx-auto size-7 text-acid" aria-hidden /><h2 className="mt-3 text-lg font-semibold text-white">Appeal review unavailable</h2><p className="mt-2 text-sm leading-6 text-mist">{APPEAL_REVIEW_DENIED_MESSAGE}</p></section>; }
function Loading({ label }: { label: string }) { return <p role="status" className="rounded-xl border border-white/10 bg-panel p-4 text-sm text-mist"><LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />{label}</p>; }
function EmptyDetail({ label }: { label: string }) { return <section className="rounded-2xl border border-dashed border-white/15 p-8 text-center"><Eye className="mx-auto size-6 text-acid" aria-hidden /><p className="mt-3 text-sm leading-6 text-mist">{label}</p></section>; }
function Fact({ label, value }: { label: string; value: string }) { return <div><dt className="text-mist/70">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-white">{value}</dd></div>; }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date); }
const selectClass = "mt-2 w-full rounded-xl border border-white/12 bg-black/25 px-3.5 py-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:opacity-60";

export function mergeModerationAppealQueue(existing: ModerationAppealSummary[], page: ModerationAppealQueuePage, currentCursor: string | null, deliveredCursors: ReadonlySet<string>) {
  const byId = new Map(existing.map((item) => [item.id, item])); for (const item of page.appeals) if (!byId.has(item.id)) byId.set(item.id, item);
  const cursorDidNotProgress = page.nextCursor !== null && (page.nextCursor === currentCursor || deliveredCursors.has(page.nextCursor));
  return { items: [...byId.values()], nextCursor: cursorDidNotProgress ? null : page.nextCursor, cursorDidNotProgress };
}
export function isAppealReviewDenied(error: unknown) { return error instanceof ApiRequestError && (error.status === 401 || error.status === 403); }
export function appealDecisionDisposition(error: unknown): "denied" | "stale" | "conflict" | "rejected" | "uncertain" {
  if (isAppealReviewDenied(error)) return "denied"; if (error instanceof ApiRequestError && error.status === 412) return "stale"; if (error instanceof ApiRequestError && error.status === 409) return "conflict"; if (error instanceof ApiRequestError && error.status !== undefined && error.status >= 400 && error.status < 500) return "rejected"; return "uncertain";
}
export function isModerationAppealDecisionCurrent(viewCurrent: boolean, selectedId: string | null, requestId: string, claimCurrent: boolean) { return viewCurrent && selectedId === requestId && claimCurrent; }
export function moderationAppealAttemptSlot(appealId: string) { return `appeal:${appealId}`; }
export function rememberModerationAppealAttempt(map: Map<string, LogicalMutationAttempt>, slot: string, attempt: LogicalMutationAttempt | null) { rememberBoundedAttempt(map, slot, attempt); }
export function clearModerationAppealAttemptIfCurrent(map: Map<string, LogicalMutationAttempt>, slot: string, attempt: LogicalMutationAttempt) { if (map.get(slot) === attempt) map.delete(slot); }
export function invalidateModerationAppealReviewAccess(selectedIdRef: { current: string | null }, detailEpochRef: { current: number }, claimSlot: LogicalMutationClaimSlot, attempts: Map<string, LogicalMutationAttempt>) { selectedIdRef.current = null; detailEpochRef.current += 1; claimSlot.current = null; attempts.clear(); }
function rememberBoundedAttempt(map: Map<string, LogicalMutationAttempt>, slot: string, attempt: LogicalMutationAttempt | null) { if (attempt === null) { map.delete(slot); return; } if (!map.has(slot) && map.size >= 64) { const oldest = map.keys().next().value; if (oldest !== undefined) map.delete(oldest); } map.set(slot, attempt); }
