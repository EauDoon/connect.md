import { ApiRequestError, apiRequest } from "@/lib/api";
import { PRODUCT_ENDPOINTS } from "@/lib/product-endpoints";
import {
  INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY,
  isSafeExactSearchCursor,
  parseDirectorySearchResponse,
  type DirectorySearchResponse,
  type SearchAgentCapability,
  type SearchMode
} from "@/lib/public-search-contract";
import { isTaxonomyFacetIdentity, isTaxonomyFilterValue, type TaxonomyFacetEntry } from "@/lib/taxonomy-api";

export { INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY, parseDirectorySearchResponse, SEARCH_MODES } from "@/lib/public-search-contract";
export type { DirectoryHit, DirectorySearchResponse, SearchAgentCapability, SearchAgentIdentity, SearchFacetOption, SearchFacets, SearchMode } from "@/lib/public-search-contract";

export const SEARCH_FACET_NAMES = ["kind", "occupation_ids", "industry_ids", "skill_ids", "language_ids", "location_country_code", "seniority_id", "work_modes", "availability_status", "open_to_ids", "organization_ids", "representation_status", "contact_disclosure"] as const;

export type SearchFilters = {
  mode: SearchMode;
  q: string;
  kind: "" | "profile" | "resume";
  skills: string[];
  occupationIds: string[];
  industryIds: string[];
  skillIds: string[];
  languageIds: string[];
  locationId: string;
  locationLabel: string;
  locationCountryCode: string;
  locationRegion: string;
  locationCity: string;
  seniorityIds: string[];
  workModes: string[];
  availabilityStatus: string;
  availabilityFrom: string;
  openTo: string[];
  organizationIds: string[];
  representativeIds: string[];
  invalidTypedValues: string[];
  invalidSearchValues: string[];
  agentCapability: "" | SearchAgentCapability;
  representationStatus: string;
  contactDisclosure: string;
  updatedSince: string;
  offset: number;
  limit: number;
  cursor: string | null;
};

export const emptySearchFilters: SearchFilters = {
  mode: "projection",
  q: "",
  kind: "",
  skills: [],
  occupationIds: [],
  industryIds: [],
  skillIds: [],
  languageIds: [],
  locationId: "",
  locationLabel: "",
  locationCountryCode: "",
  locationRegion: "",
  locationCity: "",
  seniorityIds: [],
  workModes: [],
  availabilityStatus: "",
  availabilityFrom: "",
  openTo: [],
  organizationIds: [],
  representativeIds: [],
  invalidTypedValues: [],
  invalidSearchValues: [],
  agentCapability: "",
  representationStatus: "",
  contactDisclosure: "",
  updatedSince: "",
  offset: 0,
  limit: 20,
  cursor: null
};

