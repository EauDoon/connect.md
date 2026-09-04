import { createRequire } from "node:module";
import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

type AxeWindow = Window & {
  axe?: {
    run: (
      context: Document,
      options: { runOnly: { type: "tag"; values: string[] } },
    ) => Promise<{ violations: Array<{ id: string; impact: string | null }> }>;
  };
};

const require = createRequire(resolve(process.cwd(), "package.json"));
const axeScriptPath = require.resolve("axe-core/axe.min.js");
const expectedSiteOrigin = new URL(process.env.NEXT_PUBLIC_SITE_URL ?? process.env.E2E_BASE_URL ?? "").origin;

async function seriousAccessibilityViolations(page: Page) {
  await page.addScriptTag({ path: axeScriptPath });
  return page.evaluate(async () => {
    const axe = (window as AxeWindow).axe;
    if (!axe) throw new Error("axe failed to load");
    const result = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    });
    return result.violations.filter(({ impact }) => impact === "serious" || impact === "critical");
  });
}

test("landing is an agent-first standalone site", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/connect\.md/u);
  await expect(page.getByRole("heading", { level: 1, name: "Choose what you want done." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Agent README" })).toHaveAttribute("href", `${expectedSiteOrigin}/agent-readme.md`);
  await expect(page.getByRole("link", { name: /sign in/iu })).toHaveCount(0);
});

test("agent presets expose bounded drafting instructions", async ({ page }) => {
  await page.goto("/");
  await page.locator('[aria-controls="agent-handoff-instruction"]').nth(1).click();
  const instruction = page.locator("#agent-handoff-instruction");
  await expect(instruction).toContainText("prepare my resume");
  await expect(instruction).toContainText("Do not publish, upload, contact anyone");
});

test("guided edits survive mode navigation and warn before a full reload", async ({ page }) => {
  await page.goto("/human");
  await page.getByRole("button", { name: "Next: shape" }).click();
  await page.locator("#name").fill("Ari Example");
  await page.locator("#name").press("Tab");
  await page.getByRole("navigation", { name: /Editing mode/iu }).getByRole("link", { name: "Markdown" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Edit the source. Keep the same document." })).toBeVisible();
  await expect(page.locator('aside[aria-label="Markdown status and preview"]')).toContainText("Ari Example");

  const warning = page.waitForEvent("dialog");
  const reload = page.reload();
  const dialog = await warning;
  expect(dialog.type()).toBe("beforeunload");
  await dialog.accept();
  await reload;
});

test("a valid draft downloads as a local Markdown file", async ({ page }) => {
  await page.goto("/md");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download profile .md" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("your-handle.md");
});

test("trust page states the exact browser-only boundary", async ({ page }) => {
  await page.goto("/trust");
  await expect(page.getByRole("heading", { level: 1, name: "Your draft stays in your browser." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No hidden persistence" })).toBeVisible();
  await expect(page.getByText("It has no account, publishing API, database, file-upload service, messaging system, or analytics code.")).toBeVisible();
});

test("static agent documents describe the standalone workflow", async ({ request }) => {
  for (const path of ["/agent-readme.md", "/llms.txt"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(200);
    const body = await response.text();
    expect(body, path).toContain("browser");
    expect(body, path).toContain("no publishing API");
    expect(body, path).toContain("download");
  }
});

test("crawler metadata lists only working public pages", async ({ request }) => {
  const robots = await (await request.get("/robots.txt")).text();
  expect(robots).toContain("Allow: /human");
  expect(robots).toContain("Disallow: /discover");
  expect(robots).toContain(`Sitemap: ${expectedSiteOrigin}/sitemap.xml`);

  const sitemap = await (await request.get("/sitemap.xml")).text();
  for (const path of ["/", "/human", "/md", "/trust"]) expect(sitemap).toContain(`<loc>${expectedSiteOrigin}${path}</loc>`);
  expect(sitemap).not.toContain("/discover");
});

test("retired backend routes fail closed", async ({ request }) => {
  for (const path of ["/discover", "/workspace", "/jobs/example", "/posts/example"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(404);
    expect(response.headers()["cache-control"], path).toBe("private, no-store, max-age=0");
    expect(response.headers()["x-robots-tag"], path).toBe("noindex, nofollow");
  }
});

test("public pages reflow and pass serious accessibility checks", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const path of ["/", "/human", "/md", "/trust"]) {
    await page.goto(path);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), path).toBe(true);
    expect(await seriousAccessibilityViolations(page), path).toEqual([]);
  }
});
