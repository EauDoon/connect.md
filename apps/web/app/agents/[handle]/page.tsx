import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { cache } from "react";

import { ApiRequestError } from "@/lib/api";
import { fetchPublicAgentIdentity } from "@/lib/agent-identity-api";
import { buildInboxContactReturnPath } from "@/lib/auth-return-intent";
import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";
import { safeJsonLd } from "@/lib/public-document";
import { agentIdentityJsonLd } from "@/lib/public-projections";

export const dynamic = "force-dynamic";
const getAgentIdentity = cache(fetchPublicAgentIdentity);

export async function generateMetadata({ params }: { params: Promise<{ handle: string }> }): Promise<Metadata> {
  const { handle } = await params;
  try {
    const identity = await getAgentIdentity(handle);
    const path = `/agents/${encodeURIComponent(identity.handle)}`;
    return {
      title: `${identity.displayName} | Agent Identity`,
      description: identity.description,
      alternates: { canonical: path },
      openGraph: { title: `${identity.displayName} | Agent Identity`, description: identity.description, url: path }
    };
  } catch (error) {
    if (error instanceof ApiRequestError && error.code === "not_found") return { title: "Agent Identity" };
    throw error;
  }
}

export default async function PublicAgentIdentityPage({ params }: { params: Promise<{ handle: string }> }) {
  const { handle } = await params;
  let identity;
  try { identity = await getAgentIdentity(handle); }
  catch (error) {
    if (error instanceof ApiRequestError && error.code === "not_found") notFound();
    throw error;
  }
  const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();
  const contactIntent = privateWorkspacesEnabled ? buildInboxContactReturnPath(identity.profileHandle) : null;
  return <main className="mx-auto max-w-4xl px-5 py-12 lg:px-8 lg:py-16"><script type="application/ld+json" dangerouslySetInnerHTML={{ __html: safeJsonLd(agentIdentityJsonLd(identity)) }} /><section className="rounded-[1.8rem] border border-white/10 bg-panel p-6 sm:p-9"><p className="eyebrow">Public Agent Identity</p><div className="mt-4 flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><h1 className="font-display break-anywhere text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">{identity.displayName}</h1><p className="mt-3 break-anywhere text-lg text-mist">@{identity.handle}</p></div><span className="rounded-full border border-acid/25 bg-acid/[.07] px-3 py-1.5 text-xs font-semibold text-acid">Owner-attested</span></div><p className="mt-7 max-w-3xl break-anywhere whitespace-pre-wrap text-base leading-7 text-mist">{identity.description}</p><div className="mt-8 rounded-2xl border border-white/10 bg-black/15 p-5"><p className="text-sm font-semibold text-white">Linked public profile</p><Link href={`/p/${encodeURIComponent(identity.profileHandle)}`} className="mt-2 inline-flex min-h-11 items-center break-anywhere rounded-md px-2 text-sm font-semibold text-acid underline-offset-4 hover:underline">View @{identity.profileHandle}</Link>{contactIntent && <Link href={contactIntent} className="mt-4 inline-flex min-h-11 items-center rounded-full border border-acid/30 px-4 text-sm font-semibold text-acid transition hover:bg-acid/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Prepare a private contact request</Link>}{privateWorkspacesEnabled ? <p className="mt-3 text-xs leading-5 text-mist/75">This opens a private form prefilled only with the linked public profile. It does not send a request or prove a live mandate.</p> : <p className="mt-3 text-xs leading-5 text-mist/75">Private contact controls are unavailable in this deployment.</p>}</div><div className="mt-5 rounded-2xl border border-white/10 bg-black/15 p-5"><p className="text-sm font-semibold text-white">Scope and limits</p><p className="mt-2 text-sm leading-6 text-mist">This is an owner-attested public identity, not a credential, independent verification, employment status, or evidence of a live mandate. It may be used only for consent-gated internal outreach. This page does not publish an external agent endpoint or trigger any external fetch.</p></div></section></main>;
}
