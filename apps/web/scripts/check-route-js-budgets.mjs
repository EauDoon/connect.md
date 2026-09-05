import { readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { isAbsolute, relative, resolve } from "node:path";

const BUILD_DIRECTORY = ".next";
const APP_BUILD_MANIFEST = "app-build-manifest.json";
const ASSET_ROOT = "static/chunks/";

// Baseline: the current pinned Next production build on 2026-08-13. Each
// threshold is the measured unique route JavaScript plus approximately 8%,
// rounded up to 16 KiB. These are build-size guardrails, not live performance
// or browser timing claims.
export const ROUTE_BUDGETS = Object.freeze({
  "/_not-found/page": 393216,
  "/account/page": 409600,
  "/agent-directory/page": 393216,
  "/agents/[handle]/page": 393216,
  "/agents/page": 753664,
  "/appeal-review/page": 950272,
  "/applications/page": 671744,
  "/discover/page": 393216,
  "/employer/page": 1015808,
  "/feed/page": 966656,
  "/human/page": 999424,
  "/inbox/page": 409600,
  "/jobs/[organizationSlug]/[jobSlug]/page": 671744,
  "/jobs/page": 393216,
  "/conversations/[id]/page": 393216,
  "/md/page": 1130496,
  "/messages/[conversationId]/page": 950272,
  "/moderation-review/page": 950272,
  "/moderation/page": 655360,
  "/network/page": 393216,
  "/organizations/[slug]/page": 393216,
  "/organizations/page": 393216,
  "/p/[handle]/page": 688128,
  "/page": 409600,
  "/posts/[id]/page": 933888,
  "/r/[slug]/page": 950272,
  "/representatives/page": 655360,
  "/search/page": 442368,
  "/trust/page": 393216,
  "/verification-review/page": 688128,
  "/workspace/page": 606208,
});

export class RouteBudgetError extends Error {
  constructor(message, issues = []) {
    super(message);
    this.name = "RouteBudgetError";
    this.issues = issues;
  }
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readManifest(webRoot) {
  const manifestPath = resolve(webRoot, BUILD_DIRECTORY, APP_BUILD_MANIFEST);
  let content;
  try {
    content = readFileSync(manifestPath, "utf8");
  } catch {
    throw new RouteBudgetError(
      `Missing production build metadata: ${BUILD_DIRECTORY}/${APP_BUILD_MANIFEST}`,
    );
  }
  try {
    return JSON.parse(content);
  } catch {
    throw new RouteBudgetError(
      `Unparseable production build metadata: ${BUILD_DIRECTORY}/${APP_BUILD_MANIFEST}`,
    );
  }
}

function routeIsLaunchRoute(route) {
  return route === "/page" || route.endsWith("/page");
}

function validateRouteBudgets(budgets) {
  if (!isRecord(budgets)) {
    throw new RouteBudgetError("Route budgets must be an object.");
  }
  const issues = [];
  for (const [route, budget] of Object.entries(budgets)) {
    if (!routeIsLaunchRoute(route)) {
      issues.push(`budget key is not a launch route: ${route}`);
    }
    if (!Number.isSafeInteger(budget) || budget <= 0) {
      issues.push(`budget is not a positive safe integer: ${route}`);
    }
  }
  if (issues.length > 0) {
    throw new RouteBudgetError("Invalid route budget configuration.", issues);
  }
}

function validateManifestPages(manifest) {
  if (!isRecord(manifest) || !isRecord(manifest.pages)) {
    throw new RouteBudgetError(
      `Invalid production build metadata: expected an object with a pages map in ${BUILD_DIRECTORY}/${APP_BUILD_MANIFEST}`,
    );
  }
  const issues = [];
  for (const [route, assets] of Object.entries(manifest.pages)) {
    if (!route.startsWith("/") || route.includes("\\")) {
      issues.push(`invalid manifest route: ${route}`);
    }
    if (!Array.isArray(assets) || assets.some((asset) => typeof asset !== "string")) {
      issues.push(`invalid asset list for route: ${route}`);
    }
  }
  if (issues.length > 0) {
    throw new RouteBudgetError("Invalid production route manifest.", issues);
  }
  return manifest.pages;
}

function assetPath(webRoot, asset) {
  if (
    !asset.startsWith(ASSET_ROOT) ||
    asset.includes("\\") ||
    isAbsolute(asset) ||
    asset.split("/").some((part) => part === ".." || part === "." || part === "")
  ) {
    throw new RouteBudgetError(`Unsafe production asset path: ${asset}`);
  }
  if (!asset.endsWith(".js") && !asset.endsWith(".css")) {
    throw new RouteBudgetError(`Unsupported production asset type: ${asset}`);
  }
  const buildRoot = resolve(webRoot, BUILD_DIRECTORY);
  const resolved = resolve(buildRoot, asset);
  const escape = relative(buildRoot, resolved);
  if (escape.startsWith("..") || isAbsolute(escape)) {
    throw new RouteBudgetError(`Production asset escapes build directory: ${asset}`);
  }
  return resolved;
}

function measureRoute(webRoot, route, assets, budget) {
  const jsAssets = [...new Set(assets.filter((asset) => asset.endsWith(".js")))].sort();
  if (jsAssets.length === 0) {
    throw new RouteBudgetError(`Launch route has no JavaScript assets: ${route}`);
  }
  let jsBytes = 0;
  for (const asset of assets) {
    const resolved = assetPath(webRoot, asset);
    if (!asset.endsWith(".js")) continue;
    let stats;
    try {
      stats = statSync(resolved);
    } catch {
      throw new RouteBudgetError(`Missing production asset for ${route}: ${asset}`);
    }
    if (!stats.isFile()) {
      throw new RouteBudgetError(`Production asset is not a regular file: ${asset}`);
    }
  }
  for (const asset of jsAssets) {
    jsBytes += statSync(assetPath(webRoot, asset)).size;
  }
  return { route, jsBytes, budgetBytes: budget, assetCount: jsAssets.length };
}

export function checkRouteBudgets({ webRoot = process.cwd(), budgets = ROUTE_BUDGETS } = {}) {
  validateRouteBudgets(budgets);
  const pages = validateManifestPages(readManifest(webRoot));
  const manifestRoutes = Object.keys(pages).filter(routeIsLaunchRoute).sort();
  const budgetRoutes = Object.keys(budgets).sort();
  const issues = [];

  for (const route of manifestRoutes) {
    if (!Object.hasOwn(budgets, route)) issues.push(`missing budget for launch route: ${route}`);
  }
  for (const route of budgetRoutes) {
    if (!Object.hasOwn(pages, route)) issues.push(`budget route missing from production manifest: ${route}`);
  }
  if (issues.length > 0) {
    throw new RouteBudgetError("Production route coverage is incomplete.", issues);
  }

  const rows = manifestRoutes.map((route) => measureRoute(webRoot, route, pages[route], budgets[route]));
  for (const row of rows) {
    if (row.jsBytes > row.budgetBytes) {
      issues.push(`${row.route}: ${row.jsBytes} JavaScript bytes exceeds ${row.budgetBytes} byte budget`);
    }
  }
  if (issues.length > 0) {
    throw new RouteBudgetError("Production route JavaScript budget failed.", issues);
  }
  return rows;
}

function usage() {
  return "Usage: node scripts/check-route-js-budgets.mjs [--root <apps/web-root>]";
}

function parseArgs(args) {
  if (args.length === 0) return process.cwd();
  if (args.length === 2 && args[0] === "--root" && args[1]) return resolve(args[1]);
  if (args.length === 1 && args[0] === "--help") {
    console.log(usage());
    return null;
  }
  throw new RouteBudgetError(`Invalid arguments. ${usage()}`);
}

function main() {
  let webRoot;
  try {
    webRoot = parseArgs(process.argv.slice(2));
    if (webRoot === null) return;
    const rows = checkRouteBudgets({ webRoot });
    for (const row of rows) {
      console.log(
        `[route-js-budget] PASS route=${row.route} js_bytes=${row.jsBytes} budget_bytes=${row.budgetBytes} js_assets=${row.assetCount}`,
      );
    }
    console.log(`[route-js-budget] PASS routes=${rows.length} metric=unique-route-javascript-bytes`);
  } catch (error) {
    const failure = error instanceof RouteBudgetError ? error : new RouteBudgetError("Unexpected route budget failure.");
    console.error(`[route-js-budget] FAIL ${failure.message}`);
    for (const issue of failure.issues ?? []) console.error(`[route-js-budget] FAIL ${issue}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) main();
