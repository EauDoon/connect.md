import { ApiRequestError, apiRequest, withSubjectBoundToken, type SubjectGuard, type TokenGetter } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/logical-mutation";

export type Organization = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  websiteUrl: string | null;
  visibility: "public" | "private";
  recruitingVerificationActive: boolean;
  recruitingVerificationPurpose: "recruiting_control" | null;
  recruitingVerificationExpiresAt: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
  etag: string;
};

export type Job = {
  id: string;
  organizationId: string;
  organizationSlug: string;
  organizationName: string;
  slug: string;
  title: string;
  description: string;
  location: string | null;
  workMode: "remote" | "hybrid" | "onsite" | null;
  employmentType: "full_time" | "part_time" | "contract" | "internship" | "temporary" | null;
  status: "draft" | "published" | "closed";
  version: number;
  publishedAt: string | null;
  createdAt: string;
  updatedAt: string;
  etag: string;
};

export type ApplicationStatus = "submitted" | "under_review" | "accepted" | "rejected" | "withdrawn";
export type Application = {
  id: string;
  jobId: string;
  organizationSlug: string;
  jobSlug: string;
  status: ApplicationStatus;
  snapshotKind: "profile" | "resume";
  snapshotIdentifier: string;
  snapshotVersion: number;
  snapshotSha256: string;
  confirmedAt: string;
  retentionPolicyVersion: string;
  retentionExpiresAt: string;
  createdAt: string;
  updatedAt: string;
  decidedAt: string | null;
};

export type ApplicationDetail = Application & { message: string };
export type ApplicationSnapshot = {
  applicationId: string;
  snapshotKind: "profile" | "resume";
  snapshotIdentifier: string;
  snapshotVersion: number;
  snapshotSha256: string;
  markdown: string;
  markdownUrl: string;
};
export type ApplicationDocument = { id: string; kind: "profile" | "resume"; identifier: string; version: number; visibility: "public" | "private" };
export type OrganizationInvitation = { id: string; organizationId: string; memberProfileHandle: string | null; role: "admin" | "member"; status: "invited" | "active"; createdAt: string };
export type OrganizationMembershipInvitation = { id: string; organizationId: string; organizationSlug: string; organizationName: string; role: "admin" | "member"; status: "invited"; createdAt: string };
export type CursorPage<T> = { items: T[]; nextCursor: string | null };
export type ManageableOrganizationSummary = {
  id: string;
  slug: string;
  name: string;
  managementRole: "owner" | "admin";
  visibility: "public" | "private";
  recruitingVerificationActive: boolean;
  recruitingVerificationPurpose: Organization["recruitingVerificationPurpose"];
  recruitingVerificationExpiresAt: string | null;
  updatedAt: string;
};
export type ManageableJobSummary = {
  id: string;
  organizationId: string;
  organizationSlug: string;
  organizationName: string;
  managementRole: "owner" | "admin";
  slug: string;
  title: string;
  status: "draft" | "published" | "closed";
  location: string | null;
  workMode: Job["workMode"];
  employmentType: Job["employmentType"];
  updatedAt: string;
};

export type JobSearchFilters = {
  q: string;
  organizationSlug: string;
  location: string;
  workMode: "" | "remote" | "hybrid" | "onsite";
  employmentType: "" | "full_time" | "part_time" | "contract" | "internship" | "temporary";
  cursor: string | null;
};

export const APPLICATION_MESSAGE_MAX_LENGTH = 2000;
export const VERIFICATION_ARTIFACT_MAX_BYTES = 262_144;
export const VERIFICATION_ARTIFACT_CONTENT_TYPES = ["application/pdf", "image/jpeg", "image/png", "text/plain"] as const;
export const VERIFICATION_EVIDENCE_KINDS = ["corporate_registration", "domain_control", "employment_authority", "other"] as const;
export const VERIFICATION_STATUSES = ["unverified", "submitted", "under_review", "active", "rejected", "expired", "suspended", "revoked"] as const;
export const REVIEWER_VERIFICATION_ACTIONS = ["review", "activate", "reject"] as const;
export type VerificationArtifactContentType = (typeof VERIFICATION_ARTIFACT_CONTENT_TYPES)[number];
export type VerificationEvidenceKind = (typeof VERIFICATION_EVIDENCE_KINDS)[number];
export type VerificationStatus = (typeof VERIFICATION_STATUSES)[number];
export type ReviewerVerificationAction = (typeof REVIEWER_VERIFICATION_ACTIONS)[number];
export type OrganizationVerificationSubmission = { verificationId: string; state: "submitted"; evidenceSha256: string; artifactContentType: VerificationArtifactContentType; artifactSizeBytes: number; submittedAt: string };
export type OrganizationVerificationOwnerStatus = { verificationId: string | null; state: VerificationStatus; submittedAt: string | null; updatedAt: string | null; policyVersion: string | null; expiresAt: string | null };
export type ReviewerVerification = { id: string; organizationSlug: string; organizationName: string; state: VerificationStatus; submittedAt: string; updatedAt: string; policyVersion: string | null; expiresAt: string | null };
export type ReviewerVerificationDecision = { expectedState: VerificationStatus; reviewEtag: string; policyVersion?: string; expiresAt?: string };

