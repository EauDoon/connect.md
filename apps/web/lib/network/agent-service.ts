/**
 * Agent grant service (server-side): creation, listing, revocation, and
 * bearer-token resolution for the versioned agent API.
 */

import postgres from "postgres";
import { AGENT_SCOPES, grantIsLive, mintAgentToken, validateGrantDefinition, type AgentScope } from "./agent-grants";
import { tokenDigest, tokenDisplayPrefix } from "./secrets";
import { takeRateBucket } from "./auth-service";

export type AgentGrantRecord = {
  id: string;
  name: string;
  tokenPrefix: string;
  scopes: readonly AgentScope[];
  createdAt: string;
  expiresAt: string | null;
  revokedAt: string | null;
  lastUsedAt: string | null;
};

export type ResolvedAgent = {
  grantId: string;
  accountId: string;
  accountHandle: string;
  scopes: readonly AgentScope[];
};

export class AgentGrantError extends Error {
  readonly code: "invalid" | "not-found" | "conflict" | "rate-limited" | "unauthorized";

  constructor(code: AgentGrantError["code"], message: string) {
    super(message);
    this.code = code;
    this.name = "AgentGrantError";
  }
}

export async function createAgentGrant(
  sql: postgres.Sql,
  accountId: string,
  input: unknown,
): Promise<{ record: AgentGrantRecord; token: string }> {
  const definition = validateGrantDefinition(input);
  if (!definition.ok) throw new AgentGrantError("invalid", definition.reason);
  const bucket = await takeRateBucket(sql, `grant:account:${accountId}`, 20, 3600);
  if (!bucket.allowed) throw new AgentGrantError("rate-limited", "Too many grants created recently.");

  const token = mintAgentToken();
  const rows = await sql`
    INSERT INTO network_agent_grants (account_id, name, token_hash, token_prefix, scopes, expires_at)
    VALUES (${accountId}, ${definition.definition.name}, ${tokenDigest(token)}, ${tokenDisplayPrefix(token)},
            ${sql.array(definition.definition.scopes as string[])}, ${definition.definition.expiresAt})
    RETURNING id, name, token_prefix, scopes, created_at, expires_at
  `;
  const row = rows[0]!;
  return {
    token,
    record: {
      id: row.id as string,
      name: row.name as string,
      tokenPrefix: row.token_prefix as string,
      scopes: (row.scopes as string[]) as AgentScope[],
      createdAt: (row.created_at as Date).toISOString(),
      expiresAt: row.expires_at === null ? null : (row.expires_at as Date).toISOString(),
      revokedAt: null,
      lastUsedAt: null,
    },
  };
}

export async function listAgentGrants(sql: postgres.Sql, accountId: string): Promise<AgentGrantRecord[]> {
  const rows = await sql`
    SELECT id, name, token_prefix, scopes, created_at, expires_at, revoked_at, last_used_at
    FROM network_agent_grants WHERE account_id = ${accountId}
    ORDER BY created_at DESC LIMIT 100
  `;
  return rows.map((row) => ({
    id: row.id as string,
    name: row.name as string,
    tokenPrefix: row.token_prefix as string,
    scopes: (row.scopes as string[]) as AgentScope[],
    createdAt: (row.created_at as Date).toISOString(),
    expiresAt: row.expires_at === null ? null : (row.expires_at as Date).toISOString(),
    revokedAt: row.revoked_at === null ? null : (row.revoked_at as Date).toISOString(),
    lastUsedAt: row.last_used_at === null ? null : (row.last_used_at as Date).toISOString(),
  }));
}

export async function revokeAgentGrant(sql: postgres.Sql, accountId: string, grantId: string): Promise<void> {
  const rows = await sql`
    UPDATE network_agent_grants SET revoked_at = now()
    WHERE id = ${grantId} AND account_id = ${accountId} AND revoked_at IS NULL
    RETURNING id
  `;
  if (rows.length === 0) throw new AgentGrantError("not-found", "No such active grant for this account.");
}

/** Resolve an Authorization: Bearer cnag_… token to a live grant and its account. */
export async function resolveAgentToken(sql: postgres.Sql, authorizationHeader: string | null): Promise<ResolvedAgent | null> {
  if (authorizationHeader === null || !authorizationHeader.startsWith("Bearer ")) return null;
  const token = authorizationHeader.slice("Bearer ".length).trim();
  if (!token.startsWith("cnag_") || token.length < 12 || token.length > 128) return null;
  const rows = await sql`
    SELECT g.id, g.scopes, g.expires_at, g.revoked_at, a.id AS account_id, a.handle, a.status
    FROM network_agent_grants g JOIN network_accounts a ON a.id = g.account_id
    WHERE g.token_hash = ${tokenDigest(token)}
    LIMIT 1
  `;
  const row = rows[0];
  if (row === undefined || row.status !== "active") return null;
  const grant = { expiresAt: row.expires_at === null ? null : (row.expires_at as Date).toISOString(), revokedAt: row.revoked_at === null ? null : (row.revoked_at as Date).toISOString() };
  if (!grantIsLive(grant, new Date())) return null;
  const scopes = (row.scopes as string[]).filter((scope): scope is AgentScope => (AGENT_SCOPES as readonly string[]).includes(scope));
  await sql`UPDATE network_agent_grants SET last_used_at = now() WHERE id = ${row.id}`;
  return {
    grantId: row.id as string,
    accountId: row.account_id as string,
    accountHandle: row.handle as string,
    scopes,
  };
}
