import { ApiRequestError, apiRequest, apiRequestWithMetadata, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/logical-mutation";

const AGENT_ENDPOINTS = {
  delegations: "/v1/agent-grants",
  documents: "/v1/documents",
  recentChanges: "/v1/changes/recent",
  proposals: "/v1/proposals",
} as const;

export type DelegationMode = "proposal" | "direct";
export type DelegationStatus = "active" | "paused" | "revoked" | "expired";
export type AgentDelegation = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  mode: DelegationMode;
  status: DelegationStatus;
  expiresAt: string;
  resourceType: "owner" | "document";
  resourceId: string | null;
  createdAt: string;
  lastUsedAt: string | null;
};

export type DelegationDraft = Pick<AgentDelegation, "name" | "mode" | "expiresAt" | "resourceType" | "resourceId">;
export type AgentDelegationCreateResult =
  | { delegation: AgentDelegation; key: string; recoveryRequired: false }
  | { delegation: AgentDelegation; recoveryRequired: true };
export type OwnedDocumentOption = { id: string; kind: "profile" | "resume"; identifier: string; version: number };
export type OwnedDocumentPage = { documents: OwnedDocumentOption[]; nextCursor: string | null };
export type OwnedDocumentPageOptions = { cursor?: string | null; limit?: number; kind?: "profile" | "resume"; signal?: AbortSignal };
export type DelegationAuditEvent = {
  id: string;
  delegationId: string;
  agentName: string;
  action: string;
  documentIdentifier: string | null;
  createdAt: string;
  outcome: string;
};

export type AgentProposalStatus = "pending" | "accepted" | "rejected";
export type AgentProposal = {
  id: string;
  documentId: string;
  kind: "profile" | "resume";
  identifier: string;
  markdown: string;
  ifMatch: string;
  status: AgentProposalStatus;
  submitterActorId: string;
  submitterGrantId: string;
  createdAt: string;
  decidedAt: string | null;
};
export type AgentProposalPage = { proposals: AgentProposal[]; nextCursor: string | null };

export async function listDelegations(getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(AGENT_ENDPOINTS.delegations, { token }));
  return arrayFromEnvelope(raw, "grants").map((item) => parseDelegation(item, { requireRevoked: true }));
}

export async function listOwnedDocumentOptions(getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return (await listOwnedDocumentPageForSubject(getToken, isSubjectCurrent, { limit: 100 })).documents;
}

export async function listOwnedDocumentPageForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, options: OwnedDocumentPageOptions = {}): Promise<OwnedDocumentPage> {
  const params = new URLSearchParams({ limit: String(Math.min(100, Math.max(1, options.limit ?? 25))) });
  if (options.kind) params.set("kind", options.kind);
  if (options.cursor) params.set("cursor", options.cursor);
  return withSubjectBoundToken(getToken, isSubjectCurrent, async (token) => {
    const raw = await apiRequest<unknown>(`${AGENT_ENDPOINTS.documents}?${params.toString()}`, { token, signal: options.signal });
    const envelope = asRecord(raw);
    if (!Array.isArray(envelope.documents)) throw new Error("The API returned an invalid document inventory.");
    const nextCursor = envelope.next_cursor;
    if (nextCursor !== null && nextCursor !== undefined && (typeof nextCursor !== "string" || !nextCursor)) throw new Error("The API returned an invalid document inventory cursor.");
    return { documents: envelope.documents.map(parseOwnedDocumentOption), nextCursor: typeof nextCursor === "string" ? nextCursor : null };
  });
}

export async function createDelegation(draft: DelegationDraft, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string): Promise<AgentDelegationCreateResult> {
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(AGENT_ENDPOINTS.delegations, {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({
      name: draft.name,
      mode: draft.mode === "proposal" ? "proposal_only" : "direct",
      expires_at: draft.expiresAt,
      resource: draft.resourceType === "document" && draft.resourceId
        ? { type: "document", id: draft.resourceId }
        : { type: "owner" },
      scopes: draft.mode === "proposal"
        ? ["documents:read", "inventory:read", "changes:read", "proposals:write"]
        : ["documents:read", "inventory:read", "changes:read", "documents:write"]
    })
  }));
  try {
    return parseDelegationCreateResult(response.body, response.headers, response.status);
  } catch {
    throw new ApiRequestError("The agent-grant creation response could not be confirmed. Retry the unchanged creation.", 502, "server");
  }
}

export async function setDelegationPaused(id: string, paused: boolean, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  if (!paused) throw new Error("Paused grants cannot be resumed; create a fresh bounded grant instead.");
  await revokeDelegation(id, getToken, isSubjectCurrent, idempotencyKey);
}

export async function revokeDelegation(id: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`${AGENT_ENDPOINTS.delegations}/${encodeURIComponent(id)}`, { method: "DELETE", token, headers: { "Idempotency-Key": idempotencyKey } }));
}

