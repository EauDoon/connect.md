"use client";

import { FileCheck2, LoaderCircle, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { SubjectGuard, TokenGetter } from "@/lib/api";
import {
  loadReviewerEvidence,
  presentReviewerEvidenceError,
  type LoadedReviewerEvidence,
  type RecruitingEvidenceContentType,
  type ReviewerEvidenceState,
} from "@/lib/recruiting-evidence-api";

export type ReviewerEvidenceReady = {
  verificationId: string;
  reviewEtag: string;
  state: ReviewerEvidenceState;
  updatedAt: string;
};

export type VerificationEvidenceViewerProps = {
  verificationId: string;
  expectedState: ReviewerEvidenceState;
  expectedUpdatedAt: string;
  subjectScope: string;
  getToken: TokenGetter;
  isSubjectCurrent: SubjectGuard;
  disabled?: boolean;
  onReady: (value: ReviewerEvidenceReady | null) => void;
};

type ViewerState = "idle" | "loading" | "ready" | "error";
type LoadedView = {
  evidence: LoadedReviewerEvidence;
  objectUrl: string | null;
  text: string | null;
};

export function VerificationEvidenceViewer({
  verificationId,
  expectedState,
  expectedUpdatedAt,
  subjectScope,
  getToken,
  isSubjectCurrent,
  disabled = false,
  onReady,
}: VerificationEvidenceViewerProps) {
  const [state, setState] = useState<ViewerState>("idle");
  const [loaded, setLoaded] = useState<LoadedView | null>(null);
  const [attested, setAttested] = useState(false);
  const [error, setError] = useState("");
  const mountedRef = useRef(false);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  const releaseObjectUrl = useCallback(() => {
    releaseReviewerEvidenceObjectUrl(objectUrlRef);
  }, []);

  const invalidate = useCallback(
    (updateState: boolean) => {
      requestRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      onReadyRef.current(null);
      if (updateState) {
        setLoaded(null);
        setAttested(false);
        setError("");
        setState("idle");
      }
      releaseObjectUrl();
    },
    [releaseObjectUrl],
  );

  useEffect(() => {
    mountedRef.current = true;
    invalidate(true);
    return () => {
      mountedRef.current = false;
      invalidate(false);
    };
  }, [expectedState, expectedUpdatedAt, invalidate, subjectScope, verificationId]);

  useEffect(() => {
    if (disabled) invalidate(true);
  }, [disabled, invalidate]);

  const isCurrentRequest = useCallback(
    (requestId: number) =>
      mountedRef.current && requestRef.current === requestId && isSubjectCurrent(),
    [isSubjectCurrent],
  );

  const load = useCallback(async () => {
    if (disabled || state === "loading" || !isSubjectCurrent()) return;
    invalidate(true);
    const requestId = requestRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setState("loading");
    try {
      const evidence = await loadReviewerEvidence(
        verificationId,
        getToken,
        isSubjectCurrent,
        controller.signal,
      );
      if (!isCurrentRequest(requestId)) return;
      if (
        evidence.detail.state !== expectedState ||
        evidence.detail.updatedAt !== expectedUpdatedAt
      ) {
        throw new Error("verification evidence changed during review");
      }
      const preview = reviewerEvidencePreviewKind(evidence.detail.artifactContentType);
      const text = preview === "text" ? await evidence.blob.text() : null;
      if (!isCurrentRequest(requestId)) return;
      const objectUrl = preview === "text" ? null : URL.createObjectURL(evidence.blob);
      if (!isCurrentRequest(requestId)) {
        if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
        return;
      }
      releaseObjectUrl();
      objectUrlRef.current = objectUrl;
      setLoaded({ evidence, objectUrl, text });
      setState("ready");
    } catch (cause) {
      if (!isCurrentRequest(requestId)) return;
      releaseObjectUrl();
      setLoaded(null);
      setAttested(false);
      onReadyRef.current(null);
      setError(
        cause instanceof Error && cause.message === "verification evidence changed during review"
          ? "This verification changed. Reload the queue before reviewing it."
          : presentReviewerEvidenceError(cause),
      );
      setState("error");
    } finally {
      if (requestRef.current === requestId) abortRef.current = null;
    }
  }, [
    disabled,
    expectedState,
    expectedUpdatedAt,
    getToken,
    invalidate,
    isCurrentRequest,
    isSubjectCurrent,
    releaseObjectUrl,
    state,
    verificationId,
  ]);

  const setReviewerAttestation = (checked: boolean) => {
    if (!loaded || state !== "ready" || disabled || !isSubjectCurrent()) return;
    setAttested(checked);
    onReadyRef.current(
      checked
        ? {
            verificationId: loaded.evidence.detail.verificationId,
            reviewEtag: loaded.evidence.detail.reviewEtag,
            state: loaded.evidence.detail.state,
            updatedAt: loaded.evidence.detail.updatedAt,
          }
        : null,
    );
  };

  return (
    <section className="mt-5 rounded-xl border border-white/10 bg-black/15 p-4" aria-label="Private verification evidence">
      <div className="flex items-start gap-3">
        <FileCheck2 className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden />
        <div>
          <h3 className="text-sm font-semibold text-white">Review the current private evidence</h3>
          <p className="mt-1 text-xs leading-5 text-mist">
            Evidence loads only after an explicit request. Verified bytes and preview URLs remain in memory for this signed-in review scope and are cleared when it changes.
          </p>
        </div>
      </div>

      {state === "idle" && (
        <Button className="mt-4" variant="secondary" disabled={disabled} onClick={() => void load()}>
          Load private evidence
        </Button>
      )}
      {state === "loading" && (
        <p className="mt-4 text-sm text-mist" role="status">
          <LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />
          Verifying private evidence…
        </p>
      )}
      {state === "error" && (
        <div className="mt-4 rounded-lg border border-amber-300/25 bg-amber-300/[.08] p-3">
          <p className="text-sm leading-6 text-amber-100" role="alert">
            <ShieldAlert className="mr-2 inline size-4" aria-hidden />
            {error}
          </p>
          <Button className="mt-3" variant="secondary" disabled={disabled} onClick={() => void load()}>
            Retry private evidence
          </Button>
        </div>
      )}

      {state === "ready" && loaded && (
        <EvidenceReview
          loaded={loaded}
          attested={attested}
          disabled={disabled}
          setAttested={setReviewerAttestation}
        />
      )}
    </section>
  );
}

function EvidenceReview({
  loaded,
  attested,
  disabled,
  setAttested,
}: {
  loaded: LoadedView;
  attested: boolean;
  disabled: boolean;
  setAttested: (checked: boolean) => void;
}) {
  const { detail } = loaded.evidence;
  const preview = reviewerEvidencePreviewKind(detail.artifactContentType);
  const metadata = Object.entries(detail.evidenceMetadata).sort(([left], [right]) =>
    left.localeCompare(right),
  );
  return (
    <div className="mt-4">
      <dl className="grid gap-3 border-t border-white/10 pt-4 text-xs sm:grid-cols-2">
        <EvidenceFact label="Organization" value={detail.organizationName} />
        <EvidenceFact label="Website claim" value={detail.organizationWebsiteUrl ?? "Not supplied"} />
        <EvidenceFact label="Evidence kind" value={detail.evidenceKind.replaceAll("_", " ")} />
        <EvidenceFact label="Artifact type" value={detail.artifactContentType} />
        <EvidenceFact label="Artifact size" value={`${detail.artifactSizeBytes} bytes`} />
        <EvidenceFact label="Evidence SHA-256" value={detail.evidenceSha256} mono />
        <EvidenceFact label="Retained until" value={formatEvidenceTime(detail.evidenceRetentionExpiresAt)} />
        <EvidenceFact label="Submitted" value={formatEvidenceTime(detail.submittedAt)} />
      </dl>
      {metadata.length > 0 && (
        <section className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-[.15em] text-mist/70">Submitted metadata</h4>
          <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
            {metadata.map(([key, value]) => (
              <EvidenceFact key={key} label={key} value={value} />
            ))}
          </dl>
        </section>
      )}
      <div className="mt-4 overflow-hidden rounded-lg border border-white/10 bg-black/25 p-3">
        {preview === "text" && (
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-white">
            {loaded.text}
          </pre>
        )}
        {preview === "image" && loaded.objectUrl && (
          // The source is a verified in-memory JPEG/PNG Blob, never a remote URL.
          // eslint-disable-next-line @next/next/no-img-element
          <img className="max-h-96 w-full object-contain" src={loaded.objectUrl} alt="Submitted private verification evidence" />
        )}
        {preview === "pdf" && loaded.objectUrl && (
          <iframe
            key={loaded.objectUrl}
            {...reviewerEvidencePdfFrameProps(loaded.objectUrl)}
            className="h-[32rem] w-full rounded-md bg-white"
          />
        )}
      </div>
      <label className="mt-4 flex items-start gap-3 text-sm leading-6 text-mist">
        <input
          type="checkbox"
          className="mt-1 size-4 accent-acid"
          checked={attested}
          disabled={disabled}
          onChange={(event) => setAttested(event.target.checked)}
        />
        <span>I reviewed this exact evidence and its displayed organization claims. Bind my next decision to this review snapshot.</span>
      </label>
    </div>
  );
}

function EvidenceFact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-mist/70">{label}</dt>
      <dd className={`mt-1 break-all text-white ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

export function reviewerEvidencePreviewKind(
  contentType: RecruitingEvidenceContentType,
): "text" | "image" | "pdf" {
  if (contentType === "text/plain") return "text";
  if (contentType === "application/pdf") return "pdf";
  return "image";
}

export function reviewerEvidencePdfFrameProps(objectUrl: string) {
  return {
    src: objectUrl,
    title: "Submitted private verification evidence PDF",
    sandbox: "",
    referrerPolicy: "no-referrer" as const,
  };
}

export function releaseReviewerEvidenceObjectUrl(objectUrlRef: { current: string | null }): void {
  const objectUrl = objectUrlRef.current;
  objectUrlRef.current = null;
  if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
}

function formatEvidenceTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
