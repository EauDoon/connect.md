import { expect, test } from "@playwright/test";
import { profileStarter } from "../lib/markdown";

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
