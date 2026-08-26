import type { MetadataRoute } from "next";

import { listPublicAgentDirectory } from "@/lib/agent-identity-api";
import { listPublicPostsOnServer } from "@/lib/posts-api";
import { listPublicDocumentInventory } from "@/lib/public-inventory-api";
import { absoluteSiteUrl } from "@/lib/public-document";
import { emptyJobSearchFilters, hasActiveRecruitingControl, listPublicJobs, listPublicOrganizations } from "@/lib/recruitment-api";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";

export const dynamic = "force-dynamic";

const maxSitemapEntries = 50_000;
const maxCursorPages = 250;
const sitemapCategoryIds = [0, 1, 2, 3] as const;

function baseEntries(): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = [
    { url: absoluteSiteUrl("/"), changeFrequency: "weekly", priority: 1 },
    { url: absoluteSiteUrl("/search"), changeFrequency: "daily", priority: 0.9 },
    { url: absoluteSiteUrl("/discover"), changeFrequency: "daily", priority: 0.9 },
    { url: absoluteSiteUrl("/trust"), changeFrequency: "monthly", priority: 0.7 },
    { url: absoluteSiteUrl("/representatives"), changeFrequency: "daily", priority: 0.8 },
    { url: absoluteSiteUrl("/agent-directory"), changeFrequency: "daily", priority: 0.8 },
  ];
  if (recruitingReleaseEnabled()) {
    entries.push(
      { url: absoluteSiteUrl("/organizations"), changeFrequency: "daily", priority: 0.7 },
      { url: absoluteSiteUrl("/jobs"), changeFrequency: "daily", priority: 0.7 },
    );
  }
  return entries;
}

export function generateSitemaps() {
  return sitemapCategoryIds.map((id) => ({ id }));
}

export default async function sitemap({ id }: { id: number | string }): Promise<MetadataRoute.Sitemap> {
  const categoryId = typeof id === "string" && /^[0-3]$/.test(id) ? Number(id) : id;
  if (categoryId === 0) return collectDocumentSitemap();
  if (categoryId === 1) return collectRecruitmentSitemap();
  if (categoryId === 2) return collectAgentSitemap();
  if (categoryId === 3) return collectPostSitemap();
  return [];
}

async function collectDocumentSitemap(): Promise<MetadataRoute.Sitemap> {
  const fallbackEntries = baseEntries();
  const entries: MetadataRoute.Sitemap = [...fallbackEntries];
  const seenUrls = new Set(entries.map((entry) => entry.url));
  const seenCursors = new Set<string>();
  let cursor: string | null = null;

  try {
    for (let pageNumber = 0; pageNumber < maxCursorPages; pageNumber += 1) {
      const page = await listPublicDocumentInventory(cursor);
      for (const item of page.items) {
        const url = absoluteSiteUrl(item.kind === "profile" ? `/p/${encodeURIComponent(item.slug)}` : `/r/${encodeURIComponent(item.slug)}`);
        if (!appendEntry(entries, seenUrls, {
          url,
          lastModified: new Date(item.updatedAt),
          changeFrequency: "weekly",
          priority: item.kind === "profile" ? 0.8 : 0.7
        })) return entries;
      }

      const nextCursor = page.nextCursor;
      if (!nextCursor) return entries;
      if (seenCursors.has(nextCursor)) throw new Error("The public-document inventory cursor did not progress.");
      seenCursors.add(nextCursor);
      cursor = nextCursor;
    }
    throw new Error("The public-document inventory exceeded its pagination bound.");
  } catch {
    return fallbackEntries;
  }
}

