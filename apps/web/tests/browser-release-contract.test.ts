import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = resolve(process.cwd());
const repoRoot = resolve(webRoot, "..", "..");
const harnessValidation = (await import(new URL("../e2e/production-harness.mjs", import.meta.url).href)) as {
  validateFixturePayload: (fixture: unknown) => unknown;
  validatePlaywrightJsonReceipt: (raw: string) => unknown;
  summarizePlaywrightResult: (result: {
    code: number | null;
    signal: string | null;
    stdout: string;
  }) => string;
  waitForChildOutput: (child: unknown) => Promise<{
    code: number | null;
    signal: string | null;
    stdout: string;
    stderr: string;
  }>;
};
const {
  summarizePlaywrightResult,
  validateFixturePayload,
  validatePlaywrightJsonReceipt,
  waitForChildOutput,
} = harnessValidation;
const buildValidation = (await import(new URL("../scripts/build-production-e2e.mjs", import.meta.url).href)) as {
  BROWSER_RELEASE_BUILD_PROFILE: string;
  assertNoNextDotenvFiles: (root?: string) => void;
  compareCanonicalPaths: (left: string, right: string) => number;
  createBrowserReleaseBuildInputManifest: () => unknown;
  validateBrowserReleaseBuildReceipt: (receipt: unknown, buildId: string, currentManifest: unknown) => unknown;
};
const {
  BROWSER_RELEASE_BUILD_PROFILE,
  assertNoNextDotenvFiles,
  compareCanonicalPaths,
  createBrowserReleaseBuildInputManifest,
  validateBrowserReleaseBuildReceipt,
} = buildValidation;

function source(relativePath: string): string {
  return readFileSync(resolve(repoRoot, relativePath), "utf8");
}

function harnessSources(): string {
  return [
    source("apps/web/e2e/production-harness.mjs"),
    source("apps/web/e2e/fixture-contracts.mjs"),
    source("apps/web/e2e/production-runtime.mjs"),
  ].join("\n");
}

const explicitTouchGeometryMarkers = [
  'if (!(await target.isVisible())) continue;',
  "const box = await target.boundingBox();",
  'expect(box!.width, `touch target ${index} width`).toBeGreaterThanOrEqual(44);',
  'expect(box!.height, `touch target ${index} height`).toBeGreaterThanOrEqual(44);',
] as const;

const searchFacetTouchGeometryMarkers = [
  'const targets = page.locator(\'[data-touch-target="search-facet"]\');',
  'expect(count, "search fixture must render at least one standalone facet target").toBeGreaterThan(0);',
  "const box = await target.boundingBox();",
  'expect(box!.width, `search facet ${index} width`).toBeGreaterThanOrEqual(44);',
  'expect(box!.height, `search facet ${index} height`).toBeGreaterThanOrEqual(44);',
] as const;

const searchResultPrimaryTouchGeometryMarkers = [
  'const targets = page.locator(\'[data-touch-target="search-result-primary"]\');',
  'expect(count, "search fixture must render at least one primary result target").toBeGreaterThan(0);',
  "const box = await target.boundingBox();",
  'expect(box!.width, `search result primary ${index} width`).toBeGreaterThanOrEqual(44);',
  'expect(box!.height, `search result primary ${index} height`).toBeGreaterThanOrEqual(44);',
] as const;

function assertExplicitTouchGeometryContract(sourceText: string): void {
  const start = sourceText.indexOf("async function assertExplicitMinimumTouchTargets");
  const end = sourceText.indexOf("\n}\n", start);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  const contract = sourceText.slice(start, end);
  expect(contract).toContain('textarea.min-h-11, summary"');
  for (const marker of explicitTouchGeometryMarkers) expect(contract).toContain(marker);
}

function assertSearchFacetTouchGeometryContract(sourceText: string): void {
  const start = sourceText.indexOf("async function assertSearchFacetTouchTargets");
  const end = sourceText.indexOf("\n}\n", start);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  const contract = sourceText.slice(start, end);
  for (const marker of searchFacetTouchGeometryMarkers) expect(contract).toContain(marker);
}

function assertSearchResultPrimaryTouchGeometryContract(sourceText: string): void {
  const start = sourceText.indexOf("async function assertSearchResultPrimaryTouchTargets");
  const end = sourceText.indexOf("\n}\n", start);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  const contract = sourceText.slice(start, end);
  for (const marker of searchResultPrimaryTouchGeometryMarkers) expect(contract).toContain(marker);
}

function exactNinePassPlaywrightReceipt() {
  return {
    config: {},
    stats: {
      startTime: "2026-08-15T00:00:00.000Z",
      duration: 1,
      expected: 9,
      skipped: 0,
      unexpected: 0,
      flaky: 0,
    },
    errors: [],
    suites: [
      {
        specs: Array.from({ length: 9 }, () => ({
          ok: true,
          tests: [{ expectedStatus: "passed", status: "expected", results: [{ status: "passed", errors: [] }] }],
        })),
      },
    ],
  };
}

type FixtureMutation = {
  search: { hits: Array<Record<string, unknown>> };
  searchEmpty: { hits: Array<Record<string, unknown>>; total: number };
  searchUnavailable: { status: number; body: string };
  post: Record<string, unknown>;
  posts: { items: Array<Record<string, unknown>> };
  publicDocuments: { items: Array<Record<string, unknown>> };
};

function mutableFixture(): FixtureMutation {
  return JSON.parse(source("apps/web/e2e/public-fixtures.json")) as FixtureMutation;
}

