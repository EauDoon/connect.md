import {
  ApiRequestError,
  apiRequestWithMetadata,
  apiResponse,
  type SubjectGuard,
  type TokenGetter,
  withSubjectBoundToken,
} from "@/lib/api";

export const RECRUITING_EVIDENCE_MAX_BYTES = 262_144;
export const RECRUITING_EVIDENCE_CONTENT_TYPES = [
  "application/pdf",
  "image/jpeg",
  "image/png",
  "text/plain",
] as const;

export type RecruitingEvidenceContentType =
  (typeof RECRUITING_EVIDENCE_CONTENT_TYPES)[number];
export type RecruitingEvidenceKind =
  | "corporate_registration"
  | "domain_control"
  | "employment_authority"
  | "other";
export type ReviewerEvidenceState =
  | "submitted"
  | "under_review"
  | "active"
  | "rejected"
  | "expired"
  | "suspended"
  | "revoked";

export type ReviewerEvidenceDetail = {
  verificationId: string;
  organizationSlug: string;
  organizationName: string;
  organizationWebsiteUrl: string | null;
  organizationMaterialVersion: number;
  state: ReviewerEvidenceState;
  evidenceKind: RecruitingEvidenceKind;
  evidenceSha256: string;
  evidenceMetadata: Record<string, string>;
  artifactContentType: RecruitingEvidenceContentType;
  artifactSizeBytes: number;
  evidenceRetentionExpiresAt: string;
  evidenceUrl: string;
  reviewEtag: string;
  submittedAt: string;
  updatedAt: string;
  policyVersion: string | null;
  expiresAt: string | null;
};

export type LoadedReviewerEvidence = {
  detail: ReviewerEvidenceDetail;
  blob: Blob;
  artifactEtag: string;
  contentDigest: string;
};

export class ReviewerEvidenceIntegrityError extends Error {
  constructor() {
    super("The private evidence response failed its integrity checks.");
    this.name = "ReviewerEvidenceIntegrityError";
  }
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const ORGANIZATION_SLUG_PATTERN = /^[a-z0-9][a-z0-9-]*$/u;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const STRONG_ETAG_PATTERN = /^"sha256-[0-9a-f]{64}"$/u;
const CONTENT_TYPE_EXTENSIONS: Record<RecruitingEvidenceContentType, string> = {
  "application/pdf": "pdf",
  "image/jpeg": "jpg",
  "image/png": "png",
  "text/plain": "txt",
};
const FORBIDDEN_DETAIL_FIELDS = [
  "actor_id",
  "artifact_base64",
  "owner_id",
  "storage_path",
  "submitted_by_owner_id",
] as const;

export async function loadReviewerEvidence(
  verificationId: string,
  getToken: TokenGetter,
  isSubjectCurrent: SubjectGuard,
  signal?: AbortSignal,
): Promise<LoadedReviewerEvidence> {
  const detail = await getReviewerEvidenceDetail(
    verificationId,
    getToken,
    isSubjectCurrent,
    signal,
  );
  const artifact = await getReviewerEvidenceArtifact(
    detail,
    getToken,
    isSubjectCurrent,
    signal,
  );
  return { detail, ...artifact };
}

export async function getReviewerEvidenceDetail(
  verificationId: string,
  getToken: TokenGetter,
  isSubjectCurrent: SubjectGuard,
  signal?: AbortSignal,
): Promise<ReviewerEvidenceDetail> {
  const canonicalId = uuid(verificationId, "verification id");
  const path = `/v1/internal/recruiting-verifications/${encodeURIComponent(canonicalId)}`;
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) =>
    apiRequestWithMetadata<unknown>(path, {
      token,
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal,
    }),
  );
  assertSubjectCurrent(isSubjectCurrent);
  requirePrivateNoStore(response.headers);
  const detail = parseReviewerEvidenceDetail(response.body);
  if (
    detail.verificationId !== canonicalId ||
    response.headers.get("etag") !== detail.reviewEtag
  ) {
    throw new ReviewerEvidenceIntegrityError();
  }
  return detail;
}

