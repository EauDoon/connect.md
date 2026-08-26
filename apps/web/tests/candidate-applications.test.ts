import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  beginCandidateApplicationRead,
  candidateApplicationReadIsCurrent,
  createCandidateApplicationReadState,
  finishCandidateApplicationRead,
} from "../components/candidate-applications";

const source = readFileSync(
  new URL("../components/candidate-applications.tsx", import.meta.url),
  "utf8",
);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("candidate application private-read coordination", () => {
  it("deduplicates initial Strict Mode entry and cursor dispatches", () => {
    const state = createCandidateApplicationReadState();
    const initial = beginCandidateApplicationRead(state, null);

    expect(initial?.kind).toBe("initial");
    expect(beginCandidateApplicationRead(state, null)).toBeNull();
    expect(beginCandidateApplicationRead(state, "cursor-1")).toBeNull();

    finishCandidateApplicationRead(state, initial!);
    const more = beginCandidateApplicationRead(state, "cursor-1");
    expect(more?.kind).toBe("more");
    expect(beginCandidateApplicationRead(state, "cursor-1")).toBeNull();
    finishCandidateApplicationRead(state, more!);
  });

  it("commits the refresh and suppresses a deferred older page", async () => {
    const state = createCandidateApplicationReadState();
    const first = beginCandidateApplicationRead(state, null)!;
    finishCandidateApplicationRead(state, first);
    const older = beginCandidateApplicationRead(state, "cursor-1")!;
    const refresh = beginCandidateApplicationRead(state, null)!;
    const oldPage = deferred<string>();
    const currentPage = deferred<string>();
    const commits: string[] = [];

    const settle = async (
      request: typeof older,
      response: Promise<string>,
    ) => {
      const value = await response;
      if (candidateApplicationReadIsCurrent(state, request)) commits.push(value);
      finishCandidateApplicationRead(state, request);
    };

    const oldTask = settle(older, oldPage.promise);
    const refreshTask = settle(refresh, currentPage.promise);
    currentPage.resolve("fresh");
    await refreshTask;
    oldPage.resolve("stale");
    await oldTask;

    expect(commits).toEqual(["fresh"]);
    expect(candidateApplicationReadIsCurrent(state, older)).toBe(false);
  });

  it("rejects a response after the authenticated subject changes", async () => {
    const state = createCandidateApplicationReadState();
    const request = beginCandidateApplicationRead(state, null)!;
    const response = deferred<string>();
    let currentSubject = "subject-a";
    const commits: string[] = [];

    const task = (async () => {
      const value = await response.promise;
      if (currentSubject === "subject-a" && candidateApplicationReadIsCurrent(state, request)) commits.push(value);
      finishCandidateApplicationRead(state, request);
    })();

    currentSubject = "subject-b";
    response.resolve("subject-a-data");
    await task;

    expect(commits).toEqual([]);
  });

  it("preserves loaded rows on refresh failure and keeps cursor deduplication fail-closed", () => {
    const loadStart = source.indexOf("const load = useCallback");
    const loadEnd = source.indexOf("useEffect(() => { void load(); }, [load]);", loadStart);
    const loadBody = source.slice(loadStart, loadEnd);

    expect(loadBody).toContain("deliveredCursorsRef.current.has(request.cursor)");
    expect(loadBody).toContain("finishCandidateApplicationRead(readStateRef.current, request)");
    expect(loadBody).toContain("appendCursorPage(applicationsRef.current, result, request.cursor, delivered)");
    expect(loadBody).toContain('setLoadState("error"); setLoadError(presentRecruitmentError(error))');
    expect(loadBody).not.toContain("setApplications([])");
  });

  it("guards dispatch and every read settlement without persistence or logging", () => {
    const begin = source.indexOf("const request = beginCandidateApplicationRead");
    const subjectGuard = source.indexOf("if (!isSubjectCurrent(requestSubject)) return;");
    const dispatch = source.indexOf("listMyApplications(getToken, requestIsCurrent, cursor)");
    const staleGuard = source.indexOf("if (!requestIsCurrent()) return;");
    const firstCommit = source.indexOf("applicationsRef.current = result.items");
    const append = source.indexOf("appendCursorPage(applicationsRef.current, result, request.cursor, delivered)");

    expect(begin).toBeGreaterThanOrEqual(0);
    expect(subjectGuard).toBeGreaterThanOrEqual(0);
    expect(subjectGuard).toBeLessThan(begin);
    expect(begin).toBeLessThan(dispatch);
    expect(staleGuard).toBeLessThan(firstCommit);
    expect(staleGuard).toBeLessThan(append);
    expect(source).toContain("<AuthenticatedCandidateApplications key={subject}");
    expect(source).not.toMatch(/localStorage|sessionStorage|console\.(log|warn|error)/);
  });
});
