import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  claimConversationCursor,
  claimConversationPrimary,
  claimConversationSend,
  createConversationReadCoordinator,
  isCurrentConversationRead,
  releaseSendClaimBeforeRefresh,
  releaseConversationOperation,
  resetConversationReadCoordinator,
  type ConversationOperationClaim,
} from "@/components/conversation-thread";

const source = readFileSync(
  new URL("../components/conversation-thread.tsx", import.meta.url),
  "utf8",
);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

async function applyRead<T>(
  coordinator: ReturnType<typeof createConversationReadCoordinator>,
  claim: ConversationOperationClaim,
  response: Promise<T>,
  apply: (value: T) => void,
): Promise<void> {
  try {
    const value = await response;
    if (isCurrentConversationRead(coordinator, claim)) apply(value);
  } finally {
    releaseConversationOperation(coordinator, claim);
  }
}

describe("private conversation recovery", () => {
  it("does not append a pagination response after a newer refresh wins", async () => {
    const coordinator = createConversationReadCoordinator("human-a", "conversation-a");
    const olderCursor = claimConversationCursor(
      coordinator,
      "human-a",
      "conversation-a",
    );
    expect(olderCursor).not.toBeNull();
    const cursorResponse = deferred<string[]>();
    let messages = ["retained"];
    const cursorTask = applyRead(
      coordinator,
      olderCursor!,
      cursorResponse.promise,
      (items) => {
        messages = [...messages, ...items];
      },
    );

    const refresh = claimConversationPrimary(
      coordinator,
      "human-a",
      "conversation-a",
    );
    expect(refresh).not.toBeNull();
    const refreshResponse = deferred<string[]>();
    const refreshTask = applyRead(
      coordinator,
      refresh!,
      refreshResponse.promise,
      (items) => {
        messages = items;
      },
    );

    cursorResponse.resolve(["stale-page"]);
    await cursorTask;
    expect(messages).toEqual(["retained"]);
    refreshResponse.resolve(["fresh-page"]);
    await refreshTask;
    expect(messages).toEqual(["fresh-page"]);
  });

  it("invalidates an in-flight read when its subject or conversation scope changes", async () => {
    const coordinator = createConversationReadCoordinator("human-a", "conversation-a");
    const oldClaim = claimConversationPrimary(
      coordinator,
      "human-a",
      "conversation-a",
    );
    expect(oldClaim).not.toBeNull();
    const oldResponse = deferred<string[]>();
    let messages: string[] = [];
    const oldTask = applyRead(
      coordinator,
      oldClaim!,
      oldResponse.promise,
      (items) => {
        messages = items;
      },
    );

    resetConversationReadCoordinator(
      coordinator,
      "human-b",
      "conversation-b",
    );
    oldResponse.resolve(["private-old-message"]);
    await oldTask;

    expect(messages).toEqual([]);
    expect(coordinator.primaryClaimId).toBeNull();
  });

  it("does not let a stale completion release or replace a newer primary read", async () => {
    const coordinator = createConversationReadCoordinator("human-a", "conversation-a");
    const oldClaim = claimConversationPrimary(
      coordinator,
      "human-a",
      "conversation-a",
    );
    const oldResponse = deferred<string[]>();
    let messages = ["retained"];
    const oldTask = applyRead(
      coordinator,
      oldClaim!,
      oldResponse.promise,
      (items) => {
        messages = items;
      },
    );

    resetConversationReadCoordinator(
      coordinator,
      "human-a",
      "conversation-a",
    );
    const currentClaim = claimConversationPrimary(
      coordinator,
      "human-a",
      "conversation-a",
    );
    expect(currentClaim).not.toBeNull();
    oldResponse.resolve(["stale"]);
    await oldTask;
    expect(coordinator.primaryClaimId).toBe(currentClaim!.id);
    expect(messages).toEqual(["retained"]);

    const currentResponse = deferred<string[]>();
    const currentTask = applyRead(
      coordinator,
      currentClaim!,
      currentResponse.promise,
      (items) => {
        messages = items;
      },
    );
    currentResponse.resolve(["current"]);
    await currentTask;
    expect(messages).toEqual(["current"]);
  });

  it("rejects same-tick duplicate cursor and send claims before another dispatch can start", () => {
    const coordinator = createConversationReadCoordinator("human-a", "conversation-a");
    const firstCursor = claimConversationCursor(
      coordinator,
      "human-a",
      "conversation-a",
    );
    expect(firstCursor).not.toBeNull();
    expect(
      claimConversationCursor(coordinator, "human-a", "conversation-a"),
    ).toBeNull();
    expect(
      claimConversationSend(coordinator, "human-a", "conversation-a"),
    ).toBeNull();
    expect(releaseConversationOperation(coordinator, firstCursor!)).toBe(true);

    const firstSend = claimConversationSend(
      coordinator,
      "human-a",
      "conversation-a",
    );
    expect(firstSend).not.toBeNull();
    expect(
      claimConversationSend(coordinator, "human-a", "conversation-a"),
    ).toBeNull();
  });

  it("releases the send claim before the supplied primary refresh callback", async () => {
    const coordinator = createConversationReadCoordinator("human-a", "conversation-a");
    const send = claimConversationSend(coordinator, "human-a", "conversation-a");
    expect(send).not.toBeNull();
    let released = false;
    let refresh: ConversationOperationClaim | null = null;

    await expect(releaseSendClaimBeforeRefresh(
      coordinator,
      send!,
      () => true,
      async () => {
        refresh = claimConversationPrimary(coordinator, "human-a", "conversation-a");
        if (refresh) releaseConversationOperation(coordinator, refresh);
      },
      () => { released = true; },
    )).resolves.toBe(true);

    expect(released).toBe(true);
    expect(refresh).not.toBeNull();
    expect(coordinator.interactionClaimId).toBeNull();
  });

  it("keeps retained messages gated after a current refresh fails until a current load succeeds", async () => {
    const coordinator = createConversationReadCoordinator("human-a", "conversation-a");
    let messages = ["retained"];
    let loadState: "loading" | "loaded" | "error" = "loaded";
    const failedRefresh = claimConversationPrimary(
      coordinator,
      "human-a",
      "conversation-a",
    );
    expect(failedRefresh).not.toBeNull();
    const failedResponse = deferred<string[]>();
    const failureTask = (async () => {
      loadState = "loading";
      try {
        await failedResponse.promise;
      } catch {
        if (isCurrentConversationRead(coordinator, failedRefresh!)) {
          loadState = "error";
        }
      } finally {
        releaseConversationOperation(coordinator, failedRefresh!);
      }
    })();

    failedResponse.reject(new Error("temporary failure"));
    await failureTask;
    expect(messages).toEqual(["retained"]);
    expect(loadState === "loaded").toBe(false);

    const recovery = claimConversationPrimary(
      coordinator,
      "human-a",
      "conversation-a",
    );
    expect(recovery).not.toBeNull();
    const recoveryResponse = deferred<string[]>();
    const recoveryTask = applyRead(
      coordinator,
      recovery!,
      recoveryResponse.promise,
      (items) => {
        messages = items;
        loadState = "loaded";
      },
    );
    recoveryResponse.resolve(["current-message"]);
    await recoveryTask;
    expect(messages).toEqual(["current-message"]);
    expect(loadState === "loaded").toBe(true);
  });

  it("binds the coordinator to subject and conversation guards without exposing private content", () => {
    const loadStart = source.indexOf("const load = useCallback(");
    const loadEnd = source.indexOf("useEffect(() => {", loadStart);
    const loadSource = source.slice(loadStart, loadEnd);
    const submitStart = source.indexOf("const submit = async");
    const submitEnd = source.indexOf("return (", submitStart);
    const submitSource = source.slice(submitStart, submitEnd);

    expect(source).toContain('key={`${subject}:${conversationId}`}');
    expect(source).toContain("claimConversationPrimary(");
    expect(source).toContain("claimConversationCursor(");
    expect(source).toContain("claimConversationSend(");
    expect(source).toContain("isCurrentConversationRead(coordinator, claim)");
    expect(source).toContain("releaseConversationOperation(coordinator, claim)");
    expect(loadSource.indexOf("claimConversationCursor(")).toBeLessThan(
      loadSource.indexOf("listMessagesForSubject("),
    );
    expect(submitSource.indexOf("claimConversationSend(")).toBeLessThan(
      submitSource.indexOf("sendMessage("),
    );
    expect(source).toContain('disabled={busy || loadState !== "loaded"}');
    expect(source).toContain('disabled={busy || loadState !== "loaded" || !draft.trim()}');
    expect(source).not.toMatch(/localStorage|sessionStorage|console\.|URLSearchParams/u);
    expect(source).toContain("<MarkdownPreview");
    expect(source).toContain("images and raw HTML");
  });

  it("routes production submit through the release-before-refresh helper", () => {
    const submitStart = source.indexOf("const submit = async");
    const submitEnd = source.indexOf("return (", submitStart);
    const submitSource = source.slice(submitStart, submitEnd);

    expect(submitSource).toContain("releaseSendClaimBeforeRefresh(");
    expect(submitSource).toContain("() => { sendClaimReleased = true; }");
    expect(submitSource).toContain("if (!refreshed) return;");
    expect(submitSource).not.toContain("await load();");
    expect(submitSource).toContain("if (!sendClaimReleased) sendClaimReleased = releaseConversationOperation(coordinator, claim);");
  });

  it("keeps private-network back navigation as a full-size native link", () => {
    const backLink = source.match(/<Link\s+href="\/network"\s+className="([^"]+)"/u);

    expect(backLink).not.toBeNull();
    expect(backLink?.[1]?.split(/\s+/u)).toEqual(expect.arrayContaining([
      "inline-flex",
      "min-h-11",
      "items-center",
      "px-2",
    ]));
    expect(source).toContain("<ArrowLeft className=\"size-4\" aria-hidden />");
    expect(source).toContain("Private network");
  });
});
