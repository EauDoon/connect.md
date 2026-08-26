import React, { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import AgentDirectoryLoading from "../app/agent-directory/loading";
import PublicAgentIdentityLoading from "../app/agents/[handle]/loading";
import LoadingDiscover from "../app/discover/loading";
import LoadingJobs from "../app/jobs/loading";
import LoadingOrganizations from "../app/organizations/loading";
import PublicProfileLoading from "../app/p/[handle]/loading";
import PublicPostLoading from "../app/posts/[id]/loading";
import PublicResumeLoading from "../app/r/[slug]/loading";
import RepresentativesLoading from "../app/representatives/loading";
import SearchLoading from "../app/search/loading";

vi.stubGlobal("React", React);

const boundaries: ReadonlyArray<[ComponentType, string]> = [
  [LoadingDiscover, "Loading public discovery."],
  [SearchLoading, "Loading directory results."],
  [AgentDirectoryLoading, "Loading published agent identities."],
  [RepresentativesLoading, "Loading public representative declarations."],
  [LoadingOrganizations, "Loading service-gated organizations."],
  [LoadingJobs, "Loading service-gated jobs."],
  [PublicProfileLoading, "Loading public profile."],
  [PublicResumeLoading, "Loading public resume."],
  [PublicAgentIdentityLoading, "Loading the public Agent Identity."],
  [PublicPostLoading, "Loading the public professional post."],
];

describe("public route loading boundaries", () => {
  it.each(boundaries)("announces %s without exposing its visual skeleton", (Component, label) => {
    const markup = renderToStaticMarkup(createElement(Component));

    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain(`aria-label="${label}"`);
    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('aria-atomic="true"');
    expect(markup).toContain(`class="sr-only">${label}</span>`);
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).toContain("motion-safe:animate-pulse");
    expect(markup).not.toMatch(/<(a|button|input|select|textarea)\b/u);
  });
});
