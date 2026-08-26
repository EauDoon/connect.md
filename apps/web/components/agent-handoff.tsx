"use client";

import { useRef, useState } from "react";
import { ArrowUpRight, Check, Copy, FileText, RefreshCw, UserRound } from "lucide-react";

import { publicDiscoveryUrl, publicProtocolUrl } from "@/lib/api";

export function agentHandoffPresets(agentReadmeUrl: string) {
  return [
  {
    id: "profile",
    label: "Onboard my profile",
    shortLabel: "Profile",
    prompt:
      `Open ${agentReadmeUrl} and follow it to onboard my professional profile. If you cannot browse /agent-readme.md, ask me to paste /agent-readme.md and stop; do not infer the contract. Start by asking which CV, portfolio, work history, or existing profile I want you to use. Draft the profile and resume in canonical Markdown, flag unsupported or uncertain claims, and show me the exact content and visibility before any write. Do not publish, contact anyone, or create ongoing agent access unless I explicitly approve that separate action.`
  },
  {
    id: "resume",
    label: "Import my resume",
    shortLabel: "Resume",
    prompt:
      `Open ${agentReadmeUrl} and follow it to import my resume into connect.md. If you cannot browse /agent-readme.md, ask me to paste /agent-readme.md and stop; do not infer the contract. Ask me for the source PDF, DOCX, text, or Markdown, preserve factual meaning, and identify anything uncertain instead of inventing details. Prepare a reviewable canonical Markdown resume and profile draft. Do not publish either document or contact anyone until I explicitly approve the exact content and visibility.`
  },
  {
    id: "maintain",
    label: "Keep it current",
    shortLabel: "Maintain",
    prompt:
      `Open ${agentReadmeUrl} and follow the maintenance workflow for my existing connect.md profile and resume. If you cannot browse /agent-readme.md, ask me to paste /agent-readme.md and stop; do not infer the contract. Read the current canonical Markdown first, ask what has changed, and propose the smallest factual update with a clear diff. Do not overwrite a newer version, broaden visibility, publish, contact anyone, or request ongoing access without my explicit approval.`
  }
] as const;
}

type PresetId = ReturnType<typeof agentHandoffPresets>[number]["id"];
type CopyState = "idle" | "copied" | "blocked";

const presetIcons = {
  profile: UserRound,
  resume: FileText,
  maintain: RefreshCw
} as const;

const copyMessages: Record<Exclude<CopyState, "idle">, string> = {
  copied: "Instruction copied. Paste it into ChatGPT, Claude, OpenClaw, or another agent.",
  blocked: "Copy was blocked. Select the instruction and copy it manually."
};

