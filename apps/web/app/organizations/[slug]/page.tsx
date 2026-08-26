import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { OrganizationPublicPage } from "@/components/organization-directory";
import { ApiRequestError } from "@/lib/api";
import { emptyJobSearchFilters, fetchPublicOrganization, hasActiveRecruitingControl, listPublicJobs } from "@/lib/recruitment-api";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  if (!recruitingReleaseEnabled()) notFound();
  try { const organization = await fetchPublicOrganization((await params).slug); const path = `/organizations/${encodeURIComponent(organization.slug)}`; return { title: hasActiveRecruitingControl(organization) ? organization.name : "Organization", description: organization.description ?? "Public organization page with current active recruiting verification. Management is private to signed-in humans; publication, applications, and employer applicant access require current active recruiting verification.", alternates: { canonical: path }, openGraph: { title: organization.name, description: organization.description ?? "Service-gated organization page.", url: path } }; }
  catch { return { title: "Organization" }; }
}

export default async function OrganizationPage({ params }: { params: Promise<{ slug: string }> }) {
  if (!recruitingReleaseEnabled()) notFound();
  const slug = (await params).slug;
  try {
    const organization = await fetchPublicOrganization(slug);
    if (!hasActiveRecruitingControl(organization)) notFound();
    const jobs = await listPublicJobs({ ...emptyJobSearchFilters, organizationSlug: organization.slug });
    return <OrganizationPublicPage organization={organization} jobs={jobs.items} />;
  } catch (error) {
    if (error instanceof ApiRequestError && error.code === "not_found") notFound();
    throw error;
  }
}
