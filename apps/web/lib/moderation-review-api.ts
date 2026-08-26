import { ApiRequestError, apiRequest, apiRequestWithMetadata, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";

export const MODERATION_REASON_CODES = ["spam", "harassment", "misinformation", "privacy", "illegal_content", "other"] as const;
export type ModerationReasonCode = (typeof MODERATION_REASON_CODES)[number];
export type ModerationCaseAction = "dismiss" | "withhold";
export type ModerationAppealAction = "uphold" | "overturn";

export type ModerationCaseSummary = {
  id: string;
  postId: string;
  status: "open" | "dismissed" | "withheld" | "appealed" | "appeal_upheld" | "appeal_overturned" | "legacy_withheld" | "legacy_withdrawn";
  authorProfileHandle: string;
  title: string;
  reportCount: number;
  reasonCodes: ModerationReasonCode[];
  createdAt: string;
  updatedAt: string;
};

export type ModerationAppealSummary = {
  id: string;
  caseId: string;
  postId: string;
  status: "submitted" | "upheld" | "overturned";
  authorProfileHandle: string;
  title: string;
  submittedAt: string;
};

export type ModerationPostEvidence = {
  id: string;
  authorProfileHandle: string;
  title: string;
  topics: string[];
  version: 1;
  publishedAt: string;
  status: "published" | "withdrawn" | "withheld";
  markdown: string;
};

export type ModerationReportEvidence = {
  id: string;
  reasonCode: ModerationReasonCode;
  narrative: string | null;
  createdAt: string;
};

export type ModerationCaseDetail = {
  case: ModerationCaseSummary;
  post: ModerationPostEvidence;
  reports: ModerationReportEvidence[];
  etag: string;
};

export type ModerationAppealDetail = {
  appeal: {
    id: string;
    caseId: string;
    postId: string;
    status: "submitted" | "upheld" | "overturned";
    rationale: string;
    submittedAt: string;
  };
  post: ModerationPostEvidence;
  reports: ModerationReportEvidence[];
  decision: {
    action: "withhold";
    reasonCode: ModerationReasonCode;
    subjectExplanation: string;
    decidedAt: string;
  };
  etag: string;
};

export type ModerationCaseQueuePage = { cases: ModerationCaseSummary[]; nextCursor: string | null };
export type ModerationAppealQueuePage = { appeals: ModerationAppealSummary[]; nextCursor: string | null };

const CASE_STATUSES = ["open", "dismissed", "withheld", "appealed", "appeal_upheld", "appeal_overturned", "legacy_withheld", "legacy_withdrawn"] as const;
const APPEAL_STATUSES = ["submitted", "upheld", "overturned"] as const;
const POST_STATUSES = ["published", "withdrawn", "withheld"] as const;
const STRONG_ETAG = /^"sha256-[0-9a-f]{64}"$/u;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/u;
const HANDLE = /^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/u;
const IDENTIFIER = /^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$/u;

export function presentModerationReviewError(error: unknown) {
  if (!(error instanceof ApiRequestError)) return "The private review service is unavailable. No decision was assumed.";
  if (error.status === 403) return "Your signed-in human session does not have access to this review workspace.";
  if (error.status === 412) return "This evidence changed before your decision. Reload it and confirm a new decision.";
  if (error.status === 409) return "This review conflicted with current server state. Reload it before deciding.";
  if (error.code === "not_found") return "This private review record is unavailable.";
  if (error.code === "offline") return "You are offline. Reconnect before reviewing private evidence.";
  if (error.code === "unauthorized") return "A signed-in human reviewer session is required.";
  if (error.code === "server") return "The private review service is temporarily unavailable. No decision was assumed.";
  return error.message;
}

export async function listModerationReviewCases(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null, limit = 25) {
  assertPageInput(cursor, limit);
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/internal/post-moderation/cases?${pageParams(cursor, limit)}`, { token, cache: "no-store" }).then((value) => parseCaseQueue(value, limit)));
}

export async function getModerationReviewCase(caseId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  assertIdentifier(caseId, "moderation case id");
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/internal/post-moderation/cases/${encodeURIComponent(caseId)}`, { token, cache: "no-store" }).then(parseCaseDetail));
}

