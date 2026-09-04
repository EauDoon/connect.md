import { MISSING_FRONTMATTER_ISSUE, type DocumentKind, frontmatterParseIssue, scanMarkdownHeadings, scanMarkdownHeadingSyntaxIssues, splitFrontmatter } from "@/lib/markdown";

export type ValidationIssue = {
  level: "error" | "warning" | "success";
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
  contactChannels: 20,
  contactValue: 320,
  url: 2048,
  vocabularyVersion: 64,
  region: 100,
  city: 100,
  timezone: 64,
  noticeDaysMax: 730,
  hoursPerWeekMax: 168
} as const;

const CONTACT_CHANNEL_TYPES = ["email", "phone", "url", "platform"] as const;
const REFERENCE_KEYS = ["scheme", "id", "label", "version"] as const;
const AVAILABILITY_KEYS = ["status", "available_from", "notice_days", "hours_per_week"] as const;
const REPRESENTATION_KEYS = ["status", "representative", "public_label", "public_url"] as const;
const CONTACT_KEYS = ["disclosure", "channels"] as const;
const CONTACT_CHANNEL_KEYS = ["type", "value", "label"] as const;

function describeType(value: unknown) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "a list";
  if (typeof value === "object") return "an object";
  if (typeof value === "boolean") return "a boolean";
  if (typeof value === "number") return "a number";
  return typeof value;
}

function validateStructuredText(issues: ValidationIssue[], value: unknown, label: string, maxLength: number, required = true) {
  if (value === undefined) {
    if (required) issues.push({ level: "error", message: `${label} is required in frontmatter.` });
    return;
  }
  if (typeof value !== "string") {
    issues.push({ level: "error", message: `${label} must be text, not ${describeType(value)}.` });
    return;
  }
  if (!value.trim()) {
    issues.push({ level: "error", message: required ? `${label} is required in frontmatter.` : `${label} cannot be empty.` });
    return;
  }
  if (value !== value.trim()) issues.push({ level: "error", message: `${label} cannot have leading or trailing whitespace.` });
  if (/[\r\n]/u.test(value)) issues.push({ level: "error", message: `${label} cannot contain line breaks.` });
  if (value.length > maxLength) issues.push({ level: "error", message: `${label} must be ${maxLength} characters or fewer.` });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireObject(issues: ValidationIssue[], value: unknown, label: string): value is Record<string, unknown> {
  if (isRecord(value)) return true;
  issues.push({ level: "error", message: `${label} must be a YAML object, not ${describeType(value)}.` });
  return false;
}

function validateAllowedKeys(issues: ValidationIssue[], value: Record<string, unknown>, label: string, allowed: readonly string[]) {
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unknown.length) issues.push({ level: "error", message: `${label} has unknown fields: ${unknown.join(", ")}.` });
}

function isHttpUrl(value: string) {
  if (/[\s]/u.test(value) || value.length > SCHEMA_LIMITS.url) return false;
  try {
    const parsed = new URL(value);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && Boolean(parsed.hostname);
  } catch {
    return false;
  }
}

function isEmailValue(value: string) {
  return value.length <= SCHEMA_LIMITS.contactValue && /^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(value) && !value.includes("..");
}

function isPhoneValue(value: string) {
  const digits = value.replace(/\D/gu, "");
  return digits.length >= 7 && digits.length <= 15 && /^\+?[0-9](?:[0-9 .()-]*[0-9])$/u.test(value);
}

function validateBoundedInteger(issues: ValidationIssue[], value: unknown, label: string, min: number, max: number) {
  if (value === undefined) return;
  if (typeof value === "boolean" || typeof value !== "number" || !Number.isInteger(value)) {
    issues.push({ level: "error", message: `${label} must be an integer, not ${describeType(value)}.` });
    return;
  }
  if (value < min || value > max) issues.push({ level: "error", message: `${label} must be between ${min} and ${max}.` });
}

