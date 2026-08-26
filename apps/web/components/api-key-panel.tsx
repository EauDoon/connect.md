"use client";

import { Check, Copy, KeyRound, LoaderCircle, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { AGENT_SCOPES, type AgentScope, type ApiKeyCreateResult, type ApiKeyRecord, type TokenGetter, createApiKey, listApiKeys, presentApiKeyError, revokeApiKey } from "@/lib/api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";

type KeyListState = { subject: string; records: ApiKeyRecord[]; loading: boolean; error: string };
type SecretState = { subject: string; key: string; copied: boolean } | null;

export type ApiKeyMutationClaim = { id: number; scope: string; generation: number };
export type ApiKeyMutationCoordinator = { scope: string; generation: number; nextOwnerId: number; ownerId: number | null };
export type ApiKeyCreationOutcome = { secret: SecretState; recoveryNotice: string };

function apiKeyScope(subject: string): string {
  return `${subject.length}:${subject}`;
}

export function createApiKeyMutationCoordinator(subject: string): ApiKeyMutationCoordinator {
  return { scope: apiKeyScope(subject), generation: 0, nextOwnerId: 0, ownerId: null };
}

export function resetApiKeyMutationCoordinator(coordinator: ApiKeyMutationCoordinator, subject: string): void {
  coordinator.scope = apiKeyScope(subject);
  coordinator.generation += 1;
  coordinator.ownerId = null;
}

export function claimApiKeyMutation(coordinator: ApiKeyMutationCoordinator, subject: string): ApiKeyMutationClaim | null {
  if (coordinator.scope !== apiKeyScope(subject) || coordinator.ownerId !== null) return null;
  const id = coordinator.nextOwnerId + 1;
  coordinator.nextOwnerId = id;
  coordinator.ownerId = id;
  return { id, scope: coordinator.scope, generation: coordinator.generation };
}

export function isCurrentApiKeyMutation(coordinator: ApiKeyMutationCoordinator, claim: ApiKeyMutationClaim): boolean {
  return coordinator.scope === claim.scope && coordinator.generation === claim.generation && coordinator.ownerId === claim.id;
}

export function releaseApiKeyMutation(coordinator: ApiKeyMutationCoordinator, claim: ApiKeyMutationClaim): boolean {
  if (!isCurrentApiKeyMutation(coordinator, claim)) return false;
  coordinator.ownerId = null;
  return true;
}

export function projectApiKeyCreationOutcome(subject: string, created: ApiKeyCreateResult): ApiKeyCreationOutcome {
  if (created.recovery_required) {
    return {
      secret: null,
      recoveryNotice: `The one-time secret for ${created.prefix}… was not recovered. Revoke this key and create a replacement.`
    };
  }
  return { secret: { subject, key: created.key, copied: false }, recoveryNotice: "" };
}

export function ApiKeyPanel() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  const isSubjectCurrent = useCallback(() => subjectRef.current === subject, [subject]);
  if (!configured || !isLoaded || !isSignedIn || !subject) return null;
  return <AuthenticatedApiKeyPanel key={subject} subject={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} />;
}

