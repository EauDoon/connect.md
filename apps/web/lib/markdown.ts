import YAML from "yaml";

export type DocumentKind = "profile" | "resume";

export type HumanFields = {
  schemaVersion: 1 | 2;
  name: string;
  handle: string;
  slug: string;
  title: string;
  headline: string;
  location: string;
  skills: string[];
  visibility: "private" | "public";
  narrative: string;
  experience: string;
  education: string;
  occupations: string[];
  industries: string[];
  languages: string[];
  languageProficiency: "" | "basic" | "conversational" | "professional" | "native_or_bilingual";
  seniority: string;
  workModes: Array<"on_site" | "hybrid" | "remote">;
  availabilityStatus: "available_now" | "available_from" | "not_available" | "not_disclosed";
  availableFrom: string;
  openTo: string[];
  organizations: string[];
  organizationRelationship: "current_employer" | "past_employer" | "founder" | "member" | "education" | "client" | "other";
  representationStatus: "self" | "authorized_representative" | "organization" | "not_disclosed";
  representative: string;
  contactDisclosure: "none" | "platform_only" | "public";
  contactType: "email" | "phone" | "url" | "platform";
  contactValue: string;
  contactLabel: string;
};

const sharedFrontmatter = (kind: DocumentKind) => `---
schema: connect.md/${kind}
schema_version: 2
${kind === "profile" ? "handle: your-handle\n" : "slug: your-name-resume\n"}name: Your Name
${kind === "resume" ? "title: Professional title\n" : ""}headline: Your professional headline
occupations:
  - scheme: connectmd-user-occupation
    id: unspecified-occupation
    label: Unspecified occupation
industries: []
location:
  scheme: connectmd-user-location
  id: unspecified-location
  label: Unspecified location
skills:
  - scheme: connectmd-user-skill
    id: unspecified-skill
    label: Unspecified skill
languages: []
seniority:
  scheme: connectmd-user-seniority
  id: not-disclosed
  label: Not disclosed
work_modes: []
availability:
  status: not_disclosed
open_to: []
organizations: []
public_representation:
  status: not_disclosed
contact:
  disclosure: none
visibility: private
---`;

export const profileStarter = `${sharedFrontmatter("profile")}

# Your Name

## About

Write a concise introduction that makes it easy to understand your work.

## Experience

### Current role

Describe the impact, scope, and outcomes of your work.

## Skills

- Unspecified skill
`;

export const resumeStarter = `${sharedFrontmatter("resume")}

# Your Name

## Summary

Write a concise professional summary.

## Experience

### Current role

Describe the impact, scope, and outcomes of your work.

## Education

### Education or credential

Add your most relevant education or credentials.

## Skills

- Unspecified skill
`;

export function starterFor(kind: DocumentKind) {
  return kind === "profile" ? profileStarter : resumeStarter;
}

export function normaliseMarkdown(markdown: string) {
  return markdown.replace(/\r\n?/g, "\n").replace(/\s+$/u, "") + "\n";
}

export function splitFrontmatter(markdown: string) {
  const source = normaliseMarkdown(markdown);
  const match = source.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!match) return { attributes: {} as Record<string, unknown>, body: source, hasFrontmatter: false };

  try {
    const parsed = YAML.parse(match[1]);
    return {
      attributes: isRecord(parsed) ? parsed : {},
      body: match[2],
      hasFrontmatter: true
    };
  } catch {
    return { attributes: {} as Record<string, unknown>, body: match[2], hasFrontmatter: true };
  }
}

