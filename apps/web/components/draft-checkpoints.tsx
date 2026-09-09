"use client";

import React, { useState } from "react";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { compareDrafts } from "@/lib/draft-comparison";

export function DraftCheckpoints({ onBeforeAction }: { onBeforeAction?: () => void }) {
  const { checkpoints, saveCheckpoint, renameCheckpoint, restoreCheckpoint, removeCheckpoint, masked, markdown, kind } = useDraft();
  const [comparisonId, setComparisonId] = useState<number | null>(null);
  const selected = checkpoints.find((entry) => entry.id === comparisonId);
  const comparison = selected ? compareDrafts(selected.markdown, markdown) : null;
  const [renaming, setRenaming] = useState<number | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [label, setLabel] = useState("");
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  return <details className="border-b border-white/10 px-4 py-4 sm:px-6">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Session checkpoints ({checkpoints.length}/5)</summary>
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
      <Button type="submit" variant="secondary" disabled={masked || checkpoints.length >= 5}>Keep checkpoint</Button>
    </form>
    {message && <p role={failed ? "alert" : "status"} className="mt-3 text-xs text-mist">{message}</p>}
    <ul className="mt-3 space-y-2">
      {checkpoints.map((checkpoint) => <li key={checkpoint.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-white/10 p-3">
        <span className="min-w-0 flex-1 break-words text-sm text-white">{checkpoint.label} <span className="text-xs text-mist">({checkpoint.kind})</span></span>
        <Button id={`rename-checkpoint-${checkpoint.id}`} variant="ghost" disabled={masked} aria-label={`Rename ${checkpoint.label}`} onClick={() => { setRenaming(checkpoint.id); setNewLabel(checkpoint.label); }}>Rename</Button>
        <Button variant="ghost" disabled={masked} onClick={() => { onBeforeAction?.(); setComparisonId(checkpoint.id); }} aria-label={`Compare ${checkpoint.label}`}>Compare</Button>
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
        {renaming === checkpoint.id && <form className="flex w-full flex-wrap items-end gap-3" onSubmit={(event) => {
          event.preventDefault();
          try {
            renameCheckpoint(checkpoint.id, newLabel); setFailed(false); setMessage("Checkpoint renamed. Its source was kept.");
            setRenaming(null); requestAnimationFrame(() => document.getElementById(`rename-checkpoint-${checkpoint.id}`)?.focus());
          } catch (error) { setFailed(true); setMessage(error instanceof Error ? error.message : "Could not rename checkpoint."); }
        }}>
          <label className="min-w-0 text-xs text-mist">New checkpoint name<input className="mt-1 block w-full rounded-lg border border-white/20 bg-black/20 p-2 text-sm text-white" value={newLabel} onChange={(event) => setNewLabel(event.target.value)} maxLength={60} required /></label>
          <Button type="submit" variant="secondary">Save checkpoint name</Button>
          <Button variant="ghost" onClick={() => { setRenaming(null); requestAnimationFrame(() => document.getElementById(`rename-checkpoint-${checkpoint.id}`)?.focus()); }}>Cancel rename</Button>
        </form>}
      </li>)}
    </ul>
    {selected && comparison && <section aria-label="Checkpoint comparison" className="mt-4 min-w-0 rounded-xl border border-white/10 p-3 text-xs leading-5 text-mist">
      <h3 className="text-sm font-semibold text-white">{selected.label} compared with the current draft</h3>
      {selected.kind !== kind && <p className="mt-2 text-amber-100">Document type changed from {selected.kind} to {kind}.</p>}
      <p className="mt-2">{comparison.identical ? "The Markdown bytes match exactly." : `Changed region starts at line ${comparison.firstChangedLine}: ${comparison.removedLines} previous lines, ${comparison.addedLines} current lines. Unchanged lines between separate edits may appear here.`}</p>
      {!comparison.identical && <div className="mt-3 grid min-w-0 gap-3 md:grid-cols-2">
        {[ ["Checkpoint", comparison.before], ["Current draft", comparison.after] ].map(([title, text]) => <div key={title} className="min-w-0"><h4 className="font-semibold text-white">{title}</h4><pre tabIndex={0} aria-label={`${title} changed region`} className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black/20 p-3">{text || "(No lines)"}</pre></div>)}
      </div>}
      {comparison.truncated && <p className="mt-2 text-amber-100">Display limited to 80 lines and 12,000 characters per side. Restore or download the full document to inspect all content.</p>}
      <Button variant="ghost" className="mt-2" onClick={() => setComparisonId(null)}>Close comparison</Button>
    </section>}
  </details>;
}
