/**
 * Password hashing (scrypt) and secret-token primitives for the network MVP.
 *
 * Passwords use Node's libcrypto scrypt with the same memory-hard parameters
 * as the operator vault (N=2^15, r=8, p=1, explicit 64 MiB maxmem), a unique
 * 16-byte per-password salt, and a constant-time verify. Stored strings are
 * self-describing (`scrypt$N$r$p$salt$hash`) so parameters can be raised
 * later without breaking existing verifiers. Derivation runs on libuv's
 * thread pool (async scrypt), so a 32 MiB derivation never blocks the request
 * event loop, and stored parameters are bounded before any derivation so a
 * corrupt or hostile row fails verification instead of throwing.
 *
 * Session and agent-grant tokens are 32 random bytes, base64url-encoded and
 * shown once; only their SHA-256 digest is stored. Verification is by digest
 * lookup, so a database leak does not leak usable credentials.
 */

import { createHash, randomBytes, scrypt, timingSafeEqual, type ScryptOptions } from "node:crypto";

const SCRYPT_N = 2 ** 15;
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const SCRYPT_KEY_LENGTH = 64;
const SCRYPT_MAXMEM = 64 * 1024 * 1024;

/** Lowest cost a stored hash may declare; anything weaker is refused, not trusted. */
const SCRYPT_MIN_N = 2 ** 14;
const SCRYPT_MAX_R = 16;
const SCRYPT_MAX_P = 4;

export const PASSWORD_MIN_LENGTH = 10;

function deriveKey(password: string, salt: Buffer, keyLength: number, options: ScryptOptions): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    scrypt(password.normalize("NFKC"), salt, keyLength, options, (error, key) => {
      if (error) reject(error);
      else resolve(key);
    });
  });
}

/** True when stored scrypt parameters are within the cost and memory bounds this verifier accepts. */
export function scryptParametersAcceptable(n: number, r: number, p: number): boolean {
  return Number.isSafeInteger(n) && Number.isSafeInteger(r) && Number.isSafeInteger(p)
    && n >= SCRYPT_MIN_N && (n & (n - 1)) === 0
    && r >= 1 && r <= SCRYPT_MAX_R
    && p >= 1 && p <= SCRYPT_MAX_P
    && 128 * n * r <= SCRYPT_MAXMEM;
}

export async function hashPassword(password: string): Promise<string> {
  if (password.length < PASSWORD_MIN_LENGTH) throw new RangeError("password below minimum length");
  const salt = randomBytes(16);
  const key = await deriveKey(password, salt, SCRYPT_KEY_LENGTH, {
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

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;
  const [, nText, rText, pText, saltText, keyText] = parts;
  if (![nText, rText, pText].every((text) => /^[0-9]{1,10}$/.test(text ?? ""))) return false;
  const n = Number(nText);
  const r = Number(rText);
  const p = Number(pText);
  if (!scryptParametersAcceptable(n, r, p)) return false;
  let salt: Buffer;
  let expected: Buffer;
  try {
    salt = Buffer.from(saltText ?? "", "base64");
    expected = Buffer.from(keyText ?? "", "base64");
  } catch {
    return false;
  }
  if (salt.length !== 16 || expected.length !== SCRYPT_KEY_LENGTH) return false;
  let actual: Buffer;
  try {
    actual = await deriveKey(password, salt, expected.length, { N: n, r, p, maxmem: SCRYPT_MAXMEM });
  } catch {
    return false;
  }
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
