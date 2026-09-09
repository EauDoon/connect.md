"use client";

import React, { useState } from "react";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { copyLocalMarkdown } from "@/lib/local-clipboard";

export function CopyMarkdown() {
  const { markdown, masked } = useDraft();
  const [pending, setPending] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState("");
  return <div className="mt-4 border-t border-white/10 pt-3">
    <Button variant="secondary" disabled={masked || pending} onClick={async () => {
      setPending(true);
      setError("");
      try { await copyLocalMarkdown(markdown); setCopied(markdown); }
      catch (failure) { setError(failure instanceof Error ? failure.message : "Copy failed. Download the .md file instead."); }
      finally { setPending(false); }
    }}>{pending ? "Copying Markdown" : "Copy Markdown"}</Button>
    <p className="mt-2 text-xs leading-5 text-mist">Copies the exact draft, including frontmatter and any validation errors, to your system clipboard. A clipboard copy is not a saved file.</p>
    {error ? <p role="alert" className="mt-2 text-xs text-amber-100">{error}</p> : copied !== null && <p role="status" className="mt-2 text-xs text-acid">{copied === markdown ? "Current Markdown copied. Paste only where you intend to share it." : "The draft changed after copying. Copy again for the current bytes."}</p>}
  </div>;
}
