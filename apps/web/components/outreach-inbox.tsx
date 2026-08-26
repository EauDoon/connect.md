"use client";

import { Ban, Bot, Check, Flag, Inbox, LoaderCircle, MessageSquareText, Send, ShieldCheck, UserRoundCheck, X } from "lucide-react";
import { SignInButton } from "@clerk/nextjs";
import { useCallback, useEffect, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { presentApiError } from "@/lib/api";
import { buildInboxContactReturnPath, isCanonicalProfileHandle } from "@/lib/auth-return-intent";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { appendCursorPage as appendPrivateCursorPage } from "@/lib/cursor-page";
import { actOnOutreach, getContactPolicyForSubject, listOutreachForSubject, sendContactRequest, updateContactPolicy } from "@/lib/outreach-api";
import type { ContactPolicy, ContactPolicyMode, OutreachStatus, OutreachThread } from "@/lib/product-types";
import {
  beginPrivateRead,
  createPrivateReadEpoch,
  finishPrivateRead,
  privateReadAllowsDependentWrite,
  privateReadIsCurrent,
  type PrivateReadEpoch,
} from "@/lib/private-read-epoch";

export {
  beginPrivateRead,
  createPrivateReadEpoch,
  finishPrivateRead,
  privateReadAllowsDependentWrite,
  privateReadIsCurrent,
  type PrivateReadEpoch,
};

const defaultPolicy: ContactPolicy = { mode: "request", allowAgentMessages: false, dailyRequestLimit: 5, representativeLabel: null, representativeUrl: null, etag: "" };

export function OutreachInbox({ prefillProfileHandle = null }: { prefillProfileHandle?: string | null }) {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const safePrefillProfileHandle = isCanonicalProfileHandle(prefillProfileHandle) ? prefillProfileHandle : null;
  const returnPath = safePrefillProfileHandle ? buildInboxContactReturnPath(safePrefillProfileHandle) : null;
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  if (!configured || !isLoaded || !isSignedIn || !subject) return <div className="rounded-3xl border border-white/10 bg-panel p-8 text-center"><Inbox className="mx-auto size-7 text-acid" aria-hidden /><h2 className="mt-4 text-xl font-semibold text-white">Sign in to open your private inbox</h2><AsyncBoundaryMessage className="mt-2 text-sm text-mist" loading={!isLoaded}>{!isLoaded ? "Loading your account." : configured ? safePrefillProfileHandle ? "After authentication, you can review a private contact request addressed to the linked public profile. Nothing will be sent automatically." : "Contact requests are visible only to the receiving account." : "Clerk configuration is required."}</AsyncBoundaryMessage>{returnPath && configured && isLoaded && <SignInButton mode="modal" forceRedirectUrl={returnPath} signUpForceRedirectUrl={returnPath}><button type="button" className="mx-auto mt-5 inline-flex min-h-11 items-center rounded-full bg-acid px-4 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Sign in to prepare request</button></SignInButton>}</div>;
  return <AuthenticatedInbox key={`${subject}:${safePrefillProfileHandle ?? "none"}`} subject={subject} getToken={getToken} isSubjectCurrent={() => subjectRef.current === subject} initialTarget={safePrefillProfileHandle} />;
}

function AuthenticatedInbox({ subject, getToken, isSubjectCurrent, initialTarget }: { subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean; initialTarget: string | null }) {
  const [policy, setPolicy] = useState<ContactPolicy>(defaultPolicy);
  const [threads, setThreads] = useState<OutreachThread[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [policyLoadState, setPolicyLoadState] = useState<"loading" | "loaded" | "error">("loading");
  const [inboxLoadState, setInboxLoadState] = useState<"loading" | "loaded" | "error">("loading");
  const [policyLoadError, setPolicyLoadError] = useState("");
  const [inboxLoadError, setInboxLoadError] = useState("");
  const [policyHasLoaded, setPolicyHasLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [moreLoading, setMoreLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [target, setTarget] = useState(initialTarget ?? "");
  const [purpose, setPurpose] = useState("");
  const [body, setBody] = useState("");
  const [reportingId, setReportingId] = useState<string | null>(null);
  const [reportReason, setReportReason] = useState("");
  const threadsRef = useRef(threads);
  const deliveredCursorsRef = useRef(new Set<string>());
  const moreInFlightRef = useRef(false);
  const policyReadEpochRef = useRef(createPrivateReadEpoch());
  const inboxReadEpochRef = useRef(createPrivateReadEpoch());
  const initialPolicyLoadStartedRef = useRef(false);
  const initialPolicyLoadInFlightRef = useRef(false);
  const initialInboxLoadStartedRef = useRef(false);
  const initialInboxLoadInFlightRef = useRef(false);
  const mutationAttemptsRef = useRef(new Map<string, LogicalMutationAttempt>());
  const beginAttempt = (slot: string, requestSubject: string, intent: unknown) => { const attempt = beginLogicalMutationAttempt(mutationAttemptsRef.current.get(slot) ?? null, requestSubject, intent); mutationAttemptsRef.current.set(slot, attempt); return attempt; };
  const settleAttempt = (slot: string, attempt: LogicalMutationAttempt, error: unknown) => { const next = settleLogicalMutationAttempt(attempt, error); if (next) mutationAttemptsRef.current.set(slot, next); else mutationAttemptsRef.current.delete(slot); return next; };
  const policyMutationInFlight = busy === "policy";
  const reportMutationInFlight = reportingId !== null && busy === reportingId;
  threadsRef.current = threads;

  const loadPolicy = useCallback(async (initial = false) => {
    if (!isSubjectCurrent()) return;
    if (initial) {
      if (initialPolicyLoadStartedRef.current || initialPolicyLoadInFlightRef.current) return;
      initialPolicyLoadStartedRef.current = true;
      initialPolicyLoadInFlightRef.current = true;
    }
    const requestEpoch = beginPrivateRead(policyReadEpochRef.current);
    setPolicyLoadState("loading"); setPolicyLoadError("");
    try {
      const next = await getContactPolicyForSubject(getToken, isSubjectCurrent);
      if (!isSubjectCurrent() || !privateReadIsCurrent(policyReadEpochRef.current, requestEpoch)) return;
      setPolicy(next); setPolicyHasLoaded(true); setPolicyLoadState("loaded");
    } catch (error) {
      if (isSubjectCurrent() && privateReadIsCurrent(policyReadEpochRef.current, requestEpoch)) { setPolicyLoadState("error"); setPolicyLoadError(presentApiError(error)); }
    } finally {
      finishPrivateRead(policyReadEpochRef.current, requestEpoch);
      if (initial) initialPolicyLoadInFlightRef.current = false;
    }
  }, [getToken, isSubjectCurrent]);
  const loadInbox = useCallback(async (initial = false) => {
    if (!isSubjectCurrent()) return;
    if (initial) {
      if (initialInboxLoadStartedRef.current || initialInboxLoadInFlightRef.current) return;
      initialInboxLoadStartedRef.current = true;
      initialInboxLoadInFlightRef.current = true;
    }
    const requestEpoch = beginPrivateRead(inboxReadEpochRef.current);
    setInboxLoadState("loading"); setInboxLoadError("");
    try {
      const page = await listOutreachForSubject(getToken, isSubjectCurrent);
      if (!isSubjectCurrent() || !privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)) return;
      setThreads(page.threads);
      setNextCursor(page.nextCursor);
      deliveredCursorsRef.current = new Set();
      setInboxLoadState("loaded");
    } catch (error) {
      if (isSubjectCurrent() && privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)) { setInboxLoadState("error"); setInboxLoadError(presentApiError(error)); }
    } finally {
      finishPrivateRead(inboxReadEpochRef.current, requestEpoch);
      if (initial) initialInboxLoadInFlightRef.current = false;
    }
  }, [getToken, isSubjectCurrent]);
  const refresh = useCallback(async (initial = false) => {
    setMessage("");
    await Promise.all([loadPolicy(initial), loadInbox(initial)]);
  }, [loadInbox, loadPolicy]);

  useEffect(() => { void refresh(true); }, [refresh]);

  async function loadOlder() {
    if (inboxLoadState !== "loaded" || !privateReadAllowsDependentWrite(inboxReadEpochRef.current) || busy || !nextCursor || moreInFlightRef.current) return;
    const cursor = nextCursor;
    const requestEpoch = inboxReadEpochRef.current.current;
    if (!isSubjectCurrent()) return;
    if (deliveredCursorsRef.current.has(cursor)) {
      setNextCursor(null);
      setMessage("The private outreach inbox returned a cursor that did not advance. Loaded requests remain available.");
      return;
    }
    moreInFlightRef.current = true;
    setMoreLoading(true);
    try {
      const page = await listOutreachForSubject(getToken, isSubjectCurrent, cursor);
      if (!isSubjectCurrent() || !privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)) return;
      const delivered = new Set(deliveredCursorsRef.current);
      delivered.add(cursor);
      deliveredCursorsRef.current = delivered;
      const next = appendPrivateCursorPage(
        threadsRef.current,
        { items: page.threads, nextCursor: page.nextCursor },
        cursor,
        delivered,
      );
      setThreads(next.items);
      setNextCursor(next.nextCursor);
      if (next.cursorDidNotProgress) {
        setMessage("The private outreach inbox returned a cursor that did not advance. Loaded requests remain available.");
        return;
      }
      setMessage("");
    } catch (error) {
      if (isSubjectCurrent() && privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)) setMessage(presentApiError(error));
    } finally {
      moreInFlightRef.current = false;
      setMoreLoading(false);
    }
  }

  async function savePolicy() {
    if (busy || policyLoadState !== "loaded" || !policyHasLoaded || !privateReadAllowsDependentWrite(policyReadEpochRef.current)) return;
    const requestSubject = subject;
    const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent();
    if (!requestIsCurrent()) return;
    setBusy("policy");
    try {
      const attempt = beginAttempt("policy", requestSubject, { operation: "update-contact-policy", mode: policy.mode, allowAgentMessages: policy.allowAgentMessages, dailyRequestLimit: policy.dailyRequestLimit, etag: policy.etag });
      const next = await updateContactPolicy(policy, getToken, requestIsCurrent, attempt.idempotencyKey); if (!requestIsCurrent()) return; mutationAttemptsRef.current.delete("policy"); setPolicy(next);
      setMessage("Contact policy saved.");
    } catch (error) {
      if (!requestIsCurrent()) return;
      const attempt = mutationAttemptsRef.current.get("policy"); if (attempt) settleAttempt("policy", attempt, error); setMessage(mutationAttemptsRef.current.has("policy") ? "The policy update may have completed. Retry the unchanged update to recover the same result. " + presentApiError(error) : presentApiError(error));
    } finally {
      if (requestIsCurrent()) setBusy(null);
    }
  }

  async function act(thread: OutreachThread, action: Exclude<OutreachStatus, "pending">, reason: string | null = null) {
    if (busy || inboxLoadState !== "loaded" || !privateReadAllowsDependentWrite(inboxReadEpochRef.current)) return;
    if (action === "blocked" && !window.confirm("Block this sender?")) return;
    const requestSubject = subject;
    const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent();
    if (!requestIsCurrent()) return; const requestEpoch = inboxReadEpochRef.current.current; setBusy(thread.id);
    try {
      const slot = `thread:${thread.id}`; const attempt = beginAttempt(slot, requestSubject, { operation: "act-on-outreach", threadId: thread.id, action, reason: reason?.trim() ?? null });
      await actOnOutreach(thread.id, action, reason, getToken, requestIsCurrent, attempt.idempotencyKey); if (!requestIsCurrent() || !privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)) return;
      mutationAttemptsRef.current.delete(slot);
      setThreads((current) => current.map((item) => item.id === thread.id ? { ...item, status: action } : item));
      if (action === "reported") { setReportingId(null); setReportReason(""); }
      setMessage(`Request ${action}.`);
    } catch (error) {
      if (!requestIsCurrent()) return;
      const slot = `thread:${thread.id}`; const attempt = mutationAttemptsRef.current.get(slot); if (attempt) settleAttempt(slot, attempt, error); if (privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)) setMessage(mutationAttemptsRef.current.has(slot) ? "The request action may have completed. Retry the unchanged action to recover the same result. " + presentApiError(error) : presentApiError(error));
    } finally {
      if (requestIsCurrent()) setBusy(null);
    }
  }

  async function send() {
    if (busy || !target.trim() || !purpose.trim() || !body.trim()) return;
    if (!isSubjectCurrent()) return; setBusy("send");
    try {
      const requestSubject = subject;
      const normalizedTarget = target.trim();
      if (!isCanonicalProfileHandle(normalizedTarget)) { setMessage("Enter a canonical public profile handle using lowercase letters, numbers, and hyphens."); return; }
      const normalizedPurpose = purpose.trim(); const normalizedBody = body.trim(); const attempt = beginAttempt("send", requestSubject, { operation: "send-contact-request", targetProfileHandle: normalizedTarget, purpose: normalizedPurpose, message: normalizedBody });
      await sendContactRequest(normalizedTarget, normalizedPurpose, normalizedBody, attempt.idempotencyKey, getToken, isSubjectCurrent); if (!isSubjectCurrent()) return;
      mutationAttemptsRef.current.delete("send");
      setTarget(""); setPurpose(""); setBody("");
      setMessage("Contact request sent through the internal inbox.");
    } catch (error) {
      const attempt = mutationAttemptsRef.current.get("send"); if (attempt) settleAttempt("send", attempt, error); setMessage(mutationAttemptsRef.current.has("send") ? "The contact request may have been sent. Retry the unchanged request to recover the same result. " + presentApiError(error) : presentApiError(error));
    } finally {
      setBusy(null);
    }
  }

  return <div className="grid gap-6 lg:grid-cols-[20rem_minmax(0,1fr)]">
    <aside className="space-y-6 lg:sticky lg:top-24 lg:self-start">
      <section aria-labelledby="contact-policy-title" className="rounded-[1.5rem] border border-acid/20 bg-acid/[.06] p-5"><h2 id="contact-policy-title" className="inline-flex items-center gap-2 font-semibold text-white"><ShieldCheck className="size-5 text-acid" aria-hidden /> Contact policy</h2><p className="mt-2 text-sm leading-6 text-mist">Choose whether agent-originated requests may enter this private gate.</p>{policyLoadState === "loading" && <p role="status" className="mt-4 inline-flex items-center gap-2 text-sm text-mist"><LoaderCircle className="size-4 animate-spin" aria-hidden /> {policyHasLoaded ? "Refreshing contact policy" : "Loading contact policy"}</p>}{policyLoadState === "error" && <PrivateLoadFailure label="Contact policy could not be loaded" error={policyLoadError} onRetry={() => void loadPolicy()} />}{policyHasLoaded && <><label className="mt-4 block text-sm font-medium text-white">Policy<select value={policy.mode === "closed" ? "closed" : "request"} disabled={policyMutationInFlight} onChange={(event) => { const mode = event.target.value as ContactPolicyMode; setPolicy((current) => ({ ...current, mode, allowAgentMessages: mode !== "closed" })); }} className={selectClass}><option value="request">Accept gated requests</option><option value="closed">Closed to agent requests</option></select></label><label className="mt-4 block text-sm text-white">Daily request limit<Input className="mt-1.5" type="number" min={1} max={20} value={policy.dailyRequestLimit} disabled={policyMutationInFlight || policy.mode === "closed"} onChange={(event) => setPolicy((current) => ({ ...current, dailyRequestLimit: Math.max(1, Math.min(20, Number(event.target.value) || 1)) }))} /></label><p className="mt-3 text-xs leading-5 text-mist">Public representative details remain part of the profile disclosure; this private API policy currently stores only allow/deny and a daily limit.</p><Button className="mt-5 w-full" disabled={busy !== null || policyLoadState !== "loaded"} onClick={() => void savePolicy()}>{busy === "policy" && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Save policy</Button></>}</section>

      <section aria-labelledby="new-outreach-title" className="rounded-[1.5rem] border border-white/10 bg-panel p-5"><h2 id="new-outreach-title" className="inline-flex items-center gap-2 font-semibold text-white"><Send className="size-5 text-acid" aria-hidden /> Send a request</h2><p className="mt-2 text-sm leading-6 text-mist">Internal requests respect the recipient’s contact policy. A linked public profile may be prefilled, but you still choose a purpose and explicitly send.</p><label className="mt-4 block text-sm text-white">Profile handle<Input className="mt-1.5" value={target} onChange={(event) => setTarget(event.target.value)} placeholder="profile-handle" autoCapitalize="none" spellCheck={false} /></label><label className="mt-4 block text-sm text-white">Purpose<Input className="mt-1.5" value={purpose} maxLength={160} onChange={(event) => setPurpose(event.target.value)} placeholder="Partnership discussion" /></label><label className="mt-4 block text-sm text-white">Message<Textarea className="mt-1.5" value={body} maxLength={2000} onChange={(event) => setBody(event.target.value)} placeholder="Why this is relevant and what you are asking for." /></label><Button variant="secondary" className="mt-4 w-full" disabled={busy !== null || !isCanonicalProfileHandle(target.trim()) || !purpose.trim() || !body.trim()} onClick={() => void send()}>{busy === "send" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Send className="size-4" aria-hidden />} Send request</Button></section>
    </aside>

    <section aria-labelledby="inbox-title" className="min-w-0 rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"><div className="flex items-start gap-3"><Inbox className="mt-0.5 size-5 text-acid" aria-hidden /><div><h2 id="inbox-title" className="text-lg font-semibold text-white">Contact-request inbox</h2><p className="mt-1 text-sm text-mist">Accept, reject, block, or report without exposing a private email address.</p></div></div>{message && <p role="status" className="mt-4 rounded-xl border border-white/10 bg-black/15 p-3 text-sm text-mist">{message}</p>}{inboxLoadState === "loading" && threads.length === 0 && <p role="status" className="mt-6 inline-flex items-center gap-2 text-sm text-mist"><LoaderCircle className="size-4 animate-spin" aria-hidden /> Loading private requests</p>}{inboxLoadState === "error" && threads.length === 0 && <PrivateLoadFailure label="Contact requests could not be loaded" error={inboxLoadError} onRetry={() => void loadInbox()} />}{inboxLoadState === "loaded" && threads.length === 0 && <div className="mt-6 rounded-2xl border border-dashed border-white/15 p-8 text-center"><MessageSquareText className="mx-auto size-6 text-acid" aria-hidden /><h3 className="mt-4 font-semibold text-white">Inbox clear</h3><p className="mt-2 text-sm text-mist">No contact requests were returned.</p></div>}<ol className="mt-6 space-y-4">{threads.map((thread) => <li key={thread.id} className="rounded-2xl border border-white/10 bg-black/15 p-5"><article><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-white">{thread.subject}</h3><span className="rounded-full bg-white/[.07] px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-mist">{thread.status}</span></div><p className="mt-2 inline-flex items-center gap-2 text-xs text-mist"><UserRoundCheck className="size-3.5 text-acid" aria-hidden />{thread.senderName}{thread.senderAgent ? <><Bot className="ml-2 size-3.5 text-acid" aria-hidden />via {thread.senderAgent}</> : null}</p></div><time className="text-xs text-mist/70" dateTime={thread.receivedAt}>{formatTime(thread.receivedAt)}</time></div><p className="mt-4 text-sm leading-6 text-mist">{thread.preview}</p><p className="mt-2 text-xs text-mist/70">To {thread.targetIdentifier}</p>{thread.status === "pending" && <><div className="mt-5 flex flex-wrap gap-2"><Action icon={Check} label="Accept" disabled={busy !== null || inboxLoadState !== "loaded"} onClick={() => void act(thread, "accepted")} /><Action icon={X} label="Reject" disabled={busy !== null || inboxLoadState !== "loaded"} onClick={() => void act(thread, "rejected")} /><Action icon={Ban} label="Block" danger disabled={busy !== null || inboxLoadState !== "loaded"} onClick={() => void act(thread, "blocked")} /><Action icon={Flag} label="Report" danger disabled={busy !== null || inboxLoadState !== "loaded"} onClick={() => { setReportingId(thread.id); setReportReason(""); }} /></div>{reportingId === thread.id && <div className="mt-4 rounded-xl border border-red-300/25 bg-red-300/[.06] p-4"><label htmlFor={`report-reason-${thread.id}`} className="block text-sm font-semibold text-white">Report reason</label><Textarea id={`report-reason-${thread.id}`} className="mt-2" maxLength={1000} value={reportReason} disabled={reportMutationInFlight} onChange={(event) => setReportReason(event.target.value)} placeholder="Explain why this request should be reported." /><div className="mt-3 flex flex-wrap gap-2"><Button variant="danger" disabled={reportMutationInFlight || busy !== null || inboxLoadState !== "loaded" || !reportReason.trim()} onClick={() => void act(thread, "reported", reportReason)}>Submit report</Button><Button variant="ghost" disabled={reportMutationInFlight || busy !== null} onClick={() => { setReportingId(null); setReportReason(""); }}>Cancel</Button></div></div>}</>}</article></li>)}</ol>{inboxLoadState === "error" && threads.length > 0 && <PrivateLoadFailure label="Contact requests could not be refreshed" error={inboxLoadError} onRetry={() => void loadInbox()} />}{nextCursor && <Button variant="ghost" className="mt-5 w-full" disabled={moreLoading || busy !== null || inboxLoadState !== "loaded"} onClick={() => void loadOlder()}>{moreLoading && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Load older requests</Button>}</section>
  </div>;
}

function Action({ icon: Icon, label, danger = false, disabled, onClick }: { icon: typeof Check; label: string; danger?: boolean; disabled: boolean; onClick: () => void }) { return <Button variant={danger ? "danger" : "secondary"} className="min-h-11 px-3" disabled={disabled} onClick={onClick}><Icon className="size-4" aria-hidden />{label}</Button>; }
function PrivateLoadFailure({ label, error, onRetry }: { label: string; error: string; onRetry: () => void }) { return <div role="alert" className="mt-4 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4"><p className="font-semibold text-amber-50">{label}</p><p className="mt-1 text-sm leading-6 text-amber-100/85">{error}</p><Button variant="secondary" className="mt-3" onClick={onRetry}>Retry</Button></div>; }
const selectClass = "mt-1.5 w-full rounded-xl border border-white/12 bg-black/25 px-3.5 py-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15";
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date); }
