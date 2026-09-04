"use client";

import React, { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { documentIdentifier, profileStarter, type DocumentKind, type HumanFields, normaliseMarkdown, starterFor, switchDocumentKind } from "@/lib/markdown";
import { type DocumentResponse } from "@/lib/api";
import { maskOwnedDraftSnapshot, requiresDraftReset, resolvedDraftSubject } from "@/lib/draft-security";
import { type HumanJourneyStage } from "@/lib/human-journey";

type DraftState = {
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
  getDraftSnapshot: () => { kind: DocumentKind; markdown: string; revision: number; lineage: number; identifier: string; savedDocument: DocumentResponse | null } | null;
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
  const [kind, updateKind] = useState<DocumentKind>("profile");
  const [markdown, updateMarkdown] = useState(profileStarter);
  const [savedDocument, setSavedDocument] = useState<DocumentResponse | null>(null);
  const [revision, setRevision] = useState(0);
  const [lineage, setLineage] = useState(0);
  const [humanStage, updateHumanStage] = useState<HumanJourneyStage>("foundation");
  const [guidedReferenceChoices, updateGuidedReferenceChoices] = useState(defaultGuidedReferenceChoices);
  const [localDownloadReceipt, setLocalDownloadReceipt] = useState<LocalDownloadReceipt | null>(null);
  const [draftOwner, setDraftOwner] = useState<string | null>(null);
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
  const unsavedDraft = !maskDraft
    && markdown !== (savedDocument?.markdown ?? starterFor(kind))
    && (localDownloadReceipt?.kind !== kind || localDownloadReceipt.markdown !== markdown);

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
  }, []);
  const replaceDraft = useCallback((nextKind: DocumentKind, nextMarkdown: string) => {
    if (maskDraftRef.current) return;
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
  }, []);
  const setKind = useCallback((nextKind: DocumentKind) => {
    if (maskDraftRef.current || nextKind === kindRef.current) return;
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
  }, []);
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
    identifier: documentIdentifier(markdownRef.current, kindRef.current),
    savedDocument: savedDocumentRef.current
  }), []);
  const value = useMemo(() => ({
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
  }), [getDraftSnapshot, guidedReferenceChoices, humanStage, hydrateSavedDocument, kind, lineage, localDownloadReceipt, markdown, maskDraft, recordLocalDownload, recordSavedDocument, replaceDraft, replaceMarkdown, revision, savedDocument, setGuidedReferenceChoices, setHumanStage, setKind, setMarkdown]);

  return <DraftContext.Provider key={authBoundary} value={value}>{children}</DraftContext.Provider>;
}

export function useDraft() {
  const value = useContext(DraftContext);
  if (!value) throw new Error("useDraft must be used inside DraftProvider.");
  return value;
}
