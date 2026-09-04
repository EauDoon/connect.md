import { createRequire } from "node:module";
import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { profileStarter, resumeStarter } from "../lib/markdown";

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

test("focused guided text is protected before blur", async ({ page }) => {
  await page.goto("/human");
  await page.getByRole("button", { name: "Next: shape" }).click();
  const pendingNarrative = "Focused work that has not left the field yet.";
  await page.locator("#narrative").fill(pendingNarrative);

  const warningPromise = page.waitForEvent("dialog");
  const reloadAttempt = page.reload({ timeout: 1_500 }).then(
    () => "navigated",
    (error: unknown) => error instanceof Error ? error.name : "failed",
  );
  const warning = await warningPromise;
  expect(warning.type()).toBe("beforeunload");
  await warning.dismiss();
  expect(await reloadAttempt).toBe("TimeoutError");
  await expect(page.locator("#narrative")).toHaveValue(pendingNarrative);
  await expect(page).toHaveURL(/\/human$/u);
});

test("guided edits survive navigation and stop warning after an exact download", async ({ page }) => {
  await page.goto("/human");
  await page.getByRole("button", { name: "Next: shape" }).click();
  await page.getByText("More professional signals", { exact: false }).click();
  await page.locator("#language-proficiency").selectOption("professional");
  await page.locator("#organization-relationship").selectOption("past_employer");
  await page.getByRole("button", { name: "Review document" }).click();
  await page.getByRole("button", { name: "Back to Shape" }).click();
  await page.getByText("More professional signals", { exact: false }).click();
  await expect(page.locator("#language-proficiency")).toHaveValue("professional");
  await expect(page.locator("#organization-relationship")).toHaveValue("past_employer");
  await page.getByRole("navigation", { name: /Editing mode/iu }).getByRole("link", { name: "Markdown" }).click();
  await page.getByRole("link", { name: "Continue in Guided" }).click();
  await page.getByText("More professional signals", { exact: false }).click();
  await expect(page.locator("#language-proficiency")).toHaveValue("professional");
  await expect(page.locator("#organization-relationship")).toHaveValue("past_employer");

  const replacementChooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Open local .md" }).click();
  const replacementChooser = await replacementChooserPromise;
  await replacementChooser.setFiles({
    name: "replacement-profile.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(profileStarter.replaceAll("Your Name", "Ari Example")),
  });
  await page.getByText("More professional signals", { exact: false }).click();
  await expect(page.locator("#language-proficiency")).toHaveValue("");
  await expect(page.locator("#organization-relationship")).toHaveValue("current_employer");

  await page.locator("#language-proficiency").selectOption("professional");
  await page.locator("#languages").fill("English");
  await page.locator("#languages").press("Tab");
  await expect(page.locator("#language-proficiency")).toHaveValue("professional");
  await page.locator("#organization-relationship").selectOption("past_employer");
  await page.locator("#organizations").fill("Example Company");
  await page.locator("#organizations").press("Tab");
  await expect(page.locator("#organization-relationship")).toHaveValue("past_employer");
  await page.getByRole("navigation", { name: /Editing mode/iu }).getByRole("link", { name: "Markdown" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Edit the source. Keep the same document." })).toBeVisible();
  await expect(page.locator('aside[aria-label="Markdown status and preview"]')).toContainText("Ari Example");

  expect(await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  })).toBe(true);

  const metadataDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download profile .md" }).click();
  const metadataDownload = await metadataDownloadPromise;
  const metadataStream = await metadataDownload.createReadStream();
  const metadataChunks: Buffer[] = [];
  for await (const chunk of metadataStream) metadataChunks.push(Buffer.from(chunk));
  const metadataMarkdown = Buffer.concat(metadataChunks).toString("utf8");
  expect(metadataMarkdown).toContain("proficiency: professional");
  expect(metadataMarkdown).toContain("relationship: past_employer");
  await expect(page.locator("#download-status")).toContainText("The current draft matches that local file; nothing was uploaded.");

  let warnedAfterDownload = false;
  page.once("dialog", async (unexpectedDialog) => {
    warnedAfterDownload = true;
    await unexpectedDialog.accept();
  });
  await page.reload();
  expect(warnedAfterDownload).toBe(false);
});

