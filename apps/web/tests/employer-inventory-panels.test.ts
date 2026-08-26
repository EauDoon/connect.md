import { readFileSync } from "node:fs";
import * as React from "react";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ManageableJobInventory,
  ManageableOrganizationInventory,
  MembershipInvitationInbox,
  OrganizationActions,
  OrganizationMemberInventory,
} from "../components/employer-inventory-panels";
import type {
  ManageableJobSummary,
  ManageableOrganizationSummary,
  Organization,
  OrganizationInvitation,
  OrganizationMembershipInvitation,
} from "../lib/recruitment-api";

const source = readFileSync(
  new URL("../components/employer-inventory-panels.tsx", import.meta.url),
  "utf8",
);

function render<P>(value: (props: P) => ReactElement, props: P) {
  vi.stubGlobal("React", React);
  try {
    return renderToStaticMarkup(createElement(value as React.JSXElementConstructor<P>, props));
  } finally {
    vi.unstubAllGlobals();
  }
}

describe("employer inventory presentation panels", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the extracted family presentation-only", () => {
    expect(source).toContain("export function OrganizationActions");
    expect(source).toContain("export function ManageableOrganizationInventory");
    expect(source).toContain("export function ManageableJobInventory");
    expect(source).toContain("export function MembershipInvitationInbox");
    expect(source).toContain("export function OrganizationMemberInventory");
    expect(source).not.toMatch(/\b(useConnectmdAuth|SubjectGuard|fetch|getToken|idempotencyKey)\b/u);
  });

  it("renders organization and job inventory summaries with their open callbacks", () => {
    const organizationMarkup = render(ManageableOrganizationInventory, {
      items: [organizationSummary],
      loaded: true,
      loadFailed: false,
      nextCursor: null,
      moreLoading: false,
      busy: null,
      retry: () => undefined,
      loadOlder: () => undefined,
      open: () => undefined,
    });
    const jobMarkup = render(ManageableJobInventory, {
      items: [jobSummary],
      loaded: true,
      loadFailed: false,
      nextCursor: null,
      moreLoading: false,
      busy: null,
      retry: () => undefined,
      loadOlder: () => undefined,
      open: () => undefined,
    });

    expect(organizationMarkup).toContain("Organizations I manage");
    expect(organizationMarkup).toContain("Acme Research");
    expect(organizationMarkup).toContain("Open organization");
    expect(jobMarkup).toContain("Jobs I manage");
    expect(jobMarkup).toContain("Platform engineer");
    expect(jobMarkup).toContain("Open job");
    expect(source).toContain("onClick={() => open(summary)}");
  });

  it("preserves busy disabled states for inventory and membership actions", () => {
    const membersMarkup = render(OrganizationMemberInventory, {
      organization,
      members: [member],
      nextCursor: "members-next",
      busy: "members",
      refresh: () => undefined,
      loadOlder: () => undefined,
      remove: async () => undefined,
    });
    const invitationsMarkup = render(MembershipInvitationInbox, {
      invitations: [invitation],
      loaded: true,
      loadFailed: false,
      nextCursor: "invitations-next",
      busy: invitation.id,
      refresh: () => undefined,
      loadOlder: () => undefined,
      accept: async () => undefined,
    });

    expect(membersMarkup).toContain("Member authority");
    expect(membersMarkup.match(/<button[^>]* disabled=""/gu) ?? []).toHaveLength(3);
    expect(invitationsMarkup).toContain("Invitations for this signed-in human");
    expect(invitationsMarkup.match(/<button[^>]* disabled=""/gu) ?? []).toHaveLength(3);
    expect(source).toContain("onClick={() => void remove(member)}");
    expect(source).toContain("onClick={() => void accept(invitation)}");
  });

  it("keeps fail-closed loading and empty states visible", () => {
    const jobErrorMarkup = render(ManageableJobInventory, {
      items: [],
      loaded: false,
      loadFailed: true,
      nextCursor: null,
      moreLoading: false,
      busy: null,
      retry: () => undefined,
      loadOlder: () => undefined,
      open: () => undefined,
    });
    const invitationEmptyMarkup = render(MembershipInvitationInbox, {
      invitations: [],
      loaded: true,
      loadFailed: false,
      nextCursor: null,
      busy: null,
      refresh: () => undefined,
      loadOlder: () => undefined,
      accept: async () => undefined,
    });

    expect(jobErrorMarkup).toContain('role="alert"');
    expect(jobErrorMarkup).toContain("Jobs could not be loaded");
    expect(jobErrorMarkup).toContain("Retry jobs");
    expect(invitationEmptyMarkup).toContain("No pending invitations.");
  });

  it("keeps organization establishment disabled until attestation and required fields", () => {
    const markup = render(OrganizationActions, {
      busy: null,
      inspect: async () => undefined,
      create: async () => undefined,
    });

    expect(markup).toContain("Open or establish an organization");
    expect(markup).toContain("I am the signed-in human authorized to establish this mandate.");
    expect(markup).toContain("Create private unverified organization");
    expect(markup.match(/<button[^>]* disabled=""/gu) ?? []).toHaveLength(1);
  });
});

const organizationSummary = {
  id: "organization-1",
  slug: "acme-research",
  name: "Acme Research",
  managementRole: "owner",
  visibility: "private",
  updatedAt: "2026-08-04T00:00:00Z",
  recruitingVerificationActive: false,
} as ManageableOrganizationSummary;

const jobSummary = {
  id: "job-1",
  slug: "platform-engineer",
  title: "Platform engineer",
  organizationName: "Acme Research",
  organizationSlug: "acme-research",
  managementRole: "owner",
  status: "draft",
  location: null,
  workMode: null,
  employmentType: null,
  updatedAt: "2026-08-04T00:00:00Z",
} as ManageableJobSummary;

const organization = {
  id: "organization-1",
  slug: "acme-research",
  name: "Acme Research",
} as Organization;

const member = {
  id: "membership-1",
  organizationId: "organization-1",
  memberProfileHandle: "ari-chen",
  role: "member",
  status: "invited",
  createdAt: "2026-08-04T00:00:00Z",
} as OrganizationInvitation;

const invitation = {
  id: "invitation-1",
  organizationName: "Acme Research",
  organizationSlug: "acme-research",
  role: "member",
  createdAt: "2026-08-04T00:00:00Z",
} as OrganizationMembershipInvitation;
