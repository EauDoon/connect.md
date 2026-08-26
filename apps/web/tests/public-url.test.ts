import { afterEach, describe, expect, it, vi } from "vitest";

import { agentHandoffPresets } from "../components/agent-handoff";
import { ApiRequestError, PUBLIC_PROTOCOL_PATHS, presentPublicReadError, publicDiscoveryUrl, publicProtocolUrl } from "../lib/api";
import { absoluteSiteUrl, publicSiteOrigin } from "../lib/public-document";

const originalApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
const originalSiteUrl = process.env.NEXT_PUBLIC_SITE_URL;

afterEach(() => {
  if (originalApiBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = originalApiBase;
  if (originalSiteUrl === undefined) delete process.env.NEXT_PUBLIC_SITE_URL;
  else process.env.NEXT_PUBLIC_SITE_URL = originalSiteUrl;
  vi.unstubAllEnvs();
});

describe("public URL boundaries", () => {
  it("keeps public read failures actionable without exposing raw service details", () => {
    const rawDetails = [
      new Error("database host secret"),
      new ApiRequestError("NEXT_PUBLIC_API_BASE_URL is invalid", undefined, "configuration"),
      new ApiRequestError("upstream database unavailable", 503, "server"),
      new ApiRequestError("raw validation detail", 422, "request"),
    ];

    for (const error of rawDetails) {
      const message = presentPublicReadError(error);
      expect(message).toBe("Public records are temporarily unavailable. Try again shortly.");
      expect(message).not.toMatch(/API|database|validation|secret/iu);
    }
    expect(presentPublicReadError(new ApiRequestError("offline detail", undefined, "offline"))).toBe(
      "You are offline. Reconnect, then try again.",
    );
  });

  it("keeps same-origin production discovery relative and copy-ready", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
    const readmeUrl = publicDiscoveryUrl("/agent-readme.md", absoluteSiteUrl("/agent-readme.md"));

    expect(publicDiscoveryUrl("/agent-readme.md")).toBe("/agent-readme.md");
    expect(readmeUrl).toBe("https://connect.md/agent-readme.md");
    expect(agentHandoffPresets(readmeUrl)[0].prompt).toContain(readmeUrl);
  });

  it("points first-visit links and prompts at the configured split-origin API", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");
    const readmeUrl = publicDiscoveryUrl("/agent-readme.md", absoluteSiteUrl("/agent-readme.md"));

    expect(publicDiscoveryUrl("/agent-readme.md")).toBe("https://api.connect.test/agent-readme.md");
    expect(readmeUrl).toBe("https://api.connect.test/agent-readme.md");
    expect(agentHandoffPresets(readmeUrl).every((preset) => preset.prompt.includes(readmeUrl))).toBe(true);
  });

  it("does not render an unvalidated API origin or arbitrary fallback", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/v1");
    expect(publicDiscoveryUrl("/agent-readme.md")).toBe("/agent-readme.md");
    expect(publicProtocolUrl("/agent-readme.md", "https://evil.test/agent-readme.md")).toBe("/agent-readme.md");
    expect(publicDiscoveryUrl("/agent-readme.md", "https://evil.test/agent-readme.md")).toBe("/agent-readme.md");
  });

  it("keeps protocol URL resolution on the exact current route allowlist", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");

    for (const path of PUBLIC_PROTOCOL_PATHS) expect(publicProtocolUrl(path)).toBe(path);
    expect(publicProtocolUrl("/v1/api-keys")).toBeNull();
    expect(publicProtocolUrl("/llms.txt?redirect=https://evil.test")).toBeNull();
  });

  it("makes allowlisted protocol links absolute for a split API origin", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");

    for (const path of PUBLIC_PROTOCOL_PATHS) {
      expect(publicProtocolUrl(path)).toBe(`https://api.connect.test${path}`);
    }
    expect(publicProtocolUrl("/v1/api-keys")).toBeNull();
  });

  it("returns null for a nonallowlisted path regardless of fallback", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");

    expect(publicProtocolUrl("/v1/api-keys", "/v1/api-keys")).toBeNull();
    expect(publicProtocolUrl("/v1/api-keys", "https://connect.md/v1/api-keys")).toBeNull();
  });

  it.each([
    "javascript:alert(1)",
    "https://user:pass@api.connect.test/",
    "https://api.connect.test/v1",
    "https://api.connect.test/?query=1",
    "https://api.connect.test/#fragment",
    "//api.connect.test",
  ])("fails closed for malformed API configuration (%s)", (apiBase) => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", apiBase);

    expect(publicProtocolUrl("/llms.txt", "https://evil.test/llms.txt")).toBe("/llms.txt");
  });

  it("accepts only the exact same-site fallback for the exact protocol path", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/v1");

    for (const fallback of [
      "https://evil.test/agent-readme.md",
      "https://connect.md/other.md",
      "https://connect.md/agent-readme.md?redirect=https://evil.test",
      "https://connect.md/agent-readme.md#fragment",
      "//connect.md/agent-readme.md",
      " https://connect.md/agent-readme.md",
      "https://api.connect.test/agent-readme.md",
      "https://user:pass@connect.md/agent-readme.md",
      "http://connect.md/agent-readme.md",
    ]) {
      expect(publicProtocolUrl("/agent-readme.md", fallback)).toBe("/agent-readme.md");
    }

    expect(publicProtocolUrl("/agent-readme.md", "/agent-readme.md")).toBe("/agent-readme.md");
    expect(publicProtocolUrl("/agent-readme.md", absoluteSiteUrl("/agent-readme.md"))).toBe("https://connect.md/agent-readme.md");
  });

  it("accepts a canonical fallback for the configured canonical site only", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/v1");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://preview.connect.test/");

    expect(publicProtocolUrl("/agent-readme.md", absoluteSiteUrl("/agent-readme.md"))).toBe("https://preview.connect.test/agent-readme.md");
    expect(publicProtocolUrl("/agent-readme.md", "https://connect.md/agent-readme.md")).toBe("/agent-readme.md");
  });

  it("preserves a valid split API origin with an explicit port", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test:8443/");

    expect(publicProtocolUrl("/openapi.json", "https://evil.test/openapi.json")).toBe("https://api.connect.test:8443/openapi.json");
    expect(publicDiscoveryUrl("/agent-readme.md", "https://evil.test/agent-readme.md")).toBe("https://api.connect.test:8443/agent-readme.md");
  });

  it("normalizes blank and whitespace site URLs before URL construction", () => {
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "   ");
    expect(publicSiteOrigin()).toBe("https://connect.md");
    expect(absoluteSiteUrl("/agent-readme.md")).toBe("https://connect.md/agent-readme.md");

    vi.stubEnv("NEXT_PUBLIC_SITE_URL", " https://preview.connect.test/ ");
    expect(publicSiteOrigin()).toBe("https://preview.connect.test/");
    expect(absoluteSiteUrl("/agent-readme.md")).toBe("https://preview.connect.test/agent-readme.md");
  });
});