function validateHttpUrlField(issues: ValidationIssue[], value: unknown, label: string) {
  if (value === undefined) return;
  if (typeof value !== "string") {
    issues.push({ level: "error", message: `${label} must be text, not ${describeType(value)}.` });
    return;
  }
  if (value.length > SCHEMA_LIMITS.url) issues.push({ level: "error", message: `${label} must be ${SCHEMA_LIMITS.url} characters or fewer.` });
  else if (!isHttpUrl(value)) issues.push({ level: "error", message: `${label} must be an http or https URL.` });
}

function validateVocabularyVersion(issues: ValidationIssue[], value: unknown, label: string) {
  if (value === undefined) return;
  validateStructuredText(issues, value, label, SCHEMA_LIMITS.vocabularyVersion, false);
  if (typeof value === "string" && value.trim() && !/^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$/u.test(value)) {
    issues.push({ level: "error", message: `${label} is invalid.` });
  }
}

function validateReference(issues: ValidationIssue[], value: unknown, label: string, extraKeys: readonly string[] = []) {
  if (!isRecord(value)) {
    issues.push({ level: "error", message: `${label} must be a structured scheme/id/label object.` });
    return;
  }
  validateAllowedKeys(issues, value, label, [...REFERENCE_KEYS, ...extraKeys]);
  validateStructuredText(issues, value.scheme, `${label}.scheme`, 80);
  validateStructuredText(issues, value.id, `${label}.id`, 255);
  validateStructuredText(issues, value.label, `${label}.label`, 160);
  if (typeof value.scheme === "string" && !/^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$/u.test(value.scheme)) issues.push({ level: "error", message: `${label}.scheme is invalid.` });
  if (typeof value.id === "string" && !/^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,253}[A-Za-z0-9])?$/u.test(value.id)) issues.push({ level: "error", message: `${label}.id is invalid.` });
  validateVocabularyVersion(issues, value.version, `${label}.version`);
}

function validateReferenceList(issues: ValidationIssue[], value: unknown, label: string, required = false, maxItems = Number.POSITIVE_INFINITY, extraKeys: readonly string[] = []) {
  if (!Array.isArray(value) || (required && value.length === 0)) {
    issues.push({ level: "error", message: `${label} must be ${required ? "a non-empty" : "an"} YAML list of structured references.` });
    return;
  }
  if (value.length > maxItems) issues.push({ level: "error", message: `${label} can contain at most ${maxItems} items.` });
  const identities = new Set<string>();
  value.forEach((item, index) => validateReference(issues, item, `${label}[${index}]`, extraKeys));
  value.filter(isRecord).forEach((item) => {
    const identity = `${stringValue(item.scheme)}:${stringValue(item.id)}`;
    if (identities.has(identity)) issues.push({ level: "error", message: `${label} cannot repeat a scheme/id reference.` });
    identities.add(identity);
  });
}

function stringValue(value: unknown) { return typeof value === "string" ? value : ""; }

function validateLanguageList(issues: ValidationIssue[], value: unknown) {
  validateReferenceList(issues, value, "languages", false, SCHEMA_LIMITS.languages, ["proficiency"]);
  if (!Array.isArray(value)) return;
  value.filter(isRecord).forEach((item, index) => {
    if (!["basic", "conversational", "professional", "native_or_bilingual"].includes(String(item.proficiency))) {
      issues.push({ level: "error", message: `languages[${index}].proficiency is invalid.` });
    }
  });
}

function validateOrganizationList(issues: ValidationIssue[], value: unknown) {
  validateReferenceList(issues, value, "organizations", false, SCHEMA_LIMITS.organizations, ["relationship", "url"]);
  if (!Array.isArray(value)) return;
  value.filter(isRecord).forEach((item, index) => {
    if (!["current_employer", "past_employer", "founder", "member", "education", "client", "other"].includes(String(item.relationship))) {
      issues.push({ level: "error", message: `organizations[${index}].relationship is invalid.` });
    }
    validateHttpUrlField(issues, item.url, `organizations[${index}].url`);
  });
}

