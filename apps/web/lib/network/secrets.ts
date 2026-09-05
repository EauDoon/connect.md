/**
 * Password hashing (scrypt) and secret-token primitives for the network MVP.
 *
 * Passwords use Node's libcrypto scrypt with the same memory-hard parameters
 * as the operator vault (N=2^15, r=8, p=1, explicit 64 MiB maxmem), a unique
 * 16-byte per-password salt, and a constant-time verify. Stored strings are
 * self-describing (`scrypt$N$r$p$salt$hash`) so parameters can be raised
 * later without breaking existing verifiers.
 *
 * Session and agent-grant tokens are 32 random bytes, base64url-encoded and
 * shown once; only their SHA-256 digest is stored. Verification is by digest
 * lookup, so a database leak does not leak usable credentials.
 */

import { createHash, randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

const SCRYPT_N = 2 ** 15;
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const SCRYPT_KEY_LENGTH = 64;
const SCRYPT_MAXMEM = 64 * 1024 * 1024;

export const PASSWORD_MIN_LENGTH = 10;

export function hashPassword(password: string): string {
  if (password.length < PASSWORD_MIN_LENGTH) throw new RangeError("password below minimum length");
  const salt = randomBytes(16);
  const key = scryptSync(password.normalize("NFKC"), salt, SCRYPT_KEY_LENGTH, {
    N: SCRYPT_N,
    r: SCRYPT_R,
    p: SCRYPT_P,
    maxmem: SCRYPT_MAXMEM,
  });
  return [
    "scrypt",
    String(SCRYPT_N),
    String(SCRYPT_R),
    String(SCRYPT_P),
    salt.toString("base64"),
    key.toString("base64"),
  ].join("$");
}

export function verifyPassword(password: string, stored: string): boolean {
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;
  const [, nText, rText, pText, saltText, keyText] = parts;
  const n = Number.parseInt(nText ?? "", 10);
  const r = Number.parseInt(rText ?? "", 10);
  const p = Number.parseInt(pText ?? "", 10);
  if (!Number.isFinite(n) || !Number.isFinite(r) || !Number.isFinite(p)) return false;
  let salt: Buffer;
  let expected: Buffer;
  try {
    salt = Buffer.from(saltText ?? "", "base64");
    expected = Buffer.from(keyText ?? "", "base64");
  } catch {
    return false;
  }
  if (salt.length !== 16 || expected.length !== SCRYPT_KEY_LENGTH) return false;
  const actual = scryptSync(password.normalize("NFKC"), salt, expected.length, {
    N: n,
    r,
    p,
    maxmem: SCRYPT_MAXMEM,
  });
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

export function generateToken(): string {
  return randomBytes(32).toString("base64url");
}

export function tokenDigest(token: string): string {
  return createHash("sha256").update(token, "utf8").digest("hex");
}

/** Display prefix for a shown-once token (first 8 characters + ellipsis). */
export function tokenDisplayPrefix(token: string): string {
  return token.slice(0, 8) + "…";
}

export function constantTimeEquals(left: string, right: string): boolean {
  const leftDigest = createHash("sha256").update(left, "utf8").digest();
  const rightDigest = createHash("sha256").update(right, "utf8").digest();
  return leftDigest.equals(rightDigest);
}
