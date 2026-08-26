"use client";

import { useReverification } from "@clerk/nextjs";
import { CheckCircle2, CircleAlert, Clipboard, Clock3, Download, FileDown, LoaderCircle, LockKeyhole, RefreshCcw, ShieldAlert, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { NetworkNotice } from "@/components/network-notice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ApiRequestError, type SubjectGuard } from "@/lib/api";
import { ACCOUNT_DELETION_INTENT, accountLifecycleFeatureEnabled, cancelAccountDeletion, confirmAccountDeletion, exportAccount, fetchAccountLifecycleStatus, lifecycleError, lifecycleResult, parseDeletionRequest, presentLifecycleError, recoverAccountDeletionReceipt, requestAccountDeletion, type AccountLifecycleStatus } from "@/lib/account-lifecycle-api";
import { beginLogicalMutationAttempt, claimLogicalMutation, settleLogicalMutationAttempt, type LogicalMutationAttempt, type LogicalMutationClaimSlot } from "@/lib/logical-mutation";

type BusyAction = "export" | "request" | "recover" | "confirm" | "cancel" | null;
type DeletionPhase = "confirmation_pending" | "confirmation_accepted";
type LocalDeletion = { id: string; statusReceipt: string; phase: DeletionPhase };
type ExportResult = { response: Response };

export function AccountPrivacyCenter() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const authBoundary = accountPrivacyAuthBoundaryKey(configured, isLoaded, isSignedIn, subject);
  const authBoundaryRef = useRef(authBoundary);
  authBoundaryRef.current = authBoundary;
  if (!accountLifecycleFeatureEnabled()) return null;

  return <AccountPrivacyBoundary key={authBoundary} configured={configured} isLoaded={isLoaded} isSignedIn={isSignedIn} subject={subject} getToken={getToken} isBoundaryCurrent={() => authBoundaryRef.current === authBoundary} />;
}

export function accountPrivacyAuthBoundaryKey(configured: boolean, isLoaded: boolean, isSignedIn: boolean, subject: string | null) {
  if (!configured) return "unconfigured";
  if (!isLoaded) return "loading";
  if (!isSignedIn || !subject) return "signed-out";
  return `user:${subject}`;
}

function AccountPrivacyBoundary({ configured, isLoaded, isSignedIn, subject, getToken, isBoundaryCurrent }: { configured: boolean; isLoaded: boolean; isSignedIn: boolean; subject: string | null; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isBoundaryCurrent: SubjectGuard }) {
  if (!configured || !isLoaded || !isSignedIn || !subject) return <AccountGate configured={configured} loading={!isLoaded} />;

  return <AuthenticatedPrivacyCenter subject={subject} getToken={getToken} isSubjectCurrent={isBoundaryCurrent} />;
}

function AccountGate({ configured, loading }: { configured: boolean; loading: boolean }) {
  return <main className="mx-auto min-h-[calc(100vh-4rem)] max-w-2xl px-5 py-12 lg:px-8"><section className="w-full rounded-[2rem] border border-white/10 bg-panel p-7 text-center sm:p-10"><LockKeyhole className="mx-auto size-8 text-acid" aria-hidden /><h1 className="mt-5 font-display text-4xl font-semibold tracking-[-.045em] text-white">Private account privacy</h1><AsyncBoundaryMessage className="mt-3 text-sm leading-6 text-mist" loading={loading}>{loading ? "Checking your signed-in account." : configured ? "Sign in as a human to access account export and deletion controls." : "Clerk configuration is required before private account controls are available."}</AsyncBoundaryMessage></section></main>;
}

