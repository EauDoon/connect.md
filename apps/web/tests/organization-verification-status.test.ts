import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { OWNER_VERIFICATION_STATUS_DENIED_MESSAGE, organizationVerificationRequestIsCurrent, presentOwnerVerificationStatusError } from "../components/organization-verification-status";
import { ApiRequestError } from "../lib/api";
function source(relative: string) { return readFileSync(new URL(relative, import.meta.url), "utf8"); }

describe("owner verification status", () => {
  it("keeps status truthful, subject-bound, and free of evidence content", () => {
    const value = source("../components/organization-verification-status.tsx");
    expect(value).toContain('type LoadState = "loading" | "loaded" | "error"');
    expect(value).toContain("getOrganizationVerificationStatus(organization.slug, getToken");
    expect(value).toContain("isSubjectCurrent(subject)");
    expect(value).toContain("const next = await getOrganizationVerificationStatus");
    expect(value).toContain("const requestId = requestRef.current + 1;");
    expect(value).toContain("() => stillCurrent(requestId)");
    expect(value).toContain("if (!stillCurrent(requestId)) return;");
    expect(value).toContain('setLoadState("loaded")');
    expect(value).toContain('setLoadState("error")');
    expect(value).toContain("Owner-private service status only");
    expect(value).not.toContain("artifact_base64");
    expect(value).not.toContain("metadata_json");
    expect(value).not.toContain("storage_path");
    expect(value).not.toContain("owner_id");
  });

  it("allows only the latest mounted exact-subject status request to settle", () => {
    expect(organizationVerificationRequestIsCurrent(2, 2, true, true)).toBe(true);
    expect(organizationVerificationRequestIsCurrent(1, 2, true, true)).toBe(false);
    expect(organizationVerificationRequestIsCurrent(2, 2, false, true)).toBe(false);
    expect(organizationVerificationRequestIsCurrent(2, 2, true, false)).toBe(false);
  });

  it("refreshes owner status only after a successful bounded submission", () => {
    const employer = source("../components/employer-workspace.tsx");
    const submission = source("../components/organization-verification-submission.tsx");
    expect(employer).toContain("verificationStatusRevision");
    expect(employer).toContain("<OrganizationVerificationStatusCard");
    expect(employer).toContain("onSubmitted={() => setVerificationStatusRevision");
    expect(submission).toContain("onSubmitted();");
    expect(submission).toContain("No verification decision has been made.");
  });

  it("clears selected evidence only after a successful response", () => {
    const submission = source("../components/organization-verification-submission.tsx");
    const submitStart = submission.indexOf("const submit =");
    const finallyStart = submission.indexOf("} finally {", submitStart);
    const finallyEnd = submission.indexOf("    }\n  };", finallyStart);
    expect(submitStart).toBeGreaterThan(-1);
    expect(finallyStart).toBeGreaterThan(submitStart);
    expect(finallyEnd).toBeGreaterThan(finallyStart);
    const finallyBlock = submission.slice(finallyStart, finallyEnd);

    expect(submission).toContain("let submissionSucceeded = false;");
    expect(submission).toContain("submissionSucceeded = true;");
    expect(finallyBlock).toContain("if (submissionSucceeded) clearEvidence();");
    expect(finallyBlock.match(/clearEvidence\(\)/gu)).toHaveLength(1);
    expect(finallyBlock).not.toMatch(/^\s*clearEvidence\(\);/mu);
    expect(submission).toContain("cleared after a successful submission or when this form changes scope");
  });

  it("maps a 403 API error to a generic owner-status denial", () => {
    expect(presentOwnerVerificationStatusError(new ApiRequestError("configured owner details", 403, "unauthorized"))).toBe(OWNER_VERIFICATION_STATUS_DENIED_MESSAGE);
    expect(OWNER_VERIFICATION_STATUS_DENIED_MESSAGE).not.toContain("configured owner details");
  });
});
