import { apiRequest } from "@/lib/api";
import { PRODUCT_ENDPOINTS } from "@/lib/product-endpoints";

export const TAXONOMY_NAMES = ["occupation", "industry", "location", "skill", "language", "seniority", "open_to", "organization", "representative", "work_mode"] as const;
export type TaxonomyName = (typeof TAXONOMY_NAMES)[number];
export const TAXONOMY_EXECUTABLE_PARAMETERS = ["occupation_ids", "industry_ids", "skill_ids", "language_ids", "location_id", "seniority_ids", "open_to_ids", "organization_ids", "representative_ids", "work_modes"] as const;
export type TaxonomyExecutableParameter = (typeof TAXONOMY_EXECUTABLE_PARAMETERS)[number];
export const TAXONOMY_CATALOG_PARAMETERS = [...TAXONOMY_EXECUTABLE_PARAMETERS, "seniority_id", "open_to"] as const;
export type TaxonomyCatalogParameter = typeof TAXONOMY_CATALOG_PARAMETERS[number];
const TAXONOMY_ALLOWED_CATALOG_PARAMETERS: Record<TaxonomyName, readonly TaxonomyCatalogParameter[]> = { occupation: ["occupation_ids"], industry: ["industry_ids"], location: ["location_id"], skill: ["skill_ids"], language: ["language_ids"], seniority: ["seniority_ids", "seniority_id"], open_to: ["open_to_ids", "open_to"], organization: ["organization_ids"], representative: ["representative_ids"], work_mode: ["work_modes"] };
const TAXONOMY_BY_EXECUTABLE_PARAMETER: Record<TaxonomyExecutableParameter, TaxonomyName> = { occupation_ids: "occupation", industry_ids: "industry", skill_ids: "skill", language_ids: "language", location_id: "location", seniority_ids: "seniority", open_to_ids: "open_to", organization_ids: "organization", representative_ids: "representative", work_modes: "work_mode" };
const TAXONOMY_CATALOG_METADATA: Record<TaxonomyName, { kind: TaxonomyCatalogEntry["kind"]; semantics: TaxonomyCatalogEntry["semantics"] }> = { occupation: { kind: "reference", semantics: "AND" }, industry: { kind: "reference", semantics: "AND" }, location: { kind: "reference", semantics: "singleton" }, skill: { kind: "reference", semantics: "AND" }, language: { kind: "reference", semantics: "AND" }, seniority: { kind: "reference", semantics: "OR" }, open_to: { kind: "reference", semantics: "AND" }, organization: { kind: "reference", semantics: "AND" }, representative: { kind: "reference", semantics: "OR" }, work_mode: { kind: "connect.md enum", semantics: "AND" } };
const TAXONOMY_FACET_GROUPS: Record<string, { taxonomy: TaxonomyName; parameter: TaxonomyExecutableParameter }> = {
  occupation_ids: { taxonomy: "occupation", parameter: "occupation_ids" },
  industry_ids: { taxonomy: "industry", parameter: "industry_ids" },
  skill_ids: { taxonomy: "skill", parameter: "skill_ids" },
  language_ids: { taxonomy: "language", parameter: "language_ids" },
  location_id: { taxonomy: "location", parameter: "location_id" },
  seniority_ids: { taxonomy: "seniority", parameter: "seniority_ids" },
  seniority_id: { taxonomy: "seniority", parameter: "seniority_ids" },
  open_to_ids: { taxonomy: "open_to", parameter: "open_to_ids" },
  open_to: { taxonomy: "open_to", parameter: "open_to_ids" },
  organization_ids: { taxonomy: "organization", parameter: "organization_ids" },
  representative_ids: { taxonomy: "representative", parameter: "representative_ids" },
  representative_id: { taxonomy: "representative", parameter: "representative_ids" },
  work_modes: { taxonomy: "work_mode", parameter: "work_modes" }
};
export type TaxonomyCatalogEntry = {
  taxonomy: TaxonomyName;
  parameters: TaxonomyCatalogParameter[];
  kind: "reference" | "connect.md enum";
  semantics: "AND" | "OR" | "singleton";
  source: string;
  authority: string;
  currentRevision: number;
};
export type TaxonomyTerm = {
  taxonomy: TaxonomyName;
  scheme: string;
  externalId: string;
  canonicalId: string;
  filterValue: string;
  label: string | null;
  labelConflict: boolean;
  vocabularyVersion: string | null;
  versionConflict: boolean;
};
export type TaxonomyPage = { terms: TaxonomyTerm[]; nextCursor: string | null; revision: number };
export type TaxonomyFacetEntry = {
  taxonomy: TaxonomyName;
  parameter: TaxonomyExecutableParameter;
  canonicalId: string;
  filterValue: string;
  label: string | null;
  labelConflict: boolean;
  vocabularyVersion: string | null;
  versionConflict: boolean;
  count: number;
};
export type TaxonomyFacets = Record<string, TaxonomyFacetEntry[]>;
export type TaxonomyRequestOptions = { q?: string; cursor?: string | null; limit?: number; signal?: AbortSignal; server?: boolean };
export type TaxonomyAliasSelection = { parameter: TaxonomyExecutableParameter; filterValue: string; canonicalId?: string | null; label?: string | null; labelConflict?: boolean; vocabularyVersion?: string | null; versionConflict?: boolean };

