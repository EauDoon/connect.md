import { ApiRequestError, apiRequest, apiRequestWithMetadata, apiResponse, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";

export const ACCOUNT_DELETION_INTENT = "DELETE";

export function accountLifecycleFeatureEnabled() {
  return process.env.NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED === "true";
}

export type DeletionRequestResponse = { deletionId: string; statusReceipt: string };
export type AccountLifecycleStatus = {
  contract: "account_lifecycle_status.v1";
  state: "confirmation_pending" | "confirmed" | "erasure_planned" | "erasing" | "held" | "failed" | "live_erasure_complete" | "backup_expiry_pending" | "fully_erased";
  observedAt: string;
  requestedAt: string;
  confirmedAt: string | null;
  liveErasedAt: string | null;
  terminalAt: string | null;
  policyVersion: string;
  condition: "hold_active" | "retry_exhausted" | null;
  nextCheckAfterSeconds: number;
  receiptExpiresAt: string | null;
};

export async function exportAccount(getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiResponse("/v1/account/export", {
    method: "POST",
    token,
    headers: { Accept: "application/x-ndjson" }
  }));
}

export async function requestAccountDeletion(idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiResponse("/v1/account-deletion-requests", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": idempotencyKey }
  }));
}

export async function recoverAccountDeletionReceipt(idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiResponse("/v1/account-deletion-receipts/recover", {
    method: "POST",
    token,
    headers: { "Idempotency-Key": idempotencyKey }
  }));
}

export async function fetchAccountLifecycleStatus(statusReceipt: string) {
  return parseLifecycleStatus(await apiRequest<unknown>("/v1/account/lifecycle-status", {
    method: "POST",
    headers: { Authorization: `LifecycleReceipt ${statusReceipt}` }
  }));
}

export async function confirmAccountDeletion(deletionId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  assertVisibleAsciiIdempotencyKey(idempotencyKey);
  return withSubjectBoundToken(getToken, isSubjectCurrent, async (token) => {
    const response = await apiRequestWithMetadata<unknown>(`/v1/account-deletion-requests/${encodeURIComponent(deletionId)}/confirm`, {
      method: "POST",
      token,
      headers: { "Idempotency-Key": idempotencyKey }
    });
    if (response.status !== 202 || !isJsonResponse(response.headers) || !isAllowedReplayHeader(response.headers)) throw invalidConfirmationResponse();
    try {
      return parseDeletionConfirmation(response.body, deletionId);
    } catch {
      throw invalidConfirmationResponse();
    }
  });
}

