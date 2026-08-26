import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { readFileSync } from "node:fs";

const E2E_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(E2E_DIRECTORY, "public-fixtures.json");
const PUBLIC_OWNER_ID = "00000000-0000-4000-8000-000000000001";
const PUBLIC_DOCUMENT_IDENTIFIER_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/u;
const SEARCH_FILTER_VALUE_PATTERN = /^tx1_[0-9a-f]{64}$/u;
const SEARCH_AGENT_HANDLE_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/u;
const PUBLIC_POST_HANDLE_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/u;
const PRIVATE_FIXTURE_KEYS = new Set([
  "subject",
  "clerk_subject",
  "owner_subject",
  "token",
  "authorization",
  "api_key",
  "grant_secret",
]);
const KNOWN_RAW_TEST_SUBJECTS = new Set(["user_test", "user_123", "user_a", "user_b", "user_example"]);
const MAX_FIXTURE_PRIVACY_DEPTH = 32;
const MAX_FIXTURE_PRIVACY_NODES = 20_000;
const MAX_FIXTURE_STRING_LENGTH = 1_048_576;

export const PROTOCOL_PATHS = [
  "/agent-readme.md",
  "/llms.txt",
  "/llms-full.txt",
  "/openapi.json",
  "/.well-known/agent-card.json",
];
const PROTOCOL_REQUIRED_HEADERS = {
  "/agent-readme.md": ["content-type", "x-request-id"],
  "/llms.txt": ["content-type", "x-request-id"],
  "/llms-full.txt": ["content-type", "x-request-id"],
  "/openapi.json": ["content-type", "x-request-id"],
  "/.well-known/agent-card.json": [
    "cache-control",
    "content-type",
    "etag",
    "x-request-id",
  ],
};

export const API_EXACT_PATHS = new Set([
  "/openapi.json",
  "/agent-readme.md",
  "/llms.txt",
  "/llms-full.txt",
  "/.well-known/agent-card.json",
  "/mcp",
  "/a2a",
  "/docs",
  "/redoc",
  "/v1",
]);

export const BROWSER_CREDENTIAL_HEADER_NAMES = [
  "authorization",
  "cookie",
  "proxy-authorization",
];

export function browserCredentialHeaderKind(headers) {
  for (const name of Object.keys(headers)) {
    const normalized = name.toLowerCase();
    if (BROWSER_CREDENTIAL_HEADER_NAMES.includes(normalized)) return normalized;
  }
  return null;
}

export function decodeProtocolBody(entry, pathname) {
  if (typeof entry.body_base64 !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/u.test(entry.body_base64)) {
    throw new Error(`invalid protocol body encoding for ${pathname}`);
  }
  if (entry.body_base64.length % 4 !== 0) throw new Error(`invalid protocol body encoding for ${pathname}`);
  const body = Buffer.from(entry.body_base64, "base64");
  if (body.toString("base64") !== entry.body_base64) throw new Error(`invalid protocol body encoding for ${pathname}`);
  const digest = createHash("sha256").update(body).digest("hex");
  if (entry.sha256 !== digest) throw new Error(`protocol body hash mismatch for ${pathname}`);
  return body;
}

