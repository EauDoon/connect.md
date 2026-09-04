"use client";

import React, { useState } from "react";
import { CheckCircle2, Download, LockKeyhole, TriangleAlert } from "lucide-react";

import { useDraft, type LocalDownloadReceipt } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
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
  const blocked = masked || hasValidationErrors(issues);
  const freshness = localDownloadFreshness(localDownloadReceipt, kind, markdown);
  const downloadDescriptionIds = [blocked ? "download-blocked" : "", freshness !== "none" ? "download-status" : ""].filter(Boolean).join(" ") || undefined;

  function download() {
    if (blocked) return;
    const filename = markdownDownloadName(kind, documentIdentifier(markdown, kind));
    downloadMarkdown(markdown, filename);
    recordLocalDownload(filename);
    setDownloadAnnouncement((current) => current + 1);
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
          <Button className="mt-4 w-full sm:w-auto" onClick={download} disabled={blocked} aria-describedby={downloadDescriptionIds}>
            <Download className="size-4" aria-hidden /> Download {freshness === "stale" ? "updated " : ""}{kind} .md
          </Button>
          {blocked && <p id="download-blocked" className="mt-3 text-xs leading-5 text-amber-100">Resolve the validation errors above before downloading.</p>}
          {freshness === "current" && <p key={`current-${downloadAnnouncement}`} id="download-status" role="status" aria-live="polite" className="mt-3 flex gap-2 text-sm leading-5 text-acid"><CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />{localDownloadReceipt?.filename} downloaded. The current draft matches that local file; nothing was uploaded.</p>}
          {freshness === "stale" && <p id="download-status" role="status" aria-live="polite" className="mt-3 flex gap-2 text-sm leading-5 text-amber-100"><TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />The current draft no longer matches the last downloaded file. Download it again to keep that copy current.</p>}
          <p className="mt-3 text-xs leading-5 text-mist/75">The frontmatter visibility field is metadata only in this standalone site.</p>
        </div>
      </div>
    </section>
  );
}
