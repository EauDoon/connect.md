import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";

import { PublicDocumentPage } from "@/components/public-document-page";
import { listPublicProfileAgentIdentities } from "@/lib/agent-identity-api";
import { ApiRequestError, fetchPublicProfile } from "@/lib/api";
import { publicDocumentMetadata } from "@/lib/public-document";
import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";

export const dynamic = "force-dynamic";
const getProfile = cache(fetchPublicProfile);

export async function generateMetadata({ params }: { params: Promise<{ handle: string }> }): Promise<Metadata> {
  const { handle } = await params;
  try {
    const document = await getProfile(handle);
    return publicDocumentMetadata(document, `/p/${encodeURIComponent(document.identifier)}`);
  } catch {
    return { title: "Public profile" };
  }
}

export default async function PublicProfilePage({ params }: { params: Promise<{ handle: string }> }) {
  const { handle } = await params;
  let document;
  try {
    document = await getProfile(handle);
  } catch (error) {
    if (error instanceof ApiRequestError && error.code === "not_found") notFound();
    throw error;
  }
  const identities = await listPublicProfileAgentIdentities(handle).catch(() => null);
  return <PublicDocumentPage document={document} agentIdentities={identities?.identities ?? []} agentIdentitiesUnavailable={identities === null} privateWorkspacesEnabled={privateWorkspaceConfiguredFromEnvironment()} />;
}
