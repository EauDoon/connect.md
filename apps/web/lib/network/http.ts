/**
 * Server-side session cookie + shared HTTP helpers for network routes.
 */

import { cookies } from "next/headers";
import { SESSION_COOKIE_NAME, type SessionContext, accountForSessionToken, revokeSession, type AccountActionError } from "./auth-service";
import { database, NetworkUnavailableError } from "./db";

export const SESSION_COOKIE_MAX_AGE_SECONDS = 14 * 24 * 3600;

export function sessionCookieOptions(maxAge = SESSION_COOKIE_MAX_AGE_SECONDS) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production" || process.env.CONNECTMD_NETWORK_INSECURE_COOKIE !== "1",
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

export async function setSessionCookie(token: string): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE_NAME, token, sessionCookieOptions());
}

export async function clearSessionCookie(): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE_NAME, "", { ...sessionCookieOptions(0) });
}

export async function currentSession(): Promise<SessionContext | null> {
  const store = await cookies();
  const token = store.get(SESSION_COOKIE_NAME)?.value;
  if (token === undefined || token.length < 20) return null;
  return accountForSessionToken(database(), token);
}

export async function revokeCurrentSession(sessionId: string): Promise<void> {
  await revokeSession(database(), sessionId);
}

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  });
}

export const MAX_JSON_BODY_BYTES = 8 * 1024;

export async function readBoundedJson(request: Request): Promise<Record<string, unknown> | null> {
  const declared = request.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_JSON_BODY_BYTES)) return null;
  const text = await request.text();
  if (text.length > MAX_JSON_BODY_BYTES) return null;
  try {
    const parsed = JSON.parse(text) as unknown;
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** Wrap a route handler so a missing database degrades to an explicit 503 contract. */
export function withNetworkUnavailable(handler: () => Promise<Response>): Promise<Response> {
  return handler().catch((error: unknown) => {
    if (error instanceof NetworkUnavailableError) {
      return jsonResponse(
        { ok: false, reason: "network-database-not-configured" },
        503,
        { "x-connectmd-network": "unavailable" },
      );
    }
    throw error;
  });
}

export function accountErrorStatus(error: AccountActionError): number {
  switch (error.code) {
    case "invalid": return 400;
    case "conflict": return 409;
    case "credentials": return 401;
    case "rate-limited": return 429;
    case "deactivated": return 403;
  }
}
