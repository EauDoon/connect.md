import type { Metadata } from "next";

import { publicApiMarkdownUrl, type DocumentResponse } from "@/lib/api";
import { humanFieldsFromMarkdown, splitFrontmatter, type DocumentKind } from "@/lib/markdown";

export type PublicDocumentView = {
  fields: ReturnType<typeof humanFieldsFromMarkdown>;
  schemaVersion: number;
  occupationIds: string[];
  occupations: string[];
  industryIds: string[];
  industries: string[];
  skillIds: string[];
  languageIds: string[];
  languages: string[];
  locationLabel: string;
  locationCountryCode: string | null;
  locationRegion: string | null;
  locationCity: string | null;
  seniority: string[];
  workModes: string[];
  availabilityStatus: string | null;
  availabilityFrom: string | null;
  openTo: string[];
  organizations: Array<{ label: string; relationship: string | null }>;
  representationStatus: string | null;
  representativeName: string | null;
  representativeRole: string | null;
  representativeUrl: string | null;
  contactDisclosure: string | null;
  contactRoutes: Array<{ label: string; url: string }>;
  provenance: Array<{ label: string; value: string }>;
};

export function publicDocumentView(document: DocumentResponse): PublicDocumentView {
  const { attributes } = splitFrontmatter(document.markdown);
  const kind = document.kind;
  const location = record(attributes.location);
  const representation = record(attributes.public_representation ?? attributes.representation);
  const representative = record(representation.representative);
  const contact = record(attributes.contact);
  const availability = record(attributes.availability);
  return {
    fields: humanFieldsFromMarkdown(document.markdown, kind),
    schemaVersion: integer(attributes.schema_version, 1),
    occupationIds: stringList(attributes.occupation_ids).length ? stringList(attributes.occupation_ids) : referenceIds(attributes.occupations),
    occupations: labelList(attributes.occupations),
    industryIds: stringList(attributes.industry_ids).length ? stringList(attributes.industry_ids) : referenceIds(attributes.industries),
    industries: labelList(attributes.industries),
    skillIds: stringList(attributes.skill_ids).length ? stringList(attributes.skill_ids) : referenceIds(attributes.skills),
    languageIds: stringList(attributes.language_ids).length ? stringList(attributes.language_ids) : referenceIds(attributes.languages),
    languages: labelList(attributes.languages),
    locationLabel: stringValue(attributes.location_label) || stringValue(attributes.location) || stringValue(location.label),
    locationCountryCode: nullableString(attributes.location_country_code ?? location.country_code),
    locationRegion: nullableString(attributes.location_region ?? location.region),
    locationCity: nullableString(attributes.location_city ?? location.city),
    seniority: labelsFromOneOrMany(attributes.seniority),
    workModes: stringList(attributes.work_modes),
    availabilityStatus: nullableString(attributes.availability_status ?? availability.status),
    availabilityFrom: nullableString(attributes.availability_from ?? availability.available_from),
    openTo: labelList(attributes.open_to),
    organizations: organizationList(attributes.organizations),
    representationStatus: nullableString(attributes.representation_status ?? representation.status),
    representativeName: nullableString(attributes.representative_name ?? representative.label ?? representative.name ?? representation.name),
    representativeRole: nullableString(attributes.representative_role ?? representative.role ?? representation.role),
    representativeUrl: safeUrl(stringValue(attributes.representative_url ?? representation.public_url)),
    contactDisclosure: nullableString(attributes.contact_disclosure ?? contact.disclosure),
    contactRoutes: parseContactRoutes(attributes.contact_routes ?? contact.routes ?? contact.channels),
    provenance: parseProvenance(attributes.provenance ?? attributes.source)
  };
}

export function publicDocumentMetadata(document: DocumentResponse, canonicalPath: string): Metadata {
  const view = publicDocumentView(document);
  const name = boundedMetadataText(view.fields.name || document.identifier, 120) || "Public document";
  const role = boundedMetadataText(view.fields.title || view.occupations[0] || (document.kind === "profile" ? "Professional profile" : "Professional resume"), 96);
  const title = boundedMetadataText(`${name} — ${role}`, 160);
  const description = boundedMetadataText(view.fields.headline || `${role} in ${view.locationLabel || "connect.md"}`, 280) || role;
  return {
    title,
    description,
    alternates: {
      canonical: canonicalPath,
      types: publicMarkdownAlternate(document.markdown_url)
    },
    openGraph: {
      type: document.kind === "profile" ? "profile" : "website",
      title,
      description,
      url: canonicalPath
    }
  };
}

export function publicMarkdownAlternate(markdownUrl: string) {
  const href = publicApiMarkdownUrl(markdownUrl);
  return href ? { "text/markdown": href } : undefined;
}

