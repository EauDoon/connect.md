"use client";

import { LoaderCircle } from "lucide-react";

import {
  VerificationEvidenceViewer,
  type ReviewerEvidenceReady,
} from "@/components/verification-evidence-viewer";
import {
  useReviewerFocus,
  type ReviewerSelection,
} from "@/components/verification-review-focus";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import type { TokenGetter } from "@/lib/api";
import type {
  ReviewerVerification,
  ReviewerVerificationAction,
} from "@/lib/recruitment-api";

export function VerificationRecordCard({
  record,
  selection,
  evidenceReady,
  subject,
  getToken,
  isSubjectCurrent,
  policyVersion,
  expiresAt,
  decisionBusy,
  choose,
  cancel,
  setEvidenceReady,
  setPolicyVersion,
  setExpiresAt,
  decide,
}: {
  record: ReviewerVerification;
  selection: ReviewerSelection | null;
  evidenceReady: ReviewerEvidenceReady | null;
  subject: string;
  getToken: TokenGetter;
  isSubjectCurrent: () => boolean;
  policyVersion: string;
  expiresAt: string;
  decisionBusy: boolean;
  choose: (record: ReviewerVerification, action: ReviewerVerificationAction) => void;
  cancel: () => void;
  setEvidenceReady: (value: ReviewerEvidenceReady | null) => void;
  setPolicyVersion: (value: string) => void;
  setExpiresAt: (value: string) => void;
  decide: (record: ReviewerVerification, action: ReviewerVerificationAction) => Promise<void>;
}) {
  const actions = reviewerActionsFor(record);
  const evidenceIsReady = reviewerEvidenceReadyFor(record, evidenceReady);
  const expectedEvidenceState =
    record.state === "submitted" || record.state === "under_review" ? record.state : null;
  const activationIncomplete =
    selection?.action === "activate" && (!policyVersion.trim() || !expiresAt);
  const {
    cardRef,
    recordHeadingId,
    reviewRegionId,
    reviewRegionRef,
    setActionButtonRef,
  } = useReviewerFocus(record.id, selection, decisionBusy);

  return (
    <article
      ref={cardRef}
      tabIndex={-1}
      aria-labelledby={recordHeadingId}
      className="rounded-[1.4rem] border border-white/10 bg-panel p-5 outline-none focus-visible:ring-2 focus-visible:ring-acid/60 sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Recruiting-control verification</p>
          <h2 id={recordHeadingId} className="mt-2 text-xl font-semibold text-white">{record.organizationName}</h2>
          <p className="mt-1 text-sm text-mist">{record.organizationSlug}</p>
        </div>
        <span className="rounded-full border border-white/15 px-3 py-1 text-xs font-semibold text-mist">
          {record.state.replaceAll("_", " ")}
        </span>
      </div>
      <dl className="mt-6 grid gap-4 border-t border-white/10 pt-5 text-sm sm:grid-cols-2">
        <Fact label="Submitted" value={formatTime(record.submittedAt)} />
        <Fact label="Last updated" value={formatTime(record.updatedAt)} />
        <Fact label="Policy version" value={record.policyVersion ?? "Not issued"} />
        <Fact label="Decision expiry" value={formatTime(record.expiresAt)} />
      </dl>
      {actions.length > 0 && !selection && (
        <>
          <div className="mt-5 flex flex-wrap gap-2">
            {actions.map((action) => (
              <Button
                key={action}
                ref={(button) => setActionButtonRef(action, button)}
                aria-controls={reviewRegionId}
                variant={action === "reject" ? "danger" : "secondary"}
                disabled={decisionBusy}
                onClick={() => choose(record, action)}
              >
                {reviewerActionLabel(action)}
              </Button>
            ))}
          </div>
          <div id={reviewRegionId} hidden />
        </>
      )}
      {selection && expectedEvidenceState !== null && (
        <section
          id={reviewRegionId}
          ref={reviewRegionRef}
          tabIndex={-1}
          aria-label={`${reviewerActionLabel(selection.action)} review for ${record.organizationName}`}
          className="scroll-mt-24 outline-none focus-visible:ring-2 focus-visible:ring-acid/60"
        >
          <VerificationEvidenceViewer
            key={`${record.id}:${record.state}:${record.updatedAt}`}
            verificationId={record.id}
            expectedState={expectedEvidenceState}
            expectedUpdatedAt={record.updatedAt}
            subjectScope={subject}
            getToken={getToken}
            isSubjectCurrent={isSubjectCurrent}
            disabled={decisionBusy}
            onReady={setEvidenceReady}
          />
          <section className="mt-5 rounded-xl border border-acid/25 bg-acid/[.05] p-4">
            <h3 className="text-sm font-semibold text-white">
              Confirm {reviewerActionLabel(selection.action).toLowerCase()}
            </h3>
            <p className="mt-2 text-sm leading-6 text-mist">
              {evidenceIsReady
                ? "Your attestation is bound to the verified review snapshot shown above."
                : "Load the current private evidence and attest it before this decision is enabled."}
            </p>
            {selection.action === "activate" && (
              <>
                <label className="mt-4 block text-sm font-semibold text-white">
                  Policy version
                  <Input
                    className="mt-1.5"
                    value={policyVersion}
                    maxLength={80}
                    disabled={decisionBusy}
                    onChange={(event) => setPolicyVersion(event.target.value)}
                    placeholder="recruiting-control-v1"
                  />
                </label>
                <label className="mt-3 block text-sm font-semibold text-white">
                  Decision expiry (local time)
                  <Input
                    className="mt-1.5"
                    type="datetime-local"
                    value={expiresAt}
                    disabled={decisionBusy}
                    onChange={(event) => setExpiresAt(event.target.value)}
                  />
                </label>
              </>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                disabled={decisionBusy || !evidenceIsReady || activationIncomplete}
                onClick={() => void decide(record, selection.action)}
              >
                {decisionBusy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
                Confirm {reviewerActionLabel(selection.action).toLowerCase()}
              </Button>
              <Button variant="ghost" disabled={decisionBusy} onClick={cancel}>
                Cancel
              </Button>
            </div>
          </section>
        </section>
      )}
    </article>
  );
}

function reviewerActionsFor(record: ReviewerVerification): ReviewerVerificationAction[] {
  if (record.state === "submitted") return ["review"];
  if (record.state === "under_review") return ["activate", "reject"];
  return [];
}

export function reviewerActionLabel(action: ReviewerVerificationAction) {
  if (action === "review") return "Start independent review";
  if (action === "activate") return "Activate recruiting control";
  return "Reject verification";
}

function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-mist/70">{label}</dt>
      <dd className={`mt-1 break-all text-white ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

function formatTime(value: string | null) {
  if (!value) return "Not issued";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(
        date,
      );
}

export function reviewerEvidenceReadyFor(
  record: ReviewerVerification,
  ready: ReviewerEvidenceReady | null,
) {
  return (
    ready !== null &&
    ready.verificationId === record.id &&
    ready.state === record.state &&
    ready.updatedAt === record.updatedAt &&
    /^"sha256-[0-9a-f]{64}"$/u.test(ready.reviewEtag)
  );
}

