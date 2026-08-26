const INBOX_CONTACT_QUERY_KEY = "profile";
const MAX_CANONICAL_PROFILE_HANDLE_LENGTH = 64;
const PROFILE_RETURN_PATH_PREFIX = "/p/";
const INBOX_CONTACT_RETURN_PATH_PREFIX = `/inbox?${INBOX_CONTACT_QUERY_KEY}=`;
const MAX_AUTH_RETURN_PATH_LENGTH = Math.max(
  PROFILE_RETURN_PATH_PREFIX.length + MAX_CANONICAL_PROFILE_HANDLE_LENGTH,
  INBOX_CONTACT_RETURN_PATH_PREFIX.length + MAX_CANONICAL_PROFILE_HANDLE_LENGTH,
);
const PROFILE_HANDLE_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/u;
const PROFILE_RETURN_PATH_PATTERN = /^\/p\/([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)$/u;
const INBOX_CONTACT_RETURN_PATH_PATTERN = /^\/inbox\?profile=([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)$/u;

export const AUTH_RETURN_ACTIONS = ["connect", "follow", "block"] as const;
export type AuthReturnAction = (typeof AUTH_RETURN_ACTIONS)[number];

/**
 * Build the only public-profile return target currently used by private action
 * controls. The action is deliberately not serialized: returning to the same
 * public profile lets the person explicitly confirm the action after auth.
 */
export function buildProfileActionReturnPath(handle: unknown, action: unknown): string | null {
  if (!isAuthReturnAction(action) || !isCanonicalProfileHandle(handle)) return null;
  return `${PROFILE_RETURN_PATH_PREFIX}${handle}`;
}

/**
 * Build the only inbox return target that may carry public discovery context.
 * It contains one canonical profile handle and never encodes an action, agent
 * handle, message, policy, mandate, or outbound request.
 */
export function buildInboxContactReturnPath(handle: unknown): string | null {
  if (!isCanonicalProfileHandle(handle)) return null;
  return `${INBOX_CONTACT_RETURN_PATH_PREFIX}${handle}`;
}

/**
 * Parse the only accepted inbox prefill. Extra parameters are rejected so a
 * public link cannot smuggle a purpose, message, action, or arbitrary return
 * destination into the private composer.
 */
export function parseInboxContactProfileIntent(params: Pick<URLSearchParams, "getAll" | "keys">): string | null {
  const keys = [...params.keys()];
  if (keys.length !== 1 || keys[0] !== INBOX_CONTACT_QUERY_KEY) return null;
  const values = params.getAll(INBOX_CONTACT_QUERY_KEY);
  if (values.length !== 1 || !isCanonicalProfileHandle(values[0])) return null;
  return values[0];
}

export function isCanonicalProfileHandle(value: unknown): value is string {
  return typeof value === "string" && PROFILE_HANDLE_PATTERN.test(value);
}

/**
 * Accept only a canonical, same-origin profile path. Query strings, fragments,
 * encoded paths, control characters, backslashes, and every other route are
 * rejected so this value can be passed to Clerk without open-redirect state.
 */
export function parseSafeAuthReturnPath(candidate: unknown): string | null {
  if (typeof candidate !== "string" || candidate.length > MAX_AUTH_RETURN_PATH_LENGTH || /[\u0000-\u001F\u007F\\]/u.test(candidate)) return null;
  const match = PROFILE_RETURN_PATH_PATTERN.exec(candidate);
  if (match) return `${PROFILE_RETURN_PATH_PREFIX}${match[1]}`;
  const inboxMatch = INBOX_CONTACT_RETURN_PATH_PATTERN.exec(candidate);
  return inboxMatch ? buildInboxContactReturnPath(inboxMatch[1]) : null;
}

function isAuthReturnAction(value: unknown): value is AuthReturnAction {
  return typeof value === "string" && (AUTH_RETURN_ACTIONS as readonly string[]).includes(value);
}
