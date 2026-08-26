import { afterEach, describe, expect, it, vi } from "vitest";

import { APPLICATION_MESSAGE_MAX_LENGTH, acceptOrganizationMembership, appendCursorPage, assertApplicationSnapshotMatchesApplication, authSubjectIsCurrent, changeJobLifecycle, decideReviewerVerification, getEmployerApplicationDetail, getEmployerApplicationSnapshot, getEmployerApplicationSnapshotMarkdown, getMyApplicationDetail, getOrganizationVerificationStatus, inviteOrganizationMember, listApplicationDocuments, listJobApplications, listManageableJobs, listManageableOrganizations, listMyApplications, listOrganizationMembers, listOrganizationMembershipInvitations, listReviewerVerifications, loadJobForOwner, loadOrganizationForOwner, organizationWebsiteHref, removeOrganizationMember, submitApplication, submitOrganizationVerification, type Job, type OrganizationMembershipInvitation } from "../lib/recruitment-api";

const job: Job = { id: "job_1", organizationId: "org_1", organizationSlug: "acme", organizationName: "Acme", slug: "product-lead", title: "Product lead", description: "Build products.", location: "Singapore", workMode: "hybrid", employmentType: "full_time", status: "draft", version: 1, publishedAt: null, createdAt: "2026-08-03T00:00:00Z", updatedAt: "2026-08-03T00:00:00Z", etag: "\"job-v1\"" };
const reviewEtag = `"sha256-${"c".repeat(64)}"`;
const application = { id: "app_1", job_id: "job_1", organization_slug: "acme", job_slug: "product-lead", status: "submitted", snapshot_kind: "profile", snapshot_identifier: "ari", snapshot_version: 3, snapshot_sha256: "a".repeat(64), confirmed_at: "2026-08-03T00:00:00Z", retention_policy_version: "v1", retention_expires_at: "2027-08-03T00:00:00Z", created_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z", decided_at: null };
const jobResponse = { id: "job_1", organization_id: "org_1", organization_slug: "acme", organization_name: "Acme", slug: "product-lead", title: "Product lead", description: "Build products.", location: "Singapore", work_mode: "hybrid", employment_type: "full_time", status: "published", version: 2, published_at: "2026-08-03T00:00:00Z", created_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:00Z", etag: "\"job-v2\"" };

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

function configure(response: unknown) {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
  vi.stubGlobal("crypto", { randomUUID: () => "request-1", subtle: globalThis.crypto?.subtle });
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(response), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("recruitment API trust boundaries", () => {
  it("uses the lifecycle route and an idempotency key", async () => {
    const fetchMock = configure(jobResponse);
    await changeJobLifecycle(job, "publish", async () => "clerk-token", () => true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/organizations/acme/jobs/product-lead/lifecycle/publish");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("request-1");
  });

  it("submits only the selected snapshot with human confirmation and an idempotency key", async () => {
    const fetchMock = configure(application);
    await submitApplication(job, { message: "I can help.", snapshotKind: "profile", snapshotIdentifier: "ari" }, async () => "clerk-token", () => true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/organizations/acme/jobs/product-lead/applications");
    expect(JSON.parse(String(init.body))).toEqual({ message: "I can help.", snapshot_kind: "profile", snapshot_identifier: "ari", human_confirmed: true });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("request-1");
  });

  it("submits bounded private recruiting-control evidence with an idempotency key and returns only a receipt", async () => {
    const fetchMock = configure({ verification_id: "verification-1", state: "submitted", evidence_sha256: "a".repeat(64), artifact_content_type: "text/plain", artifact_size_bytes: 24, submitted_at: "2026-08-03T00:00:00Z" });
    const submitted = await submitOrganizationVerification("acme", { evidenceKind: "corporate_registration", metadata: { jurisdiction: "SG" }, artifactContentType: "text/plain", artifactBase64: "cHJpdmF0ZSBldmlkZW5jZQ==" }, async () => "clerk-token", () => true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/organizations/acme/verification-submissions");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("request-1");
    expect(JSON.parse(String(init.body))).toEqual({ evidence_kind: "corporate_registration", metadata: { jurisdiction: "SG" }, artifact_content_type: "text/plain", artifact_base64: "cHJpdmF0ZSBldmlkZW5jZQ==" });
    expect(submitted).toEqual({ verificationId: "verification-1", state: "submitted", evidenceSha256: "a".repeat(64), artifactContentType: "text/plain", artifactSizeBytes: 24, submittedAt: "2026-08-03T00:00:00Z" });
    expect(submitted).not.toHaveProperty("artifactBase64");
    expect(submitted).not.toHaveProperty("metadata");
  });

  it("loads owner-private verification status through a subject-bound read without evidence fields", async () => {
    const fetchMock = configure({ verification_id: "verification-1", state: "under_review", submitted_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-04T00:00:00Z", policy_version: null, expires_at: null });
    const status = await getOrganizationVerificationStatus("acme", async () => "clerk-token", () => true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/organizations/acme/verification-status");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer clerk-token");
    expect(status).toEqual({ verificationId: "verification-1", state: "under_review", submittedAt: "2026-08-03T00:00:00Z", updatedAt: "2026-08-04T00:00:00Z", policyVersion: null, expiresAt: null });
    expect(status).not.toHaveProperty("metadata");
    expect(status).not.toHaveProperty("artifactBase64");
  });

  it("keeps queue summaries digest-free and binds decisions to the verified review ETag", async () => {
    const reviewer = { verification_id: "verification-1", organization_slug: "acme", organization_name: "Acme", state: "under_review", evidence_kind: "corporate_registration", evidence_sha256: "a".repeat(64), artifact_content_type: "text/plain", artifact_size_bytes: 24, material_claim_digest: "b".repeat(64), submitted_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-04T00:00:00Z", policy_version: null, expires_at: null };
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ verifications: [reviewer], next_cursor: "reviewer-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...reviewer, state: "active", policy_version: "recruiting-control-v1", expires_at: "2026-10-04T00:00:00Z" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const page = await listReviewerVerifications(async () => "clerk-token", () => true, "reviewer-cursor");
    const updated = await decideReviewerVerification("verification-1", "activate", { expectedState: "under_review", reviewEtag, policyVersion: "recruiting-control-v1", expiresAt: "2026-10-04T00:00:00Z" }, "review-decision-1", async () => "clerk-token", () => true);
    expect(page.nextCursor).toBe("reviewer-next");
    expect(page.items[0]).toMatchObject({ id: "verification-1", organizationName: "Acme" });
    expect(page.items[0]).not.toHaveProperty("materialClaimDigest");
    expect(page.items[0]).not.toHaveProperty("evidenceSha256");
    expect(page.items[0]).not.toHaveProperty("evidenceKind");
    expect(page.items[0]).not.toHaveProperty("artifactContentType");
    expect(page.items[0]).not.toHaveProperty("artifactSizeBytes");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/v1/internal/recruiting-verifications?limit=25&cursor=reviewer-cursor");
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/internal/recruiting-verifications/verification-1/activate");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("review-decision-1");
    expect(new Headers(init.headers).get("If-Match")).toBe(reviewEtag);
    expect(JSON.parse(String(init.body))).toEqual({ expected_state: "under_review", policy_version: "recruiting-control-v1", expires_at: "2026-10-04T00:00:00Z" });
    expect(String(init.body)).not.toContain("material_claim_digest");
    expect(updated).toMatchObject({ id: "verification-1", state: "active" });
    expect(updated).not.toHaveProperty("evidenceSha256");
    expect(updated).not.toHaveProperty("evidenceKind");
    expect(updated).not.toHaveProperty("artifactContentType");
    expect(updated).not.toHaveProperty("artifactSizeBytes");
    expect(updated).not.toHaveProperty("metadata");
    expect(updated).not.toHaveProperty("artifactBase64");
  });

  it("rejects a non-strong review validator before token retrieval or dispatch", async () => {
    const fetchMock = configure({});
    const getToken = vi.fn(async () => "clerk-token");
    await expect(
      decideReviewerVerification(
        "verification-1",
        "review",
        { expectedState: "submitted", reviewEtag: "*" },
        "review-decision-invalid-etag",
        getToken,
        () => true,
      ),
    ).rejects.toMatchObject({ code: "configuration" });
    expect(getToken).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not dispatch a recruitment mutation after the signed-in subject changes during token retrieval", async () => {
    const fetchMock = configure(jobResponse);
    let current = true;
    await expect(changeJobLifecycle(job, "publish", async () => { current = false; return "clerk-token-for-a-different-user"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not use public organization or job responses as private employer state without a bearer token", async () => {
    const fetchMock = configure({ ...jobResponse, visibility: "public" });

    for (const token of [null, "", "   "] as const) {
      await expect(loadOrganizationForOwner("acme", async () => token, () => true)).rejects.toMatchObject({ status: 401, code: "unauthorized" });
      await expect(loadJobForOwner("acme", "product-lead", async () => token, () => true)).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not dispatch owner status, reviewer queue, or reviewer decisions after a subject switch", async () => {
    const fetchMock = configure({ verifications: [], next_cursor: null });
    const switchedToken = async () => "clerk-token-for-a-different-user";
    await expect(getOrganizationVerificationStatus("acme", async () => { throw new Error("not reached"); }, () => false)).rejects.toMatchObject({ code: "unauthorized" });
    await expect(listReviewerVerifications(switchedToken, () => false)).rejects.toMatchObject({ code: "unauthorized" });
    await expect(decideReviewerVerification("verification-1", "review", { expectedState: "submitted", reviewEtag }, "review-decision-1", switchedToken, () => false)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("maps owner-status and reviewer-queue 403 responses to unauthorized API errors", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "configured owner details" }), { status: 403, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "configured reviewer details" }), { status: 403, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getOrganizationVerificationStatus("acme", async () => "clerk-token", () => true)).rejects.toMatchObject({ status: 403, code: "unauthorized" });
    await expect(listReviewerVerifications(async () => "clerk-token", () => true)).rejects.toMatchObject({ status: 403, code: "unauthorized" });
  });

  it("does not dispatch the application document inventory after the signed-in subject changes during token retrieval", async () => {
    const fetchMock = configure({ documents: [] });
    let current = true;

    await expect(
      listApplicationDocuments(
        async () => {
          current = false;
          return "clerk-token-for-a-different-user";
        },
        () => current,
      ),
    ).rejects.toMatchObject({ code: "unauthorized" });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads every application-document page before returning the inventory", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const privateDocument = { id: "resume-private", kind: "resume", identifier: "private-resume", version: 1, visibility: "private" };
    const publicDocument = { id: "profile-public", kind: "profile", identifier: "public-profile", version: 3, visibility: "public" };
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ documents: [privateDocument], next_cursor: "documents-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ documents: [publicDocument], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const getToken = vi.fn(async () => "clerk-token");

    await expect(listApplicationDocuments(getToken, () => true)).resolves.toEqual([privateDocument, publicDocument]);
    expect(getToken).toHaveBeenCalledTimes(2);
    expect(new URL(String(fetchMock.mock.calls[0]?.[0])).searchParams.get("cursor")).toBeNull();
    expect(new URL(String(fetchMock.mock.calls[1]?.[0])).searchParams.get("cursor")).toBe("documents-next");
  });

  it("does not fetch the next document page after the signed-in subject changes", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    let current = true;
    const fetchMock = vi.fn<typeof fetch>().mockImplementationOnce(async () => {
      current = false;
      return new Response(JSON.stringify({ documents: [], next_cursor: "documents-next" }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const getToken = vi.fn(async () => "clerk-token");

    await expect(listApplicationDocuments(getToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    expect(getToken).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed and non-progressing application-document cursors", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const malformedFetch = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ documents: [], next_cursor: "" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", malformedFetch);
    await expect(listApplicationDocuments(async () => "clerk-token", () => true)).rejects.toMatchObject({ code: "server" });

    const stuckFetch = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ documents: [], next_cursor: "stuck" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ documents: [], next_cursor: "stuck" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", stuckFetch);
    await expect(listApplicationDocuments(async () => "clerk-token", () => true)).rejects.toMatchObject({ code: "server" });
    expect(stuckFetch).toHaveBeenCalledTimes(2);
  });

  it("does not dispatch private application reads after the signed-in subject changes during token retrieval", async () => {
    const fetchMock = configure({ applications: [], next_cursor: null });
    const operations = [
      (getToken: () => Promise<string>, guard: () => boolean) => listMyApplications(getToken, guard),
      (getToken: () => Promise<string>, guard: () => boolean) => getMyApplicationDetail("app_1", getToken, guard),
      (getToken: () => Promise<string>, guard: () => boolean) => listJobApplications(job, getToken, guard),
      (getToken: () => Promise<string>, guard: () => boolean) => getEmployerApplicationDetail(job, "app_1", getToken, guard),
      (getToken: () => Promise<string>, guard: () => boolean) => getEmployerApplicationSnapshot(job, "app_1", getToken, guard),
      (getToken: () => Promise<string>, guard: () => boolean) => getEmployerApplicationSnapshotMarkdown(job, "app_1", getToken, guard),
    ];

    for (const operation of operations) {
      let current = true;
      const getToken = async () => {
        current = false;
        return "clerk-token-for-a-different-user";
      };
      await expect(operation(getToken, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the exact purpose header before an employer application-note read", async () => {
    const fetchMock = configure({ ...application, message: "Private candidate note" });
    await getEmployerApplicationDetail(job, "app_1", async () => "clerk-token", () => true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/organizations/acme/jobs/product-lead/applications/app_1");
    expect(new Headers(init.headers).get("X-Connectmd-Purpose")).toBe("job_application_review");
  });

  it("loads an employer application snapshot only through the purpose-bound route", async () => {
    const markdown = "---\nkind: profile\n---\n# Ada";
    const fetchMock = configure({ application_id: "app_1", snapshot_kind: "profile", snapshot_identifier: "ada", snapshot_version: 3, snapshot_sha256: "741d5b5987d1045eb8ba0a1ed7f948ca59900bc19a40bd8c84b62a5780e4e8ac", markdown, markdown_url: "/v1/organizations/acme/jobs/product-lead/applications/app_1/snapshot.md" });

    await expect(getEmployerApplicationSnapshot(job, "app_1", async () => "clerk-token", () => true)).resolves.toMatchObject({ applicationId: "app_1", markdown });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/organizations/acme/jobs/product-lead/applications/app_1/snapshot");
    expect(new Headers(init.headers).get("X-Connectmd-Purpose")).toBe("job_application_review");
  });

  it("rejects a tampered employer application snapshot before it reaches the workspace", async () => {
    const markdown = "---\nkind: profile\n---\n# Ada";
    configure({ application_id: "app_1", snapshot_kind: "profile", snapshot_identifier: "ada", snapshot_version: 3, snapshot_sha256: "b".repeat(64), markdown, markdown_url: "/v1/organizations/acme/jobs/product-lead/applications/app_1/snapshot.md" });
    await expect(getEmployerApplicationSnapshot(job, "app_1", async () => "clerk-token", () => true)).rejects.toMatchObject({ name: "ApplicationSnapshotIntegrityError" });
  });

  it("fails closed when an employer application snapshot is not bound to its requested route", async () => {
    const fetchMock = configure({ application_id: "other", snapshot_kind: "profile", snapshot_identifier: "ada", snapshot_version: 3, snapshot_sha256: "a".repeat(64), markdown: "# Ada", markdown_url: "/v1/organizations/acme/jobs/product-lead/applications/app_1/snapshot.md" });
    await expect(getEmployerApplicationSnapshot(job, "app_1", async () => "clerk-token", () => true)).rejects.toMatchObject({ code: "server" });

    const markdownFetch = configure("# Ada");
    await expect(getEmployerApplicationSnapshotMarkdown(job, "app_1", async () => "clerk-token", () => true)).resolves.toBe("# Ada");
    const [url, init] = markdownFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.connect.test/v1/organizations/acme/jobs/product-lead/applications/app_1/snapshot.md");
    expect(new Headers(init.headers).get("Accept")).toBe("text/markdown");
    expect(new Headers(init.headers).get("X-Connectmd-Purpose")).toBe("job_application_review");
  });

  it("rejects a verified snapshot when its immutable metadata differs from the loaded application summary", () => {
    expect(() => assertApplicationSnapshotMatchesApplication(
      { applicationId: "app_1", snapshotKind: "profile", snapshotIdentifier: "ada", snapshotVersion: 3, snapshotSha256: "a".repeat(64), markdown: "# Ada", markdownUrl: "/v1/organizations/acme/jobs/product-lead/applications/app_1/snapshot.md" },
      { id: "app_1", snapshotKind: "profile", snapshotIdentifier: "ada", snapshotVersion: 2, snapshotSha256: "a".repeat(64) },
    )).toThrow("does not match the application summary");
  });

  it("keeps the application message bound to the API maximum, rejects unsafe websites, and guards account changes", () => {
    expect(APPLICATION_MESSAGE_MAX_LENGTH).toBe(2000);
    expect(organizationWebsiteHref("https://example.com/careers")).toBe("https://example.com/careers");
    expect(organizationWebsiteHref("javascript:alert(1)")).toBeNull();
    expect(organizationWebsiteHref("http://example.com")).toBeNull();
    expect(authSubjectIsCurrent("user_b", "user_a")).toBe(false);
    expect(authSubjectIsCurrent("user_a", "user_a")).toBe(true);
  });

  it("continues candidate and employer application queues with opaque cursors without repeating records", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ applications: [application], next_cursor: "candidate-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ applications: [application], next_cursor: "employer-next" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await listMyApplications(async () => "clerk-token", () => true, "candidate-cursor");
    await listJobApplications(job, async () => "clerk-token", () => true, "employer-cursor");

    expect(new URL(String(fetchMock.mock.calls[0]?.[0])).searchParams.get("cursor")).toBe("candidate-cursor");
    expect(new URL(String(fetchMock.mock.calls[1]?.[0])).searchParams.get("cursor")).toBe("employer-cursor");
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get("X-Connectmd-Purpose")).toBe("job_application_review");
    expect(appendCursorPage([{ id: "known" }], { items: [{ id: "known" }, { id: "older" }], nextCursor: "delivered-cursor" }, "current-cursor", new Set(["delivered-cursor"]))).toEqual({ items: [{ id: "known" }, { id: "older" }], nextCursor: null, cursorDidNotProgress: true });
  });

  it("lists, accepts, and removes human-only organization memberships through exact private routes", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const invitationRaw = { id: "membership-1", organization_id: "org-1", organization_slug: "acme", organization_name: "Acme", role: "admin", status: "invited", created_at: "2026-08-03T00:00:00Z" };
    const invitedMemberRaw = { id: "membership-1", organization_id: "org-1", member_profile_handle: "profile-one", role: "admin", status: "invited", created_at: "2026-08-03T00:00:00Z" };
    const memberRaw = { ...invitedMemberRaw, status: "active" };
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(invitedMemberRaw), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ invitations: [invitationRaw], next_cursor: "invite-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ members: [memberRaw], next_cursor: "member-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(memberRaw), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";
    const current = () => true;

    await inviteOrganizationMember("acme", "@profile-one", "admin", token, current, "membership-invite-0001");
    const invitationPage = await listOrganizationMembershipInvitations(token, current, "invite-cursor");
    const invitation = invitationPage.items[0] as OrganizationMembershipInvitation;
    const memberPage = await listOrganizationMembers("acme", token, current, "member-cursor");
    await acceptOrganizationMembership(invitation, token, current, "membership-accept-0001");
    await removeOrganizationMember("acme", "membership-1", token, current, "membership-remove-0001");

    expect(invitationPage).toEqual({ items: [{ id: "membership-1", organizationId: "org-1", organizationSlug: "acme", organizationName: "Acme", role: "admin", status: "invited", createdAt: "2026-08-03T00:00:00Z" }], nextCursor: "invite-next" });
    expect(memberPage.items[0]).toEqual({ id: "membership-1", organizationId: "org-1", memberProfileHandle: "profile-one", role: "admin", status: "active", createdAt: "2026-08-03T00:00:00Z" });
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({ member_profile_handle: "profile-one", role: "admin" });
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Idempotency-Key")).toBe("membership-invite-0001");
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("/v1/organization-membership-invitations?limit=25&cursor=invite-cursor");
    expect(String(fetchMock.mock.calls[2]?.[0])).toContain("/v1/organizations/acme/members?limit=25&cursor=member-cursor");
    expect(String(fetchMock.mock.calls[3]?.[0])).toContain("/v1/organizations/acme/memberships/membership-1/accept");
    expect(fetchMock.mock.calls[3]?.[1]?.method).toBe("POST");
    expect(new Headers(fetchMock.mock.calls[3]?.[1]?.headers).get("Idempotency-Key")).toBe("membership-accept-0001");
    expect(String(fetchMock.mock.calls[4]?.[0])).toContain("/v1/organizations/acme/memberships/membership-1");
    expect(fetchMock.mock.calls[4]?.[1]?.method).toBe("DELETE");
    expect(new Headers(fetchMock.mock.calls[4]?.[1]?.headers).get("Idempotency-Key")).toBe("membership-remove-0001");
  });

  it("does not dispatch private membership reads after the authenticated subject changes", async () => {
    const fetchMock = configure({ invitations: [], next_cursor: null });
    let current = true;
    await expect(listOrganizationMembershipInvitations(async () => { current = false; return "other-user-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    await expect(listOrganizationMembers("acme", async () => "other-user-token", () => false)).rejects.toMatchObject({ code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed when a private membership collection is missing", async () => {
    configure({ next_cursor: null });
    await expect(listOrganizationMembershipInvitations(async () => "clerk-token", () => true)).rejects.toMatchObject({ code: "server" });
  });

  it("accepts an explicit legacy-null profile handle but rejects an omitted field", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const base = { id: "membership-legacy", organization_id: "org-1", role: "member", status: "active", created_at: "2026-08-03T00:00:00Z" };
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ members: [{ ...base, member_profile_handle: null }], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ members: [base], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const token = async () => "clerk-token";
    const current = () => true;

    await expect(listOrganizationMembers("acme", token, current)).resolves.toMatchObject({ items: [{ memberProfileHandle: null }] });
    await expect(listOrganizationMembers("acme", token, current)).rejects.toMatchObject({ code: "server" });
  });

  it("lists strict human-managed organization and job summaries through guarded private routes", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test");
    const organization = { id: "org-1", slug: "acme", name: "Acme", management_role: "owner", visibility: "private", recruiting_verification_active: true, recruiting_verification_purpose: "recruiting_control", recruiting_verification_expires_at: "2026-10-01T00:00:00Z", updated_at: "2026-08-04T00:00:00Z", description: "must not cross the summary boundary", owner_id: "private-owner", etag: "etag-org", version: 4 };
    const job = { id: "job-1", organization_id: "org-1", organization_slug: "acme", organization_name: "Acme", management_role: "admin", slug: "product-lead", title: "Product lead", status: "published", location: "Singapore", work_mode: "onsite", employment_type: "full_time", updated_at: "2026-08-04T00:00:00Z", description: "must not cross the summary boundary", etag: "etag-job", version: 2, application_count: 4, snapshot: "private" };
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ organizations: [organization], next_cursor: "org-next" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ jobs: [job], next_cursor: "job-next" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const organizations = await listManageableOrganizations(async () => "clerk-token", () => true, "org-cursor");
    const jobs = await listManageableJobs(async () => "clerk-token", () => true, "job-cursor");

    expect(organizations).toEqual({ items: [{ id: "org-1", slug: "acme", name: "Acme", managementRole: "owner", visibility: "private", recruitingVerificationActive: true, recruitingVerificationPurpose: "recruiting_control", recruitingVerificationExpiresAt: "2026-10-01T00:00:00Z", updatedAt: "2026-08-04T00:00:00Z" }], nextCursor: "org-next" });
    expect(jobs).toEqual({ items: [{ id: "job-1", organizationId: "org-1", organizationSlug: "acme", organizationName: "Acme", managementRole: "admin", slug: "product-lead", title: "Product lead", status: "published", location: "Singapore", workMode: "onsite", employmentType: "full_time", updatedAt: "2026-08-04T00:00:00Z" }], nextCursor: "job-next" });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("https://api.connect.test/v1/employer/organizations?limit=25&cursor=org-cursor");
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe("https://api.connect.test/v1/employer/jobs?limit=25&cursor=job-cursor");
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer clerk-token");
    expect(fetchMock.mock.calls[0]?.[1]?.cache).toBe("no-store");
    expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty("server");
    expect(organizations.items[0]).not.toHaveProperty("ownerId");
    expect(organizations.items[0]).not.toHaveProperty("description");
    expect(jobs.items[0]).not.toHaveProperty("applicationCount");
    expect(jobs.items[0]).not.toHaveProperty("snapshot");
  });

  it("fails closed on invalid managed summary fields and never dispatches after a subject switch", async () => {
    const invalid = configure({ organizations: [{ id: "org-1", slug: "acme", name: "Acme", management_role: "member", visibility: "private", recruiting_verification_active: false, recruiting_verification_purpose: null, recruiting_verification_expires_at: null, updated_at: "2026-08-04T00:00:00Z" }], next_cursor: null });
    await expect(listManageableOrganizations(async () => "clerk-token", () => true)).rejects.toMatchObject({ code: "server" });
    expect(invalid).toHaveBeenCalledTimes(1);

    const invalidJob = configure({ jobs: [{ id: "job-1", organization_id: "org-1", organization_slug: "acme", organization_name: "Acme", management_role: "admin", slug: "role", title: "Role", status: "draft", location: null, work_mode: "on_site", employment_type: "unknown", updated_at: "2026-08-04T00:00:00Z" }], next_cursor: null });
    await expect(listManageableJobs(async () => "clerk-token", () => true)).rejects.toMatchObject({ code: "server" });
    expect(invalidJob).toHaveBeenCalledTimes(1);

    const stale = configure({ organizations: [], next_cursor: null });
    let current = true;
    await expect(listManageableOrganizations(async () => { current = false; return "stale-token"; }, () => current)).rejects.toMatchObject({ code: "unauthorized" });
    await expect(listManageableJobs(async () => "stale-token", () => false)).rejects.toMatchObject({ code: "unauthorized" });
    expect(stale).not.toHaveBeenCalled();
  });
});
