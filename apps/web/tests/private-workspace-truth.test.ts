import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { mergeProposalFirstPage } from "../components/agent-delegation-manager";
import { NetworkConversationCard } from "../components/network-hub";
import type { AgentProposal } from "../lib/agent-api";

function source(relative: string) {
  return readFileSync(new URL(relative, import.meta.url), "utf8");
}

describe("private workspace load-state truthfulness", () => {
  it("keeps network slices independent and renders empty only after a successful slice load", () => {
    const value = source("../components/network-hub.tsx");
    const reads = source("../components/private-network-reads.ts");
    const panels = source("../components/network-panels.tsx");
    for (const slice of ["request", "connection", "conversation", "notification"]) {
      expect(reads).toContain(`const [${slice}LoadState`);
      expect(panels).toContain('loadState === "error"');
      expect(panels).toContain('loadState === "loaded"');
    }
    expect(panels).toContain("Connections could not be loaded");
    expect(panels).toContain("Connection requests could not be loaded");
    expect(panels).toContain("Conversations could not be loaded");
    expect(panels).toContain("Notifications could not be loaded");
  });

  it("does not render initial feed, inbox, or application failures as empty", () => {
    const feed = source("../components/professional-feed.tsx");
    expect(feed).toContain('feedLoadState === "error" && posts.length === 0');
    expect(feed).toContain('feedLoadState === "loaded" && posts.length === 0');
    expect(feed).toContain('followLoadState === "error" && follows.length === 0');

    const inbox = source("../components/outreach-inbox.tsx");
    expect(inbox).toContain('policyLoadState === "error"');
    expect(inbox).toContain('inboxLoadState === "error" && threads.length === 0');
    expect(inbox).toContain('inboxLoadState === "loaded" && threads.length === 0');

    const applications = source("../components/candidate-applications.tsx");
    expect(applications).toContain('loadState === "error" && applications.length === 0');
    expect(applications).toContain('loadState === "loaded" && applications.length === 0');
  });

  it("keeps employer inventories independent, truthful, and separate from application reads", () => {
    const employer = source("../components/employer-workspace.tsx");
    const panels = source("../components/employer-inventory-panels.tsx");
    for (const state of ["manageableOrganizations", "organizationsLoaded", "organizationsLoadFailed", "organizationInventoryCursor", "manageableJobs", "jobsLoaded", "jobsLoadFailed", "jobInventoryCursor"]) expect(employer).toContain(state);
    expect(panels).toContain('loaded && items.length === 0');
    expect(panels).toContain("No empty state is assumed");
    expect(employer).not.toContain("Inventory panels own");
    expect(employer).toContain("loadOlder={() => void loadManageableOrganizations(organizationInventoryCursor)}");
    expect(employer).toContain("loadOlder={() => void loadManageableJobs(jobInventoryCursor)}");
    expect(employer).toContain("setOrganization(nextOrganization)");
    expect(employer).toContain("setJob(nextJob)");
    expect(employer).toContain("withApplicationSummaryLoadConsent(null");
    const inventoryStart = employer.indexOf("const loadManageableOrganizations");
    const inventoryEnd = employer.indexOf("const loadInvitations");
    expect(inventoryStart).toBeGreaterThanOrEqual(0);
    expect(inventoryEnd).toBeGreaterThan(inventoryStart);
    expect(employer.slice(inventoryStart, inventoryEnd)).not.toContain("listJobApplications");
    expect(employer.slice(inventoryStart, inventoryEnd)).not.toContain("getEmployerApplication");
    const selectionStart = employer.indexOf("const inspectJobSummary");
    const selectionEnd = employer.indexOf("const saveOrg");
    expect(selectionStart).toBeGreaterThanOrEqual(0);
    expect(selectionEnd).toBeGreaterThan(selectionStart);
    expect(employer.slice(selectionStart, selectionEnd)).not.toContain("listJobApplications");
    expect(employer.slice(selectionStart, selectionEnd)).not.toContain("getEmployerApplication");
  });

  it("keeps agent inventories unavailable rather than empty after initial failures", () => {
    const identities = source("../components/agent-identity-manager.tsx");
    expect(identities).toContain('identityLoadState === "error" && identities.length === 0');
    expect(identities).toContain('identityLoadState === "loaded" && identities.length === 0');
    expect(identities).toContain('documentLoadState === "error"');
    expect(identities).toContain("mandateLoadStates");
    expect(identities).toContain("mandateLoadErrors");

    const delegations = source("../components/agent-delegation-manager.tsx");
    for (const dataset of ["grants", "documents", "audit", "proposals"]) {
      expect(delegations).toContain(`loadStates.${dataset}`);
      expect(delegations).toContain(`loadErrors.${dataset}`);
    }
    expect(identities).toContain('onRetry={() => void loadDocuments()}');
    expect(identities).toContain('onRetry={() => void loadIdentities()}');
    expect(identities).toContain('onRetry={() => void loadMandates(identity.handle)}');
    expect(delegations).toContain('onRetry={() => void loadDocuments()}');
    expect(delegations).toContain('onRetry={() => void loadGrants()}');
    expect(delegations).toContain('onRetry={() => void loadAudit()}');
    expect(delegations).toContain('onRetry={() => void loadProposals(true)}');
    expect(identities).not.toContain('onRetry={() => void refresh()}');
    expect(delegations).not.toContain('onRetry={() => void refresh()}');
  });

  it("preserves appended proposal pages when page one is retried", () => {
    const first = proposal("proposal-1", "pending");
    const appended = proposal("proposal-2", "pending");
    const refreshed = { ...first, status: "accepted" as const, decidedAt: "2026-08-04T12:00:00Z" };

    expect(mergeProposalFirstPage([first, appended], [refreshed])).toEqual([refreshed, appended]);
  });

  it("renders separate accessible conversation and profile links without nested anchors", () => {
    vi.stubGlobal("React", { createElement });
    const markup = renderToStaticMarkup(createElement(NetworkConversationCard, { conversation: { id: "conversation-1", connectionId: "connection-1", counterpartyProfileHandle: "ari-chen", createdAt: "2026-08-04T00:00:00Z", retentionExpiresAt: "2027-08-04T00:00:00Z" } }));
    vi.unstubAllGlobals();

    expect(markup.match(/<a\b/gu)).toHaveLength(2);
    expect(markup).toContain('href="/p/ari-chen"');
    expect(markup).toContain('href="/messages/conversation-1"');
    expect(markup).toContain('aria-label="Open conversation with @ari-chen"');
    expect(markup).not.toMatch(/<a\b[^>]*>(?:(?!<\/a>)[\s\S])*<a\b/u);
  });

});

function proposal(id: string, status: AgentProposal["status"]): AgentProposal {
  return { id, documentId: "document-1", kind: "profile", identifier: "ari-chen", markdown: `# ${id}`, ifMatch: "etag-1", status, submitterActorId: "agent-1", submitterGrantId: "grant-1", createdAt: "2026-08-04T00:00:00Z", decidedAt: null };
}
