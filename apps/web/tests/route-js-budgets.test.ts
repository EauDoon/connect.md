import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// @ts-expect-error The checker is intentionally dependency-free Node ESM JavaScript.
import { checkRouteBudgets, ROUTE_BUDGETS } from "../scripts/check-route-js-budgets.mjs";

const temporaryRoots: string[] = [];

type Failure = Error & { issues?: string[] };

function captureFailure(action: () => unknown): Failure {
  try {
    action();
  } catch (error) {
    if (error instanceof Error) return error as Failure;
    throw new Error("Expected a route-budget failure.");
  }
  throw new Error("Expected route-budget validation to fail.");
}

function fixture(pages: Record<string, string[]>, assets: Record<string, number>) {
  const root = mkdtempSync(join(tmpdir(), "connectmd-route-budget-"));
  temporaryRoots.push(root);
  const buildRoot = join(root, ".next");
  mkdirSync(join(buildRoot, "static", "chunks"), { recursive: true });
  writeFileSync(join(buildRoot, "app-build-manifest.json"), JSON.stringify({ pages }));
  for (const [asset, size] of Object.entries(assets)) {
    const target = join(buildRoot, ...asset.split("/"));
    mkdirSync(resolve(target, ".."), { recursive: true });
    writeFileSync(target, Buffer.alloc(size, 0x61));
  }
  return root;
}

afterEach(() => {
  while (temporaryRoots.length > 0) rmSync(temporaryRoots.pop()!, { force: true, recursive: true });
});

describe("production route JavaScript budgets", () => {
  it("keeps an explicit budget for every current launch-route shape", () => {
    expect(Object.keys(ROUTE_BUDGETS).sort()).toEqual([
      "/_not-found/page",
      "/account/page",
      "/agent-directory/page",
      "/agents/[handle]/page",
      "/agents/page",
      "/appeal-review/page",
      "/applications/page",
      "/discover/page",
      "/employer/page",
      "/feed/page",
      "/human/page",
      "/inbox/page",
      "/jobs/[organizationSlug]/[jobSlug]/page",
      "/jobs/page",
      "/md/page",
      "/messages/[conversationId]/page",
      "/moderation-review/page",
      "/moderation/page",
      "/network/page",
      "/organizations/[slug]/page",
      "/organizations/page",
      "/p/[handle]/page",
      "/p/[handle]/posts/page",
      "/page",
      "/posts/[id]/page",
      "/r/[slug]/page",
      "/representatives/page",
      "/search/page",
      "/trust/page",
      "/verification-review/page",
      "/workspace/page",
    ]);
    for (const budget of Object.values(ROUTE_BUDGETS) as number[]) expect(budget % 16384).toBe(0);
  });

  it("sums unique route JavaScript assets and excludes CSS", () => {
    const root = fixture(
      { "/page": ["static/chunks/a.js", "static/chunks/a.js", "static/chunks/b.js", "static/chunks/site.css"] },
      { "static/chunks/a.js": 7, "static/chunks/b.js": 11, "static/chunks/site.css": 1000 },
    );
    expect(checkRouteBudgets({ webRoot: root, budgets: { "/page": 18 } })).toEqual([
      { route: "/page", jsBytes: 18, budgetBytes: 18, assetCount: 2 },
    ]);
  });

  it("fails closed for unparseable production metadata", () => {
    const root = mkdtempSync(join(tmpdir(), "connectmd-route-budget-"));
    temporaryRoots.push(root);
    const buildRoot = join(root, ".next");
    mkdirSync(buildRoot, { recursive: true });
    writeFileSync(join(buildRoot, "app-build-manifest.json"), "{not-json");

    const error = captureFailure(() => checkRouteBudgets({ webRoot: root, budgets: { "/page": 10 } }));
    expect(error.message).toContain("Unparseable production build metadata");
  });

  it.each([
    ["missing production metadata", {}, { "/page": 10 }, "Missing production build metadata", undefined],
    [
      "an unknown launch route",
      { "/page": ["static/chunks/a.js"], "/new/page": ["static/chunks/b.js"] },
      { "/page": 10 },
      "Production route coverage is incomplete",
      "missing budget for launch route",
    ],
    [
      "a missing launch route",
      { "/page": ["static/chunks/a.js"] },
      { "/page": 10, "/new/page": 10 },
      "Production route coverage is incomplete",
      "budget route missing from production manifest",
    ],
    ["a missing asset", { "/page": ["static/chunks/missing.js"] }, { "/page": 10 }, "Missing production asset", undefined],
    [
      "an over-budget route",
      { "/page": ["static/chunks/a.js"] },
      { "/page": 10 },
      "Production route JavaScript budget failed",
      "exceeds",
    ],
  ])("fails closed for %s", (label, pages, budgets, message, issue) => {
    const root = label === "missing production metadata" ? mkdtempSync(join(tmpdir(), "connectmd-route-budget-")) : fixture(pages, { "static/chunks/a.js": 11 });
    if (label === "missing production metadata") temporaryRoots.push(root);
    const error = captureFailure(() => checkRouteBudgets({ webRoot: root, budgets }));
    expect(error.message).toContain(message);
    if (issue) expect(error.issues).toEqual(expect.arrayContaining([expect.stringContaining(issue)]));
  });

  it("rejects unsafe or unsupported manifest assets before measuring them", () => {
    const root = fixture({ "/page": ["static/chunks/../secret.js"] }, {});
    expect(() => checkRouteBudgets({ webRoot: root, budgets: { "/page": 10 } })).toThrow("Unsafe production asset path");
  });
});