export function validateProtocolManifest(manifest) {
  if (!manifest || typeof manifest !== "object") throw new Error("invalid protocol manifest");
  if (manifest.version !== 1 || manifest.base_url !== "https://connectmd.invalid") {
    throw new Error("invalid protocol manifest profile");
  }
  if (
    manifest.environment !== "development" ||
    manifest.recruiting_enabled !== false ||
    manifest.account_lifecycle_enabled !== false ||
    typeof manifest.evidence_boundary !== "string" ||
    !manifest.evidence_boundary.includes("hermetic current-source fixture parity") ||
    !manifest.evidence_boundary.includes("not live")
  ) {
    throw new Error("invalid protocol manifest evidence profile");
  }
  const responses = manifest.responses;
  if (!responses || typeof responses !== "object" || Array.isArray(responses)) {
    throw new Error("invalid protocol response map");
  }
  const actualPaths = Object.keys(responses).sort();
  if (actualPaths.join("\n") !== [...PROTOCOL_PATHS].sort().join("\n")) {
    throw new Error("protocol response route set drift");
  }
  for (const pathname of PROTOCOL_PATHS) {
    const entry = responses[pathname];
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error(`invalid protocol response for ${pathname}`);
    }
    if (entry.status !== 200 || typeof entry.sha256 !== "string" || !/^[0-9a-f]{64}$/u.test(entry.sha256)) {
      throw new Error(`invalid protocol response metadata for ${pathname}`);
    }
    if (!entry.headers || typeof entry.headers !== "object" || Array.isArray(entry.headers)) {
      throw new Error(`invalid protocol headers for ${pathname}`);
    }
    const headerNames = Object.keys(entry.headers);
    if (headerNames.some((name) => name !== name.toLowerCase() || name === "content-length")) {
      throw new Error(`invalid protocol framing/header for ${pathname}`);
    }
    const expectedHeaderNames = [...PROTOCOL_REQUIRED_HEADERS[pathname]].sort();
    if (headerNames.sort().join("\n") !== expectedHeaderNames.join("\n")) {
      throw new Error(`protocol header set drift for ${pathname}`);
    }
    for (const name of PROTOCOL_REQUIRED_HEADERS[pathname]) {
      if (typeof entry.headers[name] !== "string" || !entry.headers[name]) {
        throw new Error(`missing protocol header ${name} for ${pathname}`);
      }
    }
    decodeProtocolBody(entry, pathname);
  }
  return manifest;
}

function isPlainRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalidFixture() {
  throw new Error("invalid browser fixture");
}

function exactFixtureKeys(value, expected) {
  if (!isPlainRecord(value)) invalidFixture();
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  if (actual.length !== sortedExpected.length || actual.some((key, index) => key !== sortedExpected[index])) {
    invalidFixture();
  }
}

function requiredFixtureText(value, maximum) {
  if (typeof value !== "string" || !value || value.length > maximum) invalidFixture();
  return value;
}

function validFixtureTimestamp(value) {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/u.test(value) &&
    !Number.isNaN(Date.parse(value))
  );
}

export function validateFixturePrivacy(value) {
  const state = { nodes: 0 };
  const visit = (entry, depth) => {
    if (depth > MAX_FIXTURE_PRIVACY_DEPTH || state.nodes >= MAX_FIXTURE_PRIVACY_NODES) invalidFixture();
    state.nodes += 1;
    if (typeof entry === "string") {
      if (
        entry.length > MAX_FIXTURE_STRING_LENGTH ||
        [...KNOWN_RAW_TEST_SUBJECTS].some((subject) => entry.includes(subject))
      ) {
        invalidFixture();
      }
      return;
    }
    if (entry === null || typeof entry === "boolean" || typeof entry === "number") {
      if (typeof entry === "number" && !Number.isFinite(entry)) invalidFixture();
      return;
    }
    if (Array.isArray(entry)) {
      for (const item of entry) visit(item, depth + 1);
      return;
    }
    if (!isPlainRecord(entry) || Object.keys(entry).length > MAX_FIXTURE_PRIVACY_NODES) invalidFixture();
    for (const [key, child] of Object.entries(entry)) {
      const normalizedKey = key.toLowerCase();
      if (PRIVATE_FIXTURE_KEYS.has(normalizedKey)) invalidFixture();
      if (normalizedKey === "owner_id" && child !== PUBLIC_OWNER_ID) invalidFixture();
      visit(child, depth + 1);
    }
  };
  visit(value, 0);
  return value;
}

export function validatePublicDocumentsFixture(inventory, profileDocument, resumeDocument) {
  exactFixtureKeys(inventory, ["items", "next_cursor"]);
  if (inventory.next_cursor !== null || !Array.isArray(inventory.items) || inventory.items.length !== 2) invalidFixture();
  const expected = [
    { kind: "profile", slug: "ada-lovelace", updated_at: profileDocument.updated_at },
    { kind: "resume", slug: "ada-lovelace-resume", updated_at: resumeDocument.updated_at },
  ];
  for (const [index, item] of inventory.items.entries()) {
    exactFixtureKeys(item, ["kind", "slug", "updated_at"]);
    const expectedItem = expected[index];
    if (
      item.kind !== expectedItem.kind ||
      item.slug !== expectedItem.slug ||
      item.updated_at !== expectedItem.updated_at ||
      !validFixtureTimestamp(item.updated_at)
    ) {
      invalidFixture();
    }
  }
}

