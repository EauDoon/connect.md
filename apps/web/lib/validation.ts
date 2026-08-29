import { type DocumentKind, frontmatterParseIssue, scanMarkdownHeadings, scanMarkdownHeadingSyntaxIssues, splitFrontmatter } from "@/lib/markdown";

export type ValidationIssue = {
  level: "error" | "warning";
  message: string;
};

export const SCHEMA_LIMITS = {
  name: 160,
  title: 160,
  location: 160,
  headline: 280,
  skills: 50,
  skill: 80,
  occupations: 20,
  industries: 20,
  v2Skills: 100,
  languages: 30,
  openTo: 20,
  organizations: 50,
  contactChannels: 20
} as const;

function validateStructuredText(issues: ValidationIssue[], value: unknown, label: string, maxLength: number, required = true) {
  if (typeof value !== "string" || (required && !value.trim())) {
    issues.push({ level: "error", message: `${label} is required in frontmatter.` });
    return;
  }
  if (value !== value.trim()) issues.push({ level: "error", message: `${label} cannot have leading or trailing whitespace.` });
  if (/[\r\n]/u.test(value)) issues.push({ level: "error", message: `${label} cannot contain line breaks.` });
  if (value.length > maxLength) issues.push({ level: "error", message: `${label} must be ${maxLength} characters or fewer.` });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateReference(issues: ValidationIssue[], value: unknown, label: string) {
  if (!isRecord(value)) {
    issues.push({ level: "error", message: `${label} must be a structured scheme/id/label object.` });
    return;
  }
  validateStructuredText(issues, value.scheme, `${label}.scheme`, 80);
  validateStructuredText(issues, value.id, `${label}.id`, 255);
  validateStructuredText(issues, value.label, `${label}.label`, 160);
  if (typeof value.scheme === "string" && !/^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$/u.test(value.scheme)) issues.push({ level: "error", message: `${label}.scheme is invalid.` });
  if (typeof value.id === "string" && !/^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,253}[A-Za-z0-9])?$/u.test(value.id)) issues.push({ level: "error", message: `${label}.id is invalid.` });
}

function validateReferenceList(issues: ValidationIssue[], value: unknown, label: string, required = false, maxItems = Number.POSITIVE_INFINITY) {
  if (!Array.isArray(value) || (required && value.length === 0)) {
    issues.push({ level: "error", message: `${label} must be ${required ? "a non-empty" : "an"} YAML list of structured references.` });
    return;
  }
  if (value.length > maxItems) issues.push({ level: "error", message: `${label} can contain at most ${maxItems} items.` });
  const identities = new Set<string>();
  value.forEach((item, index) => validateReference(issues, item, `${label}[${index}]`));
  value.filter(isRecord).forEach((item) => {
    const identity = `${stringValue(item.scheme)}:${stringValue(item.id)}`;
    if (identities.has(identity)) issues.push({ level: "error", message: `${label} cannot repeat a scheme/id reference.` });
    identities.add(identity);
  });
}

function stringValue(value: unknown) { return typeof value === "string" ? value : ""; }

function validateLanguageList(issues: ValidationIssue[], value: unknown) {
  validateReferenceList(issues, value, "languages", false, SCHEMA_LIMITS.languages);
  if (!Array.isArray(value)) return;
  value.filter(isRecord).forEach((item, index) => {
    if (!["basic", "conversational", "professional", "native_or_bilingual"].includes(String(item.proficiency))) {
      issues.push({ level: "error", message: `languages[${index}].proficiency is invalid.` });
    }
  });
}

function validateOrganizationList(issues: ValidationIssue[], value: unknown) {
  validateReferenceList(issues, value, "organizations", false, SCHEMA_LIMITS.organizations);
  if (!Array.isArray(value)) return;
  value.filter(isRecord).forEach((item, index) => {
    if (!["current_employer", "past_employer", "founder", "member", "education", "client", "other"].includes(String(item.relationship))) {
      issues.push({ level: "error", message: `organizations[${index}].relationship is invalid.` });
    }
  });
}

function isStarterReference(value: unknown, scheme: string, id: string, label: string) {
  return isRecord(value)
    && value.scheme === scheme
    && value.id === id
    && value.label === label;
}

function validatePublicStarterPlaceholders(issues: ValidationIssue[], attributes: Record<string, unknown>) {
  if (attributes.visibility !== "public") return;
  if (Array.isArray(attributes.occupations) && attributes.occupations.some((value) => isStarterReference(value, "connectmd-user-occupation", "unspecified-occupation", "Unspecified occupation"))) {
    issues.push({ level: "error", message: "Replace the default Unspecified occupation before public publication." });
  }
  if (isStarterReference(attributes.location, "connectmd-user-location", "unspecified-location", "Unspecified location")) {
    issues.push({ level: "error", message: "Replace the default Unspecified location before public publication." });
  }
  if (Array.isArray(attributes.skills) && attributes.skills.some((value) => isStarterReference(value, "connectmd-user-skill", "unspecified-skill", "Unspecified skill"))) {
    issues.push({ level: "error", message: "Replace the default Unspecified skill before public publication." });
  }
}

