import { createElement } from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AgentDirectoryLoading from "../app/agent-directory/loading";
import { AgentDirectory } from "../components/agent-directory";
import { agentDirectoryFiltersFromParams, agentDirectoryHref } from "../lib/agent-directory";

describe("public Agent Directory", () => {
  it("defines the responsive containment used by user-controlled card text", () => {
    const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
    expect(css).toContain(".break-anywhere { overflow-wrap: anywhere; }");
  });

  it("announces a truthful, noninteractive loading boundary", () => {
    const markup = renderToStaticMarkup(createElement(AgentDirectoryLoading));

    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain("Loading published agent identities.");
    expect(markup).not.toMatch(/<(a|button|input|select|textarea)\b/u);
    expect(markup).not.toContain("Internal contact request");
    expect(markup).not.toContain("Prepare contact request");
  });

  it("preserves signed pagination filters and renders a successful cursor page", () => {
    const filters = agentDirectoryFiltersFromParams(new URLSearchParams("q=payments&profile_handle=ari-chen&cursor=signed-current"));
    const markup = renderToStaticMarkup(createElement(AgentDirectory, {
      filters,
      response: { identities: [{ handle: "ari-agent", displayName: "Ari's agent", description: "Handles mediated contact.", profileHandle: "ari-chen", capabilities: ["internal_contact_request"] }], nextCursor: "signed-next" },
      error: null
    }));

    expect(agentDirectoryHref(filters, "signed-next")).toBe("/agent-directory?q=payments&profile_handle=ari-chen&cursor=signed-next");
    expect(markup).toContain("Ari&#x27;s agent");
    expect(markup).toContain("Next results");
    expect(markup).not.toContain("Directory results are unavailable");
    expect(markup).toContain('href="/agents/ari-agent"');
    expect(markup).toContain('href="/p/ari-chen"');
    expect(markup).toContain('href="/inbox?profile=ari-chen"');
    expect(markup).not.toContain('href="/inbox?profile=ari-agent"');
    expect(markup.match(/break-anywhere/g)).toHaveLength(3);
  });

  it("distinguishes an absent cursor from invalid supplied pagination", () => {
    expect(agentDirectoryFiltersFromParams(new URLSearchParams("q=payments"))).toMatchObject({ cursor: null, invalidMessage: null });

    for (const query of [
      "cursor=",
      "cursor=%20%20",
      "cursor=first&cursor=second",
      `cursor=${"x".repeat(501)}`
    ]) {
      expect(agentDirectoryFiltersFromParams(new URLSearchParams(query))).toMatchObject({
        cursor: null,
        invalidMessage: "This pagination link is not valid. Start the search again."
      });
    }
  });

  it("keeps agent entity failures authoritative and emits structured data only from a live public read", () => {
    const detailPage = readFileSync(new URL("../app/agents/[handle]/page.tsx", import.meta.url), "utf8");
    const directoryPage = readFileSync(new URL("../app/agent-directory/page.tsx", import.meta.url), "utf8");
    expect(detailPage).toContain('error instanceof ApiRequestError && error.code === "not_found"');
    expect(detailPage).toContain("throw error");
    expect(detailPage).toContain("agentIdentityJsonLd(identity)");
    expect(detailPage).toContain("buildInboxContactReturnPath(identity.profileHandle)");
    expect(detailPage).not.toContain("buildInboxContactReturnPath(identity.handle)");
    expect(directoryPage).toContain("agentDirectoryJsonLd(response)");
  });
});
