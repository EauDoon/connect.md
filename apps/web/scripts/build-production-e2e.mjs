import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const NEXT_DIRECTORY = resolve(WEB_ROOT, ".next");
const NEXT_BUILD_ID = resolve(NEXT_DIRECTORY, "BUILD_ID");
export const BROWSER_RELEASE_BUILD_RECEIPT = resolve(
  NEXT_DIRECTORY,
  "browser-release-build-receipt.json",
);
export const BROWSER_RELEASE_BUILD_PROFILE = "hermetic-production-e2e-v1";

const BUILD_INPUT_DIRECTORIES = ["app", "components", "lib", "public", "scripts"];
const BUILD_INPUT_FILES = [
  "middleware.ts",
  "next-env.d.ts",
  "next.config.ts",
  "package-lock.json",
  "package.json",
  "postcss.config.mjs",
  "tailwind.config.ts",
  "tsconfig.json",
];
const SAFE_ENVIRONMENT_NAMES = ["PATH", "Path", "SystemRoot", "WINDIR", "TEMP", "TMP"];
const BUILD_ID_PATTERN = /^[A-Za-z0-9_-]{1,256}$/u;
const NEXT_DOTENV_FILES = [".env.production.local", ".env.local", ".env.production", ".env"];

function exactKeys(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value) {
  return JSON.stringify(value);
}

export function compareCanonicalPaths(left, right) {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function canonicalRelativePath(absolutePath) {
  const relativePath = relative(WEB_ROOT, absolutePath).replaceAll("\\", "/");
  if (!relativePath || relativePath.startsWith("../") || relativePath.includes("/../")) {
    throw new Error("browser release build input escaped the web root");
  }
  return relativePath;
}

function collectFiles(absolutePath, files) {
  const stat = lstatSync(absolutePath);
  if (stat.isSymbolicLink()) throw new Error("browser release build inputs must not contain symbolic links");
  if (stat.isDirectory()) {
    for (const entry of readdirSync(absolutePath, { withFileTypes: true }).sort((left, right) => compareCanonicalPaths(left.name, right.name))) {
      collectFiles(resolve(absolutePath, entry.name), files);
    }
    return;
  }
  if (!stat.isFile()) throw new Error("browser release build inputs must be regular files");
  const bytes = readFileSync(absolutePath);
  files.push({ path: canonicalRelativePath(absolutePath), size: bytes.byteLength, sha256: sha256(bytes) });
}

function validateBuildInputManifest(manifest) {
  if (!exactKeys(manifest, ["files", "version"]) || manifest.version !== 1 || !Array.isArray(manifest.files)) {
    throw new Error("invalid browser release build-input manifest");
  }
  let priorPath = "";
  for (const entry of manifest.files) {
    if (
      !exactKeys(entry, ["path", "sha256", "size"]) ||
      typeof entry.path !== "string" ||
      !entry.path ||
      entry.path.includes("\\") ||
      entry.path.startsWith("/") ||
      entry.path.includes("../") ||
      !Number.isInteger(entry.size) ||
      entry.size < 0 ||
      typeof entry.sha256 !== "string" ||
      !/^[0-9a-f]{64}$/u.test(entry.sha256) ||
      entry.path <= priorPath
    ) {
      throw new Error("invalid browser release build-input manifest entry");
    }
    priorPath = entry.path;
  }
  return manifest;
}

export function createBrowserReleaseBuildInputManifest() {
  const files = [];
  for (const relativePath of BUILD_INPUT_DIRECTORIES) {
    const absolutePath = resolve(WEB_ROOT, relativePath);
    if (!existsSync(absolutePath)) throw new Error(`missing browser release build-input directory: ${relativePath}`);
    collectFiles(absolutePath, files);
  }
  for (const relativePath of BUILD_INPUT_FILES) {
    const absolutePath = resolve(WEB_ROOT, relativePath);
    if (!existsSync(absolutePath)) throw new Error(`missing browser release build-input file: ${relativePath}`);
    collectFiles(absolutePath, files);
  }
  files.sort((left, right) => compareCanonicalPaths(left.path, right.path));
  return validateBuildInputManifest({ version: 1, files });
}

function readBuildId() {
  const raw = readFileSync(NEXT_BUILD_ID, "utf8");
  const buildId = raw.endsWith("\n") ? raw.slice(0, -1) : raw;
  if (raw !== buildId && raw !== `${buildId}\n`) throw new Error("invalid Next BUILD_ID framing");
  if (!BUILD_ID_PATTERN.test(buildId)) throw new Error("invalid Next BUILD_ID");
  return buildId;
}

export function assertNoNextDotenvFiles(root = WEB_ROOT) {
  for (const filename of NEXT_DOTENV_FILES) {
    if (existsSync(resolve(root, filename))) {
      throw new Error("browser release build forbids Next dotenv files");
    }
  }
}

export function validateBrowserReleaseBuildReceipt(receipt, buildId, currentManifest) {
  if (
    !exactKeys(receipt, [
      "build_id",
      "build_input_manifest_sha256",
      "build_inputs_after",
      "build_inputs_before",
      "build_profile",
      "version",
    ]) ||
    receipt.version !== 1 ||
    receipt.build_profile !== BROWSER_RELEASE_BUILD_PROFILE ||
    receipt.build_id !== buildId ||
    !BUILD_ID_PATTERN.test(receipt.build_id) ||
    typeof receipt.build_input_manifest_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(receipt.build_input_manifest_sha256)
  ) {
    throw new Error("invalid browser release build receipt");
  }
  const before = validateBuildInputManifest(receipt.build_inputs_before);
  const after = validateBuildInputManifest(receipt.build_inputs_after);
  const current = validateBuildInputManifest(currentManifest);
  const beforeJson = canonicalJson(before);
  if (
    beforeJson !== canonicalJson(after) ||
    beforeJson !== canonicalJson(current) ||
    receipt.build_input_manifest_sha256 !== sha256(beforeJson)
  ) {
    throw new Error("browser release build receipt does not bind current build inputs");
  }
  return receipt;
}

export function loadAndValidateBrowserReleaseBuildReceipt() {
  assertNoNextDotenvFiles();
  const receipt = JSON.parse(readFileSync(BROWSER_RELEASE_BUILD_RECEIPT, "utf8"));
  return validateBrowserReleaseBuildReceipt(
    receipt,
    readBuildId(),
    createBrowserReleaseBuildInputManifest(),
  );
}

function safeBuildEnvironment() {
  const inherited = {};
  for (const name of SAFE_ENVIRONMENT_NAMES) {
    if (process.env[name]) inherited[name] = process.env[name];
  }
  return {
    ...inherited,
    NODE_ENV: "production",
    NEXT_TELEMETRY_DISABLED: "1",
    CONNECTMD_API_BASE_URL: "http://127.0.0.1:9",
    CONNECTMD_RECRUITING_ENABLED: "false",
    NEXT_PUBLIC_API_BASE_URL: "",
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "",
    NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED: "false",
    NEXT_PUBLIC_SITE_URL: "https://connect.md",
  };
}

function runNode(args) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(process.execPath, args, {
      cwd: WEB_ROOT,
      env: safeBuildEnvironment(),
      stdio: "inherit",
      windowsHide: true,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0 && signal === null) resolvePromise();
      else reject(new Error("browser release build command failed"));
    });
  });
}

