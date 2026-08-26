import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";

import { PublicDocumentPage } from "@/components/public-document-page";
import { ApiRequestError, fetchPublicResume } from "@/lib/api";
import { publicDocumentMetadata } from "@/lib/public-document";
import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";

export const dynamic = "force-dynamic";
const getResume = cache(fetchPublicResume);

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  try {
    const document = await getResume(slug);
    return publicDocumentMetadata(document, `/r/${encodeURIComponent(document.identifier)}`);
  } catch {
    return { title: "Public resume" };
  }
}

export default async function PublicResumePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  let document;
  try {
    document = await getResume(slug);
  } catch (error) {
    if (error instanceof ApiRequestError && error.code === "not_found") notFound();
    throw error;
  }
  return <PublicDocumentPage document={document} privateWorkspacesEnabled={privateWorkspaceConfiguredFromEnvironment()} />;
}
