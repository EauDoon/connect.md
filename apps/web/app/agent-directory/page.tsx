import type { Metadata } from "next";

import { AgentDirectory } from "@/components/agent-directory";
import { agentDirectoryFiltersFromParams } from "@/lib/agent-directory";
import { listPublicAgentDirectory } from "@/lib/agent-identity-api";
import { presentPublicReadError } from "@/lib/api";
import { safeJsonLd } from "@/lib/public-document";
import { agentDirectoryJsonLd } from "@/lib/public-projections";
import { serverSearchParams, type ServerSearchParams } from "@/lib/server-search-params";

export const metadata: Metadata = {
  title: "Agent Directory",
  description: "Discover published connect.md Agent Identities linked to current public profiles and limited to internal mediated-contact capability.",
  alternates: { canonical: "/agent-directory" }
};
export const dynamic = "force-dynamic";

export default async function AgentDirectoryPage({ searchParams }: { searchParams: Promise<ServerSearchParams> }) {
  const filters = agentDirectoryFiltersFromParams(serverSearchParams(await searchParams));
  if (filters.invalidMessage) return <AgentDirectory filters={filters} response={null} error={filters.invalidMessage} />;
  try {
    const response = await listPublicAgentDirectory(filters);
    return <><script type="application/ld+json" dangerouslySetInnerHTML={{ __html: safeJsonLd(agentDirectoryJsonLd(response)) }} /><AgentDirectory filters={filters} response={response} error={null} /></>;
  } catch (error) {
    return <AgentDirectory filters={filters} response={null} error={presentPublicReadError(error)} />;
  }
}
