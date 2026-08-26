import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";

import type { DocumentResponse } from "../lib/api";
import { profilePageJsonLd, publicDocumentJsonLd, publicDocumentMetadata, publicMarkdownAlternate, safeJsonLd } from "../lib/public-document";
import { agentDirectoryJsonLd, agentIdentityJsonLd, jobPostingJsonLd, postArticleJsonLd, profilePostArchiveJsonLd, publicDiscoveryJsonLd } from "../lib/public-projections";
import type { Job, Organization } from "../lib/recruitment-api";
import type { ProfessionalPost } from "../lib/posts-api";

const post: ProfessionalPost = {
  id: "post-1",
  authorProfileHandle: "ari-chen",
  title: "A public professional note",
  topics: ["payments"],
  version: 1,
  publishedAt: "2026-08-03T00:00:00Z",
  updatedAt: "2026-08-03T00:00:00Z",
  markdown: "# A public professional note\n",
  markdownUrl: "/v1/posts/post-1.md",
  etag: "post-etag"
};
const publicPost = {
  id: post.id,
  authorProfileHandle: post.authorProfileHandle,
  title: post.title,
  topics: post.topics,
  version: post.version,
  publishedAt: post.publishedAt,
  updatedAt: post.updatedAt,
  htmlUrl: "/posts/post-1",
  markdownUrl: post.markdownUrl,
  etag: post.etag,
};

const organization: Organization = {
  id: "org-1",
  slug: "example-org",
  name: "Example Org",
  description: "Public organization description.",
  websiteUrl: "https://declared.example.test",
  visibility: "public",
  recruitingVerificationActive: true,
  recruitingVerificationPurpose: "recruiting_control",
  recruitingVerificationExpiresAt: "2026-09-01T00:00:00Z",
  version: 1,
  createdAt: "2026-08-01T00:00:00Z",
  updatedAt: "2026-08-03T00:00:00Z",
  etag: "org-etag"
};

const job: Job = {
  id: "job-1",
  organizationId: organization.id,
  organizationSlug: organization.slug,
  organizationName: organization.name,
  slug: "payments-lead",
  title: "Payments Lead",
  description: "Lead a public payments role.",
  location: "Singapore",
  workMode: "hybrid",
  employmentType: "full_time",
  status: "published",
  version: 1,
  publishedAt: "2026-08-03T00:00:00Z",
  createdAt: "2026-08-02T00:00:00Z",
  updatedAt: "2026-08-03T00:00:00Z",
  etag: "job-etag"
};

const priorApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
afterEach(() => {
  if (priorApiBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = priorApiBase;
});

describe("public HTML projections", () => {
  it("wraps a canonical Markdown person in ProfilePage structured data", () => {
    const document: DocumentResponse = {
      id: "doc-1",
      kind: "profile",
      identifier: "ari-chen",
      visibility: "public",
      version: 1,
      etag: "profile-etag-v1",
      updated_at: "2026-08-03T00:00:00Z",
      markdown: "---\nschema: connect.md/profile\nname: Ari Chen\nvisibility: public\n---\n# Ari Chen\n",
      markdown_url: "/v1/profiles/ari-chen.md"
    };
    const projection = profilePageJsonLd(document, "https://connect.md/p/ari-chen");
    expect(projection).toMatchObject({ "@type": "ProfilePage", dateModified: document.updated_at, mainEntity: { "@type": "Person", name: "Ari Chen", "@id": "https://connect.md/p/ari-chen#person" } });
  });

  it("projects a named resume as minimal DigitalDocument structured data", () => {
    const resumeName = "<Ari\u2028Chen\u2029>";
    const resumeHeadline = "<Payments\u2028leader\u2029>";
    const resume = {
      id: "resume-1",
      kind: "resume",
      identifier: "ari-chen-resume",
      visibility: "public",
      version: 2,
      etag: "resume-etag-v2",
      updated_at: "2026-08-03T00:00:00Z",
      owner_id: "private-owner",
      markdown: `---\nschema: connect.md/resume\nname: "${resumeName}"\nheadline: "${resumeHeadline}"\nidentifier: private-identifier-value\nslug: private-slug-value\ntitle: Private Job Title\njob: Private Job Value\norganizations:\n  - Private Organization Value\nemployer: Private Employer Value\neducation: Private Education Value\ncredentials:\n  - Private Credential Value\nowner_id: private-owner\nprivate_field: Private Field Value\nauthority: Private Authority Value\nvisibility: public\n---\n# Ari Chen\n`,
      markdown_url: "/v1/resumes/ari-chen-resume.md"
    } as DocumentResponse;
    const unnamedProfile = { ...resume, kind: "profile" as const, identifier: "ari-chen", markdown_url: "/v1/profiles/ari-chen.md", markdown: "---\nschema: connect.md/profile\nvisibility: public\n---\nNo public name declared.\n" };

    const projection = publicDocumentJsonLd(resume, "https://connect.md/r/ari-chen-resume");
    expect(projection).toEqual({
      "@context": "https://schema.org",
      "@type": "DigitalDocument",
      name: "<Ari Chen >",
      description: "<Payments leader >",
      url: "https://connect.md/r/ari-chen-resume",
      dateModified: resume.updated_at,
      version: "2",
      encodingFormat: "text/markdown"
    });
    expect(Object.keys(projection ?? {}).sort()).toEqual(["@context", "@type", "dateModified", "description", "encodingFormat", "name", "url", "version"].sort());
    const serialized = safeJsonLd(projection);
    expect(serialized).toContain("\\u003cAri Chen >");
    expect(serialized).toContain("\\u003cPayments leader >");
    expect(serialized).not.toContain("\u2028");
    expect(serialized).not.toContain("\u2029");
    expect(serialized).not.toMatch(/identifier|slug|title|job|organizations|employer|education|credentials|owner_id|private_field|authority|Person|ProfilePage|author|creator|private-identifier-value|private-slug-value|Private Job Title|Private Job Value|Private Organization Value|Private Employer Value|Private Education Value|Private Credential Value|private-owner|Private Field Value|Private Authority Value/u);
    expect(profilePageJsonLd(unnamedProfile, "https://connect.md/p/ari-chen")).toBeNull();
    expect(publicDocumentMetadata(resume, "/r/ari-chen-resume")).toMatchObject({ openGraph: { type: "website" } });
  });

  it("requires an explicit resume name and omits absent descriptions", () => {
    const unnamedResume: DocumentResponse = {
      id: "resume-unnamed",
      kind: "resume",
      identifier: "inferred-from-path-resume",
      visibility: "public",
      version: 1,
      etag: "resume-etag-v1",
      updated_at: "2026-08-03T00:00:00Z",
      markdown: "---\nschema: connect.md/resume\nvisibility: public\n---\n# Inferred From Heading\n",
      markdown_url: "/v1/resumes/inferred-from-path-resume.md"
    };
    const namedResume: DocumentResponse = {
      ...unnamedResume,
      markdown: "---\nschema: connect.md/resume\nname: Ada Lovelace\nvisibility: public\n---\n# Ada Lovelace\n"
    };

    expect(publicDocumentJsonLd(unnamedResume, "https://connect.md/r/inferred-from-path-resume")).toBeNull();
    expect(publicDocumentJsonLd(namedResume, "https://connect.md/r/ada-lovelace-resume")).toEqual({
      "@context": "https://schema.org",
      "@type": "DigitalDocument",
      name: "Ada Lovelace",
      url: "https://connect.md/r/ada-lovelace-resume",
      dateModified: namedResume.updated_at,
      version: "1",
      encodingFormat: "text/markdown"
    });
  });

  it("bounds and normalizes resume structured-data fields and preserves safe escaping", () => {
    const resume: DocumentResponse = {
      id: "resume-long",
      kind: "resume",
      identifier: "long-resume",
      visibility: "public",
      version: 3,
      etag: "resume-etag-v3",
      updated_at: "2026-08-03T00:00:00Z",
      markdown: `---\nschema: connect.md/resume\nname: "  ${"N".repeat(180)}  "\nheadline: "  ${"H".repeat(300)}  "\nvisibility: public\n---\n# Resume\n`,
      markdown_url: "/v1/resumes/long-resume.md"
    };
    const projection = publicDocumentJsonLd(resume, "https://connect.md/r/long-resume");
    expect(projection).toMatchObject({ "@type": "DigitalDocument" });
    expect(String((projection as { name: string }).name)).toHaveLength(160);
    expect(String((projection as { description: string }).description)).toHaveLength(280);
    expect(String((projection as { name: string }).name)).not.toMatch(/\s/u);
    expect(String((projection as { description: string }).description)).not.toMatch(/\s/u);

    const escaped = safeJsonLd({ projection, value: "<script>\u2028" });
    expect(escaped).toContain("\\u003cscript>");
    expect(escaped).toContain("\\u2028");
    expect(escaped).not.toContain("<script>");
  });

  it("bounds and whitespace-normalizes title and description metadata", () => {
    const document: DocumentResponse = {
      id: "doc-long",
      kind: "profile",
      identifier: "ari-chen",
      visibility: "public",
      version: 1,
      etag: "profile-etag-v1",
      updated_at: "2026-08-03T00:00:00Z",
      markdown: `---\nschema: connect.md/profile\nname: ${"N".repeat(220)}\nheadline: ${"H".repeat(420)}\nvisibility: public\n---\n# Ari Chen\n`,
      markdown_url: "/v1/profiles/ari-chen.md"
    };
    const metadata = publicDocumentMetadata(document, "/p/ari-chen");

    expect(typeof metadata.title).toBe("string");
    expect(String(metadata.title).length).toBeLessThanOrEqual(160);
    expect(String(metadata.description).length).toBeLessThanOrEqual(280);
    expect(String(metadata.title)).not.toMatch(/[\r\n]/u);
    expect(String(metadata.description)).not.toMatch(/[\r\n]/u);
  });

  it("binds the HTML body and source facts to the canonical response fields", () => {
    const source = readFileSync(new URL("../components/public-document-page.tsx", import.meta.url), "utf8");
    expect(source).toContain("<MarkdownPreview markdown={document.markdown} omitTitle />");
    expect(source).toContain("const markdownHref = publicApiMarkdownUrl(document.markdown_url);");
    expect(source).toContain('value={String(document.version)}');
    expect(source).not.toContain("owner_id");
  });

  it("prepares private contact only from an explicitly linked public profile identity", () => {
    const source = readFileSync(new URL("../components/public-document-page.tsx", import.meta.url), "utf8");
    expect(source).toContain("identity.profileHandle === document.identifier");
    expect(source).toContain("buildInboxContactReturnPath(linkedContactIdentity.profileHandle)");
    expect(source).not.toContain("buildInboxContactReturnPath(identity.handle)");
    expect(source).not.toMatch(/sendContactRequest|agent_outreach|sendAgentOutreach/u);
  });

  it("points Markdown metadata alternates at the configured public API origin", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.connect.test";
    const document: DocumentResponse = {
      id: "doc-1",
      kind: "profile",
      identifier: "ari-chen",
      visibility: "public",
      version: 1,
      etag: "profile-etag-v1",
      updated_at: "2026-08-03T00:00:00Z",
      markdown: "# Ari Chen\n",
      markdown_url: "/v1/profiles/ari-chen.md"
    };
    expect(publicMarkdownAlternate(document.markdown_url)).toEqual({ "text/markdown": "https://api.connect.test/v1/profiles/ari-chen.md" });
    expect(publicDocumentMetadata(document, "/p/ari-chen")).toMatchObject({ alternates: { types: { "text/markdown": "https://api.connect.test/v1/profiles/ari-chen.md" } } });
    expect(publicMarkdownAlternate("https://untrusted.example/profile.md")).toBeUndefined();
  });

  it("uses the same normalized Markdown alternate for public post metadata", () => {
    const source = readFileSync(new URL("../app/posts/[id]/page.tsx", import.meta.url), "utf8");
    expect(source).toContain("publicMarkdownAlternate(post.markdownUrl)");
    expect(source).not.toContain('types: { "text/markdown": post.markdownUrl }');
    expect(source).not.toContain("modifiedTime");
  });

  it("projects only public directory fields into the HTML hub ItemList", () => {
    const projection = publicDiscoveryJsonLd({
      profiles: { hits: [{ id: "doc-1", kind: "profile", identifier: "stale-identifier", name: "Ari Chen", htmlUrl: "/p/ari-chen" }], offset: 0, limit: 20, total: 1, indexingAvailable: true, warning: null, facets: {} } as never,
      agents: { identities: [{ handle: "ari-agent", displayName: "Ari Agent", description: "Mediated contact.", profileHandle: "ari-chen", capabilities: ["internal_contact_request"] }], nextCursor: null },
      organizations: [organization],
      jobs: [job],
      posts: [publicPost]
    });
    const serialized = JSON.stringify(projection);
    expect(projection).toMatchObject({ "@type": "CollectionPage", mainEntity: { "@type": "ItemList", numberOfItems: 5 } });
    expect(serialized).toContain("https://connect.md/p/ari-chen");
    expect(serialized).not.toContain("stale-identifier");
    expect(serialized).toContain("https://connect.md/agents/ari-agent");
    expect(serialized).toContain("https://connect.md/posts/post-1");
    expect(serialized).not.toContain(post.markdown);
    expect(serialized).not.toContain("owner_id");
    expect(serialized).not.toContain("mandate");
  });

  it("creates crawlable post, archive, and published-job projections", () => {
    const article = postArticleJsonLd(post);
    expect(article).toMatchObject({ "@type": "Article", headline: post.title, author: { url: "https://connect.md/p/ari-chen" } });
    expect(article).not.toHaveProperty("dateModified");
    expect(profilePostArchiveJsonLd("ari-chen", [post])).toMatchObject({ "@type": "CollectionPage", mainEntity: { numberOfItems: 1, itemListElement: [{ url: "https://connect.md/posts/post-1" }] } });
    expect(jobPostingJsonLd(job)).toMatchObject({ "@type": "JobPosting", datePosted: job.publishedAt, employmentType: "FULL_TIME", hiringOrganization: { name: organization.name } });
  });

  it("projects only owner-attested public Agent Identity fields", () => {
    const identity = { handle: "ari-agent", displayName: "Ari Agent", description: "Mediated internal contact.", profileHandle: "ari-chen", capabilities: ["internal_contact_request"] as ["internal_contact_request"] };
    expect(agentIdentityJsonLd(identity)).toMatchObject({ "@type": "ProfilePage", mainEntity: { "@type": "Thing", identifier: "ari-agent" }, relatedLink: "https://connect.md/p/ari-chen" });
    expect(agentDirectoryJsonLd({ identities: [identity], nextCursor: null })).toMatchObject({ "@type": "CollectionPage", mainEntity: { numberOfItems: 1, itemListElement: [{ url: "https://connect.md/agents/ari-agent" }] } });
    expect(JSON.stringify(agentIdentityJsonLd(identity))).not.toMatch(/mandate|grant|owner_id/u);
  });
});
