import { afterEach, describe, expect, it, vi } from "vitest";

import sitemap, { generateSitemaps } from "../app/sitemap";
import robots from "../app/robots";

const originalApiBase = process.env.CONNECTMD_API_BASE_URL;
const originalRecruitingRelease = process.env.CONNECTMD_RECRUITING_ENABLED;

vi.mock("server-only", () => ({}));

afterEach(() => {
  vi.unstubAllGlobals();
  if (originalApiBase === undefined) delete process.env.CONNECTMD_API_BASE_URL;
  else process.env.CONNECTMD_API_BASE_URL = originalApiBase;
  if (originalRecruitingRelease === undefined) delete process.env.CONNECTMD_RECRUITING_ENABLED;
  else process.env.CONNECTMD_RECRUITING_ENABLED = originalRecruitingRelease;
});

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function publicPost(index: number) {
  const id = `post-${index}`;
  return { id, author_profile_handle: "ari-chen", title: `Post ${index}`, topics: ["payments"], version: 1, published_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z", html_url: `/posts/${id}`, markdown_url: `/v1/posts/${id}.md`, etag: `\"sha256:${index}\"` };
}

describe("public sitemap", () => {
  it("generates stable category IDs and advertises their production URLs", async () => {
    expect(generateSitemaps()).toEqual([{ id: 0 }, { id: 1 }, { id: 2 }, { id: 3 }]);
    expect(robots().sitemap).toEqual([
      "https://connect.md/sitemap/0.xml",
      "https://connect.md/sitemap/1.xml",
      "https://connect.md/sitemap/2.xml",
      "https://connect.md/sitemap/3.xml"
    ]);
    await expect(sitemap({ id: 99 })).resolves.toEqual([]);
  });

  it("accepts Next's exact runtime string category IDs and rejects other strings", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse({ identities: [], next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(sitemap({ id: "0" })).resolves.toEqual(expect.arrayContaining([
      expect.objectContaining({ url: "https://connect.md/" }),
    ]));
    await expect(sitemap({ id: "1" })).resolves.toEqual([]);
    await expect(sitemap({ id: "2" })).resolves.toEqual([]);
    await expect(sitemap({ id: "3" })).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(3);

    await expect(sitemap({ id: "00" })).resolves.toEqual([]);
    await expect(sitemap({ id: "4" })).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("keeps private management routes out of crawlers", () => {
    const rules = robots().rules;
    const allow = Array.isArray(rules) ? rules.flatMap((rule) => rule.allow ?? []) : rules.allow ?? [];
    const disallow = Array.isArray(rules) ? rules.flatMap((rule) => rule.disallow ?? []) : rules.disallow ?? [];
    expect(allow).toEqual(expect.arrayContaining(["/discover", "/agent-directory", "/agents/", "/posts/"]));
    expect(allow).not.toEqual(expect.arrayContaining(["/organizations", "/jobs"]));
    expect(disallow).toEqual(expect.arrayContaining(["/agents", "/feed", "/moderation", "/network", "/messages/", "/organizations", "/jobs"]));
  });

  it("collects every cursor-paginated public document page in category 0", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [{ kind: "profile", slug: "ari-chen", updated_at: "2026-08-03T00:00:00Z" }], next_cursor: "opaque-next" }))
      .mockResolvedValueOnce(jsonResponse({ items: [{ kind: "resume", slug: "ari-chen-resume", updated_at: "2026-08-02T00:00:00Z" }], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    const entries = await sitemap({ id: 0 });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.connect.test/v1/public-documents?limit=200",
      "https://api.connect.test/v1/public-documents?limit=200&cursor=opaque-next"
    ]);
    expect(entries).toEqual(expect.arrayContaining([
      expect.objectContaining({ url: "https://connect.md/trust" }),
      expect.objectContaining({ url: "https://connect.md/p/ari-chen", lastModified: new Date("2026-08-03T00:00:00Z") }),
      expect.objectContaining({ url: "https://connect.md/r/ari-chen-resume", lastModified: new Date("2026-08-02T00:00:00Z") })
    ]));
    expect(entries.length).toBeLessThanOrEqual(50_000);
  });

  it("caps category 0 at 50,000 URLs including base entries", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    const items = Array.from({ length: 50_001 }, (_, index) => ({ kind: "profile", slug: `profile-${index}`, updated_at: "2026-08-03T00:00:00Z" }));
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items, next_cursor: null })));

    const entries = await sitemap({ id: 0 });
    const urls = entries.map((entry) => entry.url);

    expect(entries).toHaveLength(50_000);
    expect(entries.length).toBeLessThanOrEqual(50_000);
    expect(urls).toContain("https://connect.md/");
    expect(urls).toContain("https://connect.md/p/profile-0");
    expect(urls).not.toContain("https://connect.md/p/profile-49994");
  });

  it("fails closed to category 0 base entries for malformed inventory data", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items: [{ kind: "profile", slug: "", updated_at: "not-a-date" }], next_cursor: null })));

    const entries = await sitemap({ id: 0 });
    const urls = entries.map((entry) => entry.url);
    expect(urls).toEqual(expect.arrayContaining([
      "https://connect.md/",
      "https://connect.md/search",
      "https://connect.md/representatives",
      "https://connect.md/agent-directory",
      "https://connect.md/discover",
      "https://connect.md/trust"
    ]));
    expect(urls).not.toContain("https://connect.md/organizations");
    expect(urls).not.toContain("https://connect.md/jobs");
    expect(urls.some((url) => url.includes("/p/") || url.includes("/r/") || url.includes("/agents/"))).toBe(false);
    expect(urls).not.toContain("https://connect.md/employer");
  });

  it("includes only service-gated organizations and published jobs in category 1", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    process.env.CONNECTMD_RECRUITING_ENABLED = "true";
    const organization = (slug: string, active: boolean) => ({ id: slug, slug, name: slug, description: null, website_url: null, visibility: "public", recruiting_verification_active: active, recruiting_verification_purpose: active ? "recruiting_control" : null, recruiting_verification_expires_at: active ? "2026-09-03T00:00:00Z" : null, version: 1, created_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z", etag: "\"v1\"" });
    const job = (slug: string, status: "published" | "draft") => ({ id: slug, organization_id: "org_1", organization_slug: "acme", organization_name: "Acme", slug, title: slug, description: "Role", location: null, work_mode: null, employment_type: null, status, version: 1, published_at: status === "published" ? "2026-08-03T00:00:00Z" : null, created_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z", etag: "\"v1\"" });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ organizations: [organization("acme", true), organization("inactive", false)], next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse({ jobs: [job("published-role", "published"), job("draft-role", "draft")], next_cursor: null })));

    const entries = await sitemap({ id: 1 });
    const urls = entries.map((entry) => entry.url);
    expect(entries.length).toBeLessThanOrEqual(50_000);
    expect(urls).toEqual(expect.arrayContaining(["https://connect.md/organizations/acme", "https://connect.md/jobs/acme/published-role"]));
    expect(urls).not.toContain("https://connect.md/organizations/inactive");
    expect(urls).not.toContain("https://connect.md/jobs/acme/draft-role");
    expect(urls).not.toContain("https://connect.md/");
    expect(urls).not.toContain("https://connect.md/agents");
    expect(urls).not.toContain("https://connect.md/applications");
  });

  it("returns an empty category 1 sitemap when a later recruitment read fails", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    process.env.CONNECTMD_RECRUITING_ENABLED = "true";
    const activeOrganization = { id: "acme", slug: "acme", name: "Acme", description: null, website_url: null, visibility: "public", recruiting_verification_active: true, recruiting_verification_purpose: "recruiting_control", recruiting_verification_expires_at: "2026-09-03T00:00:00Z", version: 1, created_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z", etag: "\"v1\"" };
    vi.stubGlobal("fetch", vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ organizations: [activeOrganization], next_cursor: null }))
      .mockResolvedValueOnce(new Response("jobs unavailable", { status: 503 })));

    await expect(sitemap({ id: 1 })).resolves.toEqual([]);
  });

  it("keeps category 1 stable but performs zero recruitment fetches by default", async () => {
    delete process.env.CONNECTMD_RECRUITING_ENABLED;
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(sitemap({ id: 1 })).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("publishes only the full successful public Agent Identity directory in category 2", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    vi.stubGlobal("fetch", vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ identities: [{ handle: "ari-agent", display_name: "Ari Agent", description: "Mediated contact.", profile_handle: "ari-chen", capabilities: ["internal_contact_request"] }, { handle: "ari-agent", display_name: "Duplicate", description: "Duplicate.", profile_handle: "ari-chen", capabilities: ["internal_contact_request"] }], next_cursor: "agent-next" }))
      .mockResolvedValueOnce(jsonResponse({ identities: [{ handle: "another-agent", display_name: "Another Agent", description: "Mediated contact.", profile_handle: "ari-chen", capabilities: ["internal_contact_request"] }], next_cursor: null })));

    const entries = await sitemap({ id: 2 });
    const urls = entries.map((entry) => entry.url);
    expect(entries.length).toBeLessThanOrEqual(50_000);
    expect(urls).toEqual(["https://connect.md/agents/ari-agent", "https://connect.md/agents/another-agent"]);
    expect(urls).not.toContain("https://connect.md/agents");
    expect(urls).not.toContain("https://connect.md/agents/withdrawn-agent");
    expect(urls).not.toContain("https://connect.md/agents/private-agent");
    expect(urls).not.toContain("https://connect.md/agents/unavailable-agent");
  });

  it("returns an empty category 2 sitemap when a directory continuation is unavailable", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    vi.stubGlobal("fetch", vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ identities: [{ handle: "ari-agent", display_name: "Ari Agent", description: "Mediated contact.", profile_handle: "ari-chen", capabilities: ["internal_contact_request"] }], next_cursor: "agent-next" }))
      .mockResolvedValueOnce(new Response("directory unavailable", { status: 503 })));

    await expect(sitemap({ id: 2 })).resolves.toEqual([]);
  });

  it("publishes chronological public post HTML URLs from every successful category 3 page", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    const firstPage = Array.from({ length: 200 }, (_, index) => publicPost(index));
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: firstPage, next_cursor: "post-next" }))
      .mockResolvedValueOnce(jsonResponse({ items: [publicPost(200)], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    const entries = await sitemap({ id: 3 });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.connect.test/v1/posts?limit=200",
      "https://api.connect.test/v1/posts?limit=200&cursor=post-next",
    ]);
    expect(entries).toHaveLength(201);
    expect(entries[0]).toMatchObject({ url: "https://connect.md/posts/post-0", lastModified: new Date("2026-08-03T00:00:00Z") });
    expect(entries[200]).toMatchObject({ url: "https://connect.md/posts/post-200" });
    expect(entries.every((entry) => !entry.url.endsWith(".md"))).toBe(true);
  });

  it("continues category 3 across short and empty authority-filtered candidate pages", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [publicPost(0)], next_cursor: "after-short" }))
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: "after-empty" }))
      .mockResolvedValueOnce(jsonResponse({ items: [publicPost(1)], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    const entries = await sitemap({ id: 3 });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(entries.map((entry) => entry.url)).toEqual([
      "https://connect.md/posts/post-0",
      "https://connect.md/posts/post-1",
    ]);
  });

  it("fails category 3 closed on a later request failure, duplicate post, or cursor loop", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    const firstPage = Array.from({ length: 200 }, (_, index) => publicPost(index));
    const secondPage = Array.from({ length: 200 }, (_, index) => publicPost(index + 200));

    vi.stubGlobal("fetch", vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: firstPage, next_cursor: "next" }))
      .mockResolvedValueOnce(new Response("inventory unavailable", { status: 503 })));
    await expect(sitemap({ id: 3 })).resolves.toEqual([]);

    vi.stubGlobal("fetch", vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: firstPage, next_cursor: "next" }))
      .mockResolvedValueOnce(jsonResponse({ items: [publicPost(0)], next_cursor: null })));
    await expect(sitemap({ id: 3 })).resolves.toEqual([]);

    vi.stubGlobal("fetch", vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: firstPage, next_cursor: "loop" }))
      .mockResolvedValueOnce(jsonResponse({ items: secondPage, next_cursor: "loop" })));
    await expect(sitemap({ id: 3 })).resolves.toEqual([]);
  });

  it("fails category 3 closed on malformed inventory data without exposing a partial URL", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items: [{ ...publicPost(0), markdown: "body must stay private from the inventory" }], next_cursor: null })));
    await expect(sitemap({ id: 3 })).resolves.toEqual([]);
  });

  it("defines category 3 as the latest exact 50,000 public posts", async () => {
    process.env.CONNECTMD_API_BASE_URL = "https://api.connect.test";
    let pageNumber = 0;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => {
      const start = pageNumber * 200;
      const items = Array.from({ length: 200 }, (_, index) => publicPost(start + index));
      pageNumber += 1;
      return jsonResponse({ items, next_cursor: `cursor-${pageNumber}` });
    });
    vi.stubGlobal("fetch", fetchMock);

    const entries = await sitemap({ id: 3 });
    expect(fetchMock).toHaveBeenCalledTimes(250);
    expect(entries).toHaveLength(50_000);
    expect(entries[0]?.url).toBe("https://connect.md/posts/post-0");
    expect(entries.at(-1)?.url).toBe("https://connect.md/posts/post-49999");
  });
});
