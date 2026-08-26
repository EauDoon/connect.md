import type { Metadata } from "next";
import Link from "next/link";

import { OutreachInbox } from "@/components/outreach-inbox";
import { parseInboxContactProfileIntent } from "@/lib/auth-return-intent";
import { serverSearchParams, type ServerSearchParams } from "@/lib/server-search-params";

export const metadata: Metadata = { title: "Contact inbox", robots: { index: false, follow: false } };

export default async function InboxPage({ searchParams }: { searchParams: Promise<ServerSearchParams> }) {
  const prefillProfileHandle = parseInboxContactProfileIntent(serverSearchParams(await searchParams));

  return <main className="mx-auto max-w-7xl px-5 py-10 lg:px-8 lg:py-14"><section className="mb-8 max-w-3xl"><p className="eyebrow">Private outreach</p><h1 className="mt-3 font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">A deliberate door—not an open inbox.</h1><p className="mt-4 text-base leading-7 text-mist">Set the contact policy once. Human and agent requests enter one private, controllable queue.</p><Link href="/network" className="mt-5 inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white transition hover:border-acid/40">Private human network</Link></section><OutreachInbox prefillProfileHandle={prefillProfileHandle} /></main>;
}