export async function decideModerationReviewCase(caseId: string, input: { action: ModerationCaseAction; reasonCode: ModerationReasonCode; subjectExplanation: string }, etag: string, idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  assertIdentifier(caseId, "moderation case id");
  assertStrongEtag(etag);
  assertIdempotencyKey(idempotencyKey);
  if ((input.action !== "dismiss" && input.action !== "withhold") || !MODERATION_REASON_CODES.includes(input.reasonCode) || !boundedTrimmed(input.subjectExplanation, 500)) throw invalid("moderation case decision");
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(`/v1/internal/post-moderation/cases/${encodeURIComponent(caseId)}/decision`, {
    method: "POST",
    token,
    cache: "no-store",
    headers: decisionHeaders(idempotencyKey, etag),
    body: JSON.stringify({ action: input.action, reason_code: input.reasonCode, subject_explanation: input.subjectExplanation.trim() })
  }));
  assertEmptyDecisionResponse(response, "moderation case decision");
}

export async function listModerationReviewAppeals(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null, limit = 25) {
  assertPageInput(cursor, limit);
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/internal/post-moderation/appeals?${pageParams(cursor, limit)}`, { token, cache: "no-store" }).then((value) => parseAppealQueue(value, limit)));
}

export async function getModerationReviewAppeal(appealId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  assertIdentifier(appealId, "moderation appeal id");
  return withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/internal/post-moderation/appeals/${encodeURIComponent(appealId)}`, { token, cache: "no-store" }).then(parseAppealDetail));
}

export async function decideModerationReviewAppeal(appealId: string, input: { action: ModerationAppealAction; subjectExplanation: string }, etag: string, idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  assertIdentifier(appealId, "moderation appeal id");
  assertStrongEtag(etag);
  assertIdempotencyKey(idempotencyKey);
  if ((input.action !== "uphold" && input.action !== "overturn") || !boundedTrimmed(input.subjectExplanation, 500)) throw invalid("moderation appeal decision");
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequestWithMetadata<unknown>(`/v1/internal/post-moderation/appeals/${encodeURIComponent(appealId)}/decision`, {
    method: "POST",
    token,
    cache: "no-store",
    headers: decisionHeaders(idempotencyKey, etag),
    body: JSON.stringify({ action: input.action, subject_explanation: input.subjectExplanation.trim() })
  }));
  assertEmptyDecisionResponse(response, "moderation appeal decision");
}

function parseCaseQueue(value: unknown, requestedLimit: number): ModerationCaseQueuePage {
  const raw = exactRecord(value, ["cases", "next_cursor"], "moderation case queue");
  if (!Array.isArray(raw.cases) || raw.cases.length > requestedLimit) throw invalid("moderation case queue");
  const cases = raw.cases.map(parseCaseSummary);
  assertUnique(cases.map((item) => item.id), "moderation case queue");
  return { cases, nextCursor: parseCursor(raw.next_cursor) };
}

function parseAppealQueue(value: unknown, requestedLimit: number): ModerationAppealQueuePage {
  const raw = exactRecord(value, ["appeals", "next_cursor"], "moderation appeal queue");
  if (!Array.isArray(raw.appeals) || raw.appeals.length > requestedLimit) throw invalid("moderation appeal queue");
  const appeals = raw.appeals.map(parseAppealSummary);
  assertUnique(appeals.map((item) => item.id), "moderation appeal queue");
  return { appeals, nextCursor: parseCursor(raw.next_cursor) };
}

function parseCaseDetail(value: unknown): ModerationCaseDetail {
  const raw = exactRecord(value, ["case", "post", "reports", "etag"], "moderation case detail");
  const caseRecord = parseCaseSummary(raw.case);
  const post = parsePost(raw.post);
  const reports = parseReports(raw.reports);
  if (caseRecord.postId !== post.id || caseRecord.reportCount !== reports.length || !sameSet(caseRecord.reasonCodes, reports.map((report) => report.reasonCode))) throw invalid("moderation case detail binding");
  return { case: caseRecord, post, reports, etag: parseStrongEtag(raw.etag) };
}

