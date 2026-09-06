import { afterEach, describe, expect, it, vi } from "vitest";

import nextConfig from "../next.config";

afterEach(() => vi.unstubAllEnvs());

async function configuredHeaders() {
  const headers = await nextConfig.headers?.();
  return headers?.[0]?.headers ?? [];
}

function headerValue(headers: Array<{ key: string; value: string }>, key: string) {
  return headers.find((header) => header.key === key)?.value;
}

describe("standalone Vercel security headers", () => {
  it("leaves non-Vercel header ownership unchanged", async () => {
    vi.stubEnv("VERCEL", "");
    await expect(configuredHeaders()).resolves.toEqual([]);
  });

  it("rejects Vercel development because the production CSP has no unsafe-eval", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "development");
    vi.stubEnv("NODE_ENV", "development");
    await expect(configuredHeaders()).rejects.toThrow("require a production Next.js build");
  });

  it("emits a self-contained production boundary", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://connect-md.vercel.app");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://ignored.example.com");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "ignored");

    const headers = await configuredHeaders();
    const policy = headerValue(headers, "Content-Security-Policy") ?? "";
    expect(headerValue(headers, "X-Content-Type-Options")).toBe("nosniff");
    expect(headerValue(headers, "X-Frame-Options")).toBe("SAMEORIGIN");
    expect(headerValue(headers, "Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(headerValue(headers, "Permissions-Policy")).toBe("camera=(), microphone=(), geolocation=(), payment=()");
    expect(headerValue(headers, "Strict-Transport-Security")).toBe("max-age=31536000; includeSubDomains");
    expect(policy).toContain("connect-src 'self'");
    expect(policy).toContain("worker-src 'self' blob:");
    expect(policy).toContain("script-src-attr 'none'");
    expect(policy).not.toMatch(/ignored|clerk|unsafe-eval/u);
  });

  it.each([
    "",
    "http://connect-md.vercel.app",
    "https://connect-md.vercel.app/path",
    "https://user:pass@connect-md.vercel.app",
  ])("rejects a missing or non-canonical production site origin (%j)", async (siteUrl) => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", siteUrl);
    await expect(configuredHeaders()).rejects.toThrow("NEXT_PUBLIC_SITE_URL must be");
  });

  it("allows preview security headers without claiming the production origin", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "preview");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "");
    const headers = await configuredHeaders();
    expect(headerValue(headers, "Content-Security-Policy")).toContain("default-src 'self'");
    expect(headerValue(headers, "Strict-Transport-Security")).toBeUndefined();
  });
});
