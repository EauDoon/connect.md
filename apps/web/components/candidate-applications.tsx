"use client";

import { Eye, LoaderCircle, Send, Undo2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { Button } from "@/components/ui/button";
import { appendCursorPage, getMyApplicationDetail, listMyApplications, presentRecruitmentError, withdrawApplication, type Application } from "@/lib/recruitment-api";

export type CandidateApplicationReadRequest =
  | { kind: "initial"; requestId: number; epoch: number; cursor: null }
  | { kind: "more"; requestId: number; epoch: number; cursor: string };

export type CandidateApplicationReadState = {
  epoch: number;
  nextRequestId: number;
  initialRequestId: number | null;
  moreRequestId: number | null;
};

export function createCandidateApplicationReadState(): CandidateApplicationReadState {
  return { epoch: 0, nextRequestId: 0, initialRequestId: null, moreRequestId: null };
}

export function beginCandidateApplicationRead(
  state: CandidateApplicationReadState,
  cursor: string | null,
): CandidateApplicationReadRequest | null {
  if (cursor === null) {
    if (state.initialRequestId !== null) return null;
    state.nextRequestId += 1;
    state.epoch += 1;
    state.initialRequestId = state.nextRequestId;
    return { kind: "initial", requestId: state.nextRequestId, epoch: state.epoch, cursor };
  }
  if (state.initialRequestId !== null || state.moreRequestId !== null) return null;
  state.nextRequestId += 1;
  state.moreRequestId = state.nextRequestId;
  return { kind: "more", requestId: state.nextRequestId, epoch: state.epoch, cursor };
}

export function candidateApplicationReadIsCurrent(
  state: CandidateApplicationReadState,
  request: CandidateApplicationReadRequest,
) {
  return state.epoch === request.epoch
    && (request.kind === "initial" ? state.initialRequestId : state.moreRequestId) === request.requestId;
}

export function finishCandidateApplicationRead(
  state: CandidateApplicationReadState,
  request: CandidateApplicationReadRequest,
) {
  if (request.kind === "initial" && state.initialRequestId === request.requestId) state.initialRequestId = null;
  if (request.kind === "more" && state.moreRequestId === request.requestId) state.moreRequestId = null;
}

export function CandidateApplications() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  const isSubjectCurrent = useCallback((requestSubject: string) => subjectRef.current === requestSubject, []);
  if (!configured || !isLoaded || !isSignedIn || !subject) return <AuthState configured={configured} isLoaded={isLoaded} />;
  return <AuthenticatedCandidateApplications key={subject} subject={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} />;
}

