import {
  ArrowRight,
  Bot,
  Braces,
  BriefcaseBusiness,
  Building2,
  Clock3,
  Compass,
  FileCode2,
  FileText,
  LockKeyhole,
  Network,
  Search,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";
import Link from "next/link";
import React from "react";

import { NetworkNotice } from "@/components/network-notice";
import { PublicNetworkEarlyState } from "@/components/public-network-empty-state";
import type { PublicAgentIdentityDirectory } from "@/lib/agent-identity-api";
import { publicApiMarkdownUrl, publicProtocolUrl } from "@/lib/api";
import type { PublicPostInventoryPage, PublicPostSummary } from "@/lib/posts-api";
import type { DirectoryHit, DirectorySearchResponse } from "@/lib/public-search-api";
import { safeJsonLd } from "@/lib/public-document";
import { publicDiscoveryJsonLd } from "@/lib/public-projections";
import { hasActiveRecruitingControl, type CursorPage, type Job, type Organization } from "@/lib/recruitment-api";

const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid";
const discoverySources = ["documents", "agents", "organizations", "jobs", "posts"] as const;

export type DiscoverySource = (typeof discoverySources)[number];

export function DiscoverHub({
  profiles,
  agents,
  privateWorkspacesEnabled,
  recruitingEnabled,
  organizations,
  jobs,
  posts,
  unavailableSources,
}: {
  profiles: DirectorySearchResponse | null;
  agents: PublicAgentIdentityDirectory | null;
  privateWorkspacesEnabled: boolean;
  recruitingEnabled: boolean;
  organizations: CursorPage<Organization> | null;
  jobs: CursorPage<Job> | null;
  posts: PublicPostInventoryPage | null;
  unavailableSources: readonly DiscoverySource[];
}) {
  const verifiedOrganizations = recruitingEnabled
    ? organizations?.items.filter(hasActiveRecruitingControl) ?? []
    : [];
  const publishedJobs = recruitingEnabled
    ? jobs?.items.filter((job) => job.status === "published") ?? []
    : [];
  const visibleUnavailableSources = unavailableSources.filter(
    (source) => recruitingEnabled || (source !== "organizations" && source !== "jobs"),
  );
  const recruitingInventoryIsEmpty = !recruitingEnabled || (
    organizations !== null && verifiedOrganizations.length === 0 && organizations.nextCursor === null &&
    jobs !== null && publishedJobs.length === 0 && jobs.nextCursor === null
  );
  const publicNetworkIsEmpty =
    profiles !== null && profiles.indexingAvailable && profiles.total === 0 && profiles.hits.length === 0 &&
    agents !== null && agents.identities.length === 0 && agents.nextCursor === null &&
    recruitingInventoryIsEmpty &&
    posts !== null && posts.items.length === 0 && posts.nextCursor === null;

  return (
    <main className="overflow-hidden pb-16">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: safeJsonLd(
            publicDiscoveryJsonLd({
              profiles,
              agents,
              organizations: verifiedOrganizations,
              jobs: publishedJobs,
              posts: posts?.items ?? [],
            }),
          ),
        }}
      />

      <section className="relative isolate overflow-hidden border-b border-white/10 bg-black/10">
        <div className="pointer-events-none absolute inset-0 -z-10 bg-grid bg-[size:54px_54px] opacity-25 [mask-image:radial-gradient(circle_at_26%_16%,black,transparent_66%)]" aria-hidden />
        <div className="pointer-events-none absolute -left-32 top-0 -z-10 size-[34rem] rounded-full bg-acid/[.09] blur-3xl" aria-hidden />
        <div className="pointer-events-none absolute right-0 top-28 -z-10 size-[25rem] rounded-full bg-sky-400/[.06] blur-3xl" aria-hidden />
        <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8 lg:py-16">
          <p className="eyebrow">Public HTML mirror</p>
          <h1 className="mt-4 max-w-5xl font-display text-5xl font-semibold leading-[.94] tracking-[-.06em] text-white sm:text-7xl">
            One public network, projected for browsers and agents.
          </h1>
          <p className="mt-5 max-w-3xl text-lg leading-8 text-mist">
            This server-rendered hub mirrors public connect.md records into crawlable HTML. Profiles, resumes, and professional posts still come from canonical Markdown; the HTML, metadata, search projection, and direct <code className="font-mono text-white">.md</code> representations do not create another source of truth.
          </p>

          <ol aria-label="How public discovery becomes a private professional path" className="mt-8 grid gap-3 lg:grid-cols-3">
            <OrientationStep number="01" icon={Compass} title="Discover public records" description="Browse only the professional information people chose to publish." />
            <OrientationStep number="02" icon={FileText} title="Inspect canonical Markdown" description="Open the same published record and source representation available to agents." />
            <OrientationStep
              number="03"
              icon={LockKeyhole}
              title="Choose a private path"
              description={privateWorkspacesEnabled
                ? "Sign in to follow or request a connection. Relationships and conversations stay out of public discovery."
                : "Private workspaces are unavailable in this deployment. Public discovery never creates a relationship or conversation."}
            />
          </ol>

          <form action="/search" method="get" role="search" className="mt-7 flex max-w-2xl flex-col gap-3 rounded-2xl border border-white/10 bg-panel/95 p-3 shadow-glow sm:flex-row">
            <label className="sr-only" htmlFor="discover-query">Search public profiles and resumes</label>
            <input
              id="discover-query"
              name="q"
              className="min-h-11 min-w-0 flex-1 rounded-xl border border-white/12 bg-black/25 px-3 text-sm text-white outline-none placeholder:text-mist/45 focus:border-acid/70 focus:ring-2 focus:ring-acid/15"
              placeholder="Search public profiles, resumes, skills, or locations"
            />
            <button type="submit" className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:bg-[#e5ff92] ${focusRing}`}>
              <Search className="size-4" aria-hidden />
              Search
            </button>
          </form>
          <div className="mt-5 flex flex-wrap gap-x-5 gap-y-3 text-sm">
            <PublicRailLink href="/search">Public profile archives</PublicRailLink>
            {privateWorkspacesEnabled && <PublicRailLink href="/feed">Private chronological feed</PublicRailLink>}
            <PublicRailLink href="/representatives">Representative declarations</PublicRailLink>
            <PublicRailLink href="/agent-directory">Agent Directory</PublicRailLink>
            {recruitingEnabled && <PublicRailLink href="/organizations">Organizations</PublicRailLink>}
            {recruitingEnabled && <PublicRailLink href="/jobs">Published jobs</PublicRailLink>}
            <PublicRailLink href="/discover#latest-posts">Latest public posts</PublicRailLink>
          </div>
          <NetworkNotice label="Public discovery" />
        </div>
      </section>

      {publicNetworkIsEmpty && (
        <section className="mx-auto max-w-7xl px-5 pt-7 lg:px-8" aria-label="Early public network onboarding">
          <PublicNetworkEarlyState detail={recruitingEnabled
            ? "No public profiles, posts, Agent Identities, service-gated organizations, or published jobs are available in these directories yet."
            : "No public profiles, posts, or Agent Identities are available in these directories yet."} />
        </section>
      )}

      {visibleUnavailableSources.length > 0 && (
        <section className="mx-auto max-w-7xl px-5 pt-7 lg:px-8">
          <div role="alert" className="rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-5">
            <h2 className="font-semibold text-amber-50">Unavailable now: {visibleUnavailableSources.map(discoverySourceLabel).join(", ")}</h2>
            <p className="mt-2 text-sm leading-6 text-amber-100/85">Other successful public directories remain available below. You can also retry the affected directory directly.</p>
          </div>
        </section>
      )}

      <section aria-label="Public discovery and private workspace paths" className="mx-auto grid max-w-7xl gap-5 px-5 py-9 md:grid-cols-2 xl:grid-cols-5 lg:px-8">
        <DiscoveryCard icon={Compass} title="Profiles and post archives" description="Published canonical documents with optional immutable professional-post archives." href="/search" action="Search public profiles" />
        <PrivatePathCard privateWorkspacesEnabled={privateWorkspacesEnabled} icon={Clock3} title="Private chronology" description="A signed-in human can pull their own and followed profiles' posts without ranking or a public graph." href="/feed" action="Open private feed" />
        <DiscoveryCard icon={UserRoundCheck} title="Representation" description="Public representation declarations are owner-attested, not independently verified." href="/representatives" action="Explore declarations" />
        <DiscoveryCard icon={Bot} title="Agent identities" description="Public labels linked to profiles with only an internal mediated-contact capability." href="/agent-directory" action="Browse identities" />
        <PrivatePathCard privateWorkspacesEnabled={privateWorkspacesEnabled} icon={Network} title="Private connection" description="Connection requests and conversations are human-only and stay out of public discovery." href="/network" action="Open private network" />
      </section>

      <section className="mx-auto max-w-7xl px-5 py-7 lg:px-8">
        <div className="rounded-[1.6rem] border border-acid/20 bg-acid/[.045] p-5 sm:p-7">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(22rem,.75fr)] lg:items-end">
            <div>
              <p className="eyebrow">Agent protocol rails</p>
              <h2 className="mt-2 max-w-3xl text-2xl font-semibold text-white">Discover through HTML. Read and act through explicit contracts.</h2>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-mist">The public hub supplies stable links and structured HTML. Agents that reach connect.md can then choose the concise site map, full integration guide, OpenAPI contract, Markdown representation, MCP tools, or A2A card. Discovery never grants authority or sends a message.</p>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <ProtocolLink href={publicProtocolUrl("/llms.txt") ?? "/llms.txt"} icon={Braces} label="llms.txt" detail="Concise agent map" />
              <ProtocolLink href={publicProtocolUrl("/llms-full.txt") ?? "/llms-full.txt"} icon={FileCode2} label="llms-full.txt" detail="Complete safe-use guide" />
              <ProtocolLink href={publicProtocolUrl("/openapi.json") ?? "/openapi.json"} icon={FileCode2} label="OpenAPI" detail="HTTP contract" />
              <ProtocolLink href={publicProtocolUrl("/.well-known/agent-card.json") ?? "/.well-known/agent-card.json"} icon={Bot} label="A2A card" detail="Bounded platform skills" />
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-5 py-7 lg:px-8">
        <SectionTitle eyebrow="Published documents" title="Recent public profiles and resumes" href="/search" action="Open search" />
        {profiles && profiles.indexingAvailable ? (
          profiles.hits.length === 0 ? profiles.total === 0 && profiles.nextCursor === null
            ? <Empty title="No public documents are available yet" body="Owner-published profiles and resumes will appear here without implying that a search failed." />
            : <Empty title="No public documents appear in this discovery window" body="The public search may contain additional profiles or resumes; open it to continue." />
            : <ol className="mt-5 grid gap-4 md:grid-cols-2">{profiles.hits.slice(0, 4).map((hit) => <li key={hit.id}><DocumentCard hit={hit} /></li>)}</ol>
        ) : <Unavailable source="documents" />}
      </section>

      <section id="latest-posts" className="mx-auto max-w-7xl scroll-mt-24 px-5 py-7 lg:px-8">
        <SectionTitle eyebrow="Public post chronology" title="Latest professional posts" href="/discover#latest-posts" action="Newest first" />
        {posts ? (
          posts.items.length === 0 ? posts.nextCursor === null
            ? <Empty title="No public posts are available yet" body="Published posts will appear here in strict publication order without ranking." />
            : <Empty title="No public posts appear in this discovery window" body="Additional public posts may exist beyond this window; continue through the public chronology." />
            : <ol className="mt-5 grid gap-4 md:grid-cols-2">{posts.items.map((post) => <li key={post.id}><PostSummaryCard post={post} /></li>)}</ol>
        ) : <Unavailable source="posts" />}
      </section>

      <section className="mx-auto max-w-7xl px-5 py-7 lg:px-8">
        <SectionTitle eyebrow="Published Agent Identities" title="Agents representing public profiles" href="/agent-directory" action="Open directory" />
        {agents ? (
          agents.identities.length === 0 ? agents.nextCursor === null
            ? <Empty title="No public Agent Identities are available yet" body="An absent listing does not describe a person's private tools, authority, or availability." />
            : <Empty title="No public Agent Identities appear in this discovery window" body="Additional public identities may exist beyond this window; open the directory to continue." />
            : <ol className="mt-5 grid gap-4 md:grid-cols-2">{agents.identities.slice(0, 4).map((identity) => <li key={identity.handle}><Link href={`/agents/${encodeURIComponent(identity.handle)}`} className={`block rounded-2xl border border-white/10 bg-panel p-5 transition hover:border-acid/30 ${focusRing}`}><Bot className="size-5 text-acid" aria-hidden /><h3 className="mt-4 break-anywhere text-xl font-semibold text-white">{identity.displayName}</h3><p className="mt-1 break-anywhere font-mono text-xs text-mist">@{identity.handle}</p><p className="mt-3 break-anywhere line-clamp-2 text-sm leading-6 text-mist">{identity.description}</p><p className="mt-4 text-xs leading-5 text-mist/75">Owner-attested identity with internal mediated contact only.</p></Link></li>)}</ol>
        ) : <Unavailable source="agents" />}
      </section>

      {recruitingEnabled && (
        <section className="mx-auto max-w-7xl px-5 py-7 lg:px-8">
          <SectionTitle eyebrow="Service-gated organizations" title="Public organization records" href="/organizations" action="Browse organizations" />
          {organizations ? (
            verifiedOrganizations.length === 0 ? organizations.nextCursor === null
              ? <Empty title="No service-gated organizations are available yet" body="Public browsing is available for organizations with current active recruiting verification; unverified organizations stay omitted." />
              : <Empty title="No service-gated organizations appear in this discovery window" body="Additional organization records may exist beyond this window; open the directory to continue." />
              : <ol className="mt-5 grid gap-4 md:grid-cols-2">{verifiedOrganizations.slice(0, 4).map((organization) => <li key={organization.id}><Link href={`/organizations/${encodeURIComponent(organization.slug)}`} className={`block rounded-2xl border border-white/10 bg-panel p-5 transition hover:border-acid/30 ${focusRing}`}><Building2 className="size-5 text-acid" aria-hidden /><h3 className="mt-4 break-anywhere text-xl font-semibold text-white">{organization.name}</h3>{organization.description && <p className="mt-2 break-anywhere line-clamp-2 text-sm leading-6 text-mist">{organization.description}</p>}<p className="mt-4 text-xs leading-5 text-mist/75">Current active recruiting verification enables public browsing; it is not an endorsement.</p></Link></li>)}</ol>
          ) : <Unavailable source="organizations" />}
        </section>
      )}

      {recruitingEnabled && (
        <section className="mx-auto max-w-7xl px-5 py-7 lg:px-8">
          <SectionTitle eyebrow="Published roles" title="Opportunities from the service gate" href="/jobs" action="Browse jobs" />
          {jobs ? (
            publishedJobs.length === 0 ? jobs.nextCursor === null
              ? <Empty title="No published jobs are available yet" body="Public browsing is available for published jobs with current active recruiting verification; this is an empty inventory state, not a failed search." />
              : <Empty title="No published jobs appear in this discovery window" body="Additional published roles may exist beyond this window; open the jobs directory to continue." />
              : <ol className="mt-5 grid gap-4 md:grid-cols-2">{publishedJobs.slice(0, 4).map((job) => <li key={job.id}><Link href={`/jobs/${encodeURIComponent(job.organizationSlug)}/${encodeURIComponent(job.slug)}`} className={`block rounded-2xl border border-white/10 bg-panel p-5 transition hover:border-acid/30 ${focusRing}`}><BriefcaseBusiness className="size-5 text-acid" aria-hidden /><p className="mt-3 break-anywhere text-xs font-semibold uppercase tracking-wide text-acid">{job.organizationName}</p><h3 className="mt-2 break-anywhere text-xl font-semibold text-white">{job.title}</h3><p className="mt-2 break-anywhere text-sm text-mist">{[job.location, job.workMode, job.employmentType?.replaceAll("_", " ")].filter(Boolean).join(" · ")}</p></Link></li>)}</ol>
          ) : <Unavailable source="jobs" />}
        </section>
      )}

      <section className="mx-auto max-w-7xl px-5 pt-7 lg:px-8">
        <div className="rounded-2xl border border-white/10 bg-panel p-5 sm:flex sm:items-center sm:justify-between sm:gap-6">
          <div>
            <h2 className="text-lg font-semibold text-white">Found a public profile?</h2>
            <p className="mt-1 text-sm leading-6 text-mist">{privateWorkspacesEnabled
              ? "A signed-in human can privately follow a profile for a chronological feed or request a private connection. Neither creates a public relationship or count."
              : "Private follows and connections are unavailable in this deployment. Public discovery never creates a relationship or count."}</p>
          </div>
          {privateWorkspacesEnabled && <Link href="/feed" className={`mt-4 inline-flex min-h-11 items-center justify-center rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:bg-[#e5ff92] sm:mt-0 ${focusRing}`}>Private feed</Link>}
        </div>
      </section>
    </main>
  );
}

