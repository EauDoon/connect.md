import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError } from "../lib/api";
import { beginLogicalMutationAttempt, claimLogicalMutation, fingerprintMutationIntent, newIdempotencyKey, retainLogicalMutationAttempt, settleLogicalMutationAttempt } from "../lib/logical-mutation";

afterEach(() => { vi.unstubAllGlobals(); });

describe("logical mutation attempts", () => {
  it("reuses the key only for the same canonical intent and hides body bytes in the fingerprint", () => {
    const first = beginLogicalMutationAttempt(null, "subject-1", { resource: "post-1", body: "private narrative" }, () => "key-1");
    const retry = beginLogicalMutationAttempt(first, "subject-1", { body: "private narrative", resource: "post-1" }, () => "key-2");
    const changed = beginLogicalMutationAttempt(retry, "subject-1", { resource: "post-1", body: "changed" }, () => "key-3");
    expect(retry).toBe(first);
    expect(changed.idempotencyKey).toBe("key-3");
    expect(fingerprintMutationIntent({ resource: "post-1", body: "private narrative" })).not.toContain("private");
    expect(fingerprintMutationIntent({ resource: "post-1", body: "private narrative" })).toHaveLength(16);
  });

  it("rotates the key when the captured subject changes even if intent is unchanged", () => {
    const first = beginLogicalMutationAttempt(null, "subject-1", { resource: "post-1", body: "same" }, () => "key-1");
    const retry = beginLogicalMutationAttempt(first, "subject-1", { body: "same", resource: "post-1" }, () => "key-2");
    const changedSubject = beginLogicalMutationAttempt(retry, "subject-2", { resource: "post-1", body: "same" }, () => "key-3");
    expect(retry).toBe(first);
    expect(changedSubject.idempotencyKey).toBe("key-3");
    expect(changedSubject).not.toBe(first);
  });

  it("rotates for action and target changes while retaining the same exact intent", () => {
    const first = beginLogicalMutationAttempt(null, "subject-1", { operation: "follow-profile", handle: "ari-chen" }, () => "key-1");
    const changedAction = beginLogicalMutationAttempt(first, "subject-1", { operation: "unfollow-profile", handle: "ari-chen" }, () => "key-2");
    const changedTarget = beginLogicalMutationAttempt(changedAction, "subject-1", { operation: "unfollow-profile", handle: "bea-lee" }, () => "key-3");
    expect(changedAction.idempotencyKey).toBe("key-2");
    expect(changedTarget.idempotencyKey).toBe("key-3");
  });

  it("retains only ambiguous network/server acknowledgement loss", () => {
    expect(retainLogicalMutationAttempt(new ApiRequestError("lost", undefined, "request"))).toBe(true);
    expect(retainLogicalMutationAttempt(new ApiRequestError("server", 503, "server"))).toBe(true);
    expect(retainLogicalMutationAttempt(new ApiRequestError("offline", undefined, "offline"))).toBe(false);
    expect(retainLogicalMutationAttempt(new ApiRequestError("bad request", 422, "request"))).toBe(false);
    expect(retainLogicalMutationAttempt(new ApiRequestError("unauthorized", 401, "unauthorized"))).toBe(false);
    const attempt = { fingerprint: "intent", idempotencyKey: "key" };
    expect(settleLogicalMutationAttempt(attempt, new ApiRequestError("lost", undefined, "request"))).toBe(attempt);
    expect(settleLogicalMutationAttempt(attempt, new ApiRequestError("rejected", 409, "request"))).toBeNull();
    expect(settleLogicalMutationAttempt(attempt, new ApiRequestError("malformed", 502, "server"))).toBe(attempt);
    const rotated = beginLogicalMutationAttempt(null, "subject-1", { operation: "follow-profile", handle: "ari-chen" }, () => "key-2");
    expect(rotated.idempotencyKey).toBe("key-2");
  });

  it("allows one synchronous owner and prevents a stale owner from releasing the current claim", () => {
    const slot = { current: null };
    const first = claimLogicalMutation(slot);
    expect(first).not.toBeNull();
    expect(claimLogicalMutation(slot)).toBeNull();
    expect(first?.isCurrent()).toBe(true);
    first?.release();
    const current = claimLogicalMutation(slot);
    expect(current).not.toBeNull();
    first?.release();
    expect(current?.isCurrent()).toBe(true);
    current?.release();
    expect(slot.current).toBeNull();
  });

  it("suppresses stale success, catch, and finally completion mutations", () => {
    const slot = { current: null };
    const stale = claimLogicalMutation(slot);
    expect(stale).not.toBeNull();
    stale?.release();
    const current = claimLogicalMutation(slot);
    expect(current).not.toBeNull();
    const mutations: string[] = [];
    if (stale?.isCurrent()) mutations.push("success");
    if (stale?.isCurrent()) mutations.push("catch");
    if (stale?.isCurrent()) {
      stale.release();
      mutations.push("finally");
    }
    expect(mutations).toEqual([]);
    expect(current?.isCurrent()).toBe(true);
    current?.release();
  });

  it("creates UUID keys through the browser crypto boundary", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "uuid-1" });
    expect(newIdempotencyKey()).toBe("uuid-1");
  });
});
