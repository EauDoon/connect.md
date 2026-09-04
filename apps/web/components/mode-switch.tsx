"use client";

import Link from "next/link";
import { Braces, Sparkles } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import React from "react";

import { useDraft } from "@/components/draft-provider";
import { LocalMarkdownFileOpen } from "@/components/local-markdown-file-open";
import { cn } from "@/lib/utils";

const editingModes = [
  { id: "human" as const, href: "/human", label: "Guided", icon: Sparkles },
  { id: "md" as const, href: "/md", label: "Markdown", icon: Braces }
];

export function ModeSwitch({ mode, onBeforeNavigate }: { mode: "human" | "md"; onBeforeNavigate?: () => void }) {
  const { markdown } = useDraft();
  const reducedMotion = useReducedMotion();
  const draftSize = new TextEncoder().encode(markdown).length;
  return (
    <div className="flex flex-col gap-3 border-b border-white/10 bg-[linear-gradient(105deg,rgba(215,255,95,.045),transparent_48%)] px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
      <div className="min-w-0">
        <p className="text-[11px] font-bold uppercase tracking-[.14em] text-mist/75">One document · two control surfaces</p>
        <nav className="mt-2 inline-flex w-full rounded-xl border border-white/10 bg-black/25 p-1 sm:w-auto" aria-label="Editing mode. Switching views keeps the current canonical draft.">
          {editingModes.map((item) => {
            const active = item.id === mode;
            const Icon = item.icon;
            return <Link key={item.id} aria-current={active ? "page" : undefined} href={item.href} onClick={onBeforeNavigate} className={cn("relative isolate inline-flex min-h-11 min-w-0 flex-1 items-center justify-center gap-2 rounded-lg px-3 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid sm:min-w-32", active ? "text-ink" : "text-mist hover:bg-white/[.04] hover:text-white")}>
              {active && <motion.span layoutId="editing-mode-active-rail" aria-hidden className="absolute inset-0 -z-10 rounded-lg bg-acid shadow-[0_0_24px_rgba(215,255,95,.16)]" initial={false} transition={{ duration: reducedMotion ? 0 : 0.22, ease: "easeOut" }} />}
              <Icon className="size-4 shrink-0" aria-hidden /> <span className="whitespace-nowrap">{item.label}</span>
            </Link>;
          })}
        </nav>
      </div>
      <div className="flex w-full min-w-0 flex-col gap-2 sm:w-auto sm:items-end">
        <p className="shrink-0 text-xs text-mist"><span className="font-semibold text-white">Canonical buffer connected</span><span aria-hidden> · </span><span>{draftSize.toLocaleString()} bytes</span></p>
        <LocalMarkdownFileOpen />
      </div>
    </div>
  );
}