function OrientationStep({ number, icon: Icon, title, description }: { number: string; icon: typeof Compass; title: string; description: string }) {
  return (
    <li className="relative overflow-hidden rounded-2xl border border-white/10 bg-black/25 p-5 shadow-[0_20px_60px_rgba(0,0,0,.14)]">
      <span className="font-mono text-xs font-semibold tracking-[.18em] text-acid/80">{number}</span>
      <Icon className="mt-6 size-5 text-acid" aria-hidden />
      <h2 className="mt-4 text-xl font-semibold text-white">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-mist">{description}</p>
    </li>
  );
}

function PublicRailLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className={`inline-flex min-h-11 items-center font-semibold text-acid underline-offset-4 transition hover:text-white hover:underline ${focusRing}`}>{children}</Link>;
}

function DiscoveryCard({ icon: Icon, title, description, href, action }: { icon: typeof Compass; title: string; description: string; href: string; action: string }) {
  return <article className="rounded-2xl border border-white/10 bg-panel/95 p-5 shadow-[0_20px_60px_rgba(0,0,0,.1)]"><Icon className="size-5 text-acid" aria-hidden /><h2 className="mt-4 text-xl font-semibold text-white">{title}</h2><p className="mt-2 text-sm leading-6 text-mist">{description}</p><Link href={href} className={`mt-5 inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-acid underline-offset-4 transition hover:text-white hover:underline ${focusRing}`}>{action}<ArrowRight className="size-3.5" aria-hidden /></Link></article>;
}