test("a valid draft tracks whether its local download is current", async ({ page }) => {
  await page.goto("/md");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download profile .md" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("your-handle.md");
  await expect(page.locator("#download-status")).toContainText("The current draft matches that local file; nothing was uploaded.");

  await page.getByRole("link", { name: "Continue in Guided" }).click();
  await page.getByRole("button", { name: "04 Download Validate and keep the file" }).click();
  await expect(page.locator("#download-status")).toContainText("The current draft matches that local file; nothing was uploaded.");
  await page.getByRole("navigation", { name: /Editing mode/iu }).getByRole("link", { name: "Markdown" }).click();

  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Open local .md" }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles({
    name: "updated-profile.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(profileStarter.replaceAll("Your Name", "Ari Example")),
  });

  await expect(page.locator("#download-status")).toContainText("The current draft no longer matches the last downloaded file.");
  const updatedDownloadButton = page.getByRole("button", { name: "Download updated profile .md" });
  await expect(updatedDownloadButton).toBeVisible();

  await page.setViewportSize({ width: 320, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await seriousAccessibilityViolations(page)).toEqual([]);

  const updatedDownloadPromise = page.waitForEvent("download");
  await updatedDownloadButton.focus();
  await page.keyboard.press("Enter");
  const updatedDownload = await updatedDownloadPromise;
  expect(updatedDownload.suggestedFilename()).toBe("your-handle.md");
  await expect(page.locator("#download-status")).toContainText("The current draft matches that local file; nothing was uploaded.");

  const invalidChooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Open local .md" }).click();
  const invalidChooser = await invalidChooserPromise;
  page.once("dialog", (dialog) => dialog.accept());
  await invalidChooser.setFiles({
    name: "invalid-profile.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(profileStarter.replace("## About", "## Background")),
  });
  const blockedDownload = page.getByRole("button", { name: /Download(?: updated)? profile \.md/u });
  await expect(blockedDownload).toBeDisabled();
  await expect(blockedDownload).toHaveAttribute("aria-describedby", "download-blocked download-status");
  await expect(page.locator("#download-blocked")).toContainText("Resolve the validation errors above before downloading.");
  const validationRegion = page.getByRole("region", { name: "Validation" });
  await expect(validationRegion.getByRole("status")).toContainText("blocking issue");
  await expect(validationRegion.locator("ul[aria-live]")).toHaveCount(0);
});

test("an existing local Markdown file reopens without an upload", async ({ page }) => {
  await page.goto("/md");
  const nonGetRequests: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET") nonGetRequests.push(`${request.method()} ${request.url()}`);
  });

  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Open local .md" }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles({
    name: "saved-resume.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(resumeStarter.replaceAll("Your Name", "Ari Example")),
  });

  await expect(page.locator("#local-markdown-file-status")).toContainText("Opened saved-resume.md locally as a resume. Nothing was uploaded.");
  await expect(page.getByRole("button", { name: "Download resume .md" })).toBeVisible();
  await expect(page.locator('aside[aria-label="Markdown status and preview"]')).toContainText("Ari Example");
  expect(nonGetRequests).toEqual([]);
});

test("sanitized HTML remains downloadable without reporting a clean preflight", async ({ page }) => {
  await page.goto("/md");
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Open local .md" }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles({
    name: "profile-with-html.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(profileStarter.replace("## Skills", "<script>alert('unsafe')</script>\n\n## Skills")),
  });

  await expect(page.getByText("Ready with 1 warning", { exact: true })).toBeVisible();
  await expect(page.getByText("Unsafe HTML is removed in the preview and public renderer.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Download profile .md" })).toBeEnabled();

  await page.getByRole("link", { name: "Continue in Guided" }).click();
  await page.getByRole("button", { name: "04 Download Validate and keep the file" }).click();
  await expect(page.getByText("Ready with a warning", { exact: true })).toBeVisible();
  await expect(page.getByText("You can download now. Review the warning below first.")).toBeVisible();
  expect(await seriousAccessibilityViolations(page)).toEqual([]);
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

test("retired backend routes fail closed", async ({ page, request }) => {
  for (const path of ["/discover", "/workspace", "/jobs/example", "/posts/example"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(404);
    expect(response.headers()["cache-control"], path).toBe("private, no-store, max-age=0");
    expect(response.headers()["x-robots-tag"], path).toBe("noindex, nofollow");
  }

  const missingResponse = await page.goto("/definitely-not-a-connectmd-route");
  expect(missingResponse?.status()).toBe(404);
  await expect(page.getByRole("link", { name: "Build a local draft" })).toHaveAttribute("href", "/human");
  await expect(page.locator('a[href="/discover"]')).toHaveCount(0);
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
