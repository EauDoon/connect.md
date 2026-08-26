import { createHash, webcrypto } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ReviewerEvidenceIntegrityError,
  loadReviewerEvidence,
  parseReviewerEvidenceDetail,
} from "../lib/recruiting-evidence-api";

const verificationId = "22222222-2222-4222-8222-222222222222";
const payload = new TextEncoder().encode("private reviewer evidence");
const evidenceSha256 = createHash("sha256").update(payload).digest("hex");
const contentDigest = `sha-256=:${createHash("sha256").update(payload).digest("base64")}:`;
const reviewEtag = `"sha256-${"b".repeat(64)}"`;
const detailBody = {
  verification_id: verificationId,
  organization_slug: "acme",
  organization_name: "Acme",
  organization_website_url: "https://acme.example",
  organization_material_version: 3,
  state: "under_review",
  evidence_kind: "corporate_registration",
  evidence_sha256: evidenceSha256,
  evidence_metadata: { jurisdiction: "SG", registration: "2026-001" },
  artifact_content_type: "text/plain",
  artifact_size_bytes: payload.byteLength,
  evidence_retention_expires_at: "2026-09-10T02:00:00Z",
  evidence_url: `/v1/internal/recruiting-verifications/${verificationId}/evidence`,
  review_etag: reviewEtag,
  submitted_at: "2026-08-10T02:00:00Z",
  updated_at: "2026-08-11T02:00:00Z",
  policy_version: null,
  expires_at: null,
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function detailResponse(body: unknown = detailBody, etag = reviewEtag) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      "Cache-Control": "no-store, private",
      "Content-Type": "application/json",
      ETag: etag,
    },
  });
}

function artifactResponse(
  body: BodyInit = payload,
  headers: Record<string, string> = {},
) {
  return new Response(body, {
    status: 200,
    headers: {
      "Cache-Control": "no-store, private",
      "Content-Digest": contentDigest,
      "Content-Disposition": 'attachment; filename="connectmd-verification-evidence.txt"',
      "Content-Length": String(payload.byteLength),
      "Content-Type": "text/plain",
      ETag: `"sha256-${evidenceSha256}"`,
      ...headers,
    },
  });
}

function configure(detail = detailResponse(), artifact = artifactResponse()) {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
  vi.stubGlobal("crypto", webcrypto);
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(detail)
    .mockResolvedValueOnce(artifact);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("private recruiting evidence API", () => {
  it("loads exact no-store detail and client-verified artifact bytes", async () => {
    const fetchMock = configure();

    const loaded = await loadReviewerEvidence(
      verificationId,
      async () => "clerk-token",
      () => true,
    );

    expect(loaded.detail).toMatchObject({
      verificationId,
      organizationName: "Acme",
      evidenceMetadata: { jurisdiction: "SG", registration: "2026-001" },
      reviewEtag,
    });
    expect(await loaded.blob.text()).toBe("private reviewer evidence");
    expect(loaded.artifactEtag).toBe(`"sha256-${evidenceSha256}"`);
    expect(loaded.contentDigest).toBe(contentDigest);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [detailUrl, detailInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [artifactUrl, artifactInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(detailUrl).toBe(
      `https://api.connect.test/v1/internal/recruiting-verifications/${verificationId}`,
    );
    expect(artifactUrl).toBe(
      `https://api.connect.test/v1/internal/recruiting-verifications/${verificationId}/evidence`,
    );
    expect(detailInit.cache).toBe("no-store");
    expect(artifactInit.cache).toBe("no-store");
    expect(new Headers(detailInit.headers).get("Authorization")).toBe("Bearer clerk-token");
    expect(new Headers(artifactInit.headers).get("Accept")).toBe("text/plain");
  });

  it("rejects private or server-owned fields outside the detail allowlist", () => {
    expect(() =>
      parseReviewerEvidenceDetail({ ...detailBody, storage_path: "private/path.bin" }),
    ).toThrow(ReviewerEvidenceIntegrityError);
    expect(() =>
      parseReviewerEvidenceDetail({ ...detailBody, owner_id: "private-owner" }),
    ).toThrow(ReviewerEvidenceIntegrityError);
    expect(() =>
      parseReviewerEvidenceDetail({
        ...detailBody,
        organization_website_url: "http://acme.example",
      }),
    ).toThrow(ReviewerEvidenceIntegrityError);
  });

  it("preserves bounded metadata keys without prototype mutation", () => {
    const evidenceMetadata = Object.fromEntries([["__proto__", "review-only"]]);
    const parsed = parseReviewerEvidenceDetail({
      ...detailBody,
      evidence_metadata: evidenceMetadata,
    });

    expect(Object.getPrototypeOf(parsed.evidenceMetadata)).toBe(Object.prototype);
    expect(Object.prototype.hasOwnProperty.call(parsed.evidenceMetadata, "__proto__")).toBe(true);
    expect(parsed.evidenceMetadata.__proto__).toBe("review-only");
  });

  it("rejects a detail validator that differs from the body snapshot", async () => {
    configure(detailResponse(detailBody, `"sha256-${"c".repeat(64)}"`));

    await expect(
      loadReviewerEvidence(verificationId, async () => "clerk-token", () => true),
    ).rejects.toBeInstanceOf(ReviewerEvidenceIntegrityError);
  });

  it("rejects detail bytes for a different verification record before loading its artifact", async () => {
    const differentId = "33333333-3333-4333-8333-333333333333";
    const fetchMock = configure(
      detailResponse({
        ...detailBody,
        verification_id: differentId,
        evidence_url: `/v1/internal/recruiting-verifications/${differentId}/evidence`,
      }),
    );

    await expect(
      loadReviewerEvidence(verificationId, async () => "clerk-token", () => true),
    ).rejects.toBeInstanceOf(ReviewerEvidenceIntegrityError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["Content-Disposition", 'inline; filename="evidence.txt"'],
    ["Content-Digest", `sha-256=:${"A".repeat(44)}:`],
    ["Content-Length", String(payload.byteLength + 1)],
    ["Content-Type", "text/html"],
    ["ETag", `"sha256-${"0".repeat(64)}"`],
    ["Cache-Control", "public, max-age=3600"],
  ])("fails closed when %s violates the artifact contract", async (header, value) => {
    configure(detailResponse(), artifactResponse(payload, { [header]: value }));

    await expect(
      loadReviewerEvidence(verificationId, async () => "clerk-token", () => true),
    ).rejects.toBeInstanceOf(ReviewerEvidenceIntegrityError);
  });

  it("independently hashes the received bytes", async () => {
    const tampered = new TextEncoder().encode("tampered reviewer evidence");
    configure(
      detailResponse(),
      artifactResponse(tampered, { "Content-Length": String(tampered.byteLength) }),
    );

    await expect(
      loadReviewerEvidence(verificationId, async () => "clerk-token", () => true),
    ).rejects.toBeInstanceOf(ReviewerEvidenceIntegrityError);
  });

  it("drops a completed response when the authenticated subject changes", async () => {
    let current = true;
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    vi.stubGlobal("crypto", webcrypto);
    const fetchMock = vi.fn<typeof fetch>().mockImplementationOnce(async () => {
      current = false;
      return detailResponse();
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      loadReviewerEvidence(verificationId, async () => "clerk-token", () => current),
    ).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
