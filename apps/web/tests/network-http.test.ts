import { afterEach, describe, expect, it, vi } from "vitest";
import { clientKeyFromHeaders } from "@/lib/network/auth-service";
import { NetworkUnavailableError } from "@/lib/network/db";
import { MAX_PROFILE_JSON_BYTES, configuredNetworkOrigin, readBoundedJson, rejectCrossOrigin, withNetworkUnavailable } from "@/lib/network/http";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
afterEach(() => { vi.unstubAllEnvs(); vi.restoreAllMocks(); });

function request(body: string, contentType = "application/json") {
  return new Request("https://network.example/api", { method: "POST", headers: { "content-type": contentType }, body });
}

describe("network HTTP trust boundary", () => {
  it("rejects wrong media types, unknown fields, duplicate keys, nested objects, and invalid UTF-8", async () => {
    expect(await readBoundedJson(request('{"body":"ok"}', "text/plain"), ["body"])).toBeNull();
    expect(await readBoundedJson(request('{"body":"ok","extra":true}'), ["body"])).toBeNull();
    expect(await readBoundedJson(request('{"body":"one","bo\\u0064y":"two"}'), ["body"])).toBeNull();
    expect(await readBoundedJson(request('{"body":{"nested":true}}'), ["body"])).toBeNull();
    const invalid = new Request("https://network.example", { method: "POST", headers: { "content-type": "application/json" }, body: new Uint8Array([0xff]) });
    expect(await readBoundedJson(invalid, ["body"])).toBeNull();
    expect(await readBoundedJson(request('{"body":"quoted \\\"body\\\": and braces {}"}'), ["body"])).toEqual({ body: 'quoted "body": and braces {}' });
  });

  it("counts streamed bytes and cancels before consuming an oversized body", async () => {
    let chunksRead = 0;
    let canceled = false;
    const stream = new ReadableStream({
      pull(controller) { chunksRead++; controller.enqueue(new TextEncoder().encode("é".repeat(4096))); },
      cancel() { canceled = true; },
    }, { highWaterMark: 0 });
    const streamed = new Request("https://network.example", { method: "POST", headers: { "content-type": "application/json" }, body: stream, duplex: "half" } as RequestInit);
    expect(await readBoundedJson(streamed, ["body"])).toBeNull();
    expect(chunksRead).toBe(2);
    expect(canceled).toBe(true);
    expect(await readBoundedJson(request(JSON.stringify({ body: "é".repeat(5000) })), ["body"])).toBeNull();
  });

  it("allows the full canonical profile size inside its bounded JSON envelope", async () => {
    const markdown = "x".repeat(131072);
    expect(await readBoundedJson(request(JSON.stringify({ markdown })), ["markdown"], MAX_PROFILE_JSON_BYTES)).toEqual({ markdown });
  });

  it("requires the exact configured Origin for cookie mutations", () => {
    vi.stubEnv("CONNECTMD_NETWORK_ORIGIN", "https://network.example");
    expect(rejectCrossOrigin(new Request("https://network.example", { headers: { origin: "https://network.example" } }))).toBeNull();
    for (const origin of ["https://attacker.example", "https://network.example.attacker.example", "null", ""]) {
      expect(rejectCrossOrigin(new Request("https://network.example", { headers: { origin } }))?.status).toBe(403);
    }
    expect(rejectCrossOrigin(new Request("https://network.example"))?.status).toBe(403);
    expect(rejectCrossOrigin(new Request("https://network.example", {
      headers: { origin: "https://network.example", "sec-fetch-site": "cross-site" },
    }))?.status).toBe(403);
  });

  it("normalizes a trailing slash, whitespace, and a blank override instead of failing closed", () => {
    const allowed = (env: Record<string, string>) => {
      vi.unstubAllEnvs();
      vi.stubEnv("CONNECTMD_NETWORK_ORIGIN", env.CONNECTMD_NETWORK_ORIGIN ?? "");
      vi.stubEnv("NEXT_PUBLIC_SITE_URL", env.NEXT_PUBLIC_SITE_URL ?? "");
      return rejectCrossOrigin(new Request("https://network.example", { headers: { origin: "https://network.example" } }));
    };
    expect(allowed({ CONNECTMD_NETWORK_ORIGIN: "https://network.example/" })).toBeNull();
    expect(allowed({ CONNECTMD_NETWORK_ORIGIN: " https://network.example " })).toBeNull();
    expect(allowed({ CONNECTMD_NETWORK_ORIGIN: "", NEXT_PUBLIC_SITE_URL: "https://network.example/" })).toBeNull();
    expect(allowed({ CONNECTMD_NETWORK_ORIGIN: "  ", NEXT_PUBLIC_SITE_URL: "https://network.example" })).toBeNull();
    expect(allowed({ CONNECTMD_NETWORK_ORIGIN: "https://network.example/app" })?.status).toBe(403);
    expect(allowed({ CONNECTMD_NETWORK_ORIGIN: "javascript:x" })?.status).toBe(403);
    // A malformed explicit override must not silently fall back to the site URL.
    expect(allowed({ CONNECTMD_NETWORK_ORIGIN: "https://network.example/app", NEXT_PUBLIC_SITE_URL: "https://network.example" })?.status).toBe(403);
    expect(allowed({})?.status).toBe(403);
  });

  it("derives one canonical origin from the configured value", () => {
    expect(configuredNetworkOrigin({ CONNECTMD_NETWORK_ORIGIN: "HTTPS://Network.Example:443/" })).toBe("https://network.example");
    expect(configuredNetworkOrigin({ NEXT_PUBLIC_SITE_URL: "http://127.0.0.1:3210" })).toBe("http://127.0.0.1:3210");
    for (const value of ["https://user:pass@network.example", "https://network.example/?q=1", "https://network.example/#x", "ftp://network.example", "not a url"]) {
      expect(configuredNetworkOrigin({ CONNECTMD_NETWORK_ORIGIN: value })).toBeNull();
    }
    expect(configuredNetworkOrigin({})).toBeNull();
  });

  it("ignores caller-supplied forwarded IPs outside Vercel", () => {
    vi.stubEnv("VERCEL", "");
    expect(clientKeyFromHeaders(new Headers({ "x-forwarded-for": "192.0.2.1" }))).toBe(clientKeyFromHeaders(new Headers({ "x-forwarded-for": "192.0.2.2" })));
    vi.stubEnv("VERCEL", "1");
    expect(clientKeyFromHeaders(new Headers({ "x-vercel-forwarded-for": "192.0.2.1" }))).not.toBe(clientKeyFromHeaders(new Headers({ "x-vercel-forwarded-for": "192.0.2.2" })));
  });

  it("returns bounded JSON rather than database diagnostics", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await withNetworkUnavailable(async () => { throw new Error("private database details"); });
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("private database details");
  });

  it("logs one correlation id for an unexpected failure without its message", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const failure = Object.assign(new Error("private database details for ada@example.test"), { code: "ECONNRESET", detail: "private row" });
    const response = await withNetworkUnavailable(async () => { throw failure; });
    expect(response.status).toBe(503);
    const body = await response.json() as { ok: boolean; reason: string; errorId: string };
    expect(body.reason).toBe("network-unavailable");
    expect(body.errorId).toMatch(/^[0-9a-f-]{36}$/);
    expect(response.headers.get("x-connectmd-error-id")).toBe(body.errorId);
    expect(log).toHaveBeenCalledTimes(1);
    const [label, fields] = log.mock.calls[0] as [string, Record<string, unknown>];
    expect(label).toBe("[connectmd-network] unexpected route failure");
    expect(fields).toEqual({ errorId: body.errorId, name: "Error", code: "ECONNRESET" });
    expect(JSON.stringify(log.mock.calls)).not.toMatch(/private|ada@example/);
    expect(JSON.stringify(body)).not.toMatch(/private|ada@example/);
  });

  it("keeps the unconfigured-database contract silent and uncorrelated", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await withNetworkUnavailable(async () => { throw new NetworkUnavailableError(); });
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ ok: false, reason: "network-database-not-configured" });
    expect(response.headers.get("x-connectmd-error-id")).toBeNull();
    expect(log).not.toHaveBeenCalled();
  });
});