export function personJsonLd(document: DocumentResponse, canonicalUrl: string) {
  const view = publicDocumentView(document);
  const name = boundedMetadataText(view.fields.name, 160);
  if (!name) return null;
  const address = view.locationCountryCode || view.locationRegion || view.locationCity
    ? {
        "@type": "PostalAddress",
        addressCountry: view.locationCountryCode ?? undefined,
        addressRegion: view.locationRegion ?? undefined,
        addressLocality: view.locationCity ?? undefined
      }
    : undefined;
  return {
    "@context": "https://schema.org",
    "@type": "Person",
    name,
    description: view.fields.headline,
    jobTitle: view.fields.title || view.occupations[0] || undefined,
    url: canonicalUrl,
    address,
    knowsAbout: [...view.occupations, ...view.industries, ...view.fields.skills],
    contactPoint: view.contactRoutes.map((route) => ({ "@type": "ContactPoint", name: route.label, url: route.url }))
  };
}

export function profilePageJsonLd(document: DocumentResponse, canonicalUrl: string) {
  const personProjection = personJsonLd(document, canonicalUrl);
  if (!personProjection) return null;
  const { "@context": _context, ...person } = personProjection;
  return {
    "@context": "https://schema.org",
    "@type": "ProfilePage",
    url: canonicalUrl,
    dateModified: document.updated_at,
    mainEntity: {
      ...person,
      "@id": `${canonicalUrl}#person`
    }
  };
}

function resumeJsonLd(document: DocumentResponse, canonicalUrl: string) {
  const view = publicDocumentView(document);
  const { attributes } = splitFrontmatter(document.markdown);
  const name = boundedMetadataText(stringValue(attributes.name), 160);
  if (!name) return null;
  const description = boundedMetadataText(view.fields.headline, 280);
  return {
    "@context": "https://schema.org",
    "@type": "DigitalDocument",
    name,
    ...(description ? { description } : {}),
    url: canonicalUrl,
    dateModified: document.updated_at,
    version: String(document.version),
    encodingFormat: "text/markdown"
  };
}

export function publicDocumentJsonLd(document: DocumentResponse, canonicalUrl: string) {
  return document.kind === "profile" ? profilePageJsonLd(document, canonicalUrl) : resumeJsonLd(document, canonicalUrl);
}

export function safeJsonLd(value: unknown) {
  return JSON.stringify(value).replace(/[<\u2028\u2029]/gu, (character) => character === "<" ? "\\u003c" : character === "\u2028" ? "\\u2028" : "\\u2029");
}

export function publicSiteOrigin() {
  return process.env.NEXT_PUBLIC_SITE_URL?.trim() || "https://connect.md";
}

export function absoluteSiteUrl(path: string) {
  return new URL(path, publicSiteOrigin()).toString();
}

function parseContactRoutes(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const route = record(item);
    const type = stringValue(route.type);
    const raw = stringValue(route.url) || stringValue(route.value);
    const candidate = type === "email" ? `mailto:${raw}` : type === "phone" ? `tel:${raw}` : raw;
    const url = safeUrl(candidate);
    return url ? { label: stringValue(route.label) || humanLabel(type) || "Contact", url } : null;
  }).filter((route): route is { label: string; url: string } => route !== null);
}

function boundedMetadataText(value: string, maxLength: number) {
  return Array.from(value.replace(/\s+/gu, " ").trim()).slice(0, maxLength).join("");
}

function parseProvenance(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => {
    const source = record(item);
    const label = stringValue(source.label) || stringValue(source.type);
    const detail = stringValue(source.url) || stringValue(source.value);
    return label && detail ? { label, value: detail } : null;
  }).filter((item): item is { label: string; value: string } => item !== null);
  return Object.entries(record(value)).filter((entry): entry is [string, string] => typeof entry[1] === "string").map(([label, detail]) => ({ label, value: detail }));
}

function safeUrl(value: string) {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return ["https:", "http:", "mailto:", "tel:"].includes(parsed.protocol) ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function record(value: unknown): Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function stringValue(value: unknown) { return typeof value === "string" ? value : ""; }
function nullableString(value: unknown) { return typeof value === "string" && value ? value : null; }
function stringList(value: unknown) { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item)) : []; }
function referenceIds(value: unknown) { return Array.isArray(value) ? value.map((item) => { const reference = record(item); const id = stringValue(reference.id); const scheme = stringValue(reference.scheme); return id ? scheme ? `${scheme}:${id}` : id : ""; }).filter(Boolean) : []; }
function labelList(value: unknown) { return Array.isArray(value) ? value.map((item) => typeof item === "string" ? item : stringValue(record(item).label)).filter(Boolean) : []; }
function organizationList(value: unknown) { return Array.isArray(value) ? value.map((item) => typeof item === "string" ? { label: item, relationship: null } : { label: stringValue(record(item).label), relationship: nullableString(record(item).relationship) }).filter((organization) => Boolean(organization.label)) : []; }
function labelsFromOneOrMany(value: unknown) { if (Array.isArray(value)) return labelList(value); const label = typeof value === "string" ? value : stringValue(record(value).label); return label ? [label] : []; }
function integer(value: unknown, fallback: number) { return typeof value === "number" && Number.isInteger(value) ? value : fallback; }
function humanLabel(value: string) { return value ? value.charAt(0).toUpperCase() + value.slice(1) : ""; }
