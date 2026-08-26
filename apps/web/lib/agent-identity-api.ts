import { ApiRequestError, apiRequest, apiRequestWithMetadata, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/logical-mutation";

export const AGENT_IDENTITY_MAX_ACTIVE = 10;
export const AGENT_MANDATE_MAX_DAYS = 30;

export type AgentIdentity = {
  handle: string;
  displayName: string;
  description: string;
  profileHandle: string;
  status: "active" | "withdrawn" | "withheld";
  createdAt: string;
  updatedAt: string;
};

export type PublicAgentIdentity = Pick<AgentIdentity, "handle" | "displayName" | "description" | "profileHandle"> & {
  capabilities: ["internal_contact_request"];
};

export type PublicAgentIdentityDirectory = {
  identities: PublicAgentIdentity[];
  nextCursor: string | null;
};

export type AgentMandate = {
  id: string;
  scope: "internal_contact_request";
  status: "active" | "revoked" | "expired" | "suspended";
  expiresAt: string;
  grantPrefix: string;
};

export type AgentMandateIssueResult =
  | { kind: "issued"; mandate: AgentMandate; secret: string }
  | { kind: "recovery"; mandate: AgentMandate; recoveryRequired: true };

export type CreateAgentIdentityInput = {
  handle: string;
  displayName: string;
  description: string;
  profileHandle: string;
};

export async function listAgentIdentities(getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>("/v1/agent-identities?limit=50", { token }));
  if (!Array.isArray(raw)) throw invalid("agent identity list");
  return raw.map(parseAgentIdentity);
}

export async function fetchPublicAgentIdentity(handle: string) {
  return parsePublicAgentIdentity(await apiRequest<unknown>(`/v1/agent-identities/${encodeURIComponent(handle)}`, { server: true }));
}

export async function listPublicAgentDirectory({ q = "", profileHandle = null, cursor = null }: { q?: string; profileHandle?: string | null; cursor?: string | null } = {}) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (profileHandle) params.set("profile_handle", profileHandle);
  params.set("limit", "20");
  if (cursor !== null) params.set("cursor", publicAgentDirectoryCursor(cursor, "request"));
  return parsePublicAgentIdentityDirectory(await apiRequest<unknown>(`/v1/agent-directory?${params.toString()}`, { server: true }));
}

export async function listPublicProfileAgentIdentities(handle: string) {
  return parsePublicAgentIdentityDirectory(await apiRequest<unknown>(`/v1/profiles/${encodeURIComponent(handle)}/agent-identities?limit=20`, { server: true }));
}

export async function createAgentIdentity(input: CreateAgentIdentityInput, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  assertVisibleAsciiIdempotencyKey(idempotencyKey);
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>("/v1/agent-identities", {
    method: "POST",
    token,
    headers: jsonHeaders(idempotencyKey),
    body: JSON.stringify({ handle: input.handle, display_name: input.displayName, description: input.description, profile_handle: input.profileHandle })
  }));
  if (response.status !== 201 || !isJsonResponse(response.headers) || !isAllowedReplayHeader(response.headers)) throw invalidMutationSuccess("creation");
  return parseCreatedAgentIdentity(response.body);
}

export async function withdrawAgentIdentity(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  assertVisibleAsciiIdempotencyKey(idempotencyKey);
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(`/v1/agent-identities/${encodeURIComponent(handle)}`, { method: "DELETE", token, headers: jsonHeaders(idempotencyKey, false) }));
  if (response.status !== 204 || response.body !== "" || !isAllowedReplayHeader(response.headers)) throw invalidMutationSuccess("withdrawal");
}

export async function listAgentMandates(handle: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/agent-identities/${encodeURIComponent(handle)}/mandates`, { token }));
  if (!Array.isArray(raw)) throw invalid("agent mandate list");
  return raw.map(parseMandate);
}

export async function issueAgentMandate(handle: string, expiresAt: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/agent-identities/${encodeURIComponent(handle)}/mandates`, {
    method: "POST",
    token,
    headers: jsonHeaders(idempotencyKey),
    body: JSON.stringify({ expires_at: expiresAt })
  }));
  return parseMandateIssue(raw);
}

