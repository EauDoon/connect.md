import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectorySearchResponse } from "../lib/public-search-api";
import type { CursorPage, Job, Organization } from "../lib/recruitment-api";

vi.mock("@/components/network-notice", () => ({ NetworkNotice: () => null }));

const discoverHubSource = readFileSync(new URL("../components/discover-hub.tsx", import.meta.url), "utf8");

afterEach(() => {
  vi.unstubAllEnvs();
});

const emptyProps = {
  profiles: null,
  agents: null,
  privateWorkspacesEnabled: false,
  recruitingEnabled: false,
  organizations: null,
  jobs: null,
  posts: null,
  unavailableSources: [],
} as const;

describe("public Discover hub", () => {
  it("orients public discovery toward canonical Markdown and private paths without a public graph", async () => {
    const { DiscoverHub } = await import("../components/discover-hub");
    const markup = renderToStaticMarkup(createElement(DiscoverHub, emptyProps));

    expect(markup).toContain('action="/search"');
    expect(markup).toContain("Discover public records");
    expect(markup).toContain("Inspect canonical Markdown");
    expect(markup).toContain("Choose a private path");
    expect(markup).toContain('href="/representatives"');
    expect(markup).toContain('href="/agent-directory"');
    expect(markup).not.toContain('href="/organizations"');
    expect(markup).not.toContain('href="/jobs"');
    expect(markup).not.toContain('href="/network"');
    expect(markup).not.toContain('href="/feed"');
    expect(markup).toContain("Private workspaces are unavailable in this deployment.");
    expect(markup).toContain("Private follows and connections are unavailable in this deployment.");
    expect(markup).toContain("professional-post archives");
    expect(markup).toContain("owner-attested");
    expect(markup).toContain("Public HTML mirror");
    expect(markup).toContain("server-rendered hub mirrors public connect.md records");
    expect(markup).toContain('href="/llms.txt"');
    expect(markup).toContain('href="/openapi.json"');
    expect(markup).toContain("Discovery never grants authority or sends a message.");
    expect(markup).toContain("a public graph");
    expect(markup).not.toContain("No activity feed");
  });

  it("shows private workspace links only when the deployment is configured", async () => {
    const { DiscoverHub } = await import("../components/discover-hub");
    const markup = renderToStaticMarkup(createElement(DiscoverHub, {
      ...emptyProps,
      privateWorkspacesEnabled: true,
    }));

    expect(markup).toContain('href="/network"');
    expect(markup).toContain('href="/feed"');
    expect(markup).toContain("Open private feed");
    expect(markup).toContain("Open private network");
    expect(markup).not.toContain("Private workspaces are unavailable in this deployment.");
  });

  it("keeps public discovery links at the 44 pixel touch-target minimum", () => {
    expect(discoverHubSource).toContain("inline-flex min-h-11 items-center font-semibold text-acid");
    expect(discoverHubSource).toContain("mt-5 inline-flex min-h-11 items-center gap-2");
    expect(discoverHubSource.match(/inline-flex min-h-11 items-center underline-offset-4/gu)).toHaveLength(2);
    expect(discoverHubSource).toContain("inline-flex min-h-11 items-center font-semibold text-acid underline-offset-4");
  });

  it("derives private workspace availability from the server-only environment gate", () => {
    const page = readFileSync(new URL("../app/discover/page.tsx", import.meta.url), "utf8");

    expect(page).toContain('import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";');
    expect(page).toContain("const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();");
    expect(page).toContain("privateWorkspacesEnabled={privateWorkspacesEnabled}");
  });

  it("points protocol cards at the split API origin without changing app navigation", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");
    const { DiscoverHub } = await import("../components/discover-hub");
    const markup = renderToStaticMarkup(createElement(DiscoverHub, emptyProps));

    expect(markup).toContain('href="https://api.connect.test/llms.txt"');
    expect(markup).toContain('href="https://api.connect.test/llms-full.txt"');
    expect(markup).toContain('href="https://api.connect.test/openapi.json"');
    expect(markup).toContain('href="https://api.connect.test/.well-known/agent-card.json"');
    expect(markup).toContain('href="/search"');
  });

  it("keeps successful public records visible and identifies each temporarily unavailable source", async () => {
    const { DiscoverHub } = await import("../components/discover-hub");
    const agents = {
      identities: [{ handle: "ari-agent", displayName: "Ari Agent", description: "Mediated professional contact.", profileHandle: "ari-chen", capabilities: ["internal_contact_request"] as ["internal_contact_request"] }],
      nextCursor: null,
    };
    const markup = renderToStaticMarkup(createElement(DiscoverHub, { ...emptyProps, agents, recruitingEnabled: true, unavailableSources: ["documents", "jobs", "posts"] }));

    expect(markup).toContain("Unavailable now: Published documents, Published roles, Public posts");
    expect(markup).toContain("Published documents</strong> are temporarily unavailable");
    expect(markup).toContain("Published roles</strong> are temporarily unavailable");
    expect(markup).toContain("Public posts</strong> are temporarily unavailable");
    expect(markup).toContain('href="/agents/ari-agent"');
    expect(markup).toContain("Ari Agent");
    expect(markup).toContain("Owner-attested identity with internal mediated contact only");
    expect(markup).not.toContain("No public Agent Identities returned");
  });

  it("renders only metadata from the latest-post inventory without a body, excerpt, or report control", async () => {
    const { DiscoverHub } = await import("../components/discover-hub");
    const posts = {
      items: [{ id: "post-1", authorProfileHandle: "ari-chen", title: "A public professional note", topics: ["payments"], version: 1 as const, publishedAt: "2026-08-03T00:00:00Z", updatedAt: "2026-08-03T00:00:00Z", htmlUrl: "/posts/post-1", markdownUrl: "/v1/posts/post-1.md", etag: "\"sha256:post\"", markdown: "BODY MUST NOT RENDER", ownerId: "OWNER MUST NOT RENDER" }],
      nextCursor: null,
    };
    const markup = renderToStaticMarkup(createElement(DiscoverHub, { ...emptyProps, posts }));

    expect(markup).toContain("Latest professional posts");
    expect(markup).toContain("without ranking");
    expect(markup).toContain('href="/posts/post-1"');
    expect(markup).toContain('href="/v1/posts/post-1.md"');
    expect(markup).toContain("A public professional note");
    expect(markup).not.toContain("BODY MUST NOT RENDER");
    expect(markup).not.toContain("OWNER MUST NOT RENDER");
    expect(markup).not.toContain("Report this post");
  });

  it("uses returned canonical record URLs and only renders allowlisted Markdown URLs", async () => {
    const { DiscoverHub } = await import("../components/discover-hub");
    const profiles = {
      hits: [
        { id: "profile-1", kind: "profile", identifier: "stale-handle", name: "Ari Chen", headline: "Product builder", title: null, htmlUrl: "/p/ari-chen", markdownUrl: "/v1/profiles/ari-chen.md" },
        { id: "resume-1", kind: "resume", identifier: "stale-resume", name: "Ari Chen resume", headline: "Career record", title: null, htmlUrl: "/r/ari-chen", markdownUrl: "https://example.invalid/ari.md" },
      ],
      offset: 0,
      limit: 20,
      total: 2,
      indexingAvailable: true,
      warning: null,
      facets: {},
      taxonomyFacets: {},
    } as DirectorySearchResponse;
    const markup = renderToStaticMarkup(createElement(DiscoverHub, { ...emptyProps, profiles }));

    expect(markup).toContain('href="/p/ari-chen"');
    expect(markup).toContain('href="/r/ari-chen"');
    expect(markup).not.toContain("stale-handle");
    expect(markup).not.toContain("stale-resume");
    expect(markup).toContain('href="/v1/profiles/ari-chen.md"');
    expect(markup).toContain("Canonical Markdown");
    expect(markup).not.toContain("https://example.invalid/ari.md");
  });

  it("keeps gated organization and published job paths public only when explicitly enabled", async () => {
    const { DiscoverHub } = await import("../components/discover-hub");
    const agents = {
      identities: [{ handle: "ari-agent", displayName: "Ari Agent", description: "Mediated professional contact.", profileHandle: "ari-chen", capabilities: ["internal_contact_request"] as ["internal_contact_request"] }],
      nextCursor: null,
    };
    const organizations = {
      items: [
        { id: "org-active", slug: "bright-co", name: "Bright Co", description: null, recruitingVerificationActive: true, recruitingVerificationPurpose: "recruiting_control" },
        { id: "org-inactive", slug: "hidden-co", name: "Hidden Co", description: null, recruitingVerificationActive: false, recruitingVerificationPurpose: null },
      ],
      nextCursor: null,
    } as CursorPage<Organization>;
    const jobs = {
      items: [
        { id: "job-published", organizationSlug: "bright-co", organizationName: "Bright Co", slug: "product-lead", title: "Product Lead", location: null, workMode: "remote", employmentType: "full_time", status: "published" },
        { id: "job-draft", organizationSlug: "bright-co", organizationName: "Bright Co", slug: "hidden-role", title: "Hidden role", location: null, workMode: null, employmentType: null, status: "draft" },
      ],
      nextCursor: null,
    } as CursorPage<Job>;
    const markup = renderToStaticMarkup(createElement(DiscoverHub, { ...emptyProps, agents, recruitingEnabled: true, organizations, jobs }));

    expect(markup).toContain('href="/agents/ari-agent"');
    expect(markup).toContain('href="/organizations/bright-co"');
    expect(markup).toContain('href="/jobs/bright-co/product-lead"');
    expect(markup).not.toContain("Hidden Co");
    expect(markup).not.toContain("Hidden role");
    expect(markup).toContain("Current active recruiting verification enables public browsing; it is not an endorsement.");
    expect(markup).not.toContain("launch-gated");
  });

  it("omits recruiting cards, links, records, and availability errors while disabled", async () => {
    const { DiscoverHub } = await import("../components/discover-hub");
    const organizations = {
      items: [{ id: "org-active", slug: "bright-co", name: "Bright Co", description: null, recruitingVerificationActive: true, recruitingVerificationPurpose: "recruiting_control" }],
      nextCursor: null,
    } as CursorPage<Organization>;
    const jobs = {
      items: [{ id: "job-published", organizationSlug: "bright-co", organizationName: "Bright Co", slug: "product-lead", title: "Product Lead", location: null, workMode: "remote", employmentType: "full_time", status: "published" }],
      nextCursor: null,
    } as CursorPage<Job>;
    const markup = renderToStaticMarkup(createElement(DiscoverHub, {
      ...emptyProps,
      organizations,
      jobs,
      unavailableSources: ["organizations", "jobs"],
    }));

    expect(markup).not.toContain("Bright Co");
    expect(markup).not.toContain("Product Lead");
    expect(markup).not.toContain('href="/organizations');
    expect(markup).not.toContain('href="/jobs');
    expect(markup).not.toContain("Public organizations");
    expect(markup).not.toContain("Published roles");
  });

  it("contains long unbroken public document fields at narrow widths", async () => {
    const { DiscoverHub } = await import("../components/discover-hub");
    const unbroken = "a".repeat(240);
    const profiles = {
      hits: [{ id: "profile-1", kind: "profile", identifier: "profile-1", name: unbroken, headline: unbroken, title: null, htmlUrl: "/p/profile-1", markdownUrl: "/v1/profiles/profile-1.md" }],
      offset: 0,
      limit: 20,
      total: 1,
      indexingAvailable: true,
      warning: null,
      facets: {},
      taxonomyFacets: {},
    } as DirectorySearchResponse;
    const markup = renderToStaticMarkup(createElement(DiscoverHub, { ...emptyProps, profiles }));

    expect(markup).toContain(unbroken);
    expect(markup.match(/break-anywhere/g)).toHaveLength(2);
  });
});