function AuthenticatedCandidateApplications({ subject, getToken, isSubjectCurrent }: { subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: (requestSubject: string) => boolean }) {
  const [applications, setApplications] = useState<Application[]>([]); const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "loaded" | "error">("loading"); const [loadError, setLoadError] = useState(""); const [busy, setBusy] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null); const [messages, setMessages] = useState<Record<string, string>>({});
  const applicationsRef = useRef(applications); applicationsRef.current = applications;
  const mutationAttemptsRef = useRef(new Map<string, LogicalMutationAttempt>());
  const deliveredCursorsRef = useRef(new Set<string>());
  const readStateRef = useRef(createCandidateApplicationReadState());
  const moreBusyRequestRef = useRef<number | null>(null);
  const load = useCallback(async (cursor: string | null = null) => {
    const requestSubject = subject;
    if (!isSubjectCurrent(requestSubject)) return;
    const request = beginCandidateApplicationRead(readStateRef.current, cursor);
    if (!request) return;
    const requestIsCurrent = () => isSubjectCurrent(requestSubject)
      && candidateApplicationReadIsCurrent(readStateRef.current, request);
    if (request.kind === "more") {
      if (deliveredCursorsRef.current.has(request.cursor)) {
        finishCandidateApplicationRead(readStateRef.current, request);
        setNextCursor(null);
        setNotice("The application history returned a cursor that did not advance. Loaded records remain available.");
        return;
      }
      moreBusyRequestRef.current = request.requestId;
      setBusy("applications-more");
    } else { setLoadState("loading"); setLoadError(""); }
    if (request.kind === "more") setNotice(null);
    try {
      const result = await listMyApplications(getToken, requestIsCurrent, cursor);
      if (!requestIsCurrent()) return;
      if (request.kind === "initial") {
        applicationsRef.current = result.items;
        setApplications(result.items);
        setNextCursor(result.nextCursor);
        deliveredCursorsRef.current = new Set();
        setLoadState("loaded");
        return;
      }
      const delivered = new Set(deliveredCursorsRef.current);
      delivered.add(request.cursor);
      deliveredCursorsRef.current = delivered;
      const next = appendCursorPage(applicationsRef.current, result, request.cursor, delivered);
      applicationsRef.current = next.items;
      setApplications(next.items);
      setNextCursor(next.nextCursor);
      if (next.cursorDidNotProgress) setNotice("The application history returned a cursor that did not advance. Loaded records remain available.");
    } catch (error) {
      if (requestIsCurrent()) { if (request.kind === "more") setNotice(presentRecruitmentError(error)); else { setLoadState("error"); setLoadError(presentRecruitmentError(error)); } }
    } finally {
      const ownsMoreBusy = request.kind === "more" && moreBusyRequestRef.current === request.requestId;
      finishCandidateApplicationRead(readStateRef.current, request);
      if (ownsMoreBusy) {
        moreBusyRequestRef.current = null;
        if (isSubjectCurrent(requestSubject)) setBusy(null);
      }
    }
  }, [getToken, isSubjectCurrent, subject]);
  useEffect(() => { void load(); }, [load]);
  const showMessage = async (application: Application) => { const requestSubject = subject; moreBusyRequestRef.current = null; setBusy(application.id); try { const detail = await getMyApplicationDetail(application.id, getToken, () => isSubjectCurrent(requestSubject)); if (isSubjectCurrent(requestSubject)) setMessages((current) => ({ ...current, [application.id]: detail.message })); } catch (error) { if (isSubjectCurrent(requestSubject)) setNotice(presentRecruitmentError(error)); } finally { if (isSubjectCurrent(requestSubject)) setBusy(null); } };
  const withdraw = async (application: Application) => { if (!window.confirm("Withdraw this application? This action is recorded by the API.")) return; const requestSubject = subject; const slot = `application-withdraw:${application.id}`; const attempt = beginLogicalMutationAttempt(mutationAttemptsRef.current.get(slot) ?? null, requestSubject, { operation: "withdraw-application", applicationId: application.id }); mutationAttemptsRef.current.set(slot, attempt); moreBusyRequestRef.current = null; setBusy(application.id); try { const updated = await withdrawApplication(application.id, getToken, () => isSubjectCurrent(requestSubject), attempt.idempotencyKey); mutationAttemptsRef.current.delete(slot); if (!isSubjectCurrent(requestSubject)) return; setApplications((current) => current.map((item) => item.id === updated.id ? updated : item)); setNotice("Application withdrawn."); } catch (error) { const next = settleLogicalMutationAttempt(attempt, error); if (next) mutationAttemptsRef.current.set(slot, next); else mutationAttemptsRef.current.delete(slot); if (isSubjectCurrent(requestSubject)) setNotice(next ? "Application withdrawal may have succeeded but its acknowledgement was not received. Retry the unchanged action to recover the same result." : presentRecruitmentError(error)); } finally { if (isSubjectCurrent(requestSubject)) setBusy(null); } };
  const loading = loadState === "loading";
  return <main className="mx-auto max-w-5xl px-5 py-10 pb-16 lg:px-8"><p className="eyebrow">Candidate workspace</p><h1 className="mt-4 font-display text-5xl font-semibold tracking-[-.06em] text-white sm:text-6xl">Your applications.</h1><p className="mt-4 max-w-2xl text-lg leading-8 text-mist">Only you can view your submitted message or withdraw an eligible application. The list deliberately excludes message content until you request an individual record.</p>{notice && <p role="status" className="mt-6 rounded-xl border border-white/10 bg-panel p-4 text-sm text-mist">{notice}</p>}<div className="mt-7 flex items-center justify-between gap-4"><h2 className="text-lg font-semibold text-white">Submitted applications</h2><Button variant="secondary" disabled={loading || busy !== null} onClick={() => void load()}>{loading && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Refresh</Button></div>{loading && applications.length === 0 ? <p role="status" className="mt-6 text-sm text-mist">Loading your private application records…</p> : loadState === "error" && applications.length === 0 ? <PrivateLoadFailure label="Applications could not be loaded" error={loadError} onRetry={() => void load()} /> : loadState === "loaded" && applications.length === 0 ? <Empty /> : <ol className="mt-5 space-y-4">{applications.map((application) => <li key={application.id}><article className="rounded-2xl border border-white/10 bg-panel p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-white">{application.organizationSlug} / {application.jobSlug}</h3><p className="mt-1 text-xs text-mist">{application.snapshotKind} snapshot: {application.snapshotIdentifier} · v{application.snapshotVersion}</p></div><span className="rounded-full bg-white/[.07] px-3 py-1 text-xs font-semibold text-mist">{application.status.replaceAll("_", " ")}</span></div><p className="mt-3 text-xs text-mist/75">Retention through {formatDate(application.retentionExpiresAt)} · policy {application.retentionPolicyVersion}</p>{messages[application.id] && <p className="mt-4 whitespace-pre-wrap rounded-xl bg-black/20 p-3 text-sm leading-6 text-mist">{messages[application.id]}</p>}<div className="mt-4 flex flex-wrap gap-2"><Button variant="ghost" disabled={busy !== null} onClick={() => void showMessage(application)}>{busy === application.id ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Eye className="size-4" aria-hidden />} View my submitted message</Button>{(application.status === "submitted" || application.status === "under_review") && <Button variant="danger" disabled={busy !== null} onClick={() => void withdraw(application)}><Undo2 className="size-4" aria-hidden /> Withdraw</Button>}</div></article></li>)}</ol>}{loadState === "error" && applications.length > 0 && <PrivateLoadFailure label="Applications could not be refreshed" error={loadError} onRetry={() => void load()} />}{nextCursor && <Button variant="secondary" className="mt-6" disabled={loading || busy !== null} onClick={() => void load(nextCursor)}>{busy === "applications-more" && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Load older applications</Button>}</main>;
}
function AuthState({ configured, isLoaded }: { configured: boolean; isLoaded: boolean }) { return <main className="mx-auto max-w-5xl px-5 py-16 lg:px-8"><h1 className="font-display text-4xl font-semibold text-white">Your applications</h1><p role="status" className="mt-4 max-w-xl text-mist">{!configured ? "This deployment has no signed-in application workspace configured." : !isLoaded ? "Checking your session…" : "Sign in as the human applicant to view your private application records."}</p></main>; }
function Empty() { return <div className="mt-5 rounded-2xl border border-dashed border-white/15 bg-panel p-8 text-center"><Send className="mx-auto size-6 text-acid" aria-hidden /><h2 className="mt-3 font-semibold text-white">No applications yet</h2><p className="mt-2 text-sm text-mist">Published roles are available in the jobs directory.</p></div>; }
function PrivateLoadFailure({ label, error, onRetry }: { label: string; error: string; onRetry: () => void }) { return <div role="alert" className="mt-5 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4"><p className="font-semibold text-amber-50">{label}</p><p className="mt-1 text-sm leading-6 text-amber-100/85">{error}</p><Button variant="secondary" className="mt-3" onClick={onRetry}>Retry</Button></div>; }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString(); }
