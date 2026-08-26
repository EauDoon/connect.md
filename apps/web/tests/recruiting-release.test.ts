import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const originalRecruitingRelease = process.env.CONNECTMD_RECRUITING_ENABLED;

const mocks = vi.hoisted(() => {
  const notFoundMessage = "recruiting-release-not-found";
  const emptyJobFilters = {
    q: "",
    organizationSlug: "",
    location: "",
    workMode: "",
    employmentType: "",
    cursor: null,
  };
  return {
    emptyJobFilters,
    notFoundMessage,
    notFound: vi.fn((): never => {
      throw new Error(notFoundMessage);
    }),
    searchDirectory: vi.fn().mockResolvedValue({
      hits: [],
      offset: 0,
      limit: 20,
      total: 0,
      indexingAvailable: true,
      warning: null,
      facets: {},
      taxonomyFacets: {},
    }),
    listPublicAgentDirectory: vi.fn().mockResolvedValue({ identities: [], nextCursor: null }),
    listPublicPostsOnServer: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
    listPublicOrganizations: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
    listPublicJobs: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
    fetchPublicOrganization: vi.fn().mockResolvedValue({
      id: "org-1",
      slug: "acme",
      name: "Acme",
      description: "Private preparation fixture",
      recruitingVerificationActive: true,
      recruitingVerificationPurpose: "recruiting_control",
    }),
    fetchPublicJob: vi.fn().mockResolvedValue({
      id: "job-1",
      organizationSlug: "acme",
      organizationName: "Acme",
      slug: "engineer",
      title: "Engineer",
      description: "Fixture role",
      status: "published",
    }),
  };
});

vi.mock("server-only", () => ({}));
vi.mock("next/navigation", () => ({ notFound: mocks.notFound }));
vi.mock("@/components/discover-hub", () => ({ DiscoverHub: () => null }));
vi.mock("@/components/organization-directory", () => ({
  OrganizationDirectory: () => null,
  OrganizationPublicPage: () => null,
}));
vi.mock("@/components/job-directory", () => ({ JobDirectory: () => null, JobPublicPage: () => null }));
vi.mock("@/components/job-application-panel", () => ({ JobApplicationPanel: () => null }));
vi.mock("@/lib/agent-identity-api", () => ({ listPublicAgentDirectory: mocks.listPublicAgentDirectory }));
vi.mock("@/lib/posts-api", () => ({ listPublicPostsOnServer: mocks.listPublicPostsOnServer }));
vi.mock("@/lib/public-search-api", () => ({ emptySearchFilters: {}, searchDirectory: mocks.searchDirectory }));
vi.mock("@/lib/server-search-params", () => ({ serverSearchParams: () => ({}) }));
vi.mock("@/lib/api", () => ({
  ApiRequestError: class ApiRequestError extends Error {
    code = "not_found";
  },
  presentApiError: (error: unknown) => String(error),
}));
vi.mock("@/lib/recruitment-api", () => ({
  emptyJobSearchFilters: mocks.emptyJobFilters,
  fetchPublicJob: mocks.fetchPublicJob,
  fetchPublicOrganization: mocks.fetchPublicOrganization,
  hasActiveRecruitingControl: () => true,
  jobSearchFiltersFromParams: () => mocks.emptyJobFilters,
  listPublicJobs: mocks.listPublicJobs,
  listPublicOrganizations: mocks.listPublicOrganizations,
}));

vi.stubGlobal("React", React);

afterEach(() => {
  vi.clearAllMocks();
  if (originalRecruitingRelease === undefined) delete process.env.CONNECTMD_RECRUITING_ENABLED;
  else process.env.CONNECTMD_RECRUITING_ENABLED = originalRecruitingRelease;
});

