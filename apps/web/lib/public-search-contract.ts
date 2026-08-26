import { isTaxonomyFilterValue, parseTaxonomyFacets, type TaxonomyFacets } from "@/lib/taxonomy-api";

export const SEARCH_MODES = ["projection", "exact"] as const;
export type SearchMode = (typeof SEARCH_MODES)[number];
export const INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY = "internal_contact_request" as const;
export type SearchAgentCapability = typeof INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY;

const SEARCH_AGENT_HANDLE_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/u;
const PUBLIC_DOCUMENT_IDENTIFIER_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/u;
const EXACT_SEARCH_CURSOR_MAX_LENGTH = 2048;

export type DirectoryHit = {
  id: string;
  kind: "profile" | "resume";
  identifier: string;
  name: string;
  headline: string;
  title: string | null;
  occupationIds: string[];
  occupations: string[];
  industryIds: string[];
  industries: string[];
  skillIds: string[];
  skills: string[];
  languageIds: string[];
  languages: string[];
  locationId: string | null;
  locationLabel: string;
  locationCountryCode: string | null;
  locationRegion: string | null;
  locationCity: string | null;
  seniorityIds: string[];
  seniorityId: string | null;
  seniority: string[];
  workModes: string[];
  availabilityStatus: string | null;
  availabilityFrom: string | null;
  openTo: string[];
  organizationIds: string[];
  representativeIds: string[];
  representativeId: string | null;
  occupationFilterValues: string[];
  industryFilterValues: string[];
  skillFilterValues: string[];
  languageFilterValues: string[];
  locationFilterValue: string | null;
  seniorityFilterValue: string | null;
  seniorityFilterValues: string[];
  openToFilterValues: string[];
  organizationFilterValues: string[];
  representativeFilterValue: string | null;
  representativeFilterValues: string[];
  workModeFilterValues: string[];
  organizations: string[];
  representationStatus: string | null;
  contactDisclosure: string | null;
  updatedAt: string | null;
  schemaVersion: number;
  version: number;
  excerpt: string | null;
  htmlUrl: string;
  markdownUrl: string;
  agentIdentities: SearchAgentIdentity[];
};

export type SearchAgentIdentity = {
  handle: string;
  capabilities: [SearchAgentCapability];
};

export type SearchFacetOption = { value: string; label: string; count: number };
export type SearchFacets = Record<string, SearchFacetOption[]>;
export type DirectorySearchResponse = {
  hits: DirectoryHit[];
  offset: number;
  limit: number;
  total: number;
  indexingAvailable: boolean;
  warning: string | null;
  facets: SearchFacets;
  taxonomyFacets: TaxonomyFacets;
  mode: SearchMode;
  nextCursor: string | null;
  searchRevision: number | null;
  complete: boolean;
  facetTruncated: Record<string, boolean>;
};

export function parseDirectorySearchResponse(value: unknown): DirectorySearchResponse {
  const record = asRecord(value);
  if (!Object.prototype.hasOwnProperty.call(record, "taxonomy_facets")) throw new Error("The API response omitted authoritative taxonomy facets.");
  const taxonomyFacets = parseTaxonomyFacets(record.taxonomy_facets);
  if (!Array.isArray(record.hits)) throw new Error("The API response returned invalid search hits.");
  const hits = record.hits.map(parseDirectoryHit);
  if (!isPlainRecord(record.facets)) throw new Error("The API response returned invalid search facets.");
  if (typeof record.indexing_available !== "boolean") throw new Error("The API response returned invalid search availability.");
  const mode = record.mode === "projection" || record.mode === "exact" ? record.mode : (() => { throw new Error("The API response returned an invalid search mode."); })();
  const offset = boundedInteger(record.offset, "search offset", 0, 1000);
  const nextCursor = nullableSearchCursor(record.next_cursor, "search cursor");
  const searchRevision = nullableNonNegativeInteger(record.search_revision, "search revision");
  if (typeof record.complete !== "boolean") throw new Error("The API response returned an invalid search completeness state.");
  if (!isPlainRecord(record.facet_truncated)) throw new Error("The API response returned invalid facet truncation state.");
  const facetTruncated = parseFacetTruncated(record.facet_truncated);
  const warning = nullableBoundedText(record.warning, "search warning", 1000);
  if (mode === "projection" && (nextCursor !== null || searchRevision !== null || record.complete !== false)) throw new Error("The API response returned an invalid projection search state.");
  if (mode === "exact" && (offset !== 0 || searchRevision === null || record.indexing_available !== true)) throw new Error("The API response returned an invalid exact search state.");
  return {
    hits,
    offset,
    limit: boundedInteger(record.limit, "search limit", 1, 50),
    total: nonNegativeInteger(record.total, "search total"),
    indexingAvailable: record.indexing_available,
    warning,
    facets: parseFacets(record.facets),
    taxonomyFacets,
    mode,
    nextCursor,
    searchRevision,
    complete: record.complete,
    facetTruncated
  };
}

