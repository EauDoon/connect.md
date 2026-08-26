import { ApiRequestError, apiRequest, apiRequestWithMetadata, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";
import { PRODUCT_ENDPOINTS } from "@/lib/product-endpoints";
import type { ContactPolicy, ContactPolicyMode, OutreachPage, OutreachStatus, OutreachThread } from "@/lib/product-types";

export async function getContactPolicy(getToken: TokenGetter) {
  return getContactPolicyWithToken(await getToken());
}

export async function getContactPolicyForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, getContactPolicyWithToken);
}

async function getContactPolicyWithToken(token: string | null) {
  const response = await apiRequestWithMetadata<unknown>(PRODUCT_ENDPOINTS.contactPolicy, { token });
  return parseContactPolicy(response.body, response.headers.get("ETag"));
}

export async function updateContactPolicy(policy: ContactPolicy, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  const etag = requiredContactPolicyEtag(policy.etag);
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(PRODUCT_ENDPOINTS.contactPolicy, {
    method: "PUT",
    token,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "If-Match": etag },
    body: JSON.stringify({
      allow_agent_requests: policy.mode !== "closed" && policy.allowAgentMessages,
      daily_request_limit: policy.dailyRequestLimit
    })
  }));
  return parseContactPolicyMutationResponse(response.body, response.headers.get("ETag"));
}

export async function listOutreach(getToken: TokenGetter, cursor?: string | null): Promise<OutreachPage> {
  return listOutreachWithToken(await getToken(), cursor);
}

export async function listOutreachForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor?: string | null): Promise<OutreachPage> {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listOutreachWithToken(token, cursor));
}

async function listOutreachWithToken(token: string | null, cursor?: string | null): Promise<OutreachPage> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  const raw = await apiRequest<unknown>(`${PRODUCT_ENDPOINTS.outreachInbox}?${params.toString()}`, { token });
  const record = asRecord(raw);
  if (record.next_cursor !== null && record.next_cursor !== undefined && (typeof record.next_cursor !== "string" || !record.next_cursor)) {
    throw new Error("The outreach inbox returned an invalid cursor.");
  }
  return {
    threads: arrayFromEnvelope(raw, "requests").map(parseOutreachThread),
    nextCursor: textOrNull(record.next_cursor)
  };
}

export async function sendContactRequest(targetProfileHandle: string, purpose: string, message: string, idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(PRODUCT_ENDPOINTS.outreach, {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ target_profile_handle: targetProfileHandle, purpose, message })
  }));
  return parseOutreachThread(raw);
}

export async function actOnOutreach(id: string, action: Exclude<OutreachStatus, "pending">, reason: string | null, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  const normalizedReason = reason?.trim() ?? "";
  if (action === "reported" && !normalizedReason) throw new Error("A report reason is required.");
  const routeAction = { accepted: "accept", rejected: "reject", blocked: "block", reported: "report" }[action];
  await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`${PRODUCT_ENDPOINTS.outreach}/${encodeURIComponent(id)}/${routeAction}`, {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(action === "reported" ? { reason: normalizedReason } : {})
  }));
}

function parseContactPolicy(value: unknown, headerEtag: string | null): ContactPolicy {
  const record = asRecord(value);
  const representative = asRecord(record.representative);
  const mode = record.mode ?? record.policy;
  const allowed = record.allow_agent_requests === true || record.allow_agent_messages === true || record.allow_agent_contact === true;
  const etag = requiredContactPolicyEtag(headerEtag);
  if (record.etag !== etag) throw new Error("invalid contact policy ETag");
  return {
    mode: isContactPolicy(mode) ? mode : allowed ? "request" : "closed",
    allowAgentMessages: allowed,
    dailyRequestLimit: integer(record.daily_request_limit, 5),
    representativeLabel: textOrNull(record.representative_label ?? representative.label ?? representative.name),
    representativeUrl: textOrNull(record.representative_url ?? representative.url),
    etag
  };
}

function parseContactPolicyMutationResponse(value: unknown, headerEtag: string | null): ContactPolicy {
  try {
    const record = asRecord(value);
    const dailyRequestLimit = record.daily_request_limit;
    const version = record.version;
    if (typeof record.allow_agent_requests !== "boolean" || typeof dailyRequestLimit !== "number" || !Number.isInteger(dailyRequestLimit) || dailyRequestLimit < 1 || dailyRequestLimit > 20 || typeof version !== "number" || !Number.isInteger(version) || version < 0) {
      throw new Error("invalid contact policy response");
    }
    return parseContactPolicy(value, headerEtag);
  } catch {
    throw new ApiRequestError("The contact policy response could not be confirmed. Retry the unchanged update.", 502, "server");
  }
}

function requiredContactPolicyEtag(value: string | null | undefined) {
  if (typeof value !== "string" || !/^"policy-(?:0|[1-9][0-9]*)"$/u.test(value)) throw new ApiRequestError("The contact policy response could not be confirmed. Retry the unchanged update.", 502, "server");
  return value;
}

function parseOutreachThread(value: unknown): OutreachThread {
  const record = asRecord(value);
  return {
    id: requiredText(record.id, "outreach id"),
    senderName: text(record.sender_name ?? record.requester_name ?? record.sender_profile_handle ?? record.requester_profile_handle) || "Connect.md member",
    senderAgent: textOrNull(record.sender_agent ?? record.agent_name ?? (record.sender_actor_method === "agent_grant" ? record.sender_actor_id : null)),
    subject: text(record.subject ?? record.purpose) || "Professional inquiry",
    preview: text(record.preview ?? record.message),
    targetIdentifier: text(record.target_identifier ?? record.target_profile_handle ?? record.target_document_id) || "your profile",
    receivedAt: requiredText(record.received_at ?? record.created_at, "received time"),
    status: isOutreachStatus(record.status) ? record.status : "pending"
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function arrayFromEnvelope(value: unknown, key: string) {
  if (Array.isArray(value)) return value;
  const nested = asRecord(value)[key];
  return Array.isArray(nested) ? nested : [];
}

function text(value: unknown) { return typeof value === "string" ? value : ""; }
function textOrNull(value: unknown) { return typeof value === "string" && value ? value : null; }
function integer(value: unknown, fallback: number) { return typeof value === "number" && Number.isInteger(value) ? value : fallback; }
function requiredText(value: unknown, label: string) { if (typeof value !== "string" || !value) throw new Error(`The API returned an invalid ${label}.`); return value; }
function isContactPolicy(value: unknown): value is ContactPolicyMode { return value === "open" || value === "request" || value === "representative_only" || value === "closed"; }
function isOutreachStatus(value: unknown): value is OutreachStatus { return value === "pending" || value === "accepted" || value === "rejected" || value === "blocked" || value === "reported"; }
