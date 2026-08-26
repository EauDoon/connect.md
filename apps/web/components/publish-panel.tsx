"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, LoaderCircle, LockKeyhole } from "lucide-react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { presentSaveError, saveDocument, type SearchIndexingState } from "@/lib/api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { hasValidationErrors, type ValidationIssue } from "@/lib/validation";
import { discardedSuccessfulSaveMessage, priorAccountSuccessfulSaveMessage, reconcileSaveResponse, savedDocumentIdentity, uncertainSaveOutcomeMessage, type SaveSnapshot } from "@/lib/save-reconciliation";

export function PublishPanel({ issues }: { issues: ValidationIssue[] }) {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const { kind, markdown, masked: draftMasked, savedDocument, hydrateSavedDocument, recordSavedDocument, getDraftSnapshot } = useDraft();
  const [status, setStatus] = useState<"idle" | "publishing" | "success" | "error">("idle");
  const [message, setMessage] = useState("");
  const [searchIndexingState, setSearchIndexingState] = useState<SearchIndexingState | null>(null);
  const mountedRef = useRef(true);
  const controllerRef = useRef<AbortController | null>(null);
  const saveAttemptRef = useRef<LogicalMutationAttempt | null>(null);
  const requestSubjectRef = useRef<string | null>(null);
  const requestKindRef = useRef<typeof kind | null>(null);
  const authIdentity = configured && isLoaded && isSignedIn && subject ? subject : null;
  const authIdentityRef = useRef(authIdentity);
  authIdentityRef.current = authIdentity;
  const dirty = savedDocument ? savedDocument.markdown !== markdown : true;
  const blocked = draftMasked || hasValidationErrors(issues) || !authIdentity || status === "publishing" || !dirty;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller || requestSubjectRef.current === authIdentity) return;
    controller.abort();
    controllerRef.current = null;
    saveAttemptRef.current = null;
    requestSubjectRef.current = null;
    requestKindRef.current = null;
    setStatus("idle");
    setSearchIndexingState(null);
    setMessage(uncertainSaveOutcomeMessage());
  }, [authIdentity]);

  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller || requestKindRef.current === kind) return;
    controller.abort();
    controllerRef.current = null;
    saveAttemptRef.current = null;
    requestSubjectRef.current = null;
    requestKindRef.current = null;
    setStatus("idle");
    setSearchIndexingState(null);
    setMessage(uncertainSaveOutcomeMessage());
  }, [kind]);

  async function save() {
    if (!authIdentity || draftMasked || status === "publishing" || controllerRef.current) return;
    const controller = new AbortController();
    const draftAtClick = getDraftSnapshot();
    if (!draftAtClick) return;
    controllerRef.current = controller;
    const requestSubject = authIdentity;
    requestSubjectRef.current = requestSubject;
    requestKindRef.current = draftAtClick.kind;
    const saveAttempt = beginLogicalMutationAttempt(saveAttemptRef.current, requestSubject, {
      operation: "save-document",
      kind: draftAtClick.kind,
      identifier: draftAtClick.identifier,
      existingIdentity: savedDocumentIdentity(draftAtClick.savedDocument),
      markdown: draftAtClick.markdown,
    });
    saveAttemptRef.current = saveAttempt;
    const snapshot: SaveSnapshot = {
      subject: requestSubject,
      kind: draftAtClick.kind,
      revision: draftAtClick.revision,
      lineage: draftAtClick.lineage,
      identifier: draftAtClick.identifier,
      markdown: draftAtClick.markdown,
      existingIdentity: savedDocumentIdentity(draftAtClick.savedDocument)
    };
    setStatus("publishing");
    setSearchIndexingState(null);
    setMessage(`${draftAtClick.savedDocument ? "Updating" : "Creating"} the canonical ${draftAtClick.kind} in connect.md.`);
    try {
      const response = await saveDocument(draftAtClick.kind, draftAtClick.markdown, getToken, () => authIdentityRef.current === snapshot.subject, draftAtClick.savedDocument, controller.signal, saveAttempt.idempotencyKey);
      saveAttemptRef.current = null;
      if (!mountedRef.current || controller.signal.aborted) return;
      const currentDraft = getDraftSnapshot();
      const sameSubject = authIdentityRef.current === snapshot.subject;
      if (!currentDraft) {
        setStatus("idle");
        setSearchIndexingState(null);
        setMessage(priorAccountSuccessfulSaveMessage());
        return;
      }
      const reconciliation = reconcileSaveResponse(snapshot, {
        subject: authIdentityRef.current,
        kind: currentDraft.kind,
        revision: currentDraft.revision,
        lineage: currentDraft.lineage,
        identifier: currentDraft.identifier,
        markdown: currentDraft.markdown,
        existing: currentDraft.savedDocument
      }, response);
      if (reconciliation.disposition === "discard") {
        setStatus("idle");
        setSearchIndexingState(sameSubject ? response.searchIndexing : null);
        setMessage(sameSubject ? discardedSuccessfulSaveMessage(response) : priorAccountSuccessfulSaveMessage());
        return;
      }
      setSearchIndexingState(response.searchIndexing);
      if (reconciliation.disposition === "preserve") recordSavedDocument(response, reconciliation.markdown);
      else hydrateSavedDocument(response);
      setStatus("success");
      setMessage(reconciliation.disposition === "preserve"
        ? `Saved canonical ${response.kind} version ${response.version}; newer local edits remain unsaved.`
        : `Saved canonical ${response.kind} version ${response.version}.`);
    } catch (error) {
      if (!mountedRef.current || controller.signal.aborted) return;
      saveAttemptRef.current = settleLogicalMutationAttempt(saveAttempt, error);
      setStatus("error");
      setSearchIndexingState(null);
      setMessage(saveAttemptRef.current ? `${uncertainSaveOutcomeMessage()} Retry the unchanged draft to recover the same idempotent result.` : presentSaveError(error));
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        requestSubjectRef.current = null;
        requestKindRef.current = null;
      }
    }
  }

  const authMessage = !configured
    ? "Clerk configuration is required before this deployment can publish."
    : !isLoaded
      ? "Waiting for Clerk authentication before saving."
      : draftMasked
        ? "Waiting for the account transition before saving."
      : !isSignedIn || !subject
        ? "Sign in with Clerk before saving."
      : hasValidationErrors(issues)
        ? "Resolve client preflight errors before asking the API to validate and save."
        : savedDocument && !dirty
          ? `Version ${savedDocument.version} is saved with ${savedDocument.visibility} visibility.`
          : "Saving is explicit. Public visibility only takes effect after the API accepts this draft.";

  const publicDocument = savedDocument?.visibility === "public"
    ? { href: savedDocument.kind === "profile" ? `/p/${savedDocument.identifier}` : `/r/${savedDocument.identifier}`, label: savedDocument.kind }
    : null;
  const showStatus = message && (status !== "success" || !dirty);
  const searchIndexingMessage = searchIndexingNotice(searchIndexingState);

  return (
    <section aria-labelledby="save-title" className="rounded-2xl border border-acid/20 bg-acid/[.06] p-4">
      <div className="flex gap-3">
        <LockKeyhole className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden />
        <div className="min-w-0">
          <h2 id="save-title" className="text-sm font-semibold text-white">Save gate</h2>
          <p className="mt-1 text-sm leading-5 text-mist">{authMessage}</p>
          {savedDocument && <p className="mt-2 text-xs text-mist/75">Document {savedDocument.identifier} · version {savedDocument.version}{dirty ? " · unsaved changes" : ""}</p>}
          <Button className="mt-4 w-full sm:w-auto" onClick={() => void save()} disabled={blocked} aria-describedby={showStatus ? "save-status" : undefined}>
            {status === "publishing" && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
            {savedDocument ? dirty ? "Save changes" : "Saved" : `Save ${kind}`}
          </Button>
          {showStatus && <p id="save-status" role="status" className={`mt-3 text-sm ${status === "error" ? "text-red-200" : "text-mist"}`}>{message}</p>}
          {searchIndexingMessage && <p role="status" className="mt-3 text-sm text-amber-100">{searchIndexingMessage}</p>}
          {publicDocument && <Link href={publicDocument.href} className="mt-3 inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-acid underline-offset-4 hover:underline"><CheckCircle2 className="size-4" /> View canonical public {publicDocument.label}</Link>}
        </div>
      </div>
    </section>
  );
}

export function searchIndexingNotice(state: SearchIndexingState | null) {
  if (state === "queued") return "Canonical save succeeded; search indexing is pending.";
  if (state === "degraded") return "Canonical save succeeded; search indexing is temporarily degraded.";
  if (state === "unknown") return "Canonical save succeeded; search indexing status was not reported.";
  return null;
}
