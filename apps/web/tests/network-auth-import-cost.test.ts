import { describe, expect, it, vi } from "vitest";

// Every /api/network/v1 handler imports lib/network/http, which imports
// auth-service. A password derivation at import time would make every cold
// start, including public reads, pay a 32 MiB scrypt; a synchronous one would
// also block the event loop. Count derivations through a pass-through mock.
const derivations = vi.hoisted(() => ({ async: 0, sync: 0 }));

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("node:crypto", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:crypto")>();
  const scrypt = ((...args: Parameters<typeof actual.scrypt>) => {
    derivations.async++;
    return (actual.scrypt as (...inner: unknown[]) => void)(...args);
  }) as typeof actual.scrypt;
  const scryptSync = ((...args: Parameters<typeof actual.scryptSync>) => {
    derivations.sync++;
    return actual.scryptSync(...args);
  }) as typeof actual.scryptSync;
  return { ...actual, default: { ...actual, scrypt, scryptSync }, scrypt, scryptSync };
});

describe("network auth import cost", () => {
  it("derives no password key when the network route modules load", async () => {
    await import("@/lib/network/auth-service");
    await import("@/lib/network/http");
    expect(derivations).toEqual({ async: 0, sync: 0 });
  });

  it("derives passwords asynchronously and never synchronously", async () => {
    const { hashPassword, verifyPassword } = await import("@/lib/network/secrets");
    const stored = await hashPassword("GoodPassword123");
    expect(await verifyPassword("GoodPassword123", stored)).toBe(true);
    expect(derivations).toEqual({ async: 2, sync: 0 });
  });
});
