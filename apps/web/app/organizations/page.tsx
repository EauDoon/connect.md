import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { OrganizationDirectory } from "@/components/organization-directory";
import { presentPublicReadError } from "@/lib/api";
import { listPublicOrganizations } from "@/lib/recruitment-api";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";

export const dynamic = "force-dynamic";

export function generateMetadata(): Metadata {
  if (!recruitingReleaseEnabled()) notFound();
  return { title: "Organizations", description: "Public browsing of organizations with current active recruiting verification. Management is private to signed-in humans; publication, applications, and employer applicant access require current active recruiting verification.", alternates: { canonical: "/organizations" } };
}

export default async function OrganizationsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  if (!recruitingReleaseEnabled()) notFound();
  const input = await searchParams;
  const query = typeof input.q === "string" ? input.q : "";
  const cursor = typeof input.cursor === "string" ? input.cursor : null;
  try { return <OrganizationDirectory query={query} cursor={cursor} response={await listPublicOrganizations(query, cursor)} error={null} />; }
  catch (error) { return <OrganizationDirectory query={query} cursor={cursor} response={null} error={presentPublicReadError(error)} />; }
}
