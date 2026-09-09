"use client";

import React, { useState } from "react";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { buildReviewReport } from "@/lib/review-report";

export function ReviewReport({ onDownload }: { onDownload: (report: string, filename: string) => void }) {
  const { markdown, kind, masked } = useDraft();
  const [pending, setPending] = useState(false);
  const [reported, setReported] = useState<string | null>(null);
  const [error, setError] = useState("");
  return <div className="mt-4 border-t border-white/10 pt-3">
    <Button variant="secondary" disabled={pending || masked} onClick={async () => {
      setPending(true);
      setError("");
      try {
        const report = await buildReviewReport(markdown, kind);
        onDownload(report, `connectmd-${kind}-review.md`);
        setReported(markdown);
      } catch (failure) { setError(failure instanceof Error ? failure.message : "Review report could not be downloaded."); }
      finally { setPending(false); }
    }}>{pending ? "Preparing review report" : "Download review report"}</Button>
    <p className="mt-2 text-xs leading-5 text-mist">Save validation counts and review suggestions with an exact source fingerprint. The report excludes your document text and does not replace the .md download.</p>
    {error ? <p role="alert" className="mt-2 text-xs text-amber-100">{error}</p> : reported !== null && <p role="status" className="mt-2 text-xs text-mist">{reported === markdown ? "Review report downloaded for the current draft. Save the source Markdown separately." : "The draft changed after this report. Download a new report for the current bytes."}</p>}
  </div>;
}