function AuthenticatedPrivacyCenter({ subject, getToken, isSubjectCurrent }: { subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: SubjectGuard }) {
  const [busy, setBusy] = useState<BusyAction>(null);
  const [deletion, setDeletion] = useState<LocalDeletion | null>(null);
  const [typedIntent, setTypedIntent] = useState("");
  const [receiptSaved, setReceiptSaved] = useState(false);
  const [canRecoverReceipt, setCanRecoverReceipt] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const deletionRef = useRef<LocalDeletion | null>(deletion);
  deletionRef.current = deletion;
  const confirmAttemptRef = useRef<LogicalMutationAttempt | null>(null);
  const confirmMutationClaimSlotRef = useRef<LogicalMutationClaimSlot>({ current: null });
  const exportAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => {
    exportAbortRef.current?.abort();
    exportAbortRef.current = null;
  }, []);

  const exportWithReverification = useReverification(async () => {
    const response = await exportAccount(getToken, isSubjectCurrent);
    return response.ok ? { response } satisfies ExportResult : lifecycleResult(response);
  });
  const requestWithReverification = useReverification(async (idempotencyKey: string) => {
    const response = await requestAccountDeletion(idempotencyKey, getToken, isSubjectCurrent);
    return lifecycleResult(response);
  });
  const confirmWithReverification = useReverification(async (deletionId: string, idempotencyKey: string) => {
    return confirmAccountDeletion(deletionId, getToken, isSubjectCurrent, idempotencyKey);
  });
  const recoverWithReverification = useReverification(async (idempotencyKey: string) => {
    const response = await recoverAccountDeletionReceipt(idempotencyKey, getToken, isSubjectCurrent);
    return lifecycleResult(response);
  });

  const clearFeedback = useCallback(() => { setNotice(""); setError(""); }, []);

  const downloadExport = useCallback(async () => {
    if (exportAbortRef.current !== null) return;
    const controller = new AbortController();
    exportAbortRef.current = controller;
    const exportIsCurrent = () => !controller.signal.aborted && isSubjectCurrent();
    clearFeedback();
    setBusy("export");
    try {
      const result = await exportWithReverification();
      if (!exportIsCurrent()) return;
      if (!isExportResult(result)) throw lifecycleError(result);
      if (!await saveSubjectBoundExport(result.response, exportIsCurrent)) return;
      if (!exportIsCurrent()) return;
      setNotice("Your NDJSON export started downloading directly. connect.md did not create a server-side export artifact.");
    } catch (caught) {
      if (exportIsCurrent()) setError(presentLifecycleError(caught));
    } finally {
      const stillCurrent = exportIsCurrent();
      if (exportAbortRef.current === controller) exportAbortRef.current = null;
      controller.abort();
      if (stillCurrent) setBusy(null);
    }
  }, [clearFeedback, exportWithReverification, isSubjectCurrent]);

  const requestDeletion = useCallback(async () => {
    clearFeedback();
    setBusy("request");
    try {
      const receipt = parseDeletionRequest(await requestWithReverification(newIdempotencyKey()));
      if (!isSubjectCurrent()) return;
      setDeletion({ id: receipt.deletionId, statusReceipt: receipt.statusReceipt, phase: "confirmation_pending" });
      setReceiptSaved(false);
      setCanRecoverReceipt(false);
      setTypedIntent("");
      setNotice("Deletion request recorded. It is pending your separate typed confirmation and can still be cancelled while the server permits cancellation.");
    } catch (caught) {
      if (isSubjectCurrent()) {
        setCanRecoverReceipt(caught instanceof ApiRequestError && caught.message === "account_deletion_request_exists");
        setError(presentLifecycleError(caught));
      }
    } finally {
      if (isSubjectCurrent()) setBusy(null);
    }
  }, [clearFeedback, isSubjectCurrent, requestWithReverification]);

  const recoverReceipt = useCallback(async () => {
    clearFeedback();
    setBusy("recover");
    try {
      const receipt = parseDeletionRequest(await recoverWithReverification(newIdempotencyKey()));
      if (!isSubjectCurrent()) return;
      setDeletion({ id: receipt.deletionId, statusReceipt: receipt.statusReceipt, phase: "confirmation_pending" });
      setReceiptSaved(false);
      setCanRecoverReceipt(false);
      setTypedIntent("");
      setNotice("The pending request's Lifecycle Receipt was rotated and recovered. Any older receipt is now invalid.");
    } catch (caught) {
      if (isSubjectCurrent()) setError(presentLifecycleError(caught));
    } finally {
      if (isSubjectCurrent()) setBusy(null);
    }
  }, [clearFeedback, isSubjectCurrent, recoverWithReverification]);

  const confirmDeletion = useCallback(async () => {
    if (busy !== null || !deletion || deletion.phase !== "confirmation_pending" || !receiptSaved || typedIntent !== ACCOUNT_DELETION_INTENT) return;
    const claim = claimLogicalMutation(confirmMutationClaimSlotRef.current);
    if (!claim) return;
    const requestSubject = subject;
    const requestDeletionId = deletion.id;
    const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent() && deletionRef.current?.id === requestDeletionId && deletionRef.current.phase === "confirmation_pending" && claim.isCurrent();
    if (!requestIsCurrent()) {
      claim.release();
      return;
    }
    const attempt = beginLogicalMutationAttempt(confirmAttemptRef.current, requestSubject, { operation: "confirm-account-deletion", deletionId: requestDeletionId, intent: ACCOUNT_DELETION_INTENT });
    confirmAttemptRef.current = attempt;
    clearFeedback();
    setBusy("confirm");
    try {
      const confirmed = await confirmWithReverification(requestDeletionId, attempt.idempotencyKey);
      if (!requestIsCurrent()) return;
      if (confirmed.deletionId !== requestDeletionId) throw new ApiRequestError("The deletion confirmation did not match the current request.", undefined, "server");
      confirmAttemptRef.current = null;
      setDeletion((current) => current && current.id === requestDeletionId ? { ...current, phase: "confirmation_accepted" } : current);
      setTypedIntent("");
      setNotice("Deletion confirmation was accepted. Keep the Lifecycle Receipt: it can read the API's sanitized lifecycle status even after the prior account credentials are denied.");
    } catch (caught) {
      if (!requestIsCurrent()) return;
      confirmAttemptRef.current = settleConfirmationAttempt(attempt, caught);
      setError(presentLifecycleError(caught));
    } finally {
      if (requestIsCurrent()) setBusy(null);
      claim.release();
    }
  }, [busy, clearFeedback, confirmWithReverification, deletion, isSubjectCurrent, receiptSaved, subject, typedIntent]);

  const cancelDeletion = useCallback(async () => {
    if (!deletion || deletion.phase !== "confirmation_pending") return;
    clearFeedback();
    setBusy("cancel");
    try {
      await cancelAccountDeletion(deletion.id, getToken, isSubjectCurrent);
      if (!isSubjectCurrent()) return;
      setDeletion(null);
      setReceiptSaved(false);
      setTypedIntent("");
      setNotice("The pending deletion request was cancelled by the server.");
    } catch (caught) {
      if (isSubjectCurrent()) setError(presentLifecycleError(caught));
    } finally {
      if (isSubjectCurrent()) setBusy(null);
    }
  }, [clearFeedback, deletion, getToken, isSubjectCurrent]);

  const deletionPending = deletion?.phase === "confirmation_pending";
  const confirmationAccepted = deletion?.phase === "confirmation_accepted";

  return <main className="pb-16"><section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.17),_transparent_34%),linear-gradient(135deg,_rgba(255,255,255,.04),_transparent_58%)]"><div className="mx-auto max-w-7xl px-5 py-12 lg:px-8 lg:py-16"><p className="eyebrow">Signed-in human control</p><h1 className="mt-4 max-w-4xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">Private account privacy.</h1><p className="mt-5 max-w-3xl text-lg leading-8 text-mist">Download the account data the service returns, or make a separately confirmed deletion request. These controls are private and are never shown in public discovery.</p><NetworkNotice label="Private account actions" /></div></section><section className="mx-auto grid max-w-7xl gap-6 px-5 py-8 lg:grid-cols-[minmax(0,1.3fr)_minmax(20rem,.7fr)] lg:px-8"><div className="space-y-6"><section aria-labelledby="account-export-title" className="rounded-[1.7rem] border border-white/10 bg-panel p-5 sm:p-7"><div className="flex items-start gap-3"><FileDown className="mt-0.5 size-6 shrink-0 text-acid" aria-hidden /><div><h2 id="account-export-title" className="text-2xl font-semibold tracking-[-.03em] text-white">Direct data export</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-mist">The API returns NDJSON directly to this browser after fresh verification. No export file or server-side download artifact is created.</p></div></div><div className="mt-5 flex flex-wrap items-center gap-3"><Button disabled={busy !== null} onClick={() => void downloadExport()}>{busy === "export" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Download className="size-4" aria-hidden />}Download account export</Button><span className="text-xs leading-5 text-mist">A new verification may be requested for each protected action.</span></div></section><section aria-labelledby="account-deletion-title" className="rounded-[1.7rem] border border-red-300/20 bg-red-300/[.045] p-5 sm:p-7"><div className="flex items-start gap-3"><Trash2 className="mt-0.5 size-6 shrink-0 text-red-200" aria-hidden /><div><h2 id="account-deletion-title" className="text-2xl font-semibold tracking-[-.03em] text-white">Request account deletion</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-mist">Start a request first. Confirmation is a distinct protected action and requires typing the exact intent below.</p></div></div>{!deletion && <div className="mt-6 rounded-2xl border border-red-300/20 bg-black/20 p-4"><p className="text-sm leading-6 text-red-100">This request is consequential. It does not make any claim about an immediate or complete erasure outcome.</p><Button className="mt-4" variant="danger" disabled={busy !== null} onClick={() => void requestDeletion()}>{busy === "request" && <LoaderCircle className="size-4 animate-spin" aria-hidden />}Request account deletion</Button></div>}{canRecoverReceipt && !deletion && <div className="mt-4 rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-4"><p className="text-sm leading-6 text-amber-100/85">A pending request already exists. Fresh human verification can rotate its old Lifecycle Receipt and return a replacement while it is still awaiting confirmation.</p><Button className="mt-4" variant="secondary" disabled={busy !== null} onClick={() => void recoverReceipt()}>{busy === "recover" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <RefreshCcw className="size-4" aria-hidden />}Recover pending Lifecycle Receipt</Button></div>}{deletionPending && <div className="mt-6 rounded-2xl border border-white/10 bg-black/20 p-4"><p className="inline-flex items-center gap-2 text-sm font-semibold text-white"><Clock3 className="size-4 text-amber-200" aria-hidden />Deletion request pending confirmation</p><p className="mt-2 text-sm leading-6 text-mist">Type <code className="rounded bg-white/10 px-1.5 py-0.5 text-white">{ACCOUNT_DELETION_INTENT}</code> exactly, then use the separate confirm action. Cancellation is offered only while the server still permits it.</p><LifecycleReceiptPanel key={deletion.statusReceipt} statusReceipt={deletion.statusReceipt} /><label className="mt-4 flex items-start gap-3 text-sm leading-6 text-white"><input className="mt-1 size-4 accent-[var(--acid)]" type="checkbox" checked={receiptSaved} onChange={(event) => setReceiptSaved(event.target.checked)} disabled={busy !== null} />I have saved this Lifecycle Receipt securely and understand that it is the only credential this view can use to read later sanitized status.</label><label className="mt-5 block text-sm font-semibold text-white" htmlFor="account-deletion-intent">Type {ACCOUNT_DELETION_INTENT} to confirm<Input id="account-deletion-intent" className="mt-2" value={typedIntent} onChange={(event) => setTypedIntent(event.target.value)} autoCapitalize="characters" autoCorrect="off" spellCheck={false} autoComplete="off" maxLength={16} disabled={busy !== null} aria-describedby="account-deletion-intent-help" /></label><p id="account-deletion-intent-help" className="mt-2 text-xs leading-5 text-mist">The receipt and deletion identifier remain only in this current page state; connect.md does not put them in browser persistence or URLs.</p><div className="mt-5 flex flex-wrap gap-3"><Button variant="danger" disabled={busy !== null || !receiptSaved || typedIntent !== ACCOUNT_DELETION_INTENT} onClick={() => void confirmDeletion()}>{busy === "confirm" && <LoaderCircle className="size-4 animate-spin" aria-hidden />}Confirm deletion</Button><Button variant="secondary" disabled={busy !== null} onClick={() => void cancelDeletion()}>{busy === "cancel" && <LoaderCircle className="size-4 animate-spin" aria-hidden />}<X className="size-4" aria-hidden />Cancel pending request</Button></div></div>}{confirmationAccepted && <div className="mt-6 rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-4"><p className="inline-flex items-center gap-2 text-sm font-semibold text-amber-50"><ShieldAlert className="size-4" aria-hidden />Confirmation accepted locally</p><p className="mt-2 text-sm leading-6 text-amber-100/85">No cancellation or receipt recovery is offered after confirmation. Keep the saved Lifecycle Receipt private; it can read only the API&apos;s sanitized lifecycle state and grants no document access or cancellation authority.</p><LifecycleReceiptPanel key={deletion.statusReceipt} statusReceipt={deletion.statusReceipt} /></div>}</section>{notice && <p role="status" aria-live="polite" className="rounded-2xl border border-acid/25 bg-acid/[.07] p-4 text-sm leading-6 text-acid">{notice}</p>}{error && <p role="alert" className="rounded-2xl border border-red-300/25 bg-red-300/[.08] p-4 text-sm leading-6 text-red-100"><CircleAlert className="mr-2 inline size-4" aria-hidden />{error}</p>}</div><aside aria-labelledby="lifecycle-visibility-title" className="h-fit rounded-[1.7rem] border border-white/10 bg-black/20 p-5 sm:p-6"><p className="eyebrow">State boundary</p><h2 id="lifecycle-visibility-title" className="mt-3 text-2xl font-semibold tracking-[-.03em] text-white">What this page can prove</h2><ol className="mt-6 space-y-4"><LifecycleState icon={Clock3} title="Request pending" detail={deletionPending ? "The API returned a request identifier and Lifecycle Receipt in this view; cancellation remains only an attempt subject to server state." : "Not observed in this browser session."} active={deletionPending} /><LifecycleState icon={CheckCircle2} title="Confirmation accepted" detail={confirmationAccepted ? "The confirm endpoint matched and accepted the current deletion request." : "Not observed in this browser session."} active={confirmationAccepted} /><LifecycleState icon={CircleAlert} title="Erasing, held, failed, and terminal" detail="Observable only by explicitly checking a valid Lifecycle Receipt. This page does not poll, infer, or turn account-access denial into an erasure claim." /></ol><p className="mt-6 border-t border-white/10 pt-5 text-xs leading-5 text-mist">This page never persists a receipt. Pending-only recovery requires fresh human verification and rotates the old receipt; recovery is unavailable after confirmation.</p></aside></section></main>;
}