function PrivatePathCard({
  privateWorkspacesEnabled,
  icon: Icon,
  title,
  description,
  href,
  action,
}: {
  privateWorkspacesEnabled: boolean;
  icon: typeof Compass;
  title: string;
  description: string;
  href: string;
  action: string;
}) {
  if (privateWorkspacesEnabled) {
    return <DiscoveryCard icon={Icon} title={title} description={description} href={href} action={action} />;
  }

  return (
    <article className="rounded-2xl border border-white/10 bg-panel/95 p-5 shadow-[0_20px_60px_rgba(0,0,0,.1)]">
      <Icon className="size-5 text-mist" aria-hidden />
      <h2 className="mt-4 text-xl font-semibold text-white">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-mist">{description}</p>
      <p className="mt-5 text-sm font-semibold leading-6 text-mist">Private workspaces are unavailable in this deployment.</p>
    </article>
  );
}

function ProtocolLink({ href, icon: Icon, label, detail }: { href: string; icon: typeof Bot; label: string; detail: string }) {
  return <a href={href} className={`flex min-h-14 items-center gap-3 rounded-xl border border-white/10 bg-black/15 px-4 transition hover:border-acid/30 hover:bg-white/[.035] ${focusRing}`}><Icon className="size-4 shrink-0 text-acid" aria-hidden /><span><span className="block font-mono text-xs font-semibold text-white">{label}</span><span className="mt-0.5 block text-xs text-mist">{detail}</span></span></a>;
}

