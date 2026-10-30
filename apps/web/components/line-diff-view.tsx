"use client";

import React, { useMemo } from "react";
import { compareDrafts } from "@/lib/draft-comparison";
import { type DiffHunk, diffLines } from "@/lib/line-diff";

function plural(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function span(start: number, count: number) {
  if (count === 0) return `none (before line ${start})`;
  return count === 1 ? `line ${start}` : `lines ${start} to ${start + count - 1}`;
}

export function hunkHeading(hunk: DiffHunk) {
  return `Previous ${span(hunk.beforeStart, hunk.beforeCount)}; current ${span(hunk.afterStart, hunk.afterCount)}`;
}

/**
 * Multi-hunk comparison of two sources. Each change is marked with +/- and
 * visually hidden "Removed line N" or "Added line N" text, so its meaning
 * never depends on colour. Past the diff bounds it falls back to the
 * changed-region view and says so.
 */
export function LineDiffView({ before, after, label, beforeTitle, afterTitle }: { before: string; after: string; label: string; beforeTitle: string; afterTitle: string }) {
  const diff = useMemo(() => diffLines(before, after), [before, after]);
  const region = useMemo(() => diff.exact ? null : compareDrafts(before, after), [diff.exact, before, after]);
  if (diff.identical) return <p className="mt-2">The Markdown bytes match exactly.</p>;
  if (region) {
    return <div className="mt-2 min-w-0">
      <p>There are too many separate changes to list individually. Changed region starts at line {region.firstChangedLine}: {region.removedLines} previous lines, {region.addedLines} current lines. Unchanged lines between separate edits may appear here.</p>
      <div className="mt-3 grid min-w-0 gap-3 md:grid-cols-2">
        {[[beforeTitle, region.before], [afterTitle, region.after]].map(([title, text]) => <div key={title} className="min-w-0"><h4 className="font-semibold text-white">{title}</h4><pre tabIndex={0} aria-label={`${title} changed region`} className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black/20 p-3">{text || "(No lines)"}</pre></div>)}
      </div>
      {region.truncated && <p className="mt-2 text-amber-100">Display limited to 80 lines and 12,000 characters per side. Review the full sources for all changes.</p>}
    </div>;
  }
  return <div className="mt-2 min-w-0">
    <p>Changes in {plural(diff.totalHunks, "place")}: {plural(diff.addedLines, "line")} added, {diff.removedLines} removed. {beforeTitle} lines are marked -, {afterTitle.toLocaleLowerCase("en-US")} lines +.</p>
    <div role="group" aria-label={label} tabIndex={0} className="mt-3 max-h-96 min-w-0 overflow-auto rounded-lg bg-black/20 p-3 font-mono">
      {diff.hunks.map((hunk) => <section key={`${hunk.beforeStart}-${hunk.afterStart}`} aria-label={hunkHeading(hunk)} className="mt-3 first:mt-0">
        <h4 className="font-sans font-semibold text-white">{hunkHeading(hunk)}</h4>
        <ol className="mt-1">
          {hunk.lines.map((line, index) => <li key={index} className={`grid grid-cols-[5ch_5ch_1.5ch_minmax(0,1fr)] gap-x-2 ${line.type === "removed" ? "text-red-200" : line.type === "added" ? "text-acid" : ""}`}>
            <span aria-hidden className="text-right">{line.beforeLine ?? ""}</span>
            <span aria-hidden className="text-right">{line.afterLine ?? ""}</span>
            <span aria-hidden>{line.type === "removed" ? "-" : line.type === "added" ? "+" : ""}</span>
            <span className="whitespace-pre-wrap break-all">
              <span className="sr-only">{line.type === "removed" ? `Removed line ${line.beforeLine}: ` : line.type === "added" ? `Added line ${line.afterLine}: ` : `Unchanged line ${line.afterLine}: `}</span>
              {line.text || " "}
            </span>
          </li>)}
        </ol>
      </section>)}
    </div>
    {diff.truncated && <p className="mt-2 text-amber-100">Display limited to {diff.hunks.length} of {plural(diff.totalHunks, "place")}, 200 lines and 12,000 characters. Review the full sources for all changes.</p>}
  </div>;
}
