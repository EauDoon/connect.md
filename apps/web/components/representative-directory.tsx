"use client";

import { Bot, CircleAlert, FileCode2, MapPin, RadioTower, ShieldCheck, WifiOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { type DirectorySearchResponse, type SearchFilters } from "@/lib/public-search-api";
import { representativeProtocolLinks, REPRESENTATIVE_STATUSES, representativeHref } from "@/lib/representatives";

const fieldClass = "min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none placeholder:text-mist/45 focus:border-acid/70 focus:ring-2 focus:ring-acid/15";

function useOnlineStatus() {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => { window.removeEventListener("online", update); window.removeEventListener("offline", update); };
  }, []);
  return online;
}

export function RepresentativeDirectory({ filters, response, error, privateWorkspacesEnabled }: { filters: SearchFilters; response: DirectorySearchResponse | null; error: string | null; privateWorkspacesEnabled: boolean }) {
  const online = useOnlineStatus();
  const { isLoaded, isSignedIn } = useConnectmdAuth();
  const protocolLinks = representativeProtocolLinks();
  const authNotice = !privateWorkspacesEnabled
    ? "Public discovery is available. Sign-in-dependent owner controls are not configured in this deployment."
    : !isLoaded
      ? "Checking your account. Public discovery remains available."
      : isSignedIn
        ? "You are signed in. This directory remains public; private delegation stays in Agents."
        : "Public discovery does not require sign-in. Sign in only for your private owner workspace.";

  return <main className="pb-14">
    <section className="border-b border-white/10 bg-black/10">
      <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8 lg:py-16">
        <p className="eyebrow">Public representative discovery</p>
        <div className="mt-4 grid gap-8 lg:grid-cols-[minmax(0,1fr)_19rem] lg:items-end">
          <div>
            <h1 className="max-w-4xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">Find the published route to a professional signal.</h1>
            <p className="mt-5 max-w-3xl text-lg leading-8 text-mist">Discover public profiles that declare an authorised representative or organisation-managed route. Representation is owner-attested, not independently verified by connect.md.</p>
          </div>
          <div className="rounded-2xl border border-acid/20 bg-acid/[.06] p-4 text-sm leading-6 text-mist"><ShieldCheck className="size-5 text-acid" aria-hidden /><p className="mt-3 font-semibold text-white">No outbound delivery here.</p><p className="mt-1">A listed disclosure does not grant representative authority, prove identity, or send a message outside the platform.</p></div>
        </div>
        <p role="status" className="mt-6 text-sm text-mist/80">{authNotice}</p>
        {!online && <p role="status" className="mt-4 flex items-start gap-2 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-3 text-sm leading-6 text-amber-100"><WifiOff className="mt-0.5 size-4 shrink-0" aria-hidden />You are offline. The directory cannot refresh until the connection returns; no representative action has been sent.</p>}
      </div>
    </section>

    <section className="mx-auto max-w-7xl px-5 py-8 lg:px-8">
      <form action="/representatives" method="get" role="search" className="rounded-[1.5rem] border border-white/12 bg-panel p-4 sm:p-5">
        <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow">Structured discovery</p><h2 className="mt-2 text-xl font-semibold text-white">Filter public, owner-attested declarations</h2></div><Link href="/representatives" className="inline-flex min-h-11 items-center rounded-full px-4 text-sm font-semibold text-mist transition hover:bg-white/[.06] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Clear filters</Link></div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <label className="block lg:col-span-2"><span className="mb-1.5 block text-xs font-semibold text-mist">Professional signal</span><input name="q" defaultValue={filters.q} className={fieldClass} placeholder="Payments, product, advisory" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-semibold text-mist">Skill IDs</span><input name="skill_ids" defaultValue={filters.skillIds.join(", ")} className={fieldClass} placeholder="payments, strategy" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-semibold text-mist">Country code</span><input name="location_country_code" defaultValue={filters.locationCountryCode} maxLength={2} className={fieldClass} placeholder="SG" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-semibold text-mist">Representation</span><select name="representation_status" defaultValue={filters.representationStatus} className={fieldClass}>{REPRESENTATIVE_STATUSES.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}</select></label>
          <label className="block"><span className="mb-1.5 block text-xs font-semibold text-mist">Contact disclosure</span><select name="contact_disclosure" defaultValue={filters.contactDisclosure} className={fieldClass}><option value="">Any disclosure</option><option value="platform_only">Platform requests only</option><option value="public">Public contact channel</option><option value="none">No public contact</option></select></label>
        </div>
        <button type="submit" className="mt-4 inline-flex min-h-11 items-center justify-center rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:bg-[#e5ff92] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Search public declarations</button>
      </form>
    </section>

    <section className="mx-auto grid max-w-7xl gap-8 px-5 pb-12 lg:grid-cols-[minmax(0,1fr)_18rem] lg:px-8">
      <div className="min-w-0">
        {error && <div role="alert" className="rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-5"><h2 className="font-semibold text-amber-50">Representative directory is temporarily unavailable</h2><p className="mt-2 text-sm leading-6 text-amber-100/85">{error}</p></div>}
        {response && <>
          <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="eyebrow">Published declarations</p><h2 className="mt-2 text-2xl font-semibold text-white" aria-live="polite">{response.indexingAvailable ? <>{response.total.toLocaleString()} {response.total === 1 ? "profile" : "profiles"}</> : "Availability cannot be confirmed"}</h2></div>{!response.indexingAvailable && <span className="rounded-full border border-amber-300/30 px-3 py-1.5 text-xs font-semibold text-amber-100">Search index unavailable</span>}</div>
          {response.warning && <p role="status" className="mt-4 rounded-xl border border-amber-300/20 bg-amber-300/[.06] p-3 text-sm text-amber-100">{response.warning}</p>}
          {response.hits.length === 0 ? response.indexingAvailable ? <EmptyState /> : <UnavailableState /> : <ol className="mt-6 grid gap-4">{response.hits.map((hit) => <li key={hit.id}><RepresentativeCard hit={hit} /></li>)}</ol>}
          {response.indexingAvailable && <RepresentativePagination filters={filters} response={response} />}
        </>}
      </div>
      <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
        <section aria-labelledby="protocol-title" className="rounded-2xl border border-white/10 bg-panel p-5"><RadioTower className="size-5 text-acid" aria-hidden /><h2 id="protocol-title" className="mt-3 text-lg font-semibold text-white">Protocol discovery</h2><p className="mt-2 text-sm leading-6 text-mist">Use these same-origin documents to discover platform interfaces. They describe connect.md—not a listed profile’s authority.</p><ul className="mt-4 space-y-2">{protocolLinks.map((link) => <li key={link.href}><a href={link.href} className="block rounded-xl border border-white/10 p-3 transition hover:border-acid/30 hover:bg-white/[.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"><span className="flex items-center gap-2 text-sm font-semibold text-white"><FileCode2 className="size-4 text-acid" aria-hidden />{link.label}</span><span className="mt-1 block text-xs leading-5 text-mist">{link.detail}</span></a></li>)}</ul></section>
        <section className="rounded-2xl border border-white/10 bg-black/15 p-5 text-sm leading-6 text-mist"><Bot className="size-5 text-acid" aria-hidden /><h2 className="mt-3 font-semibold text-white">Owner-controlled by design</h2>{privateWorkspacesEnabled ? <p className="mt-2">For private agent permissions and reviewable changes, go to <Link href="/agents" className="inline-flex min-h-11 items-center align-middle font-semibold text-acid underline-offset-4 hover:underline">Agents</Link>. Public discovery never grants that access.</p> : <p className="mt-2">Private agent permissions and reviewable changes appear only in deployments with authenticated workspaces. Public discovery remains available here.</p>}</section>
      </aside>
    </section>
  </main>;
}