function LifecycleReceiptPanel({ statusReceipt }: { statusReceipt: string }) {
  const [status, setStatus] = useState<AccountLifecycleStatus | null>(null);
  const [busy, setBusy] = useState<"copy" | "status" | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const copyReceipt = useCallback(async () => {
    setBusy("copy");
    setNotice("");
    setError("");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(statusReceipt);
      setNotice("Lifecycle Receipt copied. Store it as a secret; do not send it in messages or URLs.");
    } catch {
      setError("Automatic copy was unavailable. Select and copy the receipt manually.");
    } finally {
      setBusy(null);
    }
  }, [statusReceipt]);

  const checkStatus = useCallback(async () => {
    setBusy("status");
    setNotice("");
    setError("");
    try {
      setStatus(await fetchAccountLifecycleStatus(statusReceipt));
    } catch (caught) {
      setError(presentLifecycleError(caught));
    } finally {
      setBusy(null);
    }
  }, [statusReceipt]);

  return <section aria-label="Lifecycle Receipt" className="mt-5 rounded-2xl border border-acid/20 bg-black/25 p-4"><p className="text-sm font-semibold text-white">Lifecycle Receipt</p><p className="mt-2 text-xs leading-5 text-mist">This bearer-like secret reads sanitized deletion status only. Anyone holding it can read that state until it expires or is invalidated.</p><code className="mt-3 block select-all break-all rounded-xl border border-white/10 bg-black/35 p-3 text-xs leading-5 text-acid">{statusReceipt}</code><div className="mt-4 flex flex-wrap gap-3"><Button variant="secondary" disabled={busy !== null} onClick={() => void copyReceipt()}>{busy === "copy" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Clipboard className="size-4" aria-hidden />}Copy receipt</Button><Button variant="secondary" disabled={busy !== null} onClick={() => void checkStatus()}>{busy === "status" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <RefreshCcw className="size-4" aria-hidden />}Check sanitized status</Button></div>{notice && <p role="status" aria-live="polite" className="mt-3 text-xs leading-5 text-acid">{notice}</p>}{error && <p role="alert" className="mt-3 text-xs leading-5 text-red-100">{error}</p>}{status && <dl className="mt-4 grid gap-3 border-t border-white/10 pt-4 text-xs sm:grid-cols-2"><StatusField label="State" value={formatLifecycleState(status.state)} /><StatusField label="Observed" value={formatStatusDate(status.observedAt)} /><StatusField label="Policy" value={status.policyVersion} /><StatusField label="Check again after" value={`${status.nextCheckAfterSeconds} seconds`} />{status.condition && <StatusField label="Condition" value={formatLifecycleState(status.condition)} />}{status.receiptExpiresAt && <StatusField label="Receipt expires" value={formatStatusDate(status.receiptExpiresAt)} />}</dl>}</section>;
}

