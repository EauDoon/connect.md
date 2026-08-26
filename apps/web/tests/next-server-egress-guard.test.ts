import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = resolve(process.cwd());
const guard = resolve(webRoot, "e2e", "next-server-egress-guard.cjs");

type EgressAudit = {
  version: number;
  fixture_origin: string;
  blocked_attempts: Array<{ module: string; operation: string }>;
};

function runAttempt(source: string, expectedOutput = "blocked"): EgressAudit {
  const directory = mkdtempSync(resolve(tmpdir(), "connectmd-next-egress-guard-test-"));
  const auditPath = resolve(directory, "next-server-egress-audit.json");
  try {
    expect(
      execFileSync(process.execPath, ["--require", guard, "-e", source], {
        encoding: "utf8",
        env: {
          NODE_ENV: "test",
          CONNECTMD_E2E_FIXTURE_API_ORIGIN: "http://127.0.0.1:43123",
          CONNECTMD_E2E_NEXT_EGRESS_AUDIT_PATH: auditPath,
        },
        stdio: ["ignore", "pipe", "pipe"],
      }).trim(),
    ).toBe(expectedOutput);
    const raw = readFileSync(auditPath, "utf8");
    expect(raw).not.toContain("example.invalid");
    expect(raw).not.toContain("secret");
    return JSON.parse(raw) as EgressAudit;
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
}

function runBlockedAttempt(source: string): EgressAudit {
  return runAttempt(source);
}

describe("Next server browser-release egress guard", () => {
  it("blocks and minimally audits non-fixture HTTP before DNS or TCP", () => {
    expect(
      runBlockedAttempt(
        'try { require("node:https").get("https://example.invalid/"); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "https", operation: "get" }],
    });
  });

  it("allows only the exact loopback fixture HTTP authority without recording an attempted egress", () => {
    expect(
      runAttempt(
        'const http = require("node:http"); const request = http.get("http://127.0.0.1:43123/"); const done = () => process.stdout.write("allowed"); request.once("response", (response) => { response.resume(); done(); }); request.once("error", done);',
        "allowed",
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [],
    });
    expect(
      runAttempt(
        'const server = require("node:net").createServer(); server.listen(0, "127.0.0.1", () => server.close(() => process.stdout.write("allowed")));',
        "allowed",
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [],
    });
  });

  it("rejects URL-plus-options authority and custom transport overrides before use", () => {
    expect(
      runBlockedAttempt(
        'try { require("node:http").get("http://127.0.0.1:43123/", { hostname: "example.invalid", port: 443 }); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "http", operation: "get" }],
    });
  });

  it("rejects fetch custom dispatchers before they can replace fixture transport", () => {
    expect(
      runBlockedAttempt(
        'try { fetch("http://127.0.0.1:43123/", { dispatcher: {} }); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "fetch", operation: "fetch" }],
    });
  });

  it("blocks Resolver callback and promise instances before DNS", () => {
    expect(
      runBlockedAttempt(
        'try { new (require("node:dns").Resolver)().resolve4("example.invalid", () => {}); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "dns", operation: "Resolver.resolve4" }],
    });
    expect(
      runBlockedAttempt(
        'try { new (require("node:dns").promises.Resolver)().resolve4("example.invalid"); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "dns", operation: "promises.Resolver.resolve4" }],
    });
  });

  it("blocks datagram constructors and prototype network methods", () => {
    expect(
      runBlockedAttempt(
        'try { new (require("node:dgram").Socket)("udp4"); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "dgram", operation: "Socket" }],
    });
    expect(
      runBlockedAttempt(
        'try { require("node:dgram").Socket.prototype.bind.call({}, 43123); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "dgram", operation: "Socket.bind" }],
    });
  });

  it("blocks child-process execution in the preloaded Next process", () => {
    expect(
      runBlockedAttempt(
        'try { require("node:child_process").spawn("must-not-run"); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "child_process", operation: "spawn" }],
    });
  });

  it("blocks ChildProcess prototype spawning and alternate egress-capable constructors", () => {
    expect(
      runBlockedAttempt(
        'try { new (require("node:child_process").ChildProcess)().spawn({ file: "must-not-run", args: [] }); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "child_process", operation: "ChildProcess.spawn" }],
    });
    expect(
      runBlockedAttempt(
        'try { new WebSocket("ws://example.invalid/secret"); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "WebSocket", operation: "constructor" }],
    });
    expect(
      runBlockedAttempt(
        'try { new (require("node:http").WebSocket)("ws://example.invalid/secret?token=secret"); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "http", operation: "WebSocket" }],
    });
    expect(
      runBlockedAttempt(
        'try { new (require("node:worker_threads").Worker)("throw new Error(\\\"must-not-run\\\")", { eval: true, execArgv: [] }); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "worker_threads", operation: "Worker" }],
    });
  });

  it("blocks the deprecated raw datagram handle escape hatch", () => {
    expect(
      runBlockedAttempt(
        'try { require("node:dgram")._createSocketHandle("127.0.0.1", 0, "udp4", undefined, 0); } catch { process.stdout.write("blocked"); }',
      ),
    ).toEqual({
      version: 1,
      fixture_origin: "http://127.0.0.1:43123",
      blocked_attempts: [{ module: "dgram", operation: "_createSocketHandle" }],
    });
  });
});