const TAXONOMY_FILTER_VALUE_PATTERN = /^tx1_[0-9a-f]{64}$/u;
const TAXONOMY_SCHEME_PATTERN = /^[a-z][a-z0-9._-]{0,79}$/u;
const TAXONOMY_EXTERNAL_ID_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,253}[A-Za-z0-9])?$/u;

export async function listTaxonomies(options: Pick<TaxonomyRequestOptions, "signal" | "server"> = {}): Promise<TaxonomyCatalogEntry[]> {
  const raw = await apiRequest<unknown>(PRODUCT_ENDPOINTS.taxonomies, { cache: "no-store", signal: options.signal, ...(options.server ? { server: true } : {}) });
  return parseTaxonomyCatalog(raw);
}

export async function listTaxonomyTerms(taxonomy: TaxonomyName, options: TaxonomyRequestOptions = {}): Promise<TaxonomyPage> {
  if (!isTaxonomyName(taxonomy)) throw new Error("The taxonomy name is not supported.");
  const q = options.q?.trim() ?? "";
  const cursor = options.cursor?.trim() ?? "";
  const limit = options.limit ?? 50;
  if (q.length > 100) throw new Error("Taxonomy search text must be 100 characters or fewer.");
  if (cursor.length > 2048) throw new Error("The taxonomy cursor is not valid.");
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error("Taxonomy page size must be between 1 and 100.");
  const params = new URLSearchParams({ limit: String(limit) });
  if (q) params.set("q", q);
  if (cursor) params.set("cursor", cursor);
  const raw = await apiRequest<unknown>(`${PRODUCT_ENDPOINTS.taxonomies}/${encodeURIComponent(taxonomy)}?${params.toString()}`, { cache: "no-store", signal: options.signal, ...(options.server ? { server: true } : {}) });
  const page = parseTaxonomyPage(raw);
  if (page.terms.some((term) => term.taxonomy !== taxonomy)) throw new Error("The taxonomy registry returned terms for the wrong taxonomy.");
  return page;
}

