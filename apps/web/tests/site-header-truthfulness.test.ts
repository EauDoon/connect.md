import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  current: { configured: true, isLoaded: true, isSignedIn: true }
}));

vi.mock("@/components/auth-provider", () => ({ useConnectmdAuth: () => auth.current }));
vi.mock("@clerk/nextjs", () => ({
  SignInButton: ({ children }: { children: ReactNode }) => children,
  UserButton: () => null
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import { SiteHeader } from "../components/site-header";

afterEach(() => {
  auth.current = { configured: true, isLoaded: true, isSignedIn: true };
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function renderHeader(privateWorkspacesEnabled: boolean): string {
  vi.stubGlobal("React", { createElement });
  return renderToStaticMarkup(createElement(SiteHeader, { privateWorkspacesEnabled }));
}

describe("global header deployment truthfulness", () => {
  it("keeps private destinations and sign-in controls hidden when server auth is incomplete", () => {
    vi.stubEnv("NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED", "true");
    const markup = renderHeader(false);

    expect(markup).toContain('href="/agent-directory"');
    for (const href of ["/network", "/agents", "/workspace", "/account"]) {
      expect(markup).not.toContain(`href="${href}"`);
    }
    expect(markup).not.toContain("Sign in");
  });

  it("shows configured private destinations to a signed-in account", () => {
    vi.stubEnv("NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED", "true");
    const markup = renderHeader(true);

    for (const href of ["/network", "/agents", "/workspace", "/account"]) {
      expect(markup).toContain(`href="${href}"`);
    }
    expect(markup).not.toContain('href="/agent-directory"');
  });

  it("fails closed when the client auth provider is also unconfigured", () => {
    auth.current = { configured: false, isLoaded: true, isSignedIn: false };
    const markup = renderHeader(true);

    expect(markup).toContain('href="/agent-directory"');
    expect(markup).not.toContain('href="/network"');
    expect(markup).not.toContain('href="/workspace"');
  });

  it("binds the server-only environment decision into the root header", () => {
    const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");

    expect(layout).toContain(
      'import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";'
    );
    expect(layout).toContain('export const dynamic = "force-dynamic";');
    expect(layout).toContain(
      "const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();"
    );
    expect(layout).toContain(
      "<SiteHeader privateWorkspacesEnabled={privateWorkspacesEnabled} />"
    );
  });
});
