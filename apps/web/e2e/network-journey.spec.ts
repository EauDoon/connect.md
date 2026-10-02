import { randomUUID } from "node:crypto";
import { expect, test, type Page, type Route } from "@playwright/test";
import { starterFor } from "../lib/markdown";

async function whileRequestIsPending(page: Page, url: string, click: () => Promise<void>, check: () => Promise<void>) {
  let release!: () => void;
  let started!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const arrived = new Promise<void>((resolve) => { started = resolve; });
  const handler = async (route: Route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    started();
    await pending;
    await route.continue();
  };
  await page.route(url, handler);
  try {
    await click();
    await arrived;
    await check();
  } finally {
    release();
    await page.unroute(url, handler);
  }
}

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

    await page.getByLabel("Grant name", { exact: true }).fill("journey-agent");
    await whileRequestIsPending(page, "**/api/network/v1/agent-grants",
      () => page.getByTestId("grant-create").click(),
      () => expect(page.getByLabel("Grant name", { exact: true })).toBeDisabled());
    await expect(page.getByTestId("grant-token-shown")).toBeVisible();
    await expect(page.getByLabel("Grant name", { exact: true })).toHaveValue("");
    const revoke = page.getByRole("button", { name: "Revoke", exact: true });
    await page.route("**/api/network/v1/agent-grants/*", (route) => route.fulfill({ status: 503, json: { ok: false } }), { times: 1 });
    await revoke.click();
    await expect(page.getByTestId("network-status")).toContainText("Could not confirm revocation");
    await expect(revoke).toBeEnabled();
    await page.route("**/api/network/v1/agent-grants/*", (route) => route.abort(), { times: 1 });
    await revoke.click();
    await expect(page.getByTestId("network-status")).toContainText("Could not confirm revocation");
    await expect(revoke).toBeEnabled();
    await revoke.click();
    await expect(page.getByTestId("network-status")).toContainText("Agent grant revoked");
    await expect(revoke).toHaveCount(0);

    await page.goto("/inbox");
    await page.getByLabel("Handle to contact").fill(secondHandle);
    await whileRequestIsPending(page, "**/api/network/v1/contacts",
      () => page.getByTestId("contact-send").click(),
      () => expect(page.getByLabel("Handle to contact")).toBeDisabled());
    await expect(page.getByTestId("inbox-notice")).toContainText("Contact request sent");
    await secondPage.goto("http://127.0.0.1:3210/inbox");
    await secondPage.getByTestId(`accept-${firstHandle}`).click();
    await expect(secondPage.getByTestId("conversations").getByRole("link")).toHaveCount(1);
    await secondPage.getByTestId("conversations").getByRole("link").click();
    await secondPage.getByLabel("Message", { exact: true }).fill("A sent message.");
    await whileRequestIsPending(secondPage, "**/api/network/v1/conversations/*/messages",
      () => secondPage.getByTestId("message-send").click(),
      () => expect(secondPage.getByLabel("Message", { exact: true })).toBeDisabled());
    await expect(secondPage.getByTestId("message-list")).toContainText("A sent message.");
    await expect(secondPage.getByLabel("Message", { exact: true })).toHaveValue("");
    await page.reload();
    await page.getByRole("button", { name: "Close contact", exact: true }).click();
    await expect(page.getByTestId("contact-requests").getByRole("listitem").filter({
      hasText: `@${firstHandle} → @${secondHandle}: revoked`,
    })).toBeVisible();
    await secondPage.getByLabel("Message", { exact: true }).fill("This send must fail after consent closes.");
    await secondPage.getByTestId("message-send").click();
    await expect(secondPage.getByTestId("conversation-notice")).toContainText("closed");
    await expect(secondPage.getByLabel("Message", { exact: true })).toHaveValue("This send must fail after consent closes.");
    await expect(secondPage.getByLabel("Message", { exact: true })).toBeEnabled();

    await page.goto("/account");
    for (const failure of ["http", "network"] as const) {
      await page.route("**/api/network/v1/accounts/logout", (route) => failure === "http"
        ? route.fulfill({ status: 503, json: { ok: false } }) : route.abort(), { times: 1 });
      await page.getByTestId("account-signout").click();
      await expect(page.getByRole("alert")).toContainText("Could not confirm sign out");
      await expect(page).toHaveURL(/\/account$/);
      await expect(page.getByTestId("account-signout")).toBeEnabled();
    }
    await page.getByTestId("account-signout").click();
    await expect(page).toHaveURL("http://127.0.0.1:3210/");
    await page.goto("/account");
    await expect(page.getByTestId("account-auth-panel")).toBeVisible();
  } finally {
    await secondContext.close();
  }
});
