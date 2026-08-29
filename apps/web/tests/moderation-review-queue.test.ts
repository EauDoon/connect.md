import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import { APPEAL_REVIEW_DENIED_MESSAGE, appealDecisionDisposition, clearModerationAppealAttemptIfCurrent, invalidateModerationAppealReviewAccess, isModerationAppealDecisionCurrent, mergeModerationAppealQueue, moderationAppealAttemptSlot, rememberModerationAppealAttempt } from "../components/moderation-appeal-review-queue";
import { MODERATION_REVIEW_DENIED_MESSAGE, clearModerationCaseAttemptIfCurrent, invalidateModerationCaseReviewAccess, isModerationCaseDecisionCurrent, mergeModerationCaseQueue, moderationCaseAttemptSlot, rememberModerationCaseAttempt, reviewDecisionDisposition } from "../components/moderation-case-review-queue";
import { ApiRequestError } from "../lib/api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt } from "../lib/logical-mutation";
import type { ModerationAppealSummary, ModerationCaseSummary } from "../lib/moderation-review-api";

const timestamp = "2026-08-05T00:00:00Z";
const caseRecord: ModerationCaseSummary = { id: "case-1", postId: "post-1", status: "open", authorProfileHandle: "ari-chen", title: "Post", reportCount: 1, reasonCodes: ["spam"], createdAt: timestamp, updatedAt: timestamp };
const appealRecord: ModerationAppealSummary = { id: "appeal-1", caseId: "case-1", postId: "post-1", status: "submitted", authorProfileHandle: "ari-chen", title: "Post", submittedAt: timestamp };
function source(relative: string) { return readFileSync(new URL(relative, import.meta.url), "utf8"); }

