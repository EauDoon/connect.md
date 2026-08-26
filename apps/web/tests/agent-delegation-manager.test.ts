import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  beginDelegationInventoryRead,
  beginDelegationResourceRead,
  commitProposalBaseMarkdownIfCurrent,
  claimDelegationMutation,
  continuousAgentHandoff,
  createDelegationMutationCoordinator,
  finishDelegationResourceRead,
  isCurrentDelegationMutation,
  isCurrentDelegationResource,
  releaseDelegationMutation,
  resetDelegationMutationCoordinator,
  upsertDelegation,
  type DelegationMutationClaim,
} from "@/components/agent-delegation-manager";
import type { AgentDelegation } from "@/lib/agent-api";
import { ApiRequestError } from "@/lib/api";
import {
  beginLogicalMutationAttempt,
  settleLogicalMutationAttempt,
} from "@/lib/logical-mutation";

const source = readFileSync(
  new URL("../components/agent-delegation-manager.tsx", import.meta.url),
  "utf8",
);
const stateSource = readFileSync(
  new URL("../lib/agent-delegation-state.ts", import.meta.url),
  "utf8",
);
const panelSource = readFileSync(
  new URL("../components/agent-delegation-panels.tsx", import.meta.url),
  "utf8",
);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function settleInventoryRead<T>(
  coordinator: ReturnType<typeof createDelegationMutationCoordinator>,
  subject: string,
  resource: "documents" | "audit",
  generation: number,
  response: Promise<T>,
  apply: (value: T) => void,
  onError?: (error: unknown) => void,
): Promise<void> {
  try {
    const value = await response;
    if (isCurrentDelegationResource(coordinator, subject, resource, generation)) apply(value);
  } catch (error) {
    if (isCurrentDelegationResource(coordinator, subject, resource, generation)) onError?.(error);
  } finally {
    finishDelegationResourceRead(coordinator, resource, generation);
  }
}

async function applyProposalDecision<T>(
  coordinator: ReturnType<typeof createDelegationMutationCoordinator>,
  claim: DelegationMutationClaim,
  response: Promise<T>,
  apply: (value: T) => void,
): Promise<void> {
  try {
    const value = await response;
    if (isCurrentDelegationMutation(coordinator, claim)) apply(value);
  } finally {
    releaseDelegationMutation(coordinator, claim);
  }
}