function StatusField({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-mist">{label}</dt><dd className="mt-1 font-semibold text-white">{value}</dd></div>;
}

function formatLifecycleState(value: string) {
  return value.replaceAll("_", " ").replace(/^./u, (character) => character.toUpperCase());
}

function formatStatusDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function LifecycleState({ icon: Icon, title, detail, active = false }: { icon: typeof Clock3; title: string; detail: string; active?: boolean }) {
  return <li className="flex gap-3"><span className={`grid size-8 shrink-0 place-items-center rounded-full ${active ? "bg-acid text-ink" : "bg-white/[.08] text-mist"}`}><Icon className="size-4" aria-hidden /></span><div><h3 className="text-sm font-semibold text-white">{title}</h3><p className="mt-1 text-sm leading-6 text-mist">{detail}</p></div></li>;
}

function isExportResult(value: unknown): value is ExportResult {
  return typeof value === "object" && value !== null && "response" in value && (value as { response?: unknown }).response instanceof Response;
}

function newIdempotencyKey() {
  if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function") {
    throw new ApiRequestError("This browser cannot create the idempotency key required for a deletion request.", undefined, "configuration");
  }
  return crypto.randomUUID();
}

function settleConfirmationAttempt(attempt: LogicalMutationAttempt, error: unknown) {
  const settled = settleLogicalMutationAttempt(attempt, error);
  return settled ?? (error instanceof ApiRequestError && error.code === "offline" ? attempt : null);
}

export async function saveSubjectBoundExport(response: Response, isSubjectCurrent: SubjectGuard): Promise<boolean> {
  if (!isSubjectCurrent()) return false;
  if (!response.headers.get("content-type")?.toLowerCase().includes("application/x-ndjson")) {
    throw new ApiRequestError("The API returned an unexpected account export format.", response.status, "server");
  }
  if (!isSubjectCurrent()) return false;
  const blob = await response.blob();
  if (!isSubjectCurrent()) return false;

  let objectUrl: string | null = null;
  let anchor: HTMLAnchorElement | null = null;
  try {
    if (!isSubjectCurrent()) return false;
    objectUrl = URL.createObjectURL(blob);
    if (!isSubjectCurrent()) return false;
    anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = "connectmd-account-export.ndjson";
    anchor.style.display = "none";
    if (!isSubjectCurrent()) return false;
    document.body.append(anchor);
    if (!isSubjectCurrent()) return false;
    anchor.click();
    return true;
  } finally {
    anchor?.remove();
    if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
  }
}
