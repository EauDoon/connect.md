"use client";

import React, { useState } from "react";
import { CheckCircle2, Download, LockKeyhole, TriangleAlert } from "lucide-react";

import { useDraft, type LocalDownloadReceipt } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { CopyMarkdown } from "@/components/copy-markdown";
import { PrivacyReview } from "@/components/privacy-review";
import { WritingReview } from "@/components/writing-review";
import { PrintDocument } from "@/components/print-document";
import { ReviewReport } from "@/components/review-report";
import { DownloadComparison } from "@/components/download-comparison";
import { documentIdentifier, type DocumentKind } from "@/lib/markdown";
import { hasValidationErrors, type ValidationIssue } from "@/lib/validation";

export function localDownloadFreshness(
  receipt: Pick<LocalDownloadReceipt, "kind" | "markdown"> | null,
  kind: DocumentKind,
  markdown: string,
) {
  if (!receipt || receipt.kind !== kind) return "none" as const;
  return receipt.markdown === markdown ? "current" as const : "stale" as const;
}

export function markdownDownloadName(kind: DocumentKind, identifier: string) {
  const safeIdentifier = identifier
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/gu, "-")
    .replace(/^-+|-+$/gu, "");
  return `${safeIdentifier || `connectmd-${kind}`}.md`;
}

export function preferredMarkdownName(kind: DocumentKind, identifier: string, preference: string) {
  const requested = preference.trim().replace(/\.md$/iu, "");
  let stem = markdownDownloadName(kind, requested || identifier).slice(0, -3).slice(0, 80).replace(/-+$/u, "");
  if (/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/iu.test(stem)) stem = `connectmd-${stem}`;
  return `${stem}.md`;
}

export function downloadMarkdown(markdown: string, filename: string) {
  const objectUrl = URL.createObjectURL(
    new Blob([markdown], { type: "text/markdown;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  try {
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.style.display = "none";
    document.body.append(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  }
}

export function PublishPanel({ issues }: { issues: ValidationIssue[] }) {
  const { kind, localDownloadReceipt, markdown, masked, recordLocalDownload } = useDraft();
  const [downloadAnnouncement, setDownloadAnnouncement] = useState(0);
  const [filenamePreference, setFilenamePreference] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const filename = preferredMarkdownName(kind, documentIdentifier(markdown, kind), filenamePreference);
  const blocked = masked || hasValidationErrors(issues);
  const freshness = localDownloadFreshness(localDownloadReceipt, kind, markdown);
  const downloadDescriptionIds = [blocked ? "download-blocked" : "", freshness !== "none" ? "download-status" : ""].filter(Boolean).join(" ") || undefined;

  function download() {
    if (blocked) return;
    try {
      downloadMarkdown(markdown, filename);
      recordLocalDownload(filename);
      setDownloadAnnouncement((current) => current + 1);
      setDownloadError("");
    } catch { setDownloadError("The download could not start. Your draft is still here. Try again or use Copy Markdown or Session recovery."); }
  }

  return (
    <section aria-labelledby="download-title" className="rounded-2xl border border-acid/20 bg-acid/[.06] p-4">
      <div className="flex gap-3">
        <LockKeyhole className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden />
        <div className="min-w-0">
          <h2 id="download-title" className="text-sm font-semibold text-white">Download gate</h2>
          <p className="mt-1 text-sm leading-5 text-mist">
            Validation happens in this browser. Downloading creates a local Markdown file; it does not publish or upload anything.
          </p>
          <label className="mt-3 block text-xs text-mist">Local filename (optional)
            <input value={filenamePreference} onChange={(event) => setFilenamePreference(event.target.value)} maxLength={80} aria-describedby="local-filename-help" className="mt-1 block w-full rounded-lg border border-white/20 bg-black/20 p-2 text-sm text-white" placeholder="Use the document identifier" />
          </label>
          <p id="local-filename-help" className="mt-1 break-all text-xs leading-5 text-mist">File: {filename}. Renaming this download does not change the document identifier.</p>
          <Button className="mt-4 w-full sm:w-auto" onClick={download} disabled={blocked} aria-describedby={downloadDescriptionIds}>
            <Download className="size-4" aria-hidden /> Download {freshness === "stale" ? "updated " : ""}{kind} .md
          </Button>
          {downloadError && <p role="alert" className="mt-3 text-xs text-amber-100">{downloadError}</p>}
          {blocked && <p id="download-blocked" className="mt-3 text-xs leading-5 text-amber-100">Resolve the validation errors above before downloading.</p>}
          {freshness === "current" && <p key={`current-${downloadAnnouncement}`} id="download-status" role="status" aria-live="polite" className="mt-3 flex gap-2 text-sm leading-5 text-acid"><CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />{localDownloadReceipt?.filename} downloaded. The current draft matches that local file; nothing was uploaded.</p>}
          {freshness === "stale" && <p id="download-status" role="status" aria-live="polite" className="mt-3 flex gap-2 text-sm leading-5 text-amber-100"><TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />The current draft no longer matches the last downloaded file. Download it again to keep that copy current.</p>}
          <p className="mt-3 text-xs leading-5 text-mist/75">The frontmatter visibility field is metadata only in this standalone site.</p>
          <CopyMarkdown />
          <DownloadComparison />
          <PrivacyReview />
          <WritingReview />
          <PrintDocument disabled={blocked} />
          <ReviewReport onDownload={downloadMarkdown} />
        </div>
      </div>
    </section>
  );
}
