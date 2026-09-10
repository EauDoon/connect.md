"use client";

import React, { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { PROFILE_RESUME_MAX_UTF8_BYTES, documentIdentifier, profileStarter, type DocumentKind, type HumanFields, normaliseMarkdown, starterFor, switchDocumentKind } from "@/lib/markdown";
import { type DocumentResponse } from "@/lib/api";
import { maskOwnedDraftSnapshot, requiresDraftReset, resolvedDraftSubject } from "@/lib/draft-security";
import { type HumanJourneyStage } from "@/lib/human-journey";
import { renameCheckpoint as renameCheckpointEntry, createCheckpoint, type DraftCheckpoint } from "@/lib/draft-checkpoints";
import { type RecoveryBundle } from "@/lib/session-recovery";

type EditorLayout = "split" | "source" | "preview";
type EditorInterface = "code" | "plain";
type DraftState = {
  editorLayout: EditorLayout;
  editorInterface: EditorInterface;
  setEditorLayout: (layout: EditorLayout) => void;
  setEditorInterface: (value: EditorInterface) => void;
  sourceLineRequest: number | null;
  requestSourceLine: (line: number | null) => void;
  previousDraft: { kind: DocumentKind; markdown: string } | null;
  undoReplacement: () => void;
  discardUndo: () => void;
  restoreRecovery: (bundle: RecoveryBundle) => void;
  checkpoints: DraftCheckpoint[];
  saveCheckpoint: (label: string) => void;
  renameCheckpoint: (id: number, label: string) => void;
  restoreCheckpoint: (id: number) => void;
  removeCheckpoint: (id: number) => void;
  kind: DocumentKind;
  markdown: string;
  savedDocument: DocumentResponse | null;
  revision: number;
  lineage: number;
  masked: boolean;
  humanStage: HumanJourneyStage;
  guidedReferenceChoices: GuidedReferenceChoices;
  localDownloadReceipt: LocalDownloadReceipt | null;
  setMarkdown: (markdown: string) => void;
  replaceMarkdown: (markdown: string) => void;
  replaceDraft: (kind: DocumentKind, markdown: string) => void;
  setKind: (kind: DocumentKind) => void;
  setHumanStage: (stage: HumanJourneyStage) => void;
  setGuidedReferenceChoices: (choices: Partial<GuidedReferenceChoices>) => void;
  hydrateSavedDocument: (document: DocumentResponse) => void;
  recordSavedDocument: (document: DocumentResponse, rebasedMarkdown: string) => void;
  recordLocalDownload: (filename: string) => void;
  getDraftSnapshot: () => { kind: DocumentKind; markdown: string; revision: number; lineage: number; checkpointGeneration: number; identifier: string; savedDocument: DocumentResponse | null } | null;
};

export type GuidedReferenceChoices = Pick<HumanFields, "languageProficiency" | "organizationRelationship">;

const defaultGuidedReferenceChoices: GuidedReferenceChoices = {
  languageProficiency: "",
  organizationRelationship: "current_employer",
};

export type LocalDownloadReceipt = {
  filename: string;
  kind: DocumentKind;
  markdown: string;
};

const DraftContext = createContext<DraftState | null>(null);

export function draftAuthBoundaryKey(configured: boolean, isLoaded: boolean, subject: string | null) {
  if (!configured) return "unconfigured";
  if (!isLoaded) return "loading";
  return subject ? `user:${subject}` : "signed-out";
}

export function DraftProvider({ children }: { children: ReactNode }) {
  const { configured, isLoaded, subject } = useConnectmdAuth();
  const [editorLayout, updateEditorLayout] = useState<EditorLayout>("split");
  const [editorInterface, updateEditorInterface] = useState<EditorInterface>("code");
  const [sourceLineRequest, setSourceLineRequest] = useState<number | null>(null);
  const [previousDraft, setPreviousDraft] = useState<{ kind: DocumentKind; markdown: string } | null>(null);
  const previousDraftRef = useRef(previousDraft);
  const [kind, updateKind] = useState<DocumentKind>("profile");
  const [markdown, updateMarkdown] = useState(profileStarter);
  const [savedDocument, setSavedDocument] = useState<DocumentResponse | null>(null);
  const [revision, setRevision] = useState(0);
  const [lineage, setLineage] = useState(0);
  const [humanStage, updateHumanStage] = useState<HumanJourneyStage>("foundation");
  const [guidedReferenceChoices, updateGuidedReferenceChoices] = useState(defaultGuidedReferenceChoices);
  const [localDownloadReceipt, setLocalDownloadReceipt] = useState<LocalDownloadReceipt | null>(null);
  const [draftOwner, setDraftOwner] = useState<string | null>(null);
  const [checkpoints, setCheckpoints] = useState<DraftCheckpoint[]>([]);
  const checkpointsRef = useRef<DraftCheckpoint[]>([]);
  const checkpointGenerationRef = useRef(0);
  const checkpointIdRef = useRef(0);
  const kindRef = useRef(kind);
  const markdownRef = useRef(markdown);
  const revisionRef = useRef(revision);
  const lineageRef = useRef(0);
  const savedDocumentRef = useRef(savedDocument);
  const resolvedSubject = resolvedDraftSubject(configured, isLoaded, subject);
  const authBoundary = draftAuthBoundaryKey(configured, isLoaded, subject);
  const maskDraft = draftOwner !== null && authBoundary !== draftOwner;
  const maskDraftRef = useRef(maskDraft);
  maskDraftRef.current = maskDraft;
  const previousSourceNeedsBackup = previousDraft !== null && previousDraft.markdown !== starterFor(previousDraft.kind);
  const unsavedDraft = !maskDraft && (previousSourceNeedsBackup || checkpoints.length > 0 || (markdown !== (savedDocument?.markdown ?? starterFor(kind))
    && (localDownloadReceipt?.kind !== kind || localDownloadReceipt.markdown !== markdown)));

  useEffect(() => {
    if (!unsavedDraft) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsavedDraft]);

  useEffect(() => {
    if (!resolvedSubject) return;
    if (draftOwner === null) {
      setDraftOwner(resolvedSubject);
      return;
    }
    if (!requiresDraftReset(draftOwner, resolvedSubject)) return;
    previousDraftRef.current = null;
    setPreviousDraft(null);
    setSourceLineRequest(null);
    updateEditorLayout("split");
    updateEditorInterface("code");
    checkpointsRef.current = [];
    checkpointGenerationRef.current += 1;
    setCheckpoints([]);
    updateKind("profile");
    updateMarkdown(profileStarter);
    setSavedDocument(null);
    setLocalDownloadReceipt(null);
    setRevision((current) => current + 1);
    kindRef.current = "profile";
    markdownRef.current = profileStarter;
    savedDocumentRef.current = null;
    revisionRef.current += 1;
    lineageRef.current += 1;
    setLineage(lineageRef.current);
    updateGuidedReferenceChoices(defaultGuidedReferenceChoices);
    updateHumanStage("foundation");
    setDraftOwner(resolvedSubject);
  }, [draftOwner, resolvedSubject]);

  const setEditorLayout = useCallback((layout: EditorLayout) => {
    if (!maskDraftRef.current) updateEditorLayout(layout);
  }, []);
  const setEditorInterface = useCallback((value: EditorInterface) => {
    if (!maskDraftRef.current) updateEditorInterface(value);
  }, []);
  const requestSourceLine = useCallback((line: number | null) => {
    if (!maskDraftRef.current) setSourceLineRequest(line);
  }, []);
  const rememberReplacement = useCallback(() => {
    const previous = new TextEncoder().encode(markdownRef.current).length <= PROFILE_RESUME_MAX_UTF8_BYTES
      ? { kind: kindRef.current, markdown: markdownRef.current } : null;
    previousDraftRef.current = previous;
    setPreviousDraft(previous);
  }, []);
  const discardUndo = useCallback(() => {
    if (maskDraftRef.current) return;
    previousDraftRef.current = null;
    setPreviousDraft(null);
  }, []);
  const setMarkdown = useCallback((next: string) => {
    if (!maskDraftRef.current) {
      const canonical = normaliseMarkdown(next);
      markdownRef.current = canonical;
      revisionRef.current += 1;
      updateMarkdown(canonical);
      setRevision((current) => current + 1);
    }
  }, []);
  const replaceMarkdown = useCallback((next: string) => {
    if (maskDraftRef.current) return;
    rememberReplacement();
    const canonical = normaliseMarkdown(next);
    markdownRef.current = canonical;
    savedDocumentRef.current = null;
    revisionRef.current += 1;
    lineageRef.current += 1;
    setLineage(lineageRef.current);
    updateGuidedReferenceChoices(defaultGuidedReferenceChoices);
    updateMarkdown(canonical);
    setSavedDocument(null);
    setRevision((current) => current + 1);
  }, [rememberReplacement]);
  const replaceDraft = useCallback((nextKind: DocumentKind, nextMarkdown: string) => {
    if (maskDraftRef.current) return;
    rememberReplacement();
    const canonical = normaliseMarkdown(nextMarkdown);
    kindRef.current = nextKind;
    markdownRef.current = canonical;
    savedDocumentRef.current = null;
    revisionRef.current += 1;
    lineageRef.current += 1;
    setLineage(lineageRef.current);
    updateGuidedReferenceChoices(defaultGuidedReferenceChoices);
    updateKind(nextKind);
    updateMarkdown(canonical);
    setSavedDocument(null);
    setRevision((current) => current + 1);
  }, [rememberReplacement]);
  const undoReplacement = useCallback(() => {
    if (maskDraftRef.current || !previousDraftRef.current) return;
    const previous = previousDraftRef.current;
    replaceDraft(previous.kind, previous.markdown);
    discardUndo();
  }, [discardUndo, replaceDraft]);
  const setKind = useCallback((nextKind: DocumentKind) => {
    if (maskDraftRef.current || nextKind === kindRef.current) return;
    rememberReplacement();
    const converted = switchDocumentKind(markdownRef.current, nextKind);
    kindRef.current = nextKind;
    markdownRef.current = converted;
    savedDocumentRef.current = null;
    revisionRef.current += 1;
    lineageRef.current += 1;
    setLineage(lineageRef.current);
    updateGuidedReferenceChoices(defaultGuidedReferenceChoices);
    updateMarkdown(converted);
    setSavedDocument(null);
    updateKind(nextKind);
    setRevision((current) => current + 1);
  }, [rememberReplacement]);
  const setHumanStage = useCallback((stage: HumanJourneyStage) => {
    if (!maskDraftRef.current) updateHumanStage(stage);
  }, []);
  const setGuidedReferenceChoices = useCallback((choices: Partial<GuidedReferenceChoices>) => {
    if (!maskDraftRef.current) updateGuidedReferenceChoices((current) => ({ ...current, ...choices }));
  }, []);
  const hydrateSavedDocument = useCallback((document: DocumentResponse) => {
    if (maskDraftRef.current) return;
    const canonical = normaliseMarkdown(document.markdown);
    kindRef.current = document.kind;
    markdownRef.current = canonical;
    savedDocumentRef.current = { ...document, markdown: canonical };
    revisionRef.current += 1;
    lineageRef.current += 1;
    setLineage(lineageRef.current);
    updateGuidedReferenceChoices(defaultGuidedReferenceChoices);
    updateKind(document.kind);
    updateMarkdown(canonical);
    setSavedDocument(savedDocumentRef.current);
    setRevision((current) => current + 1);
  }, []);
  const recordSavedDocument = useCallback((document: DocumentResponse, rebasedMarkdown: string) => {
    if (maskDraftRef.current || document.kind !== kindRef.current) return;
    const canonicalDocument = { ...document, markdown: normaliseMarkdown(document.markdown) };
    const canonicalRebase = normaliseMarkdown(rebasedMarkdown);
    markdownRef.current = canonicalRebase;
    savedDocumentRef.current = canonicalDocument;
    updateMarkdown(canonicalRebase);
    setSavedDocument(canonicalDocument);
  }, []);
  const recordLocalDownload = useCallback((filename: string) => {
    if (maskDraftRef.current) return;
    setLocalDownloadReceipt({
      filename,
      kind: kindRef.current,
      markdown: markdownRef.current,
    });
  }, []);
  const getDraftSnapshot = useCallback(() => maskOwnedDraftSnapshot(maskDraftRef.current, {
    kind: kindRef.current,
    markdown: markdownRef.current,
    revision: revisionRef.current,
    lineage: lineageRef.current,
    checkpointGeneration: checkpointGenerationRef.current,
    identifier: documentIdentifier(markdownRef.current, kindRef.current),
    savedDocument: savedDocumentRef.current
  }), []);
  const saveCheckpoint = useCallback((label: string) => {
    if (maskDraftRef.current) throw new Error("The current draft is unavailable.");
    const checkpoint = createCheckpoint(checkpointsRef.current, { kind: kindRef.current, markdown: markdownRef.current }, label, ++checkpointIdRef.current);
    checkpointsRef.current = [...checkpointsRef.current, checkpoint];
    checkpointGenerationRef.current += 1;
    setCheckpoints(checkpointsRef.current);
  }, []);
  const renameCheckpoint = useCallback((id: number, label: string) => {
    if (maskDraftRef.current) throw new Error("The current draft is unavailable.");
    const renamed = renameCheckpointEntry(checkpointsRef.current, id, label);
    checkpointsRef.current = renamed;
    checkpointGenerationRef.current += 1;
    setCheckpoints(renamed);
  }, []);
  const restoreCheckpoint = useCallback((id: number) => {
    if (maskDraftRef.current) return;
    const checkpoint = checkpointsRef.current.find((entry) => entry.id === id);
    if (checkpoint) replaceDraft(checkpoint.kind, checkpoint.markdown);
  }, [replaceDraft]);
  const removeCheckpoint = useCallback((id: number) => {
    if (maskDraftRef.current) return;
    checkpointsRef.current = checkpointsRef.current.filter((entry) => entry.id !== id);
    checkpointGenerationRef.current += 1;
    setCheckpoints(checkpointsRef.current);
  }, []);
  const restoreRecovery = useCallback((bundle: RecoveryBundle) => {
    if (maskDraftRef.current) return;
    const restored = bundle.checkpoints.map((entry) => ({ ...entry, id: ++checkpointIdRef.current }));
    replaceDraft(bundle.draft.kind, bundle.draft.markdown);
    checkpointsRef.current = restored;
    checkpointGenerationRef.current += 1;
    setCheckpoints(restored);
  }, [replaceDraft]);
  const value = useMemo(() => ({
    editorLayout: maskDraft ? "split" as const : editorLayout,
    editorInterface: maskDraft ? "code" as const : editorInterface,
    setEditorLayout,
    setEditorInterface,
    sourceLineRequest: maskDraft ? null : sourceLineRequest,
    requestSourceLine,
    previousDraft: maskDraft ? null : previousDraft,
    undoReplacement,
    discardUndo,
    restoreRecovery,
    checkpoints: maskDraft ? [] : checkpoints,
    saveCheckpoint,
    renameCheckpoint,
    restoreCheckpoint,
    removeCheckpoint,
    kind: maskDraft ? "profile" as const : kind,
    markdown: maskDraft ? profileStarter : markdown,
    savedDocument: maskDraft ? null : savedDocument,
    revision,
    lineage,
    masked: maskDraft,
    humanStage: maskDraft ? "foundation" as const : humanStage,
    guidedReferenceChoices: maskDraft ? defaultGuidedReferenceChoices : guidedReferenceChoices,
    localDownloadReceipt: maskDraft ? null : localDownloadReceipt,
    setMarkdown,
    replaceMarkdown,
    replaceDraft,
    setKind,
    setHumanStage,
    setGuidedReferenceChoices,
    hydrateSavedDocument,
    recordSavedDocument,
    recordLocalDownload,
    getDraftSnapshot
  }), [editorLayout, editorInterface, setEditorLayout, setEditorInterface, sourceLineRequest, requestSourceLine, previousDraft, undoReplacement, discardUndo, restoreRecovery, checkpoints, saveCheckpoint, renameCheckpoint, restoreCheckpoint, removeCheckpoint, getDraftSnapshot, guidedReferenceChoices, humanStage, hydrateSavedDocument, kind, lineage, localDownloadReceipt, markdown, maskDraft, recordLocalDownload, recordSavedDocument, replaceDraft, replaceMarkdown, revision, savedDocument, setGuidedReferenceChoices, setHumanStage, setKind, setMarkdown]);

  return <DraftContext.Provider key={authBoundary} value={value}>{children}</DraftContext.Provider>;
}

export function useDraft() {
  const value = useContext(DraftContext);
  if (!value) throw new Error("useDraft must be used inside DraftProvider.");
  return value;
}