export function AgentHandoff({ agentReadmeUrl }: { agentReadmeUrl: string }) {
  const presets = agentHandoffPresets(agentReadmeUrl);
  const [selectedId, setSelectedId] = useState<PresetId>("profile");
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const copyAttempt = useRef(0);
  const selected = presets.find((preset) => preset.id === selectedId) ?? presets[0];
  const statusMessage = copyState === "idle" ? `${selected.label} selected. Ready to copy.` : copyMessages[copyState];

  function selectPreset(id: PresetId) {
    copyAttempt.current += 1;
    setSelectedId(id);
    setCopyState("idle");
  }

  async function copyInstruction() {
    const attempt = copyAttempt.current + 1;
    copyAttempt.current = attempt;

    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(selected.prompt);
      if (copyAttempt.current === attempt) setCopyState("copied");
    } catch {
      if (copyAttempt.current === attempt) setCopyState("blocked");
    }
  }

  return (
    <section
      aria-labelledby="agent-handoff-title"
      className="relative min-w-0 overflow-hidden rounded-[1.75rem] border border-white/15 bg-[#0b0e12]/95 p-4 shadow-[0_32px_100px_rgba(0,0,0,.55)] sm:p-6"
    >
      <div className="pointer-events-none absolute -right-20 -top-24 size-64 rounded-full bg-acid/10 blur-3xl" aria-hidden />

      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[.22em] text-acid">Start here · give this to your agent</p>
          <h1 id="agent-handoff-title" className="mt-2 text-xl font-semibold tracking-[-.025em] text-white sm:text-2xl">
            Choose what you want done.
          </h1>
          <p className="mt-1.5 text-xs leading-5 text-mist">Works with ChatGPT, Claude, OpenClaw, and other web-capable agents.</p>
        </div>
        <a
          href={publicDiscoveryUrl("/agent-readme.md")}
          type="text/markdown"
          className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-white/15 bg-white/[.05] px-4 text-xs font-semibold text-white transition hover:border-acid/40 hover:text-acid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid motion-reduce:transition-none"
        >
          Agent README <ArrowUpRight className="size-3.5" aria-hidden />
        </a>
      </div>

      <div className="relative mt-5 grid grid-cols-3 gap-2" role="group" aria-label="Agent task presets">
        {presets.map((preset) => {
          const Icon = presetIcons[preset.id];
          const active = preset.id === selectedId;
          return (
            <button
              key={preset.id}
              type="button"
              aria-pressed={active}
              aria-controls="agent-handoff-instruction"
              onClick={() => selectPreset(preset.id)}
              className={`min-h-16 min-w-0 rounded-xl border px-2.5 py-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid motion-reduce:transition-none sm:px-3 ${
                active ? "border-acid/45 bg-acid/[.09] text-white" : "border-white/10 bg-white/[.025] text-mist hover:border-white/25 hover:text-white"
              }`}
            >
              <Icon className={`size-4 ${active ? "text-acid" : "text-mist"}`} aria-hidden />
              <span className="mt-2 block text-[11px] font-semibold leading-4 sm:hidden">{preset.shortLabel}</span>
              <span className="mt-2 hidden text-[11px] font-semibold leading-4 sm:block">{preset.label}</span>
            </button>
          );
        })}
      </div>

      <div id="agent-handoff-instruction" className="relative mt-3 overflow-hidden rounded-2xl border border-white/10 bg-black/35">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-white/10 px-4 py-2.5">
          <span id="agent-handoff-instruction-label" className="min-w-0 break-anywhere font-mono text-[10px] uppercase tracking-[.18em] text-mist">instruction.md</span>
          <span className="inline-flex min-w-0 items-center gap-1.5 break-anywhere text-[10px] font-semibold uppercase tracking-[.14em] text-acid">
            <span className="size-1.5 rounded-full bg-acid" aria-hidden /> Draft first
          </span>
        </div>
          <p aria-labelledby="agent-handoff-instruction-label" className="max-w-full min-w-0 break-anywhere select-all whitespace-pre-wrap rounded-lg p-4 font-mono text-xs leading-6 text-[#d5d9e1] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-acid focus-visible:ring-2 focus-visible:ring-acid/70 sm:p-5 sm:text-[13px]" tabIndex={0}>
          {selected.prompt}
        </p>
      </div>

      <div className="relative mt-4 flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={() => void copyInstruction()}
          className="inline-flex min-h-12 min-w-0 items-center justify-center gap-2 rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:bg-[#e5ff92] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid motion-reduce:transition-none"
        >
          {copyState === "copied" ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}
          {copyState === "copied" ? "Copied. Paste into your agent" : "Copy instruction"}
        </button>
        <p role="status" aria-live="polite" aria-atomic="true" className={`min-h-5 min-w-0 break-words text-xs leading-5 ${copyState === "blocked" ? "text-amber-200" : "text-mist"}`}>
          {statusMessage}
        </p>
      </div>

      <p className="relative mt-4 border-t border-white/10 pt-4 text-xs leading-5 text-mist">
        Your agent drafts first. Publishing, outreach, and ongoing access remain separate actions that require your explicit approval. Discovery starts at{" "}
        <a href={publicProtocolUrl("/llms.txt") ?? "/llms.txt"} type="text/plain" className="-mx-2 inline-flex min-h-11 items-center rounded-md px-2 align-middle font-mono text-white underline decoration-white/30 underline-offset-4 hover:decoration-acid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">
          /llms.txt
        </a>
        .
      </p>
    </section>
  );
}