function validateLocation(issues: ValidationIssue[], value: unknown) {
  validateReference(issues, value, "location", ["country_code", "region", "city", "timezone"]);
  if (!isRecord(value)) return;
  if (Object.hasOwn(value, "country_code")) {
    if (typeof value.country_code !== "string" || !/^[A-Z]{2}$/u.test(value.country_code)) {
      issues.push({ level: "error", message: "location.country_code must be a two-letter ISO country code." });
    }
  }
  validateStructuredText(issues, value.region, "location.region", SCHEMA_LIMITS.region, false);
  validateStructuredText(issues, value.city, "location.city", SCHEMA_LIMITS.city, false);
  if (value.timezone !== undefined) {
    validateStructuredText(issues, value.timezone, "location.timezone", SCHEMA_LIMITS.timezone, false);
    if (typeof value.timezone === "string" && value.timezone.trim() && !/^(?:UTC|[A-Za-z_+-]+\/[A-Za-z0-9_+.-]+(?:\/[A-Za-z0-9_+.-]+)?)$/u.test(value.timezone)) {
      issues.push({ level: "error", message: "location.timezone is invalid." });
    }
  }
}

function validateWorkModes(issues: ValidationIssue[], value: unknown) {
  if (!Array.isArray(value)) {
    issues.push({ level: "error", message: `work_modes must be a YAML list, not ${describeType(value)}.` });
    return;
  }
  if (value.length > 3) issues.push({ level: "error", message: "work_modes can contain at most 3 items." });
  if (value.some((mode) => typeof mode !== "string")) {
    issues.push({ level: "error", message: "work_modes may contain only text values on_site, hybrid, or remote." });
    return;
  }
  if (new Set(value).size !== value.length || value.some((mode) => !["on_site", "hybrid", "remote"].includes(mode))) {
    issues.push({ level: "error", message: "work_modes may contain only distinct on_site, hybrid, or remote values." });
  }
}

function validateAvailability(issues: ValidationIssue[], value: unknown) {
  if (!requireObject(issues, value, "availability")) return;
  validateAllowedKeys(issues, value, "availability", AVAILABILITY_KEYS);
  if (!["available_now", "available_from", "not_available", "not_disclosed"].includes(String(value.status))) {
    issues.push({ level: "error", message: "availability.status is invalid." });
  }
  if (value.status === "available_from") {
    if (typeof value.available_from !== "string" || !/^\d{4}-\d{2}-\d{2}$/u.test(value.available_from)) {
      issues.push({ level: "error", message: "availability.available_from is required for available_from status." });
    }
  } else if (Object.hasOwn(value, "available_from")) {
    issues.push({ level: "error", message: "availability.available_from is only allowed when status is available_from." });
  }
  validateBoundedInteger(issues, value.notice_days, "availability.notice_days", 0, SCHEMA_LIMITS.noticeDaysMax);
  validateBoundedInteger(issues, value.hours_per_week, "availability.hours_per_week", 1, SCHEMA_LIMITS.hoursPerWeekMax);
}

function validatePublicRepresentation(issues: ValidationIssue[], value: unknown) {
  if (!requireObject(issues, value, "public_representation")) return;
  validateAllowedKeys(issues, value, "public_representation", REPRESENTATION_KEYS);
  const status = String(value.status);
  if (!["self", "authorized_representative", "organization", "not_disclosed"].includes(status)) {
    issues.push({ level: "error", message: "public_representation.status is invalid." });
  }
  if (status === "authorized_representative" || status === "organization") {
    validateReference(issues, value.representative, "public_representation.representative");
  } else if (Object.hasOwn(value, "representative")) {
    issues.push({ level: "error", message: "public_representation.representative is only allowed for authorized_representative or organization status." });
  }
  validateStructuredText(issues, value.public_label, "public_representation.public_label", SCHEMA_LIMITS.name, false);
  validateHttpUrlField(issues, value.public_url, "public_representation.public_url");
}

