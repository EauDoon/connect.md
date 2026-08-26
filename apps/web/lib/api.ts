import { type DocumentKind } from "@/lib/markdown";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly code?: "configuration" | "offline" | "unauthorized" | "not_found" | "server" | "request"
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export function presentApiError(error: unknown) {
  if (!(error instanceof ApiRequestError)) return "connect.md could not complete that request. No draft was published.";
  if (error.code === "offline") return "You are offline. Reconnect before trying again.";
  if (error.code === "unauthorized") return "Your session is not authorized for this document. Sign in again, then retry.";
  if (error.code === "server") return "connect.md is temporarily unavailable. No draft was published.";
  if (error.code === "configuration") return error.message;
  return error.message;
}

export function presentPublicReadError(error: unknown) {
  if (error instanceof ApiRequestError && error.code === "offline") {
    return "You are offline. Reconnect, then try again.";
  }
  return "Public records are temporarily unavailable. Try again shortly.";
}

export function presentSaveError(error: unknown) {
  if (!(error instanceof ApiRequestError)) return "The save may have completed, but confirmation was lost. Verify the original document before retrying.";
  if (error.code === "offline") return "The save was not sent because you are offline.";
  if (error.code === "configuration") return `The save was not sent. ${error.message}`;
  if (error.status && error.status >= 400 && error.status < 500) return `The save was rejected: ${error.message}`;
  return "The save may have completed, but confirmation was lost. Verify the original document before retrying.";
}

export function presentApiKeyError(error: unknown, operation: "create" | "list" | "revoke") {
  const uncertain = error instanceof ApiRequestError && (error.code === "request" || error.code === "server");
  if (operation === "create" && uncertain) return "API key creation may have completed, but the one-time secret was not received. Refresh the list and revoke any unexpected prefix.";
  if (operation === "revoke" && uncertain) return "API key revocation may have completed. Refresh the list before retrying.";
  const message = presentApiError(error);
  if (operation === "list") return `API keys could not be loaded. ${message}`;
  return message;
}

export type RequestOptions = RequestInit & {
  token?: string | null;
  server?: boolean;
};

export type TokenGetter = () => Promise<string | null>;
export type SubjectGuard = () => boolean;

/**
 * Turn a canonical API Markdown URL into a browser-safe link. The API normally
 * returns root-relative /v1 paths; those must point at the public API origin
 * when the web app and API live on different origins. Invalid schemes are
 * intentionally not rendered as links.
 */
export function publicApiMarkdownUrl(value: string): string | null {
  if (!/^\/v1\/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+\.md$/u.test(value)) return null;
  const base = publicApiBase();
  return base === undefined ? value : base ? `${base}${value}` : null;
}

export const PUBLIC_PROTOCOL_PATHS = [
  "/agent-readme.md",
  "/llms.txt",
  "/llms-full.txt",
  "/openapi.json",
  "/v1/capabilities",
  "/v1/documents",
  "/v1/changes",
  "/v1/agent-outreach",
  "/mcp",
  "/a2a/message:send",
  "/.well-known/agent-card.json",
  "/.well-known/oauth-protected-resource",
  "/.well-known/oauth-protected-resource/mcp",
] as const;

export type PublicProtocolPath = (typeof PUBLIC_PROTOCOL_PATHS)[number];

const publicProtocolPathSet = new Set<string>(PUBLIC_PROTOCOL_PATHS);

/**
 * Resolve a current protocol route without allowing arbitrary API paths or
 * malformed configured origins to become browser-visible links. A blank API
 * base keeps the reverse-proxy-owned route relative; a valid split origin is
 * absolute; invalid configuration falls back to a safe relative route.
 */
export function publicProtocolUrl(path: string, sameOriginFallback?: string): string | null {
  if (!publicProtocolPathSet.has(path)) return null;
  const base = publicApiBase();
  return base ? `${base}${path}` : safeProtocolFallback(path, sameOriginFallback) ?? path;
}

/**
 * Resolve an allowlisted public protocol route without assuming that the web
 * and API processes share an origin. The typed path plus runtime allowlist
 * keep browser-visible discovery links aligned with current API routes.
 */
export function publicDiscoveryUrl(path: PublicProtocolPath, sameOriginFallback?: string): string {
  return publicProtocolUrl(path, sameOriginFallback) ?? path;
}