export function validatePublicPostInventoryFixture(inventory, post) {
  exactFixtureKeys(inventory, ["items", "next_cursor"]);
  if (inventory.next_cursor !== null || !Array.isArray(inventory.items) || inventory.items.length !== 1) invalidFixture();
  const item = inventory.items[0];
  exactFixtureKeys(item, [
    "id",
    "author_profile_handle",
    "title",
    "topics",
    "version",
    "published_at",
    "updated_at",
    "html_url",
    "markdown_url",
    "etag",
  ]);
  requiredFixtureText(item.id, 128);
  const author = requiredFixtureText(item.author_profile_handle, 100);
  if (!PUBLIC_POST_HANDLE_PATTERN.test(author)) invalidFixture();
  const title = requiredFixtureText(item.title, 160);
  if (/[\r\n]/u.test(title)) invalidFixture();
  if (
    !Array.isArray(item.topics) ||
    item.topics.length < 1 ||
    item.topics.length > 10 ||
    item.topics.some((topic) => typeof topic !== "string" || !PUBLIC_POST_HANDLE_PATTERN.test(topic) || topic.length > 49)
  ) {
    invalidFixture();
  }
  if (
    item.version !== 1 ||
    !validFixtureTimestamp(item.published_at) ||
    !validFixtureTimestamp(item.updated_at) ||
    item.html_url !== `/posts/${encodeURIComponent(item.id)}` ||
    item.markdown_url !== `/v1/posts/${encodeURIComponent(item.id)}.md` ||
    typeof item.etag !== "string" ||
    item.etag !== post.etag ||
    item.id !== post.id ||
    item.author_profile_handle !== post.author_profile_handle ||
    item.title !== post.title ||
    JSON.stringify(item.topics) !== JSON.stringify(post.topics) ||
    item.published_at !== post.published_at ||
    item.updated_at !== post.updated_at ||
    item.html_url !== "/posts/fixture-post-field-notes" ||
    item.markdown_url !== post.markdown_url
  ) {
    invalidFixture();
  }
}

function validateRequiredStringArray(record, key, maximum) {
  if (!Object.prototype.hasOwnProperty.call(record, key) || !Array.isArray(record[key])) invalidFixture();
  for (const item of record[key]) requiredFixtureText(item, maximum);
}

function validateRequiredFilterArray(record, key) {
  if (!Object.prototype.hasOwnProperty.call(record, key) || !Array.isArray(record[key])) invalidFixture();
  for (const item of record[key]) {
    if (typeof item !== "string" || !SEARCH_FILTER_VALUE_PATTERN.test(item)) invalidFixture();
  }
}

function validateNullableFilter(record, key) {
  if (!Object.prototype.hasOwnProperty.call(record, key)) invalidFixture();
  if (record[key] !== null && (typeof record[key] !== "string" || !SEARCH_FILTER_VALUE_PATTERN.test(record[key]))) invalidFixture();
}

function validateNullableBoundedText(record, key, maximum) {
  if (!Object.prototype.hasOwnProperty.call(record, key)) invalidFixture();
  if (record[key] !== null) requiredFixtureText(record[key], maximum);
}

function validateSearchAgentIdentities(value) {
  if (!Array.isArray(value) || value.length > 10) invalidFixture();
  for (const identity of value) {
    if (!isPlainRecord(identity)) invalidFixture();
    const handle = requiredFixtureText(identity.handle, 100);
    if (!SEARCH_AGENT_HANDLE_PATTERN.test(handle)) invalidFixture();
    if (!Array.isArray(identity.capabilities) || identity.capabilities.length !== 1 || identity.capabilities[0] !== "internal_contact_request") invalidFixture();
  }
}

