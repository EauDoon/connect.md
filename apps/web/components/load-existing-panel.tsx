"use client";

import { FolderOpen, LoaderCircle, RefreshCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { loadDocument, presentApiError } from "@/lib/api";
import { isImportResultCurrent, shouldConfirmDraftReplacement } from "@/lib/draft-replacement";
import { listOwnedDocumentPageForSubject, type OwnedDocumentOption } from "@/lib/agent-api";

const identifierPattern = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/u;

export function LoadExistingPanel() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const { kind, markdown, revision, savedDocument, hydrateSavedDocument } = useDraft();
  const markdownRef = useRef(markdown);
  const revisionRef = useRef(revision);
  const mountedRef = useRef(true);
  const controllerRef = useRef<AbortController | null>(null);
  const requestSubjectRef = useRef<string | null>(null);
  markdownRef.current = markdown;
  revisionRef.current = revision;
  const [identifier, setIdentifier] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [message, setMessage] = useState("");
  const [inventoryStatus, setInventoryStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [inventoryMessage, setInventoryMessage] = useState("");
  const [inventory, setInventory] = useState<OwnedDocumentOption[]>([]);
  const [inventoryCursor, setInventoryCursor] = useState<string | null>(null);
  const [inventoryPageLoading, setInventoryPageLoading] = useState(false);
  const [inventoryRefresh, setInventoryRefresh] = useState(0);
  const inventoryRequestRef = useRef(0);
  const inventoryControllerRef = useRef<AbortController | null>(null);
  const inventorySeenCursorsRef = useRef(new Set<string>());
  const normalizedIdentifier = identifier.trim().toLowerCase();
  const authIdentity = configured && isLoaded && isSignedIn && subject ? subject : null;
  const authIdentityRef = useRef(authIdentity);
  authIdentityRef.current = authIdentity;
  const available = authIdentity !== null;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      inventoryControllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller || requestSubjectRef.current === authIdentity) return;
    controller.abort();
    controllerRef.current = null;
    requestSubjectRef.current = null;
    setStatus("idle");
    setMessage("Open was cancelled because the signed-in account changed.");
  }, [authIdentity]);

  useEffect(() => {
    const requestId = inventoryRequestRef.current + 1;
    inventoryRequestRef.current = requestId;
    if (!authIdentity) {
      inventoryControllerRef.current?.abort();
      setInventory([]);
      setInventoryCursor(null);
      setInventoryPageLoading(false);
      setInventoryStatus("idle");
      setInventoryMessage("");
      return;
    }
    setInventoryStatus("loading");
    setInventoryMessage(`Finding your saved ${kind}s.`);
    inventorySeenCursorsRef.current = new Set();
    const controller = new AbortController();
    inventoryControllerRef.current?.abort();
    inventoryControllerRef.current = controller;
    const isSubjectCurrent = () => authIdentityRef.current === authIdentity && !controller.signal.aborted;
    void listOwnedDocumentPageForSubject(getToken, isSubjectCurrent, { kind, limit: 25, signal: controller.signal }).then((page) => {
      if (inventoryRequestRef.current !== requestId || authIdentityRef.current !== authIdentity || controller.signal.aborted) return;
      setInventory(page.documents);
      setInventoryCursor(page.nextCursor);
      setInventoryStatus("success");
      setInventoryMessage(page.documents.length === 0 ? `No saved ${kind} yet. Continue below to create your first one.` : `${page.documents.length} saved ${kind}${page.documents.length === 1 ? "" : "s"} available.`);
    }).catch((error) => {
      if (inventoryRequestRef.current !== requestId || authIdentityRef.current !== authIdentity || controller.signal.aborted) return;
      setInventory([]);
      setInventoryCursor(null);
      setInventoryStatus("error");
      setInventoryMessage(presentApiError(error));
    });
    return () => {
      controller.abort();
      if (inventoryRequestRef.current === requestId) inventoryRequestRef.current += 1;
    };
  }, [authIdentity, getToken, inventoryRefresh, kind]);

  async function loadMoreInventory() {
    if (!authIdentity || !inventoryCursor || inventoryPageLoading) return;
    const cursor = inventoryCursor;
    if (inventorySeenCursorsRef.current.has(cursor)) {
      setInventoryCursor(null);
      setInventoryMessage("The document list stopped because its cursor repeated.");
      return;
    }
    inventorySeenCursorsRef.current.add(cursor);
    const controller = new AbortController();
    inventoryControllerRef.current?.abort();
    inventoryControllerRef.current = controller;
    setInventoryPageLoading(true);
    setInventoryMessage(`Loading more saved ${kind}s.`);
    const isSubjectCurrent = () => authIdentityRef.current === authIdentity && !controller.signal.aborted;
    try {
      const page = await listOwnedDocumentPageForSubject(getToken, isSubjectCurrent, { kind, cursor, limit: 25, signal: controller.signal });
      if (!isSubjectCurrent()) return;
      const known = new Set(inventory.map((document) => document.id));
      setInventory((current) => [...current, ...page.documents.filter((document) => !known.has(document.id))]);
      const cursorRepeated = page.nextCursor !== null && inventorySeenCursorsRef.current.has(page.nextCursor);
      setInventoryCursor(cursorRepeated ? null : page.nextCursor);
      setInventoryMessage(cursorRepeated ? "The document list stopped because its cursor repeated." : "More saved documents loaded.");
    } catch (error) {
      inventorySeenCursorsRef.current.delete(cursor);
      if (isSubjectCurrent()) setInventoryMessage(presentApiError(error));
    } finally {
      if (isSubjectCurrent()) setInventoryPageLoading(false);
      if (inventoryControllerRef.current === controller) inventoryControllerRef.current = null;
    }
  }

  async function load() {
    if (!identifierPattern.test(normalizedIdentifier)) {
      setStatus("error");
      setMessage("Enter a lowercase identifier using letters, numbers, and hyphens.");
      return;
    }
    if (status === "loading" || controllerRef.current) return;
    if (!authIdentity) return;
    if (shouldConfirmDraftReplacement(markdownRef.current, kind, savedDocument)
      && !window.confirm(`Replace the current local draft with the saved ${kind} “${normalizedIdentifier}”? Unsaved changes will be lost.`)) return;
    const revisionAtRequest = revisionRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    requestSubjectRef.current = authIdentity;
    const requestSubject = authIdentity;
    const isSubjectCurrent = () => mountedRef.current
      && controllerRef.current === controller
      && requestSubjectRef.current === requestSubject
      && authIdentityRef.current === requestSubject
      && !controller.signal.aborted;
    setStatus("loading");
    setMessage(`Loading the canonical ${kind}.`);
    try {
      const document = await loadDocument(kind, normalizedIdentifier, getToken, isSubjectCurrent, controller.signal);
      if (!isSubjectCurrent()) return;
      if (!isImportResultCurrent(kind, revisionAtRequest, kind, revisionRef.current, mountedRef.current, controller.signal.aborted)
        && !window.confirm("The local draft changed while the saved document was loading. Replace those newer changes?")) {
        setStatus("idle");
        setMessage("Saved document was not opened; newer local changes were kept.");
        return;
      }
      hydrateSavedDocument(document);
      setIdentifier(document.identifier);
      setStatus("success");
      setMessage(`Opened canonical ${document.kind} version ${document.version}.`);
    } catch (error) {
      if (!isSubjectCurrent()) return;
      setStatus("error");
      setMessage(presentApiError(error));
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        requestSubjectRef.current = null;
      }
    }
  }

  return (
    <section aria-labelledby="load-existing-title" className="w-full min-w-0 rounded-2xl border border-white/10 bg-black/15 p-3 sm:p-4">
      <div className="flex min-w-0 gap-3">
        <FolderOpen className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 id="load-existing-title" className="text-sm font-semibold text-white">Open a saved {kind}</h2>
          <p className="mt-1 text-sm leading-5 text-mist">Choose one of your documents or enter its {kind === "profile" ? "handle" : "slug"}. Private content stays in memory only and is never stored in browser storage.</p>
          {available && <div className="mt-3 rounded-xl border border-white/10 bg-black/15 p-3">
            <label htmlFor={`owned-${kind}`} className="block text-xs font-semibold uppercase tracking-[.12em] text-mist">Your saved {kind}s</label>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <select id={`owned-${kind}`} value={inventory.some((document) => document.identifier === identifier) ? identifier : ""} disabled={inventoryStatus === "loading" || inventory.length === 0 || status === "loading"} onChange={(event) => setIdentifier(event.target.value)} className="min-h-11 min-w-0 flex-1 rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none transition focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:cursor-not-allowed disabled:opacity-60">
                <option value="">{inventoryStatus === "loading" ? "Loading saved documents…" : inventory.length === 0 ? `No saved ${kind}s` : `Select a saved ${kind}`}</option>
                {inventory.map((document) => <option key={document.id} value={document.identifier}>{document.identifier} · version {document.version}</option>)}
              </select>
              {inventoryStatus === "error" && <Button variant="ghost" className="shrink-0" disabled={status === "loading"} onClick={() => setInventoryRefresh((value) => value + 1)}><RefreshCcw className="size-4" aria-hidden /> Retry list</Button>}
              {inventoryCursor && inventoryStatus === "success" && <Button variant="ghost" className="shrink-0" disabled={status === "loading" || inventoryPageLoading} onClick={() => void loadMoreInventory()}>{inventoryPageLoading && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Load more</Button>}
            </div>
            {inventoryMessage && <p role={inventoryStatus === "error" ? "alert" : "status"} className={`mt-2 text-xs ${inventoryStatus === "error" ? "text-red-200" : "text-mist/75"}`}>{inventoryMessage}</p>}
          </div>}
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Input aria-label={`${kind} identifier`} maxLength={63} autoCapitalize="none" spellCheck={false} value={identifier} disabled={!available || status === "loading"} onChange={(event) => setIdentifier(event.target.value.toLowerCase().replace(/\s+/gu, "-"))} placeholder={kind === "profile" ? "your-handle" : "your-name-resume"} />
            <Button variant="secondary" className="shrink-0" disabled={!available || status === "loading" || !normalizedIdentifier} onClick={() => void load()}>{status === "loading" && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Open</Button>
          </div>
          {!available && <p className="mt-2 text-xs text-mist/75">{configured ? "Sign in to open an owned document." : "Clerk configuration is required to open saved documents."}</p>}
          {message && <p role={status === "error" ? "alert" : "status"} className={`mt-2 text-sm ${status === "error" ? "text-red-200" : "text-mist"}`}>{message}</p>}
        </div>
      </div>
    </section>
  );
}