function safeProtocolFallback(path: string, fallback: string | undefined): string | null {
  if (fallback === undefined) return null;
  if (fallback === path) return fallback;
  const siteOrigin = canonicalSiteOrigin();
  if (siteOrigin === null) return null;
  try {
    return fallback === new URL(path, `${siteOrigin}/`).toString() ? fallback : null;
  } catch {
    return null;
  }
}

function canonicalSiteOrigin(): string | null {
  const configured = process.env.NEXT_PUBLIC_SITE_URL?.trim() || "https://connect.md";
  try {
    const url = new URL(configured);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function publicApiBase() {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (!configured) return undefined;
  try {
    const url = new URL(configured);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export async function withSubjectBoundToken<T>(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, dispatch: (token: string) => Promise<T>) {
  if (!isSubjectCurrent()) throw subjectChangedBeforeDispatch();
  let token: string | null;
  try {
    token = await getToken();
  } catch {
    throw new ApiRequestError("A Clerk token could not be obtained, so the request was not sent.", 401, "unauthorized");
  }
  if (!isSubjectCurrent()) throw subjectChangedBeforeDispatch();
  if (typeof token !== "string" || token.trim().length === 0) {
    throw new ApiRequestError("A Clerk token could not be obtained, so the request was not sent.", 401, "unauthorized");
  }
  return dispatch(token);
}

function subjectChangedBeforeDispatch() {
  return new ApiRequestError("Your signed-in account changed before this authenticated action was sent.", 401, "unauthorized");
}

export type DocumentResponse = {
  id: string;
  kind: DocumentKind;
  owner_id?: string;
  identifier: string;
  visibility: "public" | "private";
  version: number;
  etag: string;
  updated_at: string;
  markdown: string;
  markdown_url: string;
};

export type SearchIndexingState = "ready" | "queued" | "degraded" | "unknown";
export type SaveDocumentResponse = DocumentResponse & { searchIndexing: SearchIndexingState };

export function searchIndexingStateFromHeader(value: string | null): SearchIndexingState {
  const normalized = value?.trim().toLowerCase();
  return normalized === "ready" || normalized === "queued" || normalized === "degraded" ? normalized : "unknown";
}

export const AGENT_SCOPES = ["documents:write", "documents:read", "search:read"] as const;
export type AgentScope = (typeof AGENT_SCOPES)[number];

export type ApiKeyRecord = {
  id: string;
  prefix: string;
  scopes: AgentScope[];
  revoked: boolean;
  created_at: string;
  last_used_at: string | null;
};

type ApiKeyCreateMetadata = Pick<ApiKeyRecord, "id" | "prefix" | "scopes" | "created_at">;
export type CreatedApiKey = ApiKeyCreateMetadata & { key: string; recovery_required: false };
export type RecoveredApiKey = ApiKeyCreateMetadata & { recovery_required: true };
export type ApiKeyCreateResult = CreatedApiKey | RecoveredApiKey;

function apiBase(server = false) {
  const configured = server
    ? process.env.CONNECTMD_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL
    : process.env.NEXT_PUBLIC_API_BASE_URL;

  if (server && !configured) {
    throw new ApiRequestError("CONNECTMD_API_BASE_URL is required to render public documents on the server.", undefined, "configuration");
  }
  return (configured ?? "").replace(/\/$/u, "");
}

function messageFromBody(body: unknown, fallback: string) {
  if (typeof body === "object" && body && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (isRecord(detail) && typeof detail.message === "string") return detail.message;
  }
  if (typeof body === "object" && body && "message" in body) {
    const message = (body as { message?: unknown }).message;
    return typeof message === "string" ? message : fallback;
  }
  return fallback;
}

async function readBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return response.json().catch(() => null);
  return response.text().catch(() => "");
}

export async function apiResponse(path: string, options: RequestOptions = {}) {
  if (!options.server && typeof navigator !== "undefined" && navigator.onLine === false) {
    throw new ApiRequestError("You appear to be offline. Reconnect before contacting connect.md.", undefined, "offline");
  }

  const headers = new Headers(options.headers);
  headers.set("Accept", headers.get("Accept") ?? "application/json");
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);

  try {
    return await fetch(`${apiBase(options.server)}${path}`, {
      ...options,
      headers,
      cache: options.server ? "no-store" : options.cache
    });
  } catch {
    throw new ApiRequestError("connect.md could not be reached. Try again shortly.", undefined, "request");
  }
}

export async function apiRequestWithMetadata<T>(path: string, options: RequestOptions = {}) {
  const response = await apiResponse(path, options);

  const body = await readBody(response);
  if (!response.ok) {
    const code = response.status === 401 || response.status === 403
      ? "unauthorized"
      : response.status === 404
        ? "not_found"
        : response.status >= 500
          ? "server"
          : "request";
    throw new ApiRequestError(messageFromBody(body, `The API returned ${response.status}.`), response.status, code);
  }
  return { body: body as T, headers: response.headers, status: response.status };
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return (await apiRequestWithMetadata<T>(path, options)).body;
}

export async function ingestDocument(file: File, kind: DocumentKind, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, signal?: AbortSignal) {
  const form = new FormData();
  form.set("file", file);
  form.set("target_schema", `connect.md/${kind}`);
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<Record<string, unknown>>("/v1/ingest", { method: "POST", body: form, token, signal }));
}

export function markdownFromIngestResponse(response: Record<string, unknown>) {
  if (typeof response.markdown === "string") return response.markdown;
  if (typeof response.draft_markdown === "string") return response.draft_markdown;
  if (isRecord(response.draft) && typeof response.draft.markdown === "string") return response.draft.markdown;
  return null;
}

export function ingestMetadataFromResponse(response: Record<string, unknown>) {
  const warnings = Array.isArray(response.warnings) ? response.warnings.filter((warning): warning is string => typeof warning === "string") : [];
  const provenance = isRecord(response.provenance)
    ? Object.fromEntries(Object.entries(response.provenance).filter((entry): entry is [string, string] => typeof entry[1] === "string"))
    : {};
  return { warnings, provenance };
}

export async function saveDocument(kind: DocumentKind, markdown: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, existing: DocumentResponse | null, signal?: AbortSignal, idempotencyKey?: string) {
  if (existing && existing.kind !== kind) throw new ApiRequestError("The saved document kind does not match the active draft.", 400, "request");
  const collection = kind === "profile" ? "profiles" : "resumes";
  const identifier = existing?.identifier ?? null;
  const path = identifier ? `/v1/${collection}/${encodeURIComponent(identifier)}` : `/v1/${collection}`;
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => {
    const dispatchIdempotencyKey = idempotencyKey ?? crypto.randomUUID();
    return apiRequestWithMetadata<unknown>(path, {
      method: existing ? "PUT" : "POST",
      token,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": dispatchIdempotencyKey,
        ...(existing ? { "If-Match": existing.etag } : {})
      },
      body: JSON.stringify({ markdown }),
      signal
    });
  });
  return {
    ...parseDocumentResponse(response.body),
    searchIndexing: searchIndexingStateFromHeader(response.headers.get("X-Connectmd-Search"))
  } satisfies SaveDocumentResponse;
}