function parseDirectoryHit(value: unknown): DirectoryHit {
  const hit = asRecord(value);
  if (typeof hit.id !== "string" || !hit.id || (hit.kind !== "profile" && hit.kind !== "resume") || typeof hit.identifier !== "string" || !hit.identifier || typeof hit.name !== "string" || !hit.name) throw new Error("The API response returned an invalid search hit identity.");
  const location = asRecord(hit.location);
  const identifier = hit.identifier;
  if (!PUBLIC_DOCUMENT_IDENTIFIER_PATTERN.test(identifier)) throw new Error("The API response returned an invalid search hit identifier.");
  const htmlUrl = text(hit.html_url);
  const expectedHtmlUrl = hit.kind === "profile" ? `/p/${encodeURIComponent(identifier)}` : `/r/${encodeURIComponent(identifier)}`;
  if (htmlUrl !== expectedHtmlUrl) throw new Error("The API response returned an invalid search hit HTML URL.");
  const markdownUrl = text(hit.markdown_url);
  const expectedMarkdownUrl = hit.kind === "profile"
    ? `/v1/profiles/${encodeURIComponent(identifier)}.md`
    : `/v1/resumes/${encodeURIComponent(identifier)}.md`;
  if (markdownUrl !== expectedMarkdownUrl) throw new Error("The API response returned an invalid search hit Markdown URL.");
  const occupationIds = requiredStringArray(hit, "occupation_ids", 336);
  const industryIds = requiredStringArray(hit, "industry_ids", 336);
  const skillIds = requiredStringArray(hit, "skill_ids", 336);
  const languageIds = requiredStringArray(hit, "language_ids", 336);
  const seniorityIds = requiredStringArray(hit, "seniority_ids", 336);
  const workModes = requiredStringArray(hit, "work_modes", 160);
  const openToIds = requiredStringArray(hit, "open_to_ids", 336);
  const organizationIds = requiredStringArray(hit, "organization_ids", 336);
  const representativeIds = requiredStringArray(hit, "representative_ids", 336);
  const openTo = requiredStringArray(hit, "open_to", 280);
  const occupationFilterValues = requiredFilterValues(hit, "occupation_filter_values");
  const industryFilterValues = requiredFilterValues(hit, "industry_filter_values");
  const skillFilterValues = requiredFilterValues(hit, "skill_filter_values");
  const languageFilterValues = requiredFilterValues(hit, "language_filter_values");
  const locationFilterValue = requiredNullableFilterValue(hit, "location_filter_value");
  const seniorityFilterValue = requiredNullableFilterValue(hit, "seniority_filter_value");
  const seniorityFilterValues = requiredFilterValues(hit, "seniority_filter_values");
  const openToFilterValues = requiredFilterValues(hit, "open_to_filter_values");
  const organizationFilterValues = requiredFilterValues(hit, "organization_filter_values");
  const representativeFilterValue = requiredNullableFilterValue(hit, "representative_filter_value");
  const representativeFilterValues = requiredFilterValues(hit, "representative_filter_values");
  const workModeFilterValues = requiredFilterValues(hit, "work_mode_filter_values");
  const locationId = requiredNullableBoundedText(hit, "location_id", 336);
  const seniorityId = requiredNullableBoundedText(hit, "seniority_id", 336);
  const representativeId = requiredNullableBoundedText(hit, "representative_id", 336);
  const agentIdentities = parseSearchAgentIdentities(hit.agent_identities);
  if (hit.kind === "resume" && agentIdentities.length > 0) throw new Error("The API response returned Agent Identity references for a resume.");
  return {
    id: hit.id,
    kind: hit.kind,
    identifier,
    name: hit.name,
    headline: text(hit.headline),
    title: textOrNull(hit.title),
    occupationIds,
    occupations: labels(hit.occupations),
    industryIds,
    industries: labels(hit.industries),
    skillIds,
    skills: labels(hit.skills),
    languageIds,
    languages: labels(hit.languages),
    locationId,
    locationLabel: text(hit.location_label) || text(hit.location) || text(location.label),
    locationCountryCode: textOrNull(hit.location_country_code ?? location.country_code),
    locationRegion: textOrNull(hit.location_region ?? location.region),
    locationCity: textOrNull(hit.location_city ?? location.city),
    seniorityIds,
    seniorityId,
    seniority: Array.isArray(hit.seniority) ? labels(hit.seniority) : text(hit.seniority) ? [text(hit.seniority)] : [],
    workModes,
    availabilityStatus: textOrNull(hit.availability_status),
    availabilityFrom: textOrNull(hit.availability_from),
    openTo,
    organizationIds,
    representativeIds,
    representativeId,
    occupationFilterValues,
    industryFilterValues,
    skillFilterValues,
    languageFilterValues,
    locationFilterValue,
    seniorityFilterValue,
    seniorityFilterValues,
    openToFilterValues,
    organizationFilterValues,
    representativeFilterValue,
    representativeFilterValues,
    workModeFilterValues,
    organizations: labels(hit.organizations),
    representationStatus: textOrNull(hit.representation_status),
    contactDisclosure: textOrNull(hit.contact_disclosure),
    updatedAt: textOrNull(hit.updated_at),
    schemaVersion: integer(hit.schema_version, 1),
    version: integer(hit.version, 1),
    excerpt: textOrNull(hit.excerpt),
    htmlUrl,
    markdownUrl,
    agentIdentities
  };
}

