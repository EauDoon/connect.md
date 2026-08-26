import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ProfilePostArchive } from "@/components/profile-post-archive";
import { ApiRequestError } from "@/lib/api";
import { listProfilePostsOnServer } from "@/lib/posts-api";
import { profilePostArchiveJsonLd } from "@/lib/public-projections";
import { safeJsonLd } from "@/lib/public-document";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ handle: string }> }): Promise<Metadata> {
  const { handle } = await params;
  return { title: `Posts by @${handle}`, description: "Public immutable professional posts from one profile archive.", alternates: { canonical: `/p/${encodeURIComponent(handle)}/posts` } };
}

export default async function ProfilePostsPage({ params }: { params: Promise<{ handle: string }> }) {
  const { handle } = await params;
  let initialPage;
  try {
    initialPage = await listProfilePostsOnServer(handle);
  } catch (error) {
    if (error instanceof ApiRequestError && error.code === "not_found") notFound();
    throw error;
  }
  return <><script type="application/ld+json" dangerouslySetInnerHTML={{ __html: safeJsonLd(profilePostArchiveJsonLd(handle, initialPage.posts)) }} /><ProfilePostArchive handle={handle} initialPage={initialPage} /></>;
}
