import { expect, test, type Download, type Page } from "@playwright/test";
import { profileStarter, resumeStarter } from "../lib/markdown";

async function downloadedBytes(download: Download) {
  const chunks: Buffer[] = [];
  for await (const chunk of (await download.createReadStream())!) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks);
}

async function openMarkdown(page: Page, buffer: Buffer, name = "synthetic.md") {
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Open local .md", exact: true }).click();
  await (await chooser).setFiles({ name, mimeType: "text/markdown", buffer });
}

for (const [kind, starter] of [["profile", profileStarter], ["resume", resumeStarter]] as const) {
  test(`${kind} source supports ordinary typing, review, download and exact reopening`, async ({ page }) => {
    const writes: string[] = [];
    page.on("request", request => { if (request.method() !== "GET") writes.push(request.url()); });
    await page.goto("/md");
    await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
    const source = page.getByRole("textbox", { name: "Canonical Markdown source" });
    await openMarkdown(page, Buffer.from(starter.replaceAll("Your Name", "Casey Example")));
    const imported = starter.replaceAll("Your Name", "Casey Example");
    await expect(source).toHaveValue(imported);
    await source.press("ControlOrMeta+End");
    await source.pressSequentially("Built useful tools", { delay: 10 });
    await expect(source).toHaveValue(imported + "Built useful tools");
    await source.press("Enter");
    await source.press("Enter");
    await source.pressSequentially("- ", { delay: 10 });
    await expect(source).toHaveValue(imported + "Built useful tools\n\n- ");
    await source.pressSequentially("Improved delivery  ", { delay: 10 });
    await source.press("Enter");
    await source.press("Enter");
    let expected = imported + "Built useful tools\n\n- Improved delivery  \n\n";
    await expect(source).toHaveValue(expected);
    await expect(page.getByLabel("Sanitized Markdown preview")).toContainText("Built useful tools");

    await page.getByRole("radio", { name: "Code editor", exact: true }).check();
    await expect(page.locator(".monaco-editor").first()).toBeVisible();
    const code = page.getByRole("textbox", { name: "Editor content" });
    await code.focus();
    await expect(code).toBeFocused();
    await page.keyboard.press(process.platform === "darwin" ? "Meta+ArrowDown" : "Control+End");
    await page.keyboard.type("Code edits also keep spaces  ", { delay: 60 });
    expected += "Code edits also keep spaces  ";
    await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
    await page.getByRole("link", { name: "Continue in Guided", exact: true }).click();
    await page.getByRole("button", { name: /^03 Review/ }).click();
    await page.getByRole("navigation", { name: /Editing mode/ }).getByRole("link", { name: "Markdown", exact: true }).click();
    await expect(source).toHaveValue(expected);

    const downloadEvent = page.waitForEvent("download");
    await page.getByRole("button", { name: `Download ${kind} .md`, exact: true }).click();
    const buffer = await downloadedBytes(await downloadEvent);
    expect(buffer.equals(Buffer.from(expected))).toBe(true);
    page.once("dialog", dialog => dialog.accept());
    await openMarkdown(page, buffer, "reopened.md");
    await expect(page.getByRole("status").filter({ hasText: "Opened reopened.md" })).toBeVisible();
    await expect(source).toHaveValue(expected);

    await openMarkdown(page, Buffer.from([0xc3, 0x28]), "invalid.md");
    await expect(page.getByRole("alert").filter({ hasText: "valid UTF-8" })).toBeVisible();
    await expect(source).toHaveValue(expected);
    await source.fill("# Incomplete  \n\n");
    await expect(page.getByRole("button", { name: new RegExp(`Download(?: updated)? ${kind} \\.md`) })).toBeDisabled();
    expect(writes).toEqual([]);
    expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });
  });
}

test("unfinished whitespace survives checkpoint restoration, undo and recovery after reload", async ({ page }) => {
  await page.goto("/md");
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  const source = page.getByRole("textbox", { name: "Canonical Markdown source" });
  const unfinished = "# Synthetic unfinished draft  \n\n\t";
  await source.fill(unfinished);
  await page.getByText("Session checkpoints (0/5)", { exact: true }).click();
  await page.getByLabel("Checkpoint name", { exact: true }).fill("Keep whitespace");
  await page.getByRole("button", { name: "Keep checkpoint", exact: true }).click();
  await source.fill("# Changed");
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Restore Keep whitespace", exact: true }).click();
  await expect(source).toHaveValue(unfinished);
  await page.getByText("Session recovery", { exact: true }).click();
  const backupEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download unfinished source", exact: true }).click();
  expect((await downloadedBytes(await backupEvent)).equals(Buffer.from(unfinished))).toBe(true);
  const recoveryEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download session recovery", exact: true }).click();
  const recovery = await downloadedBytes(await recoveryEvent);
  page.once("dialog", dialog => dialog.accept());
  await page.reload();
  await page.getByText("Session recovery", { exact: true }).click();
  await page.getByLabel("Open a recovery file", { exact: true }).setInputFiles({ name: "synthetic.recovery.json", mimeType: "application/json", buffer: recovery });
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Restore recovery session", exact: true }).click();
  await page.getByRole("radio", { name: "Plain-text editor", exact: true }).check();
  await expect(source).toHaveValue(unfinished);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Reset starter", exact: true }).click();
  await expect(source).toHaveValue(profileStarter);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Undo draft replacement", exact: true }).click();
  await expect(source).toHaveValue(unfinished);
  await expect(page.getByText("Session checkpoints (1/5)", { exact: true })).toBeVisible();
});