export function searchFiltersFromParams(params: URLSearchParams): SearchFilters {
  const invalidTypedValues: string[] = [];
  const invalidSearchValues: string[] = [];
  const singleValue = (key: string) => {
    const values = params.getAll(key);
    if (values.length > 1) {
      invalidSearchValues.push(`${key}:multiple`);
      return null;
    }
    return values[0] ?? null;
  };
  const modeValue = singleValue("mode");
  const mode: SearchMode = modeValue === null || modeValue === "projection"
    ? "projection"
    : modeValue === "exact"
      ? "exact"
      : (() => {
        invalidSearchValues.push(`mode:${modeValue}`);
        return "projection" as const;
      })();
  const kindValue = singleValue("kind");
  const kind = kindValue === null || kindValue === ""
    ? ""
    : kindValue === "profile" || kindValue === "resume"
      ? kindValue
      : (() => {
        invalidSearchValues.push(`kind:${kindValue}`);
        return "" as const;
      })();
  const pageInteger = (key: string, fallback: number, minimum: number, maximum: number) => {
    const value = singleValue(key);
    if (value === null) return fallback;
    if (!/^(?:0|[1-9][0-9]*)$/u.test(value)) {
      invalidSearchValues.push(`${key}:invalid`);
      return fallback;
    }
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
      invalidSearchValues.push(`${key}:out_of_range`);
      return fallback;
    }
    return parsed;
  };
  const offset = pageInteger("offset", 0, 0, 1000);
  const limit = pageInteger("limit", 20, 1, 50);
  const cursorValue = singleValue("cursor");
  const cursor = cursorValue === null
    ? null
    : isSafeExactSearchCursor(cursorValue)
      ? cursorValue
      : (() => {
        invalidSearchValues.push("cursor:invalid");
        return null;
      })();
  const agentCapabilityValue = singleValue("agent_capability");
  const agentCapability: "" | SearchAgentCapability = agentCapabilityValue === null || agentCapabilityValue === ""
    ? ""
    : agentCapabilityValue === INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY
      ? INTERNAL_CONTACT_REQUEST_AGENT_CAPABILITY
      : (() => {
        invalidSearchValues.push(`agent_capability:${agentCapabilityValue}`);
        return "" as const;
      })();
  if (mode === "exact" && offset !== 0) invalidSearchValues.push("offset:exact_requires_zero");
  if (mode === "projection" && cursor !== null) invalidSearchValues.push("cursor:projection_unsupported");
  const typedList = (key: string) => {
    const values = list(params, key);
    const valid = values.filter((value) => isTaxonomyFilterValue(value));
    invalidTypedValues.push(...values.filter((value) => !isTaxonomyFilterValue(value)).map((value) => `${key}:${value}`));
    return valid;
  };
  const typedLegacyScalar = (key: string) => {
    const values = legacyScalar(params, key);
    const valid = values.filter((value) => isTaxonomyFilterValue(value));
    invalidTypedValues.push(...values.filter((value) => !isTaxonomyFilterValue(value)).map((value) => `${key}:${value}`));
    return valid;
  };
  const locationValues = list(params, "location_id");
  const validLocationValues = locationValues.filter((value) => isTaxonomyFilterValue(value));
  invalidTypedValues.push(...locationValues.filter((value) => !isTaxonomyFilterValue(value)).map((value) => `location_id:${value}`));
  if (validLocationValues.length > 1) invalidTypedValues.push("location_id:multiple");
  const openToIds = typedList("open_to_ids");
  const legacyOpenToValues = typedList("open_to");
  const legacyOpenTo = openToIds.length > 0 || params.has("open_to_ids") ? [] : legacyOpenToValues;
  const rawLocation = clean(params.get("location"));
  const rawLocationLabel = clean(params.get("location_label"));
  if (rawLocation) invalidTypedValues.push(`location:${rawLocation}`);
  if (rawLocationLabel) invalidTypedValues.push(`location_label:${rawLocationLabel}`);
  return {
    mode,
    q: clean(params.get("q")),
    kind,
    skills: list(params, "skills"),
    occupationIds: typedList("occupation_ids"),
    industryIds: typedList("industry_ids"),
    skillIds: typedList("skill_ids"),
    languageIds: typedList("language_ids"),
    locationId: validLocationValues[0] ?? "",
    locationLabel: rawLocation || rawLocationLabel,
    locationCountryCode: clean(params.get("location_country_code")).toUpperCase(),
    locationRegion: clean(params.get("location_region")),
    locationCity: clean(params.get("location_city")),
    seniorityIds: [...typedList("seniority_ids"), ...typedLegacyScalar("seniority_id")],
    workModes: typedList("work_modes"),
    availabilityStatus: clean(params.get("availability_status")),
    availabilityFrom: clean(params.get("availability_from")),
    openTo: openToIds.length > 0 || params.has("open_to_ids") ? openToIds : legacyOpenTo,
    organizationIds: typedList("organization_ids"),
    representativeIds: typedList("representative_ids"),
    invalidTypedValues,
    invalidSearchValues,
    agentCapability,
    representationStatus: clean(params.get("representation_status")),
    contactDisclosure: clean(params.get("contact_disclosure")),
    updatedSince: clean(params.get("updated_after") ?? params.get("updated_since")),
    offset,
    limit,
    cursor
  };
}

