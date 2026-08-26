"use client";

import { AlertTriangle, Check, LoaderCircle, RotateCcw, Search, X } from "lucide-react";
import React, { useEffect, useMemo, useRef, useState } from "react";

import { ApiRequestError } from "@/lib/api";
import {
  countSearchRepeatedValues,
  type SearchFilters,
} from "@/lib/public-search-api";
import {
  isTaxonomyFilterValue,
  listTaxonomies,
  listTaxonomyTerms,
  type TaxonomyCatalogEntry,
  type TaxonomyExecutableParameter,
  type TaxonomyFacetEntry,
  type TaxonomyFacets,
  type TaxonomyName,
  type TaxonomyTerm
} from "@/lib/taxonomy-api";
import { canStartTaxonomyRequest, createTaxonomySearchState, taxonomySearchReducer, type TaxonomySearchState } from "@/lib/taxonomy-search-state";

type TaxonomyFilterPanelProps = { filters: SearchFilters; taxonomyFacets: TaxonomyFacets | null };
type SelectedEvidence = { parameter: TaxonomyExecutableParameter; filterValue: string; canonicalId: string | null; label: string | null; labelConflict: boolean; vocabularyVersion: string | null; versionConflict: boolean };
type SelectionMap = Partial<Record<TaxonomyExecutableParameter, SelectedEvidence[]>>;

const FIELD_CONFIG: Array<{ parameter: TaxonomyExecutableParameter; taxonomy: TaxonomyName; label: string; description: string; singleton?: boolean }> = [
  { parameter: "occupation_ids", taxonomy: "occupation", label: "Occupations", description: "Search the authoritative occupation registry." },
  { parameter: "industry_ids", taxonomy: "industry", label: "Industries", description: "Search the authoritative industry registry." },
  { parameter: "location_id", taxonomy: "location", label: "Location", description: "Choose one authoritative location.", singleton: true },
  { parameter: "skill_ids", taxonomy: "skill", label: "Skills", description: "Search the authoritative skills registry." },
  { parameter: "language_ids", taxonomy: "language", label: "Languages", description: "Search the authoritative language registry." },
  { parameter: "seniority_ids", taxonomy: "seniority", label: "Seniority", description: "Search the authoritative seniority registry." },
  { parameter: "open_to_ids", taxonomy: "open_to", label: "Open to", description: "Search the authoritative opportunity registry." },
  { parameter: "organization_ids", taxonomy: "organization", label: "Organizations", description: "Search public organization references." },
  { parameter: "representative_ids", taxonomy: "representative", label: "Representative", description: "Owner-attested public-profile reference only." },
  { parameter: "work_modes", taxonomy: "work_mode", label: "Work modes", description: "Search registry work-mode aliases." }
];

const panelClass = "min-w-0 max-w-full rounded-2xl border border-acid/20 bg-acid/[.035] p-4";
const fieldClass = "min-h-11 w-full rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none placeholder:text-mist/45 focus:border-acid/70 focus:ring-2 focus:ring-acid/15";

