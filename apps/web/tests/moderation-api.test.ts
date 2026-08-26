import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { logicalIdempotencyKey } from "../lib/posts-api";
import { createModerationAppeal, isAppealableModerationCase, listModerationCasesForSubject, type ModerationCase } from "../lib/moderation-api";
import { mergeCasesById } from "../components/moderation-case-manager";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

const caseResponse = {
  id: "case-1",
  post_id: "post-1",
  status: "withheld",
  reason_code: "spam",
  subject_explanation: "The post was withheld after review.",
  decided_at: "2026-08-03T00:00:00Z",
  appeal_deadline: "2026-09-02T00:00:00Z",
  appeal: null,
  updated_at: "2026-08-03T00:00:00Z",
  reporter_id: "must-not-reach-subject",
  reporter_count: 7,
  narrative: "must-not-reach-subject",
  evidence: "must-not-reach-subject",
  internal_notes: "must-not-reach-subject"
};

function configure(response: unknown, status = 200) {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
  vi.stubGlobal("crypto", { randomUUID: () => "appeal-request-1" });
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response), { status, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("private moderation case and appeal contracts", () => {
  it("parses only the exact subject-safe case fields", async () => {
    configure({ cases: [caseResponse], next_cursor: "next-page" });
    const page = await listModerationCasesForSubject(async () => "clerk-token", () => true);
    expect(page).toEqual({ cases: [{ id: "case-1", postId: "post-1", status: "withheld", reasonCode: "spam", subjectExplanation: "The post was withheld after review.", decidedAt: "2026-08-03T00:00:00Z", appealDeadline: "2026-09-02T00:00:00Z", appeal: null, updatedAt: "2026-08-03T00:00:00Z" }], nextCursor: "next-page" });
    expect(JSON.stringify(page)).not.toMatch(/reporter|narrative|evidence|internal_notes/u);
  });

  it("uses exact subject-bound list and appeal requests", async () => {
    const fetchMock = configure({ id: "appeal-1", decision_id: "decision-1", status: "submitted", submitted_at: "2026-08-03T01:00:00Z", reviewed_at: null, subject_explanation: null }, 201);
    await expect(createModerationAppeal("case-1", "Please review this decision.", "appeal-request-1", async () => "clerk-token", () => true)).resolves.toMatchObject({ id: "appeal-1", status: "submitted" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/moderation/cases/case-1/appeals");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer clerk-token");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("appeal-request-1");
    expect(JSON.parse(String(init.body))).toEqual({ rationale: "Please review this decision." });
  });

  it("does not dispatch a private case read or appeal after the account changes during token resolution", async () => {
    const fetchMock = configure({});
    const transition = async <T>(operation: (token: () => Promise<string>, guard: () => boolean) => Promise<T>) => {
      let current = true;
      await expect(operation(async () => { current = false; return "different-user-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    };
    await transition((token, guard) => listModerationCasesForSubject(token, guard));
    await transition((token, guard) => createModerationAppeal("case-1", "Review this", "appeal-request-1", token, guard));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reuses an appeal key after a lost acknowledgement and rotates it when the rationale changes", () => {
    vi.stubGlobal("crypto", { randomUUID: vi.fn().mockReturnValueOnce("appeal-1").mockReturnValueOnce("appeal-2") });
    const first = logicalIdempotencyKey(null, "subject-1", JSON.stringify({ caseId: "case-1", rationale: "Review this" }));
    expect(logicalIdempotencyKey(first, "subject-1", JSON.stringify({ caseId: "case-1", rationale: "Review this" }))).toEqual(first);
    expect(logicalIdempotencyKey(first, "subject-1", JSON.stringify({ caseId: "case-1", rationale: "Changed rationale" })).key).toBe("appeal-2");
  });

  it("allows a client appeal only for a current unappealed withheld case", () => {
    const caseRecord = { id: "case-1", postId: "post-1", status: "withheld", reasonCode: "spam", subjectExplanation: null, decidedAt: "2026-08-03T00:00:00Z", appealDeadline: "2026-08-10T00:00:00Z", appeal: null, updatedAt: "2026-08-03T00:00:00Z" } satisfies ModerationCase;
    expect(isAppealableModerationCase(caseRecord, Date.parse("2026-08-05T00:00:00Z"))).toBe(true);
    expect(isAppealableModerationCase({ ...caseRecord, status: "legacy_withheld" }, Date.parse("2026-08-05T00:00:00Z"))).toBe(false);
    expect(isAppealableModerationCase({ ...caseRecord, appealDeadline: "2026-08-01T00:00:00Z" }, Date.parse("2026-08-05T00:00:00Z"))).toBe(false);
    expect(isAppealableModerationCase({ ...caseRecord, appeal: { id: "appeal-1", decisionId: "decision-1", status: "submitted", submittedAt: "2026-08-04T00:00:00Z", reviewedAt: null, subjectExplanation: null } }, Date.parse("2026-08-05T00:00:00Z"))).toBe(false);
  });

  it("partitions rationale by account and case while deduping pages and blocking cursor loops", () => {
    const manager = readFileSync(new URL("../components/moderation-case-manager.tsx", import.meta.url), "utf8");
    expect(manager).toContain("<AuthenticatedModerationCaseManager key={subject}");
    expect(manager).toContain("<AppealForm key={caseRecord.id}");
    expect(manager).toContain("inFlightRef.current !== null");
    expect(manager).toContain("deliveredCursorsRef.current.has(requestKey)");
    const feedLink = manager.match(/<Link href="\/feed" className="([^"]+)">Open private feed<\/Link>/u);
    expect(feedLink?.[1]?.split(/\s+/u)).toEqual(expect.arrayContaining(["inline-flex", "min-h-11", "items-center"]));
    const duplicate = { id: "case-1", postId: "post-1", status: "open", reasonCode: null, subjectExplanation: null, decidedAt: null, appealDeadline: null, appeal: null, updatedAt: "2026-08-03T00:00:00Z" } satisfies ModerationCase;
    const second = { ...duplicate, id: "case-2" };
    expect(mergeCasesById([duplicate], [duplicate, second, second])).toEqual([duplicate, second]);
  });
});
