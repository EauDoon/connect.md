import { ApiRequestError, apiRequest, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";
import { appendCursorPage, type CursorPage } from "@/lib/cursor-page";
import { newIdempotencyKey } from "@/lib/logical-mutation";

export { appendCursorPage, type CursorPage };

export type ConnectionRequest = { id: string; counterpartyProfileHandle: string; direction: "inbound" | "outbound"; messagingRequested: boolean; messagingConsent: boolean | null; status: "pending" | "accepted" | "rejected" | "blocked"; createdAt: string; decidedAt: string | null; retentionExpiresAt: string };
export type Connection = { id: string; counterpartyProfileHandle: string; messagingEnabled: boolean; createdAt: string; retentionExpiresAt: string };
export type Conversation = { id: string; connectionId: string; counterpartyProfileHandle: string; createdAt: string; retentionExpiresAt: string };
export type SocialMessage = { id: string; conversationId: string; direction: "sent" | "received"; markdown: string; createdAt: string; retentionExpiresAt: string };
export type Notification = { id: string; type: string; resourceType: string; createdAt: string; readAt: string | null };
export type NotificationHubAction = {
  href: "/network?view=requests" | "/network?view=connections" | "/network?view=conversations" | "/applications";
  label: string;
};

export const MESSAGE_MAX_LENGTH = 4000;

const notificationHubActions: Readonly<Record<string, NotificationHubAction>> = {
  "connection_request.received:connection_request": { href: "/network?view=requests", label: "Open connection requests" },
  "connection_request.accepted:connection": { href: "/network?view=connections", label: "Open connections" },
  "connection_request.rejected:connection_request": { href: "/network?view=requests", label: "Open connection requests" },
  "conversation.created:conversation": { href: "/network?view=conversations", label: "Open conversations" },
  "message.received:conversation": { href: "/network?view=conversations", label: "Open conversations" },
  "application.under_review:application": { href: "/applications", label: "Open applications" },
  "application.accepted:application": { href: "/applications", label: "Open applications" },
  "application.rejected:application": { href: "/applications", label: "Open applications" },
};

export function authSubjectIsCurrent(currentSubject: string | null, requestSubject: string | null) { return Boolean(requestSubject) && currentSubject === requestSubject; }

/**
 * Private notifications contain opaque resource and actor references. This
 * maps only the recognized, generic event/resource pairs to literal private
 * hubs; no notification-provided identifier is ever used as navigation data.
 */
export function notificationHubAction(notification: Pick<Notification, "type" | "resourceType">): NotificationHubAction | null {
  return notificationHubActions[`${notification.type}:${notification.resourceType}`] ?? null;
}

export function presentSocialError(error: unknown) {
  if (!(error instanceof ApiRequestError)) return "connect.md could not complete that private network request. No change was assumed.";
  if (error.code === "offline") return "You are offline. Reconnect before trying again.";
  if (error.code === "unauthorized") return "Your signed-in human session is not authorized for that private network action.";
  if (error.code === "not_found") return "The requested private network record is unavailable.";
  if (error.code === "server") return "connect.md is temporarily unavailable. No change was assumed.";
  if (error.status === 409) return "That private network action cannot be completed in its current state.";
  return error.message;
}

export async function createConnectionRequest(handle: string, messagingRequested: boolean, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseConnectionRequest(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>("/v1/connection-requests", { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: JSON.stringify({ recipient_profile_handle: handle, messaging_requested: messagingRequested }) })));
}
export async function listConnectionRequestInboxForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) { return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listConnectionRequestInboxWithToken(token, cursor)); }
export async function decideConnectionRequest(id: string, action: "accept" | "reject" | "block", messagingConsent: boolean | null, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  const accepting = action === "accept";
  return parseConnectionRequest(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/connection-requests/${encodeURIComponent(id)}/${action}`, { method: "POST", token, headers: jsonHeaders(idempotencyKey, accepting), ...(accepting ? { body: JSON.stringify({ messaging_consent: messagingConsent === true }) } : {}) })));
}
export async function listConnectionsForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) { return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listConnectionsWithToken(token, cursor)); }
export async function removeConnection(id: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) { await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/connections/${encodeURIComponent(id)}`, { method: "DELETE", token, headers: jsonHeaders(idempotencyKey, false) })); }
export async function blockConnection(id: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) { await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/connections/${encodeURIComponent(id)}/block`, { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: "{}" })); }
export async function createConversation(connectionId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) { return parseConversation(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>("/v1/conversations", { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: JSON.stringify({ connection_id: connectionId }) }))); }
export async function listConversationsForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) { return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listConversationsWithToken(token, cursor)); }
export async function listMessages(conversationId: string, getToken: TokenGetter, cursor: string | null = null) { return listMessagesWithToken(conversationId, await getToken(), cursor); }
export async function listMessagesForSubject(conversationId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) { return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listMessagesWithToken(conversationId, token, cursor)); }
export async function sendMessage(conversationId: string, markdown: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) { return parseMessageSend(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/conversations/${encodeURIComponent(conversationId)}/messages`, { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: JSON.stringify({ markdown }) }))); }
export async function listNotificationsForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) { return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listNotificationsWithToken(token, cursor)); }
export async function markNotificationRead(id: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) { return parseNotification(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/notifications/${encodeURIComponent(id)}/read`, { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: "{}" }))); }

function params(cursor: string | null, limit = 25) { const query = new URLSearchParams({ limit: String(limit) }); if (cursor) query.set("cursor", cursor); return query; }
async function listMessagesWithToken(conversationId: string, token: string | null, cursor: string | null) { return page(record(await apiRequest<unknown>(`/v1/conversations/${encodeURIComponent(conversationId)}/messages?${params(cursor, 50).toString()}`, { token })), "messages", parseMessage); }
async function listConnectionRequestInboxWithToken(token: string | null, cursor: string | null) { return page(record(await apiRequest<unknown>(`/v1/connection-requests/inbox?${params(cursor).toString()}`, { token })), "requests", parseConnectionRequest); }
async function listConnectionsWithToken(token: string | null, cursor: string | null) { return page(record(await apiRequest<unknown>(`/v1/connections?${params(cursor).toString()}`, { token })), "connections", parseConnection); }
async function listConversationsWithToken(token: string | null, cursor: string | null) { return page(record(await apiRequest<unknown>(`/v1/conversations?${params(cursor).toString()}`, { token })), "conversations", parseConversation); }
async function listNotificationsWithToken(token: string | null, cursor: string | null) { return page(record(await apiRequest<unknown>(`/v1/notifications?${params(cursor).toString()}`, { token })), "notifications", parseNotification); }
function jsonHeaders(idempotencyKey: string, withJson = true) { return { ...(withJson ? { "Content-Type": "application/json" } : {}), "Idempotency-Key": idempotencyKey }; }
function page<T>(raw: Record<string, unknown>, key: string, parse: (value: unknown) => T): CursorPage<T> { const values = raw[key]; if (!Array.isArray(values)) throw invalid(`${key} collection`); const nextCursor = textOrNull(raw.next_cursor); if (raw.next_cursor !== null && raw.next_cursor !== undefined && !nextCursor) throw invalid(`${key} cursor`); return { items: values.map(parse), nextCursor }; }
function parseConnectionRequest(value: unknown): ConnectionRequest { const raw = record(value); return { id: required(raw.id, "connection request id"), counterpartyProfileHandle: required(raw.counterparty_profile_handle, "counterparty profile handle"), direction: oneOf(raw.direction, ["inbound", "outbound"], "connection request direction"), messagingRequested: boolean(raw.messaging_requested, "messaging request"), messagingConsent: nullableBoolean(raw.messaging_consent, "messaging consent"), status: oneOf(raw.status, ["pending", "accepted", "rejected", "blocked"], "connection request status"), createdAt: required(raw.created_at, "connection request created time"), decidedAt: textOrNull(raw.decided_at), retentionExpiresAt: required(raw.retention_expires_at, "connection request retention time") }; }
function parseConnection(value: unknown): Connection { const raw = record(value); return { id: required(raw.id, "connection id"), counterpartyProfileHandle: required(raw.counterparty_profile_handle, "counterparty profile handle"), messagingEnabled: boolean(raw.messaging_enabled, "messaging state"), createdAt: required(raw.created_at, "connection created time"), retentionExpiresAt: required(raw.retention_expires_at, "connection retention time") }; }
function parseConversation(value: unknown): Conversation { const raw = record(value); return { id: required(raw.id, "conversation id"), connectionId: required(raw.connection_id, "conversation connection id"), counterpartyProfileHandle: required(raw.counterparty_profile_handle, "counterparty profile handle"), createdAt: required(raw.created_at, "conversation created time"), retentionExpiresAt: required(raw.retention_expires_at, "conversation retention time") }; }
function parseMessageSend(value: unknown) { const raw = record(value); return { id: required(raw.id, "message id"), conversationId: required(raw.conversation_id, "message conversation id"), createdAt: required(raw.created_at, "message created time"), retentionExpiresAt: required(raw.retention_expires_at, "message retention time") }; }
function parseMessage(value: unknown): SocialMessage { const raw = record(value); const direction = raw.direction === "sent" || raw.direction === "received" ? raw.direction : raw.sender_is_self === true ? "sent" : raw.sender_is_self === false ? "received" : null; if (!direction) throw invalid("message direction"); return { ...parseMessageSend(raw), direction, markdown: required(raw.markdown, "message markdown") }; }
function parseNotification(value: unknown): Notification { const raw = record(value); return { id: required(raw.id, "notification id"), type: required(raw.type, "notification type"), resourceType: required(raw.resource_type, "notification resource type"), createdAt: required(raw.created_at, "notification created time"), readAt: textOrNull(raw.read_at) }; }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) throw invalid("response"); return value as Record<string, unknown>; }
function required(value: unknown, label: string) { if (typeof value !== "string" || !value) throw invalid(label); return value; }
function textOrNull(value: unknown) { return typeof value === "string" && value.trim() ? value : null; }
function boolean(value: unknown, label: string) { if (typeof value !== "boolean") throw invalid(label); return value; }
function nullableBoolean(value: unknown, label: string) { if (value === null || value === undefined) return null; return boolean(value, label); }
function oneOf<const T extends readonly string[]>(value: unknown, values: T, label: string): T[number] { if (typeof value !== "string" || !(values as readonly string[]).includes(value)) throw invalid(label); return value as T[number]; }
function invalid(label: string) { return new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); }