export async function loadDocument(kind: DocumentKind, identifier: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, signal?: AbortSignal) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, async (token) => {
    const collection = kind === "profile" ? "profiles" : "resumes";
    await apiRequest<unknown>(`/v1/${collection}/${encodeURIComponent(identifier)}/versions`, { token, signal });
    if (!isSubjectCurrent()) throw subjectChangedBeforeDispatch();
    const response = await apiRequest<unknown>(`/v1/${collection}/${encodeURIComponent(identifier)}`, { token, signal });
    const document = parseDocumentResponse(response);
    if (document.kind !== kind) throw new ApiRequestError("The API returned the wrong document kind.", undefined, "server");
    return document;
  });
}

export async function fetchPublicProfileMarkdown(handle: string) {
  return apiRequest<string>(`/v1/profiles/${encodeURIComponent(handle)}.md`, {
    server: true,
    headers: { Accept: "text/markdown" }
  });
}

export async function fetchPublicProfile(handle: string) {
  const response = await apiRequest<unknown>(`/v1/profiles/${encodeURIComponent(handle)}`, { server: true });
  const document = parseDocumentResponse(response);
  if (document.kind !== "profile") throw new ApiRequestError("The API returned the wrong document kind.", undefined, "server");
  return document;
}

export async function fetchPublicResumeMarkdown(identifier: string) {
  return apiRequest<string>(`/v1/resumes/${encodeURIComponent(identifier)}.md`, {
    server: true,
    headers: { Accept: "text/markdown" }
  });
}

