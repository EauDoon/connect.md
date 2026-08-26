import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SearchExperience } from "../components/search-experience";
import { emptySearchFilters, type DirectorySearchResponse } from "../lib/public-search-api";

const exactResponse: DirectorySearchResponse = {
  hits: [{
    id: "doc-1",
    kind: "profile",
    identifier: "ari-chen",
    name: "Ari Chen",
    headline: "Payments leader",
    title: "Founder",
    occupationIds: [],
    occupations: ["Product management"],
    industryIds: [],
    industries: ["Fintech"],
    skillIds: [],
    skills: ["Payments"],
    languageIds: [],
    languages: [],
    locationId: null,
    locationLabel: "Singapore",
    locationCountryCode: "SG",
    locationRegion: null,
    locationCity: "Singapore",
    seniorityIds: [],
    seniorityId: null,
    seniority: [],
    workModes: [],
    availabilityStatus: null,
    availabilityFrom: null,
    openTo: [],
    organizationIds: [],
    representativeIds: [],
    representativeId: null,
    occupationFilterValues: [],
    industryFilterValues: [],
    skillFilterValues: [],
    languageFilterValues: [],
    locationFilterValue: null,
    seniorityFilterValue: null,
    seniorityFilterValues: [],
    openToFilterValues: [],
    organizationFilterValues: [],
    representativeFilterValue: null,
    representativeFilterValues: [],
    workModeFilterValues: [],
    organizations: [],
    representationStatus: null,
    contactDisclosure: null,
    updatedAt: "2026-08-03T00:00:00Z",
    schemaVersion: 2,
    version: 3,
    excerpt: null,
    htmlUrl: "/p/ari-chen",
    markdownUrl: "/v1/profiles/ari-chen.md",
    agentIdentities: [{ handle: "ari-agent", capabilities: ["internal_contact_request"] }]
  }],
  offset: 0,
  limit: 20,
  total: 38,
  indexingAvailable: true,
  warning: null,
  facets: {},
  taxonomyFacets: {},
  mode: "exact",
  nextCursor: "server-next",
  searchRevision: 23,
  complete: true,
  facetTruncated: { kind: true }
};

describe("search experience mode parity", () => {
  it("renders exact-mode completeness, revision, server-only pagination, and discovery-only Agent Identity links", () => {
    const markup = renderToStaticMarkup(createElement(SearchExperience, {
      filters: { ...emptySearchFilters, mode: "exact", agentCapability: "internal_contact_request", cursor: "server-current" },
      response: exactResponse,
      error: null
    }));

    expect(markup).toContain("Exact canonical search");
    expect(markup).toContain("complete for this returned canonical snapshot");
    expect(markup).toContain("Search revision 23");
    expect(markup).toContain("Facet values were truncated by the server for: kind");
    expect(markup).toContain('href="/search?mode=exact&amp;agent_capability=internal_contact_request&amp;offset=0&amp;cursor=server-next"');
    expect(markup).toContain('href="/agents/ari-agent"');
    expect(markup).toContain("Discovery-only labels. They do not establish a mandate, ownership, availability, consent, or contact authority.");
    expect(markup).not.toContain('href="/inbox?profile=');
    expect(markup).not.toContain("Next</a>");
  });

  it("retains offset pagination and the bounded-completeness warning for projection mode", () => {
    const markup = renderToStaticMarkup(createElement(SearchExperience, {
      filters: emptySearchFilters,
      response: { ...exactResponse, hits: [], mode: "projection", nextCursor: null, searchRevision: null, complete: false, facetTruncated: {}, total: 1050 },
      error: null
    }));

    expect(markup).toContain("Projection search uses a bounded 1,050-candidate discovery window");
    expect(markup).toContain("1,050 indexed results");
    expect(markup).toContain('href="/search?offset=20"');
    expect(markup).not.toContain("Exact canonical search");
  });

  it("accepts three-character country codes and labels document actions by kind", () => {
    const profileMarkup = renderToStaticMarkup(createElement(SearchExperience, {
      filters: emptySearchFilters,
      response: exactResponse,
      error: null
    }));
    const resumeMarkup = renderToStaticMarkup(createElement(SearchExperience, {
      filters: emptySearchFilters,
      response: {
        ...exactResponse,
        hits: [{
          ...exactResponse.hits[0],
          kind: "resume",
          htmlUrl: "/r/ari-chen-resume",
          markdownUrl: "/v1/resumes/ari-chen-resume.md"
        }]
      },
      error: null
    }));

    expect(profileMarkup).toContain('name="location_country_code"');
    expect(profileMarkup).toContain('maxLength="3"');
    expect(profileMarkup).toContain("View profile");
    expect(resumeMarkup).toContain("View resume");
    expect(resumeMarkup).not.toContain("View profile");
  });

  it("keeps structured filters shrinkable within a narrow single-column grid", () => {
    const aliases = [`tx1_${"a".repeat(64)}`, `tx1_${"b".repeat(64)}`];
    const markup = renderToStaticMarkup(createElement(SearchExperience, {
      filters: { ...emptySearchFilters, occupationIds: aliases },
      response: { ...exactResponse, taxonomyFacets: {} },
      error: null
    }));

    expect(markup).toContain('class="mt-4 grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 border-t border-white/10 pt-4 sm:grid-cols-2 lg:grid-cols-4"');
    expect(markup).toContain('class="min-w-0 sm:col-span-2 lg:col-span-4"');
    expect(markup.match(/class="block min-w-0"/gu)).toHaveLength(11);
    expect(markup.match(/name="occupation_ids"/gu)).toHaveLength(2);
    for (const alias of aliases) expect(markup).toContain(`value="${alias}"`);
  });

  it("marks ordinary and taxonomy facet rows as standalone 44px touch targets", () => {
    const taxonomyFilterValue = `tx1_${"c".repeat(64)}`;
    const markup = renderToStaticMarkup(createElement(SearchExperience, {
      filters: emptySearchFilters,
      response: {
        ...exactResponse,
        facets: { kind: [{ value: "profile", label: "Profiles", count: 1 }] },
        taxonomyFacets: {
          occupation_ids: [{
            taxonomy: "occupation",
            parameter: "occupation_ids",
            canonicalId: "isco:2512",
            filterValue: taxonomyFilterValue,
            label: "Software developers",
            labelConflict: false,
            vocabularyVersion: "2026",
            versionConflict: false,
            count: 1,
          }],
        },
      },
      error: null,
    }));

    const facetTargets = [...markup.matchAll(/<a\b[^>]*data-touch-target="search-facet"[^>]*>/gu)]
      .map((match) => match[0]);
    expect(facetTargets).toHaveLength(2);
    for (const target of facetTargets) expect(target).toMatch(/class="[^"]*\bmin-h-11\b[^"]*"/u);
    expect(markup).toContain('href="/search?kind=profile"');
    expect(markup).toContain(`occupation_ids=${taxonomyFilterValue}`);
  });
});