export async function getReviewerEvidenceArtifact(
  detail: ReviewerEvidenceDetail,
  getToken: TokenGetter,
  isSubjectCurrent: SubjectGuard,
  signal?: AbortSignal,
): Promise<Omit<LoadedReviewerEvidence, "detail">> {
  const response = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) =>
    apiResponse(detail.evidenceUrl, {
      token,
      cache: "no-store",
      headers: { Accept: detail.artifactContentType },
      signal,
    }),
  );
  assertSubjectCurrent(isSubjectCurrent);
  if (!response.ok) throw privateReadError(response.status);

  requirePrivateNoStore(response.headers);
  const expectedArtifactEtag = `"sha256-${detail.evidenceSha256}"`;
  const expectedContentDigest = `sha-256=:${base64FromHex(detail.evidenceSha256)}:`;
  const expectedDisposition =
    `attachment; filename="connectmd-verification-evidence.` +
    `${CONTENT_TYPE_EXTENSIONS[detail.artifactContentType]}"`;
  if (
    response.headers.get("content-type") !== detail.artifactContentType ||
    response.headers.get("etag") !== expectedArtifactEtag ||
    response.headers.get("content-digest") !== expectedContentDigest ||
    response.headers.get("content-disposition") !== expectedDisposition
  ) {
    throw new ReviewerEvidenceIntegrityError();
  }
  const announcedLength = boundedContentLength(response.headers.get("content-length"));
  if (announcedLength !== detail.artifactSizeBytes) {
    throw new ReviewerEvidenceIntegrityError();
  }

  const buffer = await response.arrayBuffer();
  assertSubjectCurrent(isSubjectCurrent);
  if (
    buffer.byteLength !== announcedLength ||
    buffer.byteLength < 1 ||
    buffer.byteLength > RECRUITING_EVIDENCE_MAX_BYTES
  ) {
    throw new ReviewerEvidenceIntegrityError();
  }
  const actualSha256 = await sha256Hex(buffer);
  assertSubjectCurrent(isSubjectCurrent);
  if (actualSha256 !== detail.evidenceSha256) {
    throw new ReviewerEvidenceIntegrityError();
  }
  return {
    blob: new Blob([buffer], { type: detail.artifactContentType }),
    artifactEtag: expectedArtifactEtag,
    contentDigest: expectedContentDigest,
  };
}

export function parseReviewerEvidenceDetail(value: unknown): ReviewerEvidenceDetail {
  const raw = record(value);
  for (const field of FORBIDDEN_DETAIL_FIELDS) {
    if (field in raw) throw new ReviewerEvidenceIntegrityError();
  }
  const verificationId = uuid(raw.verification_id, "verification id");
  const evidenceSha256 = sha256Digest(raw.evidence_sha256);
  const artifactContentType = oneOf(
    raw.artifact_content_type,
    RECRUITING_EVIDENCE_CONTENT_TYPES,
  );
  const artifactSizeBytes = boundedInteger(raw.artifact_size_bytes, 1, RECRUITING_EVIDENCE_MAX_BYTES);
  const evidenceUrl = text(raw.evidence_url);
  const expectedEvidenceUrl =
    `/v1/internal/recruiting-verifications/${encodeURIComponent(verificationId)}/evidence`;
  if (evidenceUrl !== expectedEvidenceUrl) throw new ReviewerEvidenceIntegrityError();

  return {
    verificationId,
    organizationSlug: organizationSlug(raw.organization_slug),
    organizationName: boundedText(raw.organization_name, 1, 160),
    organizationWebsiteUrl: nullableWebsite(raw.organization_website_url),
    organizationMaterialVersion: boundedInteger(
      raw.organization_material_version,
      1,
      Number.MAX_SAFE_INTEGER,
    ),
    state: oneOf(raw.state, [
      "submitted",
      "under_review",
      "active",
      "rejected",
      "expired",
      "suspended",
      "revoked",
    ] as const),
    evidenceKind: oneOf(raw.evidence_kind, [
      "corporate_registration",
      "domain_control",
      "employment_authority",
      "other",
    ] as const),
    evidenceSha256,
    evidenceMetadata: metadata(raw.evidence_metadata),
    artifactContentType,
    artifactSizeBytes,
    evidenceRetentionExpiresAt: timestamp(raw.evidence_retention_expires_at),
    evidenceUrl,
    reviewEtag: strongEtag(raw.review_etag),
    submittedAt: timestamp(raw.submitted_at),
    updatedAt: timestamp(raw.updated_at),
    policyVersion: nullableBoundedText(raw.policy_version, 1, 80),
    expiresAt: nullableTimestamp(raw.expires_at),
  };
}

export function presentReviewerEvidenceError(error: unknown): string {
  if (error instanceof ReviewerEvidenceIntegrityError) return error.message;
  if (error instanceof ApiRequestError) {
    if (error.code === "unauthorized") {
      return "Your signed-in human session does not have verification-review access.";
    }
    if (error.code === "offline") return "You are offline. Reconnect before loading evidence.";
    if (error.status === 404) return "This verification evidence is no longer available.";
    if (error.status === 409 || error.status === 412) {
      return "This verification changed. Reload its current evidence before deciding.";
    }
  }
  return "The private evidence could not be loaded. No review decision was enabled.";
}

