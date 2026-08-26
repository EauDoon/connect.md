import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { CONTINUOUS_AGENT_HANDOFF_GUIDE } from "../components/agent-integration-panel";

describe("agent integration handoff guide", () => {
  it("covers discovery, synchronization, review, expiry, and revocation without carrying a credential", () => {
    for (const value of ["/llms.txt", "/llms-full.txt", "/openapi.json", "/mcp", "GET /v1/documents?kind=&limit=&cursor=", "GET /v1/changes?limit=&cursor=", "proposal_only", "direct", "If-Match", "expires or is revoked"]) expect(CONTINUOUS_AGENT_HANDOFF_GUIDE).toContain(value);
    for (const forbidden of ["cng_", "Clerk subject", "mandate ID", "private Markdown", "recipient policy"]) expect(CONTINUOUS_AGENT_HANDOFF_GUIDE).not.toContain(forbidden);
  });

  it("keeps the one-time credential display isolated from both copied guides", () => {
    const integration = readFileSync(new URL("../components/agent-integration-panel.tsx", import.meta.url), "utf8");
    const delegations = readFileSync(new URL("../components/agent-delegation-manager.tsx", import.meta.url), "utf8");
    const panels = readFileSync(new URL("../components/agent-delegation-panels.tsx", import.meta.url), "utf8");

    expect(integration).toContain("Copy maintenance guide");
    expect(integration).toContain("Copy outreach contract");
    expect(integration).toContain("const integrationContract = internalAgentOutreachGuide();");
    expect(integration).toContain("const continuousGuide = continuousAgentHandoffGuide();");
    expect(integration).toContain("copied handoffs never include them");
    expect(delegations).toContain('setSecret({ value: response.key, copied: false })');
    expect(delegations).toContain("Copy now · shown once");
    expect(delegations).not.toContain("function CopyGrantHandoff");
    expect(panels).toContain("function CopyGrantHandoff");
    expect(panels).toContain("Copy handoff");
  });

  it("keeps grant and proposal view-state semantics in the extracted panels", () => {
    const delegations = readFileSync(new URL("../components/agent-delegation-manager.tsx", import.meta.url), "utf8");
    const panels = readFileSync(new URL("../components/agent-delegation-panels.tsx", import.meta.url), "utf8");

    for (const marker of [
      'loadState === "loading" && delegations.length === 0',
      "Loading grants",
      'loadState === "error" && delegations.length === 0',
      'label="Agent grants could not be loaded"',
      'loadState === "loaded" && delegations.length === 0',
      "No agent grants have been created.",
      'loadState === "error" && delegations.length > 0',
      'label="Agent grants could not be refreshed"',
      'loadState === "loading" && proposals.length === 0',
      "Loading proposals",
      'loadState === "error" && proposals.length === 0',
      'label="Agent proposals could not be loaded"',
      'loadState === "loaded" && proposals.length === 0',
      "No agent proposals are awaiting review.",
      'loadState === "error" && proposals.length > 0',
      'label="Agent proposals could not be refreshed"',
    ]) {
      expect(panels).toContain(marker);
      expect(delegations).not.toContain(marker);
    }
  });
});
