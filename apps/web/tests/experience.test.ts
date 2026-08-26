import { afterEach, describe, expect, it, vi } from "vitest";

import { HUMAN_JOURNEY, humanJourneyPosition } from "../lib/human-journey";
import { PRIMARY_NAVIGATION, PUBLIC_PRIMARY_NAVIGATION, PUBLIC_UTILITY_NAVIGATION, WORKSPACE_NAVIGATION } from "../lib/navigation";
import { publicAuthConfigured } from "../lib/public-auth-config";
import { DEFAULT_REPRESENTATIVE_STATUS, REPRESENTATIVE_PROTOCOL_LINKS, representativeFiltersFromParams, representativeHref, representativeProtocolLinks } from "../lib/representatives";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("guided Human Mode and primary navigation", () => {
  it("keeps the four human journey chapters in a deterministic order", () => {
    expect(HUMAN_JOURNEY.map((step) => step.id)).toEqual(["foundation", "shape", "review", "release"]);
    expect(humanJourneyPosition("foundation")).toEqual({ current: 1, total: 4, percent: 25 });
    expect(humanJourneyPosition("release")).toEqual({ current: 4, total: 4, percent: 100 });
  });

  it("keeps shared primary navigation compact around the durable product paths", () => {
    expect(PRIMARY_NAVIGATION.map((link) => link.href)).toEqual(["/discover", "/human", "/network", "/agents"]);
    expect(PUBLIC_PRIMARY_NAVIGATION).toEqual([
      { href: "/discover", label: "Discover" },
      { href: "/human", label: "Create" },
      { href: "/agent-directory", label: "Agent directory" },
    ]);
    expect(PUBLIC_UTILITY_NAVIGATION).toEqual([{ href: "/trust", label: "Trust & data" }]);
    expect(WORKSPACE_NAVIGATION.map((link) => link.href)).toEqual(["/human", "/network", "/inbox", "/feed", "/applications", "/employer", "/agents", "/moderation"]);
  });

  it.each([
    [undefined, false],
    ["", false],
    ["   ", false],
    ["pk_test_connectmd", true],
  ] as const)("advertises private navigation only for a nonblank public auth key", (publishableKey, expected) => {
    expect(publicAuthConfigured(publishableKey)).toBe(expected);
  });

  it("keeps representative discovery on public profiles and existing protocol routes", () => {
    const filters = representativeFiltersFromParams(new URLSearchParams("kind=resume&representation_status=untrusted&q=payments"));
    expect(filters).toMatchObject({ kind: "profile", representationStatus: DEFAULT_REPRESENTATIVE_STATUS, q: "payments" });
    expect(new URL(representativeHref(filters), "https://connect.test").searchParams.get("kind")).toBe("profile");
    expect(REPRESENTATIVE_PROTOCOL_LINKS.map((link) => link.href)).toEqual(["/llms.txt", "/.well-known/agent-card.json", "/.well-known/oauth-protected-resource/mcp"]);
  });

  it("resolves representative protocol links against a valid split API origin", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.connect.test/");

    expect(representativeProtocolLinks().map((link) => link.href)).toEqual([
      "https://api.connect.test/llms.txt",
      "https://api.connect.test/.well-known/agent-card.json",
      "https://api.connect.test/.well-known/oauth-protected-resource/mcp",
    ]);
  });
});
