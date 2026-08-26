"use client";

import { Activity, Bot, Check, Copy, Eye, KeyRound, LoaderCircle, Octagon, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import {
  AgentGrantInventoryPanel,
  AgentProposalReviewPanel,
  formatTime,
  PrivateLoadFailure,
} from "@/components/agent-delegation-panels";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ApiRequestError, presentApiError } from "@/lib/api";
import { continuousAgentHandoff as buildContinuousAgentHandoff } from "@/lib/agent-contract-guides";
import { beginLogicalMutationAttempt, retainLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { appendCursorPage as appendPrivateCursorPage } from "@/lib/cursor-page";
import { createDelegation, decideAgentProposal, emergencyStopDelegations, listAgentProposalsForSubject, listDelegationAudit, listDelegations, listOwnedDocumentOptions, loadProposalBaseMarkdown, revokeDelegation, type AgentDelegation, type AgentProposal, type DelegationAuditEvent, type DelegationMode, type OwnedDocumentOption } from "@/lib/agent-api";
import {
  beginDelegationInventoryRead,
  beginDelegationResourceRead,
  claimDelegationMutation,
  commitProposalBaseMarkdownIfCurrent,
  createDelegationMutationCoordinator,
  finishDelegationResourceRead,
  isCurrentDelegationMutation,
  isCurrentDelegationResource,
  mergeProposalFirstPage,
  releaseDelegationMutation,
  resetDelegationMutationCoordinator,
  upsertDelegation,
} from "@/lib/agent-delegation-state";
import type {
  DelegationMutationClaim,
  DelegationMutationResource,
} from "@/lib/agent-delegation-state";

export {
  beginDelegationInventoryRead,
  beginDelegationResourceRead,
  claimDelegationMutation,
  commitProposalBaseMarkdownIfCurrent,
  createDelegationMutationCoordinator,
  finishDelegationResourceRead,
  isCurrentDelegationMutation,
  isCurrentDelegationResource,
  mergeProposalFirstPage,
  releaseDelegationMutation,
  resetDelegationMutationCoordinator,
  upsertDelegation,
} from "@/lib/agent-delegation-state";
export type {
  DelegationInventoryResource,
  DelegationMutationClaim,
  DelegationMutationCoordinator,
  DelegationMutationResource,
  DelegationReadResource,
} from "@/lib/agent-delegation-state";

export function continuousAgentHandoff(grant: AgentDelegation): string {
  return buildContinuousAgentHandoff(grant);
}

export function AgentDelegationManager() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  if (!configured || !isLoaded || !isSignedIn || !subject) return <SignedOutState configured={configured} loading={!isLoaded} />;
  return <AuthenticatedManager key={subject} subject={subject} getToken={getToken} isSubjectCurrent={() => subjectRef.current === subject} />;
}

