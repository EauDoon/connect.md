import { ArrowRight, Bot, Braces, BriefcaseBusiness, CalendarClock, Filter, MapPin, Search, ShieldCheck, Sparkles, UserRoundSearch } from "lucide-react";
import Link from "next/link";
import React from "react";

import type { DirectorySearchResponse, SearchFacetOption, SearchFilters } from "@/lib/public-search-api";
import type { TaxonomyFacetEntry } from "@/lib/taxonomy-api";
import { publicApiMarkdownUrl } from "@/lib/api";
import { applyTaxonomyFacet, isSupportedSearchFacet, searchParamsFromFilters, toggleSearchFacet } from "@/lib/public-search-api";
import { TaxonomyFilterPanel } from "@/components/taxonomy-filter-panel";
import { PublicNetworkEarlyState } from "@/components/public-network-empty-state";

export function SearchExperience({ filters, response, error }: { filters: SearchFilters; response: DirectorySearchResponse | null; error: string | null }) {
  const hasFilters = activeFilterCount(filters) > 0;
  return (
    <main className="overflow-hidden pb-16">
      <section className="relative border-b border-white/10">
        <div className="absolute inset-0 -z-10 bg-grid bg-[size:54px_54px] opacity-25 [mask-image:radial-gradient(circle_at_30%_10%,black,transparent_72%)]" aria-hidden />
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8 lg:py-20">
          <div className="max-w-4xl">
            <p className="eyebrow">Human-readable · agent-legible</p>
            <h1 className="mt-4 font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">Find the right professional signal.</h1>
            <p className="mt-5 max-w-2xl text-lg leading-8 text-mist">Search canonical profiles by work, industry, occupation, location, availability, or representation—then inspect the same source an agent sees.</p>
          </div>
          <form action="/search" method="get" role="search" className="mt-9 rounded-[1.6rem] border border-white/12 bg-panel/95 p-4 shadow-glow sm:p-5">
            <label htmlFor="directory-query" className="sr-only">Search professional profiles</label>
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="relative flex-1"><Search className="pointer-events-none absolute left-4 top-3.5 size-5 text-acid" aria-hidden /><input id="directory-query" name="q" defaultValue={filters.q} maxLength={200} className="min-h-12 w-full rounded-full border border-white/15 bg-black/30 py-3 pl-12 pr-4 text-base text-white outline-none placeholder:text-mist/55 focus:border-acid/70 focus:ring-2 focus:ring-acid/15" placeholder="Role, industry, skill, organization…" /></div>
              <button type="submit" className="inline-flex min-h-12 items-center justify-center gap-2 rounded-full bg-acid px-7 text-sm font-bold text-ink transition hover:bg-[#e5ff92] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Search <ArrowRight className="size-4" aria-hidden /></button>
            </div>

            <details className="mt-4 group" open={hasFilters}>
              <summary className="-mx-2 inline-flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-lg px-2 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"><Filter className="size-4 text-acid" aria-hidden /> Structured filters{hasFilters ? ` · ${activeFilterCount(filters)} active` : ""}</summary>
              <div className="mt-4 grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 border-t border-white/10 pt-4 sm:grid-cols-2 lg:grid-cols-4">
                <SearchField label="Search mode"><select name="mode" defaultValue={filters.mode} className={fieldClass}><option value="projection">Projection · fast discovery</option><option value="exact">Exact · canonical corpus</option></select></SearchField>
                <SearchField label="Document type"><select name="kind" defaultValue={filters.kind} className={fieldClass}><option value="">Profiles and resumes</option><option value="profile">Profiles</option><option value="resume">Resumes</option></select></SearchField>
                <SearchField label="Agent capability"><select name="agent_capability" defaultValue={filters.agentCapability} className={fieldClass}><option value="">Any public profile</option><option value="internal_contact_request">Published internal-contact Agent Identity</option></select><span className="mt-1 block text-[11px] leading-4 text-mist/75">Discovery only; it does not prove a mandate or contact authority.</span></SearchField>
                <SearchField label="Country code"><input name="location_country_code" defaultValue={filters.locationCountryCode} className={fieldClass} maxLength={3} placeholder="SG" /></SearchField>
                <SearchField label="Region"><input name="location_region" defaultValue={filters.locationRegion} className={fieldClass} placeholder="Singapore" /></SearchField>
                <SearchField label="City"><input name="location_city" defaultValue={filters.locationCity} className={fieldClass} placeholder="Singapore" /></SearchField>
                <SearchField label="Availability"><select name="availability_status" defaultValue={filters.availabilityStatus} className={fieldClass}><option value="">Any availability</option><option value="available_now">Available now</option><option value="available_from">Available from a date</option><option value="not_available">Not available</option><option value="not_disclosed">Not disclosed</option></select></SearchField>
                <SearchField label="Available from"><input type="date" name="availability_from" defaultValue={filters.availabilityFrom} className={fieldClass} /></SearchField>
                <SearchField label="Representation"><select name="representation_status" defaultValue={filters.representationStatus} className={fieldClass}><option value="">Any representation</option><option value="self">Self-managed</option><option value="authorized_representative">Authorized representative</option><option value="organization">Organization-managed</option><option value="not_disclosed">Not disclosed</option></select></SearchField>
                <SearchField label="Contact disclosure"><select name="contact_disclosure" defaultValue={filters.contactDisclosure} className={fieldClass}><option value="">Any contact disclosure</option><option value="platform_only">Platform requests only</option><option value="public">Public contact channel</option><option value="none">No public contact</option></select></SearchField>
                <SearchField label="Updated since"><input type="date" name="updated_since" defaultValue={filters.updatedSince} className={fieldClass} /></SearchField>
                <div className="min-w-0 sm:col-span-2 lg:col-span-4"><TaxonomyFilterPanel filters={filters} taxonomyFacets={response?.taxonomyFacets ?? null} /></div>
              </div>
              <div className="mt-4 flex flex-wrap gap-3"><button type="submit" className="min-h-11 rounded-full border border-acid/30 bg-acid/10 px-5 text-sm font-semibold text-acid hover:bg-acid/15">Apply filters</button>{hasFilters && <Link href="/search" className="inline-flex min-h-11 items-center rounded-full px-4 text-sm font-semibold text-mist hover:text-white">Clear all</Link>}</div>
            </details>
          </form>
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-8 px-5 py-10 lg:grid-cols-[15rem_minmax(0,1fr)] lg:px-8">
        <aside aria-label="Search facets" className="space-y-5 lg:sticky lg:top-24 lg:self-start">
          <div><p className="eyebrow">Refine</p><h2 className="mt-2 text-lg font-semibold text-white">Structured facets</h2></div>
          {response && Object.keys(response.facets).some((key) => isSupportedSearchFacet(key)) ? Object.entries(response.facets).filter(([key]) => isSupportedSearchFacet(key)).map(([key, options]) => <FacetGroup key={key} name={key} options={options} filters={filters} />) : <p className="rounded-2xl border border-white/10 bg-panel p-4 text-sm leading-6 text-mist">Facet counts appear when the discovery index supplies them. Every filter remains encoded in the URL for repeatable human and agent searches.</p>}
          {response?.taxonomyFacets && Object.entries(response.taxonomyFacets).map(([key, options]) => <TaxonomyFacetGroup key={key} name={key} options={options} filters={filters} />)}
        </aside>

        <div className="min-w-0">
          {error && <div role="alert" className="rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-5"><h2 className="font-semibold text-amber-50">Directory temporarily unavailable</h2><p className="mt-2 text-sm leading-6 text-amber-100/85">{error}</p></div>}
          {response && <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div><p className="eyebrow">{response.mode === "exact" ? "Exact directory" : "Projection directory"}</p><h2 className="mt-2 text-2xl font-semibold text-white" aria-live="polite">{response.total.toLocaleString()} {response.mode === "projection" ? response.total === 1 ? "indexed result" : "indexed results" : response.total === 1 ? "matching result" : "matching results"}</h2></div>
              {!response.indexingAvailable && <span className="rounded-full border border-amber-300/30 px-3 py-1.5 text-xs font-semibold text-amber-100">Index unavailable</span>}
            </div>
            {response.mode === "exact" ? <ExactSearchBoundary response={response} /> : <ProjectionSearchBoundary />}
            {response.warning && <p role="status" className="mt-4 rounded-xl border border-amber-300/20 bg-amber-300/[.06] p-3 text-sm text-amber-100">{response.warning}</p>}
            {response.hits.length === 0 ? !response.indexingAvailable ? <UnavailableResults /> : isUnfilteredFirstEmptyPage(filters, response) ? <div className="mt-6"><PublicNetworkEarlyState detail="No owner-published profiles or resumes are available in this directory yet." headingLevel={3} /></div> : <EmptyResults /> : <ol className="mt-6 grid gap-4">{response.hits.map((hit) => <li key={hit.id}><DirectoryCard hit={hit} /></li>)}</ol>}
            <Pagination filters={filters} response={response} />
          </>}
        </div>
      </section>
    </main>
  );
}