export async function emergencyStopDelegations(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKeyForGrant: (id: string) => string = () => newIdempotencyKey()) {
  await withSubjectBoundToken(getToken, isSubjectCurrent, async (token) => {
    const raw = await apiRequest<unknown>(AGENT_ENDPOINTS.delegations, { token });
    const activeIds = arrayFromEnvelope(raw, "grants")
      .map((item) => parseDelegation(item, { requireRevoked: true }))
      .filter((grant) => grant.status === "active")
      .map((grant) => grant.id);
    if (!isSubjectCurrent()) throw new Error("The signed-in account changed before grants could be revoked.");
    await Promise.all(activeIds.map((id) => apiRequest<unknown>(`${AGENT_ENDPOINTS.delegations}/${encodeURIComponent(id)}`, { method: "DELETE", token, headers: { "Idempotency-Key": idempotencyKeyForGrant(id) } })));
  });
}

export async function listDelegationAudit(getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(AGENT_ENDPOINTS.recentChanges, { token }));
  return arrayFromEnvelope(raw, "events").map(parseAuditEvent);
}

export async function listAgentProposals(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor?: string | null): Promise<AgentProposalPage> {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listAgentProposalsWithToken(token, cursor));
}

export async function listAgentProposalsForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor?: string | null): Promise<AgentProposalPage> {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => listAgentProposalsWithToken(token, cursor));
}

async function listAgentProposalsWithToken(token: string | null, cursor?: string | null): Promise<AgentProposalPage> {
  const params = new URLSearchParams({ limit: "100" });
  if (cursor) params.set("cursor", cursor);
  const raw = await apiRequest<unknown>(`${AGENT_ENDPOINTS.proposals}?${params.toString()}`, { token });
  const record = asRecord(raw);
  return {
    proposals: arrayFromEnvelope(raw, "proposals").map(parseAgentProposal),
    nextCursor: textOrNull(record.next_cursor)
  };
}

export async function decideAgentProposal(id: string, action: Exclude<AgentProposalStatus, "pending">, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  const routeAction = action === "accepted" ? "accept" : "reject";
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`${AGENT_ENDPOINTS.proposals}/${encodeURIComponent(id)}/${routeAction}`, {
    method: "POST",
    token,
    headers: { "Idempotency-Key": idempotencyKey }
  }));
  return parseAgentProposal(raw);
}

