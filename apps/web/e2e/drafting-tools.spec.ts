import { expect, test } from "@playwright/test";
import { profileStarter, resumeStarter } from "../lib/markdown";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { resolve } from "node:path";

test("plain text and code interfaces share the canonical draft", async ({ page }) => {
  await page.goto("/md");
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  const source = page.getByRole("textbox", { name: "Canonical Markdown source" });
  const draft = profileStarter.replaceAll("Your Name", "Casey Example");
  await source.fill(draft);
  await expect(page.getByLabel("Sanitized Markdown preview")).toContainText("Casey Example");
  await page.getByRole("radio", { name: "Code editor", exact: true }).check();
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  await expect(source).toHaveValue(draft);
  await page.getByRole("link", { name: "Continue in Guided" }).click();
  await page.getByRole("navigation", { name: /Editing mode/ }).getByRole("link", { name: "Markdown", exact: true }).click();
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  await expect(source).toHaveValue(draft);
});

test("print preview contains sanitized body and restores editor focus", async ({ page }) => {
  await page.goto("/md");
  await page.getByRole("button", { name: "Print preview", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Print document preview" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Your Name");
  await expect(dialog).not.toContainText("schema_version");
  await dialog.screenshot({ path: test.info().outputPath("print-preview.png") });
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("navigation", { name: "Primary navigation", exact: true })).not.toBeVisible();
  await expect(dialog.locator(".markdown-prose")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Print document", exact: true })).not.toBeVisible();
  await page.emulateMedia({ media: "screen" });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Print preview", exact: true })).toBeFocused();
});

test("checkpoints compare, survive mode navigation, restore, and require deliberate removal", async ({ page }) => {
  await page.goto("/md");
  await page.getByText("Session checkpoints (0/5)", { exact: true }).click();
  await page.getByLabel("Checkpoint name").fill("Before editing");
  await page.getByRole("button", { name: "Keep checkpoint", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Checkpoint kept" })).toBeVisible();
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  await page.getByRole("textbox", { name: "Canonical Markdown source" }).fill(profileStarter.replaceAll("Your Name", "Avery Example"));
  const checkpointDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download checkpoint Before editing", exact: true }).click();
  const checkpointFile = await checkpointDownload;
  const checkpointStream = await checkpointFile.createReadStream();
  const checkpointChunks: Buffer[] = [];
  for await (const chunk of checkpointStream!) checkpointChunks.push(Buffer.from(chunk));
  expect(Buffer.concat(checkpointChunks).toString("utf8")).toBe(profileStarter);
  await expect(page.getByRole("textbox", { name: "Canonical Markdown source" })).toHaveValue(profileStarter.replaceAll("Your Name", "Avery Example"));
  await page.getByRole("button", { name: "Compare Before editing", exact: true }).click();
  await expect(page.getByLabel("Checkpoint comparison")).toContainText("Avery Example");
  await page.getByRole("link", { name: "Continue in Guided" }).click();
  await expect(page).toHaveURL(/\/human$/u);
  await expect(page.getByRole("heading", { name: "Make your work read like a signal." })).toBeVisible();
  await page.getByText("Session checkpoints (1/5)", { exact: true }).click();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Restore Before editing", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Restore Before editing", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Restored Before editing" })).toBeVisible();
  await page.getByRole("navigation", { name: /Editing mode/ }).getByRole("link", { name: "Markdown", exact: true }).click();
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  await expect(page.getByRole("textbox", { name: "Canonical Markdown source" })).toHaveValue(profileStarter);
  expect(await page.evaluate(() => { const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; })).toBe(true);
  await page.getByText("Session checkpoints (1/5)", { exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Remove Before editing", exact: true }).click();
  await expect(page.getByText("Session checkpoints (0/5)", { exact: true })).toBeVisible();
  await page.getByText("Session recovery", { exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Forget previous draft", exact: true }).click();
  expect(await page.evaluate(() => { const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; })).toBe(false);
});

test("recovery files restore unfinished work after reload and reject stale or invalid replacement", async ({ page }) => {
  const writes: string[] = [];
  page.on("request", (request) => { if (request.method() !== "GET") writes.push(request.url()); });
  await page.goto("/md");
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  const source = page.getByRole("textbox", { name: "Canonical Markdown source" });
  await source.fill("# Unfinished\n");
  await page.getByText("Session checkpoints (0/5)", { exact: true }).click();
  await page.getByLabel("Checkpoint name", { exact: true }).fill("Unfinished");
  await page.getByRole("button", { name: "Keep checkpoint", exact: true }).click();
  await page.getByText("Session recovery", { exact: true }).click();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download session recovery", exact: true }).click();
  const file = await downloaded;
  const chunks: Buffer[] = [];
  for await (const chunk of (await file.createReadStream())!) chunks.push(Buffer.from(chunk));
  const buffer = Buffer.concat(chunks);
  expect(JSON.parse(buffer.toString()).draft.markdown).toBe("# Unfinished\n");
  const upload = { name: "session.recovery.json", mimeType: "application/json", buffer };
  const input = page.getByLabel("Open a recovery file", { exact: true });
  await input.setInputFiles({ ...upload, buffer: Buffer.from("null") });
  await expect(page.getByRole("alert").filter({ hasText: "Invalid recovery" })).toBeVisible();
  await expect(source).toHaveValue("# Unfinished\n");
  await input.setInputFiles(upload);
  await expect(page.getByLabel("Recovery file review")).toBeVisible();
  await source.fill("# Newer work\n");
  await page.getByRole("button", { name: "Restore recovery session", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "draft changed" })).toBeVisible();
  await expect(source).toHaveValue("# Newer work\n");
  for (const phase of ["review", "read"] as const) {
    for (const mutation of ["save", "rename", "remove"] as const) {
      if (phase === "read") {
        await page.evaluate(() => {
          const original = File.prototype.arrayBuffer;
          const state = window as unknown as { recoveryReadStarted: boolean; finishRecoveryRead: () => void };
          state.recoveryReadStarted = false;
          File.prototype.arrayBuffer = function () {
            File.prototype.arrayBuffer = original;
            state.recoveryReadStarted = true;
            return new Promise<ArrayBuffer>((resolve, reject) => {
              state.finishRecoveryRead = () => { original.call(this).then(resolve, reject); };
            });
          };
        });
      }
      await input.setInputFiles(upload);
      if (phase === "read") await expect.poll(() => page.evaluate(() => (window as unknown as { recoveryReadStarted: boolean }).recoveryReadStarted)).toBe(true);
      else await expect(page.getByLabel("Recovery file review")).toBeVisible();
      if (mutation === "save") {
        await page.getByLabel("Checkpoint name", { exact: true }).fill(`Added during ${phase}`);
        await page.getByRole("button", { name: "Keep checkpoint", exact: true }).click();
      } else if (mutation === "rename") {
        await page.getByRole("button", { name: `Rename Added during ${phase}`, exact: true }).click();
        await page.getByLabel("New checkpoint name", { exact: true }).fill(`Renamed during ${phase}`);
        await page.getByRole("button", { name: "Save checkpoint name", exact: true }).click();
      } else {
        page.once("dialog", (dialog) => dialog.accept());
        await page.getByRole("button", { name: `Remove Renamed during ${phase}`, exact: true }).click();
      }
      if (phase === "read") await page.evaluate(() => (window as unknown as { finishRecoveryRead: () => void }).finishRecoveryRead());
      await expect(page.getByLabel("Recovery file review")).toBeVisible();
      await page.getByRole("button", { name: "Restore recovery session", exact: true }).click();
      await expect(page.getByRole("alert").filter({ hasText: "checkpoints changed" })).toBeVisible();
      await expect(page.getByLabel("Recovery file review")).toHaveCount(0);
      await expect(source).toHaveValue("# Newer work\n");
      await expect(page.getByText(`Session checkpoints (${mutation === "remove" ? 1 : 2}/5)`, { exact: true })).toBeVisible();
      if (mutation !== "remove") await expect(page.getByRole("button", { name: `Rename ${mutation === "save" ? "Added" : "Renamed"} during ${phase}`, exact: true })).toBeVisible();
    }
  }
  page.once("dialog", (dialog) => dialog.accept());
  await page.reload();
  await page.getByText("Session recovery", { exact: true }).click();
  await input.setInputFiles(upload);
  await expect(page.getByLabel("Recovery file review")).toBeVisible();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Restore recovery session", exact: true }).click();
  await expect(page.getByText("Session checkpoints (0/5)", { exact: true })).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Restore recovery session", exact: true }).click();
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  await expect(source).toHaveValue("# Unfinished\n");
  await expect(page.getByText("Session checkpoints (1/5)", { exact: true })).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Reset starter", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Undo draft replacement", exact: true }).click();
  await expect(source).toHaveValue("# Unfinished\n");
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(writes).toEqual([]);
});

test("authors rename checkpoints, replace literal source, choose filenames and compare exported bytes", async ({ page }) => {
  await page.goto("/md");
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  const source = page.getByRole("textbox", { name: "Canonical Markdown source" });
  await source.fill(profileStarter.replaceAll("Your Name", "Casey Example"));
  await page.getByText("Session checkpoints (0/5)", { exact: true }).click();
  await page.getByLabel("Checkpoint name", { exact: true }).fill("Original");
  await page.getByRole("button", { name: "Keep checkpoint", exact: true }).click();
  await page.getByRole("button", { name: "Rename Original", exact: true }).click();
  await page.getByLabel("New checkpoint name", { exact: true }).fill("Reviewed");
  await page.getByRole("button", { name: "Save checkpoint name", exact: true }).click();
  await expect(page.getByRole("button", { name: "Rename Reviewed", exact: true })).toBeFocused();
  await page.getByText("Find and replace source text", { exact: true }).click();
  await page.getByLabel("Find text", { exact: true }).fill("Casey");
  await page.getByRole("button", { name: "Next match", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(source).toBeFocused();
  expect(await source.evaluate((element: HTMLTextAreaElement) => element.value.slice(element.selectionStart, element.selectionEnd))).toBe("Casey");
  await page.getByLabel("Replace with", { exact: true }).fill("Avery");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Replace all matches", exact: true }).click();
  await expect(source).toHaveValue(profileStarter.replaceAll("Your Name", "Casey Example"));
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Replace all matches", exact: true }).click();
  await expect(source).toHaveValue(profileStarter.replaceAll("Your Name", "Avery Example"));
  await page.getByRole("radio", { name: "Preview and checks", exact: true }).check();
  await expect(source).not.toBeVisible();
  await page.getByLabel("Local filename (optional)", { exact: true }).fill("Recruiter copy.md");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download profile .md", exact: true }).click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toBe("recruiter-copy.md");
  const chunks: Buffer[] = [];
  for await (const chunk of (await file.createReadStream())!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString()).toBe(profileStarter.replaceAll("Your Name", "Avery Example"));
  await page.getByRole("radio", { name: "Source only", exact: true }).check();
  await source.fill(profileStarter.replaceAll("Your Name", "Jordan Example"));
  await page.getByRole("radio", { name: "Split view", exact: true }).check();
  await page.getByText("Changes since last Markdown download", { exact: true }).click();
  await expect(page.getByLabel("Last download comparison")).toContainText("Avery Example");
  await expect(page.getByLabel("Last download comparison")).toContainText("Jordan Example");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: test.info().outputPath("revised-authoring-desktop.png"), fullPage: true });
});

test("review links return from Guided to exact source and layouts remain usable on mobile", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/md");
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  const draft = profileStarter + "\nContact: person@example.invalid\n";
  await page.getByRole("textbox", { name: "Canonical Markdown source" }).fill(draft);
  await page.getByRole("radio", { name: "Preview and checks", exact: true }).check();
  await page.getByRole("link", { name: "Continue in Guided", exact: true }).click();
  await expect(page).toHaveURL(/\/human$/u);
  await page.getByRole("button", { name: "04 Download Validate and keep the file", exact: true }).click();
  await page.getByText(/^Sharing review \(/u).click();
  const line = draft.trimEnd().split("\n").length;
  await page.getByRole("link", { name: `Edit line ${line} in Markdown`, exact: true }).click();
  await expect(page).toHaveURL(/\/md$/u);
  const source = page.getByRole("textbox", { name: "Canonical Markdown source" });
  await expect(source).toBeFocused();
  expect(await source.evaluate((element: HTMLTextAreaElement) => element.value.slice(element.selectionStart, element.selectionEnd))).toBe("Contact: person@example.invalid");
  await expect(page.getByRole("radio", { name: "Split view", exact: true })).toBeChecked();
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByText("Session recovery", { exact: true }).click();
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: test.info().outputPath("revised-authoring-mobile.png"), fullPage: true });
});

test("pasted imports fail closed and detect resume kind without uploading", async ({ page }) => {
  const writes: string[] = [];
  page.on("request", (request) => { if (request.method() !== "GET") writes.push(request.url()); });
  await page.goto("/md");
  await page.getByText("Paste a complete Markdown draft", { exact: true }).click();
  await page.getByLabel("Markdown to import").fill("---\nschema: unsupported\n---\n# Example");
  await page.getByRole("button", { name: "Open pasted Markdown", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "schema" })).toBeVisible();
  await page.getByLabel("Markdown to import").fill(resumeStarter);
  await page.getByRole("button", { name: "Open pasted Markdown", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Opened pasted resume locally" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download resume .md", exact: true })).toBeEnabled();
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  await expect(page.getByRole("textbox", { name: "Canonical Markdown source" })).toHaveValue(resumeStarter);
  expect(writes).toEqual([]);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test("outline selects source and downloaded review fingerprints exact bytes", async ({ page }) => {
  await page.goto("/md");
  await page.getByText("Document outline and length", { exact: true }).click();
  await page.getByRole("button", { name: /^Edit About at line/ }).click();
  const source = page.getByRole("textbox", { name: "Canonical Markdown source" });
  await expect(source).toBeFocused();
  expect(await source.evaluate((element: HTMLTextAreaElement) => element.value.slice(element.selectionStart, element.selectionEnd))).toBe("## About");
  const draft = profileStarter.replaceAll("Your Name", "Avery Example") + "\nContact: person@example.invalid\n";
  await source.fill(draft);
  await page.getByText(/^Sharing review \(/).click();
  await expect(page.getByText(/An email address may be included/)).toBeVisible();
  const pendingDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download review report", exact: true }).click();
  const download = await pendingDownload;
  expect(download.suggestedFilename()).toBe("connectmd-profile-review.md");
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  const report = Buffer.concat(chunks).toString("utf8");
  expect(report).toContain(createHash("sha256").update(draft).digest("hex"));
  expect(report).not.toContain("Avery Example");
  expect(report).not.toContain("person@example.invalid");
  expect(await page.evaluate(() => { const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; })).toBe(true);
});

test("clipboard denial remains actionable without claiming a download", async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => { throw new Error("denied"); } }, configurable: true }));
  await page.goto("/md");
  await page.getByRole("button", { name: "Copy Markdown", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "denied clipboard access" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download profile .md", exact: true })).toBeEnabled();
});