function validateContactChannel(issues: ValidationIssue[], value: unknown, label: string) {
  if (!isRecord(value)) {
    issues.push({ level: "error", message: `${label} must be a YAML object with type and value.` });
    return;
  }
  validateAllowedKeys(issues, value, label, CONTACT_CHANNEL_KEYS);
  const type = value.type;
  if (typeof type !== "string" || !CONTACT_CHANNEL_TYPES.includes(type as (typeof CONTACT_CHANNEL_TYPES)[number])) {
    issues.push({ level: "error", message: `${label}.type must be email, phone, url, or platform.` });
  }
  validateStructuredText(issues, value.value, `${label}.value`, SCHEMA_LIMITS.contactValue);
  if (typeof value.value === "string" && value.value.trim() && value.value === value.value.trim() && !/[\r\n]/u.test(value.value)) {
    if (type === "email" && !isEmailValue(value.value)) issues.push({ level: "error", message: `${label}.value must be an email address.` });
    if (type === "url" && !isHttpUrl(value.value)) issues.push({ level: "error", message: `${label}.value must be an http or https URL.` });
    if (type === "phone" && !isPhoneValue(value.value)) issues.push({ level: "error", message: `${label}.value must be a phone number.` });
  }
  validateStructuredText(issues, value.label, `${label}.label`, SCHEMA_LIMITS.name, false);
}

function validateContact(issues: ValidationIssue[], value: unknown) {
  if (!requireObject(issues, value, "contact")) return;
  validateAllowedKeys(issues, value, "contact", CONTACT_KEYS);
  const disclosure = String(value.disclosure);
  if (!["none", "platform_only", "public"].includes(disclosure)) {
    issues.push({ level: "error", message: "contact.disclosure is invalid." });
  }
  if (value.channels === undefined) {
    if (disclosure === "public") issues.push({ level: "error", message: "Public contact disclosure requires at least one channel." });
    return;
  }
  if (!Array.isArray(value.channels)) {
    issues.push({ level: "error", message: `contact.channels must be a YAML list, not ${describeType(value.channels)}.` });
    return;
  }
  if (disclosure !== "public" && value.channels.length > 0) {
    issues.push({ level: "error", message: "contact.channels must be empty unless disclosure is public." });
  }
  if (disclosure === "public" && value.channels.length === 0) {
    issues.push({ level: "error", message: "Public contact disclosure requires at least one channel." });
  }
  if (value.channels.length > SCHEMA_LIMITS.contactChannels) {
    issues.push({ level: "error", message: `contact.channels can contain at most ${SCHEMA_LIMITS.contactChannels} items.` });
  }
  const identities = new Set<string>();
  value.channels.forEach((channel, index) => {
    validateContactChannel(issues, channel, `contact.channels[${index}]`);
    if (!isRecord(channel)) return;
    const identity = `${stringValue(channel.type)}:${stringValue(channel.value)}`;
    if (identities.has(identity)) issues.push({ level: "error", message: "contact.channels cannot repeat a type/value pair." });
    identities.add(identity);
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
      message: parseIssue === MISSING_FRONTMATTER_ISSUE
        ? "Start with YAML frontmatter delimited by --- lines, or restore the starter template."
        : parseIssue
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
  if (schemaVersion !== 2) validateStructuredText(issues, attributes.location, "location", SCHEMA_LIMITS.location);
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
    validateLocation(issues, attributes.location);
    validateReferenceList(issues, attributes.skills, "skills", true, SCHEMA_LIMITS.v2Skills);
    validateLanguageList(issues, attributes.languages);
    validateReference(issues, attributes.seniority, "seniority");
    validateWorkModes(issues, attributes.work_modes);
    validateAvailability(issues, attributes.availability);
    validateReferenceList(issues, attributes.open_to, "open_to", false, SCHEMA_LIMITS.openTo);
    validateOrganizationList(issues, attributes.organizations);
    validatePublicRepresentation(issues, attributes.public_representation);
    validateContact(issues, attributes.contact);
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
  if (!issues.length) issues.push({ level: "success", message: "Client validation passed. Download locally; this site does not upload or publish." });
  return issues;
}

export function hasValidationErrors(issues: ValidationIssue[]): boolean {
  return issues.some((issue) => issue.level === "error");
}

export function hasValidationWarnings(issues: ValidationIssue[]): boolean {
  return issues.some((issue) => issue.level === "warning");
}
