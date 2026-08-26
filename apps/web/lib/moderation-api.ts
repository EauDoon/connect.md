import { ApiRequestError, apiRequest, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";

export const MODERATION_CASE_STATUSES = ["open", "dismissed", "withheld", "appealed", "appeal_upheld", "appeal_overturned", "legacy_withheld", "legacy_withdrawn"] as const;
export const MODERATION_APPEAL_STATUSES = ["submitted", "upheld", "overturned"] as const;
export type ModerationCaseStatus = (typeof MODERATION_CASE_STATUSES)[number];
export type ModerationAppealStatus = (typeof MODERATION_APPEAL_STATUSES)[number];

export type ModerationAppeal = {
  id: string;
  decisionId: string;
  status: ModerationAppealStatus;
  submittedAt: string;
  reviewedAt: string | null;
  subjectExplanation: string | null;
};

export type ModerationCase = {
  id: string;
  postId: string;
  status: ModerationCaseStatus;
  reasonCode: string | null;
  subjectExplanation: string | null;
  decidedAt: string | null;
  appealDeadline: string | null;
  appeal: ModerationAppeal | null;
  updatedAt: string;
};

export type ModerationCasePage = { cases: ModerationCase[]; nextCursor: string | null };

export function presentModerationError(error: unknown) {
  if (!(error instanceof ApiRequestError)) return "connect.md could not complete that private moderation request. No change was assumed.";
  if (error.code === "offline") return "You are offline. Reconnect before reviewing private case status.";
  if (error.code === "unauthorized") return "Private case status requires your signed-in human session.";
  if (error.code === "not_found") return "That private moderation case is unavailable.";
  if (error.code === "server") return "connect.md is temporarily unavailable. No change was assumed.";
  return error.message;
}

export async function listModerationCasesForSubject(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/moderation/cases?${pageParams(cursor).toString()}`, { token }).then(parseCasePage));
}

export async function createModerationAppeal(caseId: string, rationale: string, idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/moderation/cases/${encodeURIComponent(caseId)}/appeals`, {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ rationale })
  }).then(parseAppeal));
}

export function isAppealableModerationCase(caseRecord: ModerationCase, now = Date.now()) {
  if (caseRecord.status !== "withheld" || caseRecord.appeal !== null || !caseRecord.appealDeadline) return false;
  const deadline = Date.parse(caseRecord.appealDeadline);
  return Number.isFinite(deadline) && deadline >= now;
}

function pageParams(cursor: string | null) { const query = new URLSearchParams({ limit: "25" }); if (cursor) query.set("cursor", cursor); return query; }
function parseCasePage(value: unknown): ModerationCasePage { const raw = record(value, "moderation case list"); const cases = Array.isArray(raw.cases) ? raw.cases.map(parseCase) : null; if (!cases) throw invalid("moderation case list"); return { cases, nextCursor: nullableText(raw.next_cursor, "moderation case cursor") }; }
function parseCase(value: unknown): ModerationCase { const raw = record(value, "moderation case"); return { id: required(raw.id, "moderation case id"), postId: required(raw.post_id, "moderation post id"), status: oneOf(raw.status, MODERATION_CASE_STATUSES, "moderation case status"), reasonCode: nullableText(raw.reason_code, "moderation reason code"), subjectExplanation: nullableText(raw.subject_explanation, "moderation subject explanation"), decidedAt: nullableText(raw.decided_at, "moderation decision time"), appealDeadline: nullableText(raw.appeal_deadline, "moderation appeal deadline"), appeal: raw.appeal === null ? null : parseAppeal(raw.appeal), updatedAt: required(raw.updated_at, "moderation update time") }; }
function parseAppeal(value: unknown): ModerationAppeal { const raw = record(value, "moderation appeal"); return { id: required(raw.id, "moderation appeal id"), decisionId: required(raw.decision_id, "moderation appeal decision id"), status: oneOf(raw.status, MODERATION_APPEAL_STATUSES, "moderation appeal status"), submittedAt: required(raw.submitted_at, "moderation appeal submission time"), reviewedAt: nullableText(raw.reviewed_at, "moderation appeal review time"), subjectExplanation: nullableText(raw.subject_explanation, "moderation appeal subject explanation") }; }
function record(value: unknown, label: string): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) throw invalid(label); return value as Record<string, unknown>; }
function required(value: unknown, label: string) { if (typeof value !== "string" || !value) throw invalid(label); return value; }
function nullableText(value: unknown, label: string) { if (value === null || value === undefined) return null; return required(value, label); }
function oneOf<T extends string>(value: unknown, values: readonly T[], label: string): T { if (typeof value !== "string" || !values.includes(value as T)) throw invalid(label); return value as T; }
function invalid(label: string) { return new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); }
