import * as React from "react";
import { createElement } from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import PublicAgentIdentityLoading from "../app/agents/[handle]/loading";
import PublicPostLoading from "../app/posts/[id]/loading";
import { PublicDocumentPage } from "../components/public-document-page";
import type { DocumentResponse } from "../lib/api";
import { privateRouteAuthConfigured } from "../lib/private-route-auth";

vi.stubGlobal("React", React);

function source(relativePath: string) {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

describe("public dynamic detail UX", () => {
  it("renders truthful noninteractive loading boundaries", () => {
    for (const [Component, label] of [
      [PublicAgentIdentityLoading, "Loading the public Agent Identity."],
      [PublicPostLoading, "Loading the public professional post."],
    ] as const) {
      const markup = renderToStaticMarkup(createElement(Component));
      expect(markup).toContain('aria-busy="true"');
      expect(markup).toContain('aria-live="polite"');
      expect(markup).toContain(label);
      expect(markup).toContain("motion-safe:animate-pulse");
      expect(markup).not.toMatch(/<(a|button|input|select|textarea)\b/u);
    }
  });

  it("contains long public detail values without changing their content", () => {
    const agent = source("../app/agents/[handle]/page.tsx");
    const job = source("../components/job-directory.tsx");
    const organization = source("../components/organization-directory.tsx");
    const archive = source("../components/profile-post-archive.tsx");

    expect(agent).toContain('<div className="min-w-0"><h1 className="font-display break-anywhere');
    expect(agent).toContain('className="mt-7 max-w-3xl break-anywhere whitespace-pre-wrap');
    expect(job).toContain("max-w-5xl min-w-0");
    expect(job).toContain('<h1 className="mt-5 break-anywhere font-display');
    expect(job).toContain('className="mt-4 break-anywhere whitespace-pre-wrap');
    expect(organization).toContain("max-w-7xl min-w-0");
    expect(organization).toContain('<h1 className="mt-5 break-anywhere font-display');
    expect(organization).toContain('className="min-w-0"><p className="eyebrow"');
    expect(archive).toContain('<div className="min-w-0"><p className="eyebrow"');
    expect(archive).toContain('<h1 className="mt-3 break-anywhere font-display');
  });

  it("keeps essential public detail navigation at the minimum touch size", () => {
    const document = source("../components/public-document-page.tsx");
    const agent = source("../app/agents/[handle]/page.tsx");

    expect(document).toContain('href="/search" className="inline-flex min-h-11 items-center');
    expect(document).toContain('type="text/markdown" className="inline-flex min-h-11 items-center');
    expect(agent).toContain('className="mt-2 inline-flex min-h-11 items-center break-anywhere');
  });

  it("keeps each recruiting navigation link at the minimum touch size", () => {
    const jobs = source("../components/job-directory.tsx");
    const organization = source("../components/organization-directory.tsx");
    const links = [
      [jobs, "/organizations", "Service-gated organizations"],
      [jobs, "/applications", "My applications"],
      [jobs, "/employer", "Employer workspace"],
      [organization, "/jobs", "All published jobs"],
    ] as const;

    for (const [markup, href, label] of links) {
      const escapedHref = href.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
      const escapedLabel = label.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
      const matches = [...markup.matchAll(new RegExp(`<Link href="${escapedHref}" className="([^"]+)">${escapedLabel}</Link>`, "gu"))];
      expect(matches, `${href} ${label} must be asserted at its exact link`).toHaveLength(1);
      expect(matches[0]?.[1]?.split(/\s+/u)).toEqual(expect.arrayContaining(["inline-flex", "min-h-11", "items-center"]));
    }
  });

  it.each([
    [undefined, undefined, false],
    ["publishable", undefined, false],
    [undefined, "secret", false],
    ["publishable", "secret", true],
  ] as const)("advertises private public-detail actions only with complete server auth configuration", (publishableKey, secretKey, expected) => {
    expect(privateRouteAuthConfigured(publishableKey, secretKey)).toBe(expected);

    const document = source("../components/public-document-page.tsx");
    const profilePage = source("../app/p/[handle]/page.tsx");
    const resumePage = source("../app/r/[slug]/page.tsx");
    const agentPage = source("../app/agents/[handle]/page.tsx");

    expect(document).toContain("privateWorkspacesEnabled && linkedContactIntent");
    expect(document).toContain('privateWorkspacesEnabled ? <><ProfilePostControls handle={document.identifier} /><ProfileConnectControl handle={document.identifier} /></> : <p');
    expect(profilePage).toContain("privateWorkspacesEnabled={privateWorkspaceConfiguredFromEnvironment()}");
    expect(resumePage).toContain("privateWorkspacesEnabled={privateWorkspaceConfiguredFromEnvironment()}");
    expect(agentPage).toContain("const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();");
    expect(agentPage).toContain("const contactIntent = privateWorkspacesEnabled ? buildInboxContactReturnPath(identity.profileHandle) : null;");
    expect(agentPage).toContain("Private contact controls are unavailable in this deployment.");
  });

  it("keeps the profile visible while distinguishing an unavailable Agent Identity lookup from an empty result", () => {
    const document: DocumentResponse = {
      id: "profile-1",
      kind: "profile",
      identifier: "ari-chen",
      visibility: "public",
      version: 1,
      etag: "profile-etag-v1",
      updated_at: "2026-08-26T00:00:00Z",
      markdown: `---\nschema: connect.md/profile\nschema_version: 2\nid: profile-1\nhandle: ari-chen\nversion: 1\nupdated_at: '2026-08-26T00:00:00Z'\nname: Ari Chen\nvisibility: public\n---\n# Ari Chen\n`,
      markdown_url: "/v1/profiles/ari-chen.md",
    };

    const unavailableMarkup = renderToStaticMarkup(createElement(PublicDocumentPage, { document, agentIdentities: [], agentIdentitiesUnavailable: true, privateWorkspacesEnabled: true }));
    expect(unavailableMarkup).toContain("Ari Chen");
    expect(unavailableMarkup).toContain("View canonical Markdown");
    expect(unavailableMarkup).toContain("Published Agent Identities unavailable");
    expect(unavailableMarkup).toContain("current Agent Identity status cannot be confirmed");
    expect(unavailableMarkup).toContain("Open Agent Directory");
    expect(unavailableMarkup).not.toContain("Prepare private contact request");

    const emptyMarkup = renderToStaticMarkup(createElement(PublicDocumentPage, { document, agentIdentities: [], agentIdentitiesUnavailable: false, privateWorkspacesEnabled: true }));
    expect(emptyMarkup).not.toContain("Published Agent Identities unavailable");
  });
});
