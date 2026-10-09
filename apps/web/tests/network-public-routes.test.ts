import { beforeEach, describe, expect, it, vi } from "vitest";

// The public profile routes must refuse malformed input before any SQL runs,
// and must never report bad input as a network outage.
const db = vi.hoisted(() => ({ calls: [] as unknown[][] }));

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("@/lib/network/db", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/network/db")>();
  const sql = (...args: unknown[]) => {
    db.calls.push(args);
    return Promise.resolve([]);
  };
  return { ...actual, database: () => sql };
});

const { GET: listProfiles } = await import("@/app/api/network/v1/public/profiles/route");
const { GET: readProfile } = await import("@/app/api/network/v1/public/profiles/[handle]/route");

function list(prefix: string) {
  return listProfiles(new Request(`https://network.example/api/network/v1/public/profiles?prefix=${encodeURIComponent(prefix)}`));
}

function read(handle: string) {
  return readProfile(new Request("https://network.example/api"), { params: Promise.resolve({ handle }) });
}

beforeEach(() => {
  db.calls.length = 0;
});

describe("public profile discovery prefix", () => {
  it("refuses LIKE wildcards and oversized prefixes without querying", async () => {
    for (const prefix of ["_", "%", "a_b", "ada%", "x".repeat(200)]) {
      const response = await list(prefix);
      expect(response.status, prefix).toBe(400);
      expect(await response.json()).toEqual({ ok: false, reason: "request-invalid" });
    }
    expect(db.calls).toHaveLength(0);
  });

  it("passes a normalized literal prefix to the query", async () => {
    const response = await list(" Ada-L ");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true, profiles: [] });
    expect(db.calls).toHaveLength(1);
    expect(db.calls[0]).toContain("ada-l%");
  });

  it("treats a blank prefix as no filter", async () => {
    expect((await list("   ")).status).toBe(200);
    expect(db.calls).toHaveLength(1);
    expect(db.calls[0]?.some((value) => typeof value === "string" && value.includes("%"))).toBe(false);
  });
});

describe("public profile read by handle", () => {
  it("answers not-found, never an outage, for undecodable or impossible handles", async () => {
    for (const handle of ["%E0%A4%A", "%", "Ada!", "a", "bad%20handle", "x".repeat(31)]) {
      const response = await read(handle);
      expect(response.status, handle).toBe(404);
      expect(await response.json()).toEqual({ ok: false, reason: "not-found" });
    }
    expect(db.calls).toHaveLength(0);
  });

  it("still looks up a valid handle, normalized, whether or not Next decoded it", async () => {
    expect((await read("Ada-Lovelace")).status).toBe(404);
    expect((await read("ada%2Dlovelace")).status).toBe(404);
    expect(db.calls).toHaveLength(2);
    for (const call of db.calls) expect(call).toContain("ada-lovelace");
  });
});
