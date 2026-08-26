import type { Metadata } from "next";

import { RepresentativeDirectory } from "@/components/representative-directory";
import { presentPublicReadError } from "@/lib/api";
import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";
import { searchDirectory } from "@/lib/public-search-api";
import { representativeFiltersFromParams } from "@/lib/representatives";
import { serverSearchParams, type ServerSearchParams } from "@/lib/server-search-params";

export const metadata: Metadata = {
  title: "Representative discovery",
  description: "Discover public connect.md profiles with owner-attested representative or organisation-managed declarations.",
  alternates: { canonical: "/representatives" }
};
export const dynamic = "force-dynamic";

export default async function RepresentativesPage({ searchParams }: { searchParams: Promise<ServerSearchParams> }) {
  const filters = representativeFiltersFromParams(serverSearchParams(await searchParams));
  const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();
  try {
    return <RepresentativeDirectory filters={filters} response={await searchDirectory(filters)} error={null} privateWorkspacesEnabled={privateWorkspacesEnabled} />;
  } catch (error) {
    return <RepresentativeDirectory filters={filters} response={null} error={presentPublicReadError(error)} privateWorkspacesEnabled={privateWorkspacesEnabled} />;
  }
}
