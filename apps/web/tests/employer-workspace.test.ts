import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  claimEmployerBusy,
  createEmployerBusyClaim,
  releaseEmployerBusy,
} from "@/components/employer-workspace";
import { ApiRequestError } from "@/lib/api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt } from "@/lib/logical-mutation";

const source = readFileSync(
  new URL("../components/employer-workspace.tsx", import.meta.url),
  "utf8",
);
const organizationManagementSource = readFileSync(
  new URL("../components/employer-organization-management.tsx", import.meta.url),
  "utf8",
);
const jobActionsSource = readFileSync(
  new URL("../components/employer-job-actions.tsx", import.meta.url),
  "utf8",
);
const applicationReviewSource = readFileSync(
  new URL("../components/employer-application-review.tsx", import.meta.url),
  "utf8",
);
const managementSources = [
  organizationManagementSource,
  jobActionsSource,
  applicationReviewSource,
];

function handlerSource(name: string): string {
  const start = source.indexOf(`  const ${name} =`);
  const end = source.indexOf("\n  const ", start + 1);
  return source.slice(start, end === -1 ? source.length : end);
}

describe("employer workspace busy claim", () => {
  it("keeps authenticated reads and mutations in the coordinator while delegating record presentation", () => {
    expect(source).toContain('from "@/components/employer-organization-management"');
    expect(source).toContain('from "@/components/employer-job-actions"');
    expect(source).toContain('from "@/components/employer-application-review"');
    expect(source).toContain("<OrganizationManagement");
    expect(source).toContain("<JobActions");
    expect(source).toContain("<ApplicationReview");
    expect(source).not.toContain("function OrganizationManagement(");
    expect(source).not.toContain("function JobActions(");
    expect(source).not.toContain("function ApplicationReview(");

    expect(organizationManagementSource).toContain("export function OrganizationManagement(");
    expect(jobActionsSource).toContain("export function JobActions(");
    expect(applicationReviewSource).toContain("export function ApplicationReview(");
    for (const authorityCall of [
      "createOrganization(",
      "updateOrganization(",
      "inviteOrganizationMember(",
      "createJob(",
      "updateJob(",
      "changeJobLifecycle(",
      "listJobApplications(",
      "getEmployerApplicationDetail(",
      "decideApplication(",
      "beginLogicalMutationAttempt(",
    ]) {
      for (const managementSource of managementSources) {
        expect(managementSource).not.toContain(authorityCall);
      }
      expect(source).toContain(authorityCall);
    }
  });

  it("rejects same-tick duplicate and conflicting claims, then permits reacquisition after its owner releases", () => {
    const claim = createEmployerBusyClaim();

    expect(claimEmployerBusy(claim, "publish")).toBe(true);
    expect(claimEmployerBusy(claim, "publish")).toBe(false);
    expect(claimEmployerBusy(claim, "job-save")).toBe(false);
    expect(releaseEmployerBusy(claim, "job-save")).toBe(false);
    expect(claim.owner).toBe("publish");
    expect(releaseEmployerBusy(claim, "publish")).toBe(true);
    expect(claimEmployerBusy(claim, "job-save")).toBe(true);
    expect(claim.owner).toBe("job-save");
  });

  it("retains membership keys only for an unchanged ambiguous retry", () => {
    let keyNumber = 0;
    const issueKey = () => `membership-key-${++keyNumber}`;
    const intent = { operation: "invite-organization-member", organizationSlug: "acme", memberProfileHandle: "profile-one", role: "member" };
    const first = beginLogicalMutationAttempt(null, "human-a", intent, issueKey);
    const retained = settleLogicalMutationAttempt(first, new ApiRequestError("acknowledgement lost", undefined, "server"));
    const retry = beginLogicalMutationAttempt(retained, "human-a", intent, issueKey);

    expect(retry).toBe(first);
    expect(beginLogicalMutationAttempt(null, "human-a", intent, issueKey).idempotencyKey).not.toBe(first.idempotencyKey);
    expect(beginLogicalMutationAttempt(settleLogicalMutationAttempt(first, new ApiRequestError("rejected", 422, "request")), "human-a", intent, issueKey).idempotencyKey).not.toBe(first.idempotencyKey);
    expect(beginLogicalMutationAttempt(retained, "human-b", intent, issueKey).idempotencyKey).not.toBe(first.idempotencyKey);
    expect(beginLogicalMutationAttempt(retained, "human-a", { ...intent, organizationSlug: "other-org" }, issueKey).idempotencyKey).not.toBe(first.idempotencyKey);
    expect(beginLogicalMutationAttempt(retained, "human-a", { ...intent, memberProfileHandle: "profile-two" }, issueKey).idempotencyKey).not.toBe(first.idempotencyKey);
    expect(beginLogicalMutationAttempt(retained, "human-a", { ...intent, role: "admin" }, issueKey).idempotencyKey).not.toBe(first.idempotencyKey);
  });

  it("claims before every shared-busy dispatch and releases through its owning finally path", () => {
    const dispatches: ReadonlyArray<readonly [string, ...string[]]> = [
      ["loadInvitations", "listOrganizationMembershipInvitations"],
      ["acceptInvitation", "acceptOrganizationMembership"],
      ["inspectOrganization", "loadOrganizationForOwner"],
      ["inspectJob", "loadJobForOwner"],
      ["inspectJobSummary", "loadOrganizationForOwner", "loadJobForOwner"],
      ["saveOrg", "updateOrganization"],
      ["saveJob", "updateJob"],
      ["lifecycle", "changeJobLifecycle"],
      ["loadApplications", "listJobApplications"],
      ["loadOlderApplications", "listJobApplications"],
      ["viewApplication", "getEmployerApplicationDetail"],
      ["decide", "decideApplication"],
      ["establishOrganization", "createOrganization"],
      ["inviteMember", "inviteOrganizationMember"],
      ["loadMembers", "listOrganizationMembers"],
      ["removeMember", "removeOrganizationMember"],
      ["establishJob", "createJob"],
    ];

    for (const [handler, ...calls] of dispatches) {
      const block = handlerSource(handler);
      const claimAt = block.indexOf("beginBusy(busySlot)");

      expect(claimAt, handler).toBeGreaterThan(-1);
      expect(block.indexOf("endBusy(busySlot, requestSubject);"), handler).toBeGreaterThan(claimAt);
      for (const call of calls) {
        expect(block.indexOf(call), `${handler} ${call}`).toBeGreaterThan(claimAt);
      }
    }

    expect(source.match(/setBusy\(null\)/gu) ?? []).toHaveLength(1);
  });

  it("binds each membership write to its logical attempt and clears it after success", () => {
    for (const [handler, slot, intent] of [
      ["acceptInvitation", "membership-accept:${invitation.id}", 'operation: "accept-membership"'],
      ["inviteMember", "membership-invite:${organization.slug}:${memberProfileHandle}", 'operation: "invite-organization-member"'],
      ["removeMember", "membership-remove:${organization.slug}:${member.id}", 'operation: "remove-organization-member"'],
    ] as const) {
      const block = handlerSource(handler);
      const attemptAt = block.indexOf("const attempt = beginAttempt(attemptSlot, requestSubject");

      expect(block).toContain(slot);
      expect(block).toContain(intent);
      expect(attemptAt, handler).toBeGreaterThan(-1);
      expect(block.indexOf("attempt.idempotencyKey"), handler).toBeGreaterThan(attemptAt);
      expect(block.indexOf("mutationAttemptsRef.current.delete(attemptSlot);"), handler).toBeGreaterThan(attemptAt);
      expect(block.indexOf("settleAttempt(attemptSlot, attempt, error)"), handler).toBeGreaterThan(attemptAt);
    }
  });
});