async function sha256Hex(buffer: ArrayBuffer): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new ReviewerEvidenceIntegrityError();
  const digest = new Uint8Array(await subtle.digest("SHA-256", buffer));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function base64FromHex(value: string): string {
  const bytes = new Uint8Array(value.match(/../gu)?.map((pair) => Number.parseInt(pair, 16)) ?? []);
  if (bytes.length !== 32) throw new ReviewerEvidenceIntegrityError();
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let encoded = "";
  for (let index = 0; index < bytes.length; index += 3) {
    const first = bytes[index] ?? 0;
    const second = bytes[index + 1];
    const third = bytes[index + 2];
    const value24 = (first << 16) | ((second ?? 0) << 8) | (third ?? 0);
    encoded += alphabet[(value24 >> 18) & 63];
    encoded += alphabet[(value24 >> 12) & 63];
    encoded += second === undefined ? "=" : alphabet[(value24 >> 6) & 63];
    encoded += third === undefined ? "=" : alphabet[value24 & 63];
  }
  return encoded;
}

function requirePrivateNoStore(headers: Headers): void {
  const directives = new Set(
    (headers.get("cache-control") ?? "")
      .split(",")
      .map((directive) => directive.trim().toLowerCase())
      .filter(Boolean),
  );
  if (!directives.has("no-store") || !directives.has("private")) {
    throw new ReviewerEvidenceIntegrityError();
  }
}

function boundedContentLength(value: string | null): number {
  if (value === null || !/^[1-9][0-9]*$/u.test(value)) {
    throw new ReviewerEvidenceIntegrityError();
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed > RECRUITING_EVIDENCE_MAX_BYTES) {
    throw new ReviewerEvidenceIntegrityError();
  }
  return parsed;
}

function privateReadError(status: number): ApiRequestError {
  const code =
    status === 401 || status === 403
      ? "unauthorized"
      : status === 404
        ? "not_found"
        : status >= 500
          ? "server"
          : "request";
  return new ApiRequestError("Private verification evidence is unavailable.", status, code);
}

function assertSubjectCurrent(isSubjectCurrent: SubjectGuard): void {
  if (!isSubjectCurrent()) {
    throw new ApiRequestError(
      "Your signed-in account changed while private evidence was loading.",
      401,
      "unauthorized",
    );
  }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ReviewerEvidenceIntegrityError();
  }
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== "string") throw new ReviewerEvidenceIntegrityError();
  return value;
}

function boundedText(value: unknown, minimum: number, maximum: number): string {
  const parsed = text(value);
  if (parsed.length < minimum || parsed.length > maximum) {
    throw new ReviewerEvidenceIntegrityError();
  }
  return parsed;
}

function nullableBoundedText(
  value: unknown,
  minimum: number,
  maximum: number,
): string | null {
  return value === null ? null : boundedText(value, minimum, maximum);
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw new ReviewerEvidenceIntegrityError();
  }
  return value;
}

function uuid(value: unknown, _label: string): string {
  const parsed = text(value);
  if (!UUID_PATTERN.test(parsed)) throw new ReviewerEvidenceIntegrityError();
  return parsed;
}

function sha256Digest(value: unknown): string {
  const parsed = text(value);
  if (!SHA256_PATTERN.test(parsed)) throw new ReviewerEvidenceIntegrityError();
  return parsed;
}

function strongEtag(value: unknown): string {
  const parsed = text(value);
  if (!STRONG_ETAG_PATTERN.test(parsed)) throw new ReviewerEvidenceIntegrityError();
  return parsed;
}

function oneOf<const T extends readonly string[]>(value: unknown, values: T): T[number] {
  const parsed = text(value);
  if (!values.includes(parsed)) throw new ReviewerEvidenceIntegrityError();
  return parsed as T[number];
}

function timestamp(value: unknown): string {
  const parsed = text(value);
  if (
    !/(?:Z|[+-][0-9]{2}:[0-9]{2})$/u.test(parsed) ||
    Number.isNaN(Date.parse(parsed))
  ) {
    throw new ReviewerEvidenceIntegrityError();
  }
  return parsed;
}

function nullableTimestamp(value: unknown): string | null {
  return value === null ? null : timestamp(value);
}

function nullableWebsite(value: unknown): string | null {
  if (value === null) return null;
  const parsed = boundedText(value, 1, 2_048);
  let url: URL;
  try {
    url = new URL(parsed);
  } catch {
    throw new ReviewerEvidenceIntegrityError();
  }
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.hostname === "localhost" ||
    url.hostname.endsWith(".localhost")
  ) {
    throw new ReviewerEvidenceIntegrityError();
  }
  return parsed;
}

function metadata(value: unknown): Record<string, string> {
  const raw = record(value);
  const entries = Object.entries(raw);
  if (entries.length > 20) throw new ReviewerEvidenceIntegrityError();
  const safeEntries: Array<[string, string]> = [];
  for (const [key, item] of entries) {
    if (!key || key.length > 64 || typeof item !== "string" || item.length > 500) {
      throw new ReviewerEvidenceIntegrityError();
    }
    safeEntries.push([key, item]);
  }
  return Object.fromEntries(safeEntries);
}

function organizationSlug(value: unknown): string {
  const parsed = boundedText(value, 1, 80);
  if (!ORGANIZATION_SLUG_PATTERN.test(parsed)) throw new ReviewerEvidenceIntegrityError();
  return parsed;
}