function SectionTitle({ eyebrow, title, href, action }: { eyebrow: string; title: string; href: string; action: string }) {
  return <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow">{eyebrow}</p><h2 className="mt-2 text-2xl font-semibold text-white">{title}</h2></div><Link href={href} className={`inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white transition hover:border-acid/40 hover:text-acid ${focusRing}`}>{action}</Link></div>;
}

function DocumentCard({ hit }: { hit: DirectoryHit }) {
  const markdownHref = publicApiMarkdownUrl(hit.markdownUrl);
  return (
    <article className="rounded-2xl border border-white/10 bg-panel/95 p-5 transition hover:border-acid/30">
      <p className="text-xs font-semibold uppercase tracking-wide text-acid">Public {hit.kind}</p>
      <h3 className="mt-3 break-anywhere text-xl font-semibold text-white"><Link href={hit.htmlUrl} className={`inline-flex min-h-11 items-center underline-offset-4 transition hover:text-acid hover:underline ${focusRing}`}>{hit.name}</Link></h3>
      {(hit.title || hit.headline) && <p className="mt-2 break-anywhere line-clamp-2 text-sm leading-6 text-mist">{hit.title ?? hit.headline}</p>}
      {hit.kind === "profile" && <p className="mt-4 text-xs leading-5 text-mist/75">Open the profile to view any published professional-post archive.</p>}
      <div className="mt-5 flex flex-wrap gap-3">
        <Link href={hit.htmlUrl} className={`inline-flex min-h-11 items-center gap-2 rounded-full bg-acid px-4 text-xs font-bold text-ink transition hover:bg-[#e5ff92] ${focusRing}`}>Open record<ArrowRight className="size-3.5" aria-hidden /></Link>
        {markdownHref && <a href={markdownHref} type="text/markdown" className={`inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 text-xs font-semibold text-white transition hover:border-acid/40 hover:text-acid ${focusRing}`}><Braces className="size-3.5 text-acid" aria-hidden />Canonical Markdown</a>}
      </div>
    </article>
  );
}

