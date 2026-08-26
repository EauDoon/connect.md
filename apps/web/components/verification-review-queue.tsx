"use client";

import { Clock3, LoaderCircle, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import {
  VerificationRecordCard,
  reviewerActionLabel,
  reviewerEvidenceReadyFor,
} from "@/components/verification-review-card";
import type { ReviewerEvidenceReady } from "@/components/verification-evidence-viewer";
import type { ReviewerSelection } from "@/components/verification-review-focus";
import { Button } from "@/components/ui/button";
import { ApiRequestError } from "@/lib/api";
import {
  beginLogicalMutationAttempt,
  fingerprintMutationIntent,
  settleLogicalMutationAttempt,
  type LogicalMutationAttempt,
} from "@/lib/logical-mutation";
import {
  appendCursorPage,
  decideReviewerVerification,
  listReviewerVerifications,
  presentRecruitmentError,
  type CursorPage,
  type ReviewerVerification,
  type ReviewerVerificationAction,
  type ReviewerVerificationDecision,
} from "@/lib/recruitment-api";

type LoadState = "loading" | "loaded" | "error" | "denied";
type QueueRequest = { cursor: string | null; append: boolean };
type ReviewerTokenGetter = ReturnType<typeof useConnectmdAuth>["getToken"];

export const REVIEWER_ACCESS_DENIED_MESSAGE =
  "Your signed-in human session does not have verification-review access.";
export type ReviewerDecisionDisposition = "denied" | "stale" | "rejected" | "uncertain";

export function VerificationReviewQueue() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject);
  subjectRef.current = subject;
  const isSubjectCurrent = useCallback(() => subjectRef.current === subject, [subject]);
  if (!configured || !isLoaded || !isSignedIn || !subject) {
    return <ReviewerGate configured={configured} loading={!isLoaded} />;
  }
  return (
    <AuthenticatedVerificationReviewQueue key={subject}
      subject={subject}
      getToken={getToken}
      isSubjectCurrent={isSubjectCurrent}
    />
  );
}