export function authSubjectIsCurrent(currentSubject: string | null, requestSubject: string | null) {
  return Boolean(requestSubject) && currentSubject === requestSubject;
}

export function appendCursorPage<T extends { id: string }>(
  existing: T[],
  page: CursorPage<T>,
  currentCursor: string,
  deliveredCursors: ReadonlySet<string>,
) {
  const known = new Set(existing.map((item) => item.id));
  const cursorDidNotProgress =
    page.nextCursor !== null &&
    (page.nextCursor === currentCursor || deliveredCursors.has(page.nextCursor));
  return {
    items: [
      ...existing,
      ...page.items.filter((item) => {
        if (known.has(item.id)) return false;
        known.add(item.id);
        return true;
      }),
    ],
    nextCursor: cursorDidNotProgress ? null : page.nextCursor,
    cursorDidNotProgress,
  };
}

export function hasActiveRecruitingControl(organization: Pick<Organization, "recruitingVerificationActive" | "recruitingVerificationPurpose">) {
  return organization.recruitingVerificationActive && organization.recruitingVerificationPurpose === "recruiting_control";
}

export function presentRecruitmentError(error: unknown) {
  if (!(error instanceof ApiRequestError)) return "connect.md could not complete that recruitment request. No change was assumed.";
  if (error.code === "offline") return "You are offline. Reconnect before trying again.";
  if (error.code === "unauthorized") return "Your signed-in human session is not authorized for that recruitment action.";
  if (error.code === "server") return "connect.md is temporarily unavailable. No change was assumed.";
  return error.message;
}

export class ApplicationSnapshotIntegrityError extends Error {
  constructor(message = "The immutable application snapshot did not pass integrity verification.") {
    super(message);
    this.name = "ApplicationSnapshotIntegrityError";
  }
}

export function presentApplicationSnapshotError(error: unknown) {
  if (error instanceof ApplicationSnapshotIntegrityError) return "The immutable snapshot failed its integrity check and was not shown or downloaded.";
  if (!(error instanceof ApiRequestError)) return "The immutable snapshot could not be verified and was not shown.";
  if (error.code === "offline") return "You are offline. Reconnect before opening the immutable snapshot.";
  if (error.code === "unauthorized") return "Your signed-in human session is not authorized to review this application snapshot.";
  if (error.status === 410) return "This immutable snapshot has expired and is no longer available for review.";
  if (error.status === 404) {
    const detail = error.message.toLowerCase();
    if (detail.includes("withdrawn")) return "This immutable snapshot is unavailable because the application was withdrawn.";
    if (detail.includes("expired")) return "This immutable snapshot has expired and is no longer available for review.";
    return "This immutable snapshot is missing or is no longer available for this application review.";
  }
  if (error.code === "server") return "connect.md could not load the immutable snapshot. It was not shown.";
  return error.message;
}

export const emptyJobSearchFilters: JobSearchFilters = { q: "", organizationSlug: "", location: "", workMode: "", employmentType: "", cursor: null };

export function jobSearchFiltersFromParams(params: URLSearchParams): JobSearchFilters {
  const workMode = text(params.get("work_mode"));
  const employmentType = text(params.get("employment_type"));
  return {
    q: text(params.get("q")),
    organizationSlug: text(params.get("organization_slug")),
    location: text(params.get("location")),
    workMode: workMode === "remote" || workMode === "hybrid" || workMode === "onsite" ? workMode : "",
    employmentType: employmentType === "full_time" || employmentType === "part_time" || employmentType === "contract" || employmentType === "internship" || employmentType === "temporary" ? employmentType : "",
    cursor: textOrNull(params.get("cursor"))
  };
}