const SERVER_OWNED_FRONTMATTER_FIELDS = ["id", "owner_id", "version", "updated_at"] as const;
const SHARED_FRONTMATTER_FIELDS = ["schema", "schema_version", "name", "headline", "location", "skills", "visibility"] as const;
const V2_FRONTMATTER_FIELDS = ["occupations", "industries", "languages", "seniority", "work_modes", "availability", "open_to", "organizations", "public_representation", "contact"] as const;

function allowedFrontmatterKeys(kind: DocumentKind, schemaVersion: 1 | 2) {
  const identity = kind === "profile" ? ["handle"] : ["slug", "title"];
  const versioned = schemaVersion === 2 ? V2_FRONTMATTER_FIELDS : [];
  return new Set<string>([...SHARED_FRONTMATTER_FIELDS, ...identity, ...versioned, ...SERVER_OWNED_FRONTMATTER_FIELDS]);
}

function validateUnknownFrontmatterKeys(issues: ValidationIssue[], attributes: Record<string, unknown>, kind: DocumentKind, schemaVersion: 1 | 2) {
  const allowed = allowedFrontmatterKeys(kind, schemaVersion);
  const unknown = Object.keys(attributes).filter((key) => !allowed.has(key));
  if (unknown.length) issues.push({ level: "error", message: `unknown frontmatter fields: ${unknown.join(", ")}.` });
}

function validateOptionalServerFields(issues: ValidationIssue[], attributes: Record<string, unknown>) {
  if (Object.hasOwn(attributes, "id") && (typeof attributes.id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu.test(attributes.id))) {
    issues.push({ level: "error", message: "id must be a UUID." });
  }
  if (Object.hasOwn(attributes, "owner_id") && (typeof attributes.owner_id !== "string" || !attributes.owner_id.trim() || attributes.owner_id.length > 255)) {
    issues.push({ level: "error", message: "owner_id is invalid." });
  }
  if (Object.hasOwn(attributes, "version") && (typeof attributes.version !== "number" || !Number.isInteger(attributes.version) || attributes.version < 1)) {
    issues.push({ level: "error", message: "version must be a positive integer." });
  }
  if (Object.hasOwn(attributes, "updated_at") && (typeof attributes.updated_at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(attributes.updated_at))) {
    issues.push({ level: "error", message: "updated_at must be a UTC timestamp." });
  }
}

