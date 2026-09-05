import type { Metadata } from "next";
import Link from "next/link";

import { listPublishedProfiles } from "@/lib/network/profiles";
import { database, networkDatabaseConfigured } from "@/lib/network/db";

export const metadata: Metadata = {
  title: "Discover",
  description: "Professional profiles their owners have chosen to publish.",
  alternates: { canonical: "/discover" },
};

export const dynamic = "force-dynamic";

export default async function DiscoverPage() {
  if (!networkDatabaseConfigured()) {
    return <DiscoverShell>
      <p className="text-mist">Discovery is not available yet. The network database has not been configured for this deployment.</p>
    </DiscoverShell>;
  }
  const profiles = await listPublishedProfiles(database(), { limit: 100 });
  return (
    <DiscoverShell>
      {profiles.length === 0 ? (
        <p className="text-mist" data-testid="discover-empty">No published profiles yet. Publish yours from the network dashboard.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="discover-list">
          {profiles.map((profile) => (
            <li key={profile.handle}>
              <Link
                href={`/p/${profile.handle}`}
                className="block rounded-2xl border border-white/10 bg-white/[.03] p-5 transition hover:border-acid/40 hover:bg-white/[.05] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
              >
                <p className="text-lg font-semibold text-white">@{profile.handle}</p>
                <p className="mt-1 text-xs text-mist">Published {profile.publishedAt.slice(0, 10)}</p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </DiscoverShell>
  );
}

function DiscoverShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="pb-16">
      <section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.14),_transparent_34%)]">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8 lg:py-20">
          <p className="eyebrow">Discover</p>
          <h1 className="mt-4 max-w-5xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">
            Profiles their owners chose to publish.
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-mist">
            Only explicitly published profiles appear here. Private profiles
            are never listed, indexed, or exposed.
          </p>
        </div>
      </section>
      <section className="mx-auto max-w-7xl px-5 py-12 lg:px-8">{children}</section>
    </main>
  );
}
