import { afterEach, describe, expect, it, vi } from "vitest";

import { ACCOUNT_DELETION_INTENT, accountLifecycleFeatureEnabled, cancelAccountDeletion, confirmAccountDeletion, exportAccount, fetchAccountLifecycleStatus, lifecycleResult, parseDeletionConfirmation, parseDeletionRequest, presentLifecycleError, recoverAccountDeletionReceipt, requestAccountDeletion } from "../lib/account-lifecycle-api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt } from "../lib/logical-mutation";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

function configure(responses: Response[]) {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
  const fetchMock = vi.fn<typeof fetch>();
  responses.forEach((response) => fetchMock.mockResolvedValueOnce(response));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("account lifecycle API contracts", () => {
  it("keeps the private lifecycle UI disabled unless explicitly enabled", () => {
    expect(accountLifecycleFeatureEnabled()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED", "true");
    expect(accountLifecycleFeatureEnabled()).toBe(true);
  });

  it("uses the direct NDJSON export and empty-body deletion contracts with ordinary Clerk authorization", async () => {
    const fetchMock = configure([
      new Response('{"schema":"connect.md/account-export"}\n', { status: 200, headers: { "Content-Type": "application/x-ndjson" } }),
      new Response(JSON.stringify({ deletion_id: "deletion-1", status_receipt: `lr1_${"a".repeat(43)}` }), { status: 202, headers: { "Content-Type": "application/json" } }),
      new Response(JSON.stringify({ deletion_id: "deletion-1" }), { status: 202, headers: { "Content-Type": "application/json" } }),
      new Response(null, { status: 204 })
    ]);
    const token = async () => "clerk-fresh-session-token";

    const exportResponse = await exportAccount(token, () => true);
    expect(exportResponse.headers.get("content-type")).toContain("application/x-ndjson");
    expect(await exportResponse.text()).toBe('{"schema":"connect.md/account-export"}\n');
    expect(parseDeletionRequest(await lifecycleResult(await requestAccountDeletion("request-1", token, () => true)))).toEqual({ deletionId: "deletion-1", statusReceipt: `lr1_${"a".repeat(43)}` });
    await expect(confirmAccountDeletion("deletion-1", token, () => true, "confirm-1")).resolves.toEqual({ deletionId: "deletion-1" });
    await cancelAccountDeletion("deletion-1", token, () => true);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.connect.test/v1/account/export",
      "https://api.connect.test/v1/account-deletion-requests",
      "https://api.connect.test/v1/account-deletion-requests/deletion-1/confirm",
      "https://api.connect.test/v1/account-deletion-requests/deletion-1/cancel"
    ]);
    const [, exportInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [, requestInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    const [, confirmInit] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(new Headers(exportInit.headers).get("Authorization")).toBe("Bearer clerk-fresh-session-token");
    expect(new Headers(exportInit.headers).get("Accept")).toBe("application/x-ndjson");
    expect(new Headers(requestInit.headers).get("Authorization")).toBe("Bearer clerk-fresh-session-token");
    expect(new Headers(requestInit.headers).get("Idempotency-Key")).toBe("request-1");
    expect(requestInit.body).toBeUndefined();
    expect(confirmInit.method).toBe("POST");
    expect(new Headers(confirmInit.headers).get("Authorization")).toBe("Bearer clerk-fresh-session-token");
    expect(new Headers(confirmInit.headers).get("Idempotency-Key")).toBe("confirm-1");
    expect(new Headers(confirmInit.headers).get("Content-Type")).toBeNull();
    expect(confirmInit.body).toBeUndefined();
    expect(new Headers(confirmInit.headers).get("x-clerk-reverification")).toBeNull();
  });

  it("requires a visible-ASCII confirmation key before dispatch", async () => {
    const fetchMock = configure([]);
    const token = async () => "clerk-fresh-session-token";

    for (const key of [undefined, "", "bad key", "x".repeat(129), "bad\u007fkey"]) {
      await expect(confirmAccountDeletion("deletion-1", token, () => true, key as never)).rejects.toMatchObject({ status: 400, code: "request" });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts only exact first or replayed 202 JSON confirmations", async () => {
    const token = async () => "clerk-fresh-session-token";
    const replay = configure([new Response(JSON.stringify({ deletion_id: "deletion-1" }), { status: 202, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "true" } })]);
    await expect(confirmAccountDeletion("deletion-1", token, () => true, "confirm-replay")).resolves.toEqual({ deletionId: "deletion-1" });
    expect(new Headers((replay.mock.calls[0][1] as RequestInit).headers).get("Idempotency-Key")).toBe("confirm-replay");

    const invalidBodies: unknown[] = [
      { deletion_id: "deletion-1", key: "secret" },
      { deletion_id: "deletion-1", recovery_required: true },
      { deletion_id: "deletion-1", owner_id: "private-owner" },
      { deletion_id: "other-deletion" },
      { deletion_id: "deletion-1", private_value: "private" }
    ];
    for (const body of invalidBodies) {
      const fetchMock = configure([new Response(JSON.stringify(body), { status: 202, headers: { "Content-Type": "application/json" } })]);
      await expect(confirmAccountDeletion("deletion-1", token, () => true, "confirm-invalid")).rejects.toMatchObject({ status: 502, code: "server" });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  });

  it("rejects wrong status, content type, and replay marker as acknowledgement ambiguity", async () => {
    const token = async () => "clerk-fresh-session-token";
    const responses = [
      new Response(JSON.stringify({ deletion_id: "deletion-1" }), { status: 200, headers: { "Content-Type": "application/json" } }),
      new Response(JSON.stringify({ deletion_id: "deletion-1" }), { status: 202, headers: { "Content-Type": "text/plain" } }),
      new Response(JSON.stringify({ deletion_id: "deletion-1" }), { status: 202, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "false" } }),
      new Response(null, { status: 204 })
    ];
    for (const response of responses) {
      configure([response]);
      await expect(confirmAccountDeletion("deletion-1", token, () => true, "confirm-invalid")).rejects.toMatchObject({ status: 502, code: "server" });
    }
  });

  it("retains an ambiguous key for exact retry and clears it after a definitive 4xx", async () => {
    const token = async () => "clerk-fresh-session-token";
    const intent = { operation: "confirm-account-deletion", deletionId: "deletion-1", intent: ACCOUNT_DELETION_INTENT };
    const first = beginLogicalMutationAttempt(null, "human-a", intent, () => "confirm-retry-1");
    const firstFetch = configure([new Response(JSON.stringify({ deletion_id: "deletion-1", recovery_required: true }), { status: 202, headers: { "Content-Type": "application/json" } })]);
    let failure: unknown;
    try {
      await confirmAccountDeletion("deletion-1", token, () => true, first.idempotencyKey);
    } catch (caught) {
      failure = caught;
    }
    const retained = settleLogicalMutationAttempt(first, failure);
    expect(retained).toBe(first);
    const retry = beginLogicalMutationAttempt(retained, "human-a", intent, () => "confirm-retry-2");
    expect(retry.idempotencyKey).toBe("confirm-retry-1");

    const secondFetch = configure([new Response(JSON.stringify({ deletion_id: "deletion-1" }), { status: 202, headers: { "Content-Type": "application/json", "Idempotency-Replayed": "true" } })]);
    await expect(confirmAccountDeletion("deletion-1", token, () => true, retry.idempotencyKey)).resolves.toEqual({ deletionId: "deletion-1" });
    expect(new Headers((firstFetch.mock.calls[0][1] as RequestInit).headers).get("Idempotency-Key")).toBe(new Headers((secondFetch.mock.calls[0][1] as RequestInit).headers).get("Idempotency-Key"));

    const definitive = beginLogicalMutationAttempt(retained, "human-a", intent, () => "confirm-retry-3");
    const rejected = configure([new Response(JSON.stringify({ detail: "request_rejected" }), { status: 409, headers: { "Content-Type": "application/json" } })]);
    let rejection: unknown;
    try {
      await confirmAccountDeletion("deletion-1", token, () => true, definitive.idempotencyKey);
    } catch (caught) {
      rejection = caught;
    }
    expect(settleLogicalMutationAttempt(definitive, rejection)).toBeNull();
    expect(rejected).toHaveBeenCalledTimes(1);
  });

  it("rotates pending receipts with Clerk authorization and reads status only with the receipt scheme", async () => {
    const receipt = `lr1_${"b".repeat(43)}`;
    const fetchMock = configure([
      new Response(JSON.stringify({ deletion_id: "deletion-1", status_receipt: receipt }), { status: 200, headers: { "Content-Type": "application/json" } }),
      new Response(JSON.stringify({ contract: "account_lifecycle_status.v1", state: "erasing", observed_at: "2026-08-04T00:00:00Z", requested_at: "2026-08-03T00:00:00Z", confirmed_at: "2026-08-03T01:00:00Z", live_erased_at: null, terminal_at: null, policy_version: "2026-08-01", condition: null, next_check_after_seconds: 60, receipt_expires_at: null }), { status: 200, headers: { "Content-Type": "application/json" } })
    ]);
    const token = async () => "clerk-fresh-session-token";

    expect(parseDeletionRequest(await lifecycleResult(await recoverAccountDeletionReceipt("recover-1", token, () => true)))).toEqual({ deletionId: "deletion-1", statusReceipt: receipt });
    await expect(fetchAccountLifecycleStatus(receipt)).resolves.toMatchObject({ contract: "account_lifecycle_status.v1", state: "erasing", nextCheckAfterSeconds: 60 });

    const [, recoveryInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [statusUrl, statusInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(recoveryInit.headers).get("Authorization")).toBe("Bearer clerk-fresh-session-token");
    expect(new Headers(recoveryInit.headers).get("Idempotency-Key")).toBe("recover-1");
    expect(statusUrl).toBe("https://api.connect.test/v1/account/lifecycle-status");
    expect(statusInit.method).toBe("POST");
    expect(new Headers(statusInit.headers).get("Authorization")).toBe(`LifecycleReceipt ${receipt}`);
    expect(new Headers(statusInit.headers).get("Authorization")).not.toContain("Bearer");
  });

  it("does not dispatch a protected lifecycle request after the signed-in subject changes", async () => {
    const fetchMock = configure([]);
    let current = true;
    await expect(requestAccountDeletion("request-1", async () => { current = false; return "other-account-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    await expect(confirmAccountDeletion("deletion-1", async () => { current = false; return "other-account-token"; }, () => current, "confirm-subject-change")).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not turn denied access into a claimed deletion outcome", () => {
    expect(ACCOUNT_DELETION_INTENT).toBe("DELETE");
    expect(() => parseDeletionRequest({ detail: "account_access_denied" })).toThrow("account_access_denied");
    expect(parseDeletionConfirmation({ deletion_id: "deletion-1" }, "deletion-1")).toEqual({ deletionId: "deletion-1" });
    expect(() => parseDeletionConfirmation({ deletion_id: "deletion-1", owner_id: "private-owner" }, "deletion-1")).toThrow("invalid account deletion confirmation");
    expect(() => parseDeletionConfirmation({ deletion_id: "other-deletion" }, "deletion-1")).toThrow("invalid account deletion confirmation");
    expect(presentLifecycleError(parseError({ detail: "account_access_denied" }))).toContain("confirms neither worker progress nor complete erasure");
    expect(presentLifecycleError(parseError({ detail: "account deletion request was not found" }))).toContain("no longer permits cancellation");
  });
});

function parseError(value: unknown) {
  try {
    parseDeletionRequest(value);
  } catch (caught) {
    return caught;
  }
  throw new Error("Expected account lifecycle error");
}
