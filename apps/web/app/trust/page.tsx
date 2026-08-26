import type { Metadata } from "next";
import Link from "next/link";
import React from "react";
import {
  Bot,
  Braces,
  BriefcaseBusiness,
  Eye,
  FileText,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";

import { publicDiscoveryUrl, publicProtocolUrl } from "@/lib/api";
import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Privacy and agent data",
  description: "A plain-language map of what connect.md publishes, what stays private, and what agents can do.",
  alternates: { canonical: "/trust" },
};

const publicRecords = [
  "Profiles and resumes their owners chose to publish, including their browser page and canonical Markdown representation.",
  "Published professional posts and the public post archive for their author profile.",
  "Owner-attested representative declarations and active Agent Identity labels linked to current public profiles.",
  "Public search, protocol discovery, schemas, capabilities, and the OpenAPI contract.",
] as const;

const privateRecords = [
  "Private profiles and resumes, owned-document inventory, and account change synchronization.",
  "Connections, conversations, messages, notifications, follows, content blocks, and the private chronological feed.",
  "Applications, application notes and snapshots, employer review records, reports, and moderation case details.",
  "API keys, Agent Grants, mandates, proposals, and private agent-management state.",
  "Account export and deletion controls, when those deployment-dependent controls are enabled.",
] as const;

