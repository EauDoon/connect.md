"use client";

import dynamic from "next/dynamic";
import { loader } from "@monaco-editor/react";
import { Code2, Eye, FileWarning, RotateCcw, Sparkles } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useDraft } from "@/components/draft-provider";
import { MarkdownPreview } from "@/components/markdown-preview";
import { ModeSwitch } from "@/components/mode-switch";
import { PublishPanel } from "@/components/publish-panel";
import { ValidationPanel } from "@/components/validation-panel";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { isEmptyDraft, starterFor } from "@/lib/markdown";
import { validateDraft } from "@/lib/validation";

loader.config({ paths: { vs: "/monaco/vs" } });

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => <AsyncBoundaryMessage className="grid h-[540px] place-items-center rounded-2xl border border-white/10 bg-black/25 text-sm text-mist" loading>Loading Markdown editor…</AsyncBoundaryMessage>
});

export function MarkdownEditor() {
  const { kind, markdown, replaceMarkdown, setMarkdown } = useDraft();
  const issues = useMemo(() => validateDraft(markdown, kind), [kind, markdown]);
  const emptyDraft = isEmptyDraft(markdown);

  function resetToStarter() {
    const confirmed = window.confirm("Replace the current local draft with the starter template? This cannot be undone in this browser session.");
    if (confirmed) replaceMarkdown(starterFor(kind));
  }

  return (
    <main className="relative mx-auto max-w-7xl overflow-hidden px-5 pb-12 pt-9 lg:px-8 lg:pt-14">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-64 bg-[radial-gradient(ellipse_at_72%_0%,rgba(215,255,95,.12),transparent_62%)]" />
      <p className="eyebrow">Markdown mode · direct composition</p>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-4xl font-semibold tracking-[-.045em] text-white sm:text-5xl">Edit the source. Keep the same document.</h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-mist">This is the exact canonical buffer used in Guided Mode. Edit every byte here, review the sanitized preview, then download the file locally.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3"><Link href="/human" className="inline-flex min-h-11 items-center gap-2 rounded-full px-4 text-sm font-semibold text-mist transition hover:bg-white/[.06] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"><Sparkles className="size-4 text-acid" aria-hidden /> Continue in Guided</Link><Button variant="secondary" onClick={resetToStarter}><RotateCcw className="size-4" aria-hidden /> Reset starter</Button></div>
      </div>

      <Card className="mt-8 overflow-hidden">
        <ModeSwitch mode="md" />
        <div className="grid gap-6 p-5 lg:grid-cols-[minmax(0,1.16fr)_minmax(320px,.84fr)] lg:p-6">
          <section aria-labelledby="editor-title" className="min-w-0">
            <div className="mb-3 flex min-w-0 flex-wrap items-center justify-between gap-3">
              <h2 id="editor-title" className="inline-flex min-w-0 items-center gap-2 text-sm font-semibold text-white"><Code2 className="size-4 shrink-0 text-acid" aria-hidden /> Canonical Markdown</h2>
              <span className="min-w-0 break-words text-xs text-mist">UTF-8 · LF normalized</span>
            </div>
            <div className="overflow-hidden rounded-2xl border border-white/10 bg-[#0c0e12]">
              <MonacoEditor
                height="540px"
                language="markdown"
                theme="vs-dark"
                value={markdown}
                onChange={(value) => setMarkdown(value ?? "")}
                options={{
                  minimap: { enabled: false },
                  fontSize: 14,
                  lineHeight: 22,
                  wordWrap: "on",
                  scrollBeyondLastLine: false,
                  padding: { top: 18, bottom: 18 },
                  accessibilitySupport: "on",
                  tabSize: 2
                }}
              />
            </div>
            <div className="mt-4 flex gap-2 rounded-xl border border-white/10 bg-black/20 p-3 text-xs leading-5 text-mist"><FileWarning className="mt-0.5 size-4 shrink-0 text-acid" aria-hidden /> {emptyDraft ? "This buffer is empty, so download is blocked. Paste a complete Markdown file that starts with YAML frontmatter, or use Reset starter." : "The draft lives only in this browser session until you download it. A full reload or closed tab can discard it."}</div>
          </section>

          <aside className="space-y-5 min-w-0" aria-label="Markdown status and preview">
            <ValidationPanel issues={issues} />
            <PublishPanel issues={issues} />
            <section aria-labelledby="preview-title">
              <div className="mb-3 flex min-w-0 flex-wrap items-center justify-between gap-3"><h2 id="preview-title" className="inline-flex min-w-0 items-center gap-2 text-sm font-semibold text-white"><Eye className="size-4 shrink-0 text-acid" aria-hidden /> Live preview</h2><span className="min-w-0 break-words text-xs text-mist">Sanitized</span></div>
              <div tabIndex={0} aria-label="Sanitized Markdown preview" className="max-h-[420px] overflow-auto rounded-2xl border border-white/10 bg-[#f6f7f3] p-6 text-slate-950"><MarkdownPreview markdown={markdown} className="light-preview" headingOffset={2} /></div>
            </section>
          </aside>
        </div>
      </Card>
    </main>
  );
}