export function jobSearchHref(filters: JobSearchFilters, cursor: string | null = filters.cursor) {
  const params = new URLSearchParams();
  set(params, "q", filters.q);
  set(params, "organization_slug", filters.organizationSlug);
  set(params, "location", filters.location);
  set(params, "work_mode", filters.workMode);
  set(params, "employment_type", filters.employmentType);
  if (cursor) params.set("cursor", cursor);
  const query = params.toString();
  return query ? `/jobs?${query}` : "/jobs";
}

export function organizationWebsiteHref(value: string | null) {
  if (!value) return null;
  try { const url = new URL(value); return url.protocol === "https:" ? url.toString() : null; }
  catch { return null; }
}

export async function listPublicOrganizations(q = "", cursor: string | null = null): Promise<CursorPage<Organization>> {
  const params = new URLSearchParams({ limit: "20" });
  set(params, "q", q);
  if (cursor) params.set("cursor", cursor);
  const raw = record(await apiRequest<unknown>(`/v1/organizations?${params.toString()}`, { server: true }));
  return page(raw, "organizations", parseOrganization);
}

export async function fetchPublicOrganization(slug: string) {
  return parseOrganization(await apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(slug)}`, { server: true }));
}

export async function listPublicJobs(filters: JobSearchFilters): Promise<CursorPage<Job>> {
  const params = new URLSearchParams({ limit: "20" });
  set(params, "q", filters.q);
  set(params, "organization_slug", filters.organizationSlug);
  set(params, "location", filters.location);
  set(params, "work_mode", filters.workMode);
  set(params, "employment_type", filters.employmentType);
  if (filters.cursor) params.set("cursor", filters.cursor);
  const raw = record(await apiRequest<unknown>(`/v1/jobs?${params.toString()}`, { server: true }));
  return page(raw, "jobs", parseJob);
}

export async function fetchPublicJob(organizationSlug: string, jobSlug: string) {
  return parseJob(await apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organizationSlug)}/jobs/${encodeURIComponent(jobSlug)}`, { server: true }));
}

export async function listManageableOrganizations(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null): Promise<CursorPage<ManageableOrganizationSummary>> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  return page(record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/employer/organizations?${params.toString()}`, { token, cache: "no-store" }))), "organizations", parseManageableOrganizationSummary);
}

export async function listManageableJobs(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null): Promise<CursorPage<ManageableJobSummary>> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  return page(record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/employer/jobs?${params.toString()}`, { token, cache: "no-store" }))), "jobs", parseManageableJobSummary);
}

export async function loadOrganizationForOwner(slug: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(slug)}`, { token }));
  return parseOrganization(raw);
}

export async function createOrganization(input: { slug: string; name: string; description: string; websiteUrl: string }, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseOrganization(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>("/v1/organizations", {
    method: "POST", token, headers: jsonHeaders(idempotencyKey),
    body: JSON.stringify({ slug: input.slug, name: input.name, description: nullable(input.description), website_url: nullable(input.websiteUrl), visibility: "private" })
  })));
}

export async function updateOrganization(organization: Organization, input: { name: string; description: string; websiteUrl: string }, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseOrganization(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organization.slug)}`, {
    method: "PUT", token, headers: jsonHeaders(idempotencyKey, organization.etag),
    body: JSON.stringify({ name: input.name, description: nullable(input.description), website_url: nullable(input.websiteUrl) })
  })));
}

export async function submitOrganizationVerification(organizationSlug: string, input: { evidenceKind: VerificationEvidenceKind; metadata: Record<string, string>; artifactContentType: VerificationArtifactContentType; artifactBase64: string }, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseOrganizationVerificationSubmission(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organizationSlug)}/verification-submissions`, {
    method: "POST", token, headers: jsonHeaders(idempotencyKey), body: JSON.stringify({ evidence_kind: input.evidenceKind, metadata: input.metadata, artifact_content_type: input.artifactContentType, artifact_base64: input.artifactBase64 })
  })));
}

export async function getOrganizationVerificationStatus(organizationSlug: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return parseOrganizationVerificationOwnerStatus(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organizationSlug)}/verification-status`, { token })));
}

export async function listReviewerVerifications(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null): Promise<CursorPage<ReviewerVerification>> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  return page(record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/internal/recruiting-verifications?${params.toString()}`, { token, cache: "no-store" }))), "verifications", parseReviewerVerification);
}

export async function decideReviewerVerification(verificationId: string, action: ReviewerVerificationAction, decision: ReviewerVerificationDecision, idempotencyKey: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const reviewEtag = reviewerDecisionEtag(decision.reviewEtag);
  const body = {
    expected_state: decision.expectedState,
    ...(decision.policyVersion ? { policy_version: decision.policyVersion } : {}),
    ...(decision.expiresAt ? { expires_at: decision.expiresAt } : {}),
  };
  return parseReviewerVerification(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/internal/recruiting-verifications/${encodeURIComponent(verificationId)}/${action}`, {
    method: "POST", token, cache: "no-store", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey, "If-Match": reviewEtag }, body: JSON.stringify(body),
  })));
}