describe("production browser release gate", () => {
  it("claims only an absent Monaco directory and rejects partial pre-existing assets", () => {
    const harness = resolve(webRoot, "e2e", "production-harness.mjs");
    const classify = (directoryExists: boolean, loaderExists: boolean) =>
      execFileSync(
        process.execPath,
        [harness, "--classify-monaco", String(directoryExists), String(loaderExists)],
        { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
      ).trim();

    expect(classify(false, false)).toBe("create");
    expect(classify(true, true)).toBe("reuse");
    expect(() => classify(true, false)).toThrow();
    expect(() => classify(false, true)).toThrow();
  });

  it("keeps fixture contracts and runtime orchestration behind the CLI facade", () => {
    const facade = source("apps/web/e2e/production-harness.mjs");
    const fixtures = source("apps/web/e2e/fixture-contracts.mjs");
    const runtime = source("apps/web/e2e/production-runtime.mjs");

    expect(facade).toContain('from "./fixture-contracts.mjs"');
    expect(facade).toContain('from "./production-runtime.mjs"');
    expect(facade).not.toContain("function validateFixturePayload");
    expect(facade).not.toContain("function createReverseProxy");
    expect(fixtures).toContain("export function validateFixturePayload");
    expect(fixtures).toContain("export function validateProtocolManifest");
    expect(runtime).toContain("function createReverseProxy");
    expect(runtime).toContain("export async function main");
    expect(runtime).not.toContain("function validateFixturePayload");
  });

  it("validates one exact HTTP(S) E2E origin and cleans every failed listen stage", () => {
    const harness = resolve(webRoot, "e2e", "production-harness.mjs");
    const validateOrigin = (value: string) =>
      execFileSync(
        process.execPath,
        [harness, "--validate-origin", value],
        { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
      ).trim();

    expect(validateOrigin("http://127.0.0.1:43123/")).toBe("http://127.0.0.1:43123");
    expect(() => validateOrigin("http://127.0.0.1:43123/private")).toThrow();
    expect(() => validateOrigin("http://user:pass@127.0.0.1:43123/")).toThrow();
    expect(() => validateOrigin("file:///tmp/connect-md")).toThrow();

    expect(
      execFileSync(
        process.execPath,
        [harness, "--probe-listen-cleanup"],
        { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
      ).trim(),
    ).toBe("listen-cleanup-ok");

    expect(
      execFileSync(
        process.execPath,
        [harness, "--probe-child-cleanup"],
        { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 10_000 },
      ).trim(),
    ).toBe("child-cleanup-ok");
  });

  it("pins the browser and accessibility engines and exposes one bounded harness command", () => {
    const manifest = JSON.parse(source("apps/web/package.json"));
    const lock = JSON.parse(source("apps/web/package-lock.json"));

    expect(manifest.devDependencies["@playwright/test"]).toBe("1.62.1");
    expect(manifest.devDependencies["axe-core"]).toBe("4.12.1");
    expect(manifest.scripts["build:e2e"]).toBe("node scripts/build-production-e2e.mjs");
    expect(manifest.scripts["test:e2e"]).toBe("node e2e/production-harness.mjs");
    expect(lock.packages["node_modules/@playwright/test"].version).toBe("1.62.1");
    expect(lock.packages["node_modules/axe-core"].version).toBe("4.12.1");
  });

  it("runs the gate only after the production build with a locally resolved browser package", () => {
    const workflow = source(".github/workflows/ci.yml");
    const build = workflow.indexOf("- run: npm run build:e2e");
    const install = workflow.indexOf(
      "- run: npx --no-install playwright install --with-deps chromium",
      build,
    );
    const gate = workflow.indexOf("- run: npm run test:e2e", install);

    expect(build).toBeGreaterThanOrEqual(0);
    expect(install).toBeGreaterThan(build);
    expect(gate).toBeGreaterThan(install);
  });

  it("binds the browser gate to the exact deterministic build-input receipt and Next BUILD_ID", () => {
    const buildScript = source("apps/web/scripts/build-production-e2e.mjs");
    const harness = harnessSources();
    const manifest = createBrowserReleaseBuildInputManifest();
    const serializedManifest = JSON.stringify(manifest);
    const receipt = {
      version: 1,
      build_profile: BROWSER_RELEASE_BUILD_PROFILE,
      build_id: "browser-release-build-id",
      build_inputs_before: manifest,
      build_inputs_after: manifest,
      build_input_manifest_sha256: createHash("sha256").update(serializedManifest).digest("hex"),
    };

    expect(
      [
        "public/monaco/vs/monaco.contribution-BgRy6xDf.js",
        "public/monaco/vs/monaco.contribution-BPhsneLd.js",
      ].sort(compareCanonicalPaths),
    ).toEqual([
      "public/monaco/vs/monaco.contribution-BPhsneLd.js",
      "public/monaco/vs/monaco.contribution-BgRy6xDf.js",
    ]);

    expect(() => validateBrowserReleaseBuildReceipt(receipt, receipt.build_id, manifest)).not.toThrow();
    expect(() =>
      validateBrowserReleaseBuildReceipt(
        { ...receipt, build_id: "different-build-id" },
        receipt.build_id,
        manifest,
      ),
    ).toThrow();
    expect(() =>
      validateBrowserReleaseBuildReceipt(
        { ...receipt, build_input_manifest_sha256: "0".repeat(64) },
        receipt.build_id,
        manifest,
      ),
    ).toThrow();
    expect(() =>
      validateBrowserReleaseBuildReceipt(
        { ...receipt, build_profile: "different-profile" },
        receipt.build_id,
        manifest,
      ),
    ).toThrow();
    const dotenvDirectory = mkdtempSync(resolve(tmpdir(), "connectmd-browser-release-dotenv-"));
    try {
      expect(() => assertNoNextDotenvFiles(dotenvDirectory)).not.toThrow();
      for (const dotenvFilename of [".env.production.local", ".env.local", ".env.production", ".env"]) {
        writeFileSync(resolve(dotenvDirectory, dotenvFilename), "IGNORED_BY_TEST=1\n", "utf8");
        expect(() => assertNoNextDotenvFiles(dotenvDirectory), dotenvFilename).toThrow();
        rmSync(resolve(dotenvDirectory, dotenvFilename), { force: true });
      }
    } finally {
      rmSync(dotenvDirectory, { force: true, recursive: true });
    }
    expect(buildScript).toContain("copy-monaco-assets.mjs");
    expect(buildScript).toContain('"build"');
    expect(buildScript).toContain("check-route-js-budgets.mjs");
    expect(buildScript).toContain("build_inputs_before");
    expect(buildScript).toContain("build_inputs_after");
    expect(buildScript).toContain('BROWSER_RELEASE_BUILD_PROFILE = "hermetic-production-e2e-v1"');
    expect(buildScript).toContain(".env.production.local");
    expect(buildScript).toContain(".env.local");
    expect(buildScript).toContain(".env.production");
    expect(buildScript).toContain('".env"');
    expect(buildScript.indexOf("assertNoNextDotenvFiles();")).toBeLessThan(
      buildScript.indexOf("copy-monaco-assets.mjs"),
    );
    expect(buildScript).toContain(
      "export function loadAndValidateBrowserReleaseBuildReceipt() {\n  assertNoNextDotenvFiles();",
    );
    expect(buildScript).toContain("NEXT_TELEMETRY_DISABLED: \"1\"");
    expect(buildScript).toContain("CONNECTMD_API_BASE_URL: \"http://127.0.0.1:9\"");
    expect(harness).toContain("loadAndValidateBrowserReleaseBuildReceipt();");
    expect(harness.indexOf("loadAndValidateBrowserReleaseBuildReceipt();")).toBeLessThan(
      harness.indexOf("const apiOrigin = await listen(apiServer)"),
    );
  });

  it("accepts only the exact nine-pass Playwright JSON receipt", () => {
    const receipt = exactNinePassPlaywrightReceipt();
    expect(() => validatePlaywrightJsonReceipt(JSON.stringify(receipt))).not.toThrow();
    expect(() =>
      validatePlaywrightJsonReceipt(
        JSON.stringify({ ...receipt, stats: { ...receipt.stats, skipped: 1 } }),
      ),
    ).toThrow();
    expect(() =>
      validatePlaywrightJsonReceipt(
        JSON.stringify({ ...receipt, stats: { ...receipt.stats, interrupted: 1 } }),
      ),
    ).toThrow();
    expect(() => validatePlaywrightJsonReceipt(JSON.stringify({ ...receipt, interrupted: 1 }))).toThrow();
    expect(() =>
      validatePlaywrightJsonReceipt(
        JSON.stringify({ ...receipt, stats: { ...receipt.stats, duration: -1 } }),
      ),
    ).toThrow();
    expect(() =>
      validatePlaywrightJsonReceipt(
        JSON.stringify({
          ...receipt,
          suites: [{ specs: receipt.suites[0].specs.slice(0, 8) }],
        }),
      ),
    ).toThrow();
    const redistributed = exactNinePassPlaywrightReceipt();
    redistributed.suites[0].specs[0].tests.push(
      redistributed.suites[0].specs[1].tests[0],
    );
    redistributed.suites[0].specs[1].tests = [];
    expect(() => validatePlaywrightJsonReceipt(JSON.stringify(redistributed))).toThrow();
    const failedSpec = exactNinePassPlaywrightReceipt();
    failedSpec.suites[0].specs[0].ok = false;
    expect(() => validatePlaywrightJsonReceipt(JSON.stringify(failedSpec))).toThrow();
  });

  it("reports bounded Playwright status without exposing child output", () => {
    const passing = summarizePlaywrightResult({
      code: 0,
      signal: null,
      stdout: JSON.stringify(exactNinePassPlaywrightReceipt()),
    });
    expect(passing).toContain("expected=9");
    expect(passing).toContain("failed=0");
    expect(passing).toContain("skipped=0");

    const failed = exactNinePassPlaywrightReceipt();
    Object.assign(failed.suites[0].specs[2], {
      ok: false,
      title: "token=secret-should-not-appear",
    });
    Object.assign(failed.suites[0].specs[2].tests[0].results[0], {
      errorLocation: { file: "e2e/public-release.spec.ts", line: 1025, column: 17 },
      errors: [
        {
          location: { file: "C:/private/token-output.spec.ts", line: 11, column: 4 },
          message: "secret=child-output-must-not-appear",
        },
      ],
    });
    Object.assign(failed.suites[0].specs[2].tests[0], {
      annotations: [
        {
          type: "connectmd-layout-overflow",
          description: JSON.stringify({
            route_index: 1,
            viewport_width: 160,
            element_indices: [12, 29],
            element_categories: ["link", "form-control"],
          }),
        },
      ],
    });
    const diagnostic = summarizePlaywrightResult({
      code: 1,
      signal: null,
      stdout: JSON.stringify(failed),
    });
    expect(diagnostic).toContain("exit=1");
    expect(diagnostic).toContain("failed=1");
    expect(diagnostic).toContain("failed_specs=[2]");
    expect(diagnostic).toContain('failed_locations=[{"spec":2,"line":1025,"column":17}]');
    expect(diagnostic).toContain(
      'layout=[{"spec":2,"route_index":1,"viewport_width":160,"element_indices":[12,29],"element_categories":["link","form-control"]}]',
    );
    expect(diagnostic).not.toContain("public-release.spec.ts");
    expect(diagnostic).not.toContain("secret");
    expect(diagnostic).not.toContain("token");

    const untrustedLocation = exactNinePassPlaywrightReceipt();
    Object.assign(untrustedLocation.suites[0].specs[2], { ok: false });
    Object.assign(untrustedLocation.suites[0].specs[2].tests[0].results[0], {
      errorLocation: { file: "e2e/private.spec.ts", line: 10, column: 2 },
      errors: [{ location: { file: "e2e/public-release.spec.ts", line: 0, column: 1 } }],
    });
    Object.assign(untrustedLocation.suites[0].specs[2].tests[0], {
      annotations: [
        {
          type: "connectmd-layout-overflow",
          description: JSON.stringify({
            route_index: 9,
            viewport_width: 160,
            element_indices: [12],
            element_categories: ["secret"],
          }),
        },
      ],
    });
    expect(summarizePlaywrightResult({
      code: 1,
      signal: null,
      stdout: JSON.stringify(untrustedLocation),
    })).not.toContain("failed_locations");
    expect(summarizePlaywrightResult({
      code: 1,
      signal: null,
      stdout: JSON.stringify(untrustedLocation),
    })).not.toContain("layout=");
    expect(summarizePlaywrightResult({ code: 1, signal: null, stdout: "not-json" })).toContain(
      "receipt=invalid",
    );
    expect(
      summarizePlaywrightResult({
        code: 999,
        signal: "secret",
        stdout: JSON.stringify(exactNinePassPlaywrightReceipt()),
      }),
    ).toContain("exit=unknown signal=present");
  });

  it("collects child output through close after exit", async () => {
    class OutputStream extends EventEmitter {
      setEncoding(): void {}
    }
    const child = Object.assign(new EventEmitter(), {
      stdout: new OutputStream(),
      stderr: new OutputStream(),
    });
    let settled = false;
    const resultPromise = waitForChildOutput(child).then((result) => {
      settled = true;
      return result;
    });

    child.emit("exit", 0, null);
    child.stdout.emit("data", "complete");
    await new Promise((resolvePromise) => setImmediate(resolvePromise));
    expect(settled).toBe(false);

    child.emit("close", 0, null);
    await expect(resultPromise).resolves.toEqual({
      code: 0,
      signal: null,
      stdout: "complete",
      stderr: "",
    });
  });

  it("keeps the harness loopback-only, read-only, and fail-closed", () => {
    const harness = harnessSources();
    const spec = source("apps/web/e2e/public-release.spec.ts");
    const cleanupDefinition = harness.indexOf("const cleanup = async () =>");
    const firstListen = harness.indexOf("const apiOrigin = await listen(apiServer)");

    expect(harness).toContain('server.listen(port, "127.0.0.1")');
    expect(harness).toContain("strictHttpOrigin");
    expect(harness).toContain("rejectAfterCleanup");
    expect(harness).toContain("await Promise.all(servers.map((server) => closeServer(server)))");
    expect(harness).toContain('request.method !== "GET" && request.method !== "HEAD"');
    expect(harness).toContain('stdio: "ignore"');
    expect(harness).toContain("await stopChild(playwrightProcess)");
    expect(harness).toContain("await stopChild(monacoCopyProcess)");
    expect(harness).toContain("await stopChild(nextProcess)");
    expect(harness).toContain('spawn("taskkill", args');
    expect(harness).toContain("process.kill(-child.pid");
    expect(harness).toContain('CONNECTMD_RECRUITING_ENABLED: "false"');
    expect(harness).toContain('NEXT_PUBLIC_SITE_URL: "https://connect.md"');
    expect(harness).toContain("await terminateProcessTree(child, false)");
    expect(harness).toContain("child process did not exit after bounded cleanup");
    expect(harness).toContain("NEXT_EGRESS_GUARD");
    expect(harness).toContain('"--require"');
    expect(harness).toContain("NEXT_STANDALONE_SERVER");
    expect(harness).toContain("await prepareStandaloneRuntime()");
    expect(harness).toContain("await cp(PUBLIC_DIRECTORY, NEXT_STANDALONE_PUBLIC_DIRECTORY, { recursive: true })");
    expect(harness).toContain("await cp(NEXT_STATIC_DIRECTORY, NEXT_STANDALONE_STATIC_DIRECTORY, { recursive: true })");
    expect(harness).toContain('HOSTNAME: "127.0.0.1"');
    expect(harness).toContain("PORT: new URL(nextOrigin).port");
    expect(harness).toContain("cwd: NEXT_STANDALONE_DIRECTORY");
    expect(harness).not.toContain('resolve(WEB_ROOT, "node_modules", "next", "dist", "bin", "next")');
    expect(harness).toContain("CONNECTMD_E2E_FIXTURE_API_ORIGIN");
    expect(harness).toContain("CONNECTMD_E2E_NEXT_EGRESS_AUDIT_PATH");
    expect(harness).toContain("validateNextEgressAudit(nextEgressAuditPath, apiOrigin)");
    expect(harness).toContain("assertRunningChild(nextProcess, nextProcessId, \"readiness\")");
    expect(harness).toContain("assertRunningChild(nextProcess, nextProcessId, \"Playwright\")");
    expect(harness).toContain('"--reporter=json"');
    expect(harness).toContain("summarizePlaywrightResult(result)");
    expect(harness).toContain("validatePlaywrightJsonReceipt(result.stdout)");
    expect(harness).toContain("PUBLIC_RELEASE_SPEC_PATH");
    expect(harness).toContain("failed_locations");
    expect(harness).toContain("LAYOUT_DIAGNOSTIC_TYPE");
    expect(harness).toContain("MAX_LAYOUT_DIAGNOSTIC_DOM_INDEX");
    expect(harness).toContain("layout=");
    expect(harness).toContain('child.once("close"');
    expect(harness).not.toContain("result.stderr");
    expect(harness).toContain("EXPECTED_PLAYWRIGHT_TESTS = 9");
    expect(harness).toContain("stats.skipped !== 0");
    expect(harness).toContain("stats.unexpected !== 0");
    expect(harness).toContain("stats.flaky !== 0");
    expect(harness).toContain("stats.startTime");
    expect(harness).toContain("stats.duration");
    expect(harness).toContain("interrupted !== 0");
    const waitForNextStart = harness.indexOf("async function waitForNext");
    const waitForNextEnd = harness.indexOf("function waitForChild", waitForNextStart);
    expect(harness.slice(waitForNextStart, waitForNextEnd)).toContain("child.signalCode !== null");
    expect(harness).toContain("removeNextEgressAudit(nextEgressAuditDirectory)");
    expect(cleanupDefinition).toBeGreaterThanOrEqual(0);
    expect(firstListen).toBeGreaterThan(cleanupDefinition);
    expect(harness).toContain("await closeServer(nextPortServer)");
    expect(harness).toContain("MONACO_COPY_SCRIPT");
    expect(harness).toContain("mkdirSync(MONACO_DIRECTORY)");
    expect(harness.indexOf("mkdirSync(MONACO_DIRECTORY)")).toBeLessThan(
      harness.indexOf("monacoPreparedByHarness = true"),
    );
    expect(harness).toContain("await rm(MONACO_DIRECTORY, { force: true, recursive: true })");
    expect(harness).toContain("await rm(NEXT_STANDALONE_PUBLIC_DIRECTORY, { force: true, recursive: true })");
    expect(harness).toContain("await rm(NEXT_STANDALONE_STATIC_DIRECTORY, { force: true, recursive: true })");
    expect(spec).toContain('runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] }');
    expect(spec).toContain("return result.violations.map");
    expect(spec).not.toContain(".filter((violation)");
    expect(spec).toContain("text/markdown");
    expect(spec).toContain("mutatingMethods");
    expect(spec).toContain('page.locator(".monaco-editor")');
    expect(spec).toContain('name: "Edit the source. Keep the same document."');
    expect(spec).toContain('const editedName = "Ada Browser Continuity"');
    expect(spec).toContain('name: "Next: shape"');
    expect(spec).toContain("toHaveValue(editedName)");
    expect(spec).toContain("url.origin !== expectedOrigin");
    expect(spec).toContain('route.abort("blockedbyclient")');
    expect(spec).toContain("function exactE2eUrl");
    expect(spec).toContain("target.origin !== origin");
    expect(harness).toContain("function browserCredentialHeaderKind");
    expect(harness).toContain("BROWSER_CREDENTIAL_HEADER_NAMES.includes(normalized)");
    expect(harness).toContain("if (browserCredentialHeaderKind(request.headers))");
    expect(harness).toContain("credential header not allowed");
    expect(spec).toContain("function browserCredentialHeaderKind");
    expect(spec).toContain("BROWSER_CREDENTIAL_HEADER_NAMES.find");
    expect(spec).toContain("credentialHeaderViolations");
    expect(spec).toContain("webSocketOrigins");
    expect(spec).toContain('await page.routeWebSocket("**/*"');
    expect(spec).toContain("function webSocketAuditOrigin");
    expect(spec).toContain('url.protocol === "ws:" || url.protocol === "wss:" ? url.origin : "invalid"');
    expect(spec).toContain('await socket.close({ code: 1008, reason: "browser-release-websocket-blocked" })');
    expect(spec).not.toContain('page.on("websocket"');
    expect(spec).not.toContain("webSocketOrigins.push(socket.url())");
    expect(spec).toContain("audit.credentialHeaderViolations.push({ kind, origin: url.origin })");
    expect(spec).toContain("expect(audit.credentialHeaderViolations).toEqual([])");
    for (const headerName of ["authorization", "cookie", "proxy-authorization"]) {
      expect(harness).toContain(`\"${headerName}\"`);
      expect(spec).toContain(`\"${headerName}\"`);
    }
    const assertCredentialHeaderContract = (harnessSource: string, specSource: string) => {
      const assertHeaderList = (sourceText: string, label: string) => {
        const start = sourceText.indexOf("const BROWSER_CREDENTIAL_HEADER_NAMES = [");
        const end = sourceText.indexOf("];", start);
        expect(start, `${label} header list`).toBeGreaterThanOrEqual(0);
        expect(end, `${label} header list`).toBeGreaterThan(start);
        const declaration = sourceText.slice(start, end);
        for (const headerName of ["authorization", "cookie", "proxy-authorization"]) {
          expect(declaration, `${label} ${headerName} header guard`).toContain(`"${headerName}"`);
        }
      };
      assertHeaderList(harnessSource, "harness");
      assertHeaderList(specSource, "spec");
      expect(harnessSource).toContain("function browserCredentialHeaderKind");
      expect(harnessSource).toContain("BROWSER_CREDENTIAL_HEADER_NAMES.includes(normalized)");
      expect(harnessSource).toContain("if (browserCredentialHeaderKind(request.headers))");
      expect(harnessSource).toContain("credential header not allowed");
      expect(specSource).toContain("function browserCredentialHeaderKind");
      expect(specSource).toContain("BROWSER_CREDENTIAL_HEADER_NAMES.find");
      expect(specSource).toContain("credentialHeaderViolations");
      expect(specSource).toContain("audit.credentialHeaderViolations.push({ kind, origin: url.origin })");
      expect(specSource).toContain("expect(audit.credentialHeaderViolations).toEqual([]");
    };
    assertCredentialHeaderContract(harness, spec);
    const weakenCredentialHeaderGuard = (sourceText: string, headerName: string) => {
      const start = sourceText.indexOf("const BROWSER_CREDENTIAL_HEADER_NAMES = [");
      const end = sourceText.indexOf("];", start);
      expect(start).toBeGreaterThanOrEqual(0);
      expect(end).toBeGreaterThan(start);
      const declaration = sourceText.slice(start, end);
      return `${sourceText.slice(0, start)}${declaration.replace(`\"${headerName}\"`, '"removed-header"')}${sourceText.slice(end)}`;
    };
    for (const headerName of ["authorization", "cookie", "proxy-authorization"]) {
      const weakenedHarness = weakenCredentialHeaderGuard(harness, headerName);
      const weakenedSpec = weakenCredentialHeaderGuard(spec, headerName);
      expect(() => assertCredentialHeaderContract(weakenedHarness, spec), `harness ${headerName} guard`).toThrow();
      expect(() => assertCredentialHeaderContract(harness, weakenedSpec), `spec ${headerName} guard`).toThrow();
    }
    expect(spec).toContain('const canonicalSiteOrigin = "https://connect.md"');
    expect(spec).toContain('toBe(canonicalSiteOrigin)');
    expect(spec).toContain('meta[name="robots"]');
  });

  it("requires public touch targets, mobile overflow, first-use reachability, and Axe coverage", () => {
    const spec = source("apps/web/e2e/public-release.spec.ts");
    const fixture = JSON.parse(source("apps/web/e2e/public-fixtures.json")) as {
      search: { facets: Record<string, unknown> };
    };
    expect(spec).toContain("const narrowReflowViewport = { width: 320, height: 800 } as const;");
    expect(spec.match(/page\.setViewportSize\(narrowReflowViewport\)/gu)).toHaveLength(4);
    expect(spec).toContain("minimumTouchTargetBox");
    assertExplicitTouchGeometryContract(spec);
    for (const marker of explicitTouchGeometryMarkers) {
      expect(
        () => assertExplicitTouchGeometryContract(spec.replace(marker, "removed")),
        marker,
      ).toThrow();
    }
    expect(fixture.search.facets.kind).toEqual([{ value: "profile", label: "Profiles", count: 1 }]);
    expect(spec).toContain("await assertSearchFacetTouchTargets(page);");
    assertSearchFacetTouchGeometryContract(spec);
    for (const marker of searchFacetTouchGeometryMarkers) {
      const start = spec.indexOf("async function assertSearchFacetTouchTargets");
      const end = spec.indexOf("\n}\n", start);
      const contract = spec.slice(start, end);
      const weakened = `${spec.slice(0, start)}${contract.replace(marker, "removed")}${spec.slice(end)}`;
      expect(
        () => assertSearchFacetTouchGeometryContract(weakened),
        marker,
      ).toThrow();
    }
    expect(spec).toContain("await assertSearchResultPrimaryTouchTargets(page);");
    assertSearchResultPrimaryTouchGeometryContract(spec);
    for (const marker of searchResultPrimaryTouchGeometryMarkers) {
      const start = spec.indexOf("async function assertSearchResultPrimaryTouchTargets");
      const end = spec.indexOf("\n}\n", start);
      const contract = spec.slice(start, end);
      const weakened = `${spec.slice(0, start)}${contract.replace(marker, "removed")}${spec.slice(end)}`;
      expect(
        () => assertSearchResultPrimaryTouchGeometryContract(weakened),
        marker,
      ).toThrow();
    }
    expect(spec).toContain("taxonomy removal targets must not overlap");
    expect(spec).toContain("narrowModeSwitchBounds");
    expect(spec).toContain("narrowReflowViewport.height");
    const routeDeclaration = spec.match(/const mobilePublicRoutes = \[([\s\S]*?)\] as const;/u);
    const mobileRoutes = routeDeclaration?.[1].match(/"[^"]+"/gu)?.map((value) => value.slice(1, -1));
    expect(mobileRoutes).toEqual([
      "/",
      "/trust",
      "/agent-directory",
      "/representatives",
      "/r/ada-lovelace-resume",
      "/posts/fixture-post-field-notes",
      "/search",
    ]);

    const usages = [...spec.matchAll(/for \(const path of mobilePublicRoutes\)/gu)].map(
      (match) => match.index ?? -1,
    );
    expect(usages).toHaveLength(2);
    const keyboardStart = spec.indexOf('test("anonymous mobile navigation and auth boundaries remain keyboard-safe"');
    const privateRouteStart = spec.indexOf('test("anonymous private routes fail closed without protected API reads"');
    const axeStart = spec.indexOf('test("public release pages have no WCAG A or AA accessibility violations"');
    expect(keyboardStart).toBeGreaterThanOrEqual(0);
    expect(privateRouteStart).toBeGreaterThan(keyboardStart);
    expect(axeStart).toBeGreaterThan(privateRouteStart);
    expect(usages[0]).toBeGreaterThan(keyboardStart);
    expect(usages[0]).toBeLessThan(privateRouteStart);
    expect(usages[1]).toBeGreaterThan(axeStart);
    expect(spec.slice(keyboardStart, privateRouteStart)).toContain("pageFitsMobileViewport");
    expect(spec.slice(keyboardStart, privateRouteStart)).toContain(
      'page.emulateMedia({ reducedMotion: "reduce" })',
    );
    expect(spec.slice(axeStart)).toContain("await assertA11y(page);");
  });

  it("keeps one raw protocol manifest bounded and explicitly non-live", () => {
    const fixture = JSON.parse(source("apps/web/e2e/public-fixtures.json"));
    const manifest = fixture.protocolManifest as {
      version: number;
      base_url: string;
      environment: string;
      recruiting_enabled: boolean;
      account_lifecycle_enabled: boolean;
      evidence_boundary: string;
      responses: Record<string, {
        status: number;
        headers: Record<string, string>;
        sha256: string;
        body_base64: string;
      }>;
    };
    const protocolPaths = [
      "/agent-readme.md",
      "/llms.txt",
      "/llms-full.txt",
      "/openapi.json",
      "/.well-known/agent-card.json",
    ];

    expect(manifest.version).toBe(1);
    expect(manifest.base_url).toBe("https://connectmd.invalid");
    expect(manifest.environment).toBe("development");
    expect(manifest.recruiting_enabled).toBe(false);
    expect(manifest.account_lifecycle_enabled).toBe(false);
    expect(manifest.evidence_boundary).toContain("hermetic current-source fixture parity");
    expect(manifest.evidence_boundary).toContain("not live");
    expect(Object.keys(manifest.responses).sort()).toEqual([...protocolPaths].sort());
    for (const path of protocolPaths) {
      const response = manifest.responses[path];
      expect(response.status).toBe(200);
      expect(response.sha256).toMatch(/^[0-9a-f]{64}$/u);
      expect(response.body_base64).toMatch(/^[A-Za-z0-9+/]+={0,2}$/u);
      expect(response.headers["content-length"]).toBeUndefined();
      expect(response.headers["content-type"]).toBeDefined();
      expect(response.headers["x-request-id"]).toBe("fixture-protocol-v1");
    }
    expect(manifest.responses["/.well-known/agent-card.json"].headers.etag).toMatch(/^"sha256-[0-9a-f]{64}"$/u);
    expect(manifest.responses["/.well-known/agent-card.json"].headers["cache-control"]).toBe(
      "public, max-age=3600",
    );
    expect(fixture.organizations).toBeUndefined();
    expect(fixture.jobs).toBeUndefined();

    const spec = source("apps/web/e2e/public-release.spec.ts");
    const harness = harnessSources();
    expect(spec).toContain("assertProtocolResponse");
    expect(spec).toContain("/.well-known/agent-card.json");
    expect(spec).toContain("not live");
    expect(harness).toContain("validateProtocolManifest");
    expect(harness).toContain("decodeProtocolBody");
    expect(harness).toContain('CONNECTMD_RECRUITING_ENABLED: "false"');
  });

  it("covers public Resume, Post, and Search fixture routes without widening the fake API", () => {
    const fixture = JSON.parse(source("apps/web/e2e/public-fixtures.json"));
    const harness = harnessSources();
    const spec = source("apps/web/e2e/public-release.spec.ts");
    const digest = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");

    expect(fixture.resumeDocument.markdown_url).toBe("/v1/resumes/ada-lovelace-resume.md");
    expect(fixture.profileDocument.owner_id).toBe("00000000-0000-4000-8000-000000000001");
    expect(fixture.resumeDocument.owner_id).toBe("00000000-0000-4000-8000-000000000001");
    expect(fixture.profileDocument.etag).toBe(`"sha256-${digest(fixture.profileMarkdown)}"`);
    expect(fixture.resumeDocument.etag).toBe(`"sha256-${digest(fixture.resumeMarkdown)}"`);
    expect(fixture.resumeMarkdown).toContain("# Ada Lovelace Resume");
    expect(fixture.post.id).toBe("fixture-post-field-notes");
    expect(fixture.post.author_profile_handle).toBe("ada-lovelace");
    expect(fixture.post.markdown).toBe(fixture.postMarkdown);
    expect(fixture.post.markdown_url).toBe("/v1/posts/fixture-post-field-notes.md");
    expect(fixture.post.etag).toBe(`"sha256-${digest(fixture.postMarkdown)}"`);
    expect(fixture.postMarkdown).toContain("# Field notes on canonical Markdown");
    expect(fixture.postMarkdown).toContain("id: fixture-post-field-notes");
    expect(fixture.postMarkdown).toContain("author_profile_handle: ada-lovelace");
    expect(fixture.postMarkdown).toContain("version: 1");
    expect(fixture.postMarkdown).toContain("published_at: 2026-08-04T00:00:00Z");
    expect(fixture.search.hits).toHaveLength(1);
    expect(fixture.search.hits[0]).toMatchObject({
      kind: "profile",
      identifier: "ada-lovelace",
      location_filter_value: null,
      seniority_filter_value: null,
      html_url: "/p/ada-lovelace",
      markdown_url: "/v1/profiles/ada-lovelace.md",
    });
    expect(fixture.searchEmpty).toMatchObject({
      mode: "projection",
      offset: 0,
      limit: 20,
      total: 0,
      hits: [],
      indexing_available: true,
      warning: null,
      next_cursor: null,
      search_revision: null,
      complete: false,
      facets: {},
      taxonomy_facets: {},
      facet_truncated: {},
    });
    expect(fixture.searchUnavailable).toEqual({
      status: 503,
      body: '{"detail":"fixture search unavailable"}',
    });

    expect(harness).toContain('pathname === "/v1/resumes/ada-lovelace-resume"');
    expect(harness).toContain('pathname === "/v1/resumes/ada-lovelace-resume.md"');
    expect(harness).toContain('pathname === "/v1/posts/fixture-post-field-notes"');
    expect(harness).toContain('pathname === "/v1/posts/fixture-post-field-notes.md"');
    expect(harness).toContain('if (searchVariant === "empty") return jsonBody(fixture.searchEmpty);');
    expect(harness).toContain('if (searchVariant === "unavailable")');
    expect(harness).toContain('pathname === "/v1/agent-identities/fixture-unavailable"');
    expect(harness).toContain("fixture private agent service unavailable");
    expect(harness).toContain("fixture.searchUnavailable.status");
    expect(harness).toContain("validateDocumentFixture");
    expect(harness).toContain("validatePostFixture");
    expect(harness).toContain("validateSearchFixture");
    expect(harness).toContain("validateFixturePrivacy");
    expect(harness).toContain("validatePublicDocumentsFixture");
    expect(harness).toContain("validatePublicPostInventoryFixture");
    expect(harness).toContain("search.total !== search.hits.length");
    expect(harness).toContain("PRIVATE_FIXTURE_KEYS");
    expect(harness).toContain("const isDirectExecution");
    expect(harness).toContain("representationMetadata");
    expect(harness).toContain('request.method !== "GET" && request.method !== "HEAD"');

    expect(spec).toContain('test("profile HTML and canonical Markdown remain byte-parity linked"');
    expect(spec).toContain('page.goto("/r/ada-lovelace-resume"');
    expect(spec).toContain('page.goto(`/posts/${fixture.post.id}`');
    expect(spec).toContain('page.goto("/search"');
    expect(spec).toContain('a[href^="/v1/"]:not([type="text/markdown"])');
    expect(spec).toContain('name: "1 indexed result"');
    expect(spec).toContain('page.goto("/search?q=fixture-empty"');
    expect(spec).toContain('page.goto("/search?q=fixture-unavailable"');
    expect(spec).toContain('name: "No matching public documents"');
    expect(spec).toContain('name: "Directory temporarily unavailable"');
    expect(spec).toContain("Public records are temporarily unavailable. Try again shortly.");
    expect(spec).toContain("fixture search unavailable");
    expect(spec).toContain("/API origin/iu");
    expect(spec).toContain('page.goto("/agents/fixture-unavailable"');
    expect(spec).toContain("This view is temporarily unavailable.");
    expect(spec).toContain("fixture private agent service unavailable");
    expect(spec).toContain("server-side configuration");
    expect(spec).toContain("No document was changed");
    expect(spec).toContain('page.goto("/definitely-not-a-connectmd-route"');
    expect(spec).toContain("missingPageResponse?.status()");
    expect(spec).toContain("This page is not available.");
    expect(spec).toContain("Private and unpublished records are never exposed through this page.");
    expect(spec).toContain('"Not-found Discover link"');
    expect(spec).toContain('"Not-found Home link"');
    expect(spec).toContain("pageFitsMobileViewport");
    expect(spec).toContain("/r/ada-lovelace-resume");
    expect(spec).toContain("/posts/fixture-post-field-notes");
  });

  it("rejects adversarial public-fixture mutations without starting the harness", () => {
    expect(() => validateFixturePayload(mutableFixture())).not.toThrow();
    const mutations: ReadonlyArray<[string, (fixture: FixtureMutation) => void]> = [
      ["missing required search string array", (fixture) => delete fixture.search.hits[0].skill_ids],
      ["corrupt required search filter array", (fixture) => { fixture.search.hits[0].skill_filter_values = ["not-a-taxonomy-alias"]; }],
      ["empty search total drift", (fixture) => { fixture.searchEmpty.total = 1; }],
      ["empty search hit drift", (fixture) => { fixture.searchEmpty.hits = [fixture.search.hits[0]]; }],
      ["missing empty search fixture", (fixture) => delete (fixture as unknown as Record<string, unknown>).searchEmpty],
      ["unavailable search status drift", (fixture) => { fixture.searchUnavailable.status = 200; }],
      ["unavailable search body drift", (fixture) => { fixture.searchUnavailable.body = ""; }],
      ["missing required post detail Markdown", (fixture) => delete fixture.post.markdown],
      ["missing required post inventory topics", (fixture) => delete fixture.posts.items[0].topics],
      ["private subject key", (fixture) => { fixture.search.hits[0].subject = "user_test"; }],
      ["public document inventory mismatch", (fixture) => { fixture.publicDocuments.items.pop(); }],
      ["public post inventory mismatch", (fixture) => { fixture.posts.items[0].id = "fixture-post-other"; }],
    ];
    for (const [label, mutate] of mutations) {
      const fixture = mutableFixture();
      mutate(fixture);
      expect(() => validateFixturePayload(fixture), label).toThrow();
    }
  });
});
