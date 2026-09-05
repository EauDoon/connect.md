import type { Metadata } from "next";

import { InboxPanel } from "@/components/network/inbox-panel";
import { currentSession } from "@/lib/network/http";

export const metadata: Metadata = {
  title: "Inbox",
  description: "Contact requests and conversations.",
  robots: { index: false, follow: false },
};

export const dynamic = "force-dynamic";

export default async function InboxPage() {
  const session = await currentSession();
  return (
    <main className="pb-16">
      <section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.14),_transparent_34%)]">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8">
          <p className="eyebrow">Inbox</p>
          <h1 className="mt-2 font-display text-4xl font-semibold tracking-[-.04em] text-white">Contact and conversations</h1>
          <p className="mt-4 max-w-3xl text-lg leading-8 text-mist">
            Every conversation starts from an accepted contact request, and a
            block closes everything immediately.
          </p>
        </div>
      </section>
      <section className="mx-auto max-w-7xl px-5 py-12 lg:px-8">
        {session === null ? (
          <div className="rounded-3xl border border-white/10 bg-white/[.03] p-8" data-testid="inbox-signed-out">
            <p className="text-mist">
              You are signed out. <a href="/account" className="text-acid underline-offset-4 hover:underline">Sign in</a> to see your contact requests and conversations.
            </p>
          </div>
        ) : (
          <InboxPanel />
        )}
      </section>
    </main>
  );
}