function DirectoryCard({ hit }: { hit: DirectorySearchResponse["hits"][number] }) {
  const meta = [...hit.occupations, ...hit.industries, ...hit.seniority, ...hit.skills].slice(0, 8);
  const markdownHref = hit.markdownUrl ? publicApiMarkdownUrl(hit.markdownUrl) : null;
  return <article className="group rounded-[1.4rem] border border-white/10 bg-panel p-5 transition hover:border-acid/30 hover:bg-white/[.045] sm:p-6">
    <div className="flex flex-col gap-5 sm:flex-row sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2"><span className="inline-flex items-center gap-1 rounded-full border border-white/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-mist">{hit.kind === "profile" ? <UserRoundSearch className="size-3.5" aria-hidden /> : <BriefcaseBusiness className="size-3.5" aria-hidden />}{hit.kind}</span>{hit.representationStatus && <span className="inline-flex items-center gap-1 rounded-full border border-acid/20 bg-acid/[.06] px-2.5 py-1 text-[11px] text-acid"><Bot className="size-3.5" aria-hidden />{humanize(hit.representationStatus)}</span>}</div>
        <h3 className="mt-4 text-2xl font-semibold text-white"><Link href={hit.htmlUrl} data-touch-target="search-result-primary" className="inline-flex min-h-11 min-w-11 items-center rounded-md underline-offset-4 group-hover:text-acid hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">{hit.name}</Link></h3>
        {(hit.title || hit.headline) && <p className="mt-2 max-w-3xl text-sm leading-6 text-mist">{[hit.title, hit.headline].filter(Boolean).join(" · ")}</p>}
        {hit.excerpt && <p className="mt-3 max-w-3xl text-sm leading-6 text-mist/80">{hit.excerpt}</p>}
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-mist/80">{hit.locationLabel && <span className="inline-flex items-center gap-1.5"><MapPin className="size-3.5 text-acid" aria-hidden />{hit.locationLabel}</span>}{hit.updatedAt && <span className="inline-flex items-center gap-1.5"><CalendarClock className="size-3.5 text-acid" aria-hidden />Updated {formatDate(hit.updatedAt)}</span>}<span className="inline-flex items-center gap-1.5"><ShieldCheck className="size-3.5 text-acid" aria-hidden />Canonical v{hit.version}</span></div>
        {meta.length > 0 && <ul className="mt-4 flex flex-wrap gap-2" aria-label="Professional metadata">{meta.map((item) => <li key={item} className="rounded-full bg-white/[.06] px-2.5 py-1 text-xs text-[#d5d9e0]">{item}</li>)}</ul>}
        {hit.agentIdentities.length > 0 && <section className="mt-4 rounded-xl border border-acid/20 bg-acid/[.045] p-3" aria-label="Published Agent Identities"><p className="text-xs font-semibold text-white">Published Agent Identities</p><p className="mt-1 text-xs leading-5 text-mist">Discovery-only labels. They do not establish a mandate, ownership, availability, consent, or contact authority.</p><ul className="mt-2 flex flex-wrap gap-2">{hit.agentIdentities.map((identity) => <li key={identity.handle}><Link href={`/agents/${encodeURIComponent(identity.handle)}`} className="inline-flex min-h-11 items-center rounded-full border border-acid/25 px-3 text-xs font-semibold text-acid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">@{identity.handle}</Link></li>)}</ul></section>}
      </div>
      <div className="flex shrink-0 flex-row gap-2 sm:flex-col sm:items-stretch"><Link href={hit.htmlUrl} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-full bg-acid px-4 text-xs font-bold text-ink">View {hit.kind} <ArrowRight className="size-3.5" aria-hidden /></Link>{markdownHref && <a href={markdownHref} type="text/markdown" className="inline-flex min-h-11 items-center justify-center gap-2 rounded-full border border-white/15 px-4 text-xs font-semibold text-white"><Braces className="size-3.5 text-acid" aria-hidden /> Markdown</a>}</div>
    </div>
  </article>;
}