function parseFacets(value: unknown): SearchFacets {
  const facets: SearchFacets = {};
  for (const [key, raw] of Object.entries(asRecord(value))) {
    if (Array.isArray(raw)) {
      facets[key] = raw.map((item) => {
        const option = asRecord(item);
        return { value: text(option.value), label: text(option.label) || text(option.value), count: integer(option.count, 0) };
      }).filter((item) => item.value);
    } else {
      facets[key] = Object.entries(asRecord(raw)).map(([option, count]) => ({ value: option, label: option, count: integer(count, 0) }));
    }
  }
  return facets;
}

function requiredStringArray(record: Record<string, unknown>, key: string, maxLength: number) {
  if (!Object.prototype.hasOwnProperty.call(record, key) || !Array.isArray(record[key])) throw new Error(`The API returned invalid ${key}.`);
  return record[key].map((item) => requiredBoundedText(item, key, maxLength));
}
function requiredFilterValues(record: Record<string, unknown>, key: string) {
  if (!Object.prototype.hasOwnProperty.call(record, key) || !Array.isArray(record[key])) throw new Error(`The API returned invalid ${key}.`);
  return record[key].map((item) => requiredFilterValue(item));
}
function requiredNullableFilterValue(record: Record<string, unknown>, key: string) {
  if (!Object.prototype.hasOwnProperty.call(record, key)) throw new Error(`The API returned invalid ${key}.`);
  if (record[key] === null) return null;
  return requiredFilterValue(record[key]);
}
function requiredNullableBoundedText(record: Record<string, unknown>, key: string, maxLength: number) {
  if (!Object.prototype.hasOwnProperty.call(record, key)) throw new Error(`The API returned invalid ${key}.`);
  if (record[key] === null) return null;
  return requiredBoundedText(record[key], key, maxLength);
}
function asRecord(value: unknown): Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function isPlainRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function parseSearchAgentIdentities(value: unknown): SearchAgentIdentity[] {
  if (!Array.isArray(value) || value.length > 10) throw new Error("The API response returned invalid search Agent Identity references.");
  return value.map((item) => {
    const record = asRecord(item);
    const handle = requiredBoundedText(record.handle, "search Agent Identity handle", 100);
    if (!SEARCH_AGENT_HANDLE_PATTERN.test(handle)) throw new Error("The API response returned an invalid search Agent Identity handle.");
    if (!Array.isArray(record.capabilities) || record.capabilities.length !== 1 || record.capabilities[0] !== INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY) throw new Error("The API response returned invalid search Agent Identity capabilities.");
    return { handle, capabilities: [INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY] };
  });
}
function parseFacetTruncated(value: unknown): Record<string, boolean> {
  const record = asRecord(value);
  return Object.fromEntries(Object.entries(record).map(([facet, truncated]) => {
    if (!facet || facet.length > 80 || /[\u0000-\u001F\u007F]/u.test(facet) || typeof truncated !== "boolean") throw new Error("The API response returned invalid facet truncation state.");
    return [facet, truncated];
  }));
}
function nullableSearchCursor(value: unknown, label: string) {
  const cursor = nullableCursor(value, label);
  if (cursor !== null && !isSafeExactSearchCursor(cursor)) throw new Error(`The API response returned an invalid ${label}.`);
  return cursor;
}
function nullableNonNegativeInteger(value: unknown, label: string) { return value === null ? null : nonNegativeInteger(value, label); }
function nullableBoundedText(value: unknown, label: string, maximum: number) {
  if (value === null) return null;
  if (typeof value !== "string" || value.length > maximum) throw new Error(`The API response returned an invalid ${label}.`);
  return value;
}
export function isSafeExactSearchCursor(value: string) { return value.length > 0 && value.length <= EXACT_SEARCH_CURSOR_MAX_LENGTH && !/[\u0000-\u001F\u007F]/u.test(value); }
function text(value: unknown) { return typeof value === "string" ? value : ""; }
function textOrNull(value: unknown) { return typeof value === "string" && value ? value : null; }
function labels(value: unknown) { return Array.isArray(value) ? value.map((item) => typeof item === "string" ? item : text(asRecord(item).label)).filter(Boolean) : []; }
function integer(value: unknown, fallback: number) { return typeof value === "number" && Number.isInteger(value) ? value : fallback; }
function requiredText(value: unknown, label: string) { if (typeof value !== "string" || !value) throw new Error(`The API returned an invalid ${label}.`); return value; }
function requiredBoundedText(value: unknown, label: string, maxLength: number) { const result = requiredText(value, label); if (result.length > maxLength) throw new Error(`The API returned an invalid ${label}.`); return result; }
function nonNegativeInteger(value: unknown, label: string) { if (typeof value !== "number" || !Number.isInteger(value) || value < 0) throw new Error(`The API returned an invalid ${label}.`); return value; }
function boundedInteger(value: unknown, label: string, minimum: number, maximum: number) { const result = nonNegativeInteger(value, label); if (result < minimum || result > maximum) throw new Error(`The API returned an invalid ${label}.`); return result; }
function requiredFilterValue(value: unknown) { if (typeof value !== "string" || !isTaxonomyFilterValue(value)) throw new Error("The API returned an invalid taxonomy filter alias."); return value; }
function nullableCursor(value: unknown, label: string) { if (value === null) return null; if (typeof value !== "string" || !value || value.length > 2048) throw new Error(`The API returned an invalid ${label}.`); return value; }