export function frontmatterParseIssue(markdown: string) {
  const source = normaliseMarkdown(markdown);
  const match = source.match(/^---\n([\s\S]*?)\n---(?:\n|$)/);
  if (!match) return "Frontmatter delimiters are missing.";
  try {
    const parsed = YAML.parse(match[1]);
    return isRecord(parsed) ? null : "Frontmatter must contain a YAML object.";
  } catch (error) {
    return `Frontmatter YAML is invalid: ${error instanceof Error ? error.message : "unknown parser error"}`;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function referenceLabels(value: unknown) {
  return Array.isArray(value) ? value.map((item) => isRecord(item) ? stringValue(item.label) : "").filter(Boolean) : [];
}

function referenceLabel(value: unknown) {
  return isRecord(value) ? stringValue(value.label) : "";
}

function oneOf<T extends string>(value: unknown, options: readonly T[], fallback: T): T {
  return typeof value === "string" && options.includes(value as T) ? value as T : fallback;
}

export function humanFieldsFromMarkdown(markdown: string, kind: DocumentKind): HumanFields {
  const { attributes, body } = splitFrontmatter(markdown);
  const fallbackName = scanMarkdownHeadings(body).find((heading) => heading.level === 1)?.text.trim() ?? "";
  const narrativeHeading = kind === "profile" ? "About" : "Summary";
  const schemaVersion = attributes.schema_version === 2 ? 2 : 1;
  const location = isRecord(attributes.location) ? stringValue(attributes.location.label) : stringValue(attributes.location);
  const skills = Array.isArray(attributes.skills)
    ? attributes.skills.map((item) => typeof item === "string" ? item : isRecord(item) ? stringValue(item.label) : "").filter(Boolean)
    : [];
  const availability = isRecord(attributes.availability) ? attributes.availability : {};
  const representation = isRecord(attributes.public_representation) ? attributes.public_representation : {};
  const contact = isRecord(attributes.contact) ? attributes.contact : {};
  const firstChannel = Array.isArray(contact.channels) ? contact.channels.find(isRecord) ?? {} : {};
  const firstOrganization = Array.isArray(attributes.organizations) ? attributes.organizations.find(isRecord) ?? {} : {};

  return {
    schemaVersion,
    name: stringValue(attributes.name) || fallbackName,
    handle: stringValue(attributes.handle),
    slug: stringValue(attributes.slug),
    title: stringValue(attributes.title),
    headline: stringValue(attributes.headline),
    location,
    skills,
    visibility: attributes.visibility === "public" ? "public" : "private",
    narrative: sectionBody(body, narrativeHeading),
    experience: sectionBody(body, "Experience"),
    education: sectionBody(body, "Education"),
    occupations: referenceLabels(attributes.occupations),
    industries: referenceLabels(attributes.industries),
    languages: referenceLabels(attributes.languages),
    languageProficiency: "",
    seniority: referenceLabel(attributes.seniority),
    workModes: Array.isArray(attributes.work_modes) ? attributes.work_modes.filter((mode): mode is "on_site" | "hybrid" | "remote" => mode === "on_site" || mode === "hybrid" || mode === "remote") : [],
    availabilityStatus: oneOf(availability.status, ["available_now", "available_from", "not_available", "not_disclosed"], "not_disclosed"),
    availableFrom: stringValue(availability.available_from),
    openTo: referenceLabels(attributes.open_to),
    organizations: referenceLabels(attributes.organizations),
    organizationRelationship: oneOf(firstOrganization.relationship, ["current_employer", "past_employer", "founder", "member", "education", "client", "other"], "current_employer"),
    representationStatus: oneOf(representation.status, ["self", "authorized_representative", "organization", "not_disclosed"], "not_disclosed"),
    representative: referenceLabel(representation.representative),
    contactDisclosure: oneOf(contact.disclosure, ["none", "platform_only", "public"], "none"),
    contactType: oneOf(firstChannel.type, ["email", "phone", "url", "platform"], "platform"),
    contactValue: stringValue(firstChannel.value),
    contactLabel: stringValue(firstChannel.label)
  };
}

export function documentIdentifier(markdown: string, kind: DocumentKind) {
  const { attributes } = splitFrontmatter(markdown);
  return stringValue(kind === "profile" ? attributes.handle : attributes.slug);
}

export function rebaseServerOwnedFields(markdown: string, canonicalMarkdown: string) {
  const serverFields = ["id", "owner_id", "version", "updated_at"] as const;
  const edited = splitFrontmatter(markdown);
  const canonical = splitFrontmatter(canonicalMarkdown);
  const editedIssue = frontmatterParseIssue(markdown);
  const canonicalIssue = frontmatterParseIssue(canonicalMarkdown);
  if (!edited.hasFrontmatter || !canonical.hasFrontmatter || editedIssue || canonicalIssue) {
    throw new Error(`Server-owned fields could not be rebased safely. ${editedIssue ?? canonicalIssue ?? "Frontmatter is missing."}`);
  }
  const rebasedAttributes = { ...edited.attributes };
  for (const field of serverFields) {
    if (Object.hasOwn(canonical.attributes, field)) rebasedAttributes[field] = canonical.attributes[field];
    else delete rebasedAttributes[field];
  }
  return normaliseMarkdown(`---\n${YAML.stringify(rebasedAttributes).trim()}\n---\n\n${edited.body.trim()}\n`);
}

type SectionEntry = {
  heading: string;
  content: string;
  start: number;
  end: number;
};

export type MarkdownHeading = {
  level: number;
  text: string;
  start: number;
  end: number;
};

function scanMarkdownStructure(body: string) {
  const headings: MarkdownHeading[] = [];
  const invalidHeadingSyntax: number[] = [];
  let fence: { marker: "`" | "~"; length: number } | null = null;
  let inComment = false;

  for (const match of body.matchAll(/[^\n]*(?:\n|$)/g)) {
    if (!match[0]) continue;
    const start = match.index;
    const line = match[0].endsWith("\n") ? match[0].slice(0, -1) : match[0];
    const trimmedStart = line.trimStart();

    if (fence) {
      const closing = trimmedStart.match(/^(`+|~+)[ \t]*$/);
      if (closing && closing[1][0] === fence.marker && closing[1].length >= fence.length) fence = null;
      continue;
    }
    if (inComment) {
      if (line.includes("-->")) inComment = false;
      continue;
    }

    const commentStart = line.indexOf("<!--");
    const candidate = commentStart >= 0 ? line.slice(0, commentStart).trimEnd() : line;
    const openingFence = candidate.match(/^[ \t]{0,3}(`{3,}|~{3,})/);
    if (openingFence) {
      fence = { marker: openingFence[1][0] as "`" | "~", length: openingFence[1].length };
      continue;
    }
    if (commentStart >= 0 && line.indexOf("-->", commentStart + 4) < 0) inComment = true;

    const heading = candidate.match(/^(#{1,6})([ \t]+)(.*)$/);
    if (!heading) continue;
    if (heading[2] !== " " || !heading[3] || heading[3] !== heading[3].trim()) {
      invalidHeadingSyntax.push(start);
      continue;
    }
    headings.push({ level: heading[1].length, text: heading[3], start, end: start + line.length });
  }
  return { headings, invalidHeadingSyntax };
}

export function scanMarkdownHeadings(body: string): MarkdownHeading[] {
  return scanMarkdownStructure(body).headings;
}

export function scanMarkdownHeadingSyntaxIssues(body: string) {
  return scanMarkdownStructure(body).invalidHeadingSyntax;
}

function sectionEntries(body: string): SectionEntry[] {
  const headings = scanMarkdownHeadings(body).filter((heading) => heading.level === 2);
  return headings.map((heading, index) => {
    const end = headings[index + 1]?.start ?? body.length;
    return { heading: heading.text.trim(), content: body.slice(heading.end, end).trim(), start: heading.start, end };
  });
}

function sectionBody(body: string, heading: string) {
  return sectionEntries(body).find((entry) => entry.heading === heading)?.content ?? "";
}

function setSection(body: string, heading: string, value: string) {
  const existing = sectionEntries(body).find((entry) => entry.heading === heading);
  const replacement = `## ${heading}\n\n${value.trim()}\n`;
  if (!existing) return `${body.trim()}\n\n${replacement}`;
  const before = body.slice(0, existing.start).trimEnd();
  const after = body.slice(existing.end).trimStart();
  return `${before}\n\n${replacement}${after ? `\n${after}` : ""}`;
}

/**
 * Human Mode changes only selected fields directly in the canonical buffer.
 * Unknown frontmatter keys and unedited sections remain part of that buffer.
 */
export function patchHumanFields(markdown: string, kind: DocumentKind, patch: Partial<HumanFields>) {
  const parseIssue = frontmatterParseIssue(markdown);
  if (parseIssue) throw new Error(`Human Mode cannot edit this draft. ${parseIssue}`);
  const { attributes, body } = splitFrontmatter(markdown);
  const current = humanFieldsFromMarkdown(markdown, kind);
  const next = { ...current, ...patch };
  for (const field of ["name", "handle", "slug", "title", "headline", "location"] as const) next[field] = next[field].trim();
  next.skills = cleanLabels(next.skills);
  next.occupations = cleanLabels(next.occupations);
  next.industries = cleanLabels(next.industries);
  next.languages = cleanLabels(next.languages);
  next.openTo = cleanLabels(next.openTo);
  next.organizations = cleanLabels(next.organizations);
  next.seniority = next.seniority.trim();
  next.representative = next.representative.trim();
  next.availableFrom = next.availableFrom.trim();
  next.contactValue = next.contactValue.trim();
  next.contactLabel = next.contactLabel.trim();
  const schemaVersion = attributes.schema_version === 2 ? 2 : 1;
  const nextAttributes: Record<string, unknown> = {
    ...attributes,
    schema: `connect.md/${kind}`,
    schema_version: schemaVersion,
    name: next.name,
    headline: next.headline,
    visibility: next.visibility
  };
  if (schemaVersion === 2) {
    nextAttributes.location = updateLocationReference(attributes.location, current.location, next.location);
    nextAttributes.skills = updateLabelReferences(attributes.skills, next.skills, "connectmd-user-skill");
    nextAttributes.occupations = updateLabelReferences(attributes.occupations, next.occupations, "connectmd-user-occupation");
    nextAttributes.industries = updateLabelReferences(attributes.industries, next.industries, "connectmd-user-industry");
    nextAttributes.languages = updateLanguageReferences(attributes.languages, next.languages, next.languageProficiency);
    nextAttributes.seniority = updateSingleReference(attributes.seniority, current.seniority, next.seniority, "connectmd-user-seniority");
    nextAttributes.work_modes = next.workModes;
    nextAttributes.availability = updateAvailability(attributes.availability, current, next);
    nextAttributes.open_to = updateLabelReferences(attributes.open_to, next.openTo, "connectmd-user-open-to");
    nextAttributes.organizations = updateOrganizationReferences(attributes.organizations, next.organizations, next.organizationRelationship);
    nextAttributes.public_representation = updatePublicRepresentation(attributes.public_representation, current, next);
    nextAttributes.contact = updateContact(attributes.contact, current, next);
  } else {
    nextAttributes.location = next.location;
    nextAttributes.skills = next.skills;
  }

  if (kind === "profile") {
    nextAttributes.handle = next.handle;
    delete nextAttributes.slug;
    delete nextAttributes.title;
  } else {
    nextAttributes.slug = next.slug;
    nextAttributes.title = next.title;
    delete nextAttributes.handle;
  }

  const title = next.name.trim() || "Your Name";
  const h1 = scanMarkdownHeadings(body).find((heading) => heading.level === 1);
  const withoutTitle = h1 ? `${body.slice(0, h1.start)}# ${title}${body.slice(h1.end)}` : `# ${title}\n\n${body.trim()}`;
  let nextBody = setSection(withoutTitle, kind === "profile" ? "About" : "Summary", next.narrative);
  if (patch.skills) nextBody = setSection(nextBody, "Skills", next.skills.map((skill) => `- ${skill.replace(/\s+/gu, " ").trim()}`).join("\n"));
  if (patch.experience !== undefined) nextBody = setSection(nextBody, "Experience", next.experience);
  if (patch.education !== undefined) nextBody = setSection(nextBody, "Education", next.education);
  return normaliseMarkdown(`---\n${YAML.stringify(nextAttributes).trim()}\n---\n\n${nextBody.trim()}\n`);
}

export function switchDocumentKind(markdown: string, kind: DocumentKind) {
  const { attributes, body } = splitFrontmatter(markdown);
  const sourceKind: DocumentKind = attributes.schema === "connect.md/resume" ? "resume" : "profile";
  if (sourceKind === kind) return normaliseMarkdown(markdown);

  const source = humanFieldsFromMarkdown(markdown, sourceKind);
  const name = source.name.trim() || "Your Name";
  const next: HumanFields = {
    ...source,
    handle: source.handle || slugify(source.slug.replace(/-resume$/u, "")) || slugify(name),
    slug: source.slug || `${source.handle || slugify(name)}-resume`,
    title: source.title || source.headline || "Professional title",
    narrative: source.narrative
  };
  const nextAttributes: Record<string, unknown> = { ...attributes };
  for (const field of ["id", "owner_id", "version", "updated_at", "handle", "slug", "title"]) delete nextAttributes[field];
  const schemaVersion = attributes.schema_version === 2 ? 2 : 1;
  Object.assign(nextAttributes, {
    schema: `connect.md/${kind}`,
    schema_version: schemaVersion,
    name: next.name,
    headline: next.headline,
    visibility: next.visibility
  });
  if (schemaVersion === 1) {
    nextAttributes.location = next.location;
    nextAttributes.skills = next.skills;
  }
  if (kind === "profile") nextAttributes.handle = next.handle;
  else Object.assign(nextAttributes, { slug: next.slug, title: next.title });

  const nextBody = bodyForKind(body, kind, next);
  return normaliseMarkdown(`---\n${YAML.stringify(nextAttributes).trim()}\n---\n\n${nextBody}`);
}

function slugify(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/gu, "-").replace(/^-+|-+$/gu, "").slice(0, 63) || "your-name";
}

function stableLabelHash(value: string) {
  let hash = 2_166_136_261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return (hash >>> 0).toString(36);
}

function referenceSlugBase(value: string) {
  return value
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/gu, "-")
    .replace(/^-+|-+$/gu, "")
    .slice(0, 63);
}

/** User-owned IDs are deterministic, while non-ASCII and colliding labels cannot collapse together. */
function userReferenceId(label: string, used: Set<string>) {
  const base = referenceSlugBase(label);
  const hasNonAscii = /[^\x00-\x7f]/u.test(label);
  const hash = stableLabelHash(label);
  const prefix = base || "label";
  let candidate = hasNonAscii || !base ? `label-${hash}` : base;
  if (!used.has(candidate)) {
    used.add(candidate);
    return candidate;
  }
  candidate = `${prefix}-${hash}`.slice(0, 255);
  let suffix = 2;
  while (used.has(candidate)) {
    candidate = `${prefix}-${hash}-${suffix}`.slice(0, 255);
    suffix += 1;
  }
  used.add(candidate);
  return candidate;
}

function updateLocationReference(value: unknown, previousLabel: string, nextLabel: string) {
  const existing = isRecord(value) ? value : {};
  if (previousLabel === nextLabel && stringValue(existing.label) === nextLabel) return existing;
  return {
    scheme: "connectmd-user-location",
    id: userReferenceId(nextLabel, new Set()),
    label: nextLabel
  };
}

function updateLabelReferences(value: unknown, labels: string[], scheme: string) {
  const existing = Array.isArray(value) ? value.filter(isRecord) : [];
  const used = new Set<string>();
  return labels.map((label) => {
    const match = existing.find((item) => stringValue(item.label) === label);
    if (match) {
      if (stringValue(match.scheme) === scheme) used.add(stringValue(match.id));
      return match;
    }
    return { scheme, id: userReferenceId(label, used), label };
  });
}

function cleanLabels(values: string[]) {
  const seen = new Set<string>();
  return values.reduce<string[]>((labels, value) => {
    const label = value.trim();
    const key = label.normalize("NFKC").toLocaleLowerCase();
    if (!label || seen.has(key)) return labels;
    seen.add(key);
    labels.push(label);
    return labels;
  }, []);
}

function updateSingleReference(value: unknown, previousLabel: string, nextLabel: string, scheme: string) {
  const existing = isRecord(value) ? value : {};
  if (previousLabel === nextLabel && stringValue(existing.label) === nextLabel) return existing;
  return { scheme, id: userReferenceId(nextLabel, new Set()), label: nextLabel };
}

function updateLanguageReferences(value: unknown, labels: string[], proficiency: HumanFields["languageProficiency"]) {
  const existing = Array.isArray(value) ? value.filter(isRecord) : [];
  const used = new Set<string>();
  return labels.map((label) => {
    const match = existing.find((item) => stringValue(item.label) === label);
    if (match) {
      if (stringValue(match.scheme) === "connectmd-user-language") used.add(stringValue(match.id));
      return match;
    }
    const reference: Record<string, unknown> = { scheme: "connectmd-user-language", id: userReferenceId(label, used), label };
    if (proficiency) reference.proficiency = proficiency;
    return reference;
  });
}

function updateOrganizationReferences(value: unknown, labels: string[], relationship: HumanFields["organizationRelationship"]) {
  const existing = Array.isArray(value) ? value.filter(isRecord) : [];
  const used = new Set<string>();
  return labels.map((label) => {
    const match = existing.find((item) => stringValue(item.label) === label);
    if (match) {
      if (stringValue(match.scheme) === "connectmd-user-organization") used.add(stringValue(match.id));
      return match;
    }
    return { scheme: "connectmd-user-organization", id: userReferenceId(label, used), label, relationship };
  });
}

function updateAvailability(value: unknown, current: HumanFields, next: HumanFields) {
  const existing = isRecord(value) ? value : {};
  if (current.availabilityStatus === next.availabilityStatus && current.availableFrom === next.availableFrom) return existing;
  const updated: Record<string, unknown> = { ...existing, status: next.availabilityStatus };
  if (next.availabilityStatus === "available_from") updated.available_from = next.availableFrom;
  else delete updated.available_from;
  return updated;
}

function updatePublicRepresentation(value: unknown, current: HumanFields, next: HumanFields) {
  const existing = isRecord(value) ? value : {};
  if (current.representationStatus === next.representationStatus && current.representative === next.representative) return existing;
  const updated: Record<string, unknown> = { ...existing, status: next.representationStatus };
  if (next.representationStatus === "authorized_representative" || next.representationStatus === "organization") {
    updated.representative = updateSingleReference(existing.representative, current.representative, next.representative, "connectmd-user-representative");
  } else delete updated.representative;
  return updated;
}

function updateContact(value: unknown, current: HumanFields, next: HumanFields) {
  const existing = isRecord(value) ? value : {};
  if (current.contactDisclosure === next.contactDisclosure && current.contactType === next.contactType && current.contactValue === next.contactValue && current.contactLabel === next.contactLabel) return existing;
  const updated: Record<string, unknown> = { ...existing, disclosure: next.contactDisclosure };
  if (next.contactDisclosure !== "public") return { ...updated, channels: [] };
  const channels = Array.isArray(existing.channels) ? existing.channels.filter(isRecord) : [];
  const channel: Record<string, unknown> = { type: next.contactType, value: next.contactValue };
  if (next.contactLabel) channel.label = next.contactLabel;
  return { ...updated, channels: [channel, ...channels.slice(1)] };
}

function bodyForKind(body: string, kind: DocumentKind, fields: HumanFields) {
  const sections = sectionEntries(body);
  const consumed = new Set<number>();
  const take = (headings: string[], fallback: string) => {
    const content = sections
      .map((section, index) => ({ section, index }))
      .filter(({ section }) => headings.includes(section.heading))
      .map(({ section, index }) => { consumed.add(index); return section.content; })
      .filter(Boolean)
      .join("\n\n");
    return content || fallback;
  };
  const h1 = scanMarkdownHeadings(body).find((heading) => heading.level === 1);
  const firstSectionStart = sections[0]?.start ?? body.length;
  const preamble = h1 ? body.slice(h1.end, firstSectionStart).trim() : "";
  const narrative = take(kind === "profile" ? ["About", "Summary"] : ["Summary", "About"], fields.narrative || (kind === "profile" ? "Write a concise introduction." : "Write a concise professional summary."));
  const required = kind === "profile"
    ? [
        ["About", [preamble, narrative].filter(Boolean).join("\n\n")],
        ["Experience", take(["Experience"], "Describe the impact, scope, and outcomes of your work.")],
        ["Skills", take(["Skills"], `- ${fields.skills[0] ?? "Strategy"}`)]
      ]
    : [
        ["Summary", [preamble, narrative].filter(Boolean).join("\n\n")],
        ["Experience", take(["Experience"], "Describe the impact, scope, and outcomes of your work.")],
        ["Education", take(["Education"], "Add your most relevant education or credentials.")],
        ["Skills", take(["Skills"], `- ${fields.skills[0] ?? "Strategy"}`)]
      ];
  const extras = sections.filter((_, index) => !consumed.has(index));
  return [
    `# ${fields.name.trim() || "Your Name"}`,
    ...required.map(([heading, content]) => `## ${heading}\n\n${content}`),
    ...extras.map((section) => `## ${section.heading}\n\n${section.content}`)
  ].join("\n\n");
}

export function markdownBody(markdown: string) {
  return splitFrontmatter(markdown).body;
}