export async function fetchPublicResume(identifier: string) {
  const response = await apiRequest<unknown>(`/v1/resumes/${encodeURIComponent(identifier)}`, { server: true });
  const document = parseDocumentResponse(response);
  if (document.kind !== "resume") throw new ApiRequestError("The API returned the wrong document kind.", undefined, "server");
  return document;
}

export async function listApiKeys(getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, async (token) => {
    const response = await apiRequest<unknown>("/v1/api-keys", { token });
    if (!Array.isArray(response)) throw new ApiRequestError("The API returned an invalid API key list.", undefined, "server");
    return response.map((record) => parseApiKeyRecord(record));
  });
}

export async function createApiKey(scopes: AgentScope[], idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard): Promise<ApiKeyCreateResult> {
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>("/v1/api-keys", {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ scopes })
  }));
  if (!isRecord(response) || (response.recovery_required !== true && response.recovery_required !== false)) {
    throw new ApiRequestError("The API returned an invalid API-key creation result.", undefined, "server");
  }
  const record = parseApiKeyRecord(response, false);
  if (response.recovery_required === true) {
    if ("key" in response) {
      throw new ApiRequestError("The API returned an invalid API-key recovery result.", undefined, "server");
    }
    return {
      id: record.id,
      prefix: record.prefix,
      scopes: record.scopes,
      created_at: record.created_at,
      recovery_required: true
    } satisfies RecoveredApiKey;
  }
  if (typeof response.key !== "string" || !response.key) {
    throw new ApiRequestError("The API returned an invalid one-time API key.", undefined, "server");
  }
  return { id: record.id, prefix: record.prefix, scopes: record.scopes, created_at: record.created_at, key: response.key, recovery_required: false } satisfies CreatedApiKey;
}

export async function revokeApiKey(id: string, idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/api-keys/${encodeURIComponent(id)}`, {
    method: "DELETE",
    token,
    headers: { "Idempotency-Key": idempotencyKey }
  }));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseDocumentResponse(value: unknown): DocumentResponse {
  if (!isRecord(value)
    || typeof value.id !== "string"
    || (value.kind !== "profile" && value.kind !== "resume")
    || typeof value.identifier !== "string"
    || (value.visibility !== "public" && value.visibility !== "private")
    || typeof value.version !== "number"
    || !Number.isInteger(value.version)
    || typeof value.etag !== "string"
    || !value.etag
    || typeof value.updated_at !== "string"
    || typeof value.markdown !== "string"
    || typeof value.markdown_url !== "string") {
    throw new ApiRequestError("The API returned an invalid document response.", undefined, "server");
  }
  const document = value as DocumentResponse;
  const collection = document.kind === "profile" ? "profiles" : "resumes";
  if (document.markdown_url !== `/v1/${collection}/${encodeURIComponent(document.identifier)}.md`) {
    throw new ApiRequestError("The API returned an invalid document Markdown URL.", undefined, "server");
  }
  return document;
}

function parseApiKeyRecord(value: unknown, requireStatus = true): ApiKeyRecord {
  if (!isRecord(value)
    || typeof value.id !== "string"
    || typeof value.prefix !== "string"
    || !Array.isArray(value.scopes)
    || value.scopes.some((scope) => typeof scope !== "string" || !AGENT_SCOPES.includes(scope as AgentScope))
    || typeof value.created_at !== "string"
    || (requireStatus && typeof value.revoked !== "boolean")
    || (requireStatus && value.last_used_at !== null && typeof value.last_used_at !== "string")) {
    throw new ApiRequestError("The API returned an invalid API key record.", undefined, "server");
  }
  return {
    id: value.id,
    prefix: value.prefix,
    scopes: value.scopes as AgentScope[],
    revoked: requireStatus ? value.revoked as boolean : false,
    created_at: value.created_at,
    last_used_at: requireStatus ? value.last_used_at as string | null : null
  };
}