export function searchParamsFromFilters(filters: SearchFilters) {
  const params = new URLSearchParams();
  if (filters.mode === "exact") params.set("mode", "exact");
  set(params, "q", filters.q);
  set(params, "kind", filters.kind);
  set(params, "agent_capability", filters.agentCapability);
  addAll(params, "skills", filters.skills);
  addAliases(params, "occupation_ids", filters.occupationIds);
  addAliases(params, "industry_ids", filters.industryIds);
  addAliases(params, "skill_ids", filters.skillIds);
  addAliases(params, "language_ids", filters.languageIds);
  if (isTaxonomyFilterValue(filters.locationId)) params.set("location_id", filters.locationId);
  set(params, "location_country_code", filters.locationCountryCode);
  set(params, "location_region", filters.locationRegion);
  set(params, "location_city", filters.locationCity);
  addAliases(params, "seniority_ids", filters.seniorityIds);
  addAliases(params, "work_modes", filters.workModes);
  set(params, "availability_status", filters.availabilityStatus);
  set(params, "availability_from", filters.availabilityFrom);
  addAliases(params, "open_to_ids", filters.openTo);
  addAliases(params, "organization_ids", filters.organizationIds);
  addAliases(params, "representative_ids", filters.representativeIds);
  set(params, "representation_status", filters.representationStatus);
  set(params, "contact_disclosure", filters.contactDisclosure);
  set(params, "updated_after", filters.updatedSince);
  if (filters.mode === "exact") {
    params.set("offset", "0");
    if (filters.cursor) params.set("cursor", filters.cursor);
  } else if (filters.offset) params.set("offset", String(filters.offset));
  if (filters.limit !== 20) params.set("limit", String(filters.limit));
  return params;
}

export async function searchDirectory(filters: SearchFilters) {
  assertValidSearchFilters(filters);
  const params = searchParamsFromFilters(filters);
  if (filters.invalidTypedValues.length > 0) throw new ApiRequestError("The URL contains unsupported legacy taxonomy values. Choose authoritative registry aliases before searching.", 422, "request");
  const repeatedValues = countSearchRepeatedValues(filters);
  if (repeatedValues > 50) throw new ApiRequestError("Search filters exceed the 50-value request limit. Remove a repeated filter before searching.", 422, "request");
  const updatedAfter = apiUpdatedAfter(filters.updatedSince);
  if (updatedAfter) params.set("updated_after", updatedAfter);
  searchFacetNamesForFilters(filters).forEach((facet) => params.append("facets", facet));
  const raw = await apiRequest<unknown>(`${PRODUCT_ENDPOINTS.search}?${params.toString()}`, { server: true });
  const response = parseDirectorySearchResponse(raw);
  if (response.mode !== filters.mode) throw new Error("The API returned a search mode that does not match the requested mode.");
  if (filters.mode === "exact" && response.offset !== 0) throw new Error("The API returned an invalid exact search offset.");
  return response;
}

export function applyTaxonomyFacet(filters: SearchFilters, option: TaxonomyFacetEntry): SearchFilters {
  if (!isTaxonomyFacetIdentity(option)) throw new Error("The taxonomy facet identity is invalid.");
  const next = resetSearchPage(filters);
  switch (option.parameter) {
    case "location_id": return { ...next, locationId: next.locationId === option.filterValue ? "" : option.filterValue };
    case "occupation_ids": return { ...next, occupationIds: toggleAlias(next.occupationIds, option.filterValue) };
    case "industry_ids": return { ...next, industryIds: toggleAlias(next.industryIds, option.filterValue) };
    case "skill_ids": return { ...next, skillIds: toggleAlias(next.skillIds, option.filterValue) };
    case "language_ids": return { ...next, languageIds: toggleAlias(next.languageIds, option.filterValue) };
    case "seniority_ids": return { ...next, seniorityIds: toggleAlias(next.seniorityIds, option.filterValue) };
    case "open_to_ids": return { ...next, openTo: toggleAlias(next.openTo, option.filterValue) };
    case "organization_ids": return { ...next, organizationIds: toggleAlias(next.organizationIds, option.filterValue) };
    case "representative_ids": return { ...next, representativeIds: toggleAlias(next.representativeIds, option.filterValue) };
    case "work_modes": return { ...next, workModes: toggleAlias(next.workModes, option.filterValue) };
  }
}