export async function inviteOrganizationMember(organizationSlug: string, memberProfileHandle: string, role: "admin" | "member", getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  const raw = record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organizationSlug)}/admins`, {
    method: "POST", token, headers: jsonHeaders(idempotencyKey), body: JSON.stringify({ member_profile_handle: memberProfileHandle.trim().replace(/^@/u, ""), role })
  })));
  return parseInvitation(raw);
}

export async function listOrganizationMembershipInvitations(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null): Promise<CursorPage<OrganizationMembershipInvitation>> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  return page(record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organization-membership-invitations?${params.toString()}`, { token }))), "invitations", parseMembershipInvitation);
}

export async function acceptOrganizationMembership(invitation: OrganizationMembershipInvitation, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  return parseInvitation(record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(invitation.organizationSlug)}/memberships/${encodeURIComponent(invitation.id)}/accept`, { method: "POST", token, headers: jsonHeaders(idempotencyKey) }))));
}

export async function listOrganizationMembers(organizationSlug: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null): Promise<CursorPage<OrganizationInvitation>> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  return page(record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organizationSlug)}/members?${params.toString()}`, { token }))), "members", (value) => parseInvitation(record(value)));
}

export async function removeOrganizationMember(organizationSlug: string, membershipId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey: string) {
  await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organizationSlug)}/memberships/${encodeURIComponent(membershipId)}`, { method: "DELETE", token, headers: jsonHeaders(idempotencyKey, undefined, false) }));
}

export async function loadJobForOwner(organizationSlug: string, jobSlug: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const raw = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organizationSlug)}/jobs/${encodeURIComponent(jobSlug)}`, { token }));
  return parseJob(raw);
}

export async function createJob(organizationSlug: string, input: JobInput, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseJob(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(organizationSlug)}/jobs`, {
    method: "POST", token, headers: jsonHeaders(idempotencyKey), body: JSON.stringify(jobBody(input, true))
  })));
}

export async function updateJob(job: Job, input: JobInput, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseJob(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(job.organizationSlug)}/jobs/${encodeURIComponent(job.slug)}`, {
    method: "PUT", token, headers: jsonHeaders(idempotencyKey, job.etag), body: JSON.stringify(jobBody(input, false))
  })));
}

export async function changeJobLifecycle(job: Job, action: "publish" | "close", getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseJob(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(job.organizationSlug)}/jobs/${encodeURIComponent(job.slug)}/lifecycle/${action}`, {
    method: "POST", token, headers: jsonHeaders(idempotencyKey, job.etag), body: "{}"
  })));
}

export type JobInput = { slug: string; title: string; description: string; location: string; workMode: Job["workMode"]; employmentType: Job["employmentType"] };

export async function listApplicationDocuments(getToken: TokenGetter, isSubjectCurrent: SubjectGuard): Promise<ApplicationDocument[]> {
  const documents: ApplicationDocument[] = [];
  const deliveredCursors = new Set<string>();
  let cursor: string | null = null;
  while (true) {
    const params = new URLSearchParams({ limit: "100" });
    if (cursor) params.set("cursor", cursor);
    const raw = record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/documents?${params.toString()}`, { token })));
    const result = page(raw, "documents", parseApplicationDocument);
    documents.push(...result.items.filter((document): document is ApplicationDocument => document !== null));
    if (result.nextCursor === null) return documents;
    if (result.nextCursor === cursor || deliveredCursors.has(result.nextCursor)) {
      throw new ApiRequestError("The API returned a document cursor that did not advance.", undefined, "server");
    }
    deliveredCursors.add(result.nextCursor);
    cursor = result.nextCursor;
  }
}

export async function submitApplication(job: Job, input: { message: string; snapshotKind: "profile" | "resume"; snapshotIdentifier: string }, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = crypto.randomUUID()) {
  if (!idempotencyKey.trim()) throw new ApiRequestError("An application submission key is required.", 400, "request");
  return parseApplication(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(job.organizationSlug)}/jobs/${encodeURIComponent(job.slug)}/applications`, {
    method: "POST", token, headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ message: input.message, snapshot_kind: input.snapshotKind, snapshot_identifier: input.snapshotIdentifier, human_confirmed: true })
  })));
}

export async function listMyApplications(getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null): Promise<CursorPage<Application>> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  return page(record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/applications?${params.toString()}`, { token }))), "applications", parseApplication);
}