describe("server-side recruiting release gate", () => {
  it("is false by default and accepts only the exact explicit true value", async () => {
    const { recruitingReleaseEnabled } = await import("../lib/recruiting-release");

    delete process.env.CONNECTMD_RECRUITING_ENABLED;
    expect(recruitingReleaseEnabled()).toBe(false);
    process.env.CONNECTMD_RECRUITING_ENABLED = "false";
    expect(recruitingReleaseEnabled()).toBe(false);
    process.env.CONNECTMD_RECRUITING_ENABLED = "TRUE";
    expect(recruitingReleaseEnabled()).toBe(false);
    process.env.CONNECTMD_RECRUITING_ENABLED = "true";
    expect(recruitingReleaseEnabled()).toBe(true);
  });

  it("stops list/detail pages and metadata before every recruiting API read while disabled", async () => {
    delete process.env.CONNECTMD_RECRUITING_ENABLED;
    const organizations = await import("../app/organizations/page");
    const organization = await import("../app/organizations/[slug]/page");
    const jobs = await import("../app/jobs/page");
    const job = await import("../app/jobs/[organizationSlug]/[jobSlug]/page");
    const calls: Array<() => unknown | Promise<unknown>> = [
      () => organizations.generateMetadata(),
      () => organizations.default({ searchParams: Promise.resolve({}) }),
      () => organization.generateMetadata({ params: Promise.resolve({ slug: "acme" }) }),
      () => organization.default({ params: Promise.resolve({ slug: "acme" }) }),
      () => jobs.generateMetadata(),
      () => jobs.default({ searchParams: Promise.resolve({}) }),
      () => job.generateMetadata({ params: Promise.resolve({ organizationSlug: "acme", jobSlug: "engineer" }) }),
      () => job.default({ params: Promise.resolve({ organizationSlug: "acme", jobSlug: "engineer" }) }),
    ];

    for (const call of calls) {
      await expect(Promise.resolve().then(() => call())).rejects.toThrow(mocks.notFoundMessage);
    }
    expect(mocks.notFound).toHaveBeenCalledTimes(calls.length);
    expect(mocks.listPublicOrganizations).not.toHaveBeenCalled();
    expect(mocks.listPublicJobs).not.toHaveBeenCalled();
    expect(mocks.fetchPublicOrganization).not.toHaveBeenCalled();
    expect(mocks.fetchPublicJob).not.toHaveBeenCalled();
  });

  it("preserves the existing list/detail reads when the shared gate is explicitly true", async () => {
    process.env.CONNECTMD_RECRUITING_ENABLED = "true";
    const organizations = await import("../app/organizations/page");
    const organization = await import("../app/organizations/[slug]/page");
    const jobs = await import("../app/jobs/page");
    const job = await import("../app/jobs/[organizationSlug]/[jobSlug]/page");

    expect(organizations.generateMetadata()).toMatchObject({ alternates: { canonical: "/organizations" } });
    await organizations.default({ searchParams: Promise.resolve({}) });
    await organization.generateMetadata({ params: Promise.resolve({ slug: "acme" }) });
    await organization.default({ params: Promise.resolve({ slug: "acme" }) });
    expect(jobs.generateMetadata()).toMatchObject({ alternates: { canonical: "/jobs" } });
    await jobs.default({ searchParams: Promise.resolve({}) });
    await job.generateMetadata({ params: Promise.resolve({ organizationSlug: "acme", jobSlug: "engineer" }) });
    await job.default({ params: Promise.resolve({ organizationSlug: "acme", jobSlug: "engineer" }) });

    expect(mocks.notFound).not.toHaveBeenCalled();
    expect(mocks.listPublicOrganizations).toHaveBeenCalled();
    expect(mocks.listPublicJobs).toHaveBeenCalled();
    expect(mocks.fetchPublicOrganization).toHaveBeenCalled();
    expect(mocks.fetchPublicJob).toHaveBeenCalled();
  });

  it("does not dispatch recruiting discovery reads while disabled and restores them when enabled", async () => {
    const { default: DiscoverPage } = await import("../app/discover/page");

    delete process.env.CONNECTMD_RECRUITING_ENABLED;
    await DiscoverPage();
    expect(mocks.searchDirectory).toHaveBeenCalledTimes(1);
    expect(mocks.listPublicAgentDirectory).toHaveBeenCalledTimes(1);
    expect(mocks.listPublicPostsOnServer).toHaveBeenCalledTimes(1);
    expect(mocks.listPublicOrganizations).not.toHaveBeenCalled();
    expect(mocks.listPublicJobs).not.toHaveBeenCalled();

    vi.clearAllMocks();
    process.env.CONNECTMD_RECRUITING_ENABLED = "true";
    await DiscoverPage();
    expect(mocks.listPublicOrganizations).toHaveBeenCalledTimes(1);
    expect(mocks.listPublicJobs).toHaveBeenCalledTimes(1);
  });
});
