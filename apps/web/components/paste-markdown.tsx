"use client";

import React, { useState } from "react";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { shouldConfirmDraftReplacement } from "@/lib/draft-replacement";
import { LOCAL_MARKDOWN_FILE_MAX_BYTES, parseLocalMarkdownDraft } from "@/lib/local-markdown-file";

export function PasteMarkdown({ onBeforeAction }: { onBeforeAction?: () => void }) {
  const { getDraftSnapshot, replaceDraft, masked } = useDraft();
  const [source, setSource] = useState("");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  return <details className="border-b border-white/10 px-4 py-4 sm:px-6">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Paste a complete Markdown draft</summary>
    <p className="mt-3 text-xs leading-5 text-mist">Paste a profile or resume from your own editor or agent. Include the YAML frontmatter. This reads no clipboard automatically and sends nothing to a server.</p>
    <form className="mt-3" onSubmit={(event) => {
      event.preventDefault();
      onBeforeAction?.();
      try {
        const imported = parseLocalMarkdownDraft(source);
        const current = getDraftSnapshot();
        if (!current) throw new Error("The current draft is unavailable.");
        if (shouldConfirmDraftReplacement(current.markdown, current.kind, current.savedDocument)
          && !window.confirm("Replace the current draft with this pasted Markdown? Keep a checkpoint or download first to retain your current work.")) {
          setFailed(false); setMessage("The current draft was kept."); return;
        }
        replaceDraft(imported.kind, imported.markdown);
        setSource("");
        setFailed(false);
        setMessage(`Opened pasted ${imported.kind} locally. Review validation before downloading.`);
      } catch (error) { setFailed(true); setMessage(error instanceof Error ? error.message : "The pasted draft could not be opened."); }
    }}>
      <label className="text-xs text-mist">Markdown to import
        <textarea value={source} onChange={(event) => { setSource(event.target.value); setMessage(""); }} required maxLength={LOCAL_MARKDOWN_FILE_MAX_BYTES} rows={8} spellCheck={false} className="mt-2 block w-full rounded-xl border border-white/20 bg-black/20 p-3 font-mono text-sm text-white" />
      </label>
      <div className="mt-3 flex flex-wrap gap-2"><Button type="submit" disabled={masked || !source.trim()}>Open pasted Markdown</Button><Button type="button" variant="ghost" onClick={() => { setSource(""); setMessage(""); }}>Clear pasted text</Button></div>
    </form>
    {message && <p role={failed ? "alert" : "status"} className="mt-3 text-xs text-mist">{message}</p>}
  </details>;
}