describe("agent delegation mutation coordination", () => {
  it("keeps the pure delegation state machine dependency-free and re-exported", () => {
    expect(stateSource).toContain(
      'import type { AgentDelegation, AgentProposal } from "@/lib/agent-api";',
    );
    expect(stateSource).not.toMatch(/\b(apiRequest|fetch|getToken|useState|useEffect|console)\b/u);
    for (const marker of [
      "mergeProposalFirstPage",
      "upsertDelegation",
      "commitProposalBaseMarkdownIfCurrent",
      "createDelegationMutationCoordinator",
      "claimDelegationMutation",
      "releaseDelegationMutation",
    ]) {
      expect(stateSource).toContain(`export ${marker === "commitProposalBaseMarkdownIfCurrent" ? "async " : ""}function ${marker}`);
    }
    expect(source).toContain('from "@/lib/agent-delegation-state";');
    expect(source).not.toContain("function delegationScope");
  });

  it("keeps extracted panels presentation-only while the manager owns coordination", () => {
    expect(source).toContain('from "@/components/agent-delegation-panels";');
    expect(source).toContain("<AgentGrantInventoryPanel");
    expect(source).toContain("<AgentProposalReviewPanel");
    for (const marker of [
      "isSubjectCurrent",
      "claimDelegationMutation",
      "beginLogicalMutationAttempt",
      "idempotencyKey",
      "loadProposalComparison",
    ]) {
      expect(source).toContain(marker);
    }
    for (const marker of [
      "export function AgentGrantInventoryPanel",
      "export function AgentProposalReviewPanel",
      "onEmergencyStop",
      "onRevoke",
      "onCompare",
      "onDecide",
      "Accept and publish",
    ]) {
      expect(panelSource).toContain(marker);
    }
    const summaryTags = [...panelSource.matchAll(/<summary\b[^>]*>/gu)].map(
      ([tag]) => tag,
    );
    expect(summaryTags).toHaveLength(2);
    for (const summaryTag of summaryTags) {
      expect(summaryTag).toMatch(/className="[^"]*\bmin-h-11\b[^"]*"/u);
      expect(summaryTag).not.toMatch(/\b(?:onClick|onKeyDown|role)=/u);
    }
    for (const label of [
      "Candidate Markdown",
      "Line comparison with current canonical Markdown",
    ]) {
      expect(panelSource).toContain(label);
    }
    expect(panelSource).not.toMatch(
      /\b(useConnectmdAuth|getToken|fetch|createDelegation|decideAgentProposal|claimDelegationMutation|useEffect|SubjectGuard)\b/u,
    );
  });

  it("allows only one same-tick create, revoke, emergency, or proposal decision dispatch", () => {
    const coordinator = createDelegationMutationCoordinator("human-a");
    const create = claimDelegationMutation(coordinator, "human-a", "grants");

    expect(create).not.toBeNull();
    expect(
      claimDelegationMutation(coordinator, "human-a", "grants"),
    ).toBeNull();
    expect(
      claimDelegationMutation(coordinator, "human-a", "proposals"),
    ).toBeNull();
    expect(releaseDelegationMutation(coordinator, create!)).toBe(true);

    const proposal = claimDelegationMutation(
      coordinator,
      "human-a",
      "proposals",
    );
    expect(proposal).not.toBeNull();
    expect(
      claimDelegationMutation(coordinator, "human-a", "grants"),
    ).toBeNull();
  });

  it("does not let a stale owner release a newer claim after the subject changes", () => {
    const coordinator = createDelegationMutationCoordinator("human-a");
    const stale = claimDelegationMutation(coordinator, "human-a", "grants");
    expect(stale).not.toBeNull();

    resetDelegationMutationCoordinator(coordinator, "human-b");
    const current = claimDelegationMutation(coordinator, "human-b", "grants");
    expect(current).not.toBeNull();
    expect(releaseDelegationMutation(coordinator, stale!)).toBe(false);
    expect(coordinator.ownerId).toBe(current!.id);
  });

  it("drops a deferred completion after its subject scope changes", async () => {
    const coordinator = createDelegationMutationCoordinator("human-a");
    const stale = claimDelegationMutation(
      coordinator,
      "human-a",
      "proposals",
    );
    expect(stale).not.toBeNull();
    const response = deferred<string>();
    let visible = "unchanged";
    const task = applyProposalDecision(coordinator, stale!, response.promise, (value) => {
      visible = value;
    });

    resetDelegationMutationCoordinator(coordinator, "human-b");
    response.resolve("private-old-subject-result");
    await task;

    expect(visible).toBe("unchanged");
  });

  it("retains a creation key only for the same normalized intent and subject", () => {
    let keyCount = 0;
    const nextKey = () => `key-${++keyCount}`;
    const intent = { operation: "create-agent-grant", name: "Steward", mode: "proposal", resourceType: "document", resourceId: "doc-1", expiresAt: "2026-09-01T00:00:00.000Z", scopes: ["documents:read", "inventory:read", "changes:read", "proposals:write"] };
    const first = beginLogicalMutationAttempt(null, "human-a", intent, nextKey);
    const retained = settleLogicalMutationAttempt(
      first,
      new ApiRequestError("acknowledgement lost", undefined, "server"),
    );
    const retry = beginLogicalMutationAttempt(retained, "human-a", { ...intent, scopes: [...intent.scopes] }, nextKey);
    const changed = beginLogicalMutationAttempt(
      retained,
      "human-a",
      { ...intent, scopes: ["documents:read", "inventory:read", "changes:read", "documents:write"] },
      nextKey,
    );
    const differentSubject = beginLogicalMutationAttempt(retained, "human-b", intent, nextKey);

    expect(retry.idempotencyKey).toBe(first.idempotencyKey);
    expect(changed.idempotencyKey).not.toBe(first.idempotencyKey);
    expect(differentSubject.idempotencyKey).not.toBe(first.idempotencyKey);
  });

  it("upserts a recovered grant without retaining or duplicating a secret-bearing inventory row", () => {
    const prior: AgentDelegation = {
      id: "grant-1", name: "Old name", prefix: "cnd_old", scopes: ["documents:read"], mode: "proposal", status: "active", expiresAt: "2026-09-01T00:00:00Z", resourceType: "document", resourceId: "doc-1", createdAt: "2026-08-03T00:00:00Z", lastUsedAt: "2026-08-04T00:00:00Z"
    };
    const recovered: AgentDelegation = {
      ...prior, name: "Profile steward", prefix: "cnd_grant", scopes: ["documents:read", "inventory:read"], lastUsedAt: null
    };
    const merged = upsertDelegation([prior], recovered);

    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({ id: "grant-1", name: "Profile steward", prefix: "cnd_grant", lastUsedAt: "2026-08-04T00:00:00Z" });
    expect(merged[0]).not.toHaveProperty("secret");
  });

  it("creates a grant-specific continuous handoff without including credentials or unrelated private authority", () => {
    const grant: AgentDelegation = {
      id: "grant-private-id",
      name: "Profile steward",
      prefix: "cnd_private_prefix",
      scopes: ["documents:read", "inventory:read", "changes:read", "proposals:write"],
      mode: "proposal",
      status: "active",
      expiresAt: "2026-09-01T00:00:00Z",
      resourceType: "document",
      resourceId: "doc-private-boundary",
      createdAt: "2026-08-03T00:00:00Z",
      lastUsedAt: null,
    };
    const handoff = continuousAgentHandoff(grant);

    for (const value of ["/llms.txt", "/llms-full.txt", "/openapi.json", "/mcp", "doc-private-boundary", "proposal_only", "2026-09-01T00:00:00Z", "GET /v1/changes?limit=&cursor=", "next_cursor", "revoke this grant immediately"]) expect(handoff).toContain(value);
    for (const forbidden of [grant.id, grant.prefix, "cng_", "Clerk", "mandate identifier", "private Markdown", "recipient policy"]) expect(handoff).not.toContain(forbidden);
  });

  it("describes direct grants as conditional writes rather than autonomous authority", () => {
    const handoff = continuousAgentHandoff({
      id: "grant-private-id",
      name: "Direct steward",
      prefix: "cnd_private_prefix",
      scopes: ["documents:read", "documents:write"],
      mode: "direct",
      status: "active",
      expiresAt: "2026-09-01T00:00:00Z",
      resourceType: "owner",
      resourceId: null,
      createdAt: "2026-08-03T00:00:00Z",
      lastUsedAt: null,
    });

    expect(handoff).toContain("owner inventory boundary");
    expect(handoff).toContain("current ETag/If-Match precondition");
    expect(handoff).toContain("fresh Idempotency-Key");
    expect(handoff).toContain("Do not attempt to recreate, expand, or replace authority.");
  });

  it("prevents a stale proposal decision from overwriting a newer proposal refresh", async () => {
    const coordinator = createDelegationMutationCoordinator("human-a");
    const decision = claimDelegationMutation(
      coordinator,
      "human-a",
      "proposals",
    );
    expect(decision).not.toBeNull();
    const decisionResponse = deferred<string[]>();
    let proposals = ["existing"];
    const decisionTask = applyProposalDecision(
      coordinator,
      decision!,
      decisionResponse.promise,
      (items) => {
        proposals = items;
      },
    );

    const refreshGeneration = beginDelegationResourceRead(
      coordinator,
      "human-a",
      "proposals",
    );
    expect(refreshGeneration).not.toBeNull();
    decisionResponse.resolve(["stale-decision"]);
    await decisionTask;
    expect(proposals).toEqual(["existing"]);

    if (
      isCurrentDelegationResource(
        coordinator,
        "human-a",
        "proposals",
        refreshGeneration!,
      )
    ) {
      proposals = ["fresh-refresh"];
    }
    expect(proposals).toEqual(["fresh-refresh"]);
  });

  it.each(["documents", "audit"] as const)(
    "deduplicates an initial/retry %s read and rejects its stale same-subject completion",
    async (resource) => {
      const coordinator = createDelegationMutationCoordinator("human-a");
      const firstResponse = deferred<string>();
      const firstGeneration = beginDelegationInventoryRead(
        coordinator,
        "human-a",
        resource,
      );
      expect(firstGeneration).not.toBeNull();
      expect(
        beginDelegationInventoryRead(coordinator, "human-a", resource),
      ).toBeNull();

      let visible = "unchanged";
      const firstTask = settleInventoryRead(
        coordinator,
        "human-a",
        resource,
        firstGeneration!,
        firstResponse.promise,
        (value) => {
          visible = value;
        },
      );

      resetDelegationMutationCoordinator(coordinator, "human-a");
      const currentResponse = deferred<string>();
      const currentGeneration = beginDelegationInventoryRead(
        coordinator,
        "human-a",
        resource,
      );
      expect(currentGeneration).not.toBeNull();
      const currentTask = settleInventoryRead(
        coordinator,
        "human-a",
        resource,
        currentGeneration!,
        currentResponse.promise,
        (value) => {
          visible = value;
        },
      );

      firstResponse.resolve(`stale-${resource}`);
      await firstTask;
      expect(visible).toBe("unchanged");

      currentResponse.resolve(`current-${resource}`);
      await currentTask;
      expect(visible).toBe(`current-${resource}`);
      expect(coordinator.resourceReadGeneration[resource]).toBeNull();
    },
  );

  it.each(["documents", "audit"] as const)(
    "does not let a stale %s read error replace current truth",
    async (resource) => {
      const coordinator = createDelegationMutationCoordinator("human-a");
      const firstResponse = deferred<string>();
      const firstGeneration = beginDelegationInventoryRead(
        coordinator,
        "human-a",
        resource,
      );
      expect(firstGeneration).not.toBeNull();
      let visibleError = "unchanged";
      const firstTask = settleInventoryRead(
        coordinator,
        "human-a",
        resource,
        firstGeneration!,
        firstResponse.promise,
        () => undefined,
        () => {
          visibleError = "stale";
        },
      );

      resetDelegationMutationCoordinator(coordinator, "human-a");
      const currentResponse = deferred<string>();
      const currentGeneration = beginDelegationInventoryRead(
        coordinator,
        "human-a",
        resource,
      );
      expect(currentGeneration).not.toBeNull();
      const currentTask = settleInventoryRead(
        coordinator,
        "human-a",
        resource,
        currentGeneration!,
        currentResponse.promise,
        () => undefined,
        () => {
          visibleError = "current";
        },
      );

      firstResponse.reject(new Error(`stale-${resource}`));
      await firstTask;
      expect(visibleError).toBe("unchanged");

      currentResponse.reject(new Error(`current-${resource}`));
      await currentTask;
      expect(visibleError).toBe("current");
      expect(coordinator.resourceReadGeneration[resource]).toBeNull();
    },
  );

  it("guards document and audit initial/retry commits, errors, and cleanup in the component", () => {
    const documentStart = source.indexOf("const loadDocuments = useCallback");
    const auditStart = source.indexOf("const loadAudit = useCallback");
    const proposalsStart = source.indexOf("const loadProposals = useCallback");
    const documentBlock = source.slice(documentStart, auditStart);
    const auditBlock = source.slice(auditStart, proposalsStart);

    for (const [resource, block, loader, setter] of [
      ["documents", documentBlock, "listOwnedDocumentOptions", "setDocuments"],
      ["audit", auditBlock, "listDelegationAudit", "setAudit"],
    ] as const) {
      expect(block).toContain("beginDelegationInventoryRead");
      expect(block).toContain(`isCurrentDelegationResource(mutationCoordinatorRef.current, subject, "${resource}", generation)`);
      expect(block).toContain(`finishDelegationResourceRead(mutationCoordinatorRef.current, "${resource}", generation)`);
      expect(block.indexOf("isSubjectCurrent() || !isCurrentDelegationResource")).toBeGreaterThan(block.indexOf(`await ${loader}`));
      expect(block.indexOf("isSubjectCurrent() && isCurrentDelegationResource")).toBeGreaterThan(block.indexOf("catch (error)"));
      expect(block.indexOf(setter)).toBeGreaterThan(block.indexOf("isSubjectCurrent() || !isCurrentDelegationResource"));
    }
    expect(source).toContain('onRetry={() => void loadDocuments()}');
    expect(source).toContain('onRetry={() => void loadAudit()}');
  });

  it("suppresses a stale account's proposal base-Markdown completion", async () => {
    const response = deferred<string>();
    let current = true;
    let visibleBase = "unchanged";
    const task = commitProposalBaseMarkdownIfCurrent(response.promise, () => current, (markdown) => { visibleBase = markdown; });

    current = false;
    response.resolve("# private markdown from the former account");
    await expect(task).resolves.toBe(false);
    expect(visibleBase).toBe("unchanged");
  });

  it("claims before each mutation dispatch and keeps proposal state behind the current-generation check", () => {
    const createStart = source.indexOf("async function create()");
    const createEnd = source.indexOf("async function revoke", createStart);
    const revokeStart = createEnd;
    const revokeEnd = source.indexOf("async function emergencyStop", revokeStart);
    const emergencyStart = revokeEnd;
    const emergencyEnd = source.indexOf("async function copySecret", emergencyStart);
    const proposalStart = source.indexOf("async function decideProposal", emergencyEnd);
    const proposalEnd = source.indexOf("async function loadProposalComparison", proposalStart);

    for (const [label, block, dispatch] of [
      ["create", source.slice(createStart, createEnd), "createDelegation("],
      ["revoke", source.slice(revokeStart, revokeEnd), "revokeDelegation("],
      ["emergency", source.slice(emergencyStart, emergencyEnd), "emergencyStopDelegations("],
      ["proposal", source.slice(proposalStart, proposalEnd), "decideAgentProposal("],
    ] as const) {
      expect(block.indexOf("beginMutation("), label).toBeLessThan(
        block.indexOf(dispatch),
      );
      expect(block).toContain("finishMutation(claim);");
    }
    const proposalBlock = source.slice(proposalStart, proposalEnd);
    expect(proposalBlock.indexOf("if (!mutationIsCurrent(claim)) return;")).toBeLessThan(
      proposalBlock.indexOf("setProposals("),
    );
  });

  it("keeps stale create success, catch, and finally paths from mutating attempts or UI", () => {
    const createStart = source.indexOf("async function create()");
    const createEnd = source.indexOf("async function revoke", createStart);
    const createBlock = source.slice(createStart, createEnd);
    const successGuard = createBlock.indexOf("if (!requestIsCurrent()) return;", createBlock.indexOf("const response = await createDelegation"));
    const catchStart = createBlock.indexOf("} catch (error) {");
    const catchGuard = createBlock.indexOf("if (!requestIsCurrent()) return;", catchStart);
    const finallyStart = createBlock.indexOf("} finally {");

    expect(successGuard).toBeGreaterThan(createBlock.indexOf("const response = await createDelegation"));
    expect(successGuard).toBeLessThan(createBlock.indexOf('mutationAttemptsRef.current.delete("grant:create")'));
    expect(catchGuard).toBeGreaterThan(catchStart);
    expect(catchGuard).toBeLessThan(createBlock.indexOf("settleLogicalMutationAttempt", catchStart));
    expect(createBlock.slice(finallyStart)).toContain("if (requestIsCurrent()) finishMutation(claim);");
    expect(createBlock).toContain("setSecret(null);");
    expect(createBlock).toContain("upsertDelegation(current, response.delegation)");
    expect(createBlock).toContain("The one-time key cannot be recovered. Revoke and recreate this grant if you did not save it.");
    expect(createBlock).toContain("setSecret({ value: response.key, copied: false })");
  });
});