function FacetGroup({ name, options, filters }: { name: string; options: SearchFacetOption[]; filters: SearchFilters }) {
  return <section><h3 className="text-xs font-semibold uppercase tracking-[.12em] text-mist">{humanize(name)}</h3><ul className="mt-2 space-y-1">{options.slice(0, 8).map((option) => <li key={option.value}><Link href={facetHref(filters, name, option.value)} data-touch-target="search-facet" className="flex min-h-11 items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-sm text-mist hover:bg-white/[.05] hover:text-white"><span className="truncate">{option.label}</span><span className="text-xs text-mist/60">{option.count}</span></Link></li>)}</ul></section>;
}

function TaxonomyFacetGroup({ name, options, filters }: { name: string; options: TaxonomyFacetEntry[]; filters: SearchFilters }) {
  if (options.length === 0) return null;
  return <section><h3 className="text-xs font-semibold uppercase tracking-[.12em] text-mist">{humanize(name)} registry</h3><ul className="mt-2 space-y-1">{options.slice(0, 8).map((option) => <li key={`${option.parameter}:${option.filterValue}`}><Link href={taxonomyFacetHref(filters, option)} data-touch-target="search-facet" className="block min-h-11 rounded-lg px-2 py-1.5 text-sm text-mist hover:bg-white/[.05] hover:text-white"><span className="flex items-center justify-between gap-3"><span className="truncate">{option.label ?? "Unlabelled registry term"}</span><span className="text-xs text-mist/60">{option.count}</span></span><span className="mt-0.5 block break-all font-mono text-[10px] text-mist/55">{option.canonicalId}</span>{(option.labelConflict || option.versionConflict) && <span className="mt-0.5 block text-[10px] text-amber-100">Conflict evidence</span>}</Link></li>)}</ul></section>;
}

