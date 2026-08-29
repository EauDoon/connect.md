import { afterEach, describe, expect, it, vi } from "vitest";

import nextConfig from "../next.config";

afterEach(() => {
  vi.unstubAllEnvs();
});

async function configuredHeaders() {
  const headers = await nextConfig.headers?.();
  return headers?.[0]?.headers ?? [];
}

function headerValue(headers: Array<{ key: string; value: string }>, key: string) {
  return headers.find((header) => header.key === key)?.value;
}

const clerkExamplePublishableKey = "pk_live_Y2xlcmsuZXhhbXBsZS5jb20k";

describe("Vercel deployment headers", () => {
  it("leaves Docker header ownership with Nginx", async () => {
    vi.stubEnv("VERCEL", "");
    vi.stubEnv("VERCEL_ENV", "");
    vi.stubEnv("NODE_ENV", "production");

    await expect(configuredHeaders()).resolves.toEqual([]);
  });

  it("rejects the production Vercel policy for a Next.js development server", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "development");
    vi.stubEnv("NODE_ENV", "development");
    await expect(configuredHeaders()).rejects.toThrow("require a production Next.js build");
  });

  it("emits the Nginx-equivalent boundary with the configured API origin on Vercel production", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://connect.md");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.md/");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", clerkExamplePublishableKey);

    const headers = await configuredHeaders();
    const contentSecurityPolicy = headerValue(headers, "Content-Security-Policy");

    expect(headerValue(headers, "X-Content-Type-Options")).toBe("nosniff");
    expect(headerValue(headers, "X-Frame-Options")).toBe("SAMEORIGIN");
    expect(headerValue(headers, "Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(headerValue(headers, "Permissions-Policy")).toBe("camera=(), microphone=(), geolocation=(), payment=()");
    expect(headerValue(headers, "Strict-Transport-Security")).toBe("max-age=31536000; includeSubDomains");
    expect(contentSecurityPolicy).toContain("connect-src 'self' https://api.connect.md");
    expect(contentSecurityPolicy).toContain("https://clerk.example.com");
    expect(contentSecurityPolicy).not.toContain("https://clerk.connect.md");
    expect(contentSecurityPolicy).toContain("script-src-attr 'none'");
    const directives = new Map(contentSecurityPolicy?.split("; ").map((directive) => {
      const [name, ...sources] = directive.split(" ");
      return [name, sources];
    }));
    for (const name of ["script-src", "script-src-elem"]) {
      expect(directives.get(name)).toEqual(expect.arrayContaining([
        "https://clerk.example.com",
        "https://*.protect.clerk.com",
        "https://challenges.cloudflare.com",
      ]));
    }
    expect(directives.get("connect-src")).toContain("https://*.protect.clerk.com:*");
    expect(contentSecurityPolicy).toContain("frame-src 'self' blob: https://challenges.cloudflare.com https://*.protect.clerk.com");
    expect(contentSecurityPolicy).not.toContain("unsafe-eval");
  });

  it.each(["NEXT_PUBLIC_SITE_URL", "NEXT_PUBLIC_API_BASE_URL"])(
    "fails closed when %s is missing from a Vercel production build",
    async (missingVariable) => {
      vi.stubEnv("VERCEL", "1");
      vi.stubEnv("VERCEL_ENV", "production");
      vi.stubEnv("NODE_ENV", "production");
      vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://connect.md");
      vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.md");
      vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", clerkExamplePublishableKey);
      vi.stubEnv(missingVariable, "");

      await expect(configuredHeaders()).rejects.toThrow(`${missingVariable} must be an explicit HTTPS origin`);
    },
  );

  it("rejects path, credential, and non-HTTPS API values in production", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://connect.md");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", clerkExamplePublishableKey);

    for (const value of ["https://api.connect.md/v1", "https://user:pass@api.connect.md", "http://api.connect.md"]) {
      vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", value);
      await expect(configuredHeaders()).rejects.toThrow("NEXT_PUBLIC_API_BASE_URL must be a canonical HTTPS origin");
    }
  });

  it("keeps a public-only Vercel deployment free of unused Clerk origins", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://app.example.com");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");

    const contentSecurityPolicy = headerValue(await configuredHeaders(), "Content-Security-Policy");
    expect(contentSecurityPolicy).not.toContain("clerk");
    expect(contentSecurityPolicy).not.toContain("challenges.cloudflare.com");
  });

  it("fails closed when a configured Vercel Clerk publishable key is malformed", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://app.example.com");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com");

    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_bm90LWhvc3Qk");
    await expect(configuredHeaders()).rejects.toThrow(
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a well-formed Clerk publishable key for Vercel",
    );
  });

  it("supports an explicit Clerk satellite domain without guessing from the site origin", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://app.example.com");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", clerkExamplePublishableKey);
    vi.stubEnv("NEXT_PUBLIC_CLERK_DOMAIN", "tenant.example.com");
    vi.stubEnv("NEXT_PUBLIC_CLERK_IS_SATELLITE", "true");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PROXY_URL", "");

    const headers = await configuredHeaders();
    const contentSecurityPolicy = headerValue(headers, "Content-Security-Policy");

    expect(contentSecurityPolicy).toContain("https://clerk.example.com");
    expect(contentSecurityPolicy).toContain("https://clerk.tenant.example.com");
    expect(contentSecurityPolicy).not.toContain("https://proxy.example.com");
    expect(contentSecurityPolicy).not.toContain("https://clerk.app.example.com");
  });

  it("allows a same-origin relative Clerk proxy for satellite mode", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://app.example.com");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", clerkExamplePublishableKey);
    vi.stubEnv("NEXT_PUBLIC_CLERK_DOMAIN", "");
    vi.stubEnv("NEXT_PUBLIC_CLERK_IS_SATELLITE", "true");
    for (const proxyUrl of ["/__clerk", "https://app.example.com/__clerk"]) {
      vi.stubEnv("NEXT_PUBLIC_CLERK_PROXY_URL", proxyUrl);
      const headers = await configuredHeaders();
      const policy = headerValue(headers, "Content-Security-Policy");
      expect(policy).toContain("script-src 'self'");
      expect(policy).not.toContain("/__clerk");
    }
  });

  it("rejects malformed or unsupported Clerk domain and proxy settings", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("VERCEL_ENV", "production");
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://app.example.com");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", clerkExamplePublishableKey);

    vi.stubEnv("NEXT_PUBLIC_CLERK_DOMAIN", "https://tenant.example.com");
    await expect(configuredHeaders()).rejects.toThrow(
      "NEXT_PUBLIC_CLERK_DOMAIN must be a lowercase DNS hostname for Vercel",
    );

    vi.stubEnv("NEXT_PUBLIC_CLERK_DOMAIN", "");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PROXY_URL", "https://user:pass@proxy.example.com/__clerk");
    await expect(configuredHeaders()).rejects.toThrow(
      "NEXT_PUBLIC_CLERK_PROXY_URL must be a relative path or canonical HTTPS origin for Vercel",
    );

    vi.stubEnv("NEXT_PUBLIC_CLERK_PROXY_URL", "");
    vi.stubEnv("NEXT_PUBLIC_CLERK_DOMAIN", "tenant.example.com");
    await expect(configuredHeaders()).rejects.toThrow(
      "NEXT_PUBLIC_CLERK_DOMAIN requires NEXT_PUBLIC_CLERK_IS_SATELLITE=true for Vercel",
    );

    vi.stubEnv("NEXT_PUBLIC_CLERK_IS_SATELLITE", "true");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PROXY_URL", "/__clerk");
    await expect(configuredHeaders()).rejects.toThrow("are mutually exclusive for Vercel");

    vi.stubEnv("NEXT_PUBLIC_CLERK_DOMAIN", "");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PROXY_URL", "https://proxy.example.com/__clerk");
    await expect(configuredHeaders()).rejects.toThrow("must use the same origin as NEXT_PUBLIC_SITE_URL");

    vi.stubEnv("NEXT_PUBLIC_CLERK_PROXY_URL", "/__clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", `pk_test_${Buffer.from("app.clerk.accounts.dev$").toString("base64")}`);
    await expect(configuredHeaders()).rejects.toThrow("requires a production Clerk publishable key");
  });
});
