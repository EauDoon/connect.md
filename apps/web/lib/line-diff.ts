/**
 * Bounded line diff for the guest comparison views.
 *
 * Lines are split on "\n" exactly as compareDrafts splits them, so a change
 * to the final newline is a real change. The common prefix and suffix are
 * trimmed first; Myers' O((N+M)D) algorithm then finds a minimal edit script
 * for the middle. Work is bounded by an edit-distance limit and a step
 * budget, both checked as the search runs. Past either bound the result is
 * marked inexact and callers fall back to the changed-region comparison.
 */

export type DiffLine = { type: "context" | "removed" | "added"; text: string; beforeLine?: number; afterLine?: number };
export type DiffHunk = { beforeStart: number; beforeCount: number; afterStart: number; afterCount: number; lines: DiffLine[] };
export type LineDiff = {
  identical: boolean;
  /** False when the bounds were reached and no edit script was produced. */
  exact: boolean;
  /** Hunks to display, possibly cut short by the display bounds. */
  hunks: DiffHunk[];
  /** Every hunk in the edit script, including any not displayed. */
  totalHunks: number;
  addedLines: number;
  removedLines: number;
  /** True when the display bounds cut lines or hunks from `hunks`. */
  truncated: boolean;
};

export const LINE_DIFF_LIMITS = {
  maxEdits: 1000,
  maxSteps: 4_000_000,
  displayLines: 200,
  displayCharacters: 12_000,
} as const;

type Operation = { type: "equal" | "removed" | "added"; before: number; after: number };

function middleEdits(previous: number[], current: number[], maxSteps: number, maxEdits: number): Operation[] | null {
  const n = previous.length;
  const m = current.length;
  if (n === 0) return current.map((_, after) => ({ type: "added", before: 0, after }));
  if (m === 0) return previous.map((_, before) => ({ type: "removed", before, after: 0 }));
  const limit = Math.min(n + m, maxEdits);
  if (Math.abs(n - m) > limit) return null;
  const offset = limit + 1;
  const frontier = new Int32Array(2 * limit + 3);
  const trace: Int32Array[] = [];
  let steps = 0;
  for (let d = 0; d <= limit; d++) {
    // Keep only the band d needs during backtracking: k in [-d-1, d+1].
    trace.push(frontier.slice(offset - d - 1, offset + d + 2));
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && frontier[offset + k - 1] < frontier[offset + k + 1])
        ? frontier[offset + k + 1]
        : frontier[offset + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && previous[x] === current[y]) { x++; y++; steps++; }
      steps++;
      if (steps > maxSteps) return null;
      frontier[offset + k] = x;
      if (x >= n && y >= m) return backtrack(trace, n, m);
    }
  }
  return null;
}

function backtrack(trace: Int32Array[], n: number, m: number): Operation[] {
  const reversed: Operation[] = [];
  let x = n;
  let y = m;
  for (let d = trace.length - 1; d >= 0; d--) {
    const band = trace[d];
    const at = (k: number) => band[k + d + 1];
    const k = x - y;
    const previousK = k === -d || (k !== d && at(k - 1) < at(k + 1)) ? k + 1 : k - 1;
    const previousX = at(previousK);
    const previousY = previousX - previousK;
    while (x > previousX && y > previousY) {
      x--; y--;
      reversed.push({ type: "equal", before: x, after: y });
    }
    if (d > 0) {
      if (x === previousX) reversed.push({ type: "added", before: x, after: previousY });
      else reversed.push({ type: "removed", before: previousX, after: y });
    }
    x = previousX;
    y = previousY;
  }
  return reversed.reverse();
}

function interned(lines: string[], table: Map<string, number>) {
  return lines.map((line) => {
    let id = table.get(line);
    if (id === undefined) { id = table.size; table.set(line, id); }
    return id;
  });
}

/** Compare two sources line by line. Inputs are expected to be bounded drafts (128 KiB). */
export function diffLines(before: string, after: string, { context = 3 }: { context?: number } = {}): LineDiff {
  if (before === after) return { identical: true, exact: true, hunks: [], totalHunks: 0, addedLines: 0, removedLines: 0, truncated: false };
  const previous = before.split("\n");
  const current = after.split("\n");
  let prefix = 0;
  while (prefix < previous.length && prefix < current.length && previous[prefix] === current[prefix]) prefix++;
  let suffix = 0;
  while (suffix < previous.length - prefix && suffix < current.length - prefix
    && previous[previous.length - suffix - 1] === current[current.length - suffix - 1]) suffix++;
  const table = new Map<string, number>();
  const middle = middleEdits(
    interned(previous.slice(prefix, previous.length - suffix), table),
    interned(current.slice(prefix, current.length - suffix), table),
    LINE_DIFF_LIMITS.maxSteps,
    LINE_DIFF_LIMITS.maxEdits,
  );
  if (middle === null) return { identical: false, exact: false, hunks: [], totalHunks: 0, addedLines: 0, removedLines: 0, truncated: false };

  const operations: Operation[] = [];
  for (let index = 0; index < prefix; index++) operations.push({ type: "equal", before: index, after: index });
  for (const operation of middle) operations.push({ ...operation, before: operation.before + prefix, after: operation.after + prefix });
  for (let index = suffix; index > 0; index--) operations.push({ type: "equal", before: previous.length - index, after: current.length - index });

  const changed = operations.flatMap((operation, index) => operation.type === "equal" ? [] : [index]);
  const groups: Array<[number, number]> = [];
  for (const index of changed) {
    const last = groups.at(-1);
    if (last && index - last[1] - 1 <= 2 * context) last[1] = index;
    else groups.push([index, index]);
  }

  const hunks: DiffHunk[] = [];
  let shownLines = 0;
  let shownCharacters = 0;
  let truncated = false;
  for (const [first, last] of groups) {
    if (truncated) break;
    const start = Math.max(0, first - context);
    const end = Math.min(operations.length - 1, last + context);
    const opening = operations[start];
    const hunk: DiffHunk = {
      beforeStart: opening.before + 1,
      beforeCount: 0,
      afterStart: opening.after + 1,
      afterCount: 0,
      lines: [],
    };
    for (let index = start; index <= end; index++) {
      const operation = operations[index];
      if (operation.type !== "added") hunk.beforeCount++;
      if (operation.type !== "removed") hunk.afterCount++;
      if (truncated) continue;
      const text = operation.type === "added" ? current[operation.after] : previous[operation.before];
      if (shownLines >= LINE_DIFF_LIMITS.displayLines || shownCharacters + text.length > LINE_DIFF_LIMITS.displayCharacters) {
        truncated = true;
        continue;
      }
      shownLines++;
      shownCharacters += text.length;
      hunk.lines.push(operation.type === "equal"
        ? { type: "context", text, beforeLine: operation.before + 1, afterLine: operation.after + 1 }
        : operation.type === "removed"
          ? { type: "removed", text, beforeLine: operation.before + 1 }
          : { type: "added", text, afterLine: operation.after + 1 });
    }
    if (hunk.lines.length) hunks.push(hunk);
  }
  return {
    identical: false,
    exact: true,
    hunks,
    totalHunks: groups.length,
    addedLines: operations.filter((operation) => operation.type === "added").length,
    removedLines: operations.filter((operation) => operation.type === "removed").length,
    truncated,
  };
}