function Pagination({ filters, response }: { filters: SearchFilters; response: DirectorySearchResponse }) {
  if (response.mode === "exact") {
    const restart = exactPageHref(filters, null);
    return <nav aria-label="Exact search result pages" className="mt-7 flex flex-wrap items-center justify-between gap-4"><span className="text-xs text-mist">This exact page contains {response.hits.length.toLocaleString()} of {response.total.toLocaleString()} matching documents.</span><div className="flex gap-2">{filters.cursor && <Link href={restart} className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white">Restart exact search</Link>}{response.nextCursor && <Link href={exactPageHref(filters, response.nextCursor)} className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white">Next exact results</Link>}</div></nav>;
  }
  const previous = Math.max(0, response.offset - response.limit);
  const next = response.offset + response.limit;
  return <nav aria-label="Search result pages" className="mt-7 flex items-center justify-between gap-4"><span className="text-xs text-mist">Showing {response.total ? response.offset + 1 : 0}–{Math.min(response.offset + response.hits.length, response.total)}</span><div className="flex gap-2">{response.offset > 0 && <Link href={pageHref(filters, previous)} className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white">Previous</Link>}{next < response.total && <Link href={pageHref(filters, next)} className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white">Next</Link>}</div></nav>;
}

function ProjectionSearchBoundary() { return <p role="status" className="mt-4 rounded-xl border border-amber-300/20 bg-amber-300/[.06] p-3 text-sm leading-6 text-amber-100">Projection search uses a bounded 1,050-candidate discovery window. Indexed totals and facets are not complete; use Exact mode when you need the canonical corpus.</p>; }
function ExactSearchBoundary({ response }: { response: DirectorySearchResponse }) {
  const truncated = Object.entries(response.facetTruncated).filter(([, isTruncated]) => isTruncated).map(([facet]) => humanize(facet));
  return <div role="status" className="mt-4 rounded-xl border border-acid/20 bg-acid/[.06] p-3 text-sm leading-6 text-mist"><p><span className="font-semibold text-white">Exact canonical search</span>{response.complete ? " is complete for this returned canonical snapshot." : " returned an incomplete canonical snapshot; do not treat this result set as exhaustive."}{response.searchRevision !== null ? ` Search revision ${response.searchRevision}.` : " Search revision was not supplied."}</p>{truncated.length > 0 && <p className="mt-1 text-amber-100">Facet values were truncated by the server for: {truncated.join(", ")}.</p>}</div>;
}

