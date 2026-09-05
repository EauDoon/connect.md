import type { Metadata } from "next";
import Link from "next/link";

import { AccountAuthPanel, AccountSignOut } from "@/components/network/account-auth-panel";
import { currentSession } from "@/lib/network/http";

export const metadata: Metadata = {
  title: "Account",
  description: "Create an account or sign in to the connect.md network.",
  robots: { index: false, follow: false },
  alternates: { canonical: "/account" },
};

export const dynamic = "force-dynamic";

export default async function AccountPage() {
  const session = await currentSession();
  return (
    <main className="pb-16">
      <section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.14),_transparent_34%)]">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8 lg:py-20">
          <p className="eyebrow">Network account</p>
          <h1 className="mt-4 max-w-5xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">
            Your profile, under your control.
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-mist">
            Accounts hold a private-by-default Markdown profile. Nothing is
            visible to anyone else until you explicitly publish it, and the
            guest builder keeps working without an account.
          </p>
        </div>
      </section>
      <section className="mx-auto max-w-7xl px-5 py-12 lg:px-8">
        {session === null ? (
          <AccountAuthPanel />
        ) : (
          <div className="rounded-3xl border border-white/10 bg-white/[.03] p-8" data-testid="account-signed-in">
            <h2 className="text-2xl font-semibold text-white">Signed in as @{session.account.handle}</h2>
            <p className="mt-3 text-mist">{session.account.email}</p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/network" className="inline-flex min-h-11 items-center rounded-full bg-acid px-5 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">
                Open your network
              </Link>
              <Link href="/human" className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/[.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">
                Guest builder
              </Link>
            </div>
            <AccountSignOut />
          </div>
        )}
      </section>
    </main>
  );
}
