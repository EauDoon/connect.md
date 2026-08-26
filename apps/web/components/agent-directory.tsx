import { ArrowRight, Bot, RotateCcw, Search, ShieldCheck } from "lucide-react";
import Link from "next/link";
import React from "react";

import { agentDirectoryHref, type AgentDirectoryFilters } from "@/lib/agent-directory";
import type { PublicAgentIdentityDirectory } from "@/lib/agent-identity-api";
import { buildInboxContactReturnPath } from "@/lib/auth-return-intent";
import { PublicNetworkEarlyState } from "@/components/public-network-empty-state";

const fieldClass = "min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none placeholder:text-mist/45 focus:border-acid/70 focus:ring-2 focus:ring-acid/15";

export function AgentDirectory({ filters, response, error }: { filters: AgentDirectoryFilters; response: PublicAgentIdentityDirectory | null; error: string | null }) {
  const resetHref = agentDirectoryHref(filters);
  const pageError = error && filters.cursor
    ? "This signed result page is no longer available for the selected search. Start the search again; no contact request was sent."
    : error;

  return <main className="pb-16">
    <section className="relative overflow-hidden border-b border-white/10 bg-black/10">
      <div aria-hidden className="absolute inset-0 -z-10 bg-grid bg-[size:52px_52px] opacity-20 [mask-image:linear-gradient(to_right,black,transparent)]" />
      <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8 lg:py-16">
        <p className="eyebrow">Public Agent Directory</p>
        <div className="mt-4 grid gap-8 lg:grid-cols-[minmax(0,1fr)_19rem] lg:items-end">
          <div>
            <h1 className="max-w-4xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">Find a published agent identity, not a claim of authority.</h1>
            <p className="mt-5 max-w-3xl text-lg leading-8 text-mist">Browse public identities linked to current public profiles. A listed capability is limited to a platform-mediated internal contact request; it does not show availability, ownership, grants, mandates, authority, or an external endpoint.</p>
          </div>
          <div className="rounded-2xl border border-acid/20 bg-acid/[.06] p-4 text-sm leading-6 text-mist"><ShieldCheck className="size-5 text-acid" aria-hidden /><p className="mt-3 font-semibold text-white">No direct delivery</p><p className="mt-1">Discovery does not send a message or create permission. Review the linked public profile before any private, human-controlled platform action.</p></div>
        </div>
      </div>
    </section>

    <section className="mx-auto max-w-7xl px-5 py-8 lg:px-8">
      <form action="/agent-directory" method="get" role="search" className="rounded-[1.5rem] border border-white/12 bg-panel p-4 sm:p-5">
        <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow">Bounded public discovery</p><h2 className="mt-2 text-xl font-semibold text-white">Search published identity labels</h2></div><Link href="/agent-directory" className="inline-flex min-h-11 items-center gap-2 rounded-full px-4 text-sm font-semibold text-mist transition hover:bg-white/[.06] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"><RotateCcw className="size-4" aria-hidden />Clear search</Link></div>
        <div className="mt-5 grid gap-3 md:grid-cols-[minmax(0,1fr)_16rem_auto]">
          <label className="block"><span className="mb-1.5 block text-xs font-semibold text-mist">Identity search</span><input name="q" defaultValue={filters.q} maxLength={100} className={fieldClass} placeholder="Name, handle, or description" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-semibold text-mist">Public profile handle</span><input name="profile_handle" defaultValue={filters.profileHandle ?? ""} maxLength={100} autoCapitalize="none" spellCheck={false} className={fieldClass} placeholder="profile-handle" /></label>
          <button type="submit" className="inline-flex min-h-11 items-center justify-center gap-2 rounded-full bg-acid px-5 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"><Search className="size-4" aria-hidden />Search</button>
        </div>
      </form>

      {pageError ? <div role="alert" className="mt-6 rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-5"><h2 className="font-semibold text-amber-50">Directory results are unavailable</h2><p className="mt-2 text-sm leading-6 text-amber-100/85">{pageError}</p><Link href={resetHref} className="mt-4 inline-flex min-h-11 items-center rounded-full border border-amber-200/30 px-4 text-sm font-semibold text-amber-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Start this search again</Link></div> : response ? <DirectoryResults filters={filters} response={response} /> : null}
    </section>
  </main>;
}

function DirectoryResults({ filters, response }: { filters: AgentDirectoryFilters; response: PublicAgentIdentityDirectory }) {
  if (response.identities.length === 0) return isUnfilteredFirstEmptyPage(filters, response.nextCursor) ? <div className="mt-7"><PublicNetworkEarlyState detail="No public Agent Identities linked to public profiles are available in this directory yet. An absent listing says nothing about anyone’s private tools or authority." /></div> : <div className="mt-7 rounded-[1.4rem] border border-dashed border-white/15 bg-panel p-8 text-center"><Bot className="mx-auto size-6 text-acid" aria-hidden /><h2 className="mt-4 text-lg font-semibold text-white">No matching public identities</h2><p className="mt-2 text-sm leading-6 text-mist">Try a broader description or clear the profile filter. An absent result does not describe a person’s private tools or authority.</p></div>;
  return <div className="mt-7"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="eyebrow">Published identity records</p><h2 className="mt-2 text-2xl font-semibold text-white">Linked to public professional profiles</h2></div>{filters.cursor && <Link href={agentDirectoryHref(filters)} className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Start current search</Link>}</div><ol className="mt-5 grid gap-4 lg:grid-cols-2">{response.identities.map((identity) => <li key={identity.handle}><article className="h-full rounded-[1.4rem] border border-white/10 bg-panel p-5 transition hover:border-acid/30 hover:bg-white/[.045] sm:p-6"><div className="flex items-start justify-between gap-4"><Bot className="size-5 shrink-0 text-acid" aria-hidden /><span className="rounded-full border border-acid/20 bg-acid/[.06] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-acid">Internal contact request</span></div><h3 className="mt-5 break-anywhere text-2xl font-semibold text-white"><Link href={`/agents/${encodeURIComponent(identity.handle)}`} className="hover:text-acid hover:underline">{identity.displayName}</Link></h3><p className="mt-1 break-anywhere font-mono text-xs text-mist">@{identity.handle}</p><p className="mt-4 break-anywhere text-sm leading-6 text-mist">{identity.description}</p><div className="mt-6 border-t border-white/10 pt-4"><p className="text-xs leading-5 text-mist/75">This published identity is linked to a profile. Its listed capability is internal and mediated; it does not prove a live mandate, authority, ownership, or availability.</p><div className="mt-4 flex flex-wrap gap-2"><Link href={`/agents/${encodeURIComponent(identity.handle)}`} className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Identity page</Link><Link href={`/p/${encodeURIComponent(identity.profileHandle)}`} className="inline-flex min-h-11 items-center gap-2 rounded-full bg-acid px-4 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">View public profile <ArrowRight className="size-4" aria-hidden /></Link><DirectoryContactLink profileHandle={identity.profileHandle} /></div></div></article></li>)}</ol>{response.nextCursor && <nav aria-label="Agent directory result pages" className="mt-7 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-white/10 bg-black/15 p-4"><p className="max-w-2xl text-sm leading-6 text-mist">Next-page links keep the exact search and profile filter required by the signed cursor. If a link no longer works, restart this search.</p><Link href={agentDirectoryHref(filters, response.nextCursor)} className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-5 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Next results <ArrowRight className="size-4" aria-hidden /></Link></nav>}</div>;
}

function isUnfilteredFirstEmptyPage(filters: AgentDirectoryFilters, nextCursor: string | null) { return !filters.q && filters.profileHandle === null && filters.cursor === null && nextCursor === null; }

function DirectoryContactLink({ profileHandle }: { profileHandle: string }) {
  const href = buildInboxContactReturnPath(profileHandle);
  if (!href) return null;
  return <Link href={href} className="inline-flex min-h-11 items-center rounded-full border border-acid/30 px-4 text-sm font-semibold text-acid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Prepare contact request</Link>;
}
