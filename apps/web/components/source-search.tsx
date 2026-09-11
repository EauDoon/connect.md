"use client";

import React, { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { findSourceMatches, replaceSourceMatches } from "@/lib/source-search";

export function SourceSearch({ markdown, onChange, onSelect }: { markdown: string; onChange: (value: string) => void; onSelect: (start: number, end: number) => void }) {
  const [matchCase, setMatchCase] = useState(true);
  const [wholeWord, setWholeWord] = useState(false);
  const [query, setQuery] = useState("");
  const [replacement, setReplacement] = useState("");
  const [index, setIndex] = useState(-1);
  const [message, setMessage] = useState("");
  const search = useMemo(() => {
    try { return { ...findSourceMatches(markdown, query, { matchCase, wholeWord }), error: "" }; }
    catch (error) { return { matches: [], limited: false, error: error instanceof Error ? error.message : "Search unavailable." }; }
  }, [markdown, query, matchCase, wholeWord]);
  const selected = Math.min(index, search.matches.length - 1);
  function navigate(direction: number) {
    if (!search.matches.length) return;
    const next = selected < 0 ? (direction > 0 ? 0 : search.matches.length - 1) : (selected + direction + search.matches.length) % search.matches.length;
    setIndex(next); onSelect(search.matches[next], search.matches[next] + query.length);
  }
  function replace(target: number | "all") {
    try {
      if (target === "all" && !window.confirm(`Replace all ${search.matches.length} literal matches? Keep a checkpoint first if you need this version.`)) return;
      onChange(replaceSourceMatches(markdown, query, replacement, target, { matchCase, wholeWord }));
      setIndex(-1); setMessage(target === "all" ? "All matching source text replaced." : "Selected match replaced.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Replacement failed; the draft was kept."); }
  }
  return <details className="mb-4 border-b border-white/10 pb-3">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Find and replace source text</summary>
    <p className="text-xs leading-5 text-mist">Literal text with optional case and word matching. Includes frontmatter. Selecting a match opens the plain-text editor; no regular expressions are used.</p>
    <fieldset className="mt-2 flex flex-wrap gap-3 text-xs text-mist">
      <legend>Match options</legend>
      <label className="inline-flex min-h-11 items-center gap-2"><input type="checkbox" checked={matchCase} onChange={(event) => { setMatchCase(event.target.checked); setIndex(-1); }} />Match case</label>
      <label className="inline-flex min-h-11 items-center gap-2"><input type="checkbox" checked={wholeWord} onChange={(event) => { setWholeWord(event.target.checked); setIndex(-1); }} />Whole words</label>
    </fieldset>
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <label className="text-xs text-mist">Find text<input value={query} maxLength={256} onChange={(event) => { setQuery(event.target.value); setIndex(-1); setMessage(""); }} className="mt-1 block w-full rounded-lg border border-white/20 bg-black/20 p-2 text-sm text-white" /></label>
      <label className="text-xs text-mist">Replace with<input value={replacement} maxLength={1024} onChange={(event) => setReplacement(event.target.value)} className="mt-1 block w-full rounded-lg border border-white/20 bg-black/20 p-2 text-sm text-white" /></label>
    </div>
    <p role="status" className="mt-2 text-xs text-mist">{search.error || `${selected < 0 ? 0 : selected + 1} of ${search.matches.length}${search.limited ? "+" : ""} matches`}</p>
    <div className="mt-2 flex flex-wrap gap-2">
      <Button variant="ghost" disabled={!search.matches.length} onClick={() => navigate(-1)}>Previous match</Button>
      <Button variant="ghost" disabled={!search.matches.length} onClick={() => navigate(1)}>Next match</Button>
      <Button variant="secondary" disabled={selected < 0} onClick={() => replace(selected)}>Replace match</Button>
      <Button variant="secondary" disabled={!search.matches.length || search.limited} onClick={() => replace("all")}>Replace all matches</Button>
    </div>
    {message && <p role="status" className="mt-2 text-xs text-mist">{message}</p>}
  </details>;
}
