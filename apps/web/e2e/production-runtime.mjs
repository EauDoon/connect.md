import { createServer, request as httpRequest } from "node:http";
import { spawn } from "node:child_process";
import { cp, mkdtemp, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, relative, resolve } from "node:path";
import { tmpdir } from "node:os";
import { existsSync, mkdirSync, readFileSync } from "node:fs";

import { loadAndValidateBrowserReleaseBuildReceipt } from "../scripts/build-production-e2e.mjs";
import {
  API_EXACT_PATHS,
  browserCredentialHeaderKind,
  decodeProtocolBody,
  loadFixtures,
  PROTOCOL_PATHS,
  representationMetadata,
} from "./fixture-contracts.mjs";

const E2E_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(E2E_DIRECTORY, "..");
const NEXT_BUILD_ID = resolve(WEB_ROOT, ".next", "BUILD_ID");
const NEXT_STANDALONE_DIRECTORY = resolve(WEB_ROOT, ".next", "standalone");
const NEXT_STANDALONE_SERVER = resolve(NEXT_STANDALONE_DIRECTORY, "server.js");
const NEXT_STANDALONE_PUBLIC_DIRECTORY = resolve(NEXT_STANDALONE_DIRECTORY, "public");
const NEXT_STANDALONE_STATIC_DIRECTORY = resolve(NEXT_STANDALONE_DIRECTORY, ".next", "static");
const NEXT_STATIC_DIRECTORY = resolve(WEB_ROOT, ".next", "static");
const PUBLIC_DIRECTORY = resolve(WEB_ROOT, "public");
const MONACO_DIRECTORY = resolve(WEB_ROOT, "public", "monaco");
const MONACO_LOADER = resolve(MONACO_DIRECTORY, "vs", "loader.js");
const MONACO_COPY_SCRIPT = resolve(WEB_ROOT, "scripts", "copy-monaco-assets.mjs");
const NEXT_EGRESS_GUARD = resolve(E2E_DIRECTORY, "next-server-egress-guard.cjs");
const NEXT_EGRESS_AUDIT_PREFIX = "connectmd-next-egress-";
const NEXT_EGRESS_AUDIT_FILE = "next-server-egress-audit.json";
const EXPECTED_PLAYWRIGHT_TESTS = 9;
const PUBLIC_RELEASE_SPEC_PATH = "e2e/public-release.spec.ts";
const MAX_PUBLIC_RELEASE_SPEC_LINE = 2_000;
const MAX_PUBLIC_RELEASE_SPEC_COLUMN = 500;
const LAYOUT_DIAGNOSTIC_TYPE = "connectmd-layout-overflow";
const LAYOUT_DIAGNOSTIC_VIEWPORTS = new Set([160, 320]);
const LAYOUT_DIAGNOSTIC_CATEGORIES = new Set(["link", "button", "form-control", "tabbable"]);
const MAX_LAYOUT_DIAGNOSTIC_DOM_INDEX = 127;

function loopbackOrigin(address) {
  if (!address || typeof address !== "object" || typeof address.port !== "number") {
    throw new Error("loopback server did not expose a port");
  }
  return `http://127.0.0.1:${address.port}`;
}

