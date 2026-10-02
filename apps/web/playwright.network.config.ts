import { defineConfig } from "@playwright/test";

const databaseUrl = process.env.CONNECTMD_NETWORK_DATABASE_URL;
if (!databaseUrl) throw new Error("Network browser checks require a disposable PostgreSQL database.");
const target = new URL(databaseUrl);
if (!["127.0.0.1", "localhost", "[::1]"].includes(target.hostname) || !target.pathname.endsWith("_test")) {
  throw new Error("Network browser checks require a loopback database ending in _test.");
}
const origin = "http://127.0.0.1:3210";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "network-journey.spec.ts",
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 90_000,
  use: { baseURL: origin, trace: "off", screenshot: "off", video: "off" },
  webServer: {
    command: "node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3210",
    url: origin,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      CONNECTMD_NETWORK_DATABASE_URL: databaseUrl,
      CONNECTMD_NETWORK_ORIGIN: origin,
      CONNECTMD_NETWORK_INSECURE_COOKIE: "1",
      NEXT_PUBLIC_SITE_URL: origin,
      NEXT_TELEMETRY_DISABLED: "1",
    },
  },
});
