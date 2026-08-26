import type { Metadata } from "next";

import { DiscoverHub, type DiscoverySource } from "@/components/discover-hub";
import { listPublicAgentDirectory } from "@/lib/agent-identity-api";
import { listPublicPostsOnServer } from "@/lib/posts-api";
import { emptySearchFilters, searchDirectory } from "@/lib/public-search-api";
import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";
import { emptyJobSearchFilters, listPublicJobs, listPublicOrganizations } from "@/lib/recruitment-api";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";

export const metadata: Metadata = {
  title: "Discover",
  description: "Explore public connect.md profiles, chronological professional posts, and owner-attested representation.",
  alternates: { canonical: "/discover" },
};
export const dynamic = "force-dynamic";

export default async function DiscoverPage() {
  const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();
  const recruitingEnabled = recruitingReleaseEnabled();
  const organizationsRequest = recruitingEnabled ? listPublicOrganizations() : Promise.resolve(null);
  const jobsRequest = recruitingEnabled ? listPublicJobs(emptyJobSearchFilters) : Promise.resolve(null);
  const [profiles, agents, organizations, jobs, posts] = await Promise.allSettled([
    searchDirectory(emptySearchFilters),
    listPublicAgentDirectory(),
    organizationsRequest,
    jobsRequest,
    listPublicPostsOnServer(4),
  ]);
  const unavailableSources: DiscoverySource[] = [];
  if (profiles.status === "rejected") unavailableSources.push("documents");
  if (agents.status === "rejected") unavailableSources.push("agents");
  if (recruitingEnabled && organizations.status === "rejected") unavailableSources.push("organizations");
  if (recruitingEnabled && jobs.status === "rejected") unavailableSources.push("jobs");
  if (posts.status === "rejected") unavailableSources.push("posts");

  return (
    <DiscoverHub
      profiles={profiles.status === "fulfilled" ? profiles.value : null}
      agents={agents.status === "fulfilled" ? agents.value : null}
      privateWorkspacesEnabled={privateWorkspacesEnabled}
      recruitingEnabled={recruitingEnabled}
      organizations={recruitingEnabled && organizations.status === "fulfilled" ? organizations.value : null}
      jobs={recruitingEnabled && jobs.status === "fulfilled" ? jobs.value : null}
      posts={posts.status === "fulfilled" ? posts.value : null}
      unavailableSources={unavailableSources}
    />
  );
}