export function TaxonomyFilterPanel({ filters, taxonomyFacets }: TaxonomyFilterPanelProps) {
  const [catalog, setCatalog] = useState<TaxonomyCatalogEntry[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [selections, setSelections] = useState<SelectionMap>(() => selectionsFromFilters(filters, taxonomyFacets));
  const [queries, setQueries] = useState<Record<string, string>>({});
  const [states, setStates] = useState<Record<string, TaxonomySearchState>>(() => Object.fromEntries(FIELD_CONFIG.map((field) => [field.parameter, createTaxonomySearchState(field.taxonomy)])));
  const statesRef = useRef(states);
  const controllersRef = useRef<Record<string, AbortController>>({});
  const filtersSignature = filterSignature(filters);
  const selectionCount = useMemo(() => countSearchRepeatedValues(filtersFromSelections(filters, selections)), [filters, selections]);
  const remaining = Math.max(0, 50 - selectionCount);

  useEffect(() => {
    const controller = new AbortController();
    setCatalogError(null);
    void listTaxonomies({ signal: controller.signal }).then(setCatalog).catch((error: unknown) => {
      if (!controller.signal.aborted) setCatalogError(messageForError(error));
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    setSelections(selectionsFromFilters(filters, taxonomyFacets));
    const nextStates = Object.fromEntries(FIELD_CONFIG.map((field) => [field.parameter, createTaxonomySearchState(field.taxonomy)]));
    statesRef.current = nextStates;
    setStates(nextStates);
    setQueries({});
  }, [filters, filtersSignature, taxonomyFacets]);

  function updateStates(parameter: TaxonomyExecutableParameter, action: Parameters<typeof taxonomySearchReducer>[1]) {
    setStates((current) => {
      const next = taxonomySearchReducer(current[parameter], action);
      const updated = next === current[parameter] ? current : { ...current, [parameter]: next };
      statesRef.current = updated;
      return updated;
    });
  }

  async function loadTerms(field: typeof FIELD_CONFIG[number], append: boolean) {
    const taxonomy = catalog?.find((entry) => entry.taxonomy === field.taxonomy && entry.parameters.includes(field.parameter))?.taxonomy;
    if (!taxonomy) return;
    const query = queries[field.parameter] ?? "";
    const current = statesRef.current[field.parameter];
    const cursor = append ? current.nextCursor : null;
    const requestKey = `${field.parameter}|${taxonomy}|${query}|${cursor ?? "first"}`;
    if (!canStartTaxonomyRequest(current, requestKey, cursor)) return;
    const controller = new AbortController();
    controllersRef.current[field.parameter]?.abort();
    controllersRef.current[field.parameter] = controller;
    updateStates(field.parameter, { type: "start", requestKey, query, cursor });
    try {
      const page = await listTaxonomyTerms(taxonomy, { q: query, cursor, limit: 50, signal: controller.signal });
      updateStates(field.parameter, { type: "success", requestKey, query, cursor, page });
    } catch (error) {
      if (controller.signal.aborted) return;
      updateStates(field.parameter, { type: "failure", requestKey, query, cursor, status: error instanceof ApiRequestError ? error.status : undefined, message: messageForError(error) });
    } finally {
      if (controllersRef.current[field.parameter] === controller) delete controllersRef.current[field.parameter];
    }
  }

  function toggleSelection(option: TaxonomyTerm | TaxonomyFacetEntry | SelectedEvidence, parameter: TaxonomyExecutableParameter) {
    const evidence = "taxonomy" in option ? evidenceFromOption(option, parameter) : option;
    setSelections((current) => {
      const existing = current[parameter] ?? [];
      const has = existing.some((item) => item.filterValue === evidence.filterValue);
      const nextItems = has ? existing.filter((item) => item.filterValue !== evidence.filterValue) : fieldIsSingleton(parameter) ? [evidence] : [...existing, evidence];
      const next = { ...current, [parameter]: nextItems };
      if (!has && countSearchRepeatedValues(filtersFromSelections(filters, next)) > 50) return current;
      return next;
    });
  }

  const retryCatalog = () => {
    setCatalog(null);
    setCatalogError(null);
    const controller = new AbortController();
    void listTaxonomies({ signal: controller.signal }).then(setCatalog).catch((error: unknown) => setCatalogError(messageForError(error)));
  };

  return <section className={panelClass} aria-labelledby="taxonomy-filter-title">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="eyebrow">Authoritative vocabulary</p><h2 id="taxonomy-filter-title" className="mt-1 text-lg font-semibold text-white">Browse compact typed filters</h2><p className="mt-1 max-w-3xl text-xs leading-5 text-mist">Labels and canonical IDs are evidence for people; only the registry alias is placed in the search URL.</p></div>
      <span className="rounded-full border border-white/12 px-3 py-1.5 text-xs text-mist" aria-label={`${remaining} repeated filter values remaining`}>{remaining}/50 values remaining</span>
    </div>
    <p role="status" aria-live="polite" className="mt-3 text-xs leading-5 text-mist/80">{catalogError ? "Authoritative vocabulary is unavailable; no options were invented." : catalog === null ? "Loading authoritative vocabulary…" : "Choose a field, search its registry, then select a term."}</p>
    {filters.invalidTypedValues.length > 0 && <p role="alert" className="mt-3 text-xs leading-5 text-amber-100">Legacy or raw typed URL values were not re-submitted. Select authoritative registry aliases to restore those filters.</p>}
    {catalogError && <button type="button" className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-3 text-xs font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid" onClick={retryCatalog}><RotateCcw className="size-3.5" aria-hidden />Retry vocabulary</button>}
    <div className="mt-4 grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
      {FIELD_CONFIG.map((field) => <TaxonomyField key={field.parameter} field={field} state={states[field.parameter]} query={queries[field.parameter] ?? ""} catalog={catalog} catalogError={catalogError} taxonomyFacets={taxonomyFacets} selected={selections[field.parameter] ?? []} onQuery={(value) => setQueries((current) => ({ ...current, [field.parameter]: value }))} onSearch={() => void loadTerms(field, false)} onLoadMore={() => void loadTerms(field, true)} onToggle={(option) => toggleSelection(option, field.parameter)} />)}
    </div>
    <div className="mt-4 flex flex-wrap gap-2" aria-label="Selected typed filters">
      {Object.values(selections).flatMap((items) => items ?? []).map((selection) => <span key={`${selection.parameter}:${selection.filterValue}`} className="inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border border-acid/25 bg-acid/[.06] pl-3 text-xs text-white"><span className="min-w-0 truncate">{selection.label ?? selection.filterValue}</span>{selection.labelConflict && <span className="text-amber-100">Label conflict</span>}<button type="button" aria-label={`Remove ${selection.label ?? selection.filterValue}`} className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full text-mist hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid" onClick={() => toggleSelection(selection, selection.parameter)}><X className="size-3.5" aria-hidden /></button><input type="hidden" name={selection.parameter} value={selection.filterValue} /></span>)}
    </div>
    {selectionCount >= 50 && <p role="alert" className="mt-3 text-xs text-amber-100">The request-wide 50-value limit has been reached. Remove a repeated filter before adding another.</p>}
    <noscript><p className="mt-3 text-xs leading-5 text-mist">Taxonomy browsing needs JavaScript. Existing compact `/search` URLs remain usable without it.</p></noscript>
  </section>;
}

function TaxonomyField({ field, state, query, catalog, catalogError, taxonomyFacets, selected, onQuery, onSearch, onLoadMore, onToggle }: { field: typeof FIELD_CONFIG[number]; state: TaxonomySearchState; query: string; catalog: TaxonomyCatalogEntry[] | null; catalogError: string | null; taxonomyFacets: TaxonomyFacets | null; selected: SelectedEvidence[]; onQuery: (value: string) => void; onSearch: () => void; onLoadMore: () => void; onToggle: (option: TaxonomyTerm | TaxonomyFacetEntry) => void }) {
  const catalogLoading = catalog === null && catalogError === null;
  const available = catalog?.some((entry) => entry.taxonomy === field.taxonomy && entry.parameters.includes(field.parameter)) ?? false;
  const suggestions = Object.values(taxonomyFacets ?? {}).flat().filter((entry) => entry.parameter === field.parameter).slice(0, 8);
  const options: Array<TaxonomyTerm | TaxonomyFacetEntry> = state.terms.length > 0 ? state.terms : suggestions;
  return <fieldset className="min-w-0 rounded-xl border border-white/10 bg-black/[.12] p-3" disabled={catalogLoading || !available}>
    <legend className="px-1 text-sm font-semibold text-white">{field.label}{field.singleton ? " · choose one" : ""}</legend>
    <p className="mt-1 text-xs leading-5 text-mist/75">{field.description}</p>
    {field.parameter === "representative_ids" && <p className="mt-1 text-xs leading-5 text-amber-100/85">Owner-attested public-profile reference only; this does not verify identity, mandate, organization authority, availability, consent, or contact permission.</p>}
    <div className="mt-3 flex gap-2"><label className="sr-only" htmlFor={`taxonomy-query-${field.parameter}`}>Search {field.label}</label><div className="relative min-w-0 flex-1"><Search className="pointer-events-none absolute left-3 top-3 size-4 text-acid" aria-hidden /><input id={`taxonomy-query-${field.parameter}`} type="search" value={query} maxLength={100} placeholder={`Search ${field.label.toLowerCase()}`} className={`${fieldClass} pl-9`} onChange={(event) => onQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); onSearch(); } }} /></div><button type="button" aria-busy={(state.status === "loading" || state.status === "loading-more") || undefined} className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-white/15 px-3 text-xs font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:cursor-not-allowed disabled:opacity-45" disabled={!available || state.status === "loading" || state.status === "loading-more"} onClick={onSearch}>{(state.status === "loading" || state.status === "loading-more") ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> : <Search className="size-3.5" aria-hidden />}Search</button></div>
    {catalogLoading && <p role="status" className="mt-3 text-xs text-mist">Loading this registry catalog…</p>}
    {catalogError && <p role="status" className="mt-3 text-xs text-amber-100">This registry is unavailable; no vocabulary was invented.</p>}
    {!catalogLoading && !available && <p role="status" className="mt-3 text-xs text-amber-100">This registry is not available in the current catalog.</p>}
    {state.error && <p role="alert" className="mt-3 flex items-start gap-2 text-xs text-amber-100"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />{state.error}</p>}
    {available && state.status === "empty" && <p role="status" className="mt-3 text-xs text-mist">No terms match this search.</p>}
    {options.length > 0 && <ul className="mt-3 grid gap-2" aria-label={`${field.label} taxonomy terms`}>{options.map((option) => <TaxonomyOption key={`${option.taxonomy}:${option.filterValue}`} option={option} selected={selected.some((item) => item.filterValue === option.filterValue)} onToggle={() => onToggle(option)} />)}</ul>}
    {state.status === "ready" && state.nextCursor && <button type="button" className="mt-3 inline-flex min-h-11 items-center rounded-full border border-white/15 px-3 text-xs font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid" onClick={onLoadMore}>Load more terms</button>}
  </fieldset>;
}

function TaxonomyOption({ option, selected, onToggle }: { option: TaxonomyTerm | TaxonomyFacetEntry; selected: boolean; onToggle: () => void }) {
  return <li><button type="button" aria-pressed={selected} className="group flex w-full items-start justify-between gap-3 rounded-xl border border-white/10 bg-black/20 p-3 text-left transition hover:border-acid/30 hover:bg-white/[.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid" onClick={onToggle}><span className="min-w-0"><span className="block break-words text-sm font-semibold text-white">{option.label ?? "Unlabelled registry term"}</span><span className="mt-1 block break-all font-mono text-[11px] text-mist/80">{option.canonicalId}</span><span className="mt-1 block break-all font-mono text-[10px] text-mist/60">{option.filterValue}</span>{option.labelConflict || option.versionConflict ? <span className="mt-1 block text-[11px] text-amber-100">Label/version conflict — verify canonical evidence</span> : null}{option.vocabularyVersion && <span className="mt-1 block text-[10px] text-mist/60">Vocabulary {option.vocabularyVersion}</span>}</span><span className="mt-0.5 shrink-0 text-acid" aria-hidden>{selected ? <Check className="size-4" /> : null}</span></button></li>;
}

function selectionsFromFilters(filters: SearchFilters, taxonomyFacets: TaxonomyFacets | null): SelectionMap {
  const evidence = Object.values(taxonomyFacets ?? {}).flat();
  const map: SelectionMap = {};
  const add = (parameter: TaxonomyExecutableParameter, values: string[]) => {
    const invalidKeys = parameter === "open_to_ids" ? ["open_to_ids", "open_to"] : parameter === "seniority_ids" ? ["seniority_ids", "seniority_id"] : [parameter];
    if (filters.invalidTypedValues.some((value) => invalidKeys.some((key) => value.startsWith(`${key}:`)))) { map[parameter] = []; return; }
    map[parameter] = values.filter(isTaxonomyFilterValue).map((filterValue) => { const match = evidence.find((item) => item.parameter === parameter && item.filterValue === filterValue); return match ? evidenceFromOption(match, parameter) : { parameter, filterValue, canonicalId: null, label: null, labelConflict: false, vocabularyVersion: null, versionConflict: false }; });
  };
  add("occupation_ids", filters.occupationIds); add("industry_ids", filters.industryIds); add("skill_ids", filters.skillIds); add("language_ids", filters.languageIds); add("seniority_ids", filters.seniorityIds); add("open_to_ids", filters.openTo); add("organization_ids", filters.organizationIds); add("representative_ids", filters.representativeIds); add("work_modes", filters.workModes); if (filters.locationId) add("location_id", [filters.locationId]);
  return map;
}

function filtersFromSelections(filters: SearchFilters, selections: SelectionMap): SearchFilters {
  return { ...filters, occupationIds: valuesFor(selections, "occupation_ids"), industryIds: valuesFor(selections, "industry_ids"), skillIds: valuesFor(selections, "skill_ids"), languageIds: valuesFor(selections, "language_ids"), seniorityIds: valuesFor(selections, "seniority_ids"), openTo: valuesFor(selections, "open_to_ids"), organizationIds: valuesFor(selections, "organization_ids"), representativeIds: valuesFor(selections, "representative_ids"), workModes: valuesFor(selections, "work_modes"), locationId: valuesFor(selections, "location_id")[0] ?? "" };
}

function valuesFor(selections: SelectionMap, parameter: TaxonomyExecutableParameter) { return (selections[parameter] ?? []).map((item) => item.filterValue); }
function evidenceFromOption(option: TaxonomyTerm | TaxonomyFacetEntry, parameter: TaxonomyExecutableParameter): SelectedEvidence { return { parameter, filterValue: option.filterValue, canonicalId: option.canonicalId, label: option.label, labelConflict: option.labelConflict, vocabularyVersion: option.vocabularyVersion, versionConflict: option.versionConflict }; }
function fieldIsSingleton(parameter: TaxonomyExecutableParameter) { return parameter === "location_id"; }
function filterSignature(filters: SearchFilters) { return JSON.stringify([filters.q, filters.kind, filters.skills, filters.occupationIds, filters.industryIds, filters.skillIds, filters.languageIds, filters.locationId, filters.seniorityIds, filters.workModes, filters.openTo, filters.organizationIds, filters.representativeIds, filters.invalidTypedValues, filters.offset, filters.limit]); }
function messageForError(error: unknown) { return error instanceof Error ? error.message : "The authoritative vocabulary could not be loaded."; }