export async function getMyApplicationDetail(id: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return parseApplicationDetail(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/applications/${encodeURIComponent(id)}`, { token })));
}

export async function withdrawApplication(id: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseApplication(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/applications/${encodeURIComponent(id)}/withdraw`, { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: "{}" })));
}

export async function listJobApplications(job: Job, getToken: TokenGetter, isSubjectCurrent: SubjectGuard, cursor: string | null = null): Promise<CursorPage<Application>> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  return page(record(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(job.organizationSlug)}/jobs/${encodeURIComponent(job.slug)}/applications?${params.toString()}`, { token, headers: { "X-Connectmd-Purpose": "job_application_review" } }))), "applications", parseApplication);
}

export async function getEmployerApplicationDetail(job: Job, applicationId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  return parseApplicationDetail(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(job.organizationSlug)}/jobs/${encodeURIComponent(job.slug)}/applications/${encodeURIComponent(applicationId)}`, { token, headers: { "X-Connectmd-Purpose": "job_application_review" } })));
}

export async function getEmployerApplicationSnapshot(job: Job, applicationId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const path = employerApplicationSnapshotPath(job, applicationId);
  const snapshot = parseApplicationSnapshot(
    await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(path, {
      token,
      headers: { "X-Connectmd-Purpose": "job_application_review" },
    })),
    path,
    applicationId,
  );
  return verifyApplicationSnapshot(snapshot);
}

export async function getEmployerApplicationSnapshotMarkdown(job: Job, applicationId: string, getToken: TokenGetter, isSubjectCurrent: SubjectGuard) {
  const markdown = await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`${employerApplicationSnapshotPath(job, applicationId)}.md`, {
    token,
    headers: { Accept: "text/markdown", "X-Connectmd-Purpose": "job_application_review" },
  }));
  if (typeof markdown !== "string" || !markdown) throw new ApiRequestError("The API returned an invalid application snapshot Markdown body.", undefined, "server");
  return markdown;
}

export async function verifyApplicationSnapshot(snapshot: ApplicationSnapshot) {
  const digest = await markdownSha256(snapshot.markdown);
  if (digest !== snapshot.snapshotSha256) throw new ApplicationSnapshotIntegrityError();
  return snapshot;
}

export async function verifyApplicationSnapshotMarkdown(markdown: string, expectedSha256: string) {
  const digest = await markdownSha256(markdown);
  if (digest !== expectedSha256) throw new ApplicationSnapshotIntegrityError();
  return markdown;
}

export function assertApplicationSnapshotMatchesApplication(snapshot: ApplicationSnapshot, application: Pick<Application, "id" | "snapshotKind" | "snapshotIdentifier" | "snapshotVersion" | "snapshotSha256">) {
  if (
    snapshot.applicationId !== application.id ||
    snapshot.snapshotKind !== application.snapshotKind ||
    snapshot.snapshotIdentifier !== application.snapshotIdentifier ||
    snapshot.snapshotVersion !== application.snapshotVersion ||
    snapshot.snapshotSha256 !== application.snapshotSha256
  ) {
    throw new ApiRequestError("The API returned a snapshot that does not match the application summary.", undefined, "server");
  }
  return snapshot;
}

export async function decideApplication(job: Job, applicationId: string, action: "review" | "accept" | "reject", getToken: TokenGetter, isSubjectCurrent: SubjectGuard, idempotencyKey = newIdempotencyKey()) {
  return parseApplication(await withSubjectBoundToken(getToken, isSubjectCurrent, (token) => apiRequest<unknown>(`/v1/organizations/${encodeURIComponent(job.organizationSlug)}/jobs/${encodeURIComponent(job.slug)}/applications/${encodeURIComponent(applicationId)}/${action}`, { method: "POST", token, headers: jsonHeaders(idempotencyKey), body: "{}" })));
}

function page<T>(raw: Record<string, unknown>, key: string, parse: (value: unknown) => T) {
  const values = raw[key];
  if (!Array.isArray(values)) throw new ApiRequestError(`The API returned an invalid ${key} collection.`, undefined, "server");
  const nextCursor = raw.next_cursor;
  if (nextCursor !== null && nextCursor !== undefined && (typeof nextCursor !== "string" || !nextCursor)) throw new ApiRequestError(`The API returned an invalid ${key} cursor.`, undefined, "server");
  return { items: values.map(parse), nextCursor: textOrNull(nextCursor) } satisfies CursorPage<T>;
}

