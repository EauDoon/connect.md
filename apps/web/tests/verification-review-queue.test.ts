import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  REVIEWER_ACCESS_DENIED_MESSAGE,
  mergeReviewerVerificationPage,
  reviewerAccessDenied,
  reviewerDecisionDisposition,
  reviewerDecisionFingerprint,
} from "../components/verification-review-queue";
import { reviewerEvidenceReadyFor } from "../components/verification-review-card";
import { reviewerFocusTarget } from "../components/verification-review-focus";
import { ApiRequestError } from "../lib/api";
import type { ReviewerEvidenceReady } from "../components/verification-evidence-viewer";
import type { ReviewerVerification } from "../lib/recruitment-api";

const reviewEtag = `"sha256-${"c".repeat(64)}"`;
const record: ReviewerVerification = {
  id: "verification-1",
  organizationSlug: "acme",
  organizationName: "Acme",
  state: "under_review",
  submittedAt: "2026-08-04T00:00:00Z",
  updatedAt: "2026-08-04T00:00:00Z",
  policyVersion: null,
  expiresAt: null,
};
const ready: ReviewerEvidenceReady = {
  verificationId: record.id,
  reviewEtag,
  state: "under_review",
  updatedAt: record.updatedAt,
};

function source(relative: string) {
  return readFileSync(new URL(relative, import.meta.url), "utf8");
}

