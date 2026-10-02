import { afterEach, describe, expect, it, vi } from "vitest";
import { clientKeyFromHeaders } from "@/lib/network/auth-service";
import { MAX_PROFILE_JSON_BYTES, readBoundedJson, rejectCrossOrigin, withNetworkUnavailable } from "@/lib/network/http";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
afterEach(() => vi.unstubAllEnvs());

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
  });

  it("ignores caller-supplied forwarded IPs outside Vercel", () => {
    vi.stubEnv("VERCEL", "");
    expect(clientKeyFromHeaders(new Headers({ "x-forwarded-for": "192.0.2.1" }))).toBe(clientKeyFromHeaders(new Headers({ "x-forwarded-for": "192.0.2.2" })));
    vi.stubEnv("VERCEL", "1");
    expect(clientKeyFromHeaders(new Headers({ "x-vercel-forwarded-for": "192.0.2.1" }))).not.toBe(clientKeyFromHeaders(new Headers({ "x-vercel-forwarded-for": "192.0.2.2" })));
  });

  it("returns bounded JSON rather than database diagnostics", async () => {
    const response = await withNetworkUnavailable(async () => { throw new Error("private database details"); });
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("private database details");
  });
});