function parseOrganization(value: unknown): Organization {
  const raw = record(value);
  const recruitingVerificationActive = boolean(raw.recruiting_verification_active, "recruiting verification active");
  const recruitingVerificationPurpose = raw.recruiting_verification_purpose === null || raw.recruiting_verification_purpose === undefined ? null : oneOf(raw.recruiting_verification_purpose, ["recruiting_control"], "recruiting verification purpose");
  const recruitingVerificationExpiresAt = textOrNull(raw.recruiting_verification_expires_at);
  if (recruitingVerificationActive !== (recruitingVerificationPurpose === "recruiting_control")) throw new ApiRequestError("The API returned an inconsistent recruiting verification state.", undefined, "server");
  return { id: required(raw.id, "organization id"), slug: required(raw.slug, "organization slug"), name: required(raw.name, "organization name"), description: textOrNull(raw.description), websiteUrl: textOrNull(raw.website_url), visibility: oneOf(raw.visibility, ["public", "private"], "organization visibility"), recruitingVerificationActive, recruitingVerificationPurpose, recruitingVerificationExpiresAt, version: integer(raw.version, "organization version"), createdAt: required(raw.created_at, "organization created time"), updatedAt: required(raw.updated_at, "organization updated time"), etag: required(raw.etag, "organization etag") };
}

function parseManageableOrganizationSummary(value: unknown): ManageableOrganizationSummary {
  const raw = record(value);
  return {
    id: required(raw.id, "manageable organization id"),
    slug: required(raw.slug, "manageable organization slug"),
    name: required(raw.name, "manageable organization name"),
    managementRole: oneOf(raw.management_role, ["owner", "admin"], "manageable organization role"),
    visibility: oneOf(raw.visibility, ["public", "private"], "manageable organization visibility"),
    recruitingVerificationActive: boolean(raw.recruiting_verification_active, "manageable organization recruiting verification"),
    recruitingVerificationPurpose: nullableOneOfLabel(raw.recruiting_verification_purpose, ["recruiting_control"], "manageable organization recruiting verification purpose"),
    recruitingVerificationExpiresAt: nullableText(raw.recruiting_verification_expires_at, "manageable organization recruiting verification expiry"),
    updatedAt: required(raw.updated_at, "manageable organization updated time"),
  };
}

function parseOrganizationVerificationSubmission(value: unknown): OrganizationVerificationSubmission {
  const raw = record(value);
  const artifactSizeBytes = integer(raw.artifact_size_bytes, "verification artifact size");
  if (artifactSizeBytes < 1 || artifactSizeBytes > VERIFICATION_ARTIFACT_MAX_BYTES) throw new ApiRequestError("The API returned an invalid verification artifact size.", undefined, "server");
  return { verificationId: required(raw.verification_id, "verification id"), state: oneOf(raw.state, ["submitted"], "verification state"), evidenceSha256: required(raw.evidence_sha256, "evidence digest"), artifactContentType: oneOf(raw.artifact_content_type, VERIFICATION_ARTIFACT_CONTENT_TYPES, "verification artifact type"), artifactSizeBytes, submittedAt: required(raw.submitted_at, "verification submitted time") };
}

function parseOrganizationVerificationOwnerStatus(value: unknown): OrganizationVerificationOwnerStatus {
  const raw = record(value);
  return { verificationId: nullableRequired(raw.verification_id, "verification id"), state: oneOf(raw.state, VERIFICATION_STATUSES, "verification state"), submittedAt: nullableRequired(raw.submitted_at, "verification submitted time"), updatedAt: nullableRequired(raw.updated_at, "verification updated time"), policyVersion: nullableRequired(raw.policy_version, "verification policy version"), expiresAt: nullableRequired(raw.expires_at, "verification expiry") };
}

function parseReviewerVerification(value: unknown): ReviewerVerification {
  const raw = record(value);
  return { id: required(raw.verification_id, "reviewer verification id"), organizationSlug: required(raw.organization_slug, "reviewer verification organization slug"), organizationName: required(raw.organization_name, "reviewer verification organization name"), state: oneOf(raw.state, VERIFICATION_STATUSES, "reviewer verification state"), submittedAt: required(raw.submitted_at, "reviewer verification submitted time"), updatedAt: required(raw.updated_at, "reviewer verification updated time"), policyVersion: nullableRequired(raw.policy_version, "reviewer verification policy version"), expiresAt: nullableRequired(raw.expires_at, "reviewer verification expiry") };
}

