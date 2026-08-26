import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  releaseReviewerEvidenceObjectUrl,
  reviewerEvidencePdfFrameProps,
  reviewerEvidencePreviewKind,
} from "../components/verification-evidence-viewer";

const source = readFileSync(
  new URL("../components/verification-evidence-viewer.tsx", import.meta.url),
  "utf8",
);

afterEach(() => vi.restoreAllMocks());

describe("private verification evidence viewer", () => {
  it("loads only after an explicit human action and requires attestation", () => {
    const action = source.indexOf("onClick={() => void load()}");
    const request = source.indexOf("await loadReviewerEvidence(");
    expect(action).toBeGreaterThan(-1);
    expect(request).toBeGreaterThan(-1);
    expect(source).not.toContain("useEffect(() => { void load()");
    expect(source).toContain("I reviewed this exact evidence and its displayed organization claims.");
    expect(source).toContain("onReadyRef.current(");
    expect(source).toContain("reviewEtag: loaded.evidence.detail.reviewEtag");
  });

  it("binds readiness to current state and update time", () => {
    expect(source).toContain("evidence.detail.state !== expectedState");
    expect(source).toContain("evidence.detail.updatedAt !== expectedUpdatedAt");
    expect(source).toContain("isSubjectCurrent()");
    expect(source).toContain("onReadyRef.current(null)");
  });

  it("keeps previews memory-only and clears every scope", () => {
    expect(source).toContain("URL.createObjectURL(evidence.blob)");
    expect(source).toContain("URL.revokeObjectURL(");
    expect(source).toContain("abortRef.current?.abort()");
    expect(source).toContain("if (disabled) invalidate(true)");
    expect(source).not.toContain("window.open(");
    expect(source).not.toContain("WindowProxy");
    expect(source).not.toContain("localStorage");
    expect(source).not.toContain("sessionStorage");
    expect(source).not.toContain("indexedDB");
    expect(source).not.toContain("console.");
    expect(source).not.toContain("artifact_base64");
  });

  it("keeps the PDF inside a maximally sandboxed parent-owned frame", () => {
    expect(reviewerEvidencePdfFrameProps("blob:private-evidence")).toEqual({
      src: "blob:private-evidence",
      title: "Submitted private verification evidence PDF",
      sandbox: "",
      referrerPolicy: "no-referrer",
    });
    expect(source).toContain("<iframe");
    expect(source).toContain("key={loaded.objectUrl}");
    expect(source).not.toContain("srcDoc");
    expect(source).not.toMatch(/\ballow=/u);
  });

  it("revokes one exact memory URL and clears its parent lifecycle reference", () => {
    const revokeObjectUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const ref = { current: "blob:private-evidence" as string | null };

    releaseReviewerEvidenceObjectUrl(ref);
    releaseReviewerEvidenceObjectUrl(ref);

    expect(revokeObjectUrl).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:private-evidence");
    expect(ref.current).toBeNull();
    expect(source.indexOf("setLoaded(null)")).toBeLessThan(source.indexOf("releaseObjectUrl();"));
  });

  it("renders only escaped text, verified image blobs, or memory PDF blobs", () => {
    expect(reviewerEvidencePreviewKind("text/plain")).toBe("text");
    expect(reviewerEvidencePreviewKind("image/jpeg")).toBe("image");
    expect(reviewerEvidencePreviewKind("image/png")).toBe("image");
    expect(reviewerEvidencePreviewKind("application/pdf")).toBe("pdf");
    expect(source).toContain("{loaded.text}");
    expect(source).toContain("src={loaded.objectUrl}");
    expect(source).not.toContain("dangerouslySetInnerHTML");
    expect(source).not.toContain("href={detail.organizationWebsiteUrl}");
  });
});
