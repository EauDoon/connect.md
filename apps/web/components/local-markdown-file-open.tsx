"use client";

import { FileText, FolderOpen, LoaderCircle } from "lucide-react";
import React, { useRef, useState, type ChangeEvent } from "react";

import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { shouldConfirmDraftReplacement } from "@/lib/draft-replacement";
import {
  decodeLocalMarkdownFile,
  localMarkdownFilenameIssue,
  localMarkdownFileSizeIssue,
  parseLocalMarkdownDraft,
} from "@/lib/local-markdown-file";

type OpenStatus = "idle" | "reading" | "success" | "error";

export function LocalMarkdownFileOpen() {
  const { getDraftSnapshot, masked, replaceDraft } = useDraft();
  const inputRef = useRef<HTMLInputElement>(null);
  const selectionRef = useRef(0);
  const [status, setStatus] = useState<OpenStatus>("idle");
  const [message, setMessage] = useState("");

  async function openSelectedFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    const selection = selectionRef.current + 1;
    selectionRef.current = selection;

    const fileIssue = localMarkdownFilenameIssue(file.name) ?? localMarkdownFileSizeIssue(file.size);
    if (fileIssue) {
      setStatus("error");
      setMessage(fileIssue);
      return;
    }

    setStatus("reading");
    setMessage(`Reading ${file.name} locally.`);

    try {
      const source = decodeLocalMarkdownFile(await file.arrayBuffer());
      const imported = parseLocalMarkdownDraft(source);
      if (selectionRef.current !== selection) return;

      const current = getDraftSnapshot();
      if (!current) throw new Error("The current draft is unavailable. Reload the page and try again.");
      if (shouldConfirmDraftReplacement(current.markdown, current.kind, current.savedDocument)
        && !window.confirm(`Replace the current local draft with “${file.name}”? Unsaved changes will be lost.`)) {
        setStatus("idle");
        setMessage("The current draft was kept.");
        return;
      }

      replaceDraft(imported.kind, imported.markdown);
      setStatus("success");
      setMessage(`Opened ${file.name} locally as a ${imported.kind}. Nothing was uploaded.`);
    } catch (error) {
      if (selectionRef.current !== selection) return;
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "The local file could not be opened.");
    }
  }

  return (
    <div className="flex min-w-0 flex-col items-start gap-1.5 sm:items-end">
      <input
        ref={inputRef}
        type="file"
        accept=".md,text/markdown"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => void openSelectedFile(event)}
      />
      <Button
        variant="secondary"
        className="shrink-0 px-4"
        disabled={masked || status === "reading"}
        aria-describedby={message ? "local-markdown-file-status" : undefined}
        onClick={() => inputRef.current?.click()}
      >
        {status === "reading" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <FolderOpen className="size-4" aria-hidden />}
        {status === "reading" ? "Opening .md" : "Open local .md"}
      </Button>
      {message && (
        <p
          id="local-markdown-file-status"
          role={status === "error" ? "alert" : "status"}
          aria-live="polite"
          className={`max-w-sm text-xs leading-5 ${status === "error" ? "text-red-200" : "text-mist"}`}
        >
          <FileText className="mr-1 inline size-3.5 align-[-2px]" aria-hidden />
          {message}
        </p>
      )}
    </div>
  );
}
