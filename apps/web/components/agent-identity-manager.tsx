"use client";

import { Check, Copy, LoaderCircle, ShieldCheck, UserRound } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { beginLogicalMutationAttempt, claimLogicalMutation, settleLogicalMutationAttempt, type LogicalMutationAttempt, type LogicalMutationClaimSlot } from "@/lib/logical-mutation";
import { AGENT_IDENTITY_MAX_ACTIVE, AGENT_MANDATE_MAX_DAYS, createAgentIdentity, issueAgentMandate, listAgentIdentities, listAgentMandates, revokeAgentMandate, withdrawAgentIdentity, type AgentIdentity, type AgentMandate } from "@/lib/agent-identity-api";
import { ApiRequestError, presentApiError } from "@/lib/api";
import { listApplicationDocuments, type ApplicationDocument } from "@/lib/recruitment-api";

export function AgentIdentityManager() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  if (!configured || !isLoaded || !isSignedIn || !subject) return <IdentityGate configured={configured} loading={!isLoaded} />;
  return <AuthenticatedIdentityManager key={subject} subject={subject} getToken={getToken} isSubjectCurrent={() => subjectRef.current === subject} />;
}

function AuthenticatedIdentityManager({ subject, getToken, isSubjectCurrent }: { subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean }) {
  const [identities, setIdentities] = useState<AgentIdentity[]>([]);
  const [profiles, setProfiles] = useState<ApplicationDocument[]>([]);
  const [mandates, setMandates] = useState<Record<string, AgentMandate[]>>({});
  const [identityLoadState, setIdentityLoadState] = useState<"loading" | "loaded" | "error">("loading");
  const [documentLoadState, setDocumentLoadState] = useState<"loading" | "loaded" | "error">("loading");
  const [identityLoadError, setIdentityLoadError] = useState("");
  const [documentLoadError, setDocumentLoadError] = useState("");
  const [mandateLoadStates, setMandateLoadStates] = useState<Record<string, "loading" | "loaded" | "error">>({});
  const [mandateLoadErrors, setMandateLoadErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [secret, setSecret] = useState<{ identityHandle: string; mandateId: string; value: string; copied: boolean } | null>(null);
  const [handle, setHandle] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [profileHandle, setProfileHandle] = useState("");
  const [expiry, setExpiry] = useState(defaultMandateExpiry());
  const mutationAttemptsRef = useRef(new Map<string, LogicalMutationAttempt>());
  const identityMutationClaimSlotRef = useRef<LogicalMutationClaimSlot>({ current: null });
  const beginAttempt = (slot: string, requestSubject: string, intent: unknown) => { const attempt = beginLogicalMutationAttempt(mutationAttemptsRef.current.get(slot) ?? null, requestSubject, intent); mutationAttemptsRef.current.set(slot, attempt); return attempt; };
  const settleAttempt = (slot: string, attempt: LogicalMutationAttempt, error: unknown) => { const next = settleLogicalMutationAttempt(attempt, error); if (next) mutationAttemptsRef.current.set(slot, next); else mutationAttemptsRef.current.delete(slot); return next; };

  const loadIdentities = useCallback(async () => {
    if (!isSubjectCurrent()) return null;
    setIdentityLoadState("loading"); setIdentityLoadError("");
    try { const next = await listAgentIdentities(getToken, isSubjectCurrent); if (!isSubjectCurrent()) return null; setIdentities(next); setIdentityLoadState("loaded"); return next; }
    catch (error) { if (isSubjectCurrent()) { setIdentityLoadState("error"); setIdentityLoadError(presentApiError(error)); } return null; }
  }, [getToken, isSubjectCurrent]);
  const loadDocuments = useCallback(async () => {
    if (!isSubjectCurrent()) return;
    setDocumentLoadState("loading"); setDocumentLoadError("");
    try {
      const next = await listApplicationDocuments(getToken, isSubjectCurrent); if (!isSubjectCurrent()) return;
      const publicProfiles = next.filter((document) => document.kind === "profile" && document.visibility === "public");
      setProfiles(publicProfiles);
      setProfileHandle((current) => current && publicProfiles.some((profile) => profile.identifier === current) ? current : publicProfiles[0]?.identifier || "");
      setDocumentLoadState("loaded");
    } catch (error) { if (isSubjectCurrent()) { setDocumentLoadState("error"); setDocumentLoadError(presentApiError(error)); } }
  }, [getToken, isSubjectCurrent]);
  const loadMandates = useCallback(async (handle: string) => {
    if (!isSubjectCurrent()) return;
    setMandateLoadStates((current) => ({ ...current, [handle]: "loading" }));
    setMandateLoadErrors((current) => { const next = { ...current }; delete next[handle]; return next; });
    try {
      const next = await listAgentMandates(handle, getToken, isSubjectCurrent); if (!isSubjectCurrent()) return;
      setMandates((current) => ({ ...current, [handle]: next }));
      setMandateLoadStates((current) => ({ ...current, [handle]: "loaded" }));
    } catch (error) { if (isSubjectCurrent()) { setMandateLoadStates((current) => ({ ...current, [handle]: "error" })); setMandateLoadErrors((current) => ({ ...current, [handle]: presentApiError(error) })); } }
  }, [getToken, isSubjectCurrent]);

  useEffect(() => { void Promise.all([loadIdentities(), loadDocuments()]); }, [loadDocuments, loadIdentities]);
  useEffect(() => {
    identities.forEach((identity) => { if (mandateLoadStates[identity.handle] === undefined) void loadMandates(identity.handle); });
  }, [identities, loadMandates, mandateLoadStates]);

  const refresh = useCallback(async () => {
    const knownHandles = identities.map((identity) => identity.handle);
    await Promise.all([loadIdentities(), loadDocuments(), ...knownHandles.map((handle) => loadMandates(handle))]);
  }, [identities, loadDocuments, loadIdentities, loadMandates]);

  const activeIdentityCount = useMemo(() => identities.filter((identity) => identity.status === "active" && identity.profileHandle === profileHandle).length, [identities, profileHandle]);
  const loading = identityLoadState === "loading" || documentLoadState === "loading" || Object.values(mandateLoadStates).some((state) => state === "loading");
  const validExpiry = isValidMandateExpiry(expiry);
  const canCreate = activeIdentityCount < AGENT_IDENTITY_MAX_ACTIVE && Boolean(profileHandle) && handle.trim().length > 0 && displayName.trim().length > 0 && description.trim().length > 0;

  async function create() {
    if (!canCreate || busy || !isSubjectCurrent()) return;
    const identityClaim = claimLogicalMutation(identityMutationClaimSlotRef.current);
    if (!identityClaim) return;
    const requestSubject = subject;
    const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent() && identityClaim.isCurrent();
    setBusy("identity:create"); setMessage(""); setSecret(null);
    try {
      const attempt = beginAttempt("identity:create", requestSubject, { operation: "create-agent-identity", handle: handle.trim(), displayName: displayName.trim(), description: description.trim(), profileHandle });
      await createAgentIdentity({ handle: handle.trim(), displayName: displayName.trim(), description: description.trim(), profileHandle }, getToken, isSubjectCurrent, attempt.idempotencyKey);
      if (!requestIsCurrent()) return;
      mutationAttemptsRef.current.delete("identity:create");
      setHandle(""); setDisplayName(""); setDescription("");
      await loadIdentities();
      if (requestIsCurrent()) setMessage("Created an owner-attested public Agent Identity linked to your public profile.");
    } catch (error) {
      if (!requestIsCurrent()) return;
      const attempt = mutationAttemptsRef.current.get("identity:create"); if (attempt) settleAttempt("identity:create", attempt, error);
      if (requestIsCurrent()) setMessage(mutationAttemptsRef.current.has("identity:create") ? "The identity may have been created. Retry the unchanged action to recover the same result. " + presentApiError(error) : presentApiError(error));
    } finally {
      if (requestIsCurrent()) setBusy(null);
      identityClaim.release();
    }
  }

  async function withdraw(identity: AgentIdentity) {
    if (busy || !isSubjectCurrent()) return;
    const requestSubject = subject;
    const slot = `identity:${identity.handle}`;
    const identityClaim = claimLogicalMutation(identityMutationClaimSlotRef.current);
    if (!identityClaim) return;
    const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent() && identityClaim.isCurrent();
    if (!window.confirm(`Withdraw @${identity.handle}? Its public page will stop resolving and active mandates will no longer be usable.`) || !requestIsCurrent()) {
      identityClaim.release();
      return;
    }
    setBusy(`identity:${identity.handle}`); setMessage(""); setSecret(null);
    try {
      const attempt = beginAttempt(slot, requestSubject, { operation: "withdraw-agent-identity", handle: identity.handle });
      await withdrawAgentIdentity(identity.handle, getToken, isSubjectCurrent, attempt.idempotencyKey);
      if (!requestIsCurrent()) return;
      mutationAttemptsRef.current.delete(slot);
      setIdentities((current) => current.filter((item) => item.handle !== identity.handle));
      setMandates((current) => { const next = { ...current }; delete next[identity.handle]; return next; });
      if (requestIsCurrent()) setMessage(`Withdrew @${identity.handle}.`);
    } catch (error) {
      if (!requestIsCurrent()) return;
      const attempt = mutationAttemptsRef.current.get(slot); if (attempt) settleAttempt(slot, attempt, error);
      if (requestIsCurrent()) setMessage(mutationAttemptsRef.current.has(slot) ? "The withdrawal may have completed. Retry the unchanged action to recover the same result. " + presentApiError(error) : presentApiError(error));
    } finally {
      if (requestIsCurrent()) setBusy(null);
      identityClaim.release();
    }
  }

  async function issue(identity: AgentIdentity) {
    if (busy || !validExpiry || !window.confirm(`Issue one internal-contact mandate for @${identity.handle}? It expires within ${AGENT_MANDATE_MAX_DAYS} days and is limited to consent-gated internal contact requests.`) || !isSubjectCurrent()) return;
    setBusy(`issue:${identity.handle}`); setMessage(""); setSecret(null);
    try {
      const requestSubject = subject;
      const slot = `issue:${identity.handle}`; const attempt = beginAttempt(slot, requestSubject, { operation: "issue-agent-mandate", handle: identity.handle, expiresAt: new Date(expiry).toISOString() });
      const result = await issueAgentMandate(identity.handle, new Date(expiry).toISOString(), getToken, isSubjectCurrent, attempt.idempotencyKey);
      if (!isSubjectCurrent()) return;
      mutationAttemptsRef.current.delete(slot);
      if (result.kind === "issued") {
        setSecret({ identityHandle: identity.handle, mandateId: result.mandate.id, value: result.secret, copied: false });
        await loadMandates(identity.handle);
        if (isSubjectCurrent()) setMessage(`Issued an internal-contact mandate for @${identity.handle}.`);
      } else {
        await loadMandates(identity.handle);
        if (isSubjectCurrent()) setMessage("The identical issue request was already recorded, but its one-time secret cannot be recovered. Revoke the listed mandate, then issue a replacement.");
      }
    } catch (error) {
      if (!isSubjectCurrent()) return;
      const slot = `issue:${identity.handle}`; const attempt = mutationAttemptsRef.current.get(slot); if (attempt) settleAttempt(slot, attempt, error); if (error instanceof ApiRequestError && (error.code === "request" || error.code === "server")) setMessage("Mandate issuance may have succeeded but its one-time secret was not received. Refresh the mandate inventory, revoke any unexpected active mandate, then issue a replacement.");
      else setMessage(presentApiError(error));
    }
    finally { if (isSubjectCurrent()) setBusy(null); }
  }

  async function revoke(identity: AgentIdentity, mandate: AgentMandate) {
    if (busy || !window.confirm(`Revoke mandate ${mandate.grantPrefix}? This cannot be undone.`) || !isSubjectCurrent()) return;
    setBusy(`mandate:${mandate.id}`); setMessage("");
    try {
      const requestSubject = subject;
      const slot = `mandate:${mandate.id}`; const attempt = beginAttempt(slot, requestSubject, { operation: "revoke-agent-mandate", handle: identity.handle, mandateId: mandate.id });
      await revokeAgentMandate(identity.handle, mandate.id, getToken, isSubjectCurrent, attempt.idempotencyKey);
      if (!isSubjectCurrent()) return;
      mutationAttemptsRef.current.delete(slot);
      setSecret((current) => current?.mandateId === mandate.id ? null : current);
      await loadMandates(identity.handle); if (isSubjectCurrent()) setMessage(`Revoked mandate ${mandate.grantPrefix}.`);
    } catch (error) { const slot = `mandate:${mandate.id}`; const attempt = mutationAttemptsRef.current.get(slot); if (attempt) settleAttempt(slot, attempt, error); if (isSubjectCurrent()) setMessage(mutationAttemptsRef.current.has(slot) ? "The mandate revocation may have completed. Retry the unchanged action to recover the same result. " + presentApiError(error) : presentApiError(error)); }
    finally { if (isSubjectCurrent()) setBusy(null); }
  }

  async function copySecret() {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret.value);
      setSecret((current) => current ? { ...current, copied: true } : null);
    } catch { setMessage("The one-time secret is still displayed, but copying was blocked. Copy it manually into your secret manager."); }
  }

  return <section aria-labelledby="agent-identities-title" className="mt-8 grid gap-5 xl:grid-cols-[minmax(0,.9fr)_minmax(0,1.1fr)]">
    <section className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6">
      <div className="flex gap-3"><UserRound className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden /><div><p className="eyebrow">Public representative identity</p><h2 id="agent-identities-title" className="mt-2 text-2xl font-semibold text-white">Name an agent, without turning it into a credential.</h2><p className="mt-2 text-sm leading-6 text-mist">An Agent Identity is owner-attested and linked to one of your public profiles. It is not independent verification, employment status, or proof of a live mandate.</p></div></div>
      {message && <p role="status" className="mt-5 rounded-xl border border-white/10 bg-black/20 p-3 text-sm leading-6 text-mist">{message}</p>}
      {secret && <section aria-label="One-time mandate secret" className="mt-5 rounded-xl border border-acid/30 bg-acid/[.07] p-4"><div className="flex items-start gap-3"><ShieldCheck className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden /><div><h3 className="font-semibold text-white">Copy this secret now</h3><p className="mt-1 text-sm leading-6 text-mist">It is shown once for @{secret.identityHandle}. This workspace does not save it. If it is lost, revoke mandate {secret.mandateId} and issue a replacement.</p></div></div><code className="mt-4 block overflow-x-auto rounded-lg bg-black/30 p-3 text-xs text-white">{secret.value}</code><Button type="button" className="mt-3" onClick={() => void copySecret()}>{secret.copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}{secret.copied ? "Copied" : "Copy secret"}</Button></section>}
      <form className="mt-6 grid gap-4" onSubmit={(event) => { event.preventDefault(); void create(); }}>
        <label className="text-sm font-semibold text-white">Agent handle<Input value={handle} maxLength={100} autoCapitalize="none" spellCheck={false} disabled={busy !== null} onChange={(event) => setHandle(event.target.value)} placeholder="your-agent" /></label>
        <label className="text-sm font-semibold text-white">Display name<Input value={displayName} maxLength={100} disabled={busy !== null} onChange={(event) => setDisplayName(event.target.value)} placeholder="Profile steward" /></label>
        <label className="text-sm font-semibold text-white">Public description<Textarea value={description} maxLength={500} disabled={busy !== null} onChange={(event) => setDescription(event.target.value)} placeholder="What this owner-attested representative identity is for." /></label>
        <label className="text-sm font-semibold text-white">Linked public profile<select value={profileHandle} disabled={busy !== null || documentLoadState !== "loaded" || profiles.length === 0} onChange={(event) => setProfileHandle(event.target.value)} className={selectClass}><option value="">{documentLoadState === "loading" ? "Loading public profiles…" : documentLoadState === "error" ? "Public profiles unavailable" : "Choose a public profile"}</option>{profiles.map((profile) => <option key={profile.id} value={profile.identifier}>@{profile.identifier}</option>)}</select></label>
        {documentLoadState === "error" && <PrivateLoadFailure label="Public profiles could not be loaded" error={documentLoadError} onRetry={() => void loadDocuments()} />}
        {profiles.length === 0 && documentLoadState === "loaded" && <p className="text-sm leading-6 text-amber-100/85">Publish a profile first. Private profiles cannot be linked to a public Agent Identity.</p>}
        <p className="text-xs leading-5 text-mist">At most {AGENT_IDENTITY_MAX_ACTIVE} active identities may be linked to the selected public profile. Active now: {activeIdentityCount}.</p>
        <Button type="submit" disabled={busy !== null || !canCreate}>{busy === "identity:create" && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Create owner-attested identity</Button>
      </form>
    </section>
    <section className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="eyebrow">Private mandate inventory</p><h2 className="mt-2 text-2xl font-semibold text-white">Time-bound internal consent</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-mist">A mandate has exactly one scope: internal_contact_request. It can make a consent-gated internal request only; it cannot send human mandates, expose an external endpoint, or prove that work is current.</p></div><Button variant="secondary" disabled={loading || busy !== null} onClick={() => void refresh()}>{loading && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Refresh</Button></div>
      <label className="mt-5 block max-w-sm text-sm font-semibold text-white">New mandate expiry<Input type="datetime-local" min={minimumMandateExpiry()} max={maximumMandateExpiry()} value={expiry} disabled={busy !== null} onChange={(event) => setExpiry(event.target.value)} /></label>
      {!validExpiry && <p className="mt-2 text-sm text-amber-100/85">Choose a future expiry no more than {AGENT_MANDATE_MAX_DAYS} days away.</p>}
      {identityLoadState === "loading" && identities.length === 0 ? <p role="status" className="mt-6 text-sm text-mist">Loading owned identities and private mandate inventory…</p> : identityLoadState === "error" && identities.length === 0 ? <PrivateLoadFailure label="Agent Identities could not be loaded" error={identityLoadError} onRetry={() => void loadIdentities()} /> : identityLoadState === "loaded" && identities.length === 0 ? <p className="mt-6 rounded-xl border border-dashed border-white/15 p-5 text-sm leading-6 text-mist">No owned Agent Identities yet. Creating one does not issue a credential or a mandate.</p> : <ul className="mt-6 space-y-4">{identities.map((identity) => <li key={identity.handle} className="rounded-2xl border border-white/10 bg-black/15 p-4"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-white">@{identity.handle}</h3><Status value={identity.status} /></div><p className="mt-2 text-sm leading-6 text-mist">{identity.displayName} · linked to <Link href={`/p/${encodeURIComponent(identity.profileHandle)}`} className="text-acid underline-offset-4 hover:underline">@{identity.profileHandle}</Link></p></div>{identity.status === "active" && <div className="flex flex-wrap gap-2"><Button type="button" variant="secondary" disabled={busy !== null || !validExpiry || mandateLoadStates[identity.handle] !== "loaded" || (mandates[identity.handle] ?? []).some((mandate) => mandate.status === "active")} onClick={() => void issue(identity)}>{busy === `issue:${identity.handle}` && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Issue mandate</Button><Button type="button" variant="danger" disabled={busy !== null} onClick={() => void withdraw(identity)}>{busy === `identity:${identity.handle}` && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Withdraw</Button></div>}</div><MandateInventory identity={identity} state={mandateLoadStates[identity.handle] ?? "loading"} error={mandateLoadErrors[identity.handle] ?? ""} mandates={mandates[identity.handle]} busy={busy} onRetry={() => void loadMandates(identity.handle)} onRevoke={(mandate) => void revoke(identity, mandate)} /></li>)}</ul>}
      {identityLoadState === "error" && identities.length > 0 && <PrivateLoadFailure label="Agent Identities could not be refreshed" error={identityLoadError} onRetry={() => void loadIdentities()} />}
    </section>
  </section>;
}

function IdentityGate({ configured, loading }: { configured: boolean; loading: boolean }) { return <section className="mt-8 rounded-[1.5rem] border border-white/10 bg-panel p-6"><h2 className="text-xl font-semibold text-white">Agent Identity management is human-only</h2><AsyncBoundaryMessage className="mt-2 text-sm leading-6 text-mist" loading={loading}>{loading ? "Loading your signed-in session…" : configured ? "Sign in as the profile owner to create, withdraw, issue, or revoke Agent Identities and mandates." : "Authentication is not configured for this deployment."}</AsyncBoundaryMessage></section>; }
function PrivateLoadFailure({ label, error, onRetry }: { label: string; error: string; onRetry: () => void }) { return <div role="alert" className="mt-4 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4"><p className="font-semibold text-amber-50">{label}</p><p className="mt-1 text-sm leading-6 text-amber-100/85">{error}</p><Button variant="secondary" className="mt-3" onClick={onRetry}>Retry</Button></div>; }
function MandateInventory({ identity, state, error, mandates, busy, onRetry, onRevoke }: { identity: AgentIdentity; state: "loading" | "loaded" | "error"; error: string; mandates: AgentMandate[] | undefined; busy: string | null; onRetry: () => void; onRevoke: (mandate: AgentMandate) => void }) {
  if (state === "loading" && mandates === undefined) return <p role="status" className="mt-4 text-xs text-mist/75">Loading private mandates…</p>;
  if (state === "error") return <PrivateLoadFailure label={`Mandates for @${identity.handle} could not be loaded`} error={error} onRetry={onRetry} />;
  if (state === "loaded" && (mandates ?? []).length === 0) return <p className="mt-4 text-xs text-mist/75">No mandates issued.</p>;
  return <>{state === "loading" && <p role="status" className="mt-4 text-xs text-mist/75">Refreshing private mandates; the previously loaded inventory remains visible.</p>}<ul className="mt-4 space-y-2">{(mandates ?? []).map((mandate) => <li key={mandate.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 px-3 py-3"><p className="text-xs leading-5 text-mist"><span className="font-semibold text-white">{mandate.grantPrefix}</span> · {mandate.scope} · expires {formatDate(mandate.expiresAt)} <span className="ml-1"><Status value={mandate.status} /></span></p>{mandate.status === "active" && <Button type="button" variant="danger" className="min-h-11 px-3" disabled={busy !== null} onClick={() => onRevoke(mandate)}>{busy === `mandate:${mandate.id}` && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Revoke</Button>}</li>)}</ul></>;
}
function Status({ value }: { value: string }) { return <span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${value === "active" ? "bg-acid/[.12] text-acid" : value === "revoked" || value === "withdrawn" ? "bg-rose-300/[.12] text-rose-100" : "bg-white/[.08] text-mist"}`}>{value}</span>; }
function defaultMandateExpiry() { const date = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000); date.setSeconds(0, 0); return localDateTimeValue(date); }
function minimumMandateExpiry() { const date = new Date(Date.now() + 60_000); date.setSeconds(0, 0); return localDateTimeValue(date); }
function maximumMandateExpiry() { const date = new Date(Date.now() + AGENT_MANDATE_MAX_DAYS * 24 * 60 * 60 * 1000); date.setSeconds(0, 0); return localDateTimeValue(date); }
function isValidMandateExpiry(value: string) { const time = new Date(value).getTime(); return Number.isFinite(time) && time > Date.now() && time <= Date.now() + AGENT_MANDATE_MAX_DAYS * 24 * 60 * 60 * 1000; }
function localDateTimeValue(date: Date) { const offset = date.getTimezoneOffset() * 60_000; return new Date(date.getTime() - offset).toISOString().slice(0, 16); }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString(); }
const selectClass = "mt-1.5 min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:cursor-not-allowed disabled:opacity-60";