export function validateDraft(markdown: string, kind: DocumentKind): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const parseIssue = frontmatterParseIssue(markdown);
  if (parseIssue) {
    issues.push({
      level: "error",
      message: parseIssue === "Frontmatter delimiters are missing." ? "Start with YAML frontmatter delimited by --- lines." : parseIssue
    });
    return issues;
  }
  const { attributes, body } = splitFrontmatter(markdown);

  if (attributes.schema !== `connect.md/${kind}`) {
    issues.push({ level: "error", message: `schema must be connect.md/${kind}.` });
  }
  const schemaVersion = attributes.schema_version;
  if (schemaVersion !== 1 && schemaVersion !== 2) {
    issues.push({ level: "error", message: "schema_version must be the supported integer 1 or 2." });
  } else {
    validateUnknownFrontmatterKeys(issues, attributes, kind, schemaVersion);
    validateOptionalServerFields(issues, attributes);
  }
  validateStructuredText(issues, attributes.name, "name", SCHEMA_LIMITS.name);
  if (typeof attributes.name === "string" && attributes.name.endsWith("#")) issues.push({ level: "error", message: "name cannot end with # because it cannot be represented exactly as a Markdown title." });
  validateStructuredText(issues, attributes.headline, "headline", SCHEMA_LIMITS.headline);
  if (schemaVersion === 2) validateReference(issues, attributes.location, "location");
  else validateStructuredText(issues, attributes.location, "location", SCHEMA_LIMITS.location);
  const identifierPattern = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/u;
  if (kind === "profile" && (typeof attributes.handle !== "string" || !identifierPattern.test(attributes.handle))) {
    issues.push({ level: "error", message: "Profiles need a lowercase handle using letters, numbers, and hyphens." });
  }
  if (kind === "resume" && (typeof attributes.slug !== "string" || !identifierPattern.test(attributes.slug))) {
    issues.push({ level: "error", message: "Resumes need a lowercase slug using letters, numbers, and hyphens." });
  }
  if (kind === "resume") validateStructuredText(issues, attributes.title, "title", SCHEMA_LIMITS.title);
  if (attributes.visibility !== "private" && attributes.visibility !== "public") {
    issues.push({ level: "error", message: "visibility must be private or public." });
  }
  if (schemaVersion === 2) {
    validateReferenceList(issues, attributes.occupations, "occupations", true, SCHEMA_LIMITS.occupations);
    validateReferenceList(issues, attributes.industries, "industries", false, SCHEMA_LIMITS.industries);
    validateReferenceList(issues, attributes.skills, "skills", true, SCHEMA_LIMITS.v2Skills);
    validateLanguageList(issues, attributes.languages);
    validateReference(issues, attributes.seniority, "seniority");
    validateReferenceList(issues, attributes.open_to, "open_to", false, SCHEMA_LIMITS.openTo);
    validateOrganizationList(issues, attributes.organizations);
    if (!Array.isArray(attributes.work_modes) || attributes.work_modes.length > 3 || new Set(attributes.work_modes.map(String)).size !== attributes.work_modes.length || attributes.work_modes.some((mode) => !["on_site", "hybrid", "remote"].includes(String(mode)))) {
      issues.push({ level: "error", message: "work_modes may contain only distinct on_site, hybrid, or remote values." });
    }
    const availability = isRecord(attributes.availability) ? attributes.availability : {};
    if (!["available_now", "available_from", "not_available", "not_disclosed"].includes(String(availability.status))) {
      issues.push({ level: "error", message: "availability.status is invalid." });
    }
    if (availability.status === "available_from" && (typeof availability.available_from !== "string" || !/^\d{4}-\d{2}-\d{2}$/u.test(availability.available_from))) {
      issues.push({ level: "error", message: "availability.available_from is required for available_from status." });
    }
    const representation = isRecord(attributes.public_representation) ? attributes.public_representation : {};
    if (!["self", "authorized_representative", "organization", "not_disclosed"].includes(String(representation.status))) {
      issues.push({ level: "error", message: "public_representation.status is invalid." });
    }
    if (["authorized_representative", "organization"].includes(String(representation.status))) {
      validateReference(issues, representation.representative, "public_representation.representative");
    }
    const contact = isRecord(attributes.contact) ? attributes.contact : {};
    if (!["none", "platform_only", "public"].includes(String(contact.disclosure))) {
      issues.push({ level: "error", message: "contact.disclosure is invalid." });
    }
    if (contact.disclosure === "public" && (!Array.isArray(contact.channels) || contact.channels.length === 0)) {
      issues.push({ level: "error", message: "Public contact disclosure requires at least one channel." });
    }
    if (Array.isArray(contact.channels) && contact.channels.length > SCHEMA_LIMITS.contactChannels) issues.push({ level: "error", message: `contact.channels can contain at most ${SCHEMA_LIMITS.contactChannels} items.` });
    validatePublicStarterPlaceholders(issues, attributes);
  } else if (!Array.isArray(attributes.skills) || !attributes.skills.length || attributes.skills.some((skill) => typeof skill !== "string" || !skill.trim())) {
    issues.push({ level: "error", message: "skills must be a non-empty YAML list of text values." });
  } else {
    if (attributes.skills.length > SCHEMA_LIMITS.skills) issues.push({ level: "error", message: `skills can contain at most ${SCHEMA_LIMITS.skills} items.` });
    if (attributes.skills.some((skill) => skill !== skill.trim())) issues.push({ level: "error", message: "skills cannot contain leading or trailing whitespace." });
    if (attributes.skills.some((skill) => /[\r\n]/u.test(skill))) issues.push({ level: "error", message: "skills cannot contain line breaks." });
    if (attributes.skills.some((skill) => skill.length > SCHEMA_LIMITS.skill)) issues.push({ level: "error", message: `Each skill must be ${SCHEMA_LIMITS.skill} characters or fewer.` });
  }
  const expectedTitle = typeof attributes.name === "string" ? attributes.name.trim() : "";
  if (scanMarkdownHeadingSyntaxIssues(body).length) {
    issues.push({ level: "error", message: "Markdown headings require exactly one space after the # markers and no surrounding heading whitespace." });
  }
  const scannedHeadings = scanMarkdownHeadings(body);
  const h1Headings = scannedHeadings.filter((heading) => heading.level === 1).map((heading) => heading.text);
  if (h1Headings.length !== 1 || h1Headings[0] !== expectedTitle) {
    issues.push({ level: "error", message: "The document needs exactly one level-one heading matching name." });
  }
  const requiredHeadings = kind === "profile" ? ["About", "Experience", "Skills"] : ["Summary", "Experience", "Education", "Skills"];
  const h2Headings = scannedHeadings.filter((heading) => heading.level === 2).map((heading) => heading.text);
  const requiredPositions = requiredHeadings.map((heading) => h2Headings.reduce<number[]>((positions, current, index) => current === heading ? [...positions, index] : positions, []));
  if (requiredPositions.some((positions) => positions.length !== 1)) {
    issues.push({ level: "error", message: `Required headings must appear exactly once: ${requiredHeadings.join(", ")}.` });
  } else if (requiredPositions.some((positions, index) => index > 0 && positions[0] < requiredPositions[index - 1][0])) {
    issues.push({ level: "error", message: `Required headings must follow this order: ${requiredHeadings.join(" → ")}.` });
  }
  if (/<(?:script|iframe|object|embed)\b/i.test(body)) {
    issues.push({ level: "warning", message: "Unsafe HTML is removed in the preview and public renderer." });
  }
  if (!issues.length) issues.push({ level: "warning", message: "Client validation passed. Download locally; this site does not upload or publish." });
  return issues;
}

export function hasValidationErrors(issues: ValidationIssue[]): boolean {
  return issues.some((issue) => issue.level === "error");
}
