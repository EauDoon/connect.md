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
  await page.screenshot({ path: test.info().outputPath("print-preview.png"), fullPage: true });
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
  await page.getByRole("button", { name: "Compare Before editing", exact: true }).click();
  await expect(page.getByLabel("Checkpoint comparison")).toContainText("Avery Example");
  await page.getByRole("link", { name: "Continue in Guided" }).click();
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
  expect(await page.evaluate(() => { const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; })).toBe(false);
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
  await page.screenshot({ path: test.info().outputPath("drafting-tools-mobile.png"), fullPage: true });
});
