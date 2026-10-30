import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { AGENT_SCOPES } from "../lib/network/agent-grants";

const docPath = fileURLToPath(new URL("../../../docs/network-agent-api.md", import.meta.url));
const doc = readFileSync(docPath, "utf8");
const agentRoutes = fileURLToPath(new URL("../app/api/network/v1/agent/", import.meta.url));

describe("network agent API reference", () => {
  it("resolves every relative link", () => {
    const links = [...doc.matchAll(/\]\(([^)\s]+)\)/gu)].map((match) => match[1]).filter((href) => !/^[a-z][a-z0-9+.-]*:/iu.test(href) && !href.startsWith("#"));
    expect(links.length).toBeGreaterThan(0);
    for (const href of links) expect(existsSync(resolve(dirname(docPath), href.split("#")[0])), href).toBe(true);
  });

  it("documents every agent route and every scope", () => {
    for (const route of readdirSync(agentRoutes)) expect(doc, route).toMatch(new RegExp(`### (?:GET|PUT) agent/${route}(?: |$)`, "mu"));
    for (const scope of AGENT_SCOPES) expect(doc).toContain(`\`${scope}\``);
  });

  it("is linked from both READMEs and the dashboard, and stays out of the public agent guides", () => {
    expect(readFileSync(new URL("../../../README.md", import.meta.url), "utf8")).toContain("docs/network-agent-api.md");
    expect(readFileSync(new URL("../README.md", import.meta.url), "utf8")).toContain("docs/network-agent-api.md");
    expect(readFileSync(new URL("../components/network/network-dashboard.tsx", import.meta.url), "utf8")).toContain("docs/network-agent-api.md");
    for (const guide of ["../public/llms.txt", "../public/agent-readme.md"]) {
      const url = new URL(guide, import.meta.url);
      if (existsSync(url)) expect(readFileSync(url, "utf8"), guide).not.toContain("agent/grant");
    }
  });
});
