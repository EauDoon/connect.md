import type { Metadata } from "next";

import { NetworkDashboard } from "@/components/network/network-dashboard";
import { currentSession } from "@/lib/network/http";

export const metadata: Metadata = {
  title: "Your network",
  description: "Maintain your private profile, publish it explicitly, and manage contacts.",
  robots: { index: false, follow: false },
};

export const dynamic = "force-dynamic";

export default async function NetworkPage() {
  const session = await currentSession();
  return (
    <main className="pb-16">
      <section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.14),_transparent_34%)]">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8 lg:py-20">
          <p className="eyebrow">Network</p>
          <h1 className="mt-4 max-w-5xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">
            {session === null ? "Sign in to manage your profile." : `@${session.account.handle}`}
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-mist">
            Your profile is private until you publish it. Contact and
            conversation happen only by explicit consent.
          </p>
        </div>
      </section>
      <section className="mx-auto max-w-7xl px-5 py-12 lg:px-8">
        {session === null ? (
          <div className="rounded-3xl border border-white/10 bg-white/[.03] p-8" data-testid="network-signed-out">
            <p className="text-mist">
              You are signed out. <a href="/account" className="text-acid underline-offset-4 hover:underline">Sign in or create an account</a> to manage your profile and contacts.
            </p>
          </div>
        ) : (
          <NetworkDashboard handle={session.account.handle} />
        )}
      </section>
    </main>
  );
}