function reviewerDecisionEtag(value: string) {
  if (!/^"sha256-[0-9a-f]{64}"$/u.test(value)) {
    throw new ApiRequestError("A verified current review snapshot is required for this decision.", undefined, "configuration");
  }
  return value;
}

function parseJob(value: unknown): Job {
  const raw = record(value);
  return { id: required(raw.id, "job id"), organizationId: required(raw.organization_id, "job organization id"), organizationSlug: required(raw.organization_slug, "job organization slug"), organizationName: required(raw.organization_name, "job organization name"), slug: required(raw.slug, "job slug"), title: required(raw.title, "job title"), description: required(raw.description, "job description"), location: textOrNull(raw.location), workMode: nullableOneOf(raw.work_mode, ["remote", "hybrid", "onsite"]), employmentType: nullableOneOf(raw.employment_type, ["full_time", "part_time", "contract", "internship", "temporary"]), status: oneOf(raw.status, ["draft", "published", "closed"], "job status"), version: integer(raw.version, "job version"), publishedAt: textOrNull(raw.published_at), createdAt: required(raw.created_at, "job created time"), updatedAt: required(raw.updated_at, "job updated time"), etag: required(raw.etag, "job etag") };
}

function parseManageableJobSummary(value: unknown): ManageableJobSummary {
  const raw = record(value);
  return {
    id: required(raw.id, "manageable job id"),
    organizationId: required(raw.organization_id, "manageable job organization id"),
    organizationSlug: required(raw.organization_slug, "manageable job organization slug"),
    organizationName: required(raw.organization_name, "manageable job organization name"),
    managementRole: oneOf(raw.management_role, ["owner", "admin"], "manageable job role"),
    slug: required(raw.slug, "manageable job slug"),
    title: required(raw.title, "manageable job title"),
    status: oneOf(raw.status, ["draft", "published", "closed"], "manageable job status"),
    location: nullableText(raw.location, "manageable job location"),
    workMode: nullableOneOfLabel(raw.work_mode, ["onsite", "hybrid", "remote"], "manageable job work mode"),
    employmentType: nullableOneOfLabel(raw.employment_type, ["full_time", "part_time", "contract", "internship", "temporary"], "manageable job employment type"),
    updatedAt: required(raw.updated_at, "manageable job updated time"),
  };
}

function parseApplication(value: unknown): Application {
  const raw = record(value);
  return { id: required(raw.id, "application id"), jobId: required(raw.job_id, "application job id"), organizationSlug: required(raw.organization_slug, "application organization slug"), jobSlug: required(raw.job_slug, "application job slug"), status: oneOf(raw.status, ["submitted", "under_review", "accepted", "rejected", "withdrawn"], "application status"), snapshotKind: oneOf(raw.snapshot_kind, ["profile", "resume"], "application snapshot kind"), snapshotIdentifier: required(raw.snapshot_identifier, "application snapshot identifier"), snapshotVersion: integer(raw.snapshot_version, "application snapshot version"), snapshotSha256: required(raw.snapshot_sha256, "application snapshot digest"), confirmedAt: required(raw.confirmed_at, "application confirmation time"), retentionPolicyVersion: required(raw.retention_policy_version, "application retention policy"), retentionExpiresAt: required(raw.retention_expires_at, "application retention expiry"), createdAt: required(raw.created_at, "application created time"), updatedAt: required(raw.updated_at, "application updated time"), decidedAt: textOrNull(raw.decided_at) };
}