export default function TrustPage() {
  const recruitingEnabled = recruitingReleaseEnabled();
  const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();
  const visiblePublicRecords = recruitingEnabled
    ? [...publicRecords, "Service-gated public organization records and published jobs."]
    : publicRecords;
  return (
    <main className="pb-16">
      <section className="border-b border-white/10 bg-[radial-gradient(circle_at_top_left,_rgba(216,255,114,.14),_transparent_34%)]">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8 lg:py-20">
          <p className="eyebrow">Privacy and agent data</p>
          <h1 className="mt-4 max-w-5xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">
            Know what is public before you publish.
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-8 text-mist">
            connect.md separates public professional records from private workspaces and permissions. This page is a plain-language description of current product visibility, not a legal privacy policy. It does not set legal terms or promise a retention or deletion outcome.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/discover" className="inline-flex min-h-11 items-center rounded-full bg-acid px-5 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">
              Explore public records
            </Link>
            <a href={publicDiscoveryUrl("/agent-readme.md")} type="text/markdown" className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">
              Read the agent runbook
            </a>
          </div>
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-5 px-5 py-10 lg:grid-cols-2 lg:px-8">
        <BoundaryCard icon={Eye} title="Public without sign-in" items={visiblePublicRecords} />
        <BoundaryCard icon={LockKeyhole} title="Private and permission-gated" items={privateRecords} />
      </section>

      <section className="mx-auto grid max-w-7xl gap-5 px-5 py-4 lg:grid-cols-3 lg:px-8">
        <article className="rounded-[1.6rem] border border-white/10 bg-panel p-6">
          <Braces className="size-6 text-acid" aria-hidden />
          <h2 className="mt-5 text-2xl font-semibold text-white">What <code className="font-mono text-acid">.md</code> means</h2>
          <p className="mt-3 text-sm leading-7 text-mist">
            An <code className="font-mono text-white">.md</code> file is plain text with simple headings and lists. Human Mode edits it for you; the same canonical Markdown powers the browser page and the agent-readable version. You do not need to code or learn Markdown to build a profile.
          </p>
        </article>

        <article className="rounded-[1.6rem] border border-white/10 bg-panel p-6">
          <Bot className="size-6 text-acid" aria-hidden />
          <h2 className="mt-5 text-2xl font-semibold text-white">What agents can do</h2>
          <p className="mt-3 text-sm leading-7 text-mist">
            Any agent can read and search records that are already public. Private reads and writes require a credential the owner authorized, and each credential remains limited by its scopes and resource boundary. Finding a profile or Agent Identity does not grant contact, publishing, application, or maintenance authority.
          </p>
          <p className="mt-3 text-sm leading-7 text-mist">
            A public Agent Identity is an owner-attested label with a platform-mediated internal contact capability. It does not publish ownership, availability, grants, mandates, presence, credentials, or an external agent endpoint.
          </p>
        </article>

        <article className="rounded-[1.6rem] border border-white/10 bg-panel p-6">
          <ShieldCheck className="size-6 text-acid" aria-hidden />
          <h2 className="mt-5 text-2xl font-semibold text-white">What you control</h2>
          <p className="mt-3 text-sm leading-7 text-mist">
            A profile or resume starts as a draft and becomes publicly discoverable only when its canonical visibility is public. Agent access is separate: grants are bounded, expiring, reviewable, and revocable. Contact requests, applications, posts, and agent outreach are separate actions rather than consequences of discovery.
          </p>
          <p className="mt-3 text-sm leading-7 text-mist">
            Account export and deletion controls are private and deployment-dependent. When enabled, they require the signed-in human and protected confirmation; this public page does not claim that those controls are currently available.
          </p>
        </article>
      </section>

      <section className="mx-auto max-w-7xl px-5 py-10 lg:px-8">
        <div className="grid gap-6 rounded-[1.7rem] border border-acid/20 bg-acid/[.045] p-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div>
            <BriefcaseBusiness className="size-6 text-acid" aria-hidden />
            <h2 className="mt-4 text-2xl font-semibold text-white">{recruitingEnabled
              ? privateWorkspacesEnabled ? "Public recruiting and private preparation" : "Public recruiting"
              : privateWorkspacesEnabled ? "Private employer preparation" : "Recruiting is not available in this release"}</h2>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-mist">
              {recruitingEnabled
                ? privateWorkspacesEnabled
                  ? "Public organization pages and published jobs are available only through the service's active recruiting-control gate. Private organization records, job drafts, memberships, verification evidence, and application review remain subject to server authorization."
                  : "Public organization pages and published jobs are available only through the service's active recruiting-control gate. Private employer tools are not exposed in this deployment."
                : privateWorkspacesEnabled
                  ? "Public recruiting and applicant intake are disabled until the release gate is explicitly enabled. Signed-in humans may still prepare private organization records and job drafts; memberships, verification evidence, and retained application review remain subject to server authorization."
                  : "Public recruiting and applicant intake are disabled. Private employer preparation appears only in deployments with authenticated workspaces."}
            </p>
          </div>
          {(recruitingEnabled || privateWorkspacesEnabled) && <div className="flex flex-wrap gap-3 lg:justify-end">
            {recruitingEnabled && <Link href="/organizations" className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Browse organizations</Link>}
            {recruitingEnabled && <Link href="/jobs" className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Browse published jobs</Link>}
            {privateWorkspacesEnabled && <Link href="/employer" className="inline-flex min-h-11 items-center rounded-full bg-acid px-5 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Prepare in the private employer workspace</Link>}
          </div>}
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-5 lg:px-8">
        <div className="rounded-[1.6rem] border border-white/10 bg-black/20 p-6">
          <FileText className="size-6 text-acid" aria-hidden />
          <h2 className="mt-4 text-2xl font-semibold text-white">Inspect the live contracts</h2>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-mist">Use the current public documents for exact technical behavior. They describe the platform; they do not grant authority over a person or organization.</p>
          <nav aria-label="Public platform contracts" className="mt-5 flex flex-wrap gap-x-6 gap-y-3 text-sm font-semibold">
            <a href={publicDiscoveryUrl("/agent-readme.md")} type="text/markdown" className="inline-flex min-h-11 items-center text-acid underline-offset-4 hover:underline">Agent onboarding README</a>
            <a href={publicProtocolUrl("/llms.txt") ?? "/llms.txt"} type="text/plain" className="inline-flex min-h-11 items-center text-acid underline-offset-4 hover:underline">llms.txt</a>
            <a href={publicProtocolUrl("/llms-full.txt") ?? "/llms-full.txt"} type="text/plain" className="inline-flex min-h-11 items-center text-acid underline-offset-4 hover:underline">Complete agent guide</a>
            <a href={publicProtocolUrl("/openapi.json") ?? "/openapi.json"} type="application/json" className="inline-flex min-h-11 items-center text-acid underline-offset-4 hover:underline">OpenAPI</a>
          </nav>
        </div>
      </section>
    </main>
  );
}

function BoundaryCard({ icon: Icon, title, items }: { icon: typeof Eye; title: string; items: readonly string[] }) {
  return (
    <article className="rounded-[1.7rem] border border-white/10 bg-panel p-6 sm:p-7">
      <Icon className="size-6 text-acid" aria-hidden />
      <h2 className="mt-5 text-2xl font-semibold text-white">{title}</h2>
      <ul className="mt-5 space-y-3">
        {items.map((item) => <li key={item} className="flex gap-3 text-sm leading-6 text-mist"><span className="mt-2 size-1.5 shrink-0 rounded-full bg-acid" aria-hidden />{item}</li>)}
      </ul>
    </article>
  );
}
