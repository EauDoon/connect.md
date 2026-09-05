/**
 * Account and session service for the network MVP (server-side only).
 *
 * Sessions are server-side: a random 256-bit token in an HttpOnly cookie,
 * SHA-256 digest at rest, revocable, expiring. Login and registration are
 * rate-limited with durable per-account and per-IP buckets, so serverless
 * cold starts cannot reset an attacker's budget.
 */

import postgres from "postgres";
import {
  hashPassword,
  tokenDigest,
  tokenDisplayPrefix,
  verifyPassword,
  generateToken,
} from "./secrets";
import { validateEmail, validateHandle, validatePassword } from "./identity";

export const SESSION_COOKIE_NAME = "connectmd_network_session";
export const SESSION_TTL_MILLISECONDS = 14 * 24 * 3600_000;

export type AccountRecord = {
  id: string;
  email: string;
  handle: string;
  status: string;
  created_at: string;
};

export class AccountActionError extends Error {
  readonly code: "invalid" | "conflict" | "credentials" | "rate-limited" | "deactivated";

  constructor(code: AccountActionError["code"], message: string) {
    super(message);
    this.code = code;
    this.name = "AccountActionError";
  }
}

/** Durable fixed-window rate bucket keyed by arbitrary identity. */
export async function takeRateBucket(
  sql: postgres.Sql,
  bucketKey: string,
  limit: number,
  windowSeconds: number,
): Promise<{ allowed: boolean; retryAfterSeconds: number }> {
  const rows = await sql`
    INSERT INTO network_auth_buckets (bucket_key, window_started_at, count)
    VALUES (${bucketKey}, now(), 1)
    ON CONFLICT (bucket_key) DO UPDATE SET
      count = CASE
        WHEN network_auth_buckets.window_started_at < now() - (${windowSeconds} * INTERVAL '1 second')
        THEN 1 ELSE network_auth_buckets.count + 1 END,
      window_started_at = CASE
        WHEN network_auth_buckets.window_started_at < now() - (${windowSeconds} * INTERVAL '1 second')
        THEN now() ELSE network_auth_buckets.window_started_at END
    RETURNING count, EXTRACT(EPOCH FROM (window_started_at + (${windowSeconds} * INTERVAL '1 second') - now())) AS retry_after
  `;
  const count = Number(rows[0]?.count ?? 1);
  const retryAfter = Math.max(1, Math.ceil(Number(rows[0]?.retry_after ?? windowSeconds)));
  return count <= limit ? { allowed: true, retryAfterSeconds: 0 } : { allowed: false, retryAfterSeconds: retryAfter };
}

export type RegistrationInput = {
  email: unknown;
  handle: unknown;
  password: unknown;
  ipKey: string;
};

export async function registerAccount(
  sql: postgres.Sql,
  input: RegistrationInput,
): Promise<{ account: AccountRecord; sessionToken: string }> {
  const email = validateEmail(input.email);
  if (!email.ok) throw new AccountActionError("invalid", email.reason);
  const handle = validateHandle(input.handle);
  if (!handle.ok) throw new AccountActionError("invalid", handle.reason);
  const password = validatePassword(input.password);
  if (!password.ok) throw new AccountActionError("invalid", password.reason);

  const ipBucket = await takeRateBucket(sql, `register:ip:${input.ipKey}`, 10, 3600);
  if (!ipBucket.allowed) throw new AccountActionError("rate-limited", "Too many registrations from this address. Try again later.");
  const emailBucket = await takeRateBucket(sql, `register:email:${email.email}`, 5, 3600);
  if (!emailBucket.allowed) throw new AccountActionError("rate-limited", "Too many registrations for this address. Try again later.");

  const existing = await sql`
    SELECT email, handle FROM network_accounts
    WHERE email = ${email.email} OR handle = ${handle.handle} LIMIT 1
  `;
  if (existing.length > 0) {
    const row = existing[0]!;
    if (row.email === email.email) throw new AccountActionError("conflict", "An account with this email already exists.");
    throw new AccountActionError("conflict", "That handle is already taken.");
  }

  const passwordHash = hashPassword(password.password);
  const inserted = await sql`
    INSERT INTO network_accounts (email, password_hash, handle)
    VALUES (${email.email}, ${passwordHash}, ${handle.handle})
    RETURNING id, email, handle, status, created_at
  `;
  const account = serializeAccount(inserted[0]!);
  const sessionToken = await createSession(sql, account.id);
  return { account, sessionToken };
}

