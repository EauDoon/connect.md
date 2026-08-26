import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { JobApplicationPanel } from "@/components/job-application-panel";
import { JobPublicPage } from "@/components/job-directory";
import { ApiRequestError } from "@/lib/api";
import { fetchPublicJob, fetchPublicOrganization, hasActiveRecruitingControl } from "@/lib/recruitment-api";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ organizationSlug: string; jobSlug: string }> }): Promise<Metadata> {
  if (!recruitingReleaseEnabled()) notFound();
  try { const values = await params; const job = await fetchPublicJob(values.organizationSlug, values.jobSlug); const path = `/jobs/${encodeURIComponent(job.organizationSlug)}/${encodeURIComponent(job.slug)}`; return { title: `${job.title} · ${job.organizationName}`, description: job.description, alternates: { canonical: path }, openGraph: { title: `${job.title} · ${job.organizationName}`, description: job.description, url: path } }; }
  catch { return { title: "Job" }; }
}

export default async function JobPage({ params }: { params: Promise<{ organizationSlug: string; jobSlug: string }> }) {
  if (!recruitingReleaseEnabled()) notFound();
  const values = await params;
  try {
    const [job, organization] = await Promise.all([fetchPublicJob(values.organizationSlug, values.jobSlug), fetchPublicOrganization(values.organizationSlug)]);
    if (!hasActiveRecruitingControl(organization)) notFound();
    return <JobPublicPage job={job} applicationPanel={<JobApplicationPanel job={job} />} />;
  } catch (error) {
    if (error instanceof ApiRequestError && error.code === "not_found") notFound();
    throw error;
  }
}
