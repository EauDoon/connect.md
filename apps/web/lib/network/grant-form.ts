/**
 * Browser-side helpers for the Agent access form. This module must stay free
 * of server imports (agent-grants.ts pulls in node:crypto), so the scope list
 * and the liveness rule are mirrored here and pinned to the server versions by
 * tests/network-grant-form.test.ts.
 */

export const GRANT_SCOPE_OPTIONS = [
  { scope: "profile:read", label: "Read your profile", description: "Read your saved profile Markdown, including while it is private." },
  { scope: "profile:write", label: "Edit your private profile", description: "Replace your saved profile while it is unpublished. Publishing stays with you." },
  { scope: "contacts:read", label: "Read contact requests", description: "List your incoming and outgoing contact requests and their status. It cannot send, accept, or decline them." },
] as const;

export type GrantScope = (typeof GRANT_SCOPE_OPTIONS)[number]["scope"];

/** Least privilege by default: an owner ticks write or contacts access deliberately. */
export const DEFAULT_GRANT_SCOPES: readonly GrantScope[] = ["profile:read"];

export const GRANT_LIFETIME_DAYS = [7, 30, 90, 365] as const;
export type GrantLifetimeDays = (typeof GRANT_LIFETIME_DAYS)[number];
export const DEFAULT_GRANT_LIFETIME_DAYS: GrantLifetimeDays = 90;

const DAY_MILLISECONDS = 24 * 3600_000;

/** An ISO-8601 UTC expiry `days` after `now`, in the form the grant API accepts. */
export function grantExpiryFromLifetime(days: GrantLifetimeDays, now: Date = new Date()): string {
  return new Date(now.getTime() + days * DAY_MILLISECONDS).toISOString();
}

/** The POST body for a new grant: scopes in the canonical order, an explicit expiry. */
export function grantRequestBody(name: string, scopes: readonly GrantScope[], days: GrantLifetimeDays, now: Date = new Date()) {
  return {
    name: name.trim(),
    scopes: GRANT_SCOPE_OPTIONS.map((option) => option.scope).filter((scope) => scopes.includes(scope)),
    expiresAt: grantExpiryFromLifetime(days, now),
  };
}

export type GrantDisplayState = "active" | "expired" | "revoked";

/** Mirrors grantIsLive: revoked wins, then an expiry at or before now. A legacy grant with no expiry stays active. */
export function grantDisplayState(grant: { expiresAt: string | null; revokedAt: string | null }, now: Date = new Date()): GrantDisplayState {
  if (grant.revokedAt !== null) return "revoked";
  if (grant.expiresAt !== null && Date.parse(grant.expiresAt) <= now.getTime()) return "expired";
  return "active";
}
