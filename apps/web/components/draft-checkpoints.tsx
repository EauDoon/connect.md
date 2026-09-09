"use client";

import React, { useState } from "react";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";

export function DraftCheckpoints({ onBeforeAction }: { onBeforeAction?: () => void }) {
  const { checkpoints, saveCheckpoint, restoreCheckpoint, removeCheckpoint, masked } = useDraft();
  const [label, setLabel] = useState("");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  return <details className="border-b border-white/10 px-4 py-4 sm:px-6">
    <summary className="cursor-pointer text-sm font-semibold text-white">Session checkpoints ({checkpoints.length}/5)</summary>
    <p className="mt-3 text-xs leading-5 text-mist">Keep named versions while trying edits. These copies stay only in memory, disappear on reload, and never replace a downloaded backup.</p>
    <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={(event) => {
      event.preventDefault();
      onBeforeAction?.();
      try {
        saveCheckpoint(label);
        setLabel("");
        setFailed(false);
        setMessage("Checkpoint kept in this tab.");
      } catch (error) { setFailed(true); setMessage(error instanceof Error ? error.message : "Checkpoint could not be created."); }
    }}>
      <label className="min-w-0 text-xs text-mist">Checkpoint name
        <input value={label} onChange={(event) => setLabel(event.target.value)} maxLength={60} required className="mt-1 block w-full rounded-lg border border-white/20 bg-black/20 p-2 text-sm text-white" />
      </label>
      <Button variant="secondary" disabled={masked || checkpoints.length >= 5}>Keep checkpoint</Button>
    </form>
    {message && <p role={failed ? "alert" : "status"} className="mt-3 text-xs text-mist">{message}</p>}
    <ul className="mt-3 space-y-2">
      {checkpoints.map((checkpoint) => <li key={checkpoint.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-white/10 p-3">
        <span className="min-w-0 flex-1 break-words text-sm text-white">{checkpoint.label} <span className="text-xs text-mist">({checkpoint.kind})</span></span>
        <Button variant="secondary" disabled={masked} onClick={() => {
          onBeforeAction?.();
          if (!window.confirm(`Restore “${checkpoint.label}”? The current draft will be replaced. Keep a checkpoint first if you want to retain it.`)) return;
          restoreCheckpoint(checkpoint.id);
          setFailed(false);
          setMessage(`Restored ${checkpoint.label}. The checkpoint is still available.`);
        }} aria-label={`Restore ${checkpoint.label}`}>Restore</Button>
        <Button variant="ghost" disabled={masked} onClick={() => {
          if (!window.confirm(`Remove checkpoint “${checkpoint.label}” from this tab?`)) return;
          removeCheckpoint(checkpoint.id);
          setFailed(false);
          setMessage(`Removed ${checkpoint.label}. The current draft was kept.`);
        }} aria-label={`Remove ${checkpoint.label}`}>Remove</Button>
      </li>)}
    </ul>
  </details>;
}