export async function revokeAgentMandate(handle: string, mandateId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/agent-identities/${encodeURIComponent(handle)}/mandates/${encodeURIComponent(mandateId)}`, { method: "DELETE", token, headers: jsonHeaders(idempotencyKey, false) }));
}

function parseAgentIdentity(value: unknown): AgentIdentity {
  const raw = record(value, "agent identity");
  return {
    handle: required(raw.handle, "agent identity handle"),
    displayName: required(raw.display_name, "agent identity display name"),
    description: required(raw.description, "agent identity description"),
    profileHandle: required(raw.profile_handle, "agent identity profile handle"),
    status: oneOf(raw.status, ["active", "withdrawn", "withheld"], "agent identity status"),
    createdAt: required(raw.created_at, "agent identity creation time"),
    updatedAt: required(raw.updated_at, "agent identity update time")
  };
}

function parsePublicAgentIdentity(value: unknown): PublicAgentIdentity {
  const raw = record(value, "public agent identity");
  return parsePublicAgentIdentityFields(raw);
}

function parseCreatedAgentIdentity(value: unknown): PublicAgentIdentity {
  try {
    const raw = record(value, "agent identity creation response");
    if (!hasExactKeys(raw, ["capabilities", "description", "display_name", "handle", "profile_handle"])) throw invalidSuccessResponse();
    return parsePublicAgentIdentityFields(raw);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 502 && error.code === "server") throw error;
    throw invalidSuccessResponse();
  }
}

function parsePublicAgentIdentityFields(raw: Record<string, unknown>): PublicAgentIdentity {
  if (!Array.isArray(raw.capabilities) || raw.capabilities.length !== 1 || raw.capabilities[0] !== "internal_contact_request") throw invalid("agent identity capabilities");
  return {
    handle: boundedHandle(raw.handle, "agent identity handle"),
    displayName: boundedString(raw.display_name, 100, "agent identity display name"),
    description: boundedString(raw.description, 500, "agent identity description"),
    profileHandle: boundedString(raw.profile_handle, 100, "agent identity profile handle"),
    capabilities: ["internal_contact_request"]
  };
}

function parsePublicAgentIdentityDirectory(value: unknown): PublicAgentIdentityDirectory {
  const raw = record(value, "public agent identity directory");
  if (!Array.isArray(raw.identities)) throw invalid("public agent identity directory");
  const nextCursor = raw.next_cursor === null ? null : publicAgentDirectoryCursor(raw.next_cursor, "response");
  return { identities: raw.identities.map(parsePublicAgentIdentity), nextCursor };
}

function parseMandateIssue(value: unknown): AgentMandateIssueResult {
  const raw = record(value, "agent mandate issue response");
  if (raw.recovery_required === true) return { kind: "recovery", mandate: parseMandate(raw), recoveryRequired: true };
  const grant = record(raw.grant, "agent mandate grant");
  const secret = required(grant.key, "one-time mandate secret");
  return {
    kind: "issued",
    mandate: parseMandate({ ...raw, status: "active", grant_prefix: grant.prefix }),
    secret
  };
}

function parseMandate(value: unknown): AgentMandate {
  const raw = record(value, "agent mandate");
  return {
    id: required(raw.id, "agent mandate id"),
    scope: oneOf(raw.scope, ["internal_contact_request"], "agent mandate scope"),
    status: oneOf(raw.status, ["active", "revoked", "expired", "suspended"], "agent mandate status"),
    expiresAt: required(raw.expires_at, "agent mandate expiry"),
    grantPrefix: required(raw.grant_prefix, "agent mandate grant prefix")
  };
}

function jsonHeaders(idempotencyKey: string, withJson = true) { return { ...(withJson ? { "Content-Type": "application/json" } : {}), "Idempotency-Key": idempotencyKey }; }
function record(value: unknown, label: string): Record<string, unknown> { if (typeof value !== "object" || value === null || Array.isArray(value)) throw invalid(label); return value as Record<string, unknown>; }
function required(value: unknown, label: string) { if (typeof value !== "string" || !value) throw invalid(label); return value; }
function oneOf<T extends string>(value: unknown, values: readonly T[], label: string): T { if (typeof value !== "string" || !values.includes(value as T)) throw invalid(label); return value as T; }
function invalid(label: string) { return new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); }
function boundedString(value: unknown, maxLength: number, label: string) { if (typeof value !== "string" || value.length < 1 || value.length > maxLength) throw invalid(label); return value; }
function boundedHandle(value: unknown, label: string) { const handle = boundedString(value, 100, label); if (!/^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/u.test(handle)) throw invalid(label); return handle; }
function publicAgentDirectoryCursor(value: unknown, source: "request" | "response") { if (typeof value !== "string" || !value.trim() || value.length > 500) { if (source === "request") throw new ApiRequestError("The public Agent Directory cursor is invalid.", 422, "request"); throw invalid("public agent identity directory cursor"); } return value; }
function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]) { const actual = Object.keys(value).sort(); return actual.length === expected.length && actual.every((key, index) => key === expected[index]); }
function assertVisibleAsciiIdempotencyKey(value: string) { if (typeof value !== "string" || !/^[\x21-\x7E]{1,128}$/u.test(value)) throw new ApiRequestError("A visible-ASCII Idempotency-Key is required for this action.", 400, "request"); }
function isAllowedReplayHeader(headers: Headers) { const value = headers.get("Idempotency-Replayed"); return value === null || value === "true"; }
function isJsonResponse(headers: Headers) { return (headers.get("content-type") ?? "").toLowerCase().includes("application/json"); }
function invalidSuccessResponse() { return new ApiRequestError("The API returned an invalid agent identity mutation response.", 502, "server"); }
function invalidMutationSuccess(operation: "creation" | "withdrawal") { return new ApiRequestError(`The API returned an invalid agent identity ${operation} response.`, 502, "server"); }