export function countSearchRepeatedValues(filters: SearchFilters) {
  return [filters.skills, filters.occupationIds, filters.industryIds, filters.skillIds, filters.languageIds, filters.seniorityIds, filters.workModes, filters.openTo, filters.organizationIds, filters.representativeIds].reduce((total, values) => total + values.length, 0);
}

export function searchFacetNamesForFilters(filters: SearchFilters) {
  const remaining = Math.max(0, 50 - countSearchRepeatedValues(filters));
  return SEARCH_FACET_NAMES.slice(0, Math.min(SEARCH_FACET_NAMES.length, remaining));
}

export function isSupportedSearchFacet(key: string) {
  return ["kind", "location_country_code", "availability_status", "representation_status", "contact_disclosure"].includes(key);
}

export function toggleSearchFacet(filters: SearchFilters, key: string, value: string): SearchFilters | null {
  switch (key) {
    case "kind": return { ...resetSearchPage(filters), kind: filters.kind === value ? "" : value === "profile" || value === "resume" ? value : filters.kind };
    case "location_country_code": return { ...resetSearchPage(filters), locationCountryCode: filters.locationCountryCode === value ? "" : value.toUpperCase() };
    case "availability_status": return { ...resetSearchPage(filters), availabilityStatus: filters.availabilityStatus === value ? "" : value };
    case "representation_status": return { ...resetSearchPage(filters), representationStatus: filters.representationStatus === value ? "" : value };
    case "contact_disclosure": return { ...resetSearchPage(filters), contactDisclosure: filters.contactDisclosure === value ? "" : value };
    default: return null;
  }
}

function assertValidSearchFilters(filters: SearchFilters) {
  if (filters.invalidSearchValues.length > 0) throw new ApiRequestError("The search URL contains invalid or incompatible mode, page, cursor, or agent-capability values. Restart with supported search controls.", 422, "request");
  if (filters.mode === "exact" && filters.offset !== 0) throw new ApiRequestError("Exact search requires offset 0 and server-issued cursors.", 422, "request");
  if (filters.mode === "projection" && filters.cursor !== null) throw new ApiRequestError("Projection search does not accept an exact-search cursor.", 422, "request");
  if (filters.cursor !== null && !isSafeExactSearchCursor(filters.cursor)) throw new ApiRequestError("The exact-search cursor is invalid.", 422, "request");
}
function resetSearchPage(filters: SearchFilters): SearchFilters { return { ...filters, offset: 0, cursor: null }; }
function clean(value: string | null) { return value?.trim() ?? ""; }
function toggleAlias(values: string[], value: string) { return values.includes(value) ? values.filter((item) => item !== value) : [...values, value]; }
function legacyScalar(params: URLSearchParams, key: string) { const value = clean(params.get(key)); return value ? [value] : []; }
function list(params: URLSearchParams, key: string) { return params.getAll(key).flatMap((value) => value.split(",")).map((value) => value.trim()).filter(Boolean); }
function set(params: URLSearchParams, key: string, value: string) { if (value) params.set(key, value); }
function addAll(params: URLSearchParams, key: string, values: string[]) { values.forEach((value) => { if (value) params.append(key, value); }); }
function addAliases(params: URLSearchParams, key: string, values: string[]) { addAll(params, key, values.filter(isTaxonomyFilterValue)); }
function apiUpdatedAfter(value: string) { return /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value; }
