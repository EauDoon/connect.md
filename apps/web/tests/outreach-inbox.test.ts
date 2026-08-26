import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import { appendCursorPage as appendPrivateCursorPage } from "../lib/cursor-page";
import { beginLogicalMutationAttempt } from "../lib/logical-mutation";

const source = readFileSync(new URL("../components/outreach-inbox.tsx", import.meta.url), "utf8");

describe("outreach inbox race safety", () => {
  it("accepts only a validated linked profile-handle prefill and leaves sending explicit", () => {
    expect(source).toContain("safePrefillProfileHandle");
    expect(source).toContain("buildInboxContactReturnPath(safePrefillProfileHandle)");
    expect(source).toContain("useState(initialTarget ?? \"\")");
    expect(source).toContain("if (!isCanonicalProfileHandle(normalizedTarget))");
    expect(source).toContain("setTarget(\"\"); setPurpose(\"\"); setBody(\"\");");
    const initialLoad = source.slice(source.indexOf("const loadPolicy"), source.indexOf("async function send()"));
    expect(initialLoad).not.toContain("sendContactRequest");
    const sendStart = source.indexOf("async function send()");
    const sendSource = source.slice(sendStart, source.indexOf("return <div", sendStart));
    expect(sendSource.indexOf("sendContactRequest")).toBeGreaterThan(sendSource.indexOf("if (busy || !target.trim() || !purpose.trim() || !body.trim()) return;"));
  });

  it("deduplicates each Strict Mode initial policy and inbox dispatch independently", () => {
    expect(source).toContain("initialPolicyLoadStartedRef");
    expect(source).toContain("initialPolicyLoadInFlightRef");
    expect(source).toContain("initialInboxLoadStartedRef");
    expect(source).toContain("initialInboxLoadInFlightRef");
    expect(source).toContain("refresh(true)");
    expect(source).toContain("loadPolicy(initial)");
    expect(source).toContain("loadInbox(initial)");
    expect(source).toContain("if (initialPolicyLoadStartedRef.current || initialPolicyLoadInFlightRef.current) return;");
    expect(source).toContain("if (initialInboxLoadStartedRef.current || initialInboxLoadInFlightRef.current) return;");
  });

  it("suppresses older same-subject policy and inbox responses", () => {
    expect(source).toContain("policyReadEpochRef");
    expect(source).toContain("inboxReadEpochRef");
    expect(source).toContain("privateReadIsCurrent(policyReadEpochRef.current, requestEpoch)");
    expect(source).toContain("privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)");
    expect(source).toContain("key={`${subject}:${safePrefillProfileHandle ?? \"none\"}`}");
    expect(source).toContain("!isSubjectCurrent()");
  });

  it("guards policy and action mutation completions by their captured subject", () => {
    const policyStart = source.indexOf("async function savePolicy()");
    const policyEnd = source.indexOf("\n  async function act(", policyStart);
    const policySource = source.slice(policyStart, policyEnd);
    const policyTry = policySource.indexOf("try {");
    const policySubject = policySource.indexOf("const requestSubject = subject;");
    const policySuccessGuard = policySource.indexOf("if (!requestIsCurrent()) return;");
    const policySuccessCleanup = policySource.indexOf('mutationAttemptsRef.current.delete("policy")');
    const policyCatch = policySource.indexOf("} catch");
    const policyCatchGuard = policySource.indexOf("if (!requestIsCurrent()) return;", policyCatch);
    const policyCatchMessage = policySource.indexOf("setMessage", policyCatch);
    const policyFinallyGuard = policySource.indexOf("if (requestIsCurrent()) setBusy(null);");
    expect(policySubject).toBeGreaterThan(-1);
    expect(policySubject).toBeLessThan(policyTry);
    expect(policySuccessGuard).toBeLessThan(policySuccessCleanup);
    expect(policyCatchGuard).toBeLessThan(policyCatchMessage);
    expect(policyFinallyGuard).toBeGreaterThan(policyCatch);

    const actionStart = source.indexOf("async function act(");
    const actionEnd = source.indexOf("\n  async function send(", actionStart);
    const actionSource = source.slice(actionStart, actionEnd);
    const actionTry = actionSource.indexOf("try {");
    const actionSubject = actionSource.indexOf("const requestSubject = subject;");
    const actionSuccessGuard = actionSource.indexOf("if (!requestIsCurrent() || !privateReadIsCurrent");
    const actionSuccessMutation = actionSource.indexOf("setThreads");
    const actionCatch = actionSource.indexOf("} catch");
    const actionCatchGuard = actionSource.indexOf("if (!requestIsCurrent()) return;", actionCatch);
    const actionCatchMessage = actionSource.indexOf("setMessage", actionCatch);
    const actionFinallyGuard = actionSource.indexOf("if (requestIsCurrent()) setBusy(null);");
    expect(actionSubject).toBeGreaterThan(-1);
    expect(actionSubject).toBeLessThan(actionTry);
    expect(actionSuccessGuard).toBeLessThan(actionSuccessMutation);
    expect(actionCatchGuard).toBeLessThan(actionCatchMessage);
    expect(actionFinallyGuard).toBeGreaterThan(actionCatch);
  });

  it("invalidates load-more responses when a refresh supersedes them", () => {
    const refreshStart = source.indexOf("const loadInbox = useCallback(");
    const refreshSource = source.slice(refreshStart, source.indexOf("const refresh = useCallback", refreshStart));
    expect(refreshSource).toContain("beginPrivateRead(inboxReadEpochRef.current)");
    expect(refreshSource.indexOf("beginPrivateRead(inboxReadEpochRef.current)")).toBeLessThan(refreshSource.indexOf("await listOutreachForSubject"));
    expect(refreshSource).toContain("finishPrivateRead(inboxReadEpochRef.current, requestEpoch)");
    const loadMoreStart = source.indexOf("async function loadOlder()");
    const loadMoreSource = source.slice(loadMoreStart, source.indexOf("async function savePolicy()", loadMoreStart));
    expect(loadMoreSource).toContain("privateReadAllowsDependentWrite(inboxReadEpochRef.current)");
    expect(loadMoreSource).toContain("const requestEpoch = inboxReadEpochRef.current.current;");
    expect(loadMoreSource).toContain("privateReadIsCurrent(inboxReadEpochRef.current, requestEpoch)");
    expect(loadMoreSource).toContain("moreInFlightRef.current");
    expect(loadMoreSource).toContain("setMoreLoading(false)");
    expect(loadMoreSource.indexOf("if (!isSubjectCurrent()) return;")).toBeLessThan(loadMoreSource.indexOf("deliveredCursorsRef.current.has(cursor)"));
    expect(source).not.toMatch(/setThreads\(\[\]/u);
    expect(source).toContain("setThreads(page.threads)");
  });

  it("keeps a newer refresh in flight when an older request settles", async () => {
    vi.mock("@/components/auth-provider", () => ({ useConnectmdAuth: vi.fn() }));
    const { beginPrivateRead, createPrivateReadEpoch, finishPrivateRead, privateReadAllowsDependentWrite } = await import("../components/outreach-inbox");
    const state = createPrivateReadEpoch();
    let releaseFirst!: () => void;
    const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
    let releaseSecond!: () => void;
    const secondGate = new Promise<void>((resolve) => { releaseSecond = resolve; });
    const run = async (gate: Promise<void>) => {
      const requestEpoch = beginPrivateRead(state);
      await gate;
      finishPrivateRead(state, requestEpoch);
    };
    const first = run(firstGate);
    const second = run(secondGate);
    expect(state).toEqual({ current: 2, inFlight: true });
    expect(privateReadAllowsDependentWrite(state)).toBe(false);
    releaseFirst();
    await first;
    expect(state).toEqual({ current: 2, inFlight: true });
    releaseSecond();
    await second;
    expect(state).toEqual({ current: 2, inFlight: false });
    expect(privateReadAllowsDependentWrite(state)).toBe(true);
  });

  it("blocks policy saves and inbox actions synchronously once a refresh begins", () => {
    expect(source).toContain("!privateReadAllowsDependentWrite(policyReadEpochRef.current)");
    expect(source).toContain("!privateReadAllowsDependentWrite(inboxReadEpochRef.current)");
  });

  it("freezes the displayed policy and report intent during its mutation", () => {
    expect(source).toContain('const policyMutationInFlight = busy === "policy";');
    expect(source).toContain('const reportMutationInFlight = reportingId !== null && busy === reportingId;');
    expect(source).toContain("disabled={policyMutationInFlight}");
    expect(source).toContain("disabled={policyMutationInFlight || policy.mode === \"closed\"}");
    expect(source).toContain("disabled={reportMutationInFlight}");
    expect(source).toContain("disabled={reportMutationInFlight || busy !== null || inboxLoadState !== \"loaded\" || !reportReason.trim()}");
    expect(source).toContain("disabled={reportMutationInFlight || busy !== null}");
  });

  it("retains a lost-ack policy key only while its exact policy precondition stays unchanged", () => {
    const policyIntent = { operation: "update-contact-policy", mode: "request", allowAgentMessages: true, dailyRequestLimit: 5, etag: '"policy-4"' };
    const policy = beginLogicalMutationAttempt(null, "subject-1", policyIntent, () => "policy-key-1");
    expect(beginLogicalMutationAttempt(policy, "subject-1", { ...policyIntent }, () => "policy-key-2").idempotencyKey).toBe("policy-key-1");
    expect(beginLogicalMutationAttempt(policy, "subject-1", { ...policyIntent, mode: "closed" }, () => "policy-key-3").idempotencyKey).toBe("policy-key-3");
    expect(beginLogicalMutationAttempt(policy, "subject-1", { ...policyIntent, allowAgentMessages: false }, () => "policy-key-4").idempotencyKey).toBe("policy-key-4");
    expect(beginLogicalMutationAttempt(policy, "subject-1", { ...policyIntent, dailyRequestLimit: 6 }, () => "policy-key-5").idempotencyKey).toBe("policy-key-5");
    expect(beginLogicalMutationAttempt(policy, "subject-1", { ...policyIntent, etag: '"policy-5"' }, () => "policy-key-6").idempotencyKey).toBe("policy-key-6");

    const decisionIntent = { operation: "act-on-outreach", threadId: "request-1", action: "reported", reason: "Impersonation" };
    const decision = beginLogicalMutationAttempt(null, "subject-1", decisionIntent, () => "decision-key-1");
    expect(beginLogicalMutationAttempt(decision, "subject-1", { ...decisionIntent }, () => "decision-key-2").idempotencyKey).toBe("decision-key-1");
    expect(beginLogicalMutationAttempt(decision, "subject-1", { ...decisionIntent, threadId: "request-2" }, () => "decision-key-3").idempotencyKey).toBe("decision-key-3");
    expect(beginLogicalMutationAttempt(decision, "subject-1", { ...decisionIntent, action: "rejected" }, () => "decision-key-4").idempotencyKey).toBe("decision-key-4");
    expect(beginLogicalMutationAttempt(decision, "subject-1", { ...decisionIntent, reason: "Different reason" }, () => "decision-key-5").idempotencyKey).toBe("decision-key-5");
    expect(beginLogicalMutationAttempt(decision, "subject-2", { ...decisionIntent }, () => "decision-key-6").idempotencyKey).toBe("decision-key-6");
  });

  it("clears contact attempts only after success or definitive failure", () => {
    expect(source).toContain('mutationAttemptsRef.current.delete("policy")');
    expect(source).toContain("mutationAttemptsRef.current.delete(slot)");
    expect(source).toContain('settleAttempt("policy", attempt, error)');
    expect(source).toContain("settleAttempt(slot, attempt, error)");
  });

  it("keeps truthful empty/error states and disables dependent writes until loaded", () => {
    expect(source).toContain('inboxLoadState === "loading" && threads.length === 0');
    expect(source).toContain('inboxLoadState === "error" && threads.length === 0');
    expect(source).toContain('inboxLoadState === "loaded" && threads.length === 0');
    expect(source).toContain("policyHasLoaded");
    expect(source).toContain('policyLoadState !== "loaded"');
    expect(source).toContain('inboxLoadState !== "loaded"');
    expect(source).toContain('label="Contact requests could not be refreshed"');
  });

  it("keeps pagination cursor-bound, monotonic, and deduplicated", () => {
    const first = { id: "request-1" };
    expect(appendPrivateCursorPage([first], { items: [first, { id: "request-2" }], nextCursor: "cursor-2" }, "cursor-1", new Set())).toEqual({
      items: [first, { id: "request-2" }],
      nextCursor: "cursor-2",
      cursorDidNotProgress: false,
    });
    expect(appendPrivateCursorPage([first], { items: [{ id: "request-2" }], nextCursor: "cursor-2" }, "cursor-1", new Set(["cursor-2"]))).toEqual({
      items: [first, { id: "request-2" }],
      nextCursor: null,
      cursorDidNotProgress: true,
    });
  });
});