export function parseTaxonomyCatalog(value: unknown): TaxonomyCatalogEntry[] {
  if (!Array.isArray(value)) throw new Error("The taxonomy catalog is invalid.");
  if (value.length !== TAXONOMY_NAMES.length) throw new Error("The taxonomy catalog is incomplete.");
  const seen = new Set<TaxonomyName>();
  const parsed = value.map((item) => {
    const record = asRecord(item);
    const taxonomy = requiredTaxonomy(record.taxonomy);
    if (seen.has(taxonomy)) throw new Error("The taxonomy catalog contains a duplicate taxonomy.");
    seen.add(taxonomy);
    const parameters = record.parameters;
    if (!Array.isArray(parameters) || parameters.length < 1 || parameters.length > 3 || parameters.some((parameter) => !isTaxonomyCatalogParameter(parameter))) throw new Error("The taxonomy catalog returned invalid executable parameters.");
    const allowedParameters = TAXONOMY_ALLOWED_CATALOG_PARAMETERS[taxonomy];
    if (parameters.length !== allowedParameters.length || new Set(parameters).size !== parameters.length || parameters.some((parameter) => !allowedParameters.includes(parameter))) throw new Error("The taxonomy catalog returned an invalid taxonomy-to-parameter mapping.");
    const kind: TaxonomyCatalogEntry["kind"] = record.kind === "reference" || record.kind === "connect.md enum" ? record.kind : (() => { throw new Error("The taxonomy catalog returned an invalid kind."); })();
    const semantics: TaxonomyCatalogEntry["semantics"] = record.semantics === "AND" || record.semantics === "OR" || record.semantics === "singleton" ? record.semantics : (() => { throw new Error("The taxonomy catalog returned invalid semantics."); })();
    const expectedMetadata = TAXONOMY_CATALOG_METADATA[taxonomy];
    if (kind !== expectedMetadata.kind || semantics !== expectedMetadata.semantics) throw new Error("The taxonomy catalog returned invalid kind or semantics for its taxonomy.");
    return { taxonomy, parameters: parameters as TaxonomyCatalogParameter[], kind, semantics, source: requiredBoundedText(record.source, "taxonomy source", 200), authority: requiredBoundedText(record.authority, "taxonomy authority", 300), currentRevision: nonNegativeInteger(record.current_revision, "taxonomy revision") };
  });
  if (seen.size !== TAXONOMY_NAMES.length) throw new Error("The taxonomy catalog is incomplete.");
  return parsed;
}

export function parseTaxonomyPage(value: unknown): TaxonomyPage {
  const record = asRecord(value);
  if (!Array.isArray(record.terms) || record.terms.length > 100) throw new Error("The taxonomy term page is invalid.");
  const nextCursor = nullableCursor(record.next_cursor, "taxonomy cursor");
  return { terms: record.terms.map(parseTaxonomyTerm), nextCursor, revision: nonNegativeInteger(record.revision, "taxonomy page revision") };
}

export function parseTaxonomyTerm(value: unknown): TaxonomyTerm {
  const record = asRecord(value);
  const taxonomy = requiredTaxonomy(record.taxonomy);
  const scheme = requiredBoundedText(record.scheme, "taxonomy scheme", 80);
  if (!TAXONOMY_SCHEME_PATTERN.test(scheme)) throw new Error("The API returned an invalid taxonomy scheme.");
  const externalId = requiredBoundedText(record.external_id, "taxonomy external id", 255);
  if (!TAXONOMY_EXTERNAL_ID_PATTERN.test(externalId)) throw new Error("The API returned an invalid taxonomy external id.");
  const canonicalId = requiredBoundedText(record.canonical_id, "taxonomy canonical id", 336);
  if (canonicalId !== `${scheme}:${externalId}`) throw new Error("The API returned a taxonomy canonical ID that does not match its source identity.");
  const filterValue = requiredFilterValue(record.filter_value);
  if (record.label !== null && (typeof record.label !== "string" || record.label.length > 280)) throw new Error("The taxonomy term label is invalid.");
  if (typeof record.label_conflict !== "boolean" || typeof record.version_conflict !== "boolean") throw new Error("The taxonomy term conflict evidence is invalid.");
  if (record.vocabulary_version !== null && (typeof record.vocabulary_version !== "string" || record.vocabulary_version.length > 100)) throw new Error("The taxonomy vocabulary version is invalid.");
  return { taxonomy, scheme, externalId, canonicalId, filterValue, label: record.label as string | null, labelConflict: record.label_conflict, vocabularyVersion: record.vocabulary_version as string | null, versionConflict: record.version_conflict };
}