function AuthenticatedVerificationReviewQueue({
  subject,
  getToken,
  isSubjectCurrent,
}: {
  subject: string;
  getToken: ReviewerTokenGetter;
  isSubjectCurrent: () => boolean;
}) {
  const [records, setRecords] = useState<ReviewerVerification[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState<QueueRequest | null>(null);
  const [selection, setSelection] = useState<ReviewerSelection | null>(null);
  const [evidenceReady, setEvidenceReady] = useState<ReviewerEvidenceReady | null>(null);
  const [policyVersion, setPolicyVersion] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const mountedRef = useRef(false);
  const recordsRef = useRef(records);
  recordsRef.current = records;
  const inFlightRef = useRef<string | null>(null);
  const deliveredCursorsRef = useRef(new Set<string>());
  const attemptKeysRef = useRef(new Map<string, LogicalMutationAttempt>());

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const stillCurrent = useCallback(
    () => mountedRef.current && isSubjectCurrent(),
    [isSubjectCurrent],
  );

  const clearEvidenceReview = useCallback(() => {
    setSelection(null);
    setEvidenceReady(null);
    setPolicyVersion("");
    setExpiresAt("");
  }, []);

  const load = useCallback(
    async (cursor: string | null, append: boolean) => {
      const requestKey = cursor ?? "__first_page__";
      if (!stillCurrent() || inFlightRef.current !== null) return;
      if (append && deliveredCursorsRef.current.has(requestKey)) {
        clearEvidenceReview();
        setNextCursor(null);
        setRetry(null);
        setError(
          "The verification queue returned a cursor that did not advance. Loaded records remain visible.",
        );
        return;
      }
      clearEvidenceReview();
      inFlightRef.current = requestKey;
      setLoadState("loading");
      setError("");
      setRetry(null);
      try {
        const page = await listReviewerVerifications(getToken, isSubjectCurrent, cursor);
        if (!stillCurrent()) return;
        const delivered = append ? new Set(deliveredCursorsRef.current) : new Set<string>();
        delivered.add(requestKey);
        deliveredCursorsRef.current = delivered;
        const merged = mergeReviewerVerificationPage(
          append ? recordsRef.current : [],
          page,
          cursor ?? "",
          delivered,
        );
        recordsRef.current = merged.items;
        setRecords(merged.items);
        setNextCursor(merged.nextCursor);
        setLoadState("loaded");
        if (merged.cursorDidNotProgress) {
          setError(
            "The verification queue returned a cursor that did not advance. Loaded records remain visible.",
          );
        }
      } catch (cause) {
        if (!stillCurrent()) return;
        if (reviewerAccessDenied(cause)) {
          attemptKeysRef.current.clear();
          recordsRef.current = [];
          setRecords([]);
          setNextCursor(null);
          clearEvidenceReview();
          setRetry(null);
          setLoadState("denied");
          setError(REVIEWER_ACCESS_DENIED_MESSAGE);
        } else {
          setLoadState(recordsRef.current.length > 0 ? "loaded" : "error");
          setError(presentRecruitmentError(cause));
          setRetry({ cursor, append });
        }
      } finally {
        if (inFlightRef.current === requestKey) inFlightRef.current = null;
      }
    },
    [clearEvidenceReview, getToken, isSubjectCurrent, stillCurrent],
  );

  useEffect(() => {
    void load(null, false);
  }, [load]);

  const choose = (record: ReviewerVerification, action: ReviewerVerificationAction) => {
    if (decisionBusy || !stillCurrent()) return;
    clearEvidenceReview();
    setSelection({ verificationId: record.id, action });
    setNotice("");
  };

  const decide = async (record: ReviewerVerification, action: ReviewerVerificationAction) => {
    if (decisionBusy || !stillCurrent()) return;
    if (!evidenceReady || !reviewerEvidenceReadyFor(record, evidenceReady)) {
      setNotice(
        "Load, verify, and attest the current private evidence before recording this decision.",
      );
      return;
    }
    let decision: ReviewerVerificationDecision = {
      expectedState: record.state,
      reviewEtag: evidenceReady.reviewEtag,
    };
    if (action === "activate") {
      const policy = policyVersion.trim();
      const expiry = decisionExpiry(expiresAt);
      if (!policy || !expiry) {
        setNotice(
          "Activation needs a policy version and a future expiry with an explicit timezone.",
        );
        return;
      }
      decision = {
        ...decision,
        policyVersion: policy,
        expiresAt: expiry,
      };
    }
    if (
      !window.confirm(
        `${reviewerActionLabel(action)} this verification as the signed-in human reviewer?`,
      )
    ) {
      return;
    }
    const fingerprint = reviewerDecisionFingerprint(record, action, decision);
    const attempt = beginLogicalMutationAttempt(
      attemptKeysRef.current.get(fingerprint) ?? null,
      subject,
      { operation: "decide-reviewer-verification", fingerprint },
    );
    attemptKeysRef.current.set(fingerprint, attempt);
    setDecisionBusy(true);
    setNotice("");
    clearEvidenceReview();
    try {
      const updated = await decideReviewerVerification(
        record.id,
        action,
        decision,
        attempt.idempotencyKey,
        getToken,
        isSubjectCurrent,
      );
      if (!stillCurrent()) return;
      attemptKeysRef.current.delete(fingerprint);
      setNotice(
        `The server recorded the verification as ${updated.state.replaceAll("_", " ")}. Refreshing the queue.`,
      );
      void load(null, false);
    } catch (cause) {
      if (!stillCurrent()) return;
      const disposition = reviewerDecisionDisposition(cause);
      if (disposition === "denied") {
        attemptKeysRef.current.clear();
        recordsRef.current = [];
        setRecords([]);
        setNextCursor(null);
        clearEvidenceReview();
        setRetry(null);
        setLoadState("denied");
        setError(REVIEWER_ACCESS_DENIED_MESSAGE);
      } else if (disposition === "stale") {
        attemptKeysRef.current.delete(fingerprint);
        clearEvidenceReview();
        setNotice(
          "This verification changed before the decision was recorded. The queue is being refreshed; review the current record and confirm a new decision.",
        );
        void load(null, false);
      } else if (disposition === "rejected") {
        attemptKeysRef.current.delete(fingerprint);
        clearEvidenceReview();
        setNotice(
          `The server rejected this decision. Reload and review the current evidence before retrying. No change was assumed. ${presentRecruitmentError(cause)}`,
        );
      } else {
        const retained = settleLogicalMutationAttempt(attempt, cause);
        if (retained) attemptKeysRef.current.set(fingerprint, retained);
        else attemptKeysRef.current.delete(fingerprint);
        clearEvidenceReview();
        setNotice(
          retained
            ? `The decision may have been recorded. Reopen the unchanged record, verify the same evidence snapshot, and retry to recover the same result. ${presentRecruitmentError(cause)}`
            : presentRecruitmentError(cause),
        );
      }
    } finally {
      if (stillCurrent()) setDecisionBusy(false);
    }
  };

  const retryCursor = retry?.append ? retry.cursor : nextCursor;
  return (
    <main className="mx-auto max-w-5xl px-5 py-10 pb-16 lg:px-8 lg:py-14">
      <section className="max-w-3xl">
        <p className="eyebrow">Private human reviewer workspace</p>
        <h1 className="mt-3 font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">
          Recruiting-control verification queue.
        </h1>
        <p className="mt-4 text-base leading-7 text-mist">
          Access is confirmed by the server; being signed in does not establish reviewer
          authority. Queue summaries contain no submitted metadata. Private evidence loads only
          after you select a review action and explicitly request it.
        </p>
      </section>
      {notice && (
        <p
          role="status"
          className="mt-7 rounded-xl border border-white/10 bg-panel p-4 text-sm leading-6 text-mist"
        >
          {notice}
        </p>
      )}
      {loadState === "loading" && records.length === 0 && (
        <p role="status" className="mt-8 text-sm text-mist">
          <LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />
          Loading the verification queue…
        </p>
      )}
      {error && (
        <p
          role="alert"
          className="mt-7 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4 text-sm leading-6 text-amber-100"
        >
          {error}
        </p>
      )}
      {loadState === "denied" ? (
        <section className="mt-8 rounded-2xl border border-dashed border-white/15 p-7 text-center">
          <ShieldCheck className="mx-auto size-6 text-acid" aria-hidden />
          <h2 className="mt-3 text-lg font-semibold text-white">
            Verification review unavailable
          </h2>
          <p className="mt-2 text-sm leading-6 text-mist">{REVIEWER_ACCESS_DENIED_MESSAGE}</p>
        </section>
      ) : (
        <>
          {loadState === "loaded" && records.length === 0 && !error && (
            <section className="mt-8 rounded-2xl border border-dashed border-white/15 p-7 text-center">
              <ShieldCheck className="mx-auto size-6 text-acid" aria-hidden />
              <h2 className="mt-3 text-lg font-semibold text-white">
                No open verification records
              </h2>
              <p className="mt-2 text-sm leading-6 text-mist">
                The service returned no submitted or under-review recruiting-control
                verifications.
              </p>
            </section>
          )}
          {records.length > 0 && (
            <ol className="mt-8 space-y-5">
              {records.map((record) => (
                <li key={record.id}>
                  <VerificationRecordCard
                    record={record}
                    selection={selection?.verificationId === record.id ? selection : null}
                    evidenceReady={evidenceReady}
                    subject={subject}
                    getToken={getToken}
                    isSubjectCurrent={isSubjectCurrent}
                    policyVersion={policyVersion}
                    expiresAt={expiresAt}
                    decisionBusy={decisionBusy}
                    choose={choose}
                    cancel={clearEvidenceReview}
                    setEvidenceReady={setEvidenceReady}
                    setPolicyVersion={setPolicyVersion}
                    setExpiresAt={setExpiresAt}
                    decide={decide}
                  />
                </li>
              ))}
            </ol>
          )}
          {retry && !retry.append && (
            <Button
              variant="secondary"
              className="mt-7"
              disabled={loadState === "loading"}
              onClick={() => void load(null, false)}
            >
              {loadState === "loading" && (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              )}
              Retry verification queue
            </Button>
          )}
          {retryCursor && (
            <Button
              variant="secondary"
              className="mt-7"
              disabled={loadState === "loading" || decisionBusy}
              onClick={() => void load(retryCursor, true)}
            >
              {loadState === "loading" && (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              )}
              {retry?.append ? "Retry older verification records" : "Load older verification records"}
            </Button>
          )}
        </>
      )}
      <p className="mt-8 text-xs leading-5 text-mist/75">
        Review, activation, and rejection require a freshly verified and explicitly attested
        evidence snapshot. Stale decisions must be reloaded and deliberately confirmed again.
      </p>
    </main>
  );
}


function ReviewerGate({ configured, loading }: { configured: boolean; loading: boolean }) {
  return (
    <main className="mx-auto max-w-4xl px-5 py-10 lg:px-8 lg:py-14">
      <section className="rounded-[1.5rem] border border-white/10 bg-panel p-6">
        <Clock3 className="size-6 text-acid" aria-hidden />
        <h1 className="mt-4 text-2xl font-semibold text-white">Verification review</h1>
        <AsyncBoundaryMessage className="mt-3 text-sm leading-6 text-mist" loading={loading}>
          {loading
            ? "Checking your signed-in human session…"
            : configured
              ? "Sign in as a human to request server-confirmed verification-review access."
              : "Human authentication is not configured for this deployment."}
        </AsyncBoundaryMessage>
      </section>
    </main>
  );
}


export function reviewerAccessDenied(error: unknown) {
  return (
    error instanceof ApiRequestError &&
    (error.code === "unauthorized" || error.code === "not_found")
  );
}

export function reviewerDecisionDisposition(error: unknown): ReviewerDecisionDisposition {
  if (reviewerAccessDenied(error)) return "denied";
  if (error instanceof ApiRequestError && (error.status === 409 || error.status === 412)) {
    return "stale";
  }
  if (
    error instanceof ApiRequestError &&
    (error.status === 400 ||
      error.status === 413 ||
      error.status === 415 ||
      error.status === 422)
  ) {
    return "rejected";
  }
  return "uncertain";
}

function decisionExpiry(value: string) {
  const date = new Date(value);
  return !value || !Number.isFinite(date.valueOf()) || date.valueOf() <= Date.now()
    ? null
    : date.toISOString();
}


export function mergeReviewerVerificationPage(
  existing: ReviewerVerification[],
  page: CursorPage<ReviewerVerification>,
  currentCursor: string,
  deliveredCursors: ReadonlySet<string>,
) {
  return appendCursorPage(existing, page, currentCursor, deliveredCursors);
}


export function reviewerDecisionFingerprint(
  record: ReviewerVerification,
  action: ReviewerVerificationAction,
  decision: ReviewerVerificationDecision,
) {
  return fingerprintMutationIntent({
    verificationId: record.id,
    recordState: record.state,
    recordUpdatedAt: record.updatedAt,
    action,
    expectedState: decision.expectedState,
    reviewEtag: decision.reviewEtag,
    policyVersion: decision.policyVersion ?? null,
    expiresAt: decision.expiresAt ?? null,
  });
}