function RepresentativeCard({ hit }: { hit: DirectorySearchResponse["hits"][number] }) {
  const profileHref = `/p/${encodeURIComponent(hit.identifier)}`;
  const details = [hit.title, hit.headline, hit.locationLabel].filter((value): value is string => Boolean(value));
  return <article className="rounded-[1.4rem] border border-white/10 bg-panel p-5 transition hover:border-acid/30 hover:bg-white/[.045] sm:p-6"><div className="flex flex-col gap-5 sm:flex-row sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="inline-flex items-center gap-1 rounded-full border border-acid/20 bg-acid/[.06] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-acid"><Bot className="size-3.5" aria-hidden />{humanize(hit.representationStatus ?? "representation declared")}</span>{hit.contactDisclosure && <span className="rounded-full border border-white/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-mist">{humanize(hit.contactDisclosure)}</span>}</div><h3 className="mt-4 text-2xl font-semibold text-white"><Link href={profileHref} className="inline-flex min-h-11 min-w-11 max-w-full items-center break-anywhere rounded-md underline-offset-4 hover:text-acid hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">{hit.name}</Link></h3>{details.length > 0 && <p className="mt-2 text-sm leading-6 text-mist">{details.join(" · ")}</p>}{hit.excerpt && <p className="mt-3 max-w-3xl text-sm leading-6 text-mist/80">{hit.excerpt}</p>}<p className="mt-4 text-xs leading-5 text-mist/75">Representation is owner-attested on the public profile. connect.md does not independently verify identity or authority.</p>{hit.skills.length > 0 && <ul className="mt-4 flex flex-wrap gap-2" aria-label="Published skills">{hit.skills.slice(0, 8).map((skill) => <li key={skill} className="rounded-full bg-white/[.06] px-2.5 py-1 text-xs text-[#d5d9e0]">{skill}</li>)}</ul>}</div><div className="flex shrink-0 flex-row gap-2 sm:flex-col sm:items-stretch"><Link href={profileHref} className="inline-flex min-h-11 items-center justify-center rounded-full bg-acid px-4 text-xs font-bold text-ink">View public profile</Link>{hit.locationLabel && <span className="inline-flex min-h-11 items-center justify-center gap-1.5 px-3 text-xs text-mist"><MapPin className="size-3.5 text-acid" aria-hidden />{hit.locationLabel}</span>}</div></div></article>;
}

function RepresentativePagination({ filters, response }: { filters: SearchFilters; response: DirectorySearchResponse }) {
  const previous = Math.max(0, response.offset - response.limit);
  const next = response.offset + response.limit;
  return <nav aria-label="Representative result pages" className="mt-7 flex items-center justify-between gap-4"><span className="text-xs text-mist">Showing {response.total ? response.offset + 1 : 0}–{Math.min(response.offset + response.hits.length, response.total)}</span><div className="flex gap-2">{response.offset > 0 && <Link href={representativeHref(filters, previous)} className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white">Previous</Link>}{next < response.total && <Link href={representativeHref(filters, next)} className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white">Next</Link>}</div></nav>;
}

function EmptyState() { return <div className="mt-6 rounded-[1.4rem] border border-dashed border-white/15 bg-panel p-8 text-center"><ShieldCheck className="mx-auto size-6 text-acid" aria-hidden /><h3 className="mt-4 text-lg font-semibold text-white">No matching public declarations</h3><p className="mt-2 text-sm leading-6 text-mist">Try a broader term or a different representation status. An absent result does not imply that someone lacks a representative.</p></div>; }
function UnavailableState() { return <div role="status" className="mt-6 rounded-[1.4rem] border border-amber-300/25 bg-amber-300/[.08] p-8 text-center"><CircleAlert className="mx-auto size-6 text-amber-100" aria-hidden /><h3 className="mt-4 text-lg font-semibold text-amber-50">Search index unavailable</h3><p className="mt-2 text-sm leading-6 text-amber-100/85">The directory cannot confirm whether matching public declarations are available. Retry when the index recovers.</p></div>; }
function humanize(value: string) { return value.replaceAll("_", " ").replaceAll("-", " "); }
