import type { PublicAgentIdentity, PublicAgentIdentityDirectory } from "@/lib/agent-identity-api";
import type { DirectorySearchResponse } from "@/lib/public-search-api";
import { absoluteSiteUrl } from "@/lib/public-document";
import type { Job, Organization } from "@/lib/recruitment-api";
import type { ProfessionalPost, PublicPostSummary } from "@/lib/posts-api";

type DiscoveryProjection = {
  profiles: DirectorySearchResponse | null;
  agents: PublicAgentIdentityDirectory | null;
  organizations: Organization[];
  jobs: Job[];
  posts: PublicPostSummary[];
};

export function publicDiscoveryJsonLd({ profiles, agents, organizations, jobs, posts }: DiscoveryProjection) {
  const entries = [
    ...(profiles?.hits ?? []).slice(0, 8).map((hit) => ({
      name: `${hit.name} — public ${hit.kind}`,
      path: hit.htmlUrl
    })),
    ...(agents?.identities ?? []).slice(0, 8).map((identity) => ({
      name: `${identity.displayName} — public Agent Identity`,
      path: `/agents/${encodeURIComponent(identity.handle)}`
    })),
    ...organizations.slice(0, 8).map((organization) => ({
      name: organization.name,
      path: `/organizations/${encodeURIComponent(organization.slug)}`
    })),
    ...jobs.slice(0, 8).map((job) => ({
      name: `${job.title} at ${job.organizationName}`,
      path: `/jobs/${encodeURIComponent(job.organizationSlug)}/${encodeURIComponent(job.slug)}`
    })),
    ...posts.slice(0, 8).map((post) => ({
      name: `${post.title} — professional post by @${post.authorProfileHandle}`,
      path: post.htmlUrl
    }))
  ];

  return {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: "connect.md public HTML mirror",
    description: "A server-rendered discovery projection of public connect.md profiles, resumes, professional posts, Agent Identities, organizations, and jobs.",
    url: absoluteSiteUrl("/discover"),
    isPartOf: {
      "@type": "WebSite",
      name: "connect.md",
      url: absoluteSiteUrl("/")
    },
    mainEntity: {
      "@type": "ItemList",
      numberOfItems: entries.length,
      itemListElement: entries.map((entry, index) => ({
        "@type": "ListItem",
        position: index + 1,
        name: entry.name,
        url: absoluteSiteUrl(entry.path)
      }))
    }
  };
}

export function postArticleJsonLd(post: ProfessionalPost) {
  const url = absoluteSiteUrl(`/posts/${encodeURIComponent(post.id)}`);
  return {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: post.title,
    datePublished: post.publishedAt,
    mainEntityOfPage: url,
    url,
    author: {
      "@type": "Person",
      alternateName: `@${post.authorProfileHandle}`,
      url: absoluteSiteUrl(`/p/${encodeURIComponent(post.authorProfileHandle)}`)
    },
    keywords: post.topics.length > 0 ? post.topics : undefined
  };
}

export function profilePostArchiveJsonLd(handle: string, posts: ProfessionalPost[]) {
  const url = absoluteSiteUrl(`/p/${encodeURIComponent(handle)}/posts`);
  return {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: `Public professional posts by @${handle}`,
    url,
    about: {
      "@type": "Person",
      alternateName: `@${handle}`,
      url: absoluteSiteUrl(`/p/${encodeURIComponent(handle)}`)
    },
    mainEntity: {
      "@type": "ItemList",
      numberOfItems: posts.length,
      itemListElement: posts.map((post, index) => ({
        "@type": "ListItem",
        position: index + 1,
        name: post.title,
        url: absoluteSiteUrl(`/posts/${encodeURIComponent(post.id)}`)
      }))
    }
  };
}

export function agentIdentityJsonLd(identity: PublicAgentIdentity) {
  const url = absoluteSiteUrl(`/agents/${encodeURIComponent(identity.handle)}`);
  return {
    "@context": "https://schema.org",
    "@type": "ProfilePage",
    name: `${identity.displayName} — connect.md Agent Identity`,
    description: identity.description,
    url,
    mainEntity: {
      "@type": "Thing",
      "@id": `${url}#identity`,
      name: identity.displayName,
      identifier: identity.handle,
      description: identity.description
    },
    relatedLink: absoluteSiteUrl(`/p/${encodeURIComponent(identity.profileHandle)}`)
  };
}

export function agentDirectoryJsonLd(directory: PublicAgentIdentityDirectory) {
  return {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: "connect.md public Agent Directory",
    description: "Published owner-attested Agent Identities with internal mediated-contact capability.",
    url: absoluteSiteUrl("/agent-directory"),
    mainEntity: {
      "@type": "ItemList",
      numberOfItems: directory.identities.length,
      itemListElement: directory.identities.map((identity, index) => ({
        "@type": "ListItem",
        position: index + 1,
        name: identity.displayName,
        url: absoluteSiteUrl(`/agents/${encodeURIComponent(identity.handle)}`)
      }))
    }
  };
}

export function organizationJsonLd(organization: Organization) {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: organization.name,
    description: organization.description ?? undefined,
    url: absoluteSiteUrl(`/organizations/${encodeURIComponent(organization.slug)}`)
  };
}

export function jobPostingJsonLd(job: Job) {
  const remote = job.workMode === "remote";
  return {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    title: job.title,
    description: job.description,
    datePosted: job.publishedAt ?? undefined,
    employmentType: employmentType(job.employmentType),
    url: absoluteSiteUrl(`/jobs/${encodeURIComponent(job.organizationSlug)}/${encodeURIComponent(job.slug)}`),
    hiringOrganization: {
      "@type": "Organization",
      name: job.organizationName,
      url: absoluteSiteUrl(`/organizations/${encodeURIComponent(job.organizationSlug)}`)
    },
    jobLocationType: remote ? "TELECOMMUTE" : undefined,
    jobLocation: !remote && job.location ? {
      "@type": "Place",
      address: {
        "@type": "PostalAddress",
        addressLocality: job.location
      }
    } : undefined
  };
}

function employmentType(value: Job["employmentType"]) {
  return value ? value.toUpperCase() : undefined;
}
