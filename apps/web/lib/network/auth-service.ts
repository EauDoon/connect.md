/**
 * Account and session service for the network MVP (server-side only).
 *
 * Sessions are server-side: a random 256-bit token in an HttpOnly cookie,
 * SHA-256 digest at rest, revocable, expiring. Login and registration are
 * rate-limited with durable per-account and per-IP buckets, so serverless
 * cold starts cannot reset an attacker's budget.
 */

import postgres from "postgres";
import { isIP } from "node:net";
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

// The uniform-failure path verifies unknown emails against a dummy hash so a
// miss costs the same derivation as a hit. It is derived lazily, once, on the
// first sign-in, never at import: every network route imports this module,
// including public reads that never touch a password.
let dummyPasswordHashPromise: Promise<string> | null = null;
function dummyPasswordHash(): Promise<string> {
  if (dummyPasswordHashPromise === null) {
    dummyPasswordHashPromise = hashPassword("network-dummy-password");
    dummyPasswordHashPromise.catch(() => { dummyPasswordHashPromise = null; });
  }
  return dummyPasswordHashPromise;
}

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
  const emailBucket = await takeRateBucket(sql, `register:email:${tokenDigest(email.email)}`, 5, 3600);
  if (!emailBucket.allowed) throw new AccountActionError("rate-limited", "Too many registrations for this address. Try again later.");

  const existing = await sql`
    SELECT email, handle FROM network_accounts
    WHERE email = ${email.email} OR handle = ${handle.handle} LIMIT 1
  `;
  if (existing.length > 0) {
    throw new AccountActionError("conflict", "Unable to register with those account details. Try signing in or use different details.");
  }

  const passwordHash = await hashPassword(password.password);
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
  const accountBucket = await takeRateBucket(sql, `login:email:${tokenDigest(email.email)}`, 10, 900);
  if (!accountBucket.allowed) throw new AccountActionError("rate-limited", "Too many sign-in attempts for this account. Try again later.");

  const rows = await sql`
    SELECT id, email, handle, status, created_at, password_hash
    FROM network_accounts WHERE email = ${email.email} LIMIT 1
  `;
  const row = rows[0];
  // Awaited for hits and misses alike, so the one-time dummy derivation on a
  // fresh instance cannot distinguish a known address from an unknown one.
  const dummyHash = await dummyPasswordHash();
  const rowHash = typeof row?.password_hash === "string" ? row.password_hash : null;
  const ok = await verifyPassword(password.password, rowHash ?? dummyHash);
  if (!ok || row === undefined || rowHash === null) {
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
  await pruneExpiredAuthStateBestEffort(sql);
  return token;
}

/** Rows removed per table per prune, so one sign-in never pays for a large backlog. */
export const AUTH_STATE_PRUNE_BATCH = 500;

/**
 * Delete auth state that can no longer affect any decision: rate buckets whose
 * window started more than a day ago (the longest window is one hour, and a
 * stale bucket resets to 1 on its next use anyway) and sessions that expired
 * or were revoked more than a day ago (accountForSessionToken already ignores
 * them). Bucket keys embed email digests, so this bounds how long those
 * digests are retained. Each delete repeats its predicate, so a row a
 * concurrent request just refreshed is re-checked and kept.
 */
export async function pruneExpiredAuthState(sql: postgres.Sql): Promise<{ buckets: number; sessions: number }> {
  const buckets = await sql`
    DELETE FROM network_auth_buckets
    WHERE bucket_key IN (
      SELECT bucket_key FROM network_auth_buckets
      WHERE window_started_at < now() - INTERVAL '1 day'
      LIMIT ${AUTH_STATE_PRUNE_BATCH}
    )
    AND window_started_at < now() - INTERVAL '1 day'
  `;
  const sessions = await sql`
    DELETE FROM network_sessions
    WHERE id IN (
      SELECT id FROM network_sessions
      WHERE expires_at < now() - INTERVAL '1 day' OR revoked_at < now() - INTERVAL '1 day'
      LIMIT ${AUTH_STATE_PRUNE_BATCH}
    )
    AND (expires_at < now() - INTERVAL '1 day' OR revoked_at < now() - INTERVAL '1 day')
  `;
  return { buckets: buckets.count, sessions: sessions.count };
}

/** Pruning is housekeeping: a failure is logged without detail and never fails a sign-in. */
async function pruneExpiredAuthStateBestEffort(sql: postgres.Sql): Promise<void> {
  try {
    await pruneExpiredAuthState(sql);
  } catch (error) {
    const name = error instanceof Error ? error.name : typeof error;
    const raw = error !== null && typeof error === "object" ? (error as { code?: unknown }).code : undefined;
    const code = typeof raw === "string" && /^[A-Za-z0-9_.-]{1,64}$/.test(raw) ? raw : null;
    console.error("[connectmd-network] prune failed", { name, code });
  }
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

/** Only Vercel's overwritten IP header is trusted. Other hosts share a bounded bucket. */
export function clientKeyFromHeaders(headers: Headers): string {
  const address = process.env.VERCEL === "1" ? headers.get("x-vercel-forwarded-for")?.trim() : null;
  return address && isIP(address) ? tokenDigest(address) : "unverified-client";
}

export function tokenSummary(token: string): string {
  return tokenDisplayPrefix(token);
}
