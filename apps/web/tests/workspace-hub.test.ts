import { createElement, type ReactNode } from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ useConnectmdAuth: vi.fn() }));

vi.mock("@/components/auth-provider", () => ({ useConnectmdAuth: auth.useConnectmdAuth }));
vi.mock("@clerk/nextjs", () => ({ SignInButton: ({ children }: { children: ReactNode }) => children }));

import { WorkspaceHub } from "../components/workspace-hub";

const baseAuth = { subject: null, getToken: async () => null };

afterEach(() => {
  auth.useConnectmdAuth.mockReset();
  vi.unstubAllGlobals();
});

function renderWorkspace() {
  vi.stubGlobal("React", { createElement });
  return renderToStaticMarkup(createElement(WorkspaceHub));
}

describe("private workspace hub", () => {
  it("keeps public primary navigation separate and exposes every existing private workspace destination after sign-in", () => {
    auth.useConnectmdAuth.mockReturnValue({ ...baseAuth, configured: true, isLoaded: true, isSignedIn: true, subject: "user_123" });
    const markup = renderWorkspace();

    expect(markup).toContain('aria-label="Private workspace navigation"');
    for (const href of ["/human", "/network", "/inbox", "/feed", "/applications", "/employer", "/agents", "/moderation"]) expect(markup).toContain(`href="${href}"`);
    expect(markup).toContain("Access remains destination-specific");
    expect(markup).not.toContain("No applications yet");
    expect(markup).not.toContain("No notifications");
  });

  it("shows a safe sign-in orientation without private destination links", () => {
    auth.useConnectmdAuth.mockReturnValue({ ...baseAuth, configured: true, isLoaded: true, isSignedIn: false });
    const markup = renderWorkspace();

    expect(markup).toContain("Sign in to open your workspace");
    expect(markup).toContain("does not send a request, publish a document, or replay an earlier action");
    expect(markup).toContain('href="/discover"');
    for (const href of ["/network", "/inbox", "/feed", "/applications", "/employer", "/agents", "/moderation"]) expect(markup).not.toContain(`href="${href}"`);
  });

  it("is a link-only orientation layer and stays outside standalone navigation", () => {
    const hub = readFileSync(new URL("../components/workspace-hub.tsx", import.meta.url), "utf8");
    const header = readFileSync(new URL("../components/site-header.tsx", import.meta.url), "utf8");

    expect(hub).not.toMatch(/\b(fetch|apiRequest|getToken|useEffect|useState|localStorage|sessionStorage)\b/u);
    expect(header).not.toContain('href="/workspace"');
    expect(header).not.toContain("useConnectmdAuth");
    expect(header).toContain("PUBLIC_PRIMARY_NAVIGATION");
  });
});
