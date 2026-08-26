"use client";

import { SignInButton } from "@clerk/nextjs";
import {
  Bot,
  BriefcaseBusiness,
  FileText,
  Inbox,
  Network,
  Radio,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { WORKSPACE_NAVIGATION } from "@/lib/navigation";

const workspaceIcons: Record<(typeof WORKSPACE_NAVIGATION)[number]["href"], LucideIcon> = {
  "/human": FileText,
  "/network": Network,
  "/inbox": Inbox,
  "/feed": Radio,
  "/applications": BriefcaseBusiness,
  "/employer": BriefcaseBusiness,
  "/agents": Bot,
  "/moderation": ShieldCheck,
};

export function WorkspaceHub() {
  const { configured, isLoaded, isSignedIn } = useConnectmdAuth();

  if (!configured) return <WorkspaceGate title="Workspace unavailable" body="This deployment has no signed-in workspace configured. Public discovery remains available without an account." />;
  if (!isLoaded) return <WorkspaceGate title="Checking your workspace" body="No private workspace data is loaded while your signed-in session is being checked." loading />;
  if (!isSignedIn) {
    return <main className="mx-auto max-w-5xl px-5 py-12 lg:px-8 lg:py-16"><section className="rounded-[1.8rem] border border-white/10 bg-panel p-6 shadow-glow sm:p-9"><Sparkles className="size-6 text-acid" aria-hidden /><p className="eyebrow mt-5">Private workspace</p><h1 className="mt-3 font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">One private place to continue your work.</h1><p className="mt-5 max-w-2xl text-base leading-7 text-mist">Sign in as a human to navigate your documents, network, inbox, workspaces, agents, and safety controls. Signing in does not send a request, publish a document, or replay an earlier action.</p><SignInButton mode="modal"><button type="button" className="mt-7 inline-flex min-h-12 items-center rounded-full bg-acid px-6 text-sm font-bold text-ink transition hover:bg-[#e5ff92] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Sign in to open your workspace</button></SignInButton><Link href="/discover" className="ml-4 inline-flex min-h-12 items-center text-sm font-semibold text-mist underline-offset-4 transition hover:text-white hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Explore public work</Link></section></main>;
  }

  return <main className="mx-auto max-w-7xl px-5 py-10 pb-16 lg:px-8 lg:py-14"><section className="max-w-3xl"><p className="eyebrow">Private workspace</p><h1 className="mt-3 font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">Continue with clear boundaries.</h1><p className="mt-4 text-base leading-7 text-mist">Choose a private workspace to continue. This page does not load account records, counts, or status; each destination checks your current access when you open it.</p></section><nav aria-label="Private workspace navigation" className="mt-9"><ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{WORKSPACE_NAVIGATION.map((item) => <li key={item.href}><WorkspaceCard {...item} /></li>)}</ul></nav><section className="mt-9 rounded-2xl border border-acid/20 bg-acid/[.05] p-5"><h2 className="inline-flex items-center gap-2 text-lg font-semibold text-white"><ShieldCheck className="size-5 text-acid" aria-hidden />Access remains destination-specific</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-mist">Being signed in does not establish employer, reviewer, moderator, agent, or recipient authority. A destination may show an unavailable or access-denied state instead of data when the server does not authorize that role.</p></section></main>;
}

function WorkspaceCard({ href, label, description }: (typeof WORKSPACE_NAVIGATION)[number]) {
  const Icon = workspaceIcons[href];
  return <Link href={href} className="group flex min-h-52 flex-col rounded-[1.4rem] border border-white/10 bg-panel p-5 transition hover:border-acid/35 hover:bg-white/[.045] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid"><Icon className="size-5 text-acid" aria-hidden /><h2 className="mt-7 text-xl font-semibold text-white transition group-hover:text-acid">{label}</h2><p className="mt-2 text-sm leading-6 text-mist">{description}</p><span className="mt-auto pt-5 text-sm font-semibold text-acid">Open {label}<span aria-hidden> →</span></span></Link>;
}

function WorkspaceGate({ title, body, loading = false }: { title: string; body: string; loading?: boolean }) {
  return <main className="mx-auto max-w-5xl px-5 py-12 lg:px-8 lg:py-16"><section className="rounded-[1.8rem] border border-white/10 bg-panel p-6 sm:p-9"><ShieldCheck className={`size-6 text-acid${loading ? " animate-pulse" : ""}`} aria-hidden /><h1 className="mt-5 font-display text-4xl font-semibold tracking-[-.05em] text-white">{title}</h1><AsyncBoundaryMessage className="mt-4 max-w-2xl text-base leading-7 text-mist" loading={loading}>{body}</AsyncBoundaryMessage><Link href="/discover" className="mt-6 inline-flex min-h-11 items-center text-sm font-semibold text-acid underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Explore public work</Link></section></main>;
}
