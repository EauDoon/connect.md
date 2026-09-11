"use client";

import React, { useRef, useState } from "react";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { downloadMarkdown } from "@/components/publish-panel";
import { PROFILE_RESUME_MAX_UTF8_BYTES } from "@/lib/markdown";
import { encodeRecoveryBundle, parseRecoveryBundle, RECOVERY_MAX_BYTES, type RecoveryBundle } from "@/lib/session-recovery";

export function SessionRecovery({ onBeforeAction }: { onBeforeAction?: () => void }) {
  const { checkpoints, getDraftSnapshot, restoreRecovery, previousDraft, undoReplacement, discardUndo, masked } = useDraft();
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState<{ bundle: RecoveryBundle; revision: number; lineage: number; checkpointGeneration: number } | null>(null);
  const request = useRef(0);
  return <details className="border-b border-white/10 px-4 py-4 sm:px-6">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Session recovery</summary>
    <p className="mt-2 text-xs leading-5 text-mist">Keep a local recovery file with the current draft and named checkpoints, including unfinished or invalid Markdown. It contains document text: store it privately. Nothing is saved automatically or uploaded.</p>
    {previousDraft && <div className="mt-3 text-xs leading-5 text-mist">
      <p>The source from before the latest replacement is still in memory. Undo restores that source only, not a previous checkpoint collection or server association.</p>
      <div className="mt-2 flex flex-wrap gap-3">
        <Button variant="secondary" onClick={() => {
          onBeforeAction?.();
          if (!window.confirm("Undo the latest draft replacement? Edits made since that replacement will be replaced too.")) return;
          undoReplacement(); setFailed(false); setMessage("Previous draft restored. Checkpoints were kept.");
        }}>Undo draft replacement</Button>
        <Button variant="ghost" onClick={() => {
          if (window.confirm("Forget the previous draft source? The current draft and checkpoints will stay.")) discardUndo();
        }}>Forget previous draft</Button>
      </div>
    </div>}
    <Button className="mt-3" variant="secondary" disabled={masked} onClick={() => {
      onBeforeAction?.();
      try {
        const snapshot = getDraftSnapshot();
        if (!snapshot) throw new Error("The current draft is unavailable.");
        const encoded = encodeRecoveryBundle({ kind: snapshot.kind, markdown: snapshot.markdown }, checkpoints);
        downloadMarkdown(encoded, "connectmd-session.recovery.json");
        setFailed(false);
        setMessage("Recovery file download requested. Keep the file before closing this tab. This does not mark the draft as validated or downloaded Markdown.");
      } catch (error) { setFailed(true); setMessage(error instanceof Error ? error.message : "Recovery download could not start. Keep this tab open and copy your source."); }
    }}>Download session recovery</Button>
    <p className="mt-3 text-xs leading-5 text-mist">Need a plain file while fixing validation errors? Download the exact unfinished source below. It contains the full draft, has no validation guarantee, and does not include checkpoints. Paste its contents into the source editor to continue; validated file import may reject it.</p>
    <Button className="mt-2" variant="secondary" disabled={masked} onClick={() => {
      onBeforeAction?.();
      try {
        const snapshot = getDraftSnapshot();
        if (!snapshot) throw new Error("The current draft is unavailable.");
        if (new TextEncoder().encode(snapshot.markdown).length > PROFILE_RESUME_MAX_UTF8_BYTES) throw new Error("Unfinished source download is limited to 128 KiB. Copy the source before reducing its size.");
        downloadMarkdown(snapshot.markdown, `connectmd-${snapshot.kind}-unfinished.md`);
        setFailed(false); setMessage("Unfinished source download requested. This is not a validated document and does not clear the reload warning.");
      } catch (error) { setFailed(true); setMessage(error instanceof Error ? error.message : "Source download could not start. Keep this tab open and copy the source."); }
    }}>Download unfinished source</Button>
    <label className="mt-4 block text-xs text-mist">Open a recovery file
      <input type="file" accept=".json,application/json" disabled={masked} className="mt-2 block min-h-11 w-full min-w-0 text-sm" onChange={async (event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        const ticket = ++request.current;
        setPending(null);
        if (!file) return;
        onBeforeAction?.();
        const snapshot = getDraftSnapshot();
        try {
          if (!snapshot) throw new Error("The current draft is unavailable.");
          if (file.size > RECOVERY_MAX_BYTES) throw new Error("The recovery file exceeds the 8 MiB limit.");
          const source = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
          const bundle = parseRecoveryBundle(source);
          if (ticket !== request.current) return;
          setPending({ bundle, revision: snapshot.revision, lineage: snapshot.lineage, checkpointGeneration: snapshot.checkpointGeneration });
          setFailed(false);
          setMessage("Recovery file read locally. Review its contents below before replacing this session.");
        } catch (error) {
          if (ticket !== request.current) return;
          setFailed(true); setMessage(error instanceof Error ? error.message : "The recovery file could not be read. Your session was kept.");
        }
      }} />
    </label>
    {pending && <section aria-label="Recovery file review" className="mt-3 text-sm text-mist">
      <p>{pending.bundle.draft.kind} draft, {new TextEncoder().encode(pending.bundle.draft.markdown).length.toLocaleString()} bytes, {pending.bundle.checkpoints.length} checkpoints.</p>
      <ul className="mt-2">{pending.bundle.checkpoints.map((entry) => <li className="break-words" key={entry.label}>{entry.label} ({entry.kind})</li>)}</ul>
      <div className="mt-3 flex flex-wrap gap-3">
        <Button variant="secondary" disabled={masked} onClick={() => {
          onBeforeAction?.();
          const current = getDraftSnapshot();
          if (!current || current.revision !== pending.revision || current.lineage !== pending.lineage || current.checkpointGeneration !== pending.checkpointGeneration) {
            const reason = current && current.checkpointGeneration !== pending.checkpointGeneration ? "The checkpoints changed" : "The draft changed";
            setPending(null); setFailed(true); setMessage(`${reason} after opening this file. Open it again to review the replacement.`); return;
          }
          if (!window.confirm("Replace the current draft and all session checkpoints with this recovery file? Download a recovery backup first if you need to keep this session.")) return;
          restoreRecovery(pending.bundle);
          setPending(null); setFailed(false); setMessage("Session restored locally. Review and validate the source before sharing it.");
        }}>Restore recovery session</Button>
        <Button variant="ghost" onClick={() => { setPending(null); setMessage("Recovery cancelled. Your current session was kept."); }}>Cancel recovery</Button>
      </div>
    </section>}
    {message && <p className="mt-3 text-xs leading-5 text-mist" role={failed ? "alert" : "status"}>{message}</p>}
  </details>;
}
