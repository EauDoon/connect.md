import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { employerJobWorkspaceKey } from "../components/employer-workspace";

describe("private workspace account isolation", () => {
  it("remounts candidate private state for every authenticated subject", () => {
    const source = readFileSync(new URL("../components/candidate-applications.tsx", import.meta.url), "utf8");
    expect(source).toContain("<AuthenticatedCandidateApplications key={subject}");
    expect(source.indexOf("function AuthenticatedCandidateApplications")).toBeLessThan(source.indexOf("const [applications, setApplications]"));
    expect(source).not.toContain("useEffect(() => { setApplications([])");
    expect(source).toContain("isSubjectCurrent(requestSubject)");
  });

  it("remounts employer private state for every authenticated subject", () => {
    const workspace = readFileSync(new URL("../components/employer-workspace.tsx", import.meta.url), "utf8");
    const panels = readFileSync(new URL("../components/employer-inventory-panels.tsx", import.meta.url), "utf8");
    const management = readFileSync(new URL("../components/employer-organization-management.tsx", import.meta.url), "utf8");
    expect(workspace).toContain("<AuthenticatedEmployerWorkspace key={subject}");
    expect(workspace.indexOf("function AuthenticatedEmployerWorkspace")).toBeLessThan(workspace.indexOf("const [organization, setOrganization]"));
    expect(workspace.indexOf("function AuthenticatedEmployerWorkspace")).toBeLessThan(workspace.indexOf("const [invitations, setInvitations]"));
    expect(workspace).not.toContain("useEffect(() => { setOrganization(null)");
    expect(workspace).toContain("subjectRef={subjectRef}");
    expect(workspace).toContain("listOrganizationMembershipInvitations(getToken, () => authSubjectIsCurrent");
    expect(workspace).toContain("listOrganizationMembers(organization.slug, getToken, () => authSubjectIsCurrent");
    expect(workspace).toContain("Remove ${member.memberProfileHandle ? `@${member.memberProfileHandle}` : \"this member\"} from ${organization.name}?");
    expect(workspace).toContain('from "@/components/employer-organization-management"');
    expect(management).toContain("Recipient public profile handle");
    expect(management).not.toContain("Recipient owner ID");
    expect(workspace).toContain("invitationsLoadedRef");
    expect(workspace).toContain("Private organization workspace");
    expect(workspace).toContain("<ManageableOrganizationInventory");
    expect(workspace).toContain("<ManageableJobInventory");
    expect(workspace).toContain("listManageableOrganizations(getToken, () => authSubjectIsCurrent");
    expect(workspace).toContain("listManageableJobs(getToken, () => authSubjectIsCurrent");
    expect(workspace).toContain("organizationInventoryCursorsRef");
    expect(workspace).toContain("jobInventoryCursorsRef");
    expect(workspace).toContain("organizationInventoryMoreInFlightRef");
    expect(workspace).toContain("jobInventoryMoreInFlightRef");
    expect(workspace).toContain("organizationInventoryInitialInFlightRef");
    expect(workspace).toContain("jobInventoryInitialInFlightRef");
    expect(workspace).toContain("if (!cursor && organizationInventoryInitialInFlightRef.current) return");
    expect(workspace).toContain("if (!cursor && jobInventoryInitialInFlightRef.current) return");
    expect(workspace).toContain("organizationInventoryInitialInFlightRef.current = false");
    expect(workspace).toContain("jobInventoryInitialInFlightRef.current = false");
    expect(workspace).toContain("loadOrganizationForOwner(summary.organizationSlug");
    expect(workspace).toContain("loadJobForOwner(summary.organizationSlug, summary.slug");
    expect(workspace).not.toContain("Inventory panels own");
    expect(workspace).not.toContain("no invite inbox or member list is exposed here");
    expect(workspace).not.toContain("Private owner workspace");
    expect(panels).toContain("Accept as signed-in human");
    expect(panels).toContain("Remove</Button>");
    expect(panels).toContain("Only invitations addressed to your authenticated account");
    expect(panels).toContain("without raw account identifiers");
    expect(panels).not.toContain("Recipient owner ID");
    expect(panels).toContain("No empty state is assumed");
    expect(panels).toContain("Organizations could not be loaded");
    expect(panels).toContain("Jobs could not be loaded");
    expect(panels).toContain("Invitations could not be loaded");
    expect(panels).toContain("Private inventory");
    expect(panels).toContain("Private membership inbox");
  });

  it("remounts an unsaved job draft when the active organization changes", () => {
    expect(employerJobWorkspaceKey("organization-a", null)).not.toBe(employerJobWorkspaceKey("organization-b", null));
    expect(employerJobWorkspaceKey("organization-a", "job-a")).not.toBe(employerJobWorkspaceKey("organization-a", null));
  });

  it("keeps owner status and reviewer review private to each authenticated subject", () => {
    const employer = readFileSync(new URL("../components/employer-workspace.tsx", import.meta.url), "utf8");
    const reviewer = readFileSync(new URL("../components/verification-review-queue.tsx", import.meta.url), "utf8");
    const robots = readFileSync(new URL("../app/robots.ts", import.meta.url), "utf8");
    const page = readFileSync(new URL("../app/verification-review/page.tsx", import.meta.url), "utf8");
    expect(employer).toContain("key={`verification-status-${organization.id}-${subject}`}");
    expect(employer).toContain("key={`verification-${organization.id}-${subject}`}");
    expect(reviewer).toContain("<AuthenticatedVerificationReviewQueue key={subject}");
    expect(reviewer).toContain("mountedRef.current && isSubjectCurrent()");
    expect(robots).toContain('"/verification-review"');
    expect(page).toContain("robots: { index: false, follow: false }");
  });
});
