/**
 * Server-side session cookie + shared HTTP helpers for network routes.
 */

import { randomUUID } from "node:crypto";
import { cookies } from "next/headers";
import postgres from "postgres";
import { PROFILE_RESUME_MAX_UTF8_BYTES } from "@/lib/markdown";
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
// JSON escaping may use six ASCII bytes for one canonical Markdown byte.
export const MAX_PROFILE_JSON_BYTES = 6 * PROFILE_RESUME_MAX_UTF8_BYTES + 128;

type NetworkOriginEnv = { CONNECTMD_NETWORK_ORIGIN?: string; NEXT_PUBLIC_SITE_URL?: string };

/**
 * The browser origin that cookie mutations must come from, or null when none
 * can be trusted. A non-empty CONNECTMD_NETWORK_ORIGIN wins; a blank one falls
 * back to NEXT_PUBLIC_SITE_URL. Surrounding whitespace and a bare trailing "/"
 * are normalized away. A path, query, fragment, credentials, or a non-HTTP(S)
 * scheme makes the value untrusted, and a malformed explicit override never
 * falls back to the site URL.
 */
export function configuredNetworkOrigin(env: NetworkOriginEnv = {
  CONNECTMD_NETWORK_ORIGIN: process.env.CONNECTMD_NETWORK_ORIGIN,
  NEXT_PUBLIC_SITE_URL: process.env.NEXT_PUBLIC_SITE_URL,
}): string | null {
  const override = env.CONNECTMD_NETWORK_ORIGIN?.trim();
  const raw = override ? override : env.NEXT_PUBLIC_SITE_URL?.trim();
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (
    !["https:", "http:"].includes(url.protocol)
    || url.username
    || url.password
    || url.pathname !== "/"
    || url.search
    || url.hash
  ) return null;
  return url.origin;
}

/** Cookie mutations require an exact configured origin, including bodyless actions. */
export function rejectCrossOrigin(request: Request): Response | null {
  const configured = configuredNetworkOrigin();
  const origin = request.headers.get("origin");
  if (configured === null || origin !== configured || request.headers.get("sec-fetch-site") === "cross-site") {
    return jsonResponse({ ok: false, reason: "origin-denied" }, 403);
  }
  return null;
}

export async function readBoundedJson(request: Request, fields: readonly string[], limit = MAX_JSON_BODY_BYTES): Promise<Record<string, unknown> | null> {
  if (request.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json") return null;
  const declared = request.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) return null;
  if (request.body === null) return null;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); return null; }
      chunks.push(value);
    }
    const text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
    const parsed = JSON.parse(text) as unknown;
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const record = parsed as Record<string, unknown>;
    if (Object.keys(record).some((key) => !fields.includes(key))) return null;
    if (Object.values(record).some((value) => value !== null && typeof value === "object"
      && (!Array.isArray(value) || value.length > 3 || value.some((entry) => typeof entry !== "string")))) return null;
    // These endpoints accept flat objects only. Check their original key tokens
    // because JSON.parse otherwise silently accepts duplicate/escaped keys.
    let depth = 0;
    const seen = new Set<string>();
    for (const match of text.matchAll(/"(?:[^"\\]|\\.)*"|[{}\[\]]/g)) {
      const token = match[0];
      if (token === "{" || token === "[") { depth++; continue; }
      if (token === "}" || token === "]") { depth--; continue; }
      let next = match.index + token.length;
      while (/\s/.test(text[next] ?? "") && next < text.length) next++;
      if (depth === 1 && text[next] === ":") {
        const key = JSON.parse(token) as string;
        if (seen.has(key)) return null;
        seen.add(key);
      }
    }
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  } finally {
    reader.releaseLock();
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
    if (error instanceof postgres.PostgresError) {
      if (error.code === "23505") return jsonResponse({ ok: false, reason: "conflict", message: "That operation conflicts with existing data." }, 409);
      if (error.code === "22P02") return jsonResponse({ ok: false, reason: "request-invalid" }, 400);
    }
    // Anything else is unexpected. Log a correlation id with the error class and
    // code only: messages, details, queries, and parameters can carry personal data.
    const errorId = randomUUID();
    console.error("[connectmd-network] unexpected route failure", { errorId, ...errorShape(error) });
    return jsonResponse(
      { ok: false, reason: "network-unavailable", message: "The network is temporarily unavailable. Try again later.", errorId },
      503,
      { "x-connectmd-error-id": errorId },
    );
  });
}

function errorShape(error: unknown): { name: string; code: string | null } {
  const name = error instanceof Error ? error.name : typeof error;
  const raw = error !== null && typeof error === "object" ? (error as { code?: unknown }).code : undefined;
  const code = typeof raw === "string" && /^[A-Za-z0-9_.-]{1,64}$/.test(raw) ? raw : null;
  return { name, code };
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