function parseApplicationDetail(value: unknown): ApplicationDetail { const raw = record(value); return { ...parseApplication(raw), message: required(raw.message, "application message") }; }
function parseApplicationSnapshot(value: unknown, path: string, applicationId: string): ApplicationSnapshot {
  const raw = record(value);
  const receivedApplicationId = required(raw.application_id, "application snapshot application id");
  if (receivedApplicationId !== applicationId) throw new ApiRequestError("The API returned an application snapshot for a different application.", undefined, "server");
  const markdownUrl = required(raw.markdown_url, "application snapshot Markdown URL");
  if (markdownUrl !== `${path}.md`) throw new ApiRequestError("The API returned an invalid application snapshot Markdown URL.", undefined, "server");
  const snapshotVersion = integer(raw.snapshot_version, "application snapshot version");
  if (snapshotVersion < 1) throw new ApiRequestError("The API returned an invalid application snapshot version.", undefined, "server");
  return {
    applicationId: receivedApplicationId,
    snapshotKind: oneOf(raw.snapshot_kind, ["profile", "resume"], "application snapshot kind"),
    snapshotIdentifier: required(raw.snapshot_identifier, "application snapshot identifier"),
    snapshotVersion,
    snapshotSha256: sha256(raw.snapshot_sha256, "application snapshot digest"),
    markdown: required(raw.markdown, "application snapshot Markdown"),
    markdownUrl,
  };
}
function parseInvitation(value: Record<string, unknown>): OrganizationInvitation { return { id: required(value.id, "membership id"), organizationId: required(value.organization_id, "membership organization id"), memberProfileHandle: nullableRequired(value.member_profile_handle, "membership profile handle"), role: oneOf(value.role, ["admin", "member"], "membership role"), status: oneOf(value.status, ["invited", "active"], "membership status"), createdAt: required(value.created_at, "membership created time") }; }
function parseMembershipInvitation(value: unknown): OrganizationMembershipInvitation { const raw = record(value); return { id: required(raw.id, "membership invitation id"), organizationId: required(raw.organization_id, "membership invitation organization id"), organizationSlug: required(raw.organization_slug, "membership invitation organization slug"), organizationName: required(raw.organization_name, "membership invitation organization name"), role: oneOf(raw.role, ["admin", "member"], "membership invitation role"), status: oneOf(raw.status, ["invited"], "membership invitation status"), createdAt: required(raw.created_at, "membership invitation creation time") }; }
function parseApplicationDocument(value: unknown): ApplicationDocument | null { const raw = record(value); const kind = raw.kind; const visibility = raw.visibility; if ((kind !== "profile" && kind !== "resume") || (visibility !== "public" && visibility !== "private")) return null; return { id: required(raw.id, "document id"), kind, identifier: required(raw.identifier, "document identifier"), version: integer(raw.version, "document version"), visibility }; }
function jobBody(input: JobInput, create: boolean) { const body = { title: input.title, description: input.description, location: nullable(input.location), work_mode: input.workMode, employment_type: input.employmentType }; return create ? { slug: input.slug, ...body } : body; }
function jsonHeaders(idempotencyKey = newIdempotencyKey(), etag?: string, withJson = true) { return { ...(withJson ? { "Content-Type": "application/json" } : {}), "Idempotency-Key": idempotencyKey, ...(etag ? { "If-Match": etag } : {}) }; }
function record(value: unknown): Record<string, unknown> { if (typeof value !== "object" || value === null || Array.isArray(value)) throw new ApiRequestError("The API returned an invalid response.", undefined, "server"); return value as Record<string, unknown>; }
function required(value: unknown, label: string) { if (typeof value !== "string" || !value) throw new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); return value; }
function nullableRequired(value: unknown, label: string) { return value === null ? null : required(value, label); }
function integer(value: unknown, label: string) { if (typeof value !== "number" || !Number.isInteger(value)) throw new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); return value; }
function sha256(value: unknown, label: string) { const digest = required(value, label); if (!/^[0-9a-f]{64}$/u.test(digest)) throw new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); return digest; }
function nullableText(value: unknown, label: string) { if (value === null) return null; if (typeof value !== "string") throw new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); return value; }
function nullableOneOfLabel<const T extends readonly string[]>(value: unknown, options: T, label: string): T[number] | null { if (value === null) return null; return oneOf(value, options, label); }
function employerApplicationSnapshotPath(job: Pick<Job, "organizationSlug" | "slug">, applicationId: string) { return `/v1/organizations/${encodeURIComponent(job.organizationSlug)}/jobs/${encodeURIComponent(job.slug)}/applications/${encodeURIComponent(applicationId)}/snapshot`; }
async function markdownSha256(markdown: string) {
  if (!globalThis.crypto?.subtle) throw new ApplicationSnapshotIntegrityError("This browser cannot verify an immutable application snapshot.");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(markdown));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
function oneOf<const T extends readonly string[]>(value: unknown, options: T, label: string): T[number] { if (typeof value !== "string" || !(options as readonly string[]).includes(value)) throw new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); return value as T[number]; }
function nullableOneOf<const T extends readonly string[]>(value: unknown, options: T): T[number] | null { if (value === null || value === undefined) return null; return oneOf(value, options, "value"); }
function boolean(value: unknown, label: string) { if (typeof value !== "boolean") throw new ApiRequestError(`The API returned an invalid ${label}.`, undefined, "server"); return value; }
function text(value: unknown) { return typeof value === "string" ? value.trim() : ""; }
function textOrNull(value: unknown) { const cleaned = text(value); return cleaned || null; }
function nullable(value: string) { const cleaned = value.trim(); return cleaned || null; }
function set(params: URLSearchParams, key: string, value: string) { if (value.trim()) params.set(key, value.trim()); }
