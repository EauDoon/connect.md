import Link from "next/link";
import {
  ArrowRight,
  Braces,
  Check,
  Download,
  FileText,
  LockKeyhole,
  Sparkles,
} from "lucide-react";

import { AgentHandoff } from "@/components/agent-handoff";
import { absoluteSiteUrl } from "@/lib/public-document";

const proofPoints = [
  ["Private in this tab", "Your draft stays in browser memory until you download it."],
  ["Markdown stays portable", "The guided form and direct editor share one plain-text file."],
  ["No account required", "Create, validate, preview, and download without signing in."],
] as const;

const steps = [
  ["01", "Choose", "Start with a profile or resume, in guided or direct Markdown mode."],
  ["02", "Shape", "Add only facts you can support and review the sanitized preview."],
  ["03", "Download", "Save the validated .md file to your device. Nothing is uploaded."],
] as const;

const paths = [
  {
    href: "/human",
    title: "Guided builder",
    description: "Turn familiar form fields into structured Markdown without learning the syntax.",
    action: "Start guided",
    icon: Sparkles,
  },
  {
    href: "/md",
    title: "Markdown editor",
    description: "Edit every byte directly with validation and a live sanitized preview.",
    action: "Open editor",
    icon: Braces,
  },
  {
    href: "/agent-readme.md",
    title: "Agent handoff",
    description: "Give any capable agent a bounded runbook for preparing your local draft.",
    action: "Read the runbook",
    icon: FileText,
  },
  {
    href: "/trust",
    title: "Privacy model",
    description: "See exactly what the Vercel site serves and what never leaves your browser.",
    action: "Inspect the boundary",
    icon: LockKeyhole,
  },
] as const;

export default function HomePage() {
  return (
    <main className="overflow-hidden">
      <section className="relative border-b border-white/10">
        <div
          className="absolute inset-0 -z-20 bg-[radial-gradient(circle_at_18%_8%,rgba(216,255,114,.14),transparent_29%),radial-gradient(circle_at_86%_24%,rgba(103,125,255,.11),transparent_26%)]"
          aria-hidden
        />
        <div className="absolute inset-0 -z-10 bg-grid bg-[size:54px_54px] opacity-20 [mask-image:linear-gradient(to_bottom,black,transparent_92%)]" aria-hidden />

        <div className="mx-auto grid min-h-[calc(100vh-4rem)] max-w-7xl items-center gap-10 px-5 py-10 lg:grid-cols-[.82fr_1.18fr] lg:gap-14 lg:px-8 lg:py-16">
          <div className="order-1 min-w-0 lg:order-2">
            <AgentHandoff agentReadmeUrl={absoluteSiteUrl("/agent-readme.md")} />
          </div>

          <div className="order-2 min-w-0 max-w-xl lg:order-1">
            <p className="eyebrow">Private by default · built for Vercel</p>
            <h2 className="mt-5 font-display text-5xl font-semibold leading-[.93] tracking-[-.06em] text-white sm:text-6xl lg:text-7xl">
              Your work story.
              <span className="block text-acid">One portable file.</span>
            </h2>
            <p className="mt-6 max-w-lg text-base leading-7 text-mist sm:text-lg sm:leading-8">
              Build a polished professional profile or resume in your browser, review the exact Markdown, and download it. No account, database, or upload required.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-3 text-xs font-semibold text-white/80">
              <span className="inline-flex items-center gap-2"><Check className="size-3.5 text-acid" aria-hidden /> Browser-only draft</span>
              <span className="inline-flex items-center gap-2"><Check className="size-3.5 text-acid" aria-hidden /> Local download</span>
              <span className="inline-flex items-center gap-2"><Check className="size-3.5 text-acid" aria-hidden /> Open Markdown</span>
            </div>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/human" className="inline-flex min-h-12 items-center gap-2 rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:bg-[#e5ff92] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid motion-reduce:transition-none">
                <Sparkles className="size-4" aria-hidden /> Build my profile
              </Link>
              <Link href="/md" className="inline-flex min-h-12 items-center gap-2 rounded-full border border-white/15 bg-white/[.04] px-5 text-sm font-semibold text-white transition hover:border-white/30 hover:bg-white/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid motion-reduce:transition-none">
                <Braces className="size-4" aria-hidden /> Edit Markdown
              </Link>
            </div>
          </div>
        </div>
      </section>

      <section aria-label="Site guarantees" className="border-b border-white/10 bg-white/[.025]">
        <div className="mx-auto grid max-w-7xl gap-px bg-white/10 sm:grid-cols-3">
          {proofPoints.map(([title, description]) => (
            <div key={title} className="bg-ink px-5 py-7 lg:px-8">
              <p className="text-sm font-semibold text-white">{title}</p>
              <p className="mt-2 text-xs leading-5 text-mist">{description}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-10 px-5 py-16 lg:grid-cols-[.76fr_1.24fr] lg:px-8 lg:py-24">
        <div>
          <p className="eyebrow">A complete local workflow</p>
          <h2 className="mt-4 max-w-md font-display text-4xl font-semibold leading-[1.02] tracking-[-.05em] text-white sm:text-5xl">From real facts to a file you own.</h2>
          <p className="mt-5 max-w-md text-sm leading-7 text-mist">A <code className="font-mono text-white">.md</code> file is portable plain text. Keep it in any folder, version it with Git, share it deliberately, or give it to an agent later.</p>
        </div>
        <ol className="grid gap-3 sm:grid-cols-3">
          {steps.map(([number, title, description]) => (
            <li key={number} className="rounded-[1.4rem] border border-white/10 bg-panel p-5">
              <span className="font-mono text-xs text-acid">{number}</span>
              <h3 className="mt-8 text-xl font-semibold text-white">{title}</h3>
              <p className="mt-3 text-sm leading-6 text-mist">{description}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="border-y border-white/10 bg-[linear-gradient(135deg,rgba(216,255,114,.055),transparent_42%)] px-5 py-14">
        <div className="mx-auto flex max-w-7xl flex-col justify-between gap-8 lg:flex-row lg:items-center">
          <div>
            <p className="eyebrow">No lock-in</p>
            <h2 className="mt-3 max-w-2xl font-display text-3xl font-semibold tracking-[-.04em] text-white sm:text-4xl">Your final artifact is the file—not an account.</h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-mist">The site validates and previews locally, then hands the Markdown back to you.</p>
          </div>
          <Link href="/human" className="inline-flex min-h-12 items-center justify-center gap-2 rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:bg-[#e5ff92] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid motion-reduce:transition-none">
            <Download className="size-4" aria-hidden /> Create and download
          </Link>
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-4 px-5 py-12 sm:grid-cols-2 lg:grid-cols-4 lg:px-8">
        {paths.map(({ href, title, description, action, icon: Icon }) => (
          <Link key={href} href={href} className="group rounded-2xl border border-white/10 bg-white/[.025] p-5 transition hover:border-white/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid motion-reduce:transition-none">
            <Icon className="size-5 text-acid" aria-hidden />
            <h2 className="mt-7 font-semibold text-white">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-mist">{description}</p>
            <span className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-acid">{action}<ArrowRight className="size-4 transition group-hover:translate-x-1 motion-reduce:transform-none" aria-hidden /></span>
          </Link>
        ))}
      </section>

      <footer className="border-t border-white/10 px-5 py-8 text-center text-xs text-mist">
        <p>connect.md turns professional facts into portable Markdown—entirely in your browser.</p>
      </footer>
    </main>
  );
}