async function main() {
  assertNoNextDotenvFiles();
  await runNode([resolve(WEB_ROOT, "scripts", "copy-monaco-assets.mjs")]);
  const before = createBrowserReleaseBuildInputManifest();
  await runNode([resolve(WEB_ROOT, "node_modules", "next", "dist", "bin", "next"), "build"]);
  await runNode([resolve(WEB_ROOT, "scripts", "check-route-js-budgets.mjs")]);
  const after = createBrowserReleaseBuildInputManifest();
  if (canonicalJson(before) !== canonicalJson(after)) {
    throw new Error("browser release build changed a tracked build input");
  }
  const buildId = readBuildId();
  const receipt = {
    version: 1,
    build_profile: BROWSER_RELEASE_BUILD_PROFILE,
    build_id: buildId,
    build_inputs_before: before,
    build_inputs_after: after,
    build_input_manifest_sha256: sha256(canonicalJson(before)),
  };
  mkdirSync(NEXT_DIRECTORY, { recursive: true });
  writeFileSync(BROWSER_RELEASE_BUILD_RECEIPT, `${JSON.stringify(receipt)}\n`, { encoding: "utf8", mode: 0o600 });
  validateBrowserReleaseBuildReceipt(receipt, buildId, after);
}

const isDirectExecution = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectExecution) {
  main().catch(() => {
    process.stderr.write("browser release build failed\n");
    process.exitCode = 1;
  });
}
