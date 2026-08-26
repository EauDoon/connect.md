"use strict";

const childProcess = require("node:child_process");
const dgram = require("node:dgram");
const dns = require("node:dns");
const fs = require("node:fs");
const http = require("node:http");
const http2 = require("node:http2");
const https = require("node:https");
const { syncBuiltinESMExports } = require("node:module");
const net = require("node:net");
const path = require("node:path");
const tls = require("node:tls");
const workerThreads = require("node:worker_threads");

const auditPath = process.env.CONNECTMD_E2E_NEXT_EGRESS_AUDIT_PATH;
const fixtureOrigin = process.env.CONNECTMD_E2E_FIXTURE_API_ORIGIN;

function exactFixtureOrigin(value) {
  const url = new URL(value ?? "");
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    !url.port ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("invalid browser release fixture authority");
  }
  return url.origin;
}

const exactFixtureAuthority = exactFixtureOrigin(fixtureOrigin);
if (!auditPath || !path.isAbsolute(auditPath)) throw new Error("invalid browser release egress audit path");

function writeAudit(blockedAttempts) {
  fs.writeFileSync(
    auditPath,
    `${JSON.stringify({ version: 1, fixture_origin: exactFixtureAuthority, blocked_attempts: blockedAttempts })}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
}

const blockedAttempts = [];
writeAudit(blockedAttempts);

function block(moduleName, operation) {
  blockedAttempts.push({ module: moduleName, operation });
  writeAudit(blockedAttempts);
  throw new Error("Connect.md browser release guard blocked server egress");
}

function isRecord(value) {
  return value !== null && typeof value === "object";
}

function includesUnsafeHttpHook(options) {
  return ["agent", "createConnection", "lookup", "socketPath", "transport"].some((name) => name in options);
}

function includesHttpAuthorityOverride(options) {
  return ["auth", "host", "hostname", "href", "port", "protocol"].some((name) => name in options);
}

function exactHttpAuthority(args, defaultProtocol) {
  const first = args[0];
  let url;
  if (typeof first === "string" || first instanceof URL) {
    if (isRecord(args[1]) && (includesUnsafeHttpHook(args[1]) || includesHttpAuthorityOverride(args[1]))) {
      return false;
    }
    try {
      url = new URL(first);
    } catch {
      return false;
    }
  } else if (isRecord(first)) {
    if (includesUnsafeHttpHook(first)) return false;
    const protocol = first.protocol ?? defaultProtocol;
    const hostname = first.hostname ?? first.host;
    const port = first.port;
    if (
      protocol !== defaultProtocol ||
      typeof hostname !== "string" ||
      first.auth ||
      typeof port !== "undefined" && typeof port !== "string" && typeof port !== "number"
    ) {
      return false;
    }
    try {
      url = new URL(`${protocol}//${hostname}${typeof port === "undefined" ? "" : `:${port}`}/`);
    } catch {
      return false;
    }
  } else {
    return false;
  }
  return !url.username && !url.password && url.origin === exactFixtureAuthority;
}

function exactNetAuthority(args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  const second = Array.isArray(args[0]) ? undefined : args[1];
  const options = isRecord(first) ? first : { port: first, host: second };
  if (
    !isRecord(options) ||
    ["fd", "handle", "lookup", "path", "socketPath"].some((name) => options[name] !== null && typeof options[name] !== "undefined")
  ) {
    return false;
  }
  const host = options.host ?? options.hostname;
  const port = options.port;
  return host === "127.0.0.1" && Number(port) === Number(new URL(exactFixtureAuthority).port);
}

function guardHttp(moduleName, moduleValue, defaultProtocol) {
  for (const operation of ["request", "get"]) {
    const original = moduleValue[operation];
    moduleValue[operation] = function guardedHttp(...args) {
      if (!exactHttpAuthority(args, defaultProtocol)) block(moduleName, operation);
      return original.apply(this, args);
    };
  }
}

guardHttp("http", http, "http:");
guardHttp("https", https, "https:");

for (const operation of ["connect", "createConnection"]) {
  const original = net[operation];
  net[operation] = function guardedNet(...args) {
    if (!exactNetAuthority(args)) block("net", operation);
    return original.apply(this, args);
  };
}

const originalSocketConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function guardedSocketConnect(...args) {
  if (!exactNetAuthority(args)) block("net", "socket.connect");
  return originalSocketConnect.apply(this, args);
};

for (const operation of ["connect"]) {
  tls[operation] = function guardedTls() {
    return block("tls", operation);
  };
  http2[operation] = function guardedHttp2() {
    return block("http2", operation);
  };
}

