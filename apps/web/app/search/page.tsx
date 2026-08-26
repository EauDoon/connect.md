import type { Metadata } from "next";

import { SearchExperience } from "@/components/search-experience";
import { presentPublicReadError } from "@/lib/api";
import { searchDirectory, searchFiltersFromParams } from "@/lib/public-search-api";
import { serverSearchParams, type ServerSearchParams } from "@/lib/server-search-params";

export const metadata: Metadata = {
  title: "Search professional profiles",
  description: "Search canonical connect.md profiles and resumes by occupation, industry, skill, location, availability, and representation.",
  alternates: { canonical: "/search" }
};
export const dynamic = "force-dynamic";

export default async function SearchPage({ searchParams }: { searchParams: Promise<ServerSearchParams> }) {
  const filters = searchFiltersFromParams(serverSearchParams(await searchParams));
  try {
    return <SearchExperience filters={filters} response={await searchDirectory(filters)} error={null} />;
  } catch (error) {
    return <SearchExperience filters={filters} response={null} error={presentPublicReadError(error)} />;
  }
}
