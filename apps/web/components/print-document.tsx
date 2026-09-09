"use client";

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDraft } from "@/components/draft-provider";
import { MarkdownPreview } from "@/components/markdown-preview";
import { Button } from "@/components/ui/button";

function PrintDialog({ markdown, onClose }: { markdown: string; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return createPortal(<dialog ref={dialogRef} onCancel={onClose} onClose={onClose} aria-labelledby="print-document-title" className="draft-print-dialog w-[min(54rem,calc(100%-2rem))] max-w-full rounded-2xl bg-white p-6 text-slate-950 shadow-2xl backdrop:bg-black/70">
    <div className="draft-print-controls mb-6 border-b border-slate-200 pb-4">
      <h2 id="print-document-title" className="text-xl font-bold">Print document preview</h2>
      <p className="mt-2 text-sm leading-6">Print the sanitized body or choose Save as PDF in your browser, if available. Frontmatter and editing controls are excluded. Remote images remain blocked. Keep the .md download as your editable source.</p>
      <div className="mt-4 flex flex-wrap gap-3"><Button onClick={() => window.print()}>Print document</Button><button type="button" autoFocus onClick={onClose} className="min-h-11 rounded-full border border-slate-400 px-5 text-sm font-semibold">Close print preview</button></div>
    </div>
    <MarkdownPreview markdown={markdown} className="light-preview" />
  </dialog>, document.body);
}

export function PrintDocument({ disabled }: { disabled: boolean }) {
  const { markdown } = useDraft();
  const [open, setOpen] = useState(false);
  return <>
    <Button variant="secondary" className="mt-3" disabled={disabled} onClick={() => setOpen(true)}>Print preview</Button>
    {open && <PrintDialog markdown={markdown} onClose={() => setOpen(false)} />}
  </>;
}