function AuthenticatedManager({ subject, getToken, isSubjectCurrent }: { subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean }) {
  const [delegations, setDelegations] = useState<AgentDelegation[]>([]);
  const [documents, setDocuments] = useState<OwnedDocumentOption[]>([]);
  const [audit, setAudit] = useState<DelegationAuditEvent[]>([]);
  const [proposals, setProposals] = useState<AgentProposal[]>([]);
  const [proposalCursor, setProposalCursor] = useState<string | null>(null);
  const [proposalBases, setProposalBases] = useState<Record<string, string>>({});
  const [loadStates, setLoadStates] = useState({ grants: "loading", documents: "loading", audit: "loading", proposals: "loading" } as Record<"grants" | "documents" | "audit" | "proposals", "loading" | "loaded" | "error">);
  const [loadErrors, setLoadErrors] = useState({ grants: "", documents: "", audit: "", proposals: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [secret, setSecret] = useState<{ value: string; copied: boolean } | null>(null);
  const [name, setName] = useState("");
  const [mode, setMode] = useState<DelegationMode>("proposal");
  const [resourceType, setResourceType] = useState<"owner" | "document">("document");
  const [resourceId, setResourceId] = useState("");
  const [expiry, setExpiry] = useState(defaultExpiry());
  const [allowVisibilityChange, setAllowVisibilityChange] = useState(false);
  const proposalsRef = useRef(proposals);
  const deliveredCursorsRef = useRef(new Set<string>());
  const moreInFlightRef = useRef(false);
  const mutationAttemptsRef = useRef(new Map<string, LogicalMutationAttempt>());
  const mutationCoordinatorRef = useRef(
    createDelegationMutationCoordinator(subject),
  );
  const beginAttempt = (slot: string, requestSubject: string, intent: unknown) => { const attempt = beginLogicalMutationAttempt(mutationAttemptsRef.current.get(slot) ?? null, requestSubject, intent); mutationAttemptsRef.current.set(slot, attempt); return attempt; };
  proposalsRef.current = proposals;

  useEffect(() => {
    resetDelegationMutationCoordinator(mutationCoordinatorRef.current, subject);
  }, [subject]);

  const loadGrants = useCallback(async () => {
    if (!isSubjectCurrent()) return;
    if (mutationCoordinatorRef.current.ownerId !== null) return;
    const generation = beginDelegationResourceRead(
      mutationCoordinatorRef.current,
      subject,
      "grants",
    );
    if (generation === null) return;
    setLoadStates((current) => ({ ...current, grants: "loading" })); setLoadErrors((current) => ({ ...current, grants: "" }));
    try { const next = await listDelegations(getToken, isSubjectCurrent); if (!isSubjectCurrent() || !isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "grants", generation)) return; setDelegations(next); setLoadStates((current) => ({ ...current, grants: "loaded" })); }
    catch (error) { if (isSubjectCurrent() && isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "grants", generation)) { setLoadStates((current) => ({ ...current, grants: "error" })); setLoadErrors((current) => ({ ...current, grants: presentApiError(error) })); } }
    finally { finishDelegationResourceRead(mutationCoordinatorRef.current, "grants", generation); }
  }, [getToken, isSubjectCurrent, subject]);
  const loadDocuments = useCallback(async () => {
    if (!isSubjectCurrent()) return;
    const generation = beginDelegationInventoryRead(
      mutationCoordinatorRef.current,
      subject,
      "documents",
    );
    if (generation === null) return;
    setLoadStates((current) => ({ ...current, documents: "loading" })); setLoadErrors((current) => ({ ...current, documents: "" }));
    try {
      const next = await listOwnedDocumentOptions(getToken, isSubjectCurrent); if (!isSubjectCurrent() || !isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "documents", generation)) return; setDocuments(next);
      setResourceId((current) => current && next.some((document) => document.id === current) ? current : next[0]?.id || "");
      setLoadStates((current) => ({ ...current, documents: "loaded" }));
    } catch (error) { if (isSubjectCurrent() && isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "documents", generation)) { setLoadStates((current) => ({ ...current, documents: "error" })); setLoadErrors((current) => ({ ...current, documents: presentApiError(error) })); } }
    finally { finishDelegationResourceRead(mutationCoordinatorRef.current, "documents", generation); }
  }, [getToken, isSubjectCurrent, subject]);
  const loadAudit = useCallback(async () => {
    if (!isSubjectCurrent()) return;
    const generation = beginDelegationInventoryRead(
      mutationCoordinatorRef.current,
      subject,
      "audit",
    );
    if (generation === null) return;
    setLoadStates((current) => ({ ...current, audit: "loading" })); setLoadErrors((current) => ({ ...current, audit: "" }));
    try { const next = await listDelegationAudit(getToken, isSubjectCurrent); if (!isSubjectCurrent() || !isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "audit", generation)) return; setAudit(next); setLoadStates((current) => ({ ...current, audit: "loaded" })); }
    catch (error) { if (isSubjectCurrent() && isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "audit", generation)) { setLoadStates((current) => ({ ...current, audit: "error" })); setLoadErrors((current) => ({ ...current, audit: presentApiError(error) })); } }
    finally { finishDelegationResourceRead(mutationCoordinatorRef.current, "audit", generation); }
  }, [getToken, isSubjectCurrent, subject]);
  const loadProposals = useCallback(async (preserveHistory = false) => {
    if (!isSubjectCurrent()) return;
    const generation = beginDelegationResourceRead(
      mutationCoordinatorRef.current,
      subject,
      "proposals",
    );
    if (generation === null) return;
    const preserveExisting = preserveHistory && proposalsRef.current.length > 0;
    setLoadStates((current) => ({ ...current, proposals: "loading" })); setLoadErrors((current) => ({ ...current, proposals: "" }));
    try {
      const next = await listAgentProposalsForSubject(getToken, isSubjectCurrent); if (!isSubjectCurrent() || !isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "proposals", generation)) return;
      if (preserveExisting) setProposals((current) => mergeProposalFirstPage(current, next.proposals));
      else { setProposals(next.proposals); setProposalCursor(next.nextCursor); deliveredCursorsRef.current = new Set(); }
      setLoadStates((current) => ({ ...current, proposals: "loaded" }));
    } catch (error) { if (isSubjectCurrent() && isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "proposals", generation)) { setLoadStates((current) => ({ ...current, proposals: "error" })); setLoadErrors((current) => ({ ...current, proposals: presentApiError(error) })); } }
    finally { finishDelegationResourceRead(mutationCoordinatorRef.current, "proposals", generation); }
  }, [getToken, isSubjectCurrent, subject]);

  useEffect(() => {
    void Promise.all([loadGrants(), loadDocuments(), loadAudit(), loadProposals()]);
  }, [loadAudit, loadDocuments, loadGrants, loadProposals]);

  const active = useMemo(() => delegations.filter((grant) => grant.status === "active"), [delegations]);
  const loading = Object.values(loadStates).some((state) => state === "loading");
  const directScopeAcknowledged = mode === "proposal" || allowVisibilityChange;
  const canCreate = name.trim().length > 1 && Boolean(expiry) && (resourceType === "owner" || Boolean(resourceId)) && directScopeAcknowledged;
  const beginMutation = (resource: DelegationMutationResource, busyOwner: string) => {
    const claim = claimDelegationMutation(
      mutationCoordinatorRef.current,
      subject,
      resource,
    );
    if (!claim) return null;
    setBusy(busyOwner);
    return claim;
  };
  const mutationIsCurrent = (claim: DelegationMutationClaim) =>
    isSubjectCurrent() &&
    isCurrentDelegationMutation(mutationCoordinatorRef.current, claim);
  const finishMutation = (claim: DelegationMutationClaim) => {
    if (
      releaseDelegationMutation(mutationCoordinatorRef.current, claim) &&
      isSubjectCurrent()
    ) {
      setBusy(null);
    }
  };

  async function create() {
    if (!canCreate || busy) return;
    if (!isSubjectCurrent()) return;
    const requestSubject = subject;
    const claim = beginMutation("grants", "create");
    if (!claim) return;
    setMessage("");
    setSecret(null);
    const requestIsCurrent = () => requestSubject === subject && mutationIsCurrent(claim);
    try {
      const normalizedName = name.trim();
      const expiresAt = new Date(expiry).toISOString();
      const scopes = mode === "proposal"
        ? ["documents:read", "inventory:read", "changes:read", "proposals:write"]
        : ["documents:read", "inventory:read", "changes:read", "documents:write"];
      const attempt = beginAttempt("grant:create", requestSubject, { operation: "create-agent-grant", name: normalizedName, mode, resourceType, resourceId: resourceType === "document" ? resourceId : null, expiresAt, scopes });
      const response = await createDelegation({
        name: normalizedName,
        mode,
        expiresAt,
        resourceType,
        resourceId: resourceType === "document" ? resourceId : null
      }, getToken, isSubjectCurrent, attempt.idempotencyKey);
      if (!requestIsCurrent()) return;
      mutationAttemptsRef.current.delete("grant:create");
      setDelegations((current) => upsertDelegation(current, response.delegation));
      setName("");
      if (response.recoveryRequired) {
        setSecret(null);
        setMessage("The one-time key cannot be recovered. Revoke and recreate this grant if you did not save it.");
      } else {
        setSecret({ value: response.key, copied: false });
        setMessage(`Created a bounded ${response.delegation.mode === "proposal" ? "proposal-only" : "direct"} grant for ${response.delegation.name}.`);
      }
    } catch (error) {
      if (!requestIsCurrent()) return;
      const attempt = mutationAttemptsRef.current.get("grant:create"); if (attempt) { const next = settleLogicalMutationAttempt(attempt, error); if (next) mutationAttemptsRef.current.set("grant:create", next); else mutationAttemptsRef.current.delete("grant:create"); }
      setMessage(mutationAttemptsRef.current.has("grant:create") ? "The grant may have been created. Retry the unchanged action to recover the same result. " + presentApiError(error) : presentApiError(error));
    } finally {
      if (requestIsCurrent()) finishMutation(claim);
    }
  }

  async function revoke(id: string) {
    if (busy || !window.confirm("Revoke this agent grant now? The action cannot be undone.")) return;
    if (!isSubjectCurrent()) return;
    const claim = beginMutation("grants", id);
    if (!claim) return;
    try {
      const requestSubject = subject;
      const slot = `grant:${id}`; const attempt = beginAttempt(slot, requestSubject, { operation: "revoke-agent-grant", grantId: id });
      await revokeDelegation(id, getToken, isSubjectCurrent, attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(slot);
      if (!mutationIsCurrent(claim)) return;
      setDelegations((current) => current.map((grant) => grant.id === id ? { ...grant, status: "revoked" } : grant));
      setMessage("Agent grant revoked.");
    } catch (error) {
      const slot = `grant:${id}`; const attempt = mutationAttemptsRef.current.get(slot); if (attempt) { const next = settleLogicalMutationAttempt(attempt, error); if (next) mutationAttemptsRef.current.set(slot, next); else mutationAttemptsRef.current.delete(slot); }
      if (mutationIsCurrent(claim)) setMessage(mutationAttemptsRef.current.has(slot) ? "The grant revocation may have completed. Retry the unchanged action to recover the same result. " + presentApiError(error) : presentApiError(error));
    } finally {
      finishMutation(claim);
    }
  }

  async function emergencyStop() {
    if (busy || active.length === 0 || !window.confirm(`Emergency revoke all ${active.length} active agent grants? This cannot be undone.`)) return;
    if (!isSubjectCurrent()) return;
    const claim = beginMutation("grants", "emergency");
    if (!claim) return;
    try {
      const requestSubject = subject;
      await emergencyStopDelegations(getToken, isSubjectCurrent, (id) => beginAttempt(`emergency:${id}`, requestSubject, { operation: "emergency-revoke-agent-grant", grantId: id }).idempotencyKey);
      active.forEach((grant) => mutationAttemptsRef.current.delete(`emergency:${grant.id}`));
      if (!mutationIsCurrent(claim)) return;
      setDelegations((current) => current.map((grant) => grant.status === "active" ? { ...grant, status: "revoked" } : grant));
      setMessage("Emergency stop completed. Every active grant was revoked.");
    } catch (error) {
      if (!retainLogicalMutationAttempt(error)) active.forEach((grant) => mutationAttemptsRef.current.delete(`emergency:${grant.id}`));
      if (mutationIsCurrent(claim)) setMessage(active.some((grant) => mutationAttemptsRef.current.has(`emergency:${grant.id}`)) ? "The emergency stop may have revoked some grants. Retry the unchanged action to recover each result, then refresh the inventory. " + presentApiError(error) : presentApiError(error));
    } finally {
      finishMutation(claim);
    }
  }

  async function copySecret() {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret.value);
      setSecret({ ...secret, copied: true });
    } catch {
      setMessage("Clipboard access was denied. Select and copy the credential manually.");
    }
  }

  async function decideProposal(proposal: AgentProposal, action: "accepted" | "rejected") {
    if (busy) return;
    if (action === "accepted" && !window.confirm("Accept this proposal and publish its candidate Markdown as a new canonical version?")) return;
    if (!isSubjectCurrent()) return;
    const claim = beginMutation("proposals", `proposal:${proposal.id}`);
    if (!claim) return;
    try {
      const requestSubject = subject;
      const slot = `proposal:${proposal.id}`; const attempt = beginAttempt(slot, requestSubject, { operation: "decide-agent-proposal", proposalId: proposal.id, action });
      const decided = await decideAgentProposal(proposal.id, action, getToken, isSubjectCurrent, attempt.idempotencyKey);
      mutationAttemptsRef.current.delete(slot);
      if (!mutationIsCurrent(claim)) return;
      setProposals((current) => current.map((item) => item.id === decided.id ? decided : item));
      setMessage(action === "accepted" ? "Proposal accepted and published as a new canonical version." : "Proposal rejected. No canonical document was changed.");
    } catch (error) {
      const slot = `proposal:${proposal.id}`; const attempt = mutationAttemptsRef.current.get(slot); if (attempt) { const next = settleLogicalMutationAttempt(attempt, error); if (next) mutationAttemptsRef.current.set(slot, next); else mutationAttemptsRef.current.delete(slot); }
      if (mutationIsCurrent(claim)) setMessage(error instanceof ApiRequestError && error.status === 412
        ? "This proposal is stale because its base document changed. No canonical document was changed. Review the latest Markdown before requesting a fresh proposal."
        : mutationAttemptsRef.current.has(slot) ? "The proposal decision may have completed. Retry the unchanged decision to recover the same result. " + presentApiError(error) : presentApiError(error));
    } finally {
      finishMutation(claim);
    }
  }

  async function loadProposalComparison(proposal: AgentProposal) {
    if (busy || proposalBases[proposal.id] || !isSubjectCurrent()) return;
    const requestSubject = subject;
    const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent();
    if (!requestIsCurrent()) return;
    setBusy(`compare:${proposal.id}`);
    try {
      const committed = await commitProposalBaseMarkdownIfCurrent(loadProposalBaseMarkdown(proposal, getToken, requestIsCurrent), requestIsCurrent, (baseMarkdown) => setProposalBases((current) => ({ ...current, [proposal.id]: baseMarkdown })));
      if (!committed) return;
    } catch (error) {
      if (!requestIsCurrent()) return;
      setMessage(presentApiError(error));
    } finally {
      if (requestIsCurrent()) setBusy(null);
    }
  }

  async function loadOlderProposals() {
    if (loading || busy || !proposalCursor || moreInFlightRef.current) return;
    const cursor = proposalCursor;
    if (deliveredCursorsRef.current.has(cursor)) {
      setProposalCursor(null);
      setMessage("The proposal inventory returned a cursor that did not advance. Loaded proposals remain available.");
      return;
    }
    if (!isSubjectCurrent()) return;
    moreInFlightRef.current = true;
    setBusy("proposals:more");
    try {
      const page = await listAgentProposalsForSubject(
        getToken,
        isSubjectCurrent,
        cursor,
      );
      if (!isSubjectCurrent()) return;
      const delivered = new Set(deliveredCursorsRef.current);
      delivered.add(cursor);
      deliveredCursorsRef.current = delivered;
      const next = appendPrivateCursorPage(
        proposalsRef.current,
        { items: page.proposals, nextCursor: page.nextCursor },
        cursor,
        delivered,
      );
      setProposals(next.items);
      setProposalCursor(next.nextCursor);
      if (next.cursorDidNotProgress) {
        setMessage("The proposal inventory returned a cursor that did not advance. Loaded proposals remain available.");
        return;
      }
    } catch (error) {
      if (isSubjectCurrent()) setMessage(presentApiError(error));
    } finally {
      moreInFlightRef.current = false;
      if (isSubjectCurrent()) setBusy(null);
    }
  }

  return <div className="grid gap-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(20rem,.95fr)]">
    <div className="space-y-6">
      <section aria-labelledby="new-agent-title" className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6">
        <div className="flex items-start gap-3"><KeyRound className="mt-0.5 size-5 text-acid" aria-hidden /><div><h2 id="new-agent-title" className="text-lg font-semibold text-white">Create a bounded agent grant</h2><p className="mt-1 text-sm leading-6 text-mist">Name the agent, bind it to one document or your owned inventory, then choose whether changes become proposals or direct updates.</p></div></div>
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <Field label="Agent name"><Input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} placeholder="Profile steward" /></Field>
          <Field label="Mode"><select value={mode} onChange={(event) => setMode(event.target.value as DelegationMode)} className={selectClass}><option value="proposal">Proposal only (recommended)</option><option value="direct">Direct canonical updates</option></select></Field>
          <Field label="Resource boundary"><select value={resourceType} onChange={(event) => setResourceType(event.target.value as "owner" | "document")} className={selectClass}><option value="document">One document</option><option value="owner">All owned documents</option></select></Field>
          {resourceType === "document" ? <Field label="Document"><select value={resourceId} onChange={(event) => setResourceId(event.target.value)} className={selectClass} disabled={loadStates.documents !== "loaded" || documents.length === 0}><option value="">{loadStates.documents === "loading" ? "Loading documents…" : loadStates.documents === "error" ? "Documents unavailable" : "Select a document"}</option>{documents.map((document) => <option key={document.id} value={document.id}>{document.kind} · {document.identifier} · v{document.version}</option>)}</select></Field> : <div className="rounded-xl border border-amber-300/20 bg-amber-300/[.06] p-3 text-xs leading-5 text-amber-100">Owner-wide grants cover current and future documents. Prefer one-document access.</div>}
          <Field label="Expires"><Input type="datetime-local" value={expiry} min={minimumExpiry()} onChange={(event) => setExpiry(event.target.value)} /></Field>
          {mode === "direct" && <label className="flex items-start gap-3 rounded-xl border border-white/10 bg-black/15 p-3 text-sm text-white"><input type="checkbox" checked={allowVisibilityChange} onChange={(event) => setAllowVisibilityChange(event.target.checked)} className="mt-0.5 size-4 accent-[#d8ff72]" /><span><span className="font-semibold">Acknowledge full-document authority</span><span className="mt-1 block text-xs leading-5 text-mist">Direct document-write grants can change every canonical field, including visibility. Use proposal mode when you need human review.</span></span></label>}
        </div>
        {loadStates.documents === "error" && <PrivateLoadFailure label="Owned documents could not be loaded" error={loadErrors.documents} onRetry={() => void loadDocuments()} />}
        {!directScopeAcknowledged && <p role="alert" className="mt-4 text-sm text-amber-100">Direct mode grants full-document authority. Acknowledge that scope or choose proposal mode.</p>}
        <Button className="mt-6" disabled={!canCreate || busy !== null} onClick={() => void create()}>{busy === "create" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Bot className="size-4" aria-hidden />} Create agent grant</Button>
        {secret && <div className="mt-5 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4"><p className="text-xs font-semibold uppercase tracking-wide text-amber-100">Copy now · shown once</p><code className="mt-2 block select-all break-all text-sm text-white">{secret.value}</code><Button variant="ghost" className="mt-2 px-3" onClick={() => void copySecret()}>{secret.copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}{secret.copied ? "Copied" : "Copy credential"}</Button></div>}
        {message && <p role="status" className="mt-4 text-sm text-mist">{message}</p>}
      </section>

      <AgentGrantInventoryPanel
        active={active}
        busy={busy}
        delegations={delegations}
        loadError={loadErrors.grants}
        loadState={loadStates.grants}
        onEmergencyStop={() => void emergencyStop()}
        onRetry={() => void loadGrants()}
        onRevoke={(id) => void revoke(id)}
      />
      <AgentProposalReviewPanel
        busy={busy}
        loading={loading}
        onCompare={(proposal) => void loadProposalComparison(proposal)}
        onDecide={(proposal, action) => void decideProposal(proposal, action)}
        onLoadOlder={() => void loadOlderProposals()}
        onRetry={() => void loadProposals(true)}
        proposalBases={proposalBases}
        proposalCursor={proposalCursor}
        proposals={proposals}
        loadError={loadErrors.proposals}
        loadState={loadStates.proposals}
      />
    </div>

    <aside className="space-y-6">
      <section className="rounded-[1.5rem] border border-acid/20 bg-acid/[.06] p-5"><h2 className="inline-flex items-center gap-2 font-semibold text-white"><ShieldCheck className="size-5 text-acid" aria-hidden /> Delegation contract</h2><ul className="mt-4 space-y-3 text-sm leading-6 text-mist"><li className="flex gap-2"><Bot className="mt-1 size-4 shrink-0 text-acid" aria-hidden />A grant is bound to one document or your owned inventory. Direct mode has full-document authority.</li><li className="flex gap-2"><Eye className="mt-1 size-4 shrink-0 text-acid" aria-hidden />Proposal mode keeps canonical publication under explicit owner review.</li><li className="flex gap-2"><Octagon className="mt-1 size-4 shrink-0 text-acid" aria-hidden />Emergency stop revokes every active grant rather than relying on a soft pause.</li></ul></section>
      <section aria-labelledby="audit-title" className="rounded-[1.5rem] border border-white/10 bg-panel p-5"><h2 id="audit-title" className="inline-flex items-center gap-2 font-semibold text-white"><Activity className="size-5 text-acid" aria-hidden /> Recent change record</h2>{loadStates.audit === "loading" && audit.length === 0 ? <p role="status" className="mt-4 text-sm text-mist">Loading recent change records…</p> : loadStates.audit === "error" && audit.length === 0 ? <PrivateLoadFailure label="Recent change records could not be loaded" error={loadErrors.audit} onRetry={() => void loadAudit()} /> : loadStates.audit === "loaded" && audit.length === 0 ? <p className="mt-4 text-sm leading-6 text-mist">No change events were returned.</p> : <ol className="mt-4 space-y-4">{audit.map((event) => <li key={event.id} className="border-l border-white/15 pl-4"><p className="text-sm font-semibold text-white">{event.agentName} · {event.action}</p><p className="mt-1 text-xs leading-5 text-mist">{event.documentIdentifier ? `${event.documentIdentifier} · ` : ""}{event.outcome} · <time dateTime={event.createdAt}>{formatTime(event.createdAt)}</time></p></li>)}</ol>}{loadStates.audit === "error" && audit.length > 0 && <PrivateLoadFailure label="Recent change records could not be refreshed" error={loadErrors.audit} onRetry={() => void loadAudit()} />}</section>
    </aside>
  </div>;
}

function SignedOutState({ configured, loading }: { configured: boolean; loading: boolean }) { return <div className="rounded-3xl border border-white/10 bg-panel p-8 text-center"><Bot className="mx-auto size-7 text-acid" aria-hidden /><h2 className="mt-4 text-xl font-semibold text-white">Sign in to manage agents</h2><AsyncBoundaryMessage className="mt-2 text-sm text-mist" loading={loading}>{loading ? "Loading your account." : configured ? "Agent grants are private to the owning account." : "Clerk configuration is required for agent management."}</AsyncBoundaryMessage></div>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block"><span className="mb-1.5 block text-sm font-medium text-white">{label}</span>{children}</label>; }

const selectClass = "w-full rounded-xl border border-white/12 bg-black/25 px-3.5 py-3 text-sm text-white outline-none focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:opacity-50";
function defaultExpiry() { const date = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000); return localDateTime(date); }
function minimumExpiry() { return localDateTime(new Date(Date.now() + 5 * 60 * 1000)); }
function localDateTime(date: Date) { const shifted = new Date(date.valueOf() - date.getTimezoneOffset() * 60_000); return shifted.toISOString().slice(0, 16); }