export function strictHttpOrigin(value, label) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${label} must be an absolute HTTP(S) origin`);
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error(`${label} must be an absolute HTTP(S) origin`);
  }
  return parsed.origin;
}

function listen(server, port = 0) {
  return new Promise((resolvePromise, rejectPromise) => {
    let settled = false;
    const removeListeners = () => {
      server.off("error", onError);
      server.off("listening", onListening);
    };
    const rejectAfterCleanup = (error) => {
      if (settled) return;
      settled = true;
      removeListeners();
      void closeServer(server).finally(() => rejectPromise(error));
    };
    const onError = (error) => rejectAfterCleanup(error);
    const onListening = () => {
      if (settled) return;
      try {
        const origin = loopbackOrigin(server.address());
        settled = true;
        removeListeners();
        resolvePromise(origin);
      } catch (error) {
        rejectAfterCleanup(error);
      }
    };
    server.once("error", onError);
    server.once("listening", onListening);
    try {
      server.listen(port, "127.0.0.1");
    } catch (error) {
      rejectAfterCleanup(error);
    }
  });
}

function responseBody(body, contentType) {
  return { body, contentType };
}

function jsonBody(value) {
  return responseBody(JSON.stringify(value), "application/json; charset=utf-8");
}

function representationHeaders(markdown, updatedAt) {
  const metadata = representationMetadata(markdown);
  return {
    "cache-control": "no-store",
    "content-digest": metadata.contentDigest,
    etag: metadata.etag,
    "last-modified": new Date(updatedAt).toUTCString(),
  };
}

function fixtureJsonResponse(value, markdown, updatedAt) {
  return {
    body: JSON.stringify(value),
    headers: {
      ...representationHeaders(markdown, updatedAt),
      "content-type": "application/json; charset=utf-8",
      vary: "Accept",
    },
  };
}

function fixtureMarkdownResponse(markdown, updatedAt) {
  return {
    body: markdown,
    headers: {
      ...representationHeaders(markdown, updatedAt),
      "content-type": "text/markdown; charset=utf-8",
    },
  };
}

function fixtureApiResponse(pathname, fixture, searchVariant = null) {
  if (PROTOCOL_PATHS.includes(pathname)) {
    const entry = fixture.protocolManifest.responses[pathname];
    return { body: decodeProtocolBody(entry, pathname), headers: entry.headers };
  }
  if (pathname === "/v1/search") {
    if (searchVariant === "empty") return jsonBody(fixture.searchEmpty);
    if (searchVariant === "unavailable") {
      return {
        body: fixture.searchUnavailable.body,
        contentType: "application/json; charset=utf-8",
        status: fixture.searchUnavailable.status,
      };
    }
    return jsonBody(fixture.search);
  }
  if (pathname === "/v1/agent-directory") return jsonBody(fixture.agentDirectory);
  if (pathname === "/v1/agent-identities/fixture-unavailable") {
    return {
      body: '{"detail":"fixture private agent service unavailable"}',
      contentType: "application/json; charset=utf-8",
      status: 503,
    };
  }
  if (pathname === "/v1/public-documents") return jsonBody(fixture.publicDocuments);
  if (pathname === "/v1/organizations") return jsonBody({ organizations: [], next_cursor: null });
  if (pathname === "/v1/jobs") return jsonBody({ jobs: [], next_cursor: null });
  if (pathname === "/v1/posts") return jsonBody(fixture.posts);
  if (pathname === "/v1/profiles/ada-lovelace") {
    return fixtureJsonResponse(
      { ...fixture.profileDocument, markdown: fixture.profileMarkdown },
      fixture.profileMarkdown,
      fixture.profileDocument.updated_at,
    );
  }
  if (pathname === "/v1/profiles/ada-lovelace.md") {
    return fixtureMarkdownResponse(fixture.profileMarkdown, fixture.profileDocument.updated_at);
  }
  if (pathname === "/v1/resumes/ada-lovelace-resume") {
    return fixtureJsonResponse(
      { ...fixture.resumeDocument, markdown: fixture.resumeMarkdown },
      fixture.resumeMarkdown,
      fixture.resumeDocument.updated_at,
    );
  }
  if (pathname === "/v1/resumes/ada-lovelace-resume.md") {
    return fixtureMarkdownResponse(fixture.resumeMarkdown, fixture.resumeDocument.updated_at);
  }
  if (pathname === "/v1/posts/fixture-post-field-notes") {
    return fixtureJsonResponse(fixture.post, fixture.postMarkdown, fixture.post.updated_at);
  }
  if (pathname === "/v1/posts/fixture-post-field-notes.md") {
    return fixtureMarkdownResponse(fixture.postMarkdown, fixture.post.updated_at);
  }
  if (pathname === "/v1/profiles/ada-lovelace/agent-identities") {
    return jsonBody(fixture.profileAgentIdentities);
  }
  return null;
}

function writeResponse(response, requestMethod, statusCode, contentTypeOrHeaders, body) {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(body, "utf8");
  const headers =
    typeof contentTypeOrHeaders === "string"
      ? { "cache-control": "no-store", "content-type": contentTypeOrHeaders }
      : { ...contentTypeOrHeaders };
  headers["content-length"] = String(bytes.byteLength);
  response.sendDate = false;
  response.writeHead(statusCode, headers);
  if (requestMethod === "HEAD") response.end();
  else response.end(bytes);
}

function createFixtureApi(fixture) {
  return createServer((request, response) => {
    if (request.method !== "GET" && request.method !== "HEAD") {
      request.resume();
      writeResponse(response, request.method ?? "", 405, "application/json; charset=utf-8", "{\"detail\":\"method not allowed\"}");
      return;
    }
    const requestUrl = new URL(request.url ?? "/", "http://127.0.0.1");
    const pathname = requestUrl.pathname;
    const searchVariant =
      pathname === "/v1/search"
        ? requestUrl.searchParams.get("q") === "fixture-empty"
          ? "empty"
          : requestUrl.searchParams.get("q") === "fixture-unavailable"
            ? "unavailable"
            : null
        : null;
    const payload = fixtureApiResponse(pathname, fixture, searchVariant);
    if (!payload) {
      writeResponse(response, request.method, 404, "application/json; charset=utf-8", "{\"detail\":\"not found\"}");
      return;
    }
    const statusCode = typeof payload.status === "number" ? payload.status : 200;
    if (payload.headers) writeResponse(response, request.method, statusCode, payload.headers, payload.body);
    else writeResponse(response, request.method, statusCode, payload.contentType, payload.body);
  });
}

function isApiPath(pathname) {
  return (
    API_EXACT_PATHS.has(pathname) ||
    pathname.startsWith("/v1/") ||
    pathname.startsWith("/mcp/") ||
    pathname.startsWith("/a2a/") ||
    pathname.startsWith("/schemas/") ||
    pathname.startsWith("/docs/") ||
    pathname.startsWith("/redoc/") ||
    pathname.startsWith("/.well-known/oauth-protected-resource")
  );
}

function proxyHeaders(headers, targetOrigin) {
  const copied = { ...headers };
  copied.host = new URL(targetOrigin).host;
  delete copied.connection;
  return copied;
}

function createReverseProxy(apiOrigin, nextOrigin) {
  return createServer((request, response) => {
    const incoming = new URL(request.url ?? "/", "http://127.0.0.1");
    if (browserCredentialHeaderKind(request.headers)) {
      request.resume();
      writeResponse(response, request.method ?? "", 400, "application/json; charset=utf-8", "{\"detail\":\"credential header not allowed\"}");
      return;
    }
    const targetOrigin = isApiPath(incoming.pathname) ? apiOrigin : nextOrigin;
    const target = `${targetOrigin}${incoming.pathname}${incoming.search}`;
    const upstream = httpRequest(
      target,
      {
        headers: proxyHeaders(request.headers, targetOrigin),
        method: request.method,
      },
      (upstreamResponse) => {
        response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
        upstreamResponse.pipe(response);
      },
    );
    upstream.once("error", () => {
      if (!response.headersSent) writeResponse(response, "GET", 502, "text/plain; charset=utf-8", "upstream unavailable\n");
      else response.destroy();
    });
    request.once("error", () => upstream.destroy());
    request.pipe(upstream);
  });
}

function safeEnvironment(apiOrigin, proxyOrigin) {
  const safeApiOrigin = strictHttpOrigin(apiOrigin, "fixture API origin");
  const safeProxyOrigin = strictHttpOrigin(proxyOrigin, "E2E base origin");
  const environment = {};
  for (const name of ["PATH", "Path", "SystemRoot", "WINDIR", "TEMP", "TMP"]) {
    if (process.env[name]) environment[name] = process.env[name];
  }
  return {
    ...environment,
    NODE_ENV: "production",
    NEXT_TELEMETRY_DISABLED: "1",
    CONNECTMD_API_BASE_URL: safeApiOrigin,
    CONNECTMD_RECRUITING_ENABLED: "false",
    NEXT_PUBLIC_API_BASE_URL: "",
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "",
    NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED: "false",
    NEXT_PUBLIC_SITE_URL: "https://connect.md",
    E2E_BASE_URL: safeProxyOrigin,
  };
}

async function prepareStandaloneRuntime() {
  if (!existsSync(NEXT_STANDALONE_SERVER)) {
    throw new Error("browser release build did not produce the standalone server artifact");
  }
  if (!existsSync(PUBLIC_DIRECTORY) || !existsSync(NEXT_STATIC_DIRECTORY)) {
    throw new Error("browser release build did not produce the standalone static inputs");
  }
  await rm(NEXT_STANDALONE_PUBLIC_DIRECTORY, { force: true, recursive: true });
  await rm(NEXT_STANDALONE_STATIC_DIRECTORY, { force: true, recursive: true });
  await cp(PUBLIC_DIRECTORY, NEXT_STANDALONE_PUBLIC_DIRECTORY, { recursive: true });
  await cp(NEXT_STATIC_DIRECTORY, NEXT_STANDALONE_STATIC_DIRECTORY, { recursive: true });
}

function assertRunningChild(child, expectedPid, stage) {
  if (
    !child ||
    !child.pid ||
    child.pid !== expectedPid ||
    child.spawnError ||
    child.exitCode !== null ||
    child.signalCode !== null
  ) {
    throw new Error(`Next production server did not remain alive through ${stage}`);
  }
}

function exactTempAuditDirectory(directory) {
  const tempRoot = resolve(tmpdir());
  const exactDirectory = resolve(directory);
  const relativeDirectory = relative(tempRoot, exactDirectory);
  if (
    !relativeDirectory ||
    relativeDirectory.startsWith("..") ||
    relativeDirectory.includes("..\\") ||
    relativeDirectory.includes("../") ||
    dirname(exactDirectory) !== tempRoot ||
    !relativeDirectory.startsWith(NEXT_EGRESS_AUDIT_PREFIX)
  ) {
    throw new Error("invalid browser release egress audit directory");
  }
  return exactDirectory;
}

async function createNextEgressAudit() {
  const directory = exactTempAuditDirectory(
    await mkdtemp(resolve(tmpdir(), NEXT_EGRESS_AUDIT_PREFIX)),
  );
  return {
    directory,
    path: resolve(directory, NEXT_EGRESS_AUDIT_FILE),
  };
}

async function removeNextEgressAudit(directory) {
  if (!directory) return;
  await rm(exactTempAuditDirectory(directory), { force: true, recursive: true });
}

function validateNextEgressAudit(auditPath, apiOrigin) {
  const audit = JSON.parse(readFileSync(auditPath, "utf8"));
  const expectedOrigin = strictHttpOrigin(apiOrigin, "fixture API origin");
  const expectedKeys = ["blocked_attempts", "fixture_origin", "version"];
  if (
    !audit ||
    typeof audit !== "object" ||
    Array.isArray(audit) ||
    Object.keys(audit).sort().join("\n") !== expectedKeys.join("\n") ||
    audit.version !== 1 ||
    audit.fixture_origin !== expectedOrigin ||
    !Array.isArray(audit.blocked_attempts) ||
    audit.blocked_attempts.length !== 0
  ) {
    throw new Error("browser release Next egress audit failed");
  }
}

function collectPlaywrightTests(suites, tests = []) {
  if (!Array.isArray(suites)) throw new Error("invalid Playwright JSON suite receipt");
  for (const suite of suites) {
    if (!suite || typeof suite !== "object") throw new Error("invalid Playwright JSON suite receipt");
    if (Array.isArray(suite.specs)) tests.push(...suite.specs);
    if (Array.isArray(suite.suites)) collectPlaywrightTests(suite.suites, tests);
  }
  return tests;
}

function boundedPublicReleaseLocation(location) {
  if (!location || typeof location !== "object" || Array.isArray(location)) return null;
  if (typeof location.file !== "string") return null;
  const file = location.file.replaceAll("\\", "/");
  if (file !== PUBLIC_RELEASE_SPEC_PATH && !file.endsWith(`/${PUBLIC_RELEASE_SPEC_PATH}`)) return null;
  if (
    !Number.isInteger(location.line) ||
    location.line < 1 ||
    location.line > MAX_PUBLIC_RELEASE_SPEC_LINE ||
    !Number.isInteger(location.column) ||
    location.column < 1 ||
    location.column > MAX_PUBLIC_RELEASE_SPEC_COLUMN
  ) {
    return null;
  }
  return { line: location.line, column: location.column };
}

function firstPlaywrightErrorLocation(spec) {
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) return null;
  const projectTests = Array.isArray(spec.tests) ? spec.tests : [];
  for (const projectTest of projectTests) {
    if (!projectTest || typeof projectTest !== "object" || Array.isArray(projectTest)) continue;
    const results = Array.isArray(projectTest.results) ? projectTest.results : [];
    for (const result of results) {
      if (!result || typeof result !== "object" || Array.isArray(result)) continue;
      const resultLocation = boundedPublicReleaseLocation(result.errorLocation);
      if (resultLocation) return resultLocation;
      if (Array.isArray(result.errors)) {
        for (const error of result.errors) {
          const errorLocation = boundedPublicReleaseLocation(error?.location);
          if (errorLocation) return errorLocation;
        }
      }
    }
  }
  return null;
}

function boundedLayoutDiagnostic(annotation) {
  if (!annotation || typeof annotation !== "object" || Array.isArray(annotation)) return null;
  if (
    annotation.type !== LAYOUT_DIAGNOSTIC_TYPE ||
    typeof annotation.description !== "string" ||
    annotation.description.length > 256
  ) {
    return null;
  }
  let diagnostic;
  try {
    diagnostic = JSON.parse(annotation.description);
  } catch {
    return null;
  }
  if (!diagnostic || typeof diagnostic !== "object" || Array.isArray(diagnostic)) return null;
  if (
    !Number.isInteger(diagnostic.route_index) ||
    diagnostic.route_index < 0 ||
    diagnostic.route_index > 2 ||
    !Number.isInteger(diagnostic.viewport_width) ||
    !LAYOUT_DIAGNOSTIC_VIEWPORTS.has(diagnostic.viewport_width) ||
    !Array.isArray(diagnostic.element_categories) ||
    diagnostic.element_categories.length < 1 ||
    diagnostic.element_categories.length > 4 ||
    !Array.isArray(diagnostic.element_indices) ||
    diagnostic.element_indices.length !== diagnostic.element_categories.length
  ) {
    return null;
  }
  const categories = [];
  const indices = [];
  for (const [position, category] of diagnostic.element_categories.entries()) {
    const index = diagnostic.element_indices[position];
    if (
      typeof category !== "string" ||
      !LAYOUT_DIAGNOSTIC_CATEGORIES.has(category) ||
      !Number.isInteger(index) ||
      index < 0 ||
      index > MAX_LAYOUT_DIAGNOSTIC_DOM_INDEX
    ) {
      return null;
    }
    categories.push(category);
    indices.push(index);
  }
  return {
    route_index: diagnostic.route_index,
    viewport_width: diagnostic.viewport_width,
    element_indices: indices,
    element_categories: categories,
  };
}

function firstLayoutDiagnostic(spec) {
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) return null;
  const annotations = [];
  const projectTests = Array.isArray(spec.tests) ? spec.tests : [];
  for (const projectTest of projectTests) {
    if (!projectTest || typeof projectTest !== "object" || Array.isArray(projectTest)) continue;
    if (Array.isArray(projectTest.annotations)) annotations.push(...projectTest.annotations);
    const results = Array.isArray(projectTest.results) ? projectTest.results : [];
    for (const result of results) {
      if (result && typeof result === "object" && !Array.isArray(result) && Array.isArray(result.annotations)) {
        annotations.push(...result.annotations);
      }
    }
  }
  for (const annotation of annotations) {
    const diagnostic = boundedLayoutDiagnostic(annotation);
    if (diagnostic) return diagnostic;
  }
  return null;
}

function collectFailedPlaywrightSpecIndices(suites, state) {
  if (!Array.isArray(suites)) return;
  for (const suite of suites) {
    if (!suite || typeof suite !== "object" || Array.isArray(suite)) continue;
    if (Array.isArray(suite.specs)) {
      for (const spec of suite.specs) {
        const index = state.specCount;
        state.specCount = Math.min(state.specCount + 1, EXPECTED_PLAYWRIGHT_TESTS);
        if (
          index < EXPECTED_PLAYWRIGHT_TESTS &&
          spec &&
          typeof spec === "object" &&
          !Array.isArray(spec) &&
          spec.ok === false
        ) {
          state.indices.push(index);
          const location = firstPlaywrightErrorLocation(spec);
          if (location && state.locations.length < EXPECTED_PLAYWRIGHT_TESTS) {
            state.locations.push({ spec: index, ...location });
          }
          const layout = firstLayoutDiagnostic(spec);
          if (layout && state.layout.length < EXPECTED_PLAYWRIGHT_TESTS) {
            state.layout.push({ spec: index, ...layout });
          }
        }
      }
    }
    collectFailedPlaywrightSpecIndices(suite.suites, state);
  }
}

function diagnosticStatus(stats, key) {
  return stats &&
    Number.isInteger(stats[key]) &&
    stats[key] >= 0 &&
    stats[key] <= EXPECTED_PLAYWRIGHT_TESTS
    ? stats[key]
    : "unknown";
}

export function summarizePlaywrightResult(result) {
  const exitCode = Number.isInteger(result?.code) && result.code >= 0 && result.code <= 255
    ? result.code
    : "unknown";
  const signal = result?.signal === null
    ? "none"
    : typeof result?.signal === "string"
      ? "present"
      : "unknown";
  let receipt;
  try {
    receipt = JSON.parse(typeof result?.stdout === "string" ? result.stdout : "");
  } catch {
    return `browser release Playwright stage failed: exit=${exitCode} signal=${signal} receipt=invalid`;
  }
  if (
    !receipt ||
    typeof receipt !== "object" ||
    Array.isArray(receipt) ||
    !receipt.stats ||
    typeof receipt.stats !== "object" ||
    Array.isArray(receipt.stats)
  ) {
    return `browser release Playwright stage failed: exit=${exitCode} signal=${signal} receipt=invalid`;
  }
  const failedTests = { indices: [], locations: [], layout: [], specCount: 0 };
  collectFailedPlaywrightSpecIndices(receipt.suites, failedTests);
  const failedSpecs = failedTests.indices.length > 0 ? ` failed_specs=${JSON.stringify(failedTests.indices)}` : "";
  const failedLocations = failedTests.locations.length > 0
    ? ` failed_locations=${JSON.stringify(failedTests.locations)}`
    : "";
  const layoutDiagnostics = failedTests.layout.length > 0
    ? ` layout=${JSON.stringify(failedTests.layout)}`
    : "";
  return [
    `browser release Playwright stage failed: exit=${exitCode}`,
    `signal=${signal}`,
    `expected=${diagnosticStatus(receipt.stats, "expected")}`,
    `failed=${failedTests.indices.length}`,
    `skipped=${diagnosticStatus(receipt.stats, "skipped")}`,
    `unexpected=${diagnosticStatus(receipt.stats, "unexpected")}`,
    `flaky=${diagnosticStatus(receipt.stats, "flaky")}${failedSpecs}${failedLocations}${layoutDiagnostics}`,
  ].join(" ");
}

export function validatePlaywrightJsonReceipt(raw) {
  const receipt = JSON.parse(raw);
  if (
    !receipt ||
    typeof receipt !== "object" ||
    Array.isArray(receipt) ||
    Object.keys(receipt).sort().join("\n") !== ["config", "errors", "stats", "suites"].join("\n") ||
    !receipt.config ||
    typeof receipt.config !== "object" ||
    Array.isArray(receipt.config) ||
    !Array.isArray(receipt.errors) ||
    !Array.isArray(receipt.suites) ||
    !receipt.stats ||
    typeof receipt.stats !== "object" ||
    Array.isArray(receipt.stats)
  ) {
    throw new Error("invalid Playwright JSON receipt");
  }
  const stats = receipt.stats;
  if (
    Object.keys(stats).sort().join("\n") !==
      ["duration", "expected", "flaky", "skipped", "startTime", "unexpected"].join("\n") ||
    typeof stats.startTime !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u.test(stats.startTime) ||
    Number.isNaN(Date.parse(stats.startTime)) ||
    typeof stats.duration !== "number" ||
    !Number.isFinite(stats.duration) ||
    stats.duration < 0
  ) {
    throw new Error("invalid Playwright JSON statistics");
  }
  for (const key of ["expected", "skipped", "unexpected", "flaky"]) {
    if (!Number.isInteger(stats[key]) || stats[key] < 0) throw new Error("invalid Playwright JSON statistics");
  }
  const tests = collectPlaywrightTests(receipt.suites);
  const results = [];
  for (const spec of tests) {
    if (
      !spec ||
      typeof spec !== "object" ||
      spec.ok !== true ||
      !Array.isArray(spec.tests) ||
      spec.tests.length !== 1
    ) {
      throw new Error("invalid Playwright JSON test receipt");
    }
    const [projectTest] = spec.tests;
    if (
      !projectTest ||
      typeof projectTest !== "object" ||
      projectTest.expectedStatus !== "passed" ||
      projectTest.status !== "expected" ||
      !Array.isArray(projectTest.results) ||
      projectTest.results.length !== 1
    ) {
      throw new Error("invalid Playwright JSON test receipt");
    }
    const [result] = projectTest.results;
    if (
      !result ||
      typeof result !== "object" ||
      result.status !== "passed" ||
      !Array.isArray(result.errors) ||
      result.errors.length !== 0
    ) {
      throw new Error("invalid Playwright JSON test receipt");
    }
    results.push(result);
  }
  const passed = results.filter((result) => result && result.status === "passed").length;
  const interrupted = results.filter((result) => result && result.status === "interrupted").length;
  if (
    tests.length !== EXPECTED_PLAYWRIGHT_TESTS ||
    results.length !== EXPECTED_PLAYWRIGHT_TESTS ||
    passed !== EXPECTED_PLAYWRIGHT_TESTS ||
    interrupted !== 0 ||
    stats.expected !== EXPECTED_PLAYWRIGHT_TESTS ||
    stats.skipped !== 0 ||
    stats.unexpected !== 0 ||
    stats.flaky !== 0 ||
    !Array.isArray(receipt.errors) ||
    receipt.errors.length !== 0
  ) {
    throw new Error("browser release Playwright receipt was not an exact nine-pass result");
  }
  return receipt;
}

function spawnChild(command, args, options) {
  const child = spawn(command, args, {
    ...options,
    detached: process.platform !== "win32",
    windowsHide: true,
  });
  child.spawnError = null;
  child.once("error", (error) => {
    child.spawnError = error;
  });
  return child;
}

function terminateProcessTree(child, force) {
  if (!child.pid) return Promise.resolve();
  if (process.platform === "win32") {
    const args = ["/PID", String(child.pid), "/T"];
    if (force) args.push("/F");
    return new Promise((resolvePromise) => {
      let settled = false;
      let timeout;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        resolvePromise();
      };
      const fallback = () => {
        try {
          child.kill(force ? "SIGKILL" : "SIGTERM");
        } catch {
          // The exact child already exited.
        }
        finish();
      };
      const killer = spawn("taskkill", args, { stdio: "ignore", windowsHide: true });
      killer.once("error", fallback);
      killer.once("exit", (code) => {
        if (code === 0) finish();
        else fallback();
      });
      timeout = setTimeout(() => {
        try {
          killer.kill();
        } catch {
          // The taskkill helper already exited.
        }
        fallback();
      }, 2000).unref();
    });
  }
  try {
    process.kill(-child.pid, force ? "SIGKILL" : "SIGTERM");
  } catch {
    try {
      child.kill(force ? "SIGKILL" : "SIGTERM");
    } catch {
      // The exact process group already exited.
    }
  }
  return Promise.resolve();
}

function wait(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

function waitWithoutKeepingProcessAlive(milliseconds) {
  return new Promise((resolvePromise) => {
    const timer = setTimeout(resolvePromise, milliseconds);
    timer.unref();
  });
}

function httpStatus(url) {
  return new Promise((resolvePromise) => {
    const request = httpRequest(url, { method: "GET", timeout: 1000 }, (response) => {
      const status = response.statusCode ?? 0;
      response.resume();
      response.once("end", () => resolvePromise(status));
    });
    request.once("error", () => resolvePromise(0));
    request.once("timeout", () => request.destroy());
    request.end();
  });
}

async function waitForNext(origin, child) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.spawnError || child.exitCode !== null || child.signalCode !== null) {
      throw new Error("Next production server exited before readiness");
    }
    if ((await httpStatus(`${origin}/`)) === 200) return;
    await wait(250);
  }
  throw new Error("Next production server did not become ready");
}

function waitForChild(child) {
  return new Promise((resolvePromise, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolvePromise({ code, signal }));
  });
}

export function waitForChildOutput(child) {
  let stdout = "";
  let stderr = "";
  child.stdout?.setEncoding("utf8");
  child.stderr?.setEncoding("utf8");
  child.stdout?.on("data", (chunk) => {
    stdout += chunk;
  });
  child.stderr?.on("data", (chunk) => {
    stderr += chunk;
  });
  return new Promise((resolvePromise, rejectPromise) => {
    child.once("error", rejectPromise);
    child.once("close", (code, signal) => resolvePromise({ code, signal, stdout, stderr }));
  });
}

export function classifyMonacoAssets(directoryExists, loaderExists) {
  if (!directoryExists && !loaderExists) return "create";
  if (directoryExists && loaderExists) return "reuse";
  throw new Error("the Monaco asset directory is partial or inconsistent");
}

export async function runListenCleanupProbe() {
  for (const failedStage of [0, 1, 2]) {
    const blocker = createServer();
    const blockerOrigin = await listen(blocker);
    const blockedPort = Number(new URL(blockerOrigin).port);
    const servers = [createServer(), createServer(), createServer()];
    try {
      for (const [stage, server] of servers.entries()) {
        await listen(server, stage === failedStage ? blockedPort : 0);
      }
      throw new Error(`listen stage ${failedStage} unexpectedly succeeded`);
    } catch (error) {
      if (String(error?.message ?? error).includes("unexpectedly succeeded")) throw error;
    } finally {
      await Promise.all(servers.map((server) => closeServer(server)));
      await closeServer(blocker);
    }
    if (servers.some((server) => server.listening || server.address() !== null)) {
      throw new Error(`listen stage ${failedStage} leaked a server`);
    }
    if (blocker.listening || blocker.address() !== null) {
      throw new Error(`listen stage ${failedStage} leaked the reservation server`);
    }
  }
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = waitForChild(child).catch(() => undefined);
  await terminateProcessTree(child, false);
  await Promise.race([exited, waitWithoutKeepingProcessAlive(5000)]);
  if (child.exitCode === null && child.signalCode === null) {
    await terminateProcessTree(child, true);
    await Promise.race([exited, waitWithoutKeepingProcessAlive(2000)]);
  }
  if (child.exitCode === null && child.signalCode === null) {
    try {
      child.kill("SIGKILL");
    } catch {
      // The exact child already exited.
    }
    await Promise.race([exited, waitWithoutKeepingProcessAlive(1000)]);
  }
  if (child.exitCode === null && child.signalCode === null) throw new Error("child process did not exit after bounded cleanup");
}

export async function runChildCleanupProbe() {
  const child = spawnChild(
    process.execPath,
    ["-e", "setInterval(() => {}, 1000)"],
    { stdio: "ignore" },
  );
  await wait(50);
  await stopChild(child);
  if (child.exitCode === null && child.signalCode === null) throw new Error("child cleanup probe leaked a process");
}

async function closeServer(server) {
  if (!server) return;
  await new Promise((resolvePromise) => {
    let settled = false;
    let timeout;
    const onError = () => finish();
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      server.off("error", onError);
      resolvePromise();
    };
    server.once("error", onError);
    try {
      server.close(finish);
    } catch {
      finish();
      return;
    }
    timeout = setTimeout(() => {
      server.closeAllConnections?.();
      server.closeIdleConnections?.();
      finish();
    }, 3000).unref();
  });
}

export async function main() {
  if (!existsSync(NEXT_BUILD_ID)) throw new Error("run the browser release production build before the browser gate");
  loadAndValidateBrowserReleaseBuildReceipt();
  if (process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) throw new Error("the hermetic gate requires no Clerk key");
  if (process.env.NEXT_PUBLIC_API_BASE_URL) throw new Error("the hermetic gate requires same-origin API links");

  const fixture = loadFixtures();
  let apiServer = null;
  let nextPortServer = null;
  let proxyServer = null;
  let nextProcess = null;
  let playwrightProcess = null;
  let monacoCopyProcess = null;
  let nextEgressAuditDirectory = null;
  let nextEgressAuditPath = null;
  let monacoPreparedByHarness = false;
  let standalonePreparedByHarness = false;
  let cleaned = false;
  const cleanup = async () => {
    if (cleaned) return;
    cleaned = true;
    await stopChild(monacoCopyProcess);
    await stopChild(playwrightProcess);
    await stopChild(nextProcess);
    await closeServer(nextPortServer);
    await closeServer(proxyServer);
    await closeServer(apiServer);
    if (monacoPreparedByHarness) {
      await rm(MONACO_DIRECTORY, { force: true, recursive: true });
    }
    if (standalonePreparedByHarness) {
      await rm(NEXT_STANDALONE_PUBLIC_DIRECTORY, { force: true, recursive: true });
      await rm(NEXT_STANDALONE_STATIC_DIRECTORY, { force: true, recursive: true });
    }
    await removeNextEgressAudit(nextEgressAuditDirectory);
  };
  const onSignal = () => {
    void cleanup().finally(() => {
      process.exitCode = 143;
    });
  };
  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);
  try {
    const monacoState = classifyMonacoAssets(
      existsSync(MONACO_DIRECTORY),
      existsSync(MONACO_LOADER),
    );
    if (monacoState === "create") {
      mkdirSync(MONACO_DIRECTORY);
      monacoPreparedByHarness = true;
      monacoCopyProcess = spawnChild(
        process.execPath,
        [MONACO_COPY_SCRIPT],
        { cwd: WEB_ROOT, env: safeEnvironment("http://127.0.0.1", "http://127.0.0.1"), stdio: "ignore" },
      );
      const copyResult = await waitForChild(monacoCopyProcess);
      monacoCopyProcess = null;
      if (copyResult.code !== 0 || !existsSync(MONACO_LOADER)) {
        throw new Error("self-hosted Monaco assets could not be prepared");
      }
    }
    standalonePreparedByHarness = true;
    await prepareStandaloneRuntime();
    apiServer = createFixtureApi(fixture);
    const apiOrigin = await listen(apiServer);
    nextPortServer = createServer();
    const nextOrigin = await listen(nextPortServer);
    await closeServer(nextPortServer);
    nextPortServer = null;
    proxyServer = createReverseProxy(apiOrigin, nextOrigin);
    const proxyOrigin = await listen(proxyServer);
    const environment = safeEnvironment(apiOrigin, proxyOrigin);
    const nextEgressAudit = await createNextEgressAudit();
    nextEgressAuditDirectory = nextEgressAudit.directory;
    nextEgressAuditPath = nextEgressAudit.path;
    const nextEnvironment = {
      ...environment,
      CONNECTMD_E2E_FIXTURE_API_ORIGIN: strictHttpOrigin(apiOrigin, "fixture API origin"),
      CONNECTMD_E2E_NEXT_EGRESS_AUDIT_PATH: nextEgressAuditPath,
      HOSTNAME: "127.0.0.1",
      PORT: new URL(nextOrigin).port,
    };
    nextProcess = spawnChild(
      process.execPath,
      [
        "--require",
        NEXT_EGRESS_GUARD,
        NEXT_STANDALONE_SERVER,
      ],
      { cwd: NEXT_STANDALONE_DIRECTORY, env: nextEnvironment, stdio: "ignore" },
    );
    const nextProcessId = nextProcess.pid;
    await waitForNext(nextOrigin, nextProcess);
    assertRunningChild(nextProcess, nextProcessId, "readiness");
    playwrightProcess = spawnChild(
      process.execPath,
      [
        resolve(WEB_ROOT, "node_modules", "@playwright", "test", "cli.js"),
        "test",
        "--config=playwright.config.ts",
        "--reporter=json",
      ],
      { cwd: WEB_ROOT, env: environment, stdio: ["ignore", "pipe", "pipe"] },
    );
    const result = await waitForChildOutput(playwrightProcess);
    if (result.code !== 0) process.stderr.write(`${summarizePlaywrightResult(result)}\n`);
    if (result.code !== 0) throw new Error("browser release gate failed");
    try {
      validatePlaywrightJsonReceipt(result.stdout);
    } catch (error) {
      process.stderr.write(`${summarizePlaywrightResult(result)}\n`);
      throw error;
    }
    assertRunningChild(nextProcess, nextProcessId, "Playwright");
    validateNextEgressAudit(nextEgressAuditPath, apiOrigin);
  } finally {
    process.off("SIGINT", onSignal);
    process.off("SIGTERM", onSignal);
    await cleanup();
  }
}
