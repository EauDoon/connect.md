/**
 * Account identifiers, handles, and emails for the network MVP.
 *
 * Fail-closed validation: every rule lives here once, is enforced on write,
 * and is shared by API routes, pages, and tests.
 */

const HANDLE_PATTERN = /^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$/;
const EMAIL_PATTERN = /^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{2,63}$/;

/** Handles that would collide with navigation or public contracts. */
const RESERVED_HANDLES = new Set([
  "account", "agents", "agent-directory", "api", "discover", "feed", "human",
  "inbox", "jobs", "legal", "md", "messages", "moderation", "network",
  "organizations", "posts", "representatives", "root", "search", "security",
  "static", "trust", "verification-review", "workspace", "admin", "connectmd",
  "about", "help", "support", "status",
]);

export type HandleValidation =
  | { ok: true; handle: string }
  | { ok: false; reason: string };

export function validateHandle(raw: unknown): HandleValidation {
  if (typeof raw !== "string") return { ok: false, reason: "Handle must be a string." };
  const handle = raw.trim().toLowerCase();
  if (handle !== raw.trim()) return { ok: false, reason: "Handle must already be lowercase." };
  if (!HANDLE_PATTERN.test(handle)) {
    return { ok: false, reason: "Handle must be 3-30 characters of lowercase letters, digits, or hyphens, starting and ending with a letter or digit." };
  }
  if (RESERVED_HANDLES.has(handle)) {
    return { ok: false, reason: "That handle is reserved." };
  }
  if (handle.includes("--")) {
    return { ok: false, reason: "Handle must not contain consecutive hyphens." };
  }
  return { ok: true, handle };
}

const HANDLE_PREFIX_PATTERN = /^[a-z0-9-]{1,30}$/;

/**
 * Normalize an untrusted handle for a public read, or null when it cannot name
 * any stored handle. Reads are lenient about case and surrounding whitespace;
 * writes stay strict through validateHandle.
 */
export function normalizeHandleLookup(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const handle = raw.trim().toLowerCase();
  return HANDLE_PATTERN.test(handle) ? handle : null;
}

/**
 * Normalize an untrusted discovery prefix, or null when it is not a plain
 * handle fragment. The alphabet excludes the LIKE wildcards % and _, so a
 * normalized prefix is always a literal match and needs no ESCAPE clause.
 */
export function normalizeHandlePrefix(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const prefix = raw.trim().toLowerCase();
  return HANDLE_PREFIX_PATTERN.test(prefix) ? prefix : null;
}

export type EmailValidation =
  | { ok: true; email: string }
  | { ok: false; reason: string };

/** Bounded, conservative email shape. Deliverability is verified later; nothing here trusts the string. */
export function validateEmail(raw: unknown): EmailValidation {
  if (typeof raw !== "string") return { ok: false, reason: "Email must be a string." };
  const email = raw.trim().toLowerCase();
  if (email.length < 6 || email.length > 254) return { ok: false, reason: "Email must be 6-254 characters." };
  if (!EMAIL_PATTERN.test(email)) return { ok: false, reason: "Email must be a plain address like person@example.com." };
  return { ok: true, email };
}

export type PasswordValidation =
  | { ok: true; password: string }
  | { ok: false; reason: string };

export function validatePassword(raw: unknown): PasswordValidation {
  if (typeof raw !== "string") return { ok: false, reason: "Password must be a string." };
  if (raw.length < 10) return { ok: false, reason: "Password must be at least 10 characters." };
  if (raw.length > 200) return { ok: false, reason: "Password must be at most 200 characters." };
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((p) => p.test(raw)).length;
  if (classes < 2) {
    return { ok: false, reason: "Password must mix at least two of: lowercase, uppercase, digits, symbols." };
  }
  return { ok: true, password: raw };
}
