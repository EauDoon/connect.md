import { ApiRequestError } from "@/lib/api";

export type LogicalMutationAttempt = {
  fingerprint: string;
  idempotencyKey: string;
};

export type LogicalMutationClaimSlot = { current: symbol | null };

export type LogicalMutationClaim = {
  isCurrent: () => boolean;
  release: () => void;
};

type KeyFactory = () => string;

/**
 * A local-only intent digest. It is deliberately opaque and is never suitable
 * for authorization, persistence, logging, URLs, or analytics. Callers keep
 * the full intent in their component state only long enough to decide whether
 * a retry is the same logical attempt.
 */
export function fingerprintMutationIntent(intent: unknown) {
  return fnv1a64(canonicalize(intent));
}

export function beginLogicalMutationAttempt(
  previous: LogicalMutationAttempt | null,
  subject: string,
  intent: unknown,
  keyFactory: KeyFactory = newIdempotencyKey,
) {
  const fingerprint = fingerprintMutationIntent({ subject, intent });
  return previous?.fingerprint === fingerprint
    ? previous
    : { fingerprint, idempotencyKey: keyFactory() };
}

/** Keep a key only when the server may have committed but acknowledgement was lost. */
export function retainLogicalMutationAttempt(error: unknown) {
  if (!(error instanceof ApiRequestError)) return false;
  if (error.status !== undefined && error.status >= 400 && error.status < 500) return false;
  return error.code === "request" || error.code === "server";
}

export function settleLogicalMutationAttempt(
  attempt: LogicalMutationAttempt,
  error: unknown,
) {
  return retainLogicalMutationAttempt(error) ? attempt : null;
}

/**
 * Claim one synchronous in-memory mutation owner. The symbol is deliberately
 * local to the slot and cannot be persisted, logged, or reused by another
 * component instance.
 */
export function claimLogicalMutation(slot: LogicalMutationClaimSlot): LogicalMutationClaim | null {
  if (slot.current !== null) return null;
  const token = Symbol();
  slot.current = token;
  return {
    isCurrent: () => slot.current === token,
    release: () => {
      if (slot.current === token) slot.current = null;
    },
  };
}

export function newIdempotencyKey() {
  if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function") {
    throw new ApiRequestError("This browser cannot create the idempotency key required for this action.", undefined, "configuration");
  }
  return crypto.randomUUID();
}

function canonicalize(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "bigint") return `${value}n`;
  if (typeof value === "undefined") return "undefined";
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  if (typeof value === "object") {
    return `{${Object.keys(value as Record<string, unknown>).sort().map((key) => `${JSON.stringify(key)}:${canonicalize((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  return typeof value;
}

function fnv1a64(value: string) {
  return `${fnv1a32(value, 0x811c9dc5).toString(16).padStart(8, "0")}${fnv1a32(value, 0x9e3779b9).toString(16).padStart(8, "0")}`;
}

function fnv1a32(value: string, seed: number) {
  let hash = seed >>> 0;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}
