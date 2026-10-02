import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";
import { starterFor } from "../lib/markdown";

test("first save, conflict preservation, publication, and contact termination work through real routes", async ({ browser, page }) => {
  const suffix = randomUUID().slice(0, 8);
  const firstHandle = `journey-a-${suffix}`;
  const secondHandle = `journey-b-${suffix}`;
  const secondContext = await browser.newContext();
  const secondPage = await secondContext.newPage();
  try {
    for (const [actor, handle] of [[page, firstHandle], [secondPage, secondHandle]] as const) {
      await actor.goto("http://127.0.0.1:3210/account");
      await actor.getByLabel("Handle", { exact: true }).fill(handle);
      await actor.getByLabel("Email", { exact: true }).fill(`${handle}@example.com`);
      await actor.getByLabel("Password", { exact: true }).fill("JourneyPassword123");
      await actor.getByTestId("account-submit").click();
      await expect(actor.getByTestId("profile-save")).toBeEnabled();
    }
    const source = starterFor("profile");
    await page.getByLabel("Profile Markdown").fill(source);
    await page.getByTestId("profile-save").click();
    await expect(page.getByTestId("network-status")).toContainText("Profile saved");
    await expect(page.getByTestId("profile-publish")).toBeEnabled();

    const staleTab = await page.context().newPage();
    await staleTab.goto("/network");
    await expect(staleTab.getByLabel("Profile Markdown")).toHaveValue(source);
    const stalePublication = await page.context().newPage();
    await stalePublication.goto("/network");
    await expect(stalePublication.getByTestId("profile-publish")).toBeEnabled();
    await page.getByLabel("Profile Markdown").fill(source + "\nSaved revision.\n");
    await page.getByTestId("profile-save").click();
    await expect(page.getByTestId("network-status")).toContainText("Profile saved");
    const unsaved = source + "\nKeep these unsaved edits.\n";
    await staleTab.getByLabel("Profile Markdown").fill(unsaved);
    await staleTab.getByTestId("profile-save").click();
    await expect(staleTab.getByRole("button", { name: "Discard edits and reload saved profile" })).toBeVisible();
    await expect(staleTab.getByLabel("Profile Markdown")).toHaveValue(unsaved);
    await staleTab.close();
    await stalePublication.getByTestId("profile-publish").click();
    await expect(stalePublication.getByRole("button", { name: "Discard edits and reload saved profile" })).toBeVisible();
    expect((await page.request.get(`/api/network/v1/public/profiles/${firstHandle}`)).status()).toBe(404);
    await stalePublication.close();

    await page.getByTestId("profile-publish").click();
    await expect(page.getByTestId("profile-visibility")).toHaveText("published");
    await expect(page.getByLabel("Profile Markdown")).toBeDisabled();
    const publicResponse = await page.request.get(`/api/network/v1/public/profiles/${firstHandle}`);
    expect(publicResponse.ok()).toBe(true);
    await page.getByTestId("profile-unpublish").click();
    await expect(page.getByLabel("Profile Markdown")).toBeEnabled();
    expect((await page.request.get(`/api/network/v1/public/profiles/${firstHandle}`)).status()).toBe(404);

    await page.goto("/inbox");
    await page.getByLabel("Handle to contact").fill(secondHandle);
    await page.getByTestId("contact-send").click();
    await expect(page.getByTestId("inbox-notice")).toContainText("Contact request sent");
    await secondPage.goto("http://127.0.0.1:3210/inbox");
    await secondPage.getByTestId(`accept-${firstHandle}`).click();
    await expect(secondPage.getByTestId("conversations").getByRole("link")).toHaveCount(1);
    await page.reload();
    await page.getByRole("button", { name: "Close contact", exact: true }).click();
    await expect(page.getByTestId("contact-requests").getByRole("listitem").filter({
      hasText: `@${firstHandle} → @${secondHandle}: revoked`,
    })).toBeVisible();
    await secondPage.getByTestId("conversations").getByRole("link").click();
    await secondPage.getByLabel("Message", { exact: true }).fill("This send must fail after consent closes.");
    await secondPage.getByTestId("message-send").click();
    await expect(secondPage.getByTestId("conversation-notice")).toContainText("closed");
  } finally {
    await secondContext.close();
  }
});
