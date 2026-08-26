import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { JobDirectory } from "@/components/job-directory";
import { presentPublicReadError } from "@/lib/api";
import { jobSearchFiltersFromParams, listPublicJobs } from "@/lib/recruitment-api";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";
import { serverSearchParams, type ServerSearchParams } from "@/lib/server-search-params";

export const dynamic = "force-dynamic";

export function generateMetadata(): Metadata {
  if (!recruitingReleaseEnabled()) notFound();
  return { title: "Jobs", description: "Public browsing of published jobs with current active recruiting verification. Management is private to signed-in humans; publication, applications, and employer applicant access require current active recruiting verification.", alternates: { canonical: "/jobs" } };
}

export default async function JobsPage({ searchParams }: { searchParams: Promise<ServerSearchParams> }) {
  if (!recruitingReleaseEnabled()) notFound();
  const filters = jobSearchFiltersFromParams(serverSearchParams(await searchParams));
  try { return <JobDirectory filters={filters} response={await listPublicJobs(filters)} error={null} />; }
  catch (error) { return <JobDirectory filters={filters} response={null} error={presentPublicReadError(error)} />; }
}