export async function cancelAccountDeletion(deletionId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/account-deletion-requests/${encodeURIComponent(deletionId)}/cancel`, {
    method: "POST",
    token
  }));
}

export async function lifecycleResult(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return response.json().catch(() => null);
  return response.text().catch(() => "");
}

export function parseDeletionRequest(value: unknown): DeletionRequestResponse {
  if (!isRecord(value) || typeof value.deletion_id !== "string" || !value.deletion_id || typeof value.status_receipt !== "string" || !/^lr1_[A-Za-z0-9_-]{43}$/u.test(value.status_receipt)) {
    if (isRecord(value) && typeof value.detail === "string") throw lifecycleError(value);
    throw new ApiRequestError("The API returned an invalid account deletion receipt.", undefined, "server");
  }
  return { deletionId: value.deletion_id, statusReceipt: value.status_receipt };
}

export function parseDeletionConfirmation(value: unknown, expectedDeletionId?: string) {
  const valid = isRecord(value)
    && Object.keys(value).length === 1
    && typeof value.deletion_id === "string"
    && value.deletion_id.length > 0
    && (expectedDeletionId === undefined || value.deletion_id === expectedDeletionId);
  if (!valid) {
    if (expectedDeletionId === undefined && isRecord(value) && typeof value.detail === "string") throw lifecycleError(value);
    throw new ApiRequestError("The API returned an invalid account deletion confirmation.", undefined, "server");
  }
  return { deletionId: (value as { deletion_id: string }).deletion_id };
}

export function parseLifecycleStatus(value: unknown): AccountLifecycleStatus {
  if (!isRecord(value) || value.contract !== "account_lifecycle_status.v1" || !isLifecycleState(value.state) || !date(value.observed_at) || !date(value.requested_at) || !nullableDate(value.confirmed_at) || !nullableDate(value.live_erased_at) || !nullableDate(value.terminal_at) || typeof value.policy_version !== "string" || !value.policy_version || (value.condition !== null && value.condition !== "hold_active" && value.condition !== "retry_exhausted") || typeof value.next_check_after_seconds !== "number" || !Number.isInteger(value.next_check_after_seconds) || value.next_check_after_seconds < 0 || !nullableDate(value.receipt_expires_at)) {
    throw new ApiRequestError("The API returned an invalid account lifecycle status.", undefined, "server");
  }
  return {
    contract: value.contract,
    state: value.state,
    observedAt: value.observed_at,
    requestedAt: value.requested_at,
    confirmedAt: value.confirmed_at,
    liveErasedAt: value.live_erased_at,
    terminalAt: value.terminal_at,
    policyVersion: value.policy_version,
    condition: value.condition,
    nextCheckAfterSeconds: value.next_check_after_seconds,
    receiptExpiresAt: value.receipt_expires_at
  };
}

export function lifecycleError(value: unknown) {
  if (isRecord(value) && typeof value.detail === "string") return new ApiRequestError(value.detail, undefined, value.detail === "account_access_denied" ? "unauthorized" : "request");
  return new ApiRequestError("connect.md could not complete that account-lifecycle action.", undefined, "request");
}

export function presentLifecycleError(error: unknown) {
  if (error instanceof ApiRequestError) {
    if (error.code === "offline") return "You are offline. Reconnect before trying this private account action.";
    if (error.message === "account_access_denied") return "Account access is currently denied. This confirms neither worker progress nor complete erasure.";
    if (error.message === "account lifecycle is unavailable") return "This private account-lifecycle feature is unavailable on this deployment.";
    if (error.message === "account_lifecycle_impersonation_forbidden") return "This action is unavailable during an impersonated session.";
    if (error.message === "reverification_already_used") return "That verification was already used. Start the action again so Clerk can obtain a fresh verification.";
    if (error.message === "account deletion request was not found") return "The server no longer permits cancellation for this deletion request. Use the saved Lifecycle Receipt to check its sanitized status.";
    if (error.message === "account_deletion_request_exists") return "An existing deletion request prevents a new one. If it is still pending confirmation, use the recovery action below to rotate and recover its Lifecycle Receipt.";
    if (error.message === "account lifecycle status was not found") return "That Lifecycle Receipt is invalid, cancelled, expired, or unavailable. The status endpoint intentionally gives the same response for each case.";
    if (error.code === "unauthorized") return "This account action was denied. Sign in again; that denial does not establish an erasure outcome.";
    return error.message;
  }
  if (isRecord(error) && error.code === "reverification_cancelled") return "Verification was not completed. No account action was assumed.";
  return "The account-lifecycle action could not be completed. No final erasure state is assumed.";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isLifecycleState(value: unknown): value is AccountLifecycleStatus["state"] {
  return value === "confirmation_pending" || value === "confirmed" || value === "erasure_planned" || value === "erasing" || value === "held" || value === "failed" || value === "live_erasure_complete" || value === "backup_expiry_pending" || value === "fully_erased";
}

function date(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && !Number.isNaN(new Date(value).valueOf());
}

function nullableDate(value: unknown): value is string | null {
  return value === null || date(value);
}

function assertVisibleAsciiIdempotencyKey(value: string) {
  if (typeof value !== "string" || !/^[\x21-\x7E]{1,128}$/u.test(value)) throw new ApiRequestError("A visible-ASCII Idempotency-Key is required for this action.", 400, "request");
}

function isJsonResponse(headers: Headers) {
  return (headers.get("content-type") ?? "").toLowerCase().includes("application/json");
}

function isAllowedReplayHeader(headers: Headers) {
  const value = headers.get("Idempotency-Replayed");
  return value === null || value === "true";
}

function invalidConfirmationResponse() {
  return new ApiRequestError("The API returned an invalid account deletion confirmation.", 502, "server");
}
