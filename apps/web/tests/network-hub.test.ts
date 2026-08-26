import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../components/network-hub.tsx", import.meta.url),
  "utf8",
);
const panelSource = readFileSync(
  new URL("../components/network-panels.tsx", import.meta.url),
  "utf8",
);
const readSource = readFileSync(
  new URL("../components/private-network-reads.ts", import.meta.url),
  "utf8",
);

describe("private network lifecycle guards", () => {
  it("remounts private state by authenticated subject", () => {
    expect(source).toContain(
      "<AuthenticatedNetwork key={subject} subject={subject} getToken={getToken} />",
    );
    expect(source).toContain("subjectRef.current = subject;");
    expect(source).toContain("subjectRef.current = null");
  });

  it("deduplicates each initial slice independently and rejects stale responses", () => {
    const loaders = [
      ["requests", "loadRequests", "listConnectionRequestInboxForSubject"],
      ["connections", "loadConnections", "listConnectionsForSubject"],
      ["conversations", "loadConversations", "listConversationsForSubject"],
      ["notifications", "loadNotifications", "listNotificationsForSubject"],
    ] as const;

    for (const [index, [slice, loader, reader]] of loaders.entries()) {
      const start = readSource.indexOf(`const ${loader} = useCallback`);
      const nextLoader = index === loaders.length - 1
        ? "const refresh = useCallback"
        : `const ${loaders[index + 1][1]} = useCallback`;
      const end = readSource.indexOf(nextLoader, start + 1);
      const body = readSource.slice(start, end === -1 ? readSource.length : end);
      expect(body.indexOf(`beginRead("${slice}")`)).toBeLessThan(
        body.indexOf(`${reader}(getToken, () => current(requestSubject))`),
      );
      expect(body).toContain(`initialLoadInFlightRef.current.has("${slice}")`);
      expect(body).toContain(`initialLoadInFlightRef.current.add("${slice}")`);
      expect(body).toContain(`initialLoadInFlightRef.current.delete("${slice}")`);
      expect(body).toContain(`if (!current(requestSubject)) return;`);
      expect(body).toContain(`${reader}(getToken, () => current(requestSubject))`);
      expect(body).toContain(`readIsCurrent("${slice}", requestEpoch)`);
      expect(body).toContain(`finishRead("${slice}", requestEpoch)`);
    }
    expect(readSource).not.toContain("EpochAtRender");
    expect(readSource).toContain("const refresh = useCallback(async (initial = false)");
  });

  it("keeps four slice states independent and guards paginated reads too", () => {
    for (const slice of ["request", "connection", "conversation", "notification"]) {
      expect(readSource).toContain(`const [${slice}LoadState`);
      expect(panelSource).toContain('loadState === "error"');
      expect(panelSource).toContain('loadState === "loaded"');
    }
    expect(readSource).toContain("listConnectionRequestInboxForSubject(getToken, () => current(requestSubject), cursor)");
    expect(readSource).toContain("listConnectionsForSubject(getToken, () => current(requestSubject), cursor)");
    expect(readSource).toContain("listConversationsForSubject(getToken, () => current(requestSubject), cursor)");
    expect(readSource).toContain("listNotificationsForSubject(getToken, () => current(requestSubject), cursor)");
    expect(readSource).not.toContain("listConnectionRequestInbox(getToken");
    expect(readSource).not.toContain("listConnections(getToken");
    expect(readSource).not.toContain("listConversations(getToken");
    expect(readSource).not.toContain("listNotifications(getToken");
  });

  it("coordinates refresh, pagination, and dependent writes per slice", () => {
    const slices = [
      ["Requests", "requests", "requestCursorRef", "requestsRef", "setRequests"],
      ["Connections", "connections", "connectionCursorRef", "connectionsRef", "setConnections"],
      ["Conversations", "conversations", "conversationCursorRef", "conversationsRef", "setConversations"],
      ["Notifications", "notifications", "notificationCursorRef", "notificationsRef", "setNotifications"],
    ] as const;

    for (const [label, slice, cursorRef, itemsRef, setter] of slices) {
      const start = readSource.indexOf(`const loadOlder${label} = async`);
      const next = readSource.indexOf("const loadOlder", start + 1);
      const end = next === -1 ? readSource.length : next;
      const body = readSource.slice(start, end);
      expect(body).toContain(`const cursor = ${cursorRef}.current`);
      expect(body).toContain(`!readAllowsDependentAction("${slice}")`);
      expect(body).toContain(`moreInFlightRef.current.has("${slice}")`);
      expect(body).toContain(`deliveredCursorsRef.current.get("${slice}")`);
      expect(body).toContain(`readIsCurrent("${slice}", requestEpoch)`);
      expect(body.indexOf(`readIsCurrent("${slice}", requestEpoch)`)).toBeLessThan(
        body.indexOf("appendCursorPage("),
      );
      expect(body.indexOf(`${itemsRef}.current = next.items`)).toBeLessThan(
        body.indexOf(`${setter}(next.items)`),
      );
    }

    expect(source).toContain("requestsRef.current.some((item) => item.id === request.id");
    expect(source).toContain("connectionsRef.current.some((item) => item.id === connection.id)");
    expect(source).toContain("notificationsRef.current.find((item) => item.id === notification.id)");
    expect(source).toContain("const busyRef = useRef<string | null>(null)");
    expect(source).toContain("if (!beginBusy(busySlot)) return;");
  });

  it("marks recognized notifications read before navigating to a literal private hub", () => {
    const start = source.indexOf("const readNotification = async");
    const end = source.indexOf("const loadOlderRequests = async", start);
    const body = source.slice(start, end);

    expect(body).toContain("destination: NotificationHubAction | null = null");
    expect(body).toContain("const updated = await markNotificationRead");
    expect(body).toContain("if (updated.id !== currentNotification.id || !updated.readAt)");
    expect(body.indexOf("const updated = await markNotificationRead")).toBeLessThan(body.indexOf("router.push(destination.href)"));
    expect(body).toContain("void loadNotifications();");
    expect(body).toContain("settleAttempt(busySlot, attempt, error)");
    expect(body).toContain("The notification action may have completed. Retry the unchanged action to recover the same result.");
    expect(body).not.toContain("notification.resourceType");
    expect(body).not.toContain("notification.id)`");

    const notificationRows = panelSource.slice(panelSource.indexOf("{notifications.map((notification)"), panelSource.indexOf("{loadState === \"error\" && notifications.length > 0"));
    expect(notificationRows).toContain("const action = notificationHubAction(notification);");
    expect(notificationRows).toContain("onClick={() => onRead(notification, action)}");
    expect(notificationRows).toContain("(!action && Boolean(notification.readAt))");
    expect(notificationRows).not.toContain("resourceType");

    const notificationLabels = panelSource.slice(panelSource.indexOf("function notificationLabel(type: string)"));
    expect(notificationLabels).toContain('if (type === "application.under_review") return "Application under review";');
    expect(notificationLabels).toContain('if (type === "application.accepted" || type === "application.rejected")');
    expect(notificationLabels).toContain('return "Application decision available";');
  });

  it("makes a newer same-subject read authoritative before either promise resolves", async () => {
    const {
      beginPrivateNetworkRead,
      createPrivateNetworkReadEpoch,
      finishPrivateNetworkRead,
      markPrivateNetworkReadReady,
      privateNetworkReadAllowsDependentAction,
      privateNetworkReadIsCurrent,
    } = await import("../components/network-hub");
    const state = createPrivateNetworkReadEpoch();
    const commits: string[] = [];
    const deferred = () => {
      let resolve!: (value: string) => void;
      const promise = new Promise<string>((done) => { resolve = done; });
      return { promise, resolve };
    };
    const oldRead = deferred();
    const currentRead = deferred();
    const run = async (value: Promise<string>) => {
      const requestEpoch = beginPrivateNetworkRead(state);
      const result = await value;
      if (privateNetworkReadIsCurrent(state, requestEpoch)) {
        commits.push(result);
        markPrivateNetworkReadReady(state, requestEpoch);
      }
      finishPrivateNetworkRead(state, requestEpoch);
    };

    const oldTask = run(oldRead.promise);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(false);
    const currentTask = run(currentRead.promise);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(false);
    currentRead.resolve("current");
    await currentTask;
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(true);
    oldRead.resolve("stale");
    await oldTask;

    expect(commits).toEqual(["current"]);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(true);
  });

  it("keeps retained rows visible but blocks dependent actions after a current refresh fails", async () => {
    const {
      beginPrivateNetworkRead,
      createPrivateNetworkReadEpoch,
      finishPrivateNetworkRead,
      markPrivateNetworkReadReady,
      privateNetworkReadAllowsDependentAction,
    } = await import("../components/network-hub");
    const state = createPrivateNetworkReadEpoch();
    const firstEpoch = beginPrivateNetworkRead(state);
    markPrivateNetworkReadReady(state, firstEpoch);
    finishPrivateNetworkRead(state, firstEpoch);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(true);

    const failedRefreshEpoch = beginPrivateNetworkRead(state);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(false);
    finishPrivateNetworkRead(state, failedRefreshEpoch);
    expect(privateNetworkReadAllowsDependentAction(state)).toBe(false);

    for (const label of [
      "Connections could not be refreshed",
      "Connection requests could not be refreshed",
      "Conversations could not be refreshed",
      "Notifications could not be refreshed",
    ]) {
      expect(panelSource).toContain(`label="${label}"`);
    }
  });
});
