import { afterEach, describe, expect, it, vi } from "vitest";

import { applyTaxonomyFacet, countSearchRepeatedValues, emptySearchFilters, parseDirectorySearchResponse, searchDirectory, searchFacetNamesForFilters, searchFiltersFromParams, searchParamsFromFilters, toggleSearchFacet } from "../lib/public-search-api";
import { listTaxonomies, listTaxonomyTerms, parseTaxonomyCatalog, parseTaxonomyFacets, parseTaxonomyPage } from "../lib/taxonomy-api";

const taxonomyCatalogFixture = () => [
  { taxonomy: "occupation", parameters: ["occupation_ids"], kind: "reference", semantics: "AND", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "industry", parameters: ["industry_ids"], kind: "reference", semantics: "AND", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "location", parameters: ["location_id"], kind: "reference", semantics: "singleton", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "skill", parameters: ["skill_ids"], kind: "reference", semantics: "AND", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "language", parameters: ["language_ids"], kind: "reference", semantics: "AND", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "seniority", parameters: ["seniority_ids", "seniority_id"], kind: "reference", semantics: "OR", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "open_to", parameters: ["open_to_ids", "open_to"], kind: "reference", semantics: "AND", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "organization", parameters: ["organization_ids"], kind: "reference", semantics: "AND", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "representative", parameters: ["representative_ids"], kind: "reference", semantics: "OR", source: "registry", authority: "owner", current_revision: 4 },
  { taxonomy: "work_mode", parameters: ["work_modes"], kind: "connect.md enum", semantics: "AND", source: "registry", authority: "owner", current_revision: 4 }
];
const projectionSearchEnvelope = { hits: [], offset: 0, limit: 20, total: 0, indexing_available: true, warning: null, facets: {}, taxonomy_facets: {}, mode: "projection", next_cursor: null, search_revision: null, complete: false, facet_truncated: {} };

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("public search and taxonomy API contracts", () => {
  it("round-trips alias filters while rejecting raw location and typed values before fetch", async () => {
    const input = new URLSearchParams("q=payments&occupation_ids=tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&industry_ids=tx1_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb&skill_ids=tx1_cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc&location_id=tx1_dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd&location=Singapore&location_country_code=sg&open_to=legacy-open-to&open_to_ids=tx1_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee&representative_ids=tx1_ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff&representation_status=authorized_representative&contact_disclosure=platform_only&updated_after=2026-08-01");
    const filters = searchFiltersFromParams(input);
    const output = searchParamsFromFilters(filters);

    expect(filters).toMatchObject({
      q: "payments",
      occupationIds: ["tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
      industryIds: ["tx1_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
      skillIds: ["tx1_cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"],
      locationLabel: "Singapore",
      locationCountryCode: "SG",
      openTo: ["tx1_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"],
      representativeIds: ["tx1_ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"],
      representationStatus: "authorized_representative",
      contactDisclosure: "platform_only",
      updatedSince: "2026-08-01"
    });
    expect(filters.invalidTypedValues).toEqual(["open_to:legacy-open-to", "location:Singapore"]);
    expect(output.getAll("industry_ids")).toEqual(["tx1_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]);
    expect(output.getAll("open_to_ids")).toEqual(["tx1_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"]);
    expect(output.getAll("open_to")).toEqual([]);
    expect(output.getAll("representative_ids")).toEqual(["tx1_ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"]);
    expect(output.get("location")).toBeNull();
    expect(output.get("location_label")).toBeNull();
    expect(output.get("updated_after")).toBe("2026-08-01");
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    await expect(searchDirectory(filters)).rejects.toMatchObject({ status: 422 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reads legacy open_to values but writes only open_to_ids", () => {
    const legacy = searchFiltersFromParams(new URLSearchParams("open_to=advisory&open_to=partnerships"));
    expect(legacy.openTo).toEqual([]);
    expect(legacy.invalidTypedValues).toEqual(["open_to:advisory", "open_to:partnerships"]);
    expect(searchParamsFromFilters(legacy).getAll("open_to_ids")).toEqual([]);
    expect(searchParamsFromFilters(legacy).getAll("open_to")).toEqual([]);
  });

  it("rejects repeated singleton location aliases before dispatch", async () => {
    const first = "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const second = "tx1_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const filters = searchFiltersFromParams(new URLSearchParams(`location_id=${first}&location_id=${second}`));
    expect(filters.locationId).toBe(first);
    expect(filters.invalidTypedValues).toContain("location_id:multiple");
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    await expect(searchDirectory(filters)).rejects.toMatchObject({ status: 422 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("strictly parses taxonomy catalog, pages, aliases, and conflict evidence", () => {
    const alias = "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    expect(parseTaxonomyCatalog(taxonomyCatalogFixture())).toHaveLength(10);
    expect(parseTaxonomyCatalog(taxonomyCatalogFixture()).find((entry) => entry.taxonomy === "seniority")?.parameters).toEqual(["seniority_ids", "seniority_id"]);
    expect(parseTaxonomyPage({ terms: [{ taxonomy: "occupation", scheme: "isco", external_id: "123", canonical_id: "isco:123", filter_value: alias, label: null, label_conflict: true, vocabulary_version: "2026", version_conflict: false }], next_cursor: null, revision: 4 })).toMatchObject({ revision: 4, terms: [{ filterValue: alias, label: null, labelConflict: true }] });
    expect(() => parseTaxonomyCatalog({})).toThrow();
    expect(() => parseTaxonomyCatalog(taxonomyCatalogFixture().map((entry) => entry.taxonomy === "location" ? { ...entry, semantics: "AND" } : entry))).toThrow();
    expect(() => parseTaxonomyPage({ terms: [], next_cursor: "x".repeat(2049), revision: 1 })).toThrow();
    expect(() => parseTaxonomyPage({ terms: Array.from({ length: 101 }, () => ({ taxonomy: "occupation", scheme: "isco", external_id: "123", canonical_id: "isco:123", filter_value: alias, label: null, label_conflict: false, vocabulary_version: null, version_conflict: false })), next_cursor: null, revision: 1 })).toThrow();
    expect(() => parseTaxonomyPage({ terms: [{ taxonomy: "occupation", scheme: "Bad Scheme", external_id: "123", canonical_id: "Bad Scheme:123", filter_value: alias, label: null, label_conflict: false, vocabulary_version: null, version_conflict: false }], next_cursor: null, revision: 1 })).toThrow();
    expect(() => parseTaxonomyPage({ terms: [{ taxonomy: "occupation", scheme: "isco", external_id: "123", canonical_id: "other:123", filter_value: alias, label: null, label_conflict: false, vocabulary_version: null, version_conflict: false }], next_cursor: null, revision: 1 })).toThrow();
    expect(() => parseTaxonomyFacets({ occupation_ids: [{ taxonomy: "occupation", parameter: "occupation_ids", canonical_id: "isco:123", filter_value: "not-an-alias", label: "x", label_conflict: false, vocabulary_version: null, version_conflict: false, count: 1 }] })).toThrow();
  });

  it("loads taxonomy endpoints with bounded query parameters and no cache", async () => {
    const alias = "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(taxonomyCatalogFixture()), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ terms: [{ taxonomy: "occupation", scheme: "isco", external_id: "123", canonical_id: "isco:123", filter_value: alias, label: "Product", label_conflict: false, vocabulary_version: "2026", version_conflict: false }], next_cursor: "next", revision: 4 }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(listTaxonomies()).resolves.toHaveLength(10);
    await expect(listTaxonomyTerms("occupation", { q: "product manager", cursor: "cursor", limit: 25 })).resolves.toMatchObject({ nextCursor: "next", terms: [{ filterValue: alias }] });
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/taxonomies");
    expect(fetchMock.mock.calls[0][1]?.cache).toBe("no-store");
    expect(fetchMock.mock.calls[1][0]).toBe("/v1/taxonomies/occupation?limit=25&q=product+manager&cursor=cursor");
    expect(fetchMock.mock.calls[1][1]?.cache).toBe("no-store");
    await expect(listTaxonomyTerms("occupation", { q: "x".repeat(101) })).rejects.toThrow("100 characters");
  });

  it("preflights the aggregate repeated-value cap and requests only remaining facets", async () => {
    vi.stubEnv("CONNECTMD_API_BASE_URL", "https://api.test");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(projectionSearchEnvelope), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const aliases = Array.from({ length: 38 }, (_, index) => `tx1_${String(index + 1).padStart(2, "0")}${"a".repeat(62)}`);
    const filters = { ...emptySearchFilters, skillIds: aliases };
    expect(countSearchRepeatedValues(filters)).toBe(38);
    expect(searchFacetNamesForFilters(filters)).toHaveLength(12);
    await searchDirectory(filters);
    const request = new URL(String(fetchMock.mock.calls[0][0]));
    expect(request.searchParams.getAll("skill_ids")).toHaveLength(38);
    expect(request.searchParams.getAll("facets")).toHaveLength(12);
    await expect(searchDirectory({ ...emptySearchFilters, skillIds: [...aliases, ...aliases.slice(0, 13)] })).rejects.toMatchObject({ status: 422 });
    expect(countSearchRepeatedValues({ ...emptySearchFilters, skills: ["raw-skill"] })).toBe(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("normalizes a Human Mode date-only update filter for the API wire", async () => {
    vi.stubEnv("CONNECTMD_API_BASE_URL", "https://api.test");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(projectionSearchEnvelope), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await searchDirectory({ ...emptySearchFilters, updatedSince: "2026-08-01" });

    const request = new URL(String(fetchMock.mock.calls[0][0]));
    expect(request.searchParams.get("updated_after")).toBe("2026-08-01T00:00:00Z");
  });

  it("uses the exact canonical mode, server cursor, and discovery-only Agent Identity selector without a projection fallback", async () => {
    vi.stubEnv("CONNECTMD_API_BASE_URL", "https://api.test");
    const exactResponse = { ...projectionSearchEnvelope, mode: "exact", next_cursor: "server-next", search_revision: 17, complete: true, facet_truncated: { kind: true } };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(exactResponse), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const filters = searchFiltersFromParams(new URLSearchParams("mode=exact&offset=0&cursor=server-current&agent_capability=internal_contact_request&kind=profile"));

    await expect(searchDirectory(filters)).resolves.toMatchObject({ mode: "exact", nextCursor: "server-next", searchRevision: 17, complete: true, facetTruncated: { kind: true } });
    const request = new URL(String(fetchMock.mock.calls[0][0]));
    expect(request.searchParams.get("mode")).toBe("exact");
    expect(request.searchParams.get("offset")).toBe("0");
    expect(request.searchParams.get("cursor")).toBe("server-current");
    expect(request.searchParams.get("agent_capability")).toBe("internal_contact_request");

    const malformed = searchFiltersFromParams(new URLSearchParams("mode=exact&offset=1&cursor=first&cursor=second&agent_capability=not-a-capability"));
    expect(malformed.invalidSearchValues).toEqual(expect.arrayContaining(["offset:exact_requires_zero", "cursor:multiple", "agent_capability:not-a-capability"]));
    await expect(searchDirectory(malformed)).rejects.toMatchObject({ status: 422 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("accepts both MVP and enriched search hits without inventing trust", () => {
    const response = parseDirectorySearchResponse({
      hits: [{
        id: "doc-1",
        kind: "profile",
        identifier: "ari-chen",
        name: "Ari Chen",
        html_url: "/p/ari-chen",
        headline: "Product leader",
        location: "Singapore",
        skills: ["Payments"],
        version: 3,
        markdown_url: "/v1/profiles/ari-chen.md",
        occupation_ids: ["product-manager"],
        occupation_filter_values: ["tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
        occupations: [{ id: "product-manager", label: "Product manager" }],
        industry_ids: ["fintech"],
        industry_filter_values: [],
        industries: ["Financial technology"],
        skill_ids: ["payments"],
        skill_filter_values: [],
        language_ids: [],
        language_filter_values: [],
        location_id: "geonames:1880252",
        location_filter_value: "tx1_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        seniority_ids: [],
        seniority_id: null,
        seniority_filter_value: null,
        seniority_filter_values: [],
        work_modes: [],
        work_mode_filter_values: [],
        open_to: [],
        open_to_ids: [],
        open_to_filter_values: [],
        organization_ids: [],
        organization_filter_values: [],
        representation_status: "agent_assisted",
        contact_disclosure: "request",
        representative_ids: ["rep-public-1"],
        representative_id: "rep-public-1",
        representative_filter_value: "tx1_cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        representative_filter_values: ["tx1_cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"],
        updated_at: "2026-08-03T00:00:00Z",
        schema_version: 2,
        agent_identities: [{ handle: "ari-agent", capabilities: ["internal_contact_request"] }]
      }],
      offset: 0,
      limit: 20,
      total: 1,
      indexing_available: true,
      warning: null,
      facets: { industry_ids: { fintech: 1 } },
      taxonomy_facets: { occupation_ids: [{ taxonomy: "occupation", parameter: "occupation_ids", canonical_id: "isco:123", filter_value: "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", label: "Product manager", label_conflict: true, vocabulary_version: "2026", version_conflict: false, count: 1 }] },
      mode: "projection",
      next_cursor: null,
      search_revision: null,
      complete: false,
      facet_truncated: {}
    });

    expect(response.hits[0]).toMatchObject({
      htmlUrl: "/p/ari-chen",
      occupations: ["Product manager"],
      industries: ["Financial technology"],
      representationStatus: "agent_assisted",
      representativeIds: ["rep-public-1"],
      occupationFilterValues: ["tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
      locationFilterValue: "tx1_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      schemaVersion: 2,
      agentIdentities: [{ handle: "ari-agent", capabilities: ["internal_contact_request"] }]
    });
    expect(response.facets.industry_ids).toEqual([{ value: "fintech", label: "fintech", count: 1 }]);
    expect(response.taxonomyFacets?.occupation_ids[0]).toMatchObject({ filterValue: "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", canonicalId: "isco:123", labelConflict: true });
  });

  it("rejects every noncanonical public document HTML or Markdown URL at parse time", () => {
    const profileHit = {
      id: "doc-1", kind: "profile", identifier: "ari-chen", name: "Ari Chen", html_url: "/p/ari-chen", markdown_url: "/v1/profiles/ari-chen.md", agent_identities: [],
      occupation_ids: [], industry_ids: [], skill_ids: [], language_ids: [], seniority_ids: [], work_modes: [], open_to_ids: [], organization_ids: [], representative_ids: [], open_to: [],
      occupation_filter_values: [], industry_filter_values: [], skill_filter_values: [], language_filter_values: [], location_filter_value: null, seniority_filter_value: null, seniority_filter_values: [], open_to_filter_values: [], organization_filter_values: [], representative_filter_value: null, representative_filter_values: [], work_mode_filter_values: [],
      location_id: null, seniority_id: null, representative_id: null,
    };
    const resumeHit = { ...profileHit, id: "doc-2", kind: "resume", identifier: "ari-resume", html_url: "/r/ari-resume", markdown_url: "/v1/resumes/ari-resume.md" };
    const parseHit = (hit: Record<string, unknown>) => parseDirectorySearchResponse({ ...projectionSearchEnvelope, hits: [hit] }).hits[0];

    expect(parseHit(profileHit).htmlUrl).toBe("/p/ari-chen");
    expect(parseHit(profileHit).markdownUrl).toBe("/v1/profiles/ari-chen.md");
    expect(parseHit(resumeHit).htmlUrl).toBe("/r/ari-resume");
    expect(parseHit(resumeHit).markdownUrl).toBe("/v1/resumes/ari-resume.md");
    for (const htmlUrl of [undefined, "javascript:alert(1)", "data:text/html,pwn", "//evil.example/p/ari-chen", "https://evil.example/p/ari-chen", "/account", "/admin", "/private", "/r/ari-chen", "/p/other", "/p/ari-chen?next=/account", "/p/ari-chen#secret", "/p/ari%2Dchen"]) {
      expect(() => parseHit({ ...profileHit, html_url: htmlUrl })).toThrow("invalid search hit HTML URL");
    }
    for (const markdownUrl of [undefined, "", "javascript:alert(1)", "data:text/markdown,pwn", "//evil.example/v1/profiles/ari-chen.md", "https://evil.example/v1/profiles/ari-chen.md", "/v1/resumes/ari-chen.md", "/v1/profiles/other-person.md", "/v1/profiles/ari-chen", "/v1/profiles/ari-chen.md?next=/account", "/v1/profiles/ari-chen.md#secret", "/v1/profiles/ari%2Dchen.md"]) {
      expect(() => parseHit({ ...profileHit, markdown_url: markdownUrl })).toThrow("invalid search hit Markdown URL");
    }
    for (const identifier of ["Ari-Chen", "ari_chen", "-ari", "ari-", "a".repeat(65), "ari/chen", "ari%2Dchen"]) {
      expect(() => parseHit({ ...profileHit, identifier, html_url: `/p/${identifier}` })).toThrow("invalid search hit identifier");
    }
  });

  it("fails closed when authoritative taxonomy facets are missing or malformed", () => {
    const base = { ...projectionSearchEnvelope };
    const missingTaxonomyFacets = { ...base } as Record<string, unknown>;
    delete missingTaxonomyFacets.taxonomy_facets;
    expect(() => parseDirectorySearchResponse(missingTaxonomyFacets)).toThrow("omitted authoritative taxonomy facets");
    expect(() => parseDirectorySearchResponse({ ...base, taxonomy_facets: { occupation_ids: [{ taxonomy: "occupation", parameter: "occupation_ids", canonical_id: "isco:1", filter_value: "bad", label: "x", label_conflict: false, vocabulary_version: null, version_conflict: false, count: 1 }] } })).toThrow();
    expect(parseDirectorySearchResponse({ ...base, taxonomy_facets: {} }).taxonomyFacets).toEqual({});
    expect(() => parseDirectorySearchResponse({ ...base, hits: {} as never, taxonomy_facets: {} })).toThrow();
    for (const field of ["offset", "limit", "total", "indexing_available", "facets", "mode", "next_cursor", "search_revision", "complete", "facet_truncated"] as const) {
      const malformed = { ...base, taxonomy_facets: {} } as Record<string, unknown>;
      delete malformed[field];
      expect(() => parseDirectorySearchResponse(malformed)).toThrow();
    }
    expect(() => parseDirectorySearchResponse({ ...base, offset: -1, taxonomy_facets: {} })).toThrow();
    expect(() => parseDirectorySearchResponse({ ...base, limit: 0, taxonomy_facets: {} })).toThrow();
    expect(() => parseDirectorySearchResponse({ ...base, total: -1, taxonomy_facets: {} })).toThrow();
    expect(() => parseDirectorySearchResponse({ ...base, indexing_available: "yes", taxonomy_facets: {} })).toThrow();
    expect(() => parseDirectorySearchResponse({ ...base, facets: [], taxonomy_facets: {} })).toThrow();
    expect(() => parseDirectorySearchResponse({ ...base, mode: "projection", next_cursor: "not-allowed", taxonomy_facets: {} })).toThrow("projection search state");
    expect(() => parseDirectorySearchResponse({ ...base, mode: "exact", offset: 1, next_cursor: null, search_revision: 1, complete: true, taxonomy_facets: {} })).toThrow("exact search state");
  });

it("toggles only supported scalar facets and routes typed aliases through taxonomy evidence", () => {
    expect(toggleSearchFacet(emptySearchFilters, "kind", "profile")).toMatchObject({ kind: "profile", offset: 0 });
    expect(toggleSearchFacet(emptySearchFilters, "location_country_code", "sg")).toMatchObject({ locationCountryCode: "SG" });
    expect(toggleSearchFacet(emptySearchFilters, "availability_status", "available_now")).toMatchObject({ availabilityStatus: "available_now" });
    expect(toggleSearchFacet(emptySearchFilters, "representation_status", "self")).toMatchObject({ representationStatus: "self" });
    expect(toggleSearchFacet(emptySearchFilters, "contact_disclosure", "platform_only")).toMatchObject({ contactDisclosure: "platform_only" });
    expect(toggleSearchFacet(emptySearchFilters, "occupation_ids", "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")).toBeNull();
    expect(applyTaxonomyFacet(emptySearchFilters, { taxonomy: "occupation", parameter: "occupation_ids", canonicalId: "isco:123", filterValue: "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", label: "Product", labelConflict: false, vocabularyVersion: null, versionConflict: false, count: 1 }).occupationIds).toEqual(["tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]);
  });
});
