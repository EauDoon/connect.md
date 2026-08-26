import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import {
  refreshApplicationSummaryState,
  type ApplicationSummaryRefreshState,
  withApplicationSummaryLoadConsent,
} from "@/components/employer-workspace";

const source = readFileSync(
  new URL("../components/employer-workspace.tsx", import.meta.url),
  "utf8",
);

describe("employer application summary access", () => {
  function existingState(): ApplicationSummaryRefreshState<string> {
    return {
      applications: ["existing-application"],
      applicationCursor: "existing-cursor",
      messages: { "existing-application": "private review note" },
      deliveredCursors: new Set(["delivered-cursor"]),
    };
  }

  it("confirms immediately before a fresh application-summary dispatch", async () => {
    const events: string[] = [];
    const dispatch = vi.fn(async () => {
      events.push("dispatch");
      return "loaded";
    });

    await expect(withApplicationSummaryLoadConsent(null, () => {
      events.push("confirm");
      return true;
    }, dispatch)).resolves.toBe("loaded");

    expect(events).toEqual(["confirm", "dispatch"]);
    expect(source.indexOf("withApplicationSummaryLoadConsent(null")).toBeLessThan(source.indexOf("listJobApplications(job"));
  });

  it("preserves every existing summary field when the human cancels", async () => {
    const current = existingState();
    const dispatch = vi.fn(async () => ({ items: ["replacement"], nextCursor: null }));

    await expect(refreshApplicationSummaryState(current, () => false, dispatch, () => true)).resolves.toBe(current);
    expect(dispatch).not.toHaveBeenCalled();
    expect(current).toEqual(existingState());
    const freshLoad = source.indexOf("const loadApplications");
    const loadBody = source.slice(freshLoad, source.indexOf("const loadOlderApplications", freshLoad));
    const cancellationGuard = loadBody.indexOf("if (result === currentState)");

    expect(loadBody).not.toContain("setApplications([])");
    expect(cancellationGuard).toBeGreaterThan(loadBody.indexOf("refreshApplicationSummaryState(currentState"));
    expect(loadBody.indexOf("setMessages(result.messages)")).toBeGreaterThan(cancellationGuard);
    expect(loadBody.indexOf("deliveredCursorsRef.current = result.deliveredCursors")).toBeGreaterThan(cancellationGuard);
    expect(loadBody.indexOf("setApplications(result.applications)")).toBeGreaterThan(cancellationGuard);
    expect(loadBody.indexOf("setApplicationCursor(result.applicationCursor)")).toBeGreaterThan(cancellationGuard);
  });

  it("preserves existing state when a refresh rejects or becomes stale", async () => {
    const rejectedState = existingState();
    await expect(refreshApplicationSummaryState(
      rejectedState,
      () => true,
      async () => { throw new Error("refresh failed"); },
      () => true,
    )).rejects.toThrow("refresh failed");
    expect(rejectedState).toEqual(existingState());

    const staleState = existingState();
    let current = true;
    let finish: ((page: { items: string[]; nextCursor: string | null }) => void) | undefined;
    const dispatch = vi.fn(() => new Promise<{ items: string[]; nextCursor: string | null }>((resolve) => { finish = resolve; }));
    const pending = refreshApplicationSummaryState(staleState, () => true, dispatch, () => current);
    current = false;
    finish?.({ items: ["stale-application"], nextCursor: "stale-cursor" });

    await expect(pending).resolves.toBe(staleState);
    expect(staleState).toEqual(existingState());
  });

  it("atomically replaces the current page and clears stale private review state after success", async () => {
    const current = existingState();
    const next = await refreshApplicationSummaryState(
      current,
      () => true,
      async () => ({ items: ["fresh-application"], nextCursor: "fresh-cursor" }),
      () => true,
    );

    expect(next).not.toBe(current);
    expect(next).toEqual({
      applications: ["fresh-application"],
      applicationCursor: "fresh-cursor",
      messages: {},
      deliveredCursors: new Set(),
    });
    expect(current).toEqual(existingState());
  });

  it("continues an already opened opaque cursor without another confirmation", async () => {
    const confirm = vi.fn(() => false);
    const dispatch = vi.fn(async () => "older");

    await expect(withApplicationSummaryLoadConsent("older-cursor", confirm, dispatch)).resolves.toBe("older");
    expect(confirm).not.toHaveBeenCalled();
    expect(dispatch).toHaveBeenCalledTimes(1);
  });
});