export async function loginAccount(
  sql: postgres.Sql,
  input: { email: unknown; password: unknown; ipKey: string },
): Promise<{ account: AccountRecord; sessionToken: string }> {
  const email = validateEmail(input.email);
  if (!email.ok) throw new AccountActionError("credentials", "Email or password is incorrect.");
  const password = validatePassword(input.password);
  if (!password.ok) throw new AccountActionError("credentials", "Email or password is incorrect.");

  const ipBucket = await takeRateBucket(sql, `login:ip:${input.ipKey}`, 30, 900);
  if (!ipBucket.allowed) throw new AccountActionError("rate-limited", "Too many sign-in attempts. Try again later.");
  const accountBucket = await takeRateBucket(sql, `login:email:${email.email}`, 10, 900);
  if (!accountBucket.allowed) throw new AccountActionError("rate-limited", "Too many sign-in attempts for this account. Try again later.");

  const rows = await sql`
    SELECT id, email, handle, status, created_at, password_hash
    FROM network_accounts WHERE email = ${email.email} LIMIT 1
  `;
  const row = rows[0];
  const ok = row !== undefined && verifyPassword(password.password, row.password_hash as string);
  if (!ok) {
    // Uniform failure: never reveal whether the address exists.
    throw new AccountActionError("credentials", "Email or password is incorrect.");
  }
  if (row!.status !== "active") {
    throw new AccountActionError("deactivated", "This account is deactivated.");
  }
  const account = serializeAccount(row!);
  const sessionToken = await createSession(sql, account.id);
  return { account, sessionToken };
}

export async function createSession(sql: postgres.Sql, accountId: string): Promise<string> {
  const token = generateToken();
  await sql`
    INSERT INTO network_sessions (account_id, token_hash, expires_at)
    VALUES (${accountId}, ${tokenDigest(token)}, now() + (${SESSION_TTL_MILLISECONDS / 1000} * INTERVAL '1 second'))
  `;
  return token;
}

export type SessionContext = { account: AccountRecord; sessionId: string };

export async function accountForSessionToken(sql: postgres.Sql, token: string): Promise<SessionContext | null> {
  const rows = await sql`
    SELECT a.id, a.email, a.handle, a.status, a.created_at, s.id AS session_id, s.expires_at
    FROM network_sessions s
    JOIN network_accounts a ON a.id = s.account_id
    WHERE s.token_hash = ${tokenDigest(token)} AND s.revoked_at IS NULL AND s.expires_at > now()
    LIMIT 1
  `;
  const row = rows[0];
  if (row === undefined || row.status !== "active") return null;
  return {
    account: serializeAccount(row),
    sessionId: row.session_id as string,
  };
}

export async function revokeSession(sql: postgres.Sql, sessionId: string): Promise<void> {
  await sql`UPDATE network_sessions SET revoked_at = now() WHERE id = ${sessionId} AND revoked_at IS NULL`;
}

function serializeAccount(row: Record<string, unknown>): AccountRecord {
  return {
    id: row.id as string,
    email: row.email as string,
    handle: row.handle as string,
    status: row.status as string,
    created_at: (row.created_at as Date).toISOString(),
  };
}

/** Client-identity key for per-IP buckets (proxy-established or socket; never a secret). */
export function clientKeyFromHeaders(headers: Headers): string {
  const forwarded = headers.get("x-forwarded-for");
  const first = forwarded?.split(",")[0]?.trim();
  return first !== undefined && first.length > 0 && first.length <= 64 ? first : "unknown-client";
}

export function tokenSummary(token: string): string {
  return tokenDisplayPrefix(token);
}
