import { ArrowLeft, BadgeCheck, Bot, Braces, BriefcaseBusiness, CalendarClock, ExternalLink, MapPin, Network, ShieldCheck, UserRoundCheck } from "lucide-react";
import Link from "next/link";

import { MarkdownPreview } from "@/components/markdown-preview";
import { ProfileConnectControl } from "@/components/profile-connect-control";
import { ProfilePostControls } from "@/components/profile-post-controls";
import { agentDirectoryHref } from "@/lib/agent-directory";
import type { PublicAgentIdentity } from "@/lib/agent-identity-api";
import { publicApiMarkdownUrl, type DocumentResponse } from "@/lib/api";
import { buildInboxContactReturnPath } from "@/lib/auth-return-intent";
import { absoluteSiteUrl, publicDocumentJsonLd, publicDocumentView, safeJsonLd } from "@/lib/public-document";

export function PublicDocumentPage({ document, agentIdentities = [], agentIdentitiesUnavailable = false, privateWorkspacesEnabled }: { document: DocumentResponse; agentIdentities?: PublicAgentIdentity[]; agentIdentitiesUnavailable?: boolean; privateWorkspacesEnabled: boolean }) {
  const view = publicDocumentView(document);
  const canonicalPath = document.kind === "profile" ? `/p/${encodeURIComponent(document.identifier)}` : `/r/${encodeURIComponent(document.identifier)}`;
  const canonicalUrl = absoluteSiteUrl(canonicalPath);
  const structuredData = publicDocumentJsonLd(document, canonicalUrl);
  const markdownHref = publicApiMarkdownUrl(document.markdown_url);
  const taxonomy = [...view.occupations, ...view.industries, ...view.seniority];
  const professionalSignals = [
    view.organizations.length ? { label: "Organizations", value: view.organizations.map((organization) => `${organization.label}${organization.relationship ? ` (${humanize(organization.relationship)})` : ""}`).join(", ") } : null,
    view.languages.length ? { label: "Languages", value: view.languages.join(", ") } : null,
    view.workModes.length ? { label: "Work modes", value: view.workModes.map(humanize).join(", ") } : null,
    view.availabilityStatus ? { label: "Availability", value: `${humanize(view.availabilityStatus)}${view.availabilityFrom ? ` from ${formatDate(view.availabilityFrom)}` : ""}` } : null,
    view.openTo.length ? { label: "Open to", value: view.openTo.map(humanize).join(", ") } : null
  ].filter((signal): signal is { label: string; value: string } => signal !== null);
  const updatedLabel = formatDate(document.updated_at);
  const hasRepresentation = Boolean(view.representationStatus || view.contactRoutes.length > 0 || view.contactDisclosure);
  const agentIdentityLookupUnavailable = agentIdentitiesUnavailable && agentIdentities.length === 0;
  const linkedContactIdentity = document.kind === "profile"
    ? agentIdentities.find((identity) => identity.profileHandle === document.identifier && identity.capabilities[0] === "internal_contact_request")
    : undefined;
  const linkedContactIntent = linkedContactIdentity
    ? buildInboxContactReturnPath(linkedContactIdentity.profileHandle)
    : null;

  return (
    <main className="mx-auto max-w-5xl px-5 py-9 sm:py-14 lg:px-8">
      {structuredData && <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: safeJsonLd(structuredData) }} />}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link href="/search" className="inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm text-mist transition hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid"><ArrowLeft className="size-4" aria-hidden /> Search people</Link>
        {markdownHref && <a href={markdownHref} type="text/markdown" className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 bg-white/[.04] px-4 py-2 text-sm font-semibold text-white transition hover:border-acid/40 hover:bg-acid/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"><Braces className="size-4 text-acid" aria-hidden /> View canonical Markdown</a>}
      </div>

      <article className="mt-7 overflow-hidden rounded-[2rem] border border-white/10 bg-panel shadow-glow">
        <header className="relative overflow-hidden border-b border-white/10 px-6 py-9 sm:px-10 sm:py-12">
          <div className="absolute inset-0 -z-0 bg-grid bg-[size:46px_46px] opacity-20 [mask-image:linear-gradient(to_right,black,transparent)]" aria-hidden />
          <div className="relative">
            <div className="flex flex-wrap items-center gap-2">
              <p className="eyebrow">Canonical public {document.kind}</p>
              <span className="inline-flex items-center gap-1 rounded-full border border-acid/25 bg-acid/10 px-2.5 py-1 text-[11px] font-semibold text-acid"><BadgeCheck className="size-3.5" aria-hidden /> Schema-valid · v{view.schemaVersion}</span>
            </div>
            <h1 className="mt-5 max-w-4xl font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">{view.fields.name || document.identifier}</h1>
            {view.fields.title && <p className="mt-4 inline-flex items-center gap-2 text-lg text-white"><BriefcaseBusiness className="size-5 text-acid" aria-hidden />{view.fields.title}</p>}
            {view.fields.headline && <p className="mt-3 max-w-3xl text-lg leading-8 text-mist">{view.fields.headline}</p>}
            <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-sm text-mist">
              {view.locationLabel && <span className="inline-flex items-center gap-2"><MapPin className="size-4 text-acid" aria-hidden />{view.locationLabel}</span>}
              <span className="inline-flex items-center gap-2"><CalendarClock className="size-4 text-acid" aria-hidden />Updated <time dateTime={document.updated_at}>{updatedLabel}</time></span>
              <span className="inline-flex items-center gap-2"><ShieldCheck className="size-4 text-acid" aria-hidden />Version {document.version}</span>
            </div>
            {(taxonomy.length > 0 || view.fields.skills.length > 0) && <ul className="mt-7 flex flex-wrap gap-2" aria-label="Professional metadata">
              {taxonomy.map((item) => <li key={`taxonomy-${item}`} className="rounded-full border border-acid/20 bg-acid/[.07] px-3 py-1.5 text-xs text-acid">{item}</li>)}
              {view.fields.skills.map((skill) => <li key={`skill-${skill}`} className="rounded-full border border-white/12 bg-white/[.045] px-3 py-1.5 text-xs text-[#d5d9e0]">{skill}</li>)}
            </ul>}
          </div>
        </header>

        {(hasRepresentation || agentIdentities.length > 0 || agentIdentityLookupUnavailable) && <section aria-labelledby={hasRepresentation ? "representation-title" : "agent-identities-title"} className="grid gap-5 border-b border-white/10 bg-black/15 px-6 py-6 sm:grid-cols-[1fr_auto] sm:px-10">
          {hasRepresentation && <div>
            <h2 id="representation-title" className="inline-flex items-center gap-2 text-sm font-semibold text-white">{view.representationStatus?.includes("agent") ? <Bot className="size-4 text-acid" aria-hidden /> : <UserRoundCheck className="size-4 text-acid" aria-hidden />} Representation and contact</h2>
            <p className="mt-2 text-sm leading-6 text-mist">{representationLabel(view.representationStatus, view.representativeName, view.representativeRole)}{view.contactDisclosure ? ` Contact policy: ${humanize(view.contactDisclosure)}.` : ""}</p>
          </div>}
          {hasRepresentation && (view.representativeUrl || view.contactRoutes.length > 0) && <div className="flex flex-wrap items-center gap-2 sm:justify-end">{view.representativeUrl && <a href={view.representativeUrl} rel="nofollow noreferrer" className="inline-flex min-h-11 items-center gap-2 rounded-full border border-acid/30 px-4 text-sm font-semibold text-acid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Representative <ExternalLink className="size-4" aria-hidden /></a>}{view.contactRoutes.map((route) => <a key={`${route.label}-${route.url}`} href={route.url} rel="nofollow noreferrer" className="inline-flex min-h-11 items-center gap-2 rounded-full bg-acid px-4 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">{route.label}<ExternalLink className="size-4" aria-hidden /></a>)}</div>}
          {agentIdentities.length > 0 && <div className="sm:col-span-2 rounded-2xl border border-acid/20 bg-acid/[.045] p-4"><div className="flex flex-wrap items-start justify-between gap-4"><div><h2 id="agent-identities-title" className="inline-flex items-center gap-2 text-sm font-semibold text-white"><Bot className="size-4 text-acid" aria-hidden />Published Agent Identities</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-mist">These owner-attested labels are linked to this public profile. Their only published capability is a platform-mediated internal contact request; they do not show authority, ownership, availability, grants, or mandates.</p></div><div className="flex flex-wrap gap-2"><Link href={agentDirectoryHref({ q: "", profileHandle: document.identifier })} className="inline-flex min-h-11 shrink-0 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Agent Directory</Link>{privateWorkspacesEnabled && linkedContactIntent && <Link href={linkedContactIntent} className="inline-flex min-h-11 shrink-0 items-center rounded-full border border-acid/30 px-4 text-sm font-semibold text-acid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Prepare private contact request</Link>}</div></div>{privateWorkspacesEnabled ? <p className="mt-3 text-xs leading-5 text-mist/75">Preparing a request fills only this linked profile handle in a private form. It does not send outreach, select an agent-outreach route, or establish a mandate.</p> : <p className="mt-3 text-xs leading-5 text-mist/75">Private contact controls are unavailable in this deployment.</p>}<ul className="mt-4 flex flex-wrap gap-2" aria-label="Published Agent Identities">{agentIdentities.map((identity) => <li key={identity.handle} className="rounded-xl border border-white/10 bg-black/15 px-3 py-2"><p className="text-sm font-semibold text-white">{identity.displayName}</p><p className="mt-0.5 font-mono text-xs text-mist">@{identity.handle}</p></li>)}</ul></div>}
          {agentIdentityLookupUnavailable && <div role="status" className="sm:col-span-2 rounded-2xl border border-amber-300/25 bg-amber-300/[.06] p-4"><h2 id="agent-identities-title" className="inline-flex items-center gap-2 text-sm font-semibold text-white"><Bot className="size-4 text-amber-200" aria-hidden />Published Agent Identities unavailable</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-mist">This profile remains available, but its current Agent Identity status cannot be confirmed. Try the Agent Directory again later.</p><Link href={agentDirectoryHref({ q: "", profileHandle: document.identifier })} className="mt-3 inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Open Agent Directory</Link></div>}
        </section>}

        <div className="grid gap-8 px-6 py-8 sm:px-10 sm:py-11 lg:grid-cols-[minmax(0,1fr)_15rem]">
          <div><MarkdownPreview markdown={document.markdown} omitTitle /></div>
          <aside className="space-y-5" aria-label="Document provenance">
            {document.kind === "profile" && <><Link href={`/p/${encodeURIComponent(document.identifier)}/posts`} className="block rounded-2xl border border-white/10 bg-black/15 p-4 transition hover:border-acid/30"><h2 className="text-sm font-semibold text-white">Professional post archive</h2><p className="mt-2 text-xs leading-5 text-mist">View immutable public posts from this profile. There is no public global timeline or follower graph.</p></Link>{privateWorkspacesEnabled ? <><ProfilePostControls handle={document.identifier} /><ProfileConnectControl handle={document.identifier} /></> : <p className="mt-5 text-sm leading-6 text-mist">Private human connection controls are unavailable in this deployment.</p>}</>}
            {professionalSignals.length > 0 && <section className="rounded-2xl border border-acid/20 bg-acid/[.05] p-4"><h2 className="text-sm font-semibold text-white">Professional signals</h2><dl className="mt-3 space-y-3 text-xs">{professionalSignals.map((signal) => <Fact key={signal.label} label={signal.label} value={signal.value} />)}</dl></section>}
            <section className="rounded-2xl border border-white/10 bg-black/15 p-4">
              <h2 className="inline-flex items-center gap-2 text-sm font-semibold text-white"><Network className="size-4 text-acid" aria-hidden /> Source facts</h2>
              <dl className="mt-3 space-y-3 text-xs">
                <Fact label="Document" value={document.id} mono />
                <Fact label="Schema" value={`connect.md/${document.kind} v${view.schemaVersion}`} />
                <Fact label="Canonical version" value={String(document.version)} />
                <Fact label="Updated" value={updatedLabel} />
              </dl>
            </section>
            {view.provenance.length > 0 && <section className="rounded-2xl border border-white/10 bg-black/15 p-4"><h2 className="text-sm font-semibold text-white">Declared provenance</h2><dl className="mt-3 space-y-3 text-xs">{view.provenance.map((item) => <Fact key={`${item.label}-${item.value}`} label={item.label} value={item.value} />)}</dl></section>}
            <p className="text-xs leading-5 text-mist/75">Canonical and schema-valid describe this document’s source and format. They do not independently verify identity, employment, credentials, or representation authority.</p>
          </aside>
        </div>
      </article>
    </main>
  );
}

function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div><dt className="text-mist/70">{label}</dt><dd className={`mt-0.5 break-all text-white ${mono ? "font-mono" : ""}`}>{value}</dd></div>;
}

function representationLabel(status: string | null, name: string | null, role: string | null) {
  if (!status) return "No public representation status has been declared.";
  const representative = [name, role].filter(Boolean).join(" · ");
  return `${humanize(status)}${representative ? ` by ${representative}` : ""}. This is owner-attested unless a separate verification is shown.`;
}

function humanize(value: string) { return value.replaceAll("_", " ").replaceAll("-", " "); }
function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}