const DNS_OPERATIONS = [
  "lookup",
  "lookupService",
  "resolve",
  "resolve4",
  "resolve6",
  "resolveAny",
  "resolveCaa",
  "resolveCname",
  "resolveMx",
  "resolveNaptr",
  "resolveNs",
  "resolvePtr",
  "resolveSoa",
  "resolveSrv",
  "resolveTlsa",
  "resolveTxt",
  "reverse",
];

const originalDnsLookup = dns.lookup;
const originalDnsPromiseLookup = dns.promises?.lookup;

for (const operation of DNS_OPERATIONS) {
  if (typeof dns[operation] === "function") {
    dns[operation] =
      operation === "lookup"
        ? function guardedDnsLookup(hostname, ...args) {
            if (hostname !== "127.0.0.1") return block("dns", operation);
            return originalDnsLookup.call(this, hostname, ...args);
          }
        : function guardedDns() {
            return block("dns", operation);
          };
  }
}

for (const operation of DNS_OPERATIONS) {
  if (dns.promises && typeof dns.promises[operation] === "function") {
    dns.promises[operation] =
      operation === "lookup"
        ? function guardedDnsPromiseLookup(hostname, ...args) {
            if (hostname !== "127.0.0.1") return block("dns", `promises.${operation}`);
            return originalDnsPromiseLookup.call(this, hostname, ...args);
          }
        : function guardedDnsPromise() {
            return block("dns", `promises.${operation}`);
          };
  }
}

function guardResolverPrototype(Resolver, operationPrefix) {
  if (typeof Resolver !== "function" || !Resolver.prototype) return;
  for (const operation of DNS_OPERATIONS.filter((name) => name !== "lookup" && name !== "lookupService")) {
    if (typeof Resolver.prototype[operation] === "function") {
      Resolver.prototype[operation] = function guardedResolver() {
        return block("dns", `${operationPrefix}.${operation}`);
      };
    }
  }
}

guardResolverPrototype(dns.Resolver, "Resolver");
guardResolverPrototype(dns.promises?.Resolver, "promises.Resolver");

const OriginalDgramSocket = dgram.Socket;
for (const operation of ["addMembership", "addSourceSpecificMembership", "bind", "connect", "send", "sendto"]) {
  if (OriginalDgramSocket?.prototype && typeof OriginalDgramSocket.prototype[operation] === "function") {
    OriginalDgramSocket.prototype[operation] = function guardedDgramSocket() {
      return block("dgram", `Socket.${operation}`);
    };
  }
}

function GuardedDgramSocket() {
  return block("dgram", "Socket");
}

if (OriginalDgramSocket?.prototype) GuardedDgramSocket.prototype = OriginalDgramSocket.prototype;
dgram.Socket = GuardedDgramSocket;
dgram.createSocket = function guardedDgram() {
  return block("dgram", "createSocket");
};
if (typeof dgram._createSocketHandle === "function") {
  dgram._createSocketHandle = function guardedDgramSocketHandle() {
    return block("dgram", "_createSocketHandle");
  };
}

for (const operation of ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"]) {
  childProcess[operation] = function guardedChildProcess() {
    return block("child_process", operation);
  };
}

if (childProcess.ChildProcess?.prototype && typeof childProcess.ChildProcess.prototype.spawn === "function") {
  childProcess.ChildProcess.prototype.spawn = function guardedChildProcessPrototype() {
    return block("child_process", "ChildProcess.spawn");
  };
}

if (typeof workerThreads.Worker === "function") {
  workerThreads.Worker = function guardedWorker() {
    return block("worker_threads", "Worker");
  };
}

if (typeof globalThis.fetch === "function") {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = function guardedFetch(input, ...rest) {
    let target;
    try {
      target = new URL(input instanceof Request ? input.url : input);
    } catch {
      return block("fetch", "fetch");
    }
    const options = rest[0];
    if (
      target.username ||
      target.password ||
      target.origin !== exactFixtureAuthority ||
      (isRecord(options) && ["agent", "dispatcher", "lookup"].some((name) => name in options))
    ) {
      return block("fetch", "fetch");
    }
    return originalFetch.call(this, input, ...rest);
  };
}

if (typeof globalThis.WebSocket === "function") {
  globalThis.WebSocket = function guardedWebSocket() {
    return block("WebSocket", "constructor");
  };
}

if (typeof http.WebSocket === "function") {
  Object.defineProperty(http, "WebSocket", {
    configurable: false,
    enumerable: true,
    value: function guardedHttpWebSocket() {
      return block("http", "WebSocket");
    },
    writable: false,
  });
}

syncBuiltinESMExports();