function PostSummaryCard({ post }: { post: PublicPostSummary }) {
  const markdownHref = publicApiMarkdownUrl(post.markdownUrl);
  return (
    <article className="rounded-2xl border border-white/10 bg-panel/95 p-5 transition hover:border-acid/30">
      <p className="text-xs font-semibold uppercase tracking-wide text-acid">Professional post · newest first</p>
      <h3 className="mt-3 break-anywhere text-xl font-semibold text-white"><Link href={post.htmlUrl} className={`inline-flex min-h-11 items-center underline-offset-4 transition hover:text-acid hover:underline ${focusRing}`}>{post.title}</Link></h3>
      <p className="mt-2 break-anywhere text-sm text-mist"><Link href={`/p/${encodeURIComponent(post.authorProfileHandle)}`} className={`inline-flex min-h-11 items-center font-semibold text-acid underline-offset-4 hover:underline ${focusRing}`}>@{post.authorProfileHandle}</Link> · <time dateTime={post.publishedAt}>{formatPublicDate(post.publishedAt)}</time></p>
      <ul className="mt-4 flex flex-wrap gap-2" aria-label="Post topics">{post.topics.map((topic) => <li key={topic} className="rounded-full border border-acid/20 bg-acid/[.07] px-3 py-1 text-xs text-acid">{topic}</li>)}</ul>
      <div className="mt-5 flex flex-wrap gap-3">
        <Link href={post.htmlUrl} className={`inline-flex min-h-11 items-center gap-2 rounded-full bg-acid px-4 text-xs font-bold text-ink transition hover:bg-[#e5ff92] ${focusRing}`}>Open post<ArrowRight className="size-3.5" aria-hidden /></Link>
        {markdownHref && <a href={markdownHref} type="text/markdown" className={`inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 text-xs font-semibold text-white transition hover:border-acid/40 hover:text-acid ${focusRing}`}><Braces className="size-3.5 text-acid" aria-hidden />Canonical Markdown</a>}
      </div>
    </article>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return <div className="mt-5 rounded-2xl border border-dashed border-white/15 p-7 text-center"><ShieldCheck className="mx-auto size-5 text-acid" aria-hidden /><h3 className="mt-3 font-semibold text-white">{title}</h3><p className="mt-1 text-sm leading-6 text-mist">{body}</p></div>;
}

function Unavailable({ source }: { source: DiscoverySource }) {
  return <div role="status" className="mt-5 rounded-2xl border border-amber-300/25 bg-amber-300/[.08] p-5 text-sm leading-6 text-amber-100"><strong>{discoverySourceLabel(source)}</strong> are temporarily unavailable. Use their directory link above to retry.</div>;
}

function discoverySourceLabel(source: DiscoverySource) {
  if (source === "documents") return "Published documents";
  if (source === "agents") return "Agent identities";
  if (source === "organizations") return "Public organizations";
  if (source === "jobs") return "Published roles";
  return "Public posts";
}

function formatPublicDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}