function validateSearchHit(hit) {
  if (!isPlainRecord(hit)) invalidFixture();
  const kind = hit.kind;
  if (kind !== "profile" && kind !== "resume") invalidFixture();
  const identifier = requiredFixtureText(hit.identifier, 63);
  if (!PUBLIC_DOCUMENT_IDENTIFIER_PATTERN.test(identifier)) invalidFixture();
  requiredFixtureText(hit.id, 336);
  requiredFixtureText(hit.name, 336);
  if (hit.html_url !== (kind === "profile" ? `/p/${encodeURIComponent(identifier)}` : `/r/${encodeURIComponent(identifier)}`)) invalidFixture();
  if (hit.markdown_url !== `/v1/${kind === "profile" ? "profiles" : "resumes"}/${encodeURIComponent(identifier)}.md`) invalidFixture();
  if (Object.prototype.hasOwnProperty.call(hit, "owner_id")) invalidFixture();

  for (const [key, maximum] of [
    ["occupation_ids", 336],
    ["industry_ids", 336],
    ["skill_ids", 336],
    ["language_ids", 336],
    ["seniority_ids", 336],
    ["work_modes", 160],
    ["open_to_ids", 336],
    ["organization_ids", 336],
    ["representative_ids", 336],
    ["open_to", 280],
  ]) {
    validateRequiredStringArray(hit, key, maximum);
  }
  for (const key of [
    "occupation_filter_values",
    "industry_filter_values",
    "skill_filter_values",
    "language_filter_values",
    "seniority_filter_values",
    "open_to_filter_values",
    "organization_filter_values",
    "representative_filter_values",
    "work_mode_filter_values",
  ]) {
    validateRequiredFilterArray(hit, key);
  }
  for (const key of ["location_filter_value", "seniority_filter_value", "representative_filter_value"]) {
    validateNullableFilter(hit, key);
  }
  for (const key of ["location_id", "seniority_id", "representative_id"]) {
    validateNullableBoundedText(hit, key, 336);
  }
  validateSearchAgentIdentities(hit.agent_identities);
  if (kind === "resume" && hit.agent_identities.length > 0) invalidFixture();
}

export function validateSearchFixture(search) {
  if (
    !isPlainRecord(search) ||
    search.mode !== "projection" ||
    search.offset !== 0 ||
    search.limit !== 20 ||
    typeof search.total !== "number" ||
    !Number.isInteger(search.total) ||
    search.total < 0 ||
    !Array.isArray(search.hits) ||
    search.total !== search.hits.length ||
    search.indexing_available !== true ||
    search.warning !== null ||
    search.next_cursor !== null ||
    search.search_revision !== null ||
    search.complete !== false ||
    !isPlainRecord(search.facets) ||
    !isPlainRecord(search.taxonomy_facets) ||
    Object.keys(search.taxonomy_facets).length !== 0 ||
    !isPlainRecord(search.facet_truncated) ||
    Object.keys(search.facet_truncated).length !== 0
  ) {
    invalidFixture();
  }
  for (const hit of search.hits) validateSearchHit(hit);
}

export function validateEmptySearchFixture(search) {
  validateSearchFixture(search);
  if (search.total !== 0 || search.hits.length !== 0) invalidFixture();
}

export function validateSearchUnavailableFixture(unavailable) {
  if (
    !isPlainRecord(unavailable) ||
    Object.keys(unavailable).sort().join(",") !== "body,status" ||
    unavailable.status !== 503 ||
    unavailable.body !== '{"detail":"fixture search unavailable"}'
  ) {
    invalidFixture();
  }
}

export function loadFixtures() {
  const fixture = JSON.parse(readFileSync(FIXTURE_PATH, "utf8"));
  return validateFixturePayload(fixture);
}

