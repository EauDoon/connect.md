import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { afterEach, describe, expect, it } from "vitest";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const repoRoot = resolve(webRoot, "..", "..");
const temporaryRoots: string[] = [];

function source(relativePath: string) {
  return readFileSync(resolve(repoRoot, relativePath), "utf8");
}

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((path) => rm(path, { force: true, recursive: true })));
});

describe("self-hosted Monaco contract", () => {
  it("copies the locked Monaco distribution to an isolated target", async () => {
    const scratch = await mkdtemp(join(tmpdir(), "connectmd-monaco-"));
    temporaryRoots.push(scratch);
    const target = join(scratch, "vs");
    const result = spawnSync(process.execPath, [join(webRoot, "scripts", "copy-monaco-assets.mjs"), target], {
      cwd: webRoot,
      encoding: "utf8",
    });

    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout).toContain("Copied hardened Monaco 0.56.0 assets");
    for (const path of ["loader.js", "editor/editor.main.css", "editor/editor.main.js"]) {
      expect(existsSync(join(target, path)), path).toBe(true);
    }
    expect((await readFile(join(target, "loader.js"), "utf8")).length).toBeGreaterThan(1_000);
    const editorBundle = (await readdir(target)).find(
      (path) => path.startsWith("editor-") && path.endsWith(".js"),
    );
    expect(editorBundle).toBeDefined();
    const originalEditorSource = await readFile(
      join(webRoot, "node_modules", "monaco-editor", "min", "vs", editorBundle!),
      "utf8",
    );
    const editorSource = await readFile(join(target, editorBundle!), "utf8");
    expect(originalEditorSource).toContain(".IN_PLACE||!1");
    expect(editorSource).not.toContain(".IN_PLACE||!1");
  }, 15_000);

  it("pins Monaco directly and prepares same-origin assets before development and production builds", () => {
    const manifest = JSON.parse(source("apps/web/package.json"));
    const lock = JSON.parse(source("apps/web/package-lock.json"));

    expect(manifest.dependencies["monaco-editor"]).toBe("0.56.0");
    expect(manifest.scripts.predev).toBe("node scripts/copy-monaco-assets.mjs");
    expect(manifest.scripts.prebuild).toBe("node scripts/copy-monaco-assets.mjs");
    expect(manifest.overrides["monaco-editor"].dompurify).toBe("3.4.13");
    expect(lock.packages[""].dependencies["monaco-editor"]).toBe("0.56.0");
    expect(lock.packages["node_modules/monaco-editor"]).toMatchObject({ version: "0.56.0" });
    expect(lock.packages["node_modules/dompurify"]).toMatchObject({ version: "3.4.13" });
    expect(lock.packages["node_modules/monaco-editor"].peer).not.toBe(true);
  });

  it("loads Monaco from the site origin and excludes generated assets from source contexts", () => {
    const editor = source("apps/web/components/markdown-editor.tsx");

    expect(editor).toContain('import { loader } from "@monaco-editor/react";');
    expect(editor).toContain('loader.config({ paths: { vs: "/monaco/vs" } });');
    expect(source(".gitignore")).toContain("apps/web/public/monaco/");
    expect(source("apps/web/.dockerignore")).toContain("public/monaco/");
  });

  it("removes the Monaco CDN from production policy and probes the baked same-origin loader", () => {
    const nginx = source("infra/nginx/conf.d/connectmd.tls.conf");
    const smoke = source("infra/tests/https-smoke.sh");

    expect(nginx).not.toContain("cdn.jsdelivr.net");
    expect(smoke).not.toContain("cdn.jsdelivr.net");
    expect(smoke).toContain('monaco_loader="$(https_get /monaco/vs/loader.js)"');
    expect(smoke).toContain('assert_monaco_loader "$monaco_loader"');
  });
});
