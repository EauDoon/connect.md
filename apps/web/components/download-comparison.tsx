"use client";

import React, { useMemo } from "react";
import { useDraft } from "@/components/draft-provider";
import { compareDrafts } from "@/lib/draft-comparison";

export function DownloadComparison() {
  const { kind, markdown, localDownloadReceipt } = useDraft();
  const comparison = useMemo(() => localDownloadReceipt ? compareDrafts(localDownloadReceipt.markdown, markdown) : null, [localDownloadReceipt, markdown]);
  if (!localDownloadReceipt || !comparison) return null;
  return <details className="mt-4 border-t border-white/10 pt-3">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Changes since last Markdown download</summary>
    <section aria-label="Last download comparison" className="min-w-0 text-xs leading-5 text-mist">
      <p className="break-words">Compared with {localDownloadReceipt.filename}, the exact source used for this session&apos;s last Markdown download request.</p>
      {localDownloadReceipt.kind !== kind && <p className="mt-2 text-amber-100">Document type changed from {localDownloadReceipt.kind} to {kind}.</p>}
      <p className="mt-2">{comparison.identical ? "The Markdown bytes match exactly." : `Changed region starts at line ${comparison.firstChangedLine}: ${comparison.removedLines} previous lines, ${comparison.addedLines} current lines.`}</p>
      {!comparison.identical && <div className="mt-3 grid min-w-0 gap-3">
        {[["Last downloaded source", comparison.before], ["Current source", comparison.after]].map(([title, source]) => <div key={title} className="min-w-0"><h3 className="font-semibold text-white">{title}</h3><pre tabIndex={0} aria-label={title} className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black/20 p-3">{source || "(No lines)"}</pre></div>)}
      </div>}
      {comparison.truncated && <p className="mt-2 text-amber-100">Comparison display is limited to 80 lines and 12,000 characters per side. Review the full sources for all changes.</p>}
      <p className="mt-2">Separate edits may include unchanged lines between them. Copying or downloading a recovery/review file does not move this comparison baseline.</p>
    </section>
  </details>;
}