export function validateFixturePayload(fixture) {
  if (!isPlainRecord(fixture)) invalidFixture();
  validateFixturePrivacy(fixture);
  if (
    typeof fixture.profileMarkdown !== "string" ||
    typeof fixture.resumeMarkdown !== "string" ||
    typeof fixture.postMarkdown !== "string" ||
    !fixture.protocolManifest ||
    !fixture.publicDocuments ||
    !fixture.profileDocument ||
    fixture.profileDocument.markdown_url !== "/v1/profiles/ada-lovelace.md" ||
    !fixture.resumeDocument ||
    fixture.resumeDocument.markdown_url !== "/v1/resumes/ada-lovelace-resume.md" ||
    !fixture.post ||
    fixture.post.markdown_url !== "/v1/posts/fixture-post-field-notes.md" ||
    !fixture.posts ||
    !fixture.search ||
    !Array.isArray(fixture.search.hits) ||
    !fixture.searchEmpty ||
    !fixture.searchUnavailable
  ) {
    invalidFixture();
  }
  validateDocumentFixture(
    fixture.profileDocument,
    fixture.profileMarkdown,
    "profile",
    "ada-lovelace",
    "/v1/profiles/ada-lovelace.md",
  );
  validateDocumentFixture(
    fixture.resumeDocument,
    fixture.resumeMarkdown,
    "resume",
    "ada-lovelace-resume",
    "/v1/resumes/ada-lovelace-resume.md",
  );
  validatePostFixture(fixture.post, fixture.postMarkdown);
  validatePublicDocumentsFixture(fixture.publicDocuments, fixture.profileDocument, fixture.resumeDocument);
  validatePublicPostInventoryFixture(fixture.posts, fixture.post);
  validateSearchFixture(fixture.search);
  validateEmptySearchFixture(fixture.searchEmpty);
  validateSearchUnavailableFixture(fixture.searchUnavailable);
  validateProtocolManifest(fixture.protocolManifest);
  return fixture;
}

export function representationMetadata(markdown) {
  if (typeof markdown !== "string") throw new Error("invalid canonical Markdown fixture");
  const digest = createHash("sha256").update(markdown, "utf8").digest();
  const sha256 = digest.toString("hex");
  return {
    sha256,
    etag: `"sha256-${sha256}"`,
    contentDigest: `sha-256=:${digest.toString("base64")}:`,
  };
}

export function validateDocumentFixture(document, markdown, kind, identifier, markdownPath) {
  if (
    !document ||
    document.id !== `fixture-${kind}-ada-lovelace` ||
    document.owner_id !== PUBLIC_OWNER_ID ||
    document.kind !== kind ||
    document.identifier !== identifier ||
    document.visibility !== "public" ||
    document.version !== 1 ||
    typeof document.updated_at !== "string" ||
    Number.isNaN(Date.parse(document.updated_at)) ||
    document.markdown_url !== markdownPath ||
    typeof document.etag !== "string" ||
    typeof markdown !== "string" ||
    Buffer.byteLength(markdown, "utf8") > 131072
  ) {
    throw new Error(`invalid public ${kind} fixture`);
  }
  const metadata = representationMetadata(markdown);
  if (document.etag !== metadata.etag) throw new Error(`public ${kind} fixture digest drift`);
}

export function validatePostFixture(post, markdown) {
  if (
    !post ||
    post.id !== "fixture-post-field-notes" ||
    post.author_profile_handle !== "ada-lovelace" ||
    post.title !== "Field notes on canonical Markdown" ||
    !Array.isArray(post.topics) ||
    post.version !== 1 ||
    typeof post.published_at !== "string" ||
    Number.isNaN(Date.parse(post.published_at)) ||
    typeof post.updated_at !== "string" ||
    Number.isNaN(Date.parse(post.updated_at)) ||
    post.markdown !== markdown ||
    post.markdown_url !== "/v1/posts/fixture-post-field-notes.md" ||
    typeof post.etag !== "string" ||
    typeof markdown !== "string" ||
    Buffer.byteLength(markdown, "utf8") > 10240
  ) {
    throw new Error("invalid public post fixture");
  }
  const metadata = representationMetadata(markdown);
  if (post.etag !== metadata.etag) throw new Error("public post fixture digest drift");
  const lines = markdown.split("\n");
  if (lines[0] !== "---") throw new Error("public post fixture frontmatter missing");
  const end = lines.indexOf("---", 1);
  if (end < 0) throw new Error("public post fixture frontmatter unclosed");
  const fields = new Map();
  for (const line of lines.slice(1, end)) {
    const separator = line.indexOf(":");
    if (separator > 0) fields.set(line.slice(0, separator), line.slice(separator + 1).trim());
  }
  if (
    fields.get("id") !== post.id ||
    fields.get("author_profile_handle") !== post.author_profile_handle ||
    fields.get("version") !== "1" ||
    fields.get("published_at") !== post.published_at ||
    fields.get("title") !== post.title
  ) {
    throw new Error("public post fixture frontmatter drift");
  }
}