function EmptyResults() { return <div className="mt-6 rounded-[1.4rem] border border-dashed border-white/15 bg-panel p-8 text-center"><Sparkles className="mx-auto size-6 text-acid" aria-hidden /><h3 className="mt-4 text-lg font-semibold text-white">No matching public documents</h3><p className="mt-2 text-sm text-mist">Remove a filter or try a broader professional term.</p></div>; }
function UnavailableResults() { return <div role="status" className="mt-6 rounded-[1.4rem] border border-amber-300/25 bg-amber-300/[.08] p-8 text-center"><ShieldCheck className="mx-auto size-6 text-amber-100" aria-hidden /><h3 className="mt-4 text-lg font-semibold text-amber-50">Search index unavailable</h3><p className="mt-2 text-sm text-amber-100/85">The directory cannot confirm whether public documents are available. Retry when the index recovers.</p></div>; }
function SearchField({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block min-w-0"><span className="mb-1.5 block text-xs font-semibold text-mist">{label}</span>{children}</label>; }

const fieldClass = "min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none placeholder:text-mist/45 focus:border-acid/70 focus:ring-2 focus:ring-acid/15";
function isUnfilteredFirstEmptyPage(filters: SearchFilters, response: DirectorySearchResponse) { return response.indexingAvailable && activeFilterCount(filters) === 0 && filters.offset === 0 && filters.cursor === null && response.total === 0 && response.nextCursor === null; }
function activeFilterCount(filters: SearchFilters) { return [filters.q, filters.kind, filters.agentCapability, ...filters.skills, ...filters.occupationIds, ...filters.industryIds, ...filters.skillIds, ...filters.languageIds, filters.locationId, filters.locationLabel, filters.locationCountryCode, filters.locationRegion, filters.locationCity, ...filters.seniorityIds, ...filters.workModes, filters.availabilityStatus, filters.availabilityFrom, ...filters.openTo, ...filters.organizationIds, ...filters.representativeIds, filters.representationStatus, filters.contactDisclosure, filters.updatedSince].filter(Boolean).length; }
function pageHref(filters: SearchFilters, offset: number) { return `/search?${searchParamsFromFilters({ ...filters, mode: "projection", cursor: null, offset }).toString()}`; }
function exactPageHref(filters: SearchFilters, cursor: string | null) { return `/search?${searchParamsFromFilters({ ...filters, mode: "exact", offset: 0, cursor }).toString()}`; }
function facetHref(filters: SearchFilters, key: string, value: string) {
  const next = toggleSearchFacet(filters, key, value);
  return `/search?${searchParamsFromFilters(next ?? filters).toString()}`;
}
function taxonomyFacetHref(filters: SearchFilters, option: TaxonomyFacetEntry) { return `/search?${searchParamsFromFilters(applyTaxonomyFacet(filters, option)).toString()}`; }
function humanize(value: string) { return value.replaceAll("_", " ").replaceAll("-", " "); }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date); }