async function collectRecruitmentSitemap(): Promise<MetadataRoute.Sitemap> {
  if (!recruitingReleaseEnabled()) return [];
  const entries: MetadataRoute.Sitemap = [];
  const seenUrls = new Set<string>();

  try {
    const seenOrganizationCursors = new Set<string>();
    let organizationCursor: string | null = null;
    for (let pageNumber = 0; pageNumber < maxCursorPages; pageNumber += 1) {
      const page = await listPublicOrganizations("", organizationCursor);
      for (const organization of page.items) {
        if (!hasActiveRecruitingControl(organization)) continue;
        if (!appendEntry(entries, seenUrls, {
          url: absoluteSiteUrl(`/organizations/${encodeURIComponent(organization.slug)}`),
          lastModified: new Date(organization.updatedAt),
          changeFrequency: "weekly",
          priority: 0.65
        })) return entries;
      }

      const nextCursor = page.nextCursor;
      if (!nextCursor) {
        organizationCursor = null;
        break;
      }
      if (seenOrganizationCursors.has(nextCursor)) throw new Error("The organization cursor did not progress.");
      seenOrganizationCursors.add(nextCursor);
      organizationCursor = nextCursor;
    }
    if (organizationCursor) throw new Error("The organization inventory exceeded its pagination bound.");

    const seenJobCursors = new Set<string>();
    let jobCursor: string | null = null;
    for (let pageNumber = 0; pageNumber < maxCursorPages; pageNumber += 1) {
      const page = await listPublicJobs({ ...emptyJobSearchFilters, cursor: jobCursor });
      for (const job of page.items) {
        if (job.status !== "published") continue;
        if (!appendEntry(entries, seenUrls, {
          url: absoluteSiteUrl(`/jobs/${encodeURIComponent(job.organizationSlug)}/${encodeURIComponent(job.slug)}`),
          lastModified: new Date(job.updatedAt),
          changeFrequency: "weekly",
          priority: 0.65
        })) return entries;
      }

      const nextCursor = page.nextCursor;
      if (!nextCursor) {
        jobCursor = null;
        break;
      }
      if (seenJobCursors.has(nextCursor)) throw new Error("The job cursor did not progress.");
      seenJobCursors.add(nextCursor);
      jobCursor = nextCursor;
    }
    if (jobCursor) throw new Error("The job inventory exceeded its pagination bound.");
    return entries;
  } catch {
    return [];
  }
}

async function collectAgentSitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = [];
  const seenUrls = new Set<string>();
  const seenHandles = new Set<string>();
  const seenCursors = new Set<string>();
  let cursor: string | null = null;

  try {
    for (let pageNumber = 0; pageNumber < maxCursorPages; pageNumber += 1) {
      const page = await listPublicAgentDirectory({ cursor });
      for (const identity of page.identities) {
        if (seenHandles.has(identity.handle)) continue;
        seenHandles.add(identity.handle);
        if (!appendEntry(entries, seenUrls, {
          url: absoluteSiteUrl(`/agents/${encodeURIComponent(identity.handle)}`),
          changeFrequency: "daily",
          priority: 0.6
        })) return entries;
      }

      const nextCursor = page.nextCursor;
      if (!nextCursor) return entries;
      if (seenCursors.has(nextCursor)) throw new Error("The public agent directory cursor did not progress.");
      seenCursors.add(nextCursor);
      cursor = nextCursor;
    }
    throw new Error("The public agent directory exceeded its pagination bound.");
  } catch {
    return [];
  }
}

async function collectPostSitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = [];
  const seenIds = new Set<string>();
  const seenCursors = new Set<string>();
  let cursor: string | null = null;

  try {
    for (let pageNumber = 0; pageNumber < maxCursorPages; pageNumber += 1) {
      const page = await listPublicPostsOnServer(200, cursor);
      for (const post of page.items) {
        if (seenIds.has(post.id)) throw new Error("The public post inventory repeated a post.");
        seenIds.add(post.id);
        if (entries.length >= maxSitemapEntries) throw new Error("The public post sitemap exceeded its entry bound.");
        entries.push({
          url: absoluteSiteUrl(post.htmlUrl),
          lastModified: new Date(post.updatedAt),
          changeFrequency: "weekly",
          priority: 0.55,
        });
      }

      const nextCursor = page.nextCursor;
      if (!nextCursor || entries.length === maxSitemapEntries) return entries;
      if (seenCursors.has(nextCursor)) throw new Error("The public post inventory cursor did not progress.");
      seenCursors.add(nextCursor);
      cursor = nextCursor;
    }
    throw new Error("The public post inventory exceeded its 50,000-post sitemap window.");
  } catch {
    return [];
  }
}

function appendEntry(entries: MetadataRoute.Sitemap, seenUrls: Set<string>, entry: MetadataRoute.Sitemap[number]) {
  if (seenUrls.has(entry.url)) return true;
  if (entries.length >= maxSitemapEntries) return false;
  seenUrls.add(entry.url);
  entries.push(entry);
  return true;
}
