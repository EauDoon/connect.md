/**
 * Scoped agent grants for the versioned network API.
 *
 * A grant is named, owner-issued, bounded in time, revocable, and restricted
 * to an explicit scope list. Only the token's SHA-256 digest is stored; the
 * token is shown exactly once at creation. Scopes are an allow-list — there
 * is no wildcard — and the set is deliberately tiny for the MVP.
 *
 * Authority invariants (carried over from docs/social-network.md):
 *   - Agents can never send contact requests or messages.
 *   - Agents can never create, accept, or reject consent decisions.
 *   - Agents can never issue grants (no scope escalation).
 *
 * Time bound: a grant created without an explicit expiry expires after
 * DEFAULT_GRANT_TTL_DAYS, and an explicit expiry may be at most one year out.
 * Rows created before the default existed may carry no expiry; they stay live
 * until revoked, which grantIsLive still honours.
 */

import { randomBytes } from "node:crypto";

export const AGENT_SCOPES = ["profile:read", "profile:write", "contacts:read"] as const;
export type AgentScope = (typeof AGENT_SCOPES)[number];

export const AGENT_TOKEN_PREFIX = "cnag_";

/** Lifetime of a grant created without an explicit expiresAt. */
export const DEFAULT_GRANT_TTL_DAYS = 90;
const DAY_MILLISECONDS = 24 * 3600_000;
const MAX_GRANT_TTL_MILLISECONDS = 366 * DAY_MILLISECONDS;

export type AgentGrantDefinition = Readonly<{
  name: string;
  scopes: readonly AgentScope[];
  expiresAt: string;
}>;

export function mintAgentToken(): string {
  return AGENT_TOKEN_PREFIX + randomBytes(32).toString("base64url");
}

export function validateGrantDefinition(
  input: unknown,
  now: Date = new Date(),
): { ok: true; definition: AgentGrantDefinition } | { ok: false; reason: string } {
  if (input === null || typeof input !== "object") return { ok: false, reason: "Grant definition must be an object." };
  const record = input as Record<string, unknown>;
  if (typeof record.name !== "string" || record.name.trim().length < 1 || record.name.trim().length > 64) {
    return { ok: false, reason: "Grant name must be 1-64 characters." };
  }
  if (!Array.isArray(record.scopes) || record.scopes.length === 0 || record.scopes.length > AGENT_SCOPES.length) {
    return { ok: false, reason: `Grant scopes must be a non-empty subset of ${AGENT_SCOPES.join(", ")}.` };
  }
  const scopes: AgentScope[] = [];
  for (const scope of record.scopes) {
    if (typeof scope !== "string" || !(AGENT_SCOPES as readonly string[]).includes(scope)) {
      return { ok: false, reason: `Unknown scope ${JSON.stringify(scope)}.` };
    }
    if (!scopes.includes(scope as AgentScope)) scopes.push(scope as AgentScope);
  }
  // A missing or null expiry takes the default lifetime: new grants never
  // become perpetual bearer tokens by omission.
  let expiresAt = new Date(now.getTime() + DEFAULT_GRANT_TTL_DAYS * DAY_MILLISECONDS).toISOString();
  if (record.expiresAt !== undefined && record.expiresAt !== null) {
    if (typeof record.expiresAt !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/.test(record.expiresAt)) {
      return { ok: false, reason: "expiresAt must be an ISO-8601 UTC timestamp." };
    }
    const parsed = Date.parse(record.expiresAt);
    if (Number.isNaN(parsed) || parsed <= now.getTime() || parsed > now.getTime() + MAX_GRANT_TTL_MILLISECONDS) {
      return { ok: false, reason: "expiresAt must be in the future and within one year." };
    }
    expiresAt = new Date(parsed).toISOString();
  }
  return { ok: true, definition: { name: record.name.trim(), scopes, expiresAt } };
}

export function grantIsLive(grant: { expiresAt: string | null; revokedAt: string | null }, now: Date): boolean {
  if (grant.revokedAt !== null) return false;
  if (grant.expiresAt !== null && Date.parse(grant.expiresAt) <= now.getTime()) return false;
  return true;
}

export function scopeAllows(grant: { scopes: readonly string[] }, required: AgentScope): boolean {
  return grant.scopes.includes(required);
}
