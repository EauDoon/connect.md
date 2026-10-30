"use client";

import React, { useState } from "react";
import { useDraft } from "@/components/draft-provider";
import { LineDiffView } from "@/components/line-diff-view";

export function DownloadComparison({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const { kind, markdown, localDownloadReceipt } = useDraft();
  // The comparison is computed only while the details are open, so typing
  // never pays for a diff nobody is looking at.
  const [open, setOpen] = useState(defaultOpen);
  if (!localDownloadReceipt) return null;
  return <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="mt-4 border-t border-white/10 pt-3">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Changes since last Markdown download</summary>
    {open && <section aria-label="Last download comparison" className="min-w-0 text-xs leading-5 text-mist">
      <p className="break-words">Compared with {localDownloadReceipt.filename}, the exact source used for this session&apos;s last Markdown download request.</p>
      {localDownloadReceipt.kind !== kind && <p className="mt-2 text-amber-100">Document type changed from {localDownloadReceipt.kind} to {kind}.</p>}
      <LineDiffView before={localDownloadReceipt.markdown} after={markdown} label="Changes from the last downloaded source to the current source" beforeTitle="Last downloaded source" afterTitle="Current source" />
      <p className="mt-2">Copying or downloading a recovery/review file does not move this comparison baseline.</p>
    </section>}
  </details>;
}
