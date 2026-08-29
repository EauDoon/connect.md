"use client";

import { useState } from "react";
import { Download, LockKeyhole } from "lucide-react";

import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { documentIdentifier, type DocumentKind } from "@/lib/markdown";
import { hasValidationErrors, type ValidationIssue } from "@/lib/validation";

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
  const { kind, markdown, masked } = useDraft();
  const [message, setMessage] = useState("");
  const blocked = masked || hasValidationErrors(issues);

  function download() {
    if (blocked) return;
    const filename = markdownDownloadName(kind, documentIdentifier(markdown, kind));
    downloadMarkdown(markdown, filename);
    setMessage(`${filename} downloaded. The draft was not uploaded.`);
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
          <Button className="mt-4 w-full sm:w-auto" onClick={download} disabled={blocked} aria-describedby={message ? "download-status" : undefined}>
            <Download className="size-4" aria-hidden /> Download {kind} .md
          </Button>
          {blocked && <p className="mt-3 text-xs leading-5 text-amber-100">Resolve the validation errors above before downloading.</p>}
          {message && <p id="download-status" role="status" aria-live="polite" className="mt-3 text-sm text-acid">{message}</p>}
          <p className="mt-3 text-xs leading-5 text-mist/75">The frontmatter visibility field is metadata only in this standalone site.</p>
        </div>
      </div>
    </section>
  );
}
