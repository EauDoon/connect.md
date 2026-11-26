"use client";

import React, { useMemo, useState } from "react";
import { downloadMarkdown, preferredMarkdownName } from "@/components/publish-panel";
import { Button } from "@/components/ui/button";
import { sourceLineRange } from "@/lib/source-line";
import { documentMetrics, filterOutline, sectionExcerpt } from "@/lib/document-metrics";

const pageSize = 60;

export function DocumentOutline({ markdown, onSelect }: { markdown: string; onSelect: (start: number, end: number) => void }) {
  const [excerpt, setExcerpt] = useState<{ title: string; markdown: string; source: string; offset: number; includeChildren: boolean } | null>(null);
  const [excerptPage, setExcerptPage] = useState(0);
  const excerptPages = useMemo(() => excerpt?.markdown.match(/[\s\S]{1,12000}/gu) ?? [""], [excerpt]);
  const [exportMessage, setExportMessage] = useState("");
  const [filter, setFilter] = useState("");
  const [level, setLevel] = useState(0);
  const [page, setPage] = useState(0);
  const [line, setLine] = useState("1");
  const [error, setError] = useState("");
  const metrics = useMemo(() => documentMetrics(markdown), [markdown]);
  const headings = filterOutline(metrics.headings, filter).filter((heading) => !level || heading.level === level);
  const lastPage = Math.max(0, Math.ceil(headings.length / pageSize) - 1);
  const visiblePage = Math.min(page, lastPage);
  const start = visiblePage * pageSize;
  function reviewExcerpt(offset: number, includeChildren = true) {
    try {
      setExcerpt({ ...sectionExcerpt(markdown, offset, includeChildren), source: markdown, offset, includeChildren });
      setExcerptPage(0);
      setExportMessage("");
      setError("");
    } catch {
      setError("Section could not be selected. Choose a current heading.");
    }
  }
  return <details className="mb-4 rounded-xl border border-white/10 p-3">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Document outline and length</summary>
    <p className="mt-3 text-xs leading-5 text-mist">{metrics.bytes.toLocaleString()} UTF-8 bytes of 131,072 allowed. {metrics.limited ? "Reduce the draft size to inspect its structure." : `About ${metrics.words.toLocaleString()} body words, ${metrics.readingMinutes} minute${metrics.readingMinutes === 1 ? "" : "s"} to read at 200 words per minute. Counts are estimates, especially for languages without spaces.`}</p>
    <p className="mt-2 text-xs leading-5 text-mist">Choose a heading to select its source in the plain-text editor. Frontmatter, fenced code, and comments are excluded from the outline.</p>
    {metrics.headings.length === 0 && !metrics.limited && <p className="mt-2 text-xs text-mist">No supported Markdown headings found.</p>}
    <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(event) => {
      event.preventDefault();
      const range = sourceLineRange(markdown, Number(line));
      if (!range) { setError("Choose an existing source line."); return; }
      setError(""); onSelect(range.start, range.end);
    }}>
      <label className="text-xs text-mist">Source line<input type="number" min={1} step={1} required value={line} onChange={(event) => setLine(event.target.value)} className="mt-1 block w-28 rounded-lg border border-white/20 bg-black/20 p-2 text-sm text-white" /></label>
      <Button type="submit" variant="secondary" disabled={metrics.limited}>Go to line</Button>
    </form>
    {error && <p role="alert" className="mt-2 text-xs text-amber-100">{error}</p>}
    <label className="mt-3 block text-xs text-mist">Filter headings<input type="search" maxLength={256} value={filter} onChange={(event) => { setFilter(event.target.value); setPage(0); }} className="mt-1 block w-full rounded-lg border border-white/20 bg-black/20 p-2 text-sm text-white" /></label>
    <label className="mt-3 block text-xs text-mist">Heading level
      <select value={level} onChange={(event) => { setLevel(Number(event.target.value)); setPage(0); }} className="ml-2 min-h-11 rounded-lg bg-panel px-2 text-white">
        <option value={0}>All levels</option>
        {[1, 2, 3, 4, 5, 6].map((depth) => <option key={depth} value={depth}>H{depth}</option>)}
      </select>
    </label>
    {(filter || level > 0) && <p role="status" className="mt-2 text-xs text-mist">{headings.length} matching headings. <button type="button" className="min-h-11 underline" onClick={() => { setFilter(""); setLevel(0); setPage(0); }}>Clear heading filter</button></p>}
    <nav aria-label="Document outline" className="mt-2 max-h-60 overflow-auto">
      <ol>{headings.slice(start, start + pageSize).map((heading) => <li key={heading.start}>
        <button type="button" onClick={() => onSelect(heading.start, heading.end)} aria-label={`Edit ${heading.text.slice(0, 120)} at line ${heading.line}`} className="min-h-11 w-full break-words rounded-lg px-2 py-1 text-left text-xs text-mist hover:bg-white/5 hover:text-white">
          <span className="mr-2 font-mono text-acid">H{heading.level} · {heading.line}</span>{heading.text.slice(0, 120)}
        </button>
        <Button variant="ghost" aria-label={`Review excerpt ${heading.text.slice(0, 120)} at line ${heading.line}`} onClick={() => reviewExcerpt(heading.start)}>Review excerpt</Button>
      </li>)}</ol>
    </nav>
    {excerpt && <section aria-label="Section excerpt review" className="mt-3 text-xs text-mist">
      <h3 className="font-semibold text-white">{excerpt.title}</h3>
      <p className="mt-2">Body section{excerpt.includeChildren ? " and child sections" : " without child sections"} only. This excerpt excludes frontmatter and is not a complete validated profile or resume. Review sensitive content before sharing.</p>
      <label className="inline-flex min-h-11 items-center gap-2"><input type="checkbox" checked={excerpt.includeChildren} disabled={excerpt.source !== markdown} onChange={(event) => reviewExcerpt(excerpt.offset, event.target.checked)} />Include child sections</label>
      <pre tabIndex={0} className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all bg-black/20 p-2">{excerptPages[excerptPage]}</pre>
      {excerptPages.length > 1 && <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" disabled={excerptPage === 0} onClick={() => setExcerptPage(excerptPage - 1)}>Previous excerpt page</Button>
        <p role="status">Excerpt page {excerptPage + 1} of {excerptPages.length}</p>
        <Button variant="ghost" disabled={excerptPage + 1 === excerptPages.length} onClick={() => setExcerptPage(excerptPage + 1)}>Next excerpt page</Button>
      </div>}
      {excerpt.source !== markdown && <p role="alert">The draft changed. Select the excerpt again before downloading.</p>}
      <div className="mt-2 flex flex-wrap gap-2"><Button variant="secondary" disabled={excerpt.source !== markdown} onClick={() => {
        if (excerpt.source !== markdown) return;
        try { downloadMarkdown(excerpt.markdown, preferredMarkdownName("profile", "section", `excerpt-${excerpt.title}`)); setExportMessage("Excerpt download requested. The full draft and download receipt were kept."); }
        catch { setExportMessage("Excerpt download could not start. Your draft is still here."); }
      }}>Download section excerpt</Button><Button variant="ghost" onClick={() => setExcerpt(null)}>Close excerpt</Button></div>
      {exportMessage && <p role="status" className="mt-2">{exportMessage}</p>}
    </section>}
    {lastPage > 0 && <div className="mt-2 flex flex-wrap items-center gap-2">
      <Button variant="ghost" disabled={visiblePage === 0} onClick={() => setPage(visiblePage - 1)}>Previous headings</Button>
      <p role="status" className="text-xs text-mist">Headings {start + 1}–{Math.min(start + pageSize, headings.length)} of {headings.length}</p>
      <Button variant="ghost" disabled={visiblePage === lastPage} onClick={() => setPage(visiblePage + 1)}>Next headings</Button>
    </div>}
  </details>;
}
