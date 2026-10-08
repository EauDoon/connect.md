import type { Metadata } from "next";

import { MarkdownPreview } from "@/components/markdown-preview";

import { loadPublishedProfile } from "./published-profile";

type PageProps = { params: Promise<{ handle: string }> };

export const dynamic = "force-dynamic";

// Only an explicitly published profile reaches this point (the layout and this
// shared lookup call notFound() otherwise), so it alone is indexable, under its
// normalized canonical handle.
export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { handle } = await params;
  const profile = await loadPublishedProfile(handle);
  return {
    title: `@${profile.handle}`,
    description: `Published professional profile of @${profile.handle} on connect.md.`,
    robots: { index: true, follow: true },
    alternates: { canonical: `/p/${profile.handle}` },
  };
}

export default async function PublicProfilePage({ params }: PageProps) {
  const { handle } = await params;
  const profile = await loadPublishedProfile(handle);
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
}

function ProfileShell({ handle, children }: { handle: string; children: React.ReactNode }) {
  return (
    <main className="pb-16">
      <section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.14),_transparent_34%)]">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8">
          <p className="eyebrow">Published profile</p>
          <h1 className="mt-2 font-display text-4xl font-semibold tracking-[-.04em] text-white">@{handle}</h1>
        </div>
      </section>
      <section className="mx-auto max-w-4xl px-5 py-12 lg:px-8">{children}</section>
    </main>
  );
}
