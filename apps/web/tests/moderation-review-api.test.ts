import { afterEach, describe, expect, it, vi } from "vitest";

import { decideModerationReviewAppeal, decideModerationReviewCase, getModerationReviewAppeal, getModerationReviewCase, listModerationReviewAppeals, listModerationReviewCases } from "../lib/moderation-review-api";

const etag = `"sha256-${"a".repeat(64)}"`;
const timestamp = "2026-08-05T00:00:00Z";
const caseSummary = { id: "case-1", post_id: "post-1", status: "open", author_profile_handle: "ari-chen", title: "Professional note", report_count: 2, reason_codes: ["spam", "privacy"], created_at: timestamp, updated_at: timestamp };
const post = { id: "post-1", author_profile_handle: "ari-chen", title: "Professional note", topics: ["payments"], version: 1, published_at: timestamp, status: "published", markdown: "# Professional note\n\nReviewed body." };
const reports = [{ id: "report-1", reason_code: "spam", narrative: "<script>not markup</script>", created_at: timestamp }, { id: "report-2", reason_code: "privacy", narrative: null, created_at: timestamp }];
const caseDetail = { case: caseSummary, post, reports, etag };
const appealSummary = { id: "appeal-1", case_id: "case-1", post_id: "post-1", status: "submitted", author_profile_handle: "ari-chen", title: "Professional note", submitted_at: timestamp };
const appealDetail = { appeal: { id: "appeal-1", case_id: "case-1", post_id: "post-1", status: "submitted", rationale: "Please review.", submitted_at: timestamp }, post: { ...post, status: "withheld" }, reports, decision: { action: "withhold", reason_code: "spam", subject_explanation: "Withheld after review.", decided_at: timestamp }, etag };

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

function configure(responses: Array<{ body?: unknown; status?: number; headers?: Record<string, string> }>) {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
  const fetchMock = vi.fn<typeof fetch>();
  for (const response of responses) fetchMock.mockResolvedValueOnce(new Response(response.status === 204 ? null : JSON.stringify(response.body), { status: response.status ?? 200, headers: response.status === 204 ? response.headers : { "Content-Type": "application/json", ...response.headers } }));
  vi.stubGlobal("fetch", fetchMock); return fetchMock;
}

describe("private moderation reviewer HTTP contract", () => {
  it("uses Clerk-human subject binding, no-store reads, bounded cursors, and exact queue parsers", async () => {
    const fetchMock = configure([{ body: { cases: [caseSummary], next_cursor: "case-next" } }, { body: { appeals: [appealSummary], next_cursor: null } }]);
    await expect(listModerationReviewCases(async () => "clerk-token", () => true, null, 25)).resolves.toMatchObject({ cases: [{ id: "case-1", reportCount: 2 }], nextCursor: "case-next" });
    await expect(listModerationReviewAppeals(async () => "clerk-token", () => true)).resolves.toMatchObject({ appeals: [{ id: "appeal-1", caseId: "case-1" }] });
    for (const [, init] of fetchMock.mock.calls) { expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer clerk-token"); expect(init).toMatchObject({ cache: "no-store" }); }
    await expect(listModerationReviewCases(async () => "token", () => true, "x".repeat(501))).rejects.toMatchObject({ status: 400, code: "request" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("never dispatches after the authenticated subject changes during token resolution", async () => {
    const fetchMock = configure([]);
    let current = true;
    const getToken = async () => { current = false; return "other-subject-token"; };
    await expect(getModerationReviewCase("case-1", getToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects every extra or forbidden private field instead of projecting it", async () => {
    const forbidden = ["owner_id", "reporter_id", "moderator_id", "reviewer_id", "storage_path", "sha256", "internal_rationale", "evidence", "grant_id"];
    for (const key of forbidden) {
      configure([{ body: { ...caseDetail, [key]: "secret" } }]);
      await expect(getModerationReviewCase("case-1", async () => "token", () => true)).rejects.toMatchObject({ code: "server", message: expect.not.stringContaining("secret") });
    }
  });

  it("strictly parses bound detail evidence and strong ETags", async () => {
    configure([{ body: caseDetail }, { body: appealDetail }]);
    await expect(getModerationReviewCase("case-1", async () => "token", () => true)).resolves.toMatchObject({ case: { id: "case-1" }, post: { id: "post-1" }, reports: [{ id: "report-1" }, { id: "report-2" }], etag });
    await expect(getModerationReviewAppeal("appeal-1", async () => "token", () => true)).resolves.toMatchObject({ appeal: { id: "appeal-1" }, decision: { action: "withhold" }, etag });
    configure([{ body: { ...caseDetail, etag: "W/\"weak\"" } }]);
    await expect(getModerationReviewCase("case-1", async () => "token", () => true)).rejects.toMatchObject({ code: "server" });
  });

  it("sends exact If-Match and idempotency headers and accepts only strict empty 204 receipts", async () => {
    const fetchMock = configure([{ status: 204 }, { status: 204, headers: { "Idempotency-Replayed": "true" } }]);
    await decideModerationReviewCase("case-1", { action: "withhold", reasonCode: "privacy", subjectExplanation: "Subject-safe explanation." }, etag, "case-attempt-1", async () => "clerk-token", () => true);
    await decideModerationReviewAppeal("appeal-1", { action: "overturn", subjectExplanation: "Independent subject-safe explanation." }, etag, "appeal-attempt-1", async () => "clerk-token", () => true);
    const caseInit = fetchMock.mock.calls[0]?.[1] as RequestInit; const appealInit = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(caseInit.headers).get("If-Match")).toBe(etag);
    expect(new Headers(caseInit.headers).get("Idempotency-Key")).toBe("case-attempt-1");
    expect(JSON.parse(String(caseInit.body))).toEqual({ action: "withhold", reason_code: "privacy", subject_explanation: "Subject-safe explanation." });
    expect(new Headers(appealInit.headers).get("If-Match")).toBe(etag);
    expect(new Headers(appealInit.headers).get("Idempotency-Key")).toBe("appeal-attempt-1");
    expect(caseInit).toMatchObject({ method: "POST", cache: "no-store" });
  });

  it("rejects malformed decision receipts and duplicate queue identifiers", async () => {
    configure([{ body: { cases: [caseSummary, caseSummary], next_cursor: null } }, { body: {}, status: 200 }]);
    await expect(listModerationReviewCases(async () => "token", () => true)).rejects.toMatchObject({ code: "server" });
    await expect(decideModerationReviewCase("case-1", { action: "dismiss", reasonCode: "other", subjectExplanation: "No violation." }, etag, "attempt", async () => "token", () => true)).rejects.toMatchObject({ code: "server" });
  });
});
