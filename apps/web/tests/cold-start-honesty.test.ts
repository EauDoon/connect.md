import { createElement } from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentDirectory } from "../components/agent-directory";
import { DiscoverHub } from "../components/discover-hub";
import { JobDirectory } from "../components/job-directory";
import { OrganizationDirectory } from "../components/organization-directory";
import { SearchExperience } from "../components/search-experience";
import { emptySearchFilters, type DirectorySearchResponse } from "../lib/public-search-api";
import { emptyJobSearchFilters, type Job } from "../lib/recruitment-api";

vi.mock("@/components/network-notice", () => ({ NetworkNotice: () => null }));
vi.mock("server-only", () => ({}));

const emptySearchResponse: DirectorySearchResponse = {
  hits: [],
  offset: 0,
  limit: 20,
  total: 0,
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

const emptyCursorPage = { items: [], nextCursor: null };
const emptyAgentPage = { identities: [], nextCursor: null };
const publicJob: Job = { id: "job-1", organizationId: "org-1", organizationSlug: "acme", organizationName: "Acme", slug: "role", title: "Role", description: "Role description", location: null, workMode: null, employmentType: null, status: "published", version: 1, publishedAt: "2026-08-23T00:00:00.000Z", createdAt: "2026-08-23T00:00:00.000Z", updatedAt: "2026-08-23T00:00:00.000Z", etag: "etag-1" };
const publicJobPage = { items: [publicJob], nextCursor: null };
const originalClerkPublishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const originalClerkSecretKey = process.env.CLERK_SECRET_KEY;

afterEach(() => {
  if (originalClerkPublishableKey === undefined) delete process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  else process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = originalClerkPublishableKey;
  if (originalClerkSecretKey === undefined) delete process.env.CLERK_SECRET_KEY;
  else process.env.CLERK_SECRET_KEY = originalClerkSecretKey;
});

describe("cold-start honesty", () => {
  it("routes every conclusive unfiltered empty directory to private agent-first or Human Mode drafting", () => {
    const markups = [
      renderToStaticMarkup(createElement(SearchExperience, { filters: emptySearchFilters, response: emptySearchResponse, error: null })),
      renderToStaticMarkup(createElement(OrganizationDirectory, { query: "", cursor: null, response: emptyCursorPage, error: null })),
      renderToStaticMarkup(createElement(JobDirectory, { filters: emptyJobSearchFilters, response: emptyCursorPage, error: null })),
      renderToStaticMarkup(createElement(AgentDirectory, { filters: { q: "", profileHandle: null, cursor: null, invalidMessage: null }, response: emptyAgentPage, error: null })),
    ];

    for (const markup of markups) {
      expect(markup).toContain("The public network is early.");
      expect(markup).toContain('href="/agent-readme.md"');
      expect(markup).toContain('href="/human"');
      expect(markup).toContain("Start a private draft");
      expect(markup).toContain("Publication remains a separate, explicit action.");
      expect(markup).not.toMatch(/No (matching|published jobs match|service-gated organizations match)/u);
    }
  });

  it("keeps private job destinations truthful across public auth states", () => {
    const render = () => renderToStaticMarkup(createElement(JobDirectory, { filters: emptyJobSearchFilters, response: publicJobPage, error: null }));

    for (const [publishableKey, secretKey, privateWorkspacesEnabled] of [
      [undefined, undefined, false],
      ["   ", undefined, false],
      ["publishable-configured", undefined, false],
      [undefined, "secret-configured", false],
      ["publishable-configured", "secret-configured", true],
    ] as const) {
      if (publishableKey === undefined) delete process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
      else process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = publishableKey;
      if (secretKey === undefined) delete process.env.CLERK_SECRET_KEY;
      else process.env.CLERK_SECRET_KEY = secretKey;

      const markup = render();
      expect(markup).toContain('href="/organizations"');
      if (privateWorkspacesEnabled) {
        expect(markup).toContain('href="/applications"');
        expect(markup).toContain('href="/employer"');
      } else {
        expect(markup).not.toContain('href="/applications"');
        expect(markup).not.toContain('href="/employer"');
      }
    }
  });

  it("retains search-miss language for query, filter, and pagination-specific empty pages", () => {
    const markups = [
      renderToStaticMarkup(createElement(SearchExperience, { filters: { ...emptySearchFilters, q: "payments" }, response: emptySearchResponse, error: null })),
      renderToStaticMarkup(createElement(SearchExperience, { filters: { ...emptySearchFilters, offset: 20 }, response: emptySearchResponse, error: null })),
      renderToStaticMarkup(createElement(OrganizationDirectory, { query: "payments", cursor: null, response: emptyCursorPage, error: null })),
      renderToStaticMarkup(createElement(OrganizationDirectory, { query: "", cursor: "signed-page", response: emptyCursorPage, error: null })),
      renderToStaticMarkup(createElement(JobDirectory, { filters: { ...emptyJobSearchFilters, location: "Singapore" }, response: emptyCursorPage, error: null })),
      renderToStaticMarkup(createElement(JobDirectory, { filters: { ...emptyJobSearchFilters, cursor: "signed-page" }, response: emptyCursorPage, error: null })),
      renderToStaticMarkup(createElement(AgentDirectory, { filters: { q: "payments", profileHandle: null, cursor: null, invalidMessage: null }, response: emptyAgentPage, error: null })),
      renderToStaticMarkup(createElement(AgentDirectory, { filters: { q: "", profileHandle: null, cursor: "signed-page", invalidMessage: null }, response: emptyAgentPage, error: null })),
    ];

    expect(markups[0]).toContain("No matching public documents");
    expect(markups[1]).toContain("No matching public documents");
    expect(markups[2]).toContain("No service-gated organizations match");
    expect(markups[3]).toContain("No service-gated organizations match");
    expect(markups[4]).toContain("No published jobs match");
    expect(markups[5]).toContain("No published jobs match");
    expect(markups[6]).toContain("No matching public identities");
    expect(markups[7]).toContain("No matching public identities");
    for (const markup of markups) expect(markup).not.toContain("The public network is early.");
  });

  it("keeps service failures distinct from an honest empty inventory", () => {
    const markups = [
      renderToStaticMarkup(createElement(SearchExperience, { filters: emptySearchFilters, response: null, error: "Retry later." })),
      renderToStaticMarkup(createElement(OrganizationDirectory, { query: "", cursor: null, response: null, error: "Retry later." })),
      renderToStaticMarkup(createElement(JobDirectory, { filters: emptyJobSearchFilters, response: null, error: "Retry later." })),
      renderToStaticMarkup(createElement(AgentDirectory, { filters: { q: "", profileHandle: null, cursor: null, invalidMessage: null }, response: null, error: "Retry later." })),
    ];

    expect(markups[0]).toContain("Directory temporarily unavailable");
    expect(markups[1]).toContain("Organization directory is temporarily unavailable");
    expect(markups[2]).toContain("Jobs are temporarily unavailable");
    expect(markups[3]).toContain("Directory results are unavailable");
    for (const markup of markups) expect(markup).not.toContain("The public network is early.");
  });

  it("treats a fulfilled but unavailable search index as degraded rather than genuinely empty", () => {
    const degradedSearchResponse = { ...emptySearchResponse, indexingAvailable: false };
    const searchMarkup = renderToStaticMarkup(createElement(SearchExperience, {
      filters: emptySearchFilters,
      response: degradedSearchResponse,
      error: null,
    }));
    const discoverMarkup = renderToStaticMarkup(createElement(DiscoverHub, {
      profiles: degradedSearchResponse,
      agents: emptyAgentPage,
      privateWorkspacesEnabled: false,
      recruitingEnabled: false,
      organizations: emptyCursorPage,
      jobs: emptyCursorPage,
      posts: emptyCursorPage,
      unavailableSources: [],
    }));

    expect(searchMarkup).toContain("Search index unavailable");
    expect(searchMarkup).toContain("cannot confirm whether public documents are available");
    expect(discoverMarkup).toContain("Published documents");
    expect(discoverMarkup).toContain("are temporarily unavailable");
    expect(discoverMarkup).not.toContain("No public documents are available yet");
    expect(searchMarkup).not.toContain("The public network is early.");
    expect(discoverMarkup).not.toContain("The public network is early.");
  });

  it("presents one consolidated onboarding path when the loaded Discover inventory is empty", () => {
    const markup = renderToStaticMarkup(createElement(DiscoverHub, {
      profiles: emptySearchResponse,
      agents: emptyAgentPage,
      privateWorkspacesEnabled: false,
      recruitingEnabled: true,
      organizations: emptyCursorPage,
      jobs: emptyCursorPage,
      posts: emptyCursorPage,
      unavailableSources: [],
    }));

    expect(markup.match(/The public network is early\./gu)).toHaveLength(1);
    expect(markup).toContain('href="/agent-readme.md"');
    expect(markup).toContain('href="/human"');
    expect(markup).toContain("No public documents are available yet");
    expect(markup).toContain("No public posts are available yet");
    expect(markup).toContain("No public Agent Identities are available yet");
    expect(markup).toContain("No service-gated organizations are available yet");
    expect(markup).toContain("No published jobs are available yet");
    expect(markup).not.toContain("No public documents returned");
    expect(markup).not.toContain("broader term or filter");
  });

  it("does not describe an empty discovery window as an exhausted public inventory", () => {
    const markup = renderToStaticMarkup(createElement(DiscoverHub, {
      profiles: { ...emptySearchResponse, total: 1, nextCursor: "documents-next" },
      agents: { identities: [], nextCursor: "agents-next" },
      privateWorkspacesEnabled: false,
      recruitingEnabled: true,
      organizations: { items: [], nextCursor: "organizations-next" },
      jobs: { items: [], nextCursor: "jobs-next" },
      posts: { items: [], nextCursor: "posts-next" },
      unavailableSources: [],
    }));

    for (const title of [
      "No public documents appear in this discovery window",
      "No public posts appear in this discovery window",
      "No public Agent Identities appear in this discovery window",
      "No service-gated organizations appear in this discovery window",
      "No published jobs appear in this discovery window",
    ]) expect(markup).toContain(title);

    expect(markup).not.toContain("The public network is early.");
    expect(markup).not.toContain("are available yet");
  });

  it("sets honest landing expectations and explains the .md format without weakening publication boundaries", () => {
    const source = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
    expect(source).toContain("Explore the early public network");
    expect(source).toContain("as the public inventory grows");
    expect(source).toContain("portable plain-text Markdown, readable by people and agents");
    expect(source).toContain("You remain the authority for facts, visibility, publication, and access.");
  });
});
