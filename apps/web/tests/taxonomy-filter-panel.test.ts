import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TaxonomyFilterPanel } from "../components/taxonomy-filter-panel";
import { SearchExperience } from "../components/search-experience";
import { emptySearchFilters, type DirectorySearchResponse } from "../lib/public-search-api";
import type { TaxonomyFacets } from "../lib/taxonomy-api";

const alias = "tx1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const facets: TaxonomyFacets = {
  occupation_ids: [{ taxonomy: "occupation", parameter: "occupation_ids", canonicalId: "isco:123", filterValue: alias, label: "Product manager", labelConflict: true, vocabularyVersion: "2026", versionConflict: false, count: 3 }]
};

describe("taxonomy filter panel", () => {
  it("renders native labelled controls, compact aliases, and visible evidence without radio roles", () => {
    const markup = renderToStaticMarkup(createElement(TaxonomyFilterPanel, { filters: { ...emptySearchFilters, occupationIds: [alias] }, taxonomyFacets: facets }));
    expect(markup).toContain("<fieldset");
    expect(markup).toContain('type="search"');
    expect(markup).toContain('type="button"');
    expect(markup).toContain('name="occupation_ids"');
    expect(markup).toContain(`value="${alias}"`);
    expect(markup).toContain("isco:123");
    expect(markup).toContain("Label/version conflict");
    expect(markup).toContain("Owner-attested public-profile reference only");
    expect(markup).toContain('class="mt-4 flex flex-wrap gap-2" aria-label="Selected typed filters"');
    expect(markup).toContain('class="min-w-0 max-w-full rounded-2xl border border-acid/20 bg-acid/[.035] p-4"');
    expect(markup).toContain('class="mt-4 grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2"');
    expect(markup).toContain('<h2 id="taxonomy-filter-title"');
    expect(markup).not.toContain('<h3 id="taxonomy-filter-title"');
    expect(markup.match(/<fieldset class="min-w-0 rounded-xl/gu)).toHaveLength(10);
    expect(markup).toContain('class="inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border border-acid/25 bg-acid/[.06] pl-3 text-xs text-white"');
    expect(markup).toContain('class="min-w-0 truncate"');
    const removeButton = markup.match(/<button type="button" aria-label="Remove Product manager"[^>]*>/u)?.[0] ?? "";
    expect(removeButton).not.toBe("");
    for (const token of ["inline-flex", "min-h-11", "min-w-11", "shrink-0", "items-center", "justify-center"]) expect(removeButton).toContain(token);
    expect(removeButton).not.toContain("-m-3");
    expect(removeButton).not.toContain("p-0.5");
    expect(markup).not.toContain('role="radio"');
    expect(markup).not.toContain('role="radiogroup"');
    expect(markup).not.toContain('name="filter_value"');
  });

  it("does not re-submit invalid legacy typed values and keeps the failure visible", () => {
    const markup = renderToStaticMarkup(createElement(TaxonomyFilterPanel, { filters: { ...emptySearchFilters, invalidTypedValues: ["occupation_ids:isco:123", "location:Singapore"] }, taxonomyFacets: {} }));
    expect(markup).toContain("Legacy or raw typed URL values were not re-submitted");
    expect(markup).not.toContain('value="isco:123"');
    expect(markup).not.toContain('value="Singapore"');
  });

  it("renders a truthful unavailable catalog boundary instead of vocabulary guesses", () => {
    const markup = renderToStaticMarkup(createElement(TaxonomyFilterPanel, { filters: emptySearchFilters, taxonomyFacets: {} }));
    expect(markup).toContain("Loading authoritative vocabulary");
    expect(markup).toContain("Loading this registry catalog");
    expect(markup).not.toContain("product-manager");
  });

  it("mounts the taxonomy panel in the existing form and excludes raw typed legacy facets", () => {
    const response: DirectorySearchResponse = { hits: [], offset: 0, limit: 20, total: 0, indexingAvailable: true, warning: null, facets: { kind: [{ value: "profile", label: "profile", count: 1 }], occupation_ids: [{ value: "isco:999", label: "isco:999", count: 1 }] }, taxonomyFacets: facets, mode: "projection", nextCursor: null, searchRevision: null, complete: false, facetTruncated: {} };
    const markup = renderToStaticMarkup(createElement(SearchExperience, { filters: emptySearchFilters, response, error: null }));
    expect(markup.indexOf("<form")).toBeLessThan(markup.indexOf("Browse compact typed filters"));
    expect(markup).toContain("isco:123");
    expect(markup).toContain(alias);
    expect(markup).not.toContain('name="occupation_ids"');
    expect(markup).not.toContain('name="location_label"');
    expect(markup).not.toContain("isco:999");
  });
});