describe("private moderation review workspaces", () => {
  it("deduplicates cursor pages and closes repeated-cursor continuation", () => {
    expect(mergeModerationCaseQueue([caseRecord], { cases: [caseRecord, { ...caseRecord, id: "case-2" }], nextCursor: "seen" }, "current", new Set(["seen"]))).toEqual({ items: [caseRecord, { ...caseRecord, id: "case-2" }], nextCursor: null, cursorDidNotProgress: true });
    expect(mergeModerationAppealQueue([appealRecord], { appeals: [appealRecord, { ...appealRecord, id: "appeal-2" }], nextCursor: null }, "current", new Set())).toEqual({ items: [appealRecord, { ...appealRecord, id: "appeal-2" }], nextCursor: null, cursorDidNotProgress: false });
  });

  it("retains one logical key only for an unchanged ambiguous decision", () => {
    const issue = vi.fn().mockReturnValueOnce("decision-key-1").mockReturnValueOnce("decision-key-2");
    const intent = { operation: "moderation-case-decision", caseId: "case-1", etag: "etag-1", action: "withhold", reasonCode: "privacy", subjectExplanation: "Explanation" };
    const first = beginLogicalMutationAttempt(null, "human-1", intent, issue);
    const retained = settleLogicalMutationAttempt(first, new ApiRequestError("acknowledgement lost", 503, "server"));
    expect(beginLogicalMutationAttempt(retained, "human-1", intent, issue).idempotencyKey).toBe("decision-key-1");
    expect(beginLogicalMutationAttempt(retained, "human-1", { ...intent, subjectExplanation: "Changed" }, issue).idempotencyKey).toBe("decision-key-2");
    expect(settleLogicalMutationAttempt(first, new ApiRequestError("stale", 412, "request"))).toBeNull();
  });

  it("keeps case A's ambiguous retry key after a separate case B action", () => {
    const issue = vi.fn().mockReturnValueOnce("case-a-key").mockReturnValueOnce("case-b-key");
    const attempts = new Map();
    const intentA = { operation: "moderation-case-decision", caseId: "case-a", etag: "etag-a", action: "withhold", reasonCode: "privacy", subjectExplanation: "A" };
    const intentB = { ...intentA, caseId: "case-b", etag: "etag-b", subjectExplanation: "B" };
    const slotA = moderationCaseAttemptSlot("case-a"); const slotB = moderationCaseAttemptSlot("case-b");
    const firstA = beginLogicalMutationAttempt(null, "human-1", intentA, issue);
    rememberModerationCaseAttempt(attempts, slotA, settleLogicalMutationAttempt(firstA, new ApiRequestError("lost", 503, "server")));
    const firstB = beginLogicalMutationAttempt(attempts.get(slotB) ?? null, "human-1", intentB, issue); rememberModerationCaseAttempt(attempts, slotB, firstB); rememberModerationCaseAttempt(attempts, slotB, null);
    expect(firstB.idempotencyKey).toBe("case-b-key");
    expect(beginLogicalMutationAttempt(attempts.get(slotA) ?? null, "human-1", intentA, issue).idempotencyKey).toBe("case-a-key");
  });

  it("keeps appeal A's ambiguous retry key after a separate appeal B action", () => {
    const issue = vi.fn().mockReturnValueOnce("appeal-a-key").mockReturnValueOnce("appeal-b-key");
    const attempts = new Map();
    const intentA = { operation: "moderation-appeal-decision", appealId: "appeal-a", etag: "etag-a", action: "uphold", subjectExplanation: "A" };
    const intentB = { ...intentA, appealId: "appeal-b", etag: "etag-b", action: "overturn", subjectExplanation: "B" };
    const slotA = moderationAppealAttemptSlot("appeal-a"); const slotB = moderationAppealAttemptSlot("appeal-b");
    const firstA = beginLogicalMutationAttempt(null, "human-1", intentA, issue);
    rememberModerationAppealAttempt(attempts, slotA, settleLogicalMutationAttempt(firstA, new ApiRequestError("lost", 503, "server")));
    const firstB = beginLogicalMutationAttempt(attempts.get(slotB) ?? null, "human-1", intentB, issue); rememberModerationAppealAttempt(attempts, slotB, firstB); rememberModerationAppealAttempt(attempts, slotB, null);
    expect(firstB.idempotencyKey).toBe("appeal-b-key");
    expect(beginLogicalMutationAttempt(attempts.get(slotA) ?? null, "human-1", intentA, issue).idempotencyKey).toBe("appeal-a-key");
  });

  it("invalidates selected authority, detail epochs, live claims, and attempts on access denial", () => {
    const caseAttempts = new Map([[moderationCaseAttemptSlot("case-1"), beginLogicalMutationAttempt(null, "human-1", { operation: "moderation-case-decision" }, () => "case-key")]]);
    const caseSelection = { current: "case-1" as string | null }; const caseDetailEpoch = { current: 8 }; const caseClaim = { current: Symbol("case-claim") };
    invalidateModerationCaseReviewAccess(caseSelection, caseDetailEpoch, caseClaim, caseAttempts);
    expect(caseSelection.current).toBeNull(); expect(caseDetailEpoch.current).toBe(9); expect(caseClaim.current).toBeNull(); expect(caseAttempts).toHaveLength(0);

    const appealAttempts = new Map([[moderationAppealAttemptSlot("appeal-1"), beginLogicalMutationAttempt(null, "human-1", { operation: "moderation-appeal-decision" }, () => "appeal-key")]]);
    const appealSelection = { current: "appeal-1" as string | null }; const appealDetailEpoch = { current: 3 }; const appealClaim = { current: Symbol("appeal-claim") };
    invalidateModerationAppealReviewAccess(appealSelection, appealDetailEpoch, appealClaim, appealAttempts);
    expect(appealSelection.current).toBeNull(); expect(appealDetailEpoch.current).toBe(4); expect(appealClaim.current).toBeNull(); expect(appealAttempts).toHaveLength(0);
  });

  it("clears a stale definitive completion's own slot without clearing the newly selected record", () => {
    const caseAttempts = new Map<string, ReturnType<typeof beginLogicalMutationAttempt>>();
    const staleCaseSlot = moderationCaseAttemptSlot("case-1"); const currentCaseSlot = moderationCaseAttemptSlot("case-2");
    const staleCaseAttempt = beginLogicalMutationAttempt(null, "human-1", { operation: "moderation-case-decision", caseId: "case-1" }, () => "case-old"); const currentCaseAttempt = beginLogicalMutationAttempt(null, "human-1", { operation: "moderation-case-decision", caseId: "case-2" }, () => "case-new");
    caseAttempts.set(staleCaseSlot, staleCaseAttempt); caseAttempts.set(currentCaseSlot, currentCaseAttempt);
    expect(isModerationCaseDecisionCurrent(true, "case-2", "case-1", false)).toBe(false);
    clearModerationCaseAttemptIfCurrent(caseAttempts, staleCaseSlot, staleCaseAttempt);
    expect(caseAttempts.get(staleCaseSlot)).toBeUndefined(); expect(caseAttempts.get(currentCaseSlot)).toBe(currentCaseAttempt);

    const appealAttempts = new Map<string, ReturnType<typeof beginLogicalMutationAttempt>>();
    const staleAppealSlot = moderationAppealAttemptSlot("appeal-1"); const currentAppealSlot = moderationAppealAttemptSlot("appeal-2");
    const staleAppealAttempt = beginLogicalMutationAttempt(null, "human-1", { operation: "moderation-appeal-decision", appealId: "appeal-1" }, () => "appeal-old"); const currentAppealAttempt = beginLogicalMutationAttempt(null, "human-1", { operation: "moderation-appeal-decision", appealId: "appeal-2" }, () => "appeal-new");
    appealAttempts.set(staleAppealSlot, staleAppealAttempt); appealAttempts.set(currentAppealSlot, currentAppealAttempt);
    expect(isModerationAppealDecisionCurrent(true, "appeal-2", "appeal-1", false)).toBe(false);
    clearModerationAppealAttemptIfCurrent(appealAttempts, staleAppealSlot, staleAppealAttempt);
    expect(appealAttempts.get(staleAppealSlot)).toBeUndefined(); expect(appealAttempts.get(currentAppealSlot)).toBe(currentAppealAttempt);
  });

  it("accepts a reviewer completion only for the selected record, current subject, and live claim", () => {
    expect(isModerationCaseDecisionCurrent(true, "case-1", "case-1", true)).toBe(true);
    expect(isModerationCaseDecisionCurrent(true, "case-2", "case-1", true)).toBe(false);
    expect(isModerationCaseDecisionCurrent(false, "case-1", "case-1", true)).toBe(false);
    expect(isModerationCaseDecisionCurrent(true, "case-1", "case-1", false)).toBe(false);
    expect(isModerationAppealDecisionCurrent(true, "appeal-1", "appeal-1", true)).toBe(true);
    expect(isModerationAppealDecisionCurrent(true, "appeal-2", "appeal-1", true)).toBe(false);
    expect(isModerationAppealDecisionCurrent(true, "appeal-1", "appeal-1", false)).toBe(false);
  });

  it("presents distinct access, precondition, and conflict states without leaking authority identities", () => {
    expect(reviewDecisionDisposition(new ApiRequestError("moderator secret", 403, "unauthorized"))).toBe("denied");
    expect(reviewDecisionDisposition(new ApiRequestError("stale", 412, "request"))).toBe("stale");
    expect(appealDecisionDisposition(new ApiRequestError("conflict", 409, "request"))).toBe("conflict");
    expect(MODERATION_REVIEW_DENIED_MESSAGE).not.toContain("secret");
    expect(APPEAL_REVIEW_DENIED_MESSAGE).not.toContain("secret");
  });

  it("keeps a nonempty queue refresh failure retryable while denied access stays non-retryable", () => {
    for (const value of [source("../components/moderation-case-review-queue.tsx"), source("../components/moderation-appeal-review-queue.tsx")]) {
      expect(value).toContain('setLoadState("error"); setQueueError(presentModerationReviewError(error));');
      expect(value).not.toMatch(/setLoadState\((?:cases|appeals)Ref\.current\.length \? "loaded" : "error"\)/u);
      expect(value).toContain('loadState === "error"');
      expect(value).toContain('onClick={() => void loadQueue(null, false)}');
      expect(value).toContain('setLoadState("loading"); setQueueError("");');
      expect(value).toContain('setLoadState("loaded");');
      expect(value).toContain('setLoadState("denied")');
      expect(value).toContain('loadState === "denied" ? <Denied /> :');
    }
  });

  it("suppresses stale responses, confirms decisions, refetches authority, and never persists attempts", () => {
    for (const [value, requestId, retryLabel] of [[source("../components/moderation-case-review-queue.tsx"), "requestCaseId", "Retry case evidence"], [source("../components/moderation-appeal-review-queue.tsx"), "requestAppealId", "Retry appeal evidence"]] as const) {
      expect(value).toContain("epoch !== queueEpochRef.current");
      expect(value).toContain("epoch !== detailEpochRef.current");
      expect(value).toContain("window.confirm(");
      expect(value).toContain("claimLogicalMutation(mutationClaimRef.current)");
      expect(value).toContain("attemptsRef.current.get(slot)");
      expect(value).toContain("attempts.clear()");
      expect(value).toContain("mutationClaimRef.current.current = null; setDecisionBusy(false);");
      expect(value).toContain(requestId);
      expect(value).toContain("isDecisionCurrent()");
      expect(value).toContain("Promise.allSettled([loadQueue(null, false), loadDetail(");
      expect(value).toContain("detail.etag");
      expect(value).not.toContain('<section aria-live="polite">');
      expect(value).toContain('onClick={() => void loadDetail(selectedId)}');
      expect(value).toContain(retryLabel);
      expect(value).not.toMatch(/localStorage|sessionStorage|indexedDB|console\./u);
    }
  });

  it("moves explicit queue selections to the current detail region and exposes selected state", () => {
    for (const [value, detailId, headingId] of [
      [source("../components/moderation-case-review-queue.tsx"), "moderation-case-detail", "moderation-case-detail-heading"],
      [source("../components/moderation-appeal-review-queue.tsx"), "moderation-appeal-detail", "moderation-appeal-detail-heading"],
    ] as const) {
      expect(value).toContain("focusDetailIdRef.current = record.id");
      expect(value).toContain("focusDetailIdRef.current !== detailId");
      expect(value).toContain("focusDetailIdRef.current = null");
      expect(value).toContain("detailRegionRef.current?.focus()");
      expect(value).toContain(`aria-controls="${detailId}"`);
      expect(value).toContain("aria-pressed={selectedId === record.id}");
      expect(value).toContain(`id="${detailId}" ref={detailRegionRef} tabIndex={-1}`);
      expect(value).toContain(`"${headingId}"`);
    }
  });

  it("renders post Markdown only through the sanitizer and reporter narratives as plain text", () => {
    for (const value of [source("../components/moderation-case-review-queue.tsx"), source("../components/moderation-appeal-review-queue.tsx")]) {
      expect(value).toContain("<MarkdownPreview markdown={detail.post.markdown}");
      expect(value).toContain("data-untrusted-evidence");
      expect(value).toContain("whitespace-pre-wrap");
      expect(value).toContain("never interpreted as Markdown or HTML");
      expect(value).not.toContain("<MarkdownPreview markdown={report.narrative}");
      expect(value).not.toContain("dangerouslySetInnerHTML");
    }
  });

  it("keeps both pages noindex, robot-blocked, and absent from public or workspace navigation", () => {
    const casePage = source("../app/moderation-review/page.tsx"); const appealPage = source("../app/appeal-review/page.tsx");
    const robots = source("../app/robots.ts"); const navigation = source("../lib/navigation.ts"); const header = source("../components/site-header.tsx");
    expect(casePage).toContain("robots: { index: false, follow: false }");
    expect(appealPage).toContain("robots: { index: false, follow: false }");
    expect(robots).toContain('"/moderation"'); expect(robots).toContain('"/appeal-review"');
    expect(navigation).not.toContain("/moderation-review"); expect(navigation).not.toContain("/appeal-review");
    expect(header).not.toContain("/moderation-review"); expect(header).not.toContain("/appeal-review");
  });
});
