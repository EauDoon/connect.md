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