test("clipboard copy preserves draft text across platform line endings and reports edits", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/md");
  await page.getByRole("button", { name: "Copy Markdown", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Current Markdown copied" })).toBeVisible();
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipboard.replace(/\r\n?/gu, "\n")).toBe(profileStarter);
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  await page.getByRole("textbox", { name: "Canonical Markdown source" }).fill(profileStarter.replaceAll("Your Name", "Casey Example"));
  await expect(page.getByRole("status").filter({ hasText: "draft changed after copying" })).toBeVisible();
});

test("expanded drafting tools reflow at 320 pixels and pass serious accessibility checks", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/md");
  for (const title of ["Session checkpoints (0/5)", "Paste a complete Markdown draft", "Document outline and length"]) await page.getByText(title, { exact: true }).click();
  await page.getByText(/^Writing review \(/).click();
  await page.getByText(/^Sharing review \(/).click();
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const require = createRequire(resolve(process.cwd(), "package.json"));
  await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
  const violations = await page.evaluate(async () => {
    const axe = (window as unknown as { axe: typeof import("axe-core") }).axe;
    const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    return result.violations.filter(({ impact }) => impact === "serious" || impact === "critical");
  });
  expect(violations).toEqual([]);
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: test.info().outputPath("drafting-tools-mobile.png"), fullPage: true });
});