export async function loadProposalBaseMarkdown(proposal: Pick<AgentProposal, "kind" | "identifier">, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const collection = proposal.kind === "profile" ? "profiles" : "resumes";
  const raw = asRecord(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/${collection}/${encodeURIComponent(proposal.identifier)}`, { token })));
  return requiredText(raw.markdown, "proposal base Markdown");
}

function parseOwnedDocumentOption(value: unknown): OwnedDocumentOption {
  const record = asRecord(value);
  if (record.kind !== "profile" && record.kind !== "resume") throw new Error("The API returned an invalid document kind.");
  const version = record.version ?? record.current_version;
  if (typeof version !== "number" || !Number.isInteger(version) || version < 1) throw new Error("The API returned an invalid document version.");
  return {
    id: requiredText(record.id, "document id"),
    kind: record.kind,
    identifier: requiredText(record.identifier ?? record.public_identifier, "document identifier"),
    version
  };
}

function parseDelegation(value: unknown, options: { requireRevoked?: boolean } = {}): AgentDelegation {
  const record = asRecord(value);
  const resource = asRecord(record.resource);
  rejectDelegationSecrets(record);
  const requireRevoked = options.requireRevoked === true;
  const hasStatus = Object.prototype.hasOwnProperty.call(record, "status");
  if (requireRevoked && typeof record.revoked !== "boolean") throw new Error("The API returned an invalid delegation revocation state.");
  if (requireRevoked && hasStatus && !isDelegationStatus(record.status)) throw new Error("The API returned an invalid delegation status.");
  if (requireRevoked && hasStatus && ((record.revoked === true && record.status !== "revoked") || (record.revoked === false && record.status === "revoked"))) throw new Error("The API returned an ambiguous delegation status.");
  const mode = record.mode === "direct" ? "direct" : record.mode === "proposal_only" ? "proposal" : (() => { throw new Error("The API returned an invalid delegation mode."); })();
  const resourceType = resource.type === "document" ? "document" : resource.type === "owner" ? "owner" : (() => { throw new Error("The API returned an invalid delegation resource."); })();
  const scopes = requiredStringArray(record, "scopes", 128);
  if (scopes.length === 0) throw new Error("The API returned invalid delegation scopes.");
  if (resourceType === "document" && !textOrNull(resource.id)) throw new Error("The API returned an invalid delegation resource.");
  const expiresAt = requiredText(record.expires_at, "delegation expiry");
  if (requireRevoked && Number.isNaN(new Date(expiresAt).valueOf())) throw new Error("The API returned an invalid delegation expiry.");
  const status = requireRevoked
    ? record.revoked === true
      ? "revoked"
      : hasStatus
        ? record.status as DelegationStatus
        : isExpired(expiresAt)
          ? "expired"
          : "active"
    : record.revoked === true || record.revoked_at ? "revoked" : isDelegationStatus(record.status) ? record.status : isExpired(expiresAt) ? "expired" : "active";
  return {
    id: requiredText(record.id, "delegation id"),
    name: requiredText(record.name, "delegation name"),
    prefix: requiredBoundedText(record.prefix, "delegation prefix", 128),
    scopes,
    mode,
    status,
    expiresAt,
    resourceType,
    resourceId: textOrNull(resource.id),
    createdAt: requiredText(record.created_at, "delegation creation time"),
    lastUsedAt: textOrNull(record.last_used_at)
  };
}

function parseDelegationCreateResult(value: unknown, headers: Headers, status: number): AgentDelegationCreateResult {
  if (status !== 201) throw new Error("The API returned an invalid agent-grant creation status.");
  const record = asRecord(value);
  const replayed = headers.get("Idempotency-Replayed");
  if (record.recovery_required === true) {
    if (replayed !== "true") throw new Error("The API returned an invalid agent-grant recovery response.");
    rejectDelegationSecrets(record);
    return { delegation: parseDelegation(record), recoveryRequired: true };
  }
  if (Object.prototype.hasOwnProperty.call(record, "recovery_required") || replayed !== null) {
    throw new Error("The API returned an invalid agent-grant creation response.");
  }
  if (typeof record.key !== "string" || !record.key) throw new Error("The API returned an invalid one-time agent-grant key.");
  if (Object.prototype.hasOwnProperty.call(record, "token") || Object.prototype.hasOwnProperty.call(record, "secret")) {
    throw new Error("The API returned an invalid agent-grant creation response.");
  }
  const { key: _key, ...metadata } = record;
  return { delegation: parseDelegation(metadata), key: record.key, recoveryRequired: false };
}

function rejectDelegationSecrets(record: Record<string, unknown>) {
  for (const key of ["key", "token", "secret"]) {
    if (Object.prototype.hasOwnProperty.call(record, key)) throw new Error("The API returned an invalid agent-grant response.");
  }
}

function parseAgentProposal(value: unknown): AgentProposal {
  const record = asRecord(value);
  return {
    id: requiredText(record.id, "proposal id"),
    documentId: requiredText(record.document_id, "proposal document id"),
    kind: record.kind === "resume" ? "resume" : record.kind === "profile" ? "profile" : (() => { throw new Error("The API returned an invalid proposal kind."); })(),
    identifier: requiredText(record.identifier, "proposal document identifier"),
    markdown: requiredText(record.markdown, "proposal Markdown"),
    ifMatch: requiredText(record.if_match, "proposal ETag"),
    status: isAgentProposalStatus(record.status) ? record.status : (() => { throw new Error("The API returned an invalid proposal status."); })(),
    submitterActorId: requiredText(record.submitter_actor_id, "proposal submitter"),
    submitterGrantId: requiredText(record.submitter_grant_id, "proposal grant"),
    createdAt: requiredText(record.created_at, "proposal creation time"),
    decidedAt: textOrNull(record.decided_at)
  };
}

function parseAuditEvent(value: unknown): DelegationAuditEvent {
  const record = asRecord(value);
  const data = asRecord(record.data);
  return {
    id: text(record.id) || String(integer(record.sequence, 0)),
    delegationId: text(record.delegation_id ?? record.grant_id),
    agentName: text(record.agent_name ?? record.actor_label ?? record.actor_id) || "Agent",
    action: text(record.action ?? record.operation ?? record.type) || "change",
    documentIdentifier: textOrNull(record.document_identifier ?? record.resource_id),
    createdAt: requiredText(record.created_at ?? record.occurred_at, "audit time"),
    outcome: text(record.outcome ?? data.outcome ?? record.actor_method) || "recorded"
  };
}

function requiredStringArray(record: Record<string, unknown>, key: string, maxLength: number) {
  if (!Object.prototype.hasOwnProperty.call(record, key) || !Array.isArray(record[key])) throw new Error(`The API returned invalid ${key}.`);
  return record[key].map((item) => requiredBoundedText(item, key, maxLength));
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
function requiredBoundedText(value: unknown, label: string, maxLength: number) { const result = requiredText(value, label); if (result.length > maxLength) throw new Error(`The API returned an invalid ${label}.`); return result; }
function isDelegationStatus(value: unknown): value is DelegationStatus { return value === "active" || value === "paused" || value === "revoked" || value === "expired"; }
function isAgentProposalStatus(value: unknown): value is AgentProposalStatus { return value === "pending" || value === "accepted" || value === "rejected"; }
function isExpired(value: unknown) { return typeof value === "string" && !Number.isNaN(new Date(value).valueOf()) && new Date(value).valueOf() <= Date.now(); }
