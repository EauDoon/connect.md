import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  beginPrivateNetworkRead,
  createPrivateNetworkReadEpoch,
  finishPrivateNetworkRead,
  markPrivateNetworkReadReady,
  privateNetworkReadAllowsDependentAction,
  privateNetworkReadIsCurrent,
} from "../components/private-network-reads";

const source = readFileSync(
  new URL("../components/private-network-reads.ts", import.meta.url),
  "utf8",
);

describe("private network read controller", () => {
  it("keeps the extracted epoch contract stale-safe and fail-closed", async () => {
    const state = createPrivateNetworkReadEpoch();
    const commits: string[] = [];
    const deferred = () => {
      let resolve!: (value: string) => void;
      const promise = new Promise<string>((done) => { resolve = done; });
      return { promise, resolve };
    };
    const oldResponse = deferred();
    const currentResponse = deferred();
    const run = async (value: Promise<string>) => {
      const requestEpoch = beginPrivateNetworkRead(state);
      const result = await value;
      if (privateNetworkReadIsCurrent(state, requestEpoch)) {
        commits.push(result);
        markPrivateNetworkReadReady(state, requestEpoch);
      }
      finishPrivateNetworkRead(state, requestEpoch);
    };

    const oldTask = run(oldResponse.promise);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(false);
    const currentTask = run(currentResponse.promise);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(false);
    currentResponse.resolve("current");
    await currentTask;
    oldResponse.resolve("stale");
    await oldTask;

    expect(commits).toEqual(["current"]);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(true);
  });

  it("owns all four independent readers and preserves cursor and error guards", () => {
    const loaders = [
      ["requests", "loadRequests", "listConnectionRequestInboxForSubject"],
      ["connections", "loadConnections", "listConnectionsForSubject"],
      ["conversations", "loadConversations", "listConversationsForSubject"],
      ["notifications", "loadNotifications", "listNotificationsForSubject"],
    ] as const;

    for (const [slice, loader, reader] of loaders) {
      expect(source).toContain(`const [${slice.slice(0, -1)}LoadState`);
      expect(source).toContain(`const ${loader} = useCallback`);
      expect(source).toContain(`${reader}(getToken, () => current(requestSubject))`);
      expect(source).toContain(`readIsCurrent("${slice}", requestEpoch)`);
      expect(source).toContain(`finishRead("${slice}", requestEpoch)`);
      expect(source).toContain(`${reader}(getToken, () => current(requestSubject), cursor)`);
    }

    expect(source).toContain("const refresh = useCallback(async (initial = false)");
    expect(source).toContain("const loadOlderRequests = async");
    expect(source).toContain("const loadOlderConnections = async");
    expect(source).toContain("const loadOlderConversations = async");
    expect(source).toContain("const loadOlderNotifications = async");
    expect(source).toContain("appendCursorPage(");
    expect(source).toContain("deliveredCursorsRef.current.get");
    expect(source).toContain("onNotice(presentSocialError(error))");
    expect(source).toContain("initialLoadInFlightRef.current.delete");
    expect(source).not.toContain("localStorage");
    expect(source).not.toContain("sessionStorage");
  });
});