function AuthenticatedApiKeyPanel({ subject, getToken, isSubjectCurrent }: { subject: string; getToken: TokenGetter; isSubjectCurrent: () => boolean }) {
  const [selectedScopes, setSelectedScopes] = useState<AgentScope[]>([...AGENT_SCOPES]);
  const [listState, setListState] = useState<KeyListState | null>(null);
  const [secret, setSecret] = useState<SecretState>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [recoveryNotice, setRecoveryNotice] = useState("");
  const createAttemptRef = useRef<LogicalMutationAttempt | null>(null);
  const revokeAttemptsRef = useRef<Map<string, LogicalMutationAttempt>>(new Map());
  const mutationCoordinatorRef = useRef(createApiKeyMutationCoordinator(subject));
  const currentList = listState?.subject === subject ? listState : null;
  const currentSecret = secret?.subject === subject ? secret : null;

  useEffect(() => {
    resetApiKeyMutationCoordinator(mutationCoordinatorRef.current, subject);
  }, [subject]);

  useEffect(() => {
    let active = true;
    const owner = subject;
    const isListSubjectCurrent = () => active && isSubjectCurrent();
    void (async () => {
      try {
        const records = await listApiKeys(getToken, isListSubjectCurrent);
        if (isListSubjectCurrent()) setListState({ subject: owner, records, loading: false, error: "" });
      } catch (error) {
        if (isListSubjectCurrent()) setListState({ subject: owner, records: [], loading: false, error: presentApiKeyError(error, "list") });
      }
    })();
    return () => { active = false; };
  }, [getToken, isSubjectCurrent, subject]);

  const ownerSubject = subject;

  function claimMutation() {
    return claimApiKeyMutation(mutationCoordinatorRef.current, ownerSubject);
  }

  function mutationIsCurrent(claim: ApiKeyMutationClaim) {
    return isSubjectCurrent() && isCurrentApiKeyMutation(mutationCoordinatorRef.current, claim);
  }

  function releaseMutation(claim: ApiKeyMutationClaim) {
    if (releaseApiKeyMutation(mutationCoordinatorRef.current, claim) && isSubjectCurrent()) setBusy(null);
  }

  function toggleScope(scope: AgentScope) {
    setSelectedScopes((current) => current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope]);
  }

  async function create() {
    if (!isSubjectCurrent()) return;
    const normalizedScopes = [...new Set(selectedScopes)].sort();
    const claim = claimMutation();
    if (!claim) return;
    const attempt = beginLogicalMutationAttempt(
      createAttemptRef.current,
      ownerSubject,
      { kind: "api-key-create", scopes: normalizedScopes },
    );
    createAttemptRef.current = attempt;
    setBusy("create");
    setRecoveryNotice("");
    try {
      const created = await createApiKey(normalizedScopes, attempt.idempotencyKey, getToken, isSubjectCurrent);
      if (!mutationIsCurrent(claim)) return;
      createAttemptRef.current = null;
      const outcome = projectApiKeyCreationOutcome(ownerSubject, created);
      setSecret(outcome.secret);
      setRecoveryNotice(outcome.recoveryNotice);
      const metadata = { ...created, revoked: false, last_used_at: null } satisfies ApiKeyRecord;
      setListState((current) => ({
        subject: ownerSubject,
        loading: false,
        error: "",
        records: [metadata, ...(current?.subject === ownerSubject ? current.records.filter((record) => record.id !== created.id) : [])]
      }));
    } catch (error) {
      if (!mutationIsCurrent(claim)) return;
      createAttemptRef.current = settleLogicalMutationAttempt(attempt, error);
      setListState((current) => ({ subject: ownerSubject, records: current?.subject === ownerSubject ? current.records : [], loading: false, error: presentApiKeyError(error, "create") }));
    } finally {
      releaseMutation(claim);
    }
  }

  async function revoke(id: string) {
    if (!isSubjectCurrent()) return;
    const claim = claimMutation();
    if (!claim) return;
    const attempt = beginLogicalMutationAttempt(
      revokeAttemptsRef.current.get(id) ?? null,
      ownerSubject,
      { kind: "api-key-revoke", id },
    );
    revokeAttemptsRef.current.set(id, attempt);
    setBusy(id);
    setRecoveryNotice("");
    try {
      await revokeApiKey(id, attempt.idempotencyKey, getToken, isSubjectCurrent);
      if (!mutationIsCurrent(claim)) return;
      revokeAttemptsRef.current.delete(id);
      setListState((current) => current?.subject === ownerSubject
        ? { ...current, error: "", records: current.records.map((record) => record.id === id ? { ...record, revoked: true } : record) }
        : current);
    } catch (error) {
      if (!mutationIsCurrent(claim)) return;
      const retained = settleLogicalMutationAttempt(attempt, error);
      if (retained) revokeAttemptsRef.current.set(id, retained);
      else revokeAttemptsRef.current.delete(id);
      setListState((current) => ({ subject: ownerSubject, records: current?.subject === ownerSubject ? current.records : [], loading: false, error: presentApiKeyError(error, "revoke") }));
    } finally {
      releaseMutation(claim);
    }
  }

  async function copySecret() {
    if (!currentSecret) return;
    try {
      await navigator.clipboard.writeText(currentSecret.key);
      setSecret({ ...currentSecret, copied: true });
    } catch {
      setListState((current) => ({ subject: ownerSubject, records: current?.subject === ownerSubject ? current.records : [], loading: false, error: "Clipboard access was denied. Select and copy the key manually." }));
    }
  }

  return (
    <section aria-labelledby="agent-keys-title" className="rounded-2xl border border-white/10 bg-black/15 p-4">
      <div className="flex items-start gap-3">
        <KeyRound className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 id="agent-keys-title" className="text-sm font-semibold text-white">Agent API keys</h2>
          <p className="mt-1 text-sm leading-5 text-mist">Create scoped credentials for agents. A new secret is shown only once.</p>
          <fieldset className="mt-4">
            <legend className="text-xs font-semibold uppercase tracking-wide text-mist">Scopes</legend>
            <div className="mt-2 flex flex-wrap gap-3">
              {AGENT_SCOPES.map((scope) => <label key={scope} className="inline-flex items-center gap-2 text-sm text-white"><input type="checkbox" checked={selectedScopes.includes(scope)} onChange={() => toggleScope(scope)} className="size-4 accent-[#d8ff72]" />{scope}</label>)}
            </div>
          </fieldset>
          <Button variant="secondary" className="mt-4" disabled={selectedScopes.length === 0 || busy !== null} onClick={() => void create()}>
            {busy === "create" && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Create API key
          </Button>

          {currentSecret && <div className="mt-4 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-amber-100">Copy now — this secret cannot be retrieved again</p>
            <code className="mt-2 block break-all select-all text-sm text-white">{currentSecret.key}</code>
            <Button variant="ghost" className="mt-2 px-3" onClick={() => void copySecret()}>{currentSecret.copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}{currentSecret.copied ? "Copied" : "Copy secret"}</Button>
          </div>}

          {recoveryNotice && <p role="status" className="mt-3 text-sm text-amber-100">{recoveryNotice}</p>}
          {currentList?.error && <p role="alert" className="mt-3 text-sm text-red-200">{currentList.error}</p>}
          {!currentList && <p role="status" className="mt-4 inline-flex items-center gap-2 text-sm text-mist"><LoaderCircle className="size-4 animate-spin" aria-hidden /> Loading keys</p>}
          {currentList && currentList.records.length === 0 && !currentList.error && <p className="mt-4 text-sm text-mist">No API keys yet.</p>}
          {currentList && currentList.records.length > 0 && <ul className="mt-4 space-y-2" aria-label="Agent API keys">
            {currentList.records.map((record) => <li key={record.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 px-3 py-3">
              <div className="min-w-0">
                <p className="font-mono text-sm text-white">{record.prefix}…</p>
                <p className="mt-1 break-words text-xs text-mist">{record.scopes.join(" · ")}{record.revoked ? " · revoked" : ""}</p>
              </div>
              {!record.revoked && <Button variant="danger" className="min-h-11 px-3" disabled={busy !== null} onClick={() => void revoke(record.id)}>{busy === record.id ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <X className="size-4" aria-hidden />} Revoke</Button>}
            </li>)}
          </ul>}
        </div>
      </div>
    </section>
  );
}