describe("verification reviewer queue", () => {
  it("deduplicates opaque cursor pages and binds logical attempts to the review ETag", () => {
    expect(
      mergeReviewerVerificationPage(
        [record],
        { items: [record, { ...record, id: "verification-2" }], nextCursor: "next" },
        "current",
        new Set(["next"]),
      ),
    ).toEqual({
      items: [record, { ...record, id: "verification-2" }],
      nextCursor: null,
      cursorDidNotProgress: true,
    });
    const decision = {
      expectedState: "under_review" as const,
      reviewEtag,
      policyVersion: "recruiting-control-v1",
      expiresAt: "2026-10-01T00:00:00Z",
    };
    expect(reviewerDecisionFingerprint(record, "activate", decision)).toBe(
      reviewerDecisionFingerprint(record, "activate", decision),
    );
    expect(
      reviewerDecisionFingerprint(record, "activate", {
        ...decision,
        reviewEtag: `"sha256-${"d".repeat(64)}"`,
      }),
    ).not.toBe(reviewerDecisionFingerprint(record, "activate", decision));
    expect(
      reviewerDecisionFingerprint(record, "reject", {
        expectedState: "under_review",
        reviewEtag,
      }),
    ).not.toBe(reviewerDecisionFingerprint(record, "activate", decision));
  });

  it("accepts readiness only for the exact current record and strong review ETag", () => {
    expect(reviewerEvidenceReadyFor(record, ready)).toBe(true);
    expect(reviewerEvidenceReadyFor(record, { ...ready, verificationId: "verification-2" })).toBe(
      false,
    );
    expect(reviewerEvidenceReadyFor(record, { ...ready, state: "submitted" })).toBe(false);
    expect(
      reviewerEvidenceReadyFor(record, {
        ...ready,
        updatedAt: "2026-08-04T00:00:01Z",
      }),
    ).toBe(false);
    expect(reviewerEvidenceReadyFor(record, { ...ready, reviewEtag: "*" })).toBe(false);
    expect(reviewerEvidenceReadyFor(record, null)).toBe(false);
  });

  it("mounts memory-only evidence only after action selection and gates every decision", () => {
    const queue = source("../components/verification-review-queue.tsx");
    const card = source("../components/verification-review-card.tsx");
    const combined = `${queue}\n${card}`;
    expect(queue).toContain(
      "Access is confirmed by the server; being signed in does not establish reviewer",
    );
    expect(queue).toContain("<VerificationRecordCard");
    expect(card).toContain("<VerificationEvidenceViewer");
    expect(card.indexOf("{selection && expectedEvidenceState !== null && (")).toBeLessThan(
      card.indexOf("<VerificationEvidenceViewer"),
    );
    expect(queue).toContain("!evidenceReady || !reviewerEvidenceReadyFor(record, evidenceReady)");
    expect(card).toContain("decisionBusy || !evidenceIsReady || activationIncomplete");
    expect(queue).toContain("reviewEtag: evidenceReady.reviewEtag");
    expect(queue).toContain("return fingerprintMutationIntent({");
    expect(queue).toContain("clearEvidenceReview();");
    expect(queue).toContain("const page = await listReviewerVerifications");
    expect(queue).toContain("if (!stillCurrent()) return;");
    expect(combined).not.toContain('label="Evidence SHA-256"');
    expect(combined).not.toContain('label="Artifact type"');
    expect(combined).not.toContain('label="Artifact size"');
    expect(combined).not.toContain('record.evidenceKind');
    expect(combined).not.toContain("materialClaimDigest");
    expect(combined).not.toContain("artifact_base64");
    expect(combined).not.toContain("metadata_json");
    expect(combined).not.toContain("storage_path");
    expect(combined).not.toContain("owner_id");
    expect(combined).not.toContain("localStorage");
    expect(combined).not.toContain("sessionStorage");
    expect(combined).not.toContain("console.");
  });

  it("moves focus into review and restores it without keeping removed controls active", () => {
    const activation = { verificationId: record.id, action: "activate" as const };
    const rejection = { verificationId: record.id, action: "reject" as const };
    const other = { verificationId: "verification-2", action: "activate" as const };

    expect(reviewerFocusTarget(null, activation, record.id, false)).toBe("review");
    expect(reviewerFocusTarget(activation, activation, record.id, false)).toBeNull();
    expect(reviewerFocusTarget(activation, rejection, record.id, false)).toBe("review");
    expect(reviewerFocusTarget(activation, null, record.id, false)).toBe("trigger");
    expect(reviewerFocusTarget(activation, null, record.id, true)).toBe("card");
    expect(reviewerFocusTarget(activation, other, record.id, false)).toBe("trigger");
    expect(reviewerFocusTarget(null, other, record.id, false)).toBeNull();

    const card = source("../components/verification-review-card.tsx");
    const focus = source("../components/verification-review-focus.ts");
    expect(card).toContain("aria-controls={reviewRegionId}");
    expect(card).toContain("aria-labelledby={recordHeadingId}");
    expect(card).toContain("<div id={reviewRegionId} hidden />");
    expect(card).toContain('tabIndex={-1}');
    expect(card).toContain("useReviewerFocus(record.id, selection, decisionBusy)");
    expect(focus).toContain('reviewRegionRef.current?.focus({ preventScroll: true });');
    expect(focus).toContain('scrollIntoView({ block: "nearest", behavior: "auto" })');
    expect(focus).toContain('actionButtonRefs.current.get(previous.action)?.focus({ preventScroll: true });');
    expect(focus).toContain('cardRef.current?.focus({ preventScroll: true });');
  });

  it("keeps reviewer authority server-confirmed and clears stale or denied state", () => {
    const value = source("../components/verification-review-queue.tsx");
    expect(value).toContain(REVIEWER_ACCESS_DENIED_MESSAGE);
    expect(value).toContain("attemptKeysRef.current.get(fingerprint)");
    expect(value).toContain("attemptKeysRef.current.delete(fingerprint)");
    expect(value).toContain("reviewerDecisionDisposition(cause)");
    expect(value).toContain('disposition === "rejected"');
    expect(value).toContain("review the current record and confirm a new decision");
    expect(value).toContain("void load(null, false)");
  });

  it("maps 403 to generic denial and clears keys only for confirmed no-change outcomes", () => {
    const denied = new ApiRequestError("configured reviewer: alice", 403, "unauthorized");
    expect(reviewerAccessDenied(denied)).toBe(true);
    expect(reviewerDecisionDisposition(denied)).toBe("denied");
    expect(REVIEWER_ACCESS_DENIED_MESSAGE).not.toContain("alice");
    expect(reviewerDecisionDisposition(new ApiRequestError("stale", 412, "request"))).toBe(
      "stale",
    );
    expect(
      reviewerDecisionDisposition(new ApiRequestError("invalid expiry", 422, "request")),
    ).toBe("rejected");
    expect(
      reviewerDecisionDisposition(new ApiRequestError("service unavailable", 503, "server")),
    ).toBe("uncertain");
  });
});
