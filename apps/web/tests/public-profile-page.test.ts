import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.stubGlobal("React", React);

const mocks = vi.hoisted(() => ({
  notFoundSignal: new Error("not-found sentinel"),
  configured: true,
  getPublishedProfile: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  notFound: () => {
    throw mocks.notFoundSignal;
  },
}));
vi.mock("@/lib/network/db", () => ({
  networkDatabaseConfigured: () => mocks.configured,
  database: () => ({}),
}));
vi.mock("@/lib/network/profiles", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/network/profiles")>()),
  getPublishedProfile: mocks.getPublishedProfile,
}));

const { ProfileError } = await import("@/lib/network/profiles");
const { default: PublicProfilePage, generateMetadata } = await import("../app/p/[handle]/page");
const { default: PublicProfileLayout } = await import("../app/p/[handle]/layout");
const { default: PublicProfileNotFound } = await import("../app/p/not-found");

const PUBLISHED = {
  handle: "ada-lovelace",
  markdown: "---\nkind: profile\n---\n\n# Ada Lovelace\n\n## About\n\nAnalyst of engines.\n",
  etag: '"etag"',
  visibility: "public" as const,
  publishedAt: "2026-10-01T00:00:00.000Z",
  updatedAt: "2026-10-01T00:00:00.000Z",
  createdAt: "2026-10-01T00:00:00.000Z",
};

function props(handle: string) {
  return { params: Promise.resolve({ handle }) };
}

function layout(handle: string) {
  return PublicProfileLayout({ ...props(handle), children: createElement("p", null, "child") });
}

beforeEach(() => {
  mocks.configured = true;
  mocks.getPublishedProfile.mockReset();
});

describe("public profile page", () => {
  it.each([
    ["an impossible handle", "Ada!"],
    ["an undecodable handle", "%E0%A4%A"],
  ])("is not-found for %s without querying", async (_label, handle) => {
    await expect(layout(handle)).rejects.toBe(mocks.notFoundSignal);
    await expect(generateMetadata(props(handle))).rejects.toBe(mocks.notFoundSignal);
    await expect(PublicProfilePage(props(handle))).rejects.toBe(mocks.notFoundSignal);
    expect(mocks.getPublishedProfile).not.toHaveBeenCalled();
  });

  it("is not-found while the network database is unconfigured", async () => {
    mocks.configured = false;
    await expect(layout("ada-lovelace")).rejects.toBe(mocks.notFoundSignal);
    await expect(generateMetadata(props("ada-lovelace"))).rejects.toBe(mocks.notFoundSignal);
    await expect(PublicProfilePage(props("ada-lovelace"))).rejects.toBe(mocks.notFoundSignal);
    expect(mocks.getPublishedProfile).not.toHaveBeenCalled();
  });

  it("is not-found for a private, unpublished, or missing profile", async () => {
    mocks.getPublishedProfile.mockRejectedValue(new ProfileError("not-found", "No published profile for that handle."));
    await expect(layout("ada-lovelace")).rejects.toBe(mocks.notFoundSignal);
    await expect(generateMetadata(props("ada-lovelace"))).rejects.toBe(mocks.notFoundSignal);
    await expect(PublicProfilePage(props("ada-lovelace"))).rejects.toBe(mocks.notFoundSignal);
  });

  it("does not disguise an unexpected failure as not-found", async () => {
    const outage = new Error("database unreachable");
    mocks.getPublishedProfile.mockRejectedValue(outage);
    await expect(layout("ada-lovelace")).rejects.toBe(outage);
    await expect(PublicProfilePage(props("ada-lovelace"))).rejects.toBe(outage);
  });

  it("indexes only a published profile, under its normalized canonical handle", async () => {
    mocks.getPublishedProfile.mockResolvedValue(PUBLISHED);
    expect(renderToStaticMarkup(await layout("Ada-Lovelace"))).toBe("<p>child</p>");
    const metadata = await generateMetadata(props("Ada-Lovelace"));
    expect(mocks.getPublishedProfile).toHaveBeenCalledWith(expect.anything(), "ada-lovelace");
    expect(metadata.title).toBe("@ada-lovelace");
    expect(metadata.robots).toEqual({ index: true, follow: true });
    expect(metadata.alternates).toEqual({ canonical: "/p/ada-lovelace" });

    const markup = renderToStaticMarkup(await PublicProfilePage(props("Ada-Lovelace")));
    expect(markup).toContain('data-testid="public-profile"');
    expect(markup).toContain("@ada-lovelace");
    expect(markup).toContain("Analyst of engines.");
  });
});

describe("public profile not-found segment", () => {
  it("keeps a touch-safe path back to discovery", () => {
    const markup = renderToStaticMarkup(createElement(PublicProfileNotFound));
    expect(markup).toContain("No published profile at this address.");
    expect(markup).toContain("The profile may be private, unpublished, or nonexistent.");
    expect(markup).toContain('href="/discover"');
    expect(markup).toContain("min-h-11");
    expect(markup).toContain('data-testid="profile-unavailable"');
    expect(markup).not.toMatch(/[–—]/u);
  });
});