export function parseTaxonomyFacets(value: unknown): TaxonomyFacets {
  if (!isPlainRecord(value)) throw new Error("The taxonomy facets response is invalid.");
  const record = value;
  const parsed: TaxonomyFacets = {};
  for (const [key, raw] of Object.entries(record)) {
    const expected = TAXONOMY_FACET_GROUPS[key];
    if (!expected) throw new Error("The taxonomy facet group is not supported.");
    if (!Array.isArray(raw)) throw new Error("The taxonomy facet group is invalid.");
    parsed[key] = raw.map((item) => {
      const entry = asRecord(item);
      const taxonomy = requiredTaxonomy(entry.taxonomy);
      const parameter = requiredTaxonomyExecutableParameter(entry.parameter);
      if (taxonomy !== expected.taxonomy || parameter !== expected.parameter) throw new Error("The taxonomy facet entry does not match its group.");
      const canonicalId = requiredBoundedText(entry.canonical_id, "taxonomy facet canonical id", 336);
      const filterValue = requiredFilterValue(entry.filter_value);
      if (entry.label !== null && (typeof entry.label !== "string" || entry.label.length > 280)) throw new Error("The taxonomy facet label is invalid.");
      if (typeof entry.label_conflict !== "boolean" || typeof entry.version_conflict !== "boolean") throw new Error("The taxonomy facet conflict evidence is invalid.");
      if (entry.vocabulary_version !== null && (typeof entry.vocabulary_version !== "string" || entry.vocabulary_version.length > 100)) throw new Error("The taxonomy facet vocabulary version is invalid.");
      return { taxonomy, parameter, canonicalId, filterValue, label: entry.label as string | null, labelConflict: entry.label_conflict, vocabularyVersion: entry.vocabulary_version as string | null, versionConflict: entry.version_conflict, count: nonNegativeInteger(entry.count, "taxonomy facet count") };
    });
  }
  return parsed;
}

export function isTaxonomyFilterValue(value: string) { return TAXONOMY_FILTER_VALUE_PATTERN.test(value); }
export function isTaxonomyFacetIdentity(option: Pick<TaxonomyFacetEntry, "taxonomy" | "parameter" | "filterValue">) { return TAXONOMY_FILTER_VALUE_PATTERN.test(option.filterValue) && TAXONOMY_BY_EXECUTABLE_PARAMETER[option.parameter] === option.taxonomy; }

function asRecord(value: unknown): Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function isPlainRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function requiredFilterValue(value: unknown) { if (typeof value !== "string" || !TAXONOMY_FILTER_VALUE_PATTERN.test(value)) throw new Error("The API returned an invalid taxonomy filter alias."); return value; }
function nullableCursor(value: unknown, label: string) { if (value === null) return null; if (typeof value !== "string" || !value || value.length > 2048) throw new Error(`The API returned an invalid ${label}.`); return value; }
function isTaxonomyName(value: unknown): value is TaxonomyName { return typeof value === "string" && (TAXONOMY_NAMES as readonly string[]).includes(value); }
function requiredTaxonomy(value: unknown) { if (!isTaxonomyName(value)) throw new Error("The API returned an invalid taxonomy name."); return value; }
function isTaxonomyExecutableParameter(value: unknown): value is TaxonomyExecutableParameter { return typeof value === "string" && (TAXONOMY_EXECUTABLE_PARAMETERS as readonly string[]).includes(value); }
function isTaxonomyCatalogParameter(value: unknown): value is TaxonomyCatalogParameter { return typeof value === "string" && (TAXONOMY_CATALOG_PARAMETERS as readonly string[]).includes(value); }
function requiredTaxonomyExecutableParameter(value: unknown) { if (!isTaxonomyExecutableParameter(value)) throw new Error("The API returned an invalid taxonomy executable parameter."); return value; }
function requiredText(value: unknown, label: string) { if (typeof value !== "string" || !value) throw new Error(`The API returned an invalid ${label}.`); return value; }
function requiredBoundedText(value: unknown, label: string, maxLength: number) { const result = requiredText(value, label); if (result.length > maxLength) throw new Error(`The API returned an invalid ${label}.`); return result; }
function nonNegativeInteger(value: unknown, label: string) { if (typeof value !== "number" || !Number.isInteger(value) || value < 0) throw new Error(`The API returned an invalid ${label}.`); return value; }
