import type { Metadata } from "next";
import Link from "next/link";

import { getPublishedProfile, ProfileError } from "@/lib/network/profiles";
import { MarkdownPreview } from "@/components/markdown-preview";
import { database, networkDatabaseConfigured } from "@/lib/network/db";

type PageProps = { params: Promise<{ handle: string }> };

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { handle } = await params;
  return {
    title: `@${handle}`,
    description: `Published professional profile of @${handle} on connect.md.`,
    robots: { index: true, follow: true },
    alternates: { canonical: `/p/${handle}` },
  };
}

export default async function PublicProfilePage({ params }: PageProps) {
  const { handle } = await params;
  if (!networkDatabaseConfigured()) {
    return (
      <ProfileShell handle={handle}>
        <p className="text-mist">Profile pages are not available yet on this deployment.</p>
      </ProfileShell>
    );
  }
  try {
    const profile = await getPublishedProfile(database(), handle);
    return (
      <ProfileShell handle={profile.handle}>
        <article className="rounded-3xl border border-white/10 bg-white/[.03] p-6 sm:p-10" data-testid="public-profile">
          <MarkdownPreview markdown={profile.markdown} />
        </article>
        <p className="mt-6 text-xs text-mist">
          Published by the owner. If this profile should not be public, its
          owner can unpublish it at any time.
        </p>
      </ProfileShell>
    );
  } catch (error) {
    if (error instanceof ProfileError && error.code === "not-found") {
      return (
        <ProfileShell handle={handle}>
          <div className="rounded-3xl border border-white/10 bg-white/[.03] p-8" data-testid="profile-unavailable">
            <p className="text-lg font-semibold text-white">No published profile for @{handle.toLowerCase()}.</p>
            <p className="mt-2 text-mist">The profile may be private, unpublished, or nonexistent.</p>
            <Link href="/discover" className="mt-6 inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/[.06]">
              Back to discovery
            </Link>
          </div>
        </ProfileShell>
      );
    }
    throw error;
  }
}

function ProfileShell({ handle, children }: { handle: string; children: React.ReactNode }) {
  return (
    <main className="pb-16">
      <section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.14),_transparent_34%)]">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8">
          <p className="eyebrow">Published profile</p>
          <h1 className="mt-2 font-display text-4xl font-semibold tracking-[-.04em] text-white">@{handle.toLowerCase()}</h1>
        </div>
      </section>
      <section className="mx-auto max-w-4xl px-5 py-12 lg:px-8">{children}</section>
    </main>
  );
}