function parseAppealDetail(value: unknown): ModerationAppealDetail {
  const raw = exactRecord(value, ["appeal", "post", "reports", "decision", "etag"], "moderation appeal detail");
  const appealRaw = exactRecord(raw.appeal, ["id", "case_id", "post_id", "status", "rationale", "submitted_at"], "moderation appeal");
  const decisionRaw = exactRecord(raw.decision, ["action", "reason_code", "subject_explanation", "decided_at"], "moderation appeal decision");
  const appeal = {
    id: parseIdentifier(appealRaw.id, "moderation appeal id"),
    caseId: parseIdentifier(appealRaw.case_id, "moderation appeal case id"),
    postId: parseIdentifier(appealRaw.post_id, "moderation appeal post id"),
    status: oneOf(appealRaw.status, APPEAL_STATUSES, "moderation appeal status"),
    rationale: requiredBoundedText(appealRaw.rationale, "moderation appeal rationale", 2_000, true),
    submittedAt: parseTimestamp(appealRaw.submitted_at, "moderation appeal submission time")
  };
  const post = parsePost(raw.post);
  if (appeal.postId !== post.id) throw invalid("moderation appeal detail binding");
  return {
    appeal,
    post,
    reports: parseReports(raw.reports),
    decision: {
      action: oneOf(decisionRaw.action, ["withhold"] as const, "moderation appeal decision action"),
      reasonCode: oneOf(decisionRaw.reason_code, MODERATION_REASON_CODES, "moderation appeal decision reason"),
      subjectExplanation: requiredBoundedText(decisionRaw.subject_explanation, "moderation appeal decision explanation", 500, true),
      decidedAt: parseTimestamp(decisionRaw.decided_at, "moderation appeal decision time")
    },
    etag: parseStrongEtag(raw.etag)
  };
}

function parseCaseSummary(value: unknown): ModerationCaseSummary {
  const raw = exactRecord(value, ["id", "post_id", "status", "author_profile_handle", "title", "report_count", "reason_codes", "created_at", "updated_at"], "moderation case");
  if (!Array.isArray(raw.reason_codes) || raw.reason_codes.length > MODERATION_REASON_CODES.length) throw invalid("moderation case reason codes");
  const reasonCodes = raw.reason_codes.map((value) => oneOf(value, MODERATION_REASON_CODES, "moderation case reason code"));
  assertUnique(reasonCodes, "moderation case reason codes");
  return {
    id: parseIdentifier(raw.id, "moderation case id"),
    postId: parseIdentifier(raw.post_id, "moderation post id"),
    status: oneOf(raw.status, CASE_STATUSES, "moderation case status"),
    authorProfileHandle: parseHandle(raw.author_profile_handle),
    title: requiredBoundedText(raw.title, "moderation post title", 160),
    reportCount: boundedInteger(raw.report_count, "moderation report count", 0, 1_000_000),
    reasonCodes,
    createdAt: parseTimestamp(raw.created_at, "moderation case creation time"),
    updatedAt: parseTimestamp(raw.updated_at, "moderation case update time")
  };
}

function parseAppealSummary(value: unknown): ModerationAppealSummary {
  const raw = exactRecord(value, ["id", "case_id", "post_id", "status", "author_profile_handle", "title", "submitted_at"], "moderation appeal summary");
  return {
    id: parseIdentifier(raw.id, "moderation appeal id"),
    caseId: parseIdentifier(raw.case_id, "moderation appeal case id"),
    postId: parseIdentifier(raw.post_id, "moderation appeal post id"),
    status: oneOf(raw.status, APPEAL_STATUSES, "moderation appeal status"),
    authorProfileHandle: parseHandle(raw.author_profile_handle),
    title: requiredBoundedText(raw.title, "moderation appeal post title", 160),
    submittedAt: parseTimestamp(raw.submitted_at, "moderation appeal submission time")
  };
}

function parsePost(value: unknown): ModerationPostEvidence {
  const raw = exactRecord(value, ["id", "author_profile_handle", "title", "topics", "version", "published_at", "status", "markdown"], "moderation post evidence");
  if (!Array.isArray(raw.topics) || raw.topics.length < 1 || raw.topics.length > 10 || raw.topics.some((topic) => typeof topic !== "string" || !/^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$/u.test(topic)) || new Set(raw.topics).size !== raw.topics.length) throw invalid("moderation post topics");
  const markdown = requiredBoundedText(raw.markdown, "moderation post Markdown", 10_240, true);
  if (new TextEncoder().encode(markdown).byteLength > 10_240) throw invalid("moderation post Markdown");
  return {
    id: parseIdentifier(raw.id, "moderation post id"),
    authorProfileHandle: parseHandle(raw.author_profile_handle),
    title: requiredBoundedText(raw.title, "moderation post title", 160),
    topics: raw.topics as string[],
    version: oneOf(raw.version, [1] as const, "moderation post version"),
    publishedAt: parseTimestamp(raw.published_at, "moderation post publication time"),
    status: oneOf(raw.status, POST_STATUSES, "moderation post status"),
    markdown
  };
}

