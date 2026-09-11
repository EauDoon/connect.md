"use client";

import React, { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { sourceLineRange } from "@/lib/source-line";
import { documentMetrics } from "@/lib/document-metrics";

export function DocumentOutline({ markdown, onSelect }: { markdown: string; onSelect: (offset: number) => void }) {
  const [line, setLine] = useState("1");
  const [error, setError] = useState("");
  const metrics = useMemo(() => documentMetrics(markdown), [markdown]);
  return <details className="mb-4 rounded-xl border border-white/10 p-3">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Document outline and length</summary>
    <p className="mt-3 text-xs leading-5 text-mist">{metrics.bytes.toLocaleString()} UTF-8 bytes of 131,072 allowed. {metrics.limited ? "Reduce the draft size to inspect its structure." : `About ${metrics.words.toLocaleString()} body words, ${metrics.readingMinutes} minute${metrics.readingMinutes === 1 ? "" : "s"} to read at 200 words per minute. Counts are estimates, especially for languages without spaces.`}</p>
    <p className="mt-2 text-xs leading-5 text-mist">Choose a heading to select its source in the plain-text editor. Frontmatter, fenced code, and comments are excluded from the outline.</p>
    {metrics.headings.length === 0 && !metrics.limited && <p className="mt-2 text-xs text-mist">No supported Markdown headings found.</p>}
    <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(event) => {
      event.preventDefault();
      const range = sourceLineRange(markdown, Number(line));
      if (!range) { setError("Choose an existing source line."); return; }
      setError(""); onSelect(range.start);
    }}>
      <label className="text-xs text-mist">Source line<input type="number" min={1} step={1} required value={line} onChange={(event) => setLine(event.target.value)} className="mt-1 block w-28 rounded-lg border border-white/20 bg-black/20 p-2 text-sm text-white" /></label>
      <Button type="submit" variant="secondary" disabled={metrics.limited}>Go to line</Button>
    </form>
    {error && <p role="alert" className="mt-2 text-xs text-amber-100">{error}</p>}
    <nav aria-label="Document outline" className="mt-2 max-h-60 overflow-auto">
      <ol>{metrics.headings.slice(0, 60).map((heading) => <li key={heading.start}>
        <button type="button" onClick={() => onSelect(heading.start)} aria-label={`Edit ${heading.text.slice(0, 120)} at line ${heading.line}`} className="min-h-11 w-full break-words rounded-lg px-2 py-1 text-left text-xs text-mist hover:bg-white/5 hover:text-white">
          <span className="mr-2 font-mono text-acid">H{heading.level} · {heading.line}</span>{heading.text.slice(0, 120)}
        </button>
      </li>)}</ol>
    </nav>
    {metrics.headings.length > 60 && <p className="mt-2 text-xs text-mist">Showing the first 60 of {metrics.headings.length} headings.</p>}
  </details>;
}
