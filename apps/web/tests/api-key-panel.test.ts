import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  claimApiKeyMutation,
  createApiKeyMutationCoordinator,
  isCurrentApiKeyMutation,
  projectApiKeyCreationOutcome,
  releaseApiKeyMutation,
  resetApiKeyMutationCoordinator,
} from "@/components/api-key-panel";
import { ApiRequestError, type ApiKeyCreateResult } from "@/lib/api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt } from "@/lib/logical-mutation";

const source = readFileSync(new URL("../components/api-key-panel.tsx", import.meta.url), "utf8");

const createdKey: ApiKeyCreateResult = {
  id: "key-1",
  prefix: "cmd_live",
  scopes: ["documents:read"],
  created_at: "2026-08-05T00:00:00Z",
  key: "cmd_secret_exact",
  recovery_required: false,
};

describe("API-key panel logical mutation boundaries", () => {
  it("allows exactly one same-tick create or revoke claim", () => {
    const coordinator = createApiKeyMutationCoordinator("user-a");
    const create = claimApiKeyMutation(coordinator, "user-a");
    const secondCreate = claimApiKeyMutation(coordinator, "user-a");
    const concurrentRevoke = claimApiKeyMutation(coordinator, "user-a");

    expect(create).not.toBeNull();
    expect(secondCreate).toBeNull();
    expect(concurrentRevoke).toBeNull();
    expect(releaseApiKeyMutation(coordinator, create!)).toBe(true);

    const laterRevoke = claimApiKeyMutation(coordinator, "user-a");
    const secondRevoke = claimApiKeyMutation(coordinator, "user-a");
    expect(laterRevoke).toMatchObject({ id: create!.id + 1, scope: create!.scope });
    expect(secondRevoke).toBeNull();
  });

  it("does not allow an old owner to release a reset coordinator", () => {
    const coordinator = createApiKeyMutationCoordinator("user-a");
    const stale = claimApiKeyMutation(coordinator, "user-a");
    expect(stale).not.toBeNull();

    resetApiKeyMutationCoordinator(coordinator, "user-a");
    const current = claimApiKeyMutation(coordinator, "user-a");

    expect(current).not.toBeNull();
    expect(releaseApiKeyMutation(coordinator, stale!)).toBe(false);
    expect(isCurrentApiKeyMutation(coordinator, current!)).toBe(true);
  });

  it("does not apply a deferred result after its subject scope changes", async () => {
    const coordinator = createApiKeyMutationCoordinator("user-a");
    const stale = claimApiKeyMutation(coordinator, "user-a");
    let resolve!: (value: ApiKeyCreateResult) => void;
    const response = new Promise<ApiKeyCreateResult>((resolveResponse) => { resolve = resolveResponse; });
    let visibleOutcome: ReturnType<typeof projectApiKeyCreationOutcome> | null = null;
    const inFlight = response.then((result) => {
      if (isCurrentApiKeyMutation(coordinator, stale!)) visibleOutcome = projectApiKeyCreationOutcome("user-a", result);
    });

    resetApiKeyMutationCoordinator(coordinator, "user-b");
    resolve(createdKey);
    await inFlight;

    expect(visibleOutcome).toBeNull();
    expect(releaseApiKeyMutation(coordinator, stale!)).toBe(false);
  });

  it("reuses logical keys only for an unchanged subject and intent", () => {
    let issued = 0;
    const issueKey = () => `logical-${++issued}`;
    const first = beginLogicalMutationAttempt(null, "user-a", { kind: "api-key-create", scopes: ["documents:read"] }, issueKey);
    const retained = settleLogicalMutationAttempt(first, new ApiRequestError("confirmation lost", undefined, "server"));
    const unchanged = beginLogicalMutationAttempt(retained, "user-a", { kind: "api-key-create", scopes: ["documents:read"] }, issueKey);
    const changedScopes = beginLogicalMutationAttempt(retained, "user-a", { kind: "api-key-create", scopes: ["documents:write"] }, issueKey);
    const changedSubject = beginLogicalMutationAttempt(retained, "user-b", { kind: "api-key-create", scopes: ["documents:read"] }, issueKey);

    expect(unchanged.idempotencyKey).toBe(first.idempotencyKey);
    expect(changedScopes.idempotencyKey).not.toBe(first.idempotencyKey);
    expect(changedSubject.idempotencyKey).not.toBe(first.idempotencyKey);
  });

  it("projects recovery without a secret and preserves the first created secret exactly", () => {
    expect(projectApiKeyCreationOutcome("user-a", createdKey)).toStrictEqual({
      secret: { subject: "user-a", key: "cmd_secret_exact", copied: false },
      recoveryNotice: "",
    });
    expect(projectApiKeyCreationOutcome("user-a", {
      id: "key-2",
      prefix: "cmd_recovered",
      scopes: ["documents:read"],
      created_at: "2026-08-05T00:00:00Z",
      recovery_required: true,
    })).toStrictEqual({
      secret: null,
      recoveryNotice: "The one-time secret for cmd_recovered… was not recovered. Revoke this key and create a replacement.",
    });
  });

  it("wires both mutation paths through the local claim and outcome guards", () => {
    expect(source).toContain("const claim = claimMutation();");
    expect(source).toContain("if (!mutationIsCurrent(claim)) return;");
    expect(source).toContain("const outcome = projectApiKeyCreationOutcome(ownerSubject, created);");
    expect(source).toContain("setSecret(outcome.secret);");
    expect(source).toContain("setRecoveryNotice(outcome.recoveryNotice);");
  });

  it("binds the API-key inventory request and every resulting state update to the current subject", () => {
    expect(source).toContain("const isListSubjectCurrent = () => active && isSubjectCurrent();");
    expect(source).toContain("listApiKeys(getToken, isListSubjectCurrent)");
    expect(source.match(/if \(isListSubjectCurrent\(\)\) setListState/gu)).toHaveLength(2);
  });
});
