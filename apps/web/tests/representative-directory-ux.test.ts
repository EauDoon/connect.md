import { createElement, Fragment } from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/auth-provider", () => ({
  useConnectmdAuth: () => ({
    configured: false,
    isLoaded: true,
    isSignedIn: false,
    subject: null,
    getToken: async () => null,
  }),
}));

import { RepresentativeDirectory } from "../components/representative-directory";
import { emptySearchFilters, type DirectorySearchResponse } from "../lib/public-search-api";

afterEach(() => vi.unstubAllGlobals());

function source(relativePath: string) {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

describe("representative directory touch targets", () => {
  it("keeps profile links at the minimum touch size", () => {
    const component = source("../components/representative-directory.tsx");
    expect(component).toContain('className="inline-flex min-h-11 min-w-11 max-w-full items-center break-anywhere rounded-md');
    expect(component).toContain('className="inline-flex min-h-11 items-center justify-center rounded-full bg-acid');
  });

  it("advertises private agent controls only when the full workspace configuration is available", () => {
    const component = source("../components/representative-directory.tsx");
    const page = source("../app/representatives/page.tsx");
    vi.stubGlobal("React", { createElement, Fragment });
    const unavailable = renderToStaticMarkup(createElement(RepresentativeDirectory, { filters: emptySearchFilters, response: null, error: null, privateWorkspacesEnabled: false }));
    const available = renderToStaticMarkup(createElement(RepresentativeDirectory, { filters: emptySearchFilters, response: null, error: null, privateWorkspacesEnabled: true }));

    expect(page).toContain('import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";');
    expect(page).toContain("const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();");
    expect(page.split("privateWorkspacesEnabled={privateWorkspacesEnabled}")).toHaveLength(3);
    expect(unavailable).not.toContain('href="/agents"');
    expect(unavailable).toContain("Private agent permissions and reviewable changes appear only in deployments with authenticated workspaces.");
    expect(available).toMatch(/<a(?=[^>]*href="\/agents")(?=[^>]*min-h-11)[^>]*>Agents<\/a>/u);
    expect(component).toContain('<Link href={profileHref}');
    expect(component).toContain("View public profile</Link>");
  });

  it("does not claim an empty result when the search index is unavailable", () => {
    const response: DirectorySearchResponse = {
      hits: [],
      offset: 0,
      limit: 20,
      total: 0,
      indexingAvailable: false,
      warning: "Search projection is temporarily unavailable.",
      facets: {},
      taxonomyFacets: {},
      mode: "projection",
      nextCursor: null,
      searchRevision: null,
      complete: false,
      facetTruncated: {},
    };
    vi.stubGlobal("React", { createElement, Fragment });

    const markup = renderToStaticMarkup(createElement(RepresentativeDirectory, {
      filters: emptySearchFilters,
      response,
      error: null,
      privateWorkspacesEnabled: false,
    }));

    expect(markup).toContain("Search index unavailable");
    expect(markup).toContain("Availability cannot be confirmed");
    expect(markup).toContain("cannot confirm whether matching public declarations are available");
    expect(markup).toContain(response.warning);
    expect(markup).not.toContain("No matching public declarations");
    expect(markup).not.toContain("Try a broader term");
    expect(markup).not.toContain("0 profiles");
    expect(markup).not.toContain("Showing 0");
  });

  it("renders long profile names with encoded destinations and minimum touch targets", () => {
    const longName = `${"A".repeat(180)} Profile`;
    const response: DirectorySearchResponse = {
      hits: [{
        id: "representative-1",
        kind: "profile",
        identifier: "ada/lovelace",
        name: longName,
        headline: "Systems engineer",
        title: "Principal engineer",
        occupationIds: [],
        occupations: [],
        industryIds: [],
        industries: [],
        skillIds: [],
        skills: [],
        languageIds: [],
        languages: [],
        locationId: null,
        locationLabel: "Singapore",
        locationCountryCode: "SG",
        locationRegion: null,
        locationCity: null,
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
        representationStatus: "authorized_representative",
        contactDisclosure: "platform_only",
        updatedAt: null,
        schemaVersion: 2,
        version: 1,
        excerpt: null,
        htmlUrl: "/p/ada%2Flovelace",
        markdownUrl: "/v1/profiles/ada%2Flovelace.md",
        agentIdentities: [],
      }],
      offset: 0,
      limit: 20,
      total: 1,
      indexingAvailable: true,
      warning: null,
      facets: {},
      taxonomyFacets: {},
      mode: "projection",
      nextCursor: null,
      searchRevision: null,
      complete: false,
      facetTruncated: {},
    };
    vi.stubGlobal("React", { createElement, Fragment });
    const markup = renderToStaticMarkup(createElement(RepresentativeDirectory, { filters: emptySearchFilters, response, error: null, privateWorkspacesEnabled: false }));
    const encodedProfileHref = "/p/ada%2Flovelace";
    const nameLink = markup.match(new RegExp(`<a(?=[^>]*href="${encodedProfileHref}")(?=[^>]*break-anywhere)[^>]*>[^<]*</a>`, "u"))?.[0] ?? "";
    const profileLinks = markup.match(new RegExp(`href="${encodedProfileHref}"`, "gu")) ?? [];

    expect(markup).toContain(longName);
    expect(markup).not.toContain('href="/agents"');
    expect(profileLinks).toHaveLength(2);
    expect(nameLink).toContain("min-h-11");
    expect(nameLink).toContain("min-w-11");
    expect(nameLink).toContain("max-w-full");
    expect(nameLink).toContain("break-anywhere");
    expect(markup).toMatch(new RegExp(`<a(?=[^>]*href="${encodedProfileHref}")(?=[^>]*min-h-11)[^>]*>View public profile`, "u"));
    expect(markup).toContain("Representation is owner-attested on the public profile.");
    expect(markup).toContain("does not independently verify identity or authority.");

    const shortMarkup = renderToStaticMarkup(createElement(RepresentativeDirectory, {
      filters: emptySearchFilters,
      response: { ...response, hits: [{ ...response.hits[0], name: "A" }] },
      error: null,
      privateWorkspacesEnabled: false,
    }));
    const shortNameLink = shortMarkup.match(new RegExp(`<a(?=[^>]*href="${encodedProfileHref}")(?=[^>]*>A</a>)[^>]*>A</a>`, "u"))?.[0] ?? "";
    expect(shortNameLink).toContain("min-w-11");
  });
});