function parseReports(value: unknown) {
  if (!Array.isArray(value) || value.length > 1_000) throw invalid("moderation reports");
  const reports = value.map((item): ModerationReportEvidence => {
    const raw = exactRecord(item, ["id", "reason_code", "narrative", "created_at"], "moderation report");
    return {
      id: parseIdentifier(raw.id, "moderation report id"),
      reasonCode: oneOf(raw.reason_code, MODERATION_REASON_CODES, "moderation report reason"),
      narrative: raw.narrative === null ? null : requiredBoundedText(raw.narrative, "moderation report narrative", 2_000, true),
      createdAt: parseTimestamp(raw.created_at, "moderation report creation time")
    };
  });
  assertUnique(reports.map((item) => item.id), "moderation reports");
  return reports;
}

function pageParams(cursor: string | null, limit: number) { const params = new URLSearchParams({ limit: String(limit) }); if (cursor) params.set("cursor", cursor); return params.toString(); }
function assertPageInput(cursor: string | null, limit: number) { if (!Number.isInteger(limit) || limit < 1 || limit > 50 || (cursor !== null && (!cursor || cursor.length > 500))) throw new ApiRequestError("The moderation review page request is invalid.", 400, "request"); }
function decisionHeaders(idempotencyKey: string, etag: string) { return { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "If-Match": etag }; }
function assertEmptyDecisionResponse(response: { status: number; body: unknown; headers: Headers }, label: string) { const replayed = response.headers.get("Idempotency-Replayed"); if (response.status !== 204 || response.body !== "" || (replayed !== null && replayed !== "true")) throw new ApiRequestError(`The API returned an invalid ${label} response.`, 502, "server"); }
function assertIdempotencyKey(value: string) { if (!/^[\x21-\x7E]{1,128}$/u.test(value)) throw new ApiRequestError("A visible-ASCII Idempotency-Key is required for this action.", 400, "request"); }
function assertStrongEtag(value: string) { if (!STRONG_ETAG.test(value)) throw new ApiRequestError("A current strong moderation-review ETag is required for this decision.", 400, "request"); }
function parseStrongEtag(value: unknown) { if (typeof value !== "string" || !STRONG_ETAG.test(value)) throw invalid("moderation review ETag"); return value; }
function parseCursor(value: unknown) { if (value === null) return null; return requiredBoundedText(value, "moderation review cursor", 500); }
function exactRecord(value: unknown, expected: readonly string[], label: string): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) throw invalid(label); const raw = value as Record<string, unknown>; const keys = Object.keys(raw).sort(); const allowed = [...expected].sort(); if (keys.length !== allowed.length || keys.some((key, index) => key !== allowed[index])) throw invalid(label); return raw; }
function parseIdentifier(value: unknown, label: string) { const parsed = requiredBoundedText(value, label, 128); if (!IDENTIFIER.test(parsed)) throw invalid(label); return parsed; }
function assertIdentifier(value: string, label: string) { if (!value || value.length > 128 || !IDENTIFIER.test(value)) throw new ApiRequestError(`The ${label} is invalid.`, 400, "request"); }
function parseHandle(value: unknown) { const parsed = requiredBoundedText(value, "moderation post author profile handle", 100); if (!HANDLE.test(parsed)) throw invalid("moderation post author profile handle"); return parsed; }
function requiredBoundedText(value: unknown, label: string, max: number, multiline = false) { if (typeof value !== "string" || !boundedTrimmed(value, max) || (!multiline && /[\r\n]/u.test(value))) throw invalid(label); return value; }
function boundedTrimmed(value: string, max: number) { return value.length > 0 && value.length <= max && value.trim().length > 0; }
function parseTimestamp(value: unknown, label: string) { if (typeof value !== "string" || !TIMESTAMP.test(value) || Number.isNaN(Date.parse(value))) throw invalid(label); return value; }
function boundedInteger(value: unknown, label: string, min: number, max: number) { if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) throw invalid(label); return value; }
function oneOf<T extends string | number>(value: unknown, values: readonly T[], label: string): T { if (!values.includes(value as T)) throw invalid(label); return value as T; }
function assertUnique(values: readonly string[], label: string) { if (new Set(values).size !== values.length) throw invalid(label); }
function sameSet(left: readonly string[], right: readonly string[]) { return left.length === new Set(right).size && left.every((value) => right.includes(value)); }
function invalid(label: string) { return new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); }
