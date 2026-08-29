import { defineConfig } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "standalone-release.spec.ts",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: "line",
  use: {
    baseURL,
    colorScheme: "dark",
    locale: "en-US",
    serviceWorkers: "block",
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
