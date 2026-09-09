import { expect, test } from "@playwright/test";
import { profileStarter, resumeStarter } from "../lib/markdown";
import { createHash } from "node:crypto";

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
  await expect(page.getByRole("status")).toContainText("Checkpoint kept");
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
  await expect(page.getByRole("status")).toContainText("Restored Before editing");
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
  await expect(page.getByRole("alert")).toContainText("schema");
  await page.getByLabel("Markdown to import").fill(resumeStarter);
  await page.getByRole("button", { name: "Open pasted Markdown", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Opened pasted resume locally");
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
  await expect(page.getByRole("alert")).toContainText("denied clipboard access");
  await expect(page.getByRole("button", { name: "Download profile .md", exact: true })).toBeEnabled();
});
