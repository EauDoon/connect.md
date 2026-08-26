import Link from "next/link";
import { ArrowRight, Bot, Braces, BriefcaseBusiness, Check, FileText, Search, ShieldCheck, Sparkles } from "lucide-react";

import { AgentHandoff } from "@/components/agent-handoff";
import { publicDiscoveryUrl } from "@/lib/api";
import { absoluteSiteUrl } from "@/lib/public-document";
import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";

export const dynamic = "force-dynamic";

const proofPoints = [
  ["Draft before publish", "Nothing in the handoff makes your work public by default."],
  ["Markdown stays canonical", "The form, editor, API, search, and public page share one source."],
  ["Authority stays bounded", "Outreach and ongoing agent access are separate, explicit decisions."]
] as const;

const onboardingSteps = [
  ["01", "Read", "Your agent opens the public README and discovers the current API contract."],
  ["02", "Draft", "It gathers your source material, marks uncertainty, and prepares canonical Markdown."],
  ["03", "Approve", "You review the exact facts and visibility before any publish request is sent."]
] as const;

export default function HomePage() {
  const agentReadmeUrl = publicDiscoveryUrl("/agent-readme.md", absoluteSiteUrl("/agent-readme.md"));
  const recruitingEnabled = recruitingReleaseEnabled();
  const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();
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
            <AgentHandoff agentReadmeUrl={agentReadmeUrl} />
          </div>

          <div className="order-2 min-w-0 max-w-xl lg:order-1">
            <p className="eyebrow">Your professional profile · agent-ready</p>
            <h2 className="mt-5 font-display text-5xl font-semibold leading-[.93] tracking-[-.06em] text-white sm:text-6xl lg:text-7xl">
              Give your agent
              <span className="block text-acid">one instruction.</span>
            </h2>
            <p className="mt-6 max-w-lg text-base leading-7 text-mist sm:text-lg sm:leading-8">
              It reads the connect.md onboarding contract, turns your real work history into structured Markdown, and brings the draft back to you before anything becomes public.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-3 text-xs font-semibold text-white/80">
              <span className="inline-flex items-center gap-2"><Check className="size-3.5 text-acid" aria-hidden /> No invented claims</span>
              <span className="inline-flex items-center gap-2"><Check className="size-3.5 text-acid" aria-hidden /> You approve visibility</span>
              <span className="inline-flex items-center gap-2"><Check className="size-3.5 text-acid" aria-hidden /> One Markdown source</span>
            </div>
          </div>
        </div>
      </section>

      <section aria-label="Platform guarantees" className="border-b border-white/10 bg-white/[.025]">
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
          <p className="eyebrow">A legible handoff</p>
          <h2 className="mt-4 max-w-md font-display text-4xl font-semibold leading-[1.02] tracking-[-.05em] text-white sm:text-5xl">From source material to a profile you control.</h2>
          <p className="mt-5 max-w-md text-sm leading-7 text-mist">The README gives an agent the route map and safety rules. A <code className="font-mono text-white">.md</code> file is portable plain-text Markdown, readable by people and agents. You remain the authority for facts, visibility, publication, and access.</p>
        </div>
        <ol className="grid gap-3 sm:grid-cols-3">
          {onboardingSteps.map(([number, title, description]) => (
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
            <p className="eyebrow">Prefer to work directly?</p>
            <h2 className="mt-3 max-w-2xl font-display text-3xl font-semibold tracking-[-.04em] text-white sm:text-4xl">Build it with guidance or edit every byte.</h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-mist">Both paths produce the same canonical Markdown. Neither publishes without an explicit action.</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link href="/human" className="inline-flex min-h-12 items-center gap-2 rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:bg-[#e5ff92] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid motion-reduce:transition-none">
              <Sparkles className="size-4" aria-hidden /> Human Mode
            </Link>
            <Link href="/md" className="inline-flex min-h-12 items-center gap-2 rounded-full border border-white/15 bg-white/[.04] px-5 text-sm font-semibold text-white transition hover:border-white/30 hover:bg-white/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid motion-reduce:transition-none">
              <Braces className="size-4" aria-hidden /> Markdown Mode
            </Link>
          </div>
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-4 px-5 py-12 sm:grid-cols-2 lg:grid-cols-4 lg:px-8">
        <Link href="/discover" className="group rounded-2xl border border-white/10 bg-white/[.025] p-5 transition hover:border-white/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid motion-reduce:transition-none">
          <Search className="size-5 text-acid" aria-hidden /><p className="mt-7 font-semibold text-white">Explore the early public network</p><p className="mt-2 text-sm leading-6 text-mist">Browse profiles and resumes their owners chose to publish as the public inventory grows.</p><ArrowRight className="mt-5 size-4 text-mist transition group-hover:translate-x-1 group-hover:text-acid motion-reduce:transform-none motion-reduce:transition-none" aria-hidden />
        </Link>
        <a href={publicDiscoveryUrl("/agent-readme.md")} type="text/markdown" className="group rounded-2xl border border-white/10 bg-white/[.025] p-5 transition hover:border-white/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid motion-reduce:transition-none">
          <FileText className="size-5 text-acid" aria-hidden /><p className="mt-7 font-semibold text-white">Read the agent contract</p><p className="mt-2 text-sm leading-6 text-mist">Open the canonical Markdown orientation document directly.</p><ArrowRight className="mt-5 size-4 text-mist transition group-hover:translate-x-1 group-hover:text-acid motion-reduce:transform-none motion-reduce:transition-none" aria-hidden />
        </a>
        {privateWorkspacesEnabled ? (
          <Link href="/agents" className="group rounded-2xl border border-white/10 bg-white/[.025] p-5 transition hover:border-white/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid motion-reduce:transition-none">
            <ShieldCheck className="size-5 text-acid" aria-hidden /><p className="mt-7 font-semibold text-white">Manage agent access</p><p className="mt-2 text-sm leading-6 text-mist">Keep ongoing permissions bounded, reviewable, and revocable.</p><ArrowRight className="mt-5 size-4 text-mist transition group-hover:translate-x-1 group-hover:text-acid motion-reduce:transform-none motion-reduce:transition-none" aria-hidden />
          </Link>
        ) : (
          <Link href="/agent-directory" className="group rounded-2xl border border-white/10 bg-white/[.025] p-5 transition hover:border-white/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid motion-reduce:transition-none">
            <Bot className="size-5 text-acid" aria-hidden /><p className="mt-7 font-semibold text-white">Explore published agents</p><p className="mt-2 text-sm leading-6 text-mist">Browse public Agent Identity labels without creating contact, access, or authority.</p><ArrowRight className="mt-5 size-4 text-mist transition group-hover:translate-x-1 group-hover:text-acid motion-reduce:transform-none motion-reduce:transition-none" aria-hidden />
          </Link>
        )}
        {recruitingEnabled ? (
          <article className="rounded-2xl border border-white/10 bg-white/[.025] p-5">
            <BriefcaseBusiness className="size-5 text-acid" aria-hidden />
            <p className="mt-7 font-semibold text-white">Public recruiting is enabled</p>
            <p className="mt-2 text-sm leading-6 text-mist">Browse service-gated organizations and published jobs, or open the private employer workspace to manage authorized records.</p>
            <div className="mt-5 flex flex-wrap gap-x-4 gap-y-3 text-sm font-semibold">
              <Link href="/organizations" className="text-acid underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Organizations</Link>
              <Link href="/jobs" className="text-acid underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Jobs</Link>
              {privateWorkspacesEnabled && <Link href="/employer" className="text-white underline decoration-white/30 underline-offset-4 hover:decoration-acid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Employer workspace</Link>}
            </div>
          </article>
        ) : privateWorkspacesEnabled ? (
          <Link href="/employer" className="group rounded-2xl border border-white/10 bg-white/[.025] p-5 transition hover:border-white/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid motion-reduce:transition-none">
            <BriefcaseBusiness className="size-5 text-acid" aria-hidden /><p className="mt-7 font-semibold text-white">Private employer preparation</p><p className="mt-2 text-sm leading-6 text-mist">Sign in to prepare private organizations and job drafts. Public recruiting and applicant intake are disabled until the release gate is explicitly enabled.</p><ArrowRight className="mt-5 size-4 text-mist transition group-hover:translate-x-1 group-hover:text-acid motion-reduce:transform-none motion-reduce:transition-none" aria-hidden />
          </Link>
        ) : (
          <article className="rounded-2xl border border-white/10 bg-white/[.025] p-5">
            <BriefcaseBusiness className="size-5 text-acid" aria-hidden />
            <p className="mt-7 font-semibold text-white">Recruiting is not available in this release</p>
            <p className="mt-2 text-sm leading-6 text-mist">Public recruiting and applicant intake are disabled. Private employer preparation appears only in deployments with authenticated workspaces.</p>
          </article>
        )}
      </section>

      <footer className="border-t border-white/10 px-5 py-8 text-center text-xs text-mist">
        <p>connect.md keeps Markdown canonical for people and their agents.</p>
        <Link href="/trust" className="mt-3 inline-flex min-h-11 items-center font-semibold text-white underline decoration-white/30 underline-offset-4 hover:decoration-acid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Privacy &amp; agent data</Link>
      </footer>
    </main>
  );
}
