"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useEffect, useRef, useState, type DragEvent, type ChangeEvent } from "react";
import { CheckCircle2, FileUp, LoaderCircle, ShieldAlert } from "lucide-react";

import { ingestDocument, ingestMetadataFromResponse, markdownFromIngestResponse, presentApiError } from "@/lib/api";
import { useConnectmdAuth } from "@/components/auth-provider";
import { useDraft } from "@/components/draft-provider";
import { cn } from "@/lib/utils";
import { isImportResultCurrent, shouldConfirmDraftReplacement } from "@/lib/draft-replacement";

const acceptedExtensions = ["pdf", "docx", "md", "markdown", "txt"];
const maxBytes = 10 * 1024 * 1024;

function isAccepted(file: File) {
  const extension = file.name.split(".").pop()?.toLowerCase();
  return extension ? acceptedExtensions.includes(extension) : false;
}

export function IngestDropzone() {
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadingRef = useRef(false);
  const dragDepthRef = useRef(0);
  const mountedRef = useRef(true);
  const controllerRef = useRef<AbortController | null>(null);
  const requestSubjectRef = useRef<string | null>(null);
  const { kind, markdown, revision, savedDocument, replaceMarkdown } = useDraft();
  const markdownRef = useRef(markdown);
  const kindRef = useRef(kind);
  const revisionRef = useRef(revision);
  markdownRef.current = markdown;
  kindRef.current = kind;
  revisionRef.current = revision;
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const authIdentity = configured && isLoaded && isSignedIn && subject ? subject : null;
  const authIdentityRef = useRef(authIdentity);
  authIdentityRef.current = authIdentity;
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<"idle" | "uploading" | "success" | "error">("idle");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [message, setMessage] = useState("Drop a PDF, DOCX, Markdown, or text file to create a draft.");
  const [metadata, setMetadata] = useState<{ warnings: string[]; provenance: Record<string, string> } | null>(null);
  const reducedMotion = useReducedMotion();

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
    requestSubjectRef.current = null;
    uploadingRef.current = false;
    dragDepthRef.current = 0;
    setDragging(false);
    setSelectedFile(null);
    setStatus("idle");
    setMetadata(null);
    setMessage("Import was cancelled because the signed-in account changed.");
  }, [authIdentity]);

  async function processFile(file: File | undefined) {
    if (!file || uploadingRef.current) return;
    setSelectedFile(file.name);
    if (!isAccepted(file)) {
      setStatus("error");
      setMessage("Choose a PDF, DOCX, Markdown, or plain-text file.");
      return;
    }
    if (file.size > maxBytes) {
      setStatus("error");
      setMessage("This file exceeds the 10 MiB upload limit.");
      return;
    }
    if (!authIdentity) {
      setStatus("error");
      setMessage(configured ? "Wait for authentication to finish, then sign in before requesting ingestion." : "Clerk is not configured, so ingestion is unavailable.");
      return;
    }
    if (shouldConfirmDraftReplacement(markdownRef.current, kind, savedDocument)
      && !window.confirm("Replace the current local draft with an imported draft? Unsaved content and saved-document identity will be removed.")) return;

    uploadingRef.current = true;
    const kindAtRequest = kind;
    const revisionAtRequest = revisionRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    requestSubjectRef.current = authIdentity;
    setStatus("uploading");
    setMetadata(null);
    setMessage(`Uploading ${file.name}; waiting for connect.md to return a draft.`);
    try {
      const response = await ingestDocument(file, kindAtRequest, getToken, () => authIdentityRef.current === requestSubjectRef.current, controller.signal);
      if (!mountedRef.current || controller.signal.aborted) return;
      if (authIdentityRef.current !== requestSubjectRef.current) {
        setStatus("idle");
        setMetadata(null);
        setMessage("Import was cancelled because the signed-in account changed.");
        return;
      }
      const markdown = markdownFromIngestResponse(response);
      if (!markdown) throw new Error("The API response did not contain a canonical Markdown draft.");
      if (kindRef.current !== kindAtRequest) {
        setStatus("idle");
        setMessage("Import was not applied because the document type changed while it was running.");
        return;
      }
      if (!isImportResultCurrent(kindAtRequest, revisionAtRequest, kindRef.current, revisionRef.current, mountedRef.current, controller.signal.aborted)
        && !window.confirm("The local draft changed while import was running. Replace those newer changes with the imported draft?")) {
        setStatus("idle");
        setMessage("Import finished but was not applied; newer local changes were kept.");
        return;
      }
      replaceMarkdown(markdown);
      setMetadata(ingestMetadataFromResponse(response));
      setStatus("success");
      setMessage("Draft received. Review the fields and publish explicitly when ready.");
    } catch (error) {
      if (!mountedRef.current || controller.signal.aborted) return;
      setStatus("error");
      setMessage(presentApiError(error));
    } finally {
      if (controllerRef.current === controller) {
        uploadingRef.current = false;
        controllerRef.current = null;
        requestSubjectRef.current = null;
      }
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);
    if (!uploadingRef.current) void processFile(event.dataTransfer.files.item(0) ?? undefined);
  }

  function onDragEnter(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (uploadingRef.current) return;
    dragDepthRef.current += 1;
    setDragging(true);
  }

  function onDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragging(false);
  }

  function onChange(event: ChangeEvent<HTMLInputElement>) {
    void processFile(event.target.files?.[0]);
    event.target.value = "";
  }

  return (
    <motion.section layout aria-labelledby="ingest-title" className={cn("w-full min-w-0 rounded-2xl border border-dashed p-3 transition-colors sm:p-4", dragging ? "border-acid/60 bg-acid/[.07]" : "border-white/15 bg-black/20")}>
      <motion.div
        role="button"
        tabIndex={0}
        aria-describedby="ingest-status"
        aria-disabled={status === "uploading"}
        onClick={() => { if (!uploadingRef.current) inputRef.current?.click(); }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (!uploadingRef.current) inputRef.current?.click();
          }
        }}
        onDragEnter={onDragEnter}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        animate={reducedMotion ? undefined : { scale: dragging ? 1.01 : 1 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className={cn("relative cursor-pointer overflow-hidden rounded-xl p-1 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid sm:p-4", status === "uploading" && "cursor-wait opacity-70", dragging ? "bg-acid/10" : "hover:bg-white/[.04]")}
      >
        <AnimatePresence>
          {dragging && <motion.div initial={reducedMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={reducedMotion ? undefined : { opacity: 0 }} className="pointer-events-none absolute inset-2 grid place-items-center rounded-lg border border-acid/40 bg-[#0c1205]/95 text-center">
            <span><strong className="block text-sm text-acid">Release to create a private draft</strong><span className="mt-1 block text-xs text-mist">The file is sent for ingestion; nothing is published.</span></span>
          </motion.div>}
        </AnimatePresence>
        <div className="flex min-w-0 gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-white/[.07] text-acid" aria-hidden>
            {status === "uploading" ? <LoaderCircle className="size-5 animate-spin" /> : status === "error" ? <ShieldAlert className="size-5 text-red-300" /> : <FileUp className="size-5" />}
          </span>
          <div className="min-w-0">
            <h2 id="ingest-title" className="text-sm font-semibold text-white">Import an existing document</h2>
            <p id="ingest-status" aria-live="polite" className={cn("mt-1 text-sm leading-5", status === "error" ? "text-red-200" : "text-mist")}>{message}</p>
            {selectedFile && <p className="mt-2 truncate text-xs font-medium text-white/80">Selected: {selectedFile}</p>}
            <p className="mt-2 text-xs text-mist/70">PDF, DOCX, Markdown, or text · max 10 MiB · ingestion never publishes</p>
          </div>
        </div>
      </motion.div>
      <AnimatePresence initial={!reducedMotion}>
        {status === "success" && <motion.div initial={reducedMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={reducedMotion ? undefined : { opacity: 0, y: -4 }} className="mt-3 flex gap-2 rounded-xl border border-acid/20 bg-acid/[.06] p-3 text-sm text-mist" role="status">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-acid" aria-hidden />
          <p><span className="font-semibold text-white">Draft ready.</span> Continue with the guided fields, then save explicitly when the preview is right.</p>
        </motion.div>}
      </AnimatePresence>
      {status === "success" && metadata && <div className="mt-3 grid gap-3 border-t border-white/10 pt-3 text-xs text-mist sm:grid-cols-2">
        <div><p className="font-semibold text-white">Ingestion warnings</p>{metadata.warnings.length ? <ul className="mt-1 list-disc space-y-1 pl-4">{metadata.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul> : <p className="mt-1">No warnings returned.</p>}</div>
        <div><p className="font-semibold text-white">Provenance</p>{Object.keys(metadata.provenance).length ? <dl className="mt-1 space-y-1">{Object.entries(metadata.provenance).map(([key, value]) => <div key={key} className="flex flex-wrap gap-x-2"><dt className="font-medium text-white/80">{key}</dt><dd className="break-all">{value}</dd></div>)}</dl> : <p className="mt-1">No provenance returned.</p>}</div>
      </div>}
      <input ref={inputRef} disabled={status === "uploading"} type="file" className="sr-only" accept=".pdf,.docx,.md,.markdown,.txt,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={onChange} />
    </motion.section>
  );
}
