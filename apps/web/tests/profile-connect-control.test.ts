import { createElement } from "react";
import type { ButtonHTMLAttributes } from "react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/auth-provider", () => ({ useConnectmdAuth: () => ({ configured: true, isLoaded: true, isSignedIn: true, subject: "clerk-private-subject", getToken: async () => "token" }) }));
vi.mock("@/components/ui/button", async () => {
  const React = await import("react");
  return { Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => React.createElement("button", props, children) };
});

describe("public profile connection control", () => {
  it("offers only a human-gated private request with optional messaging and network management", async () => {
    const { ProfileConnectControl } = await import("../components/profile-connect-control");
    const markup = renderToStaticMarkup(createElement(ProfileConnectControl, { handle: "ari-chen" }));
    expect(markup).toContain("Request connection");
    expect(markup).toContain("Request messaging");
    expect(markup).toContain('href="/network"');
    expect(markup).not.toContain("clerk-private-subject");
    expect(markup).toContain("Nothing is added to a public graph.");
  });

  it("keeps the signed-out handoff on the canonical public profile and never replays the request", () => {
    const source = readFileSync(new URL("../components/profile-connect-control.tsx", import.meta.url), "utf8");
    expect(source).toContain('buildProfileActionReturnPath(handle, "connect")');
    expect(source).toContain("forceRedirectUrl={returnPath}");
    expect(source).toContain("signUpForceRedirectUrl={returnPath}");
    expect(source).toContain('key={`${subject}:${handle}`}');
    expect(source).toContain("isSubjectCurrent={() => authSubjectIsCurrent(subjectRef.current, subject)}");
    const signedOutBranch = source.slice(source.indexOf("if (!isSignedIn"), source.indexOf("function AuthenticatedProfileConnectControl"));
    expect(signedOutBranch).not.toContain("createConnectionRequest");
    expect(signedOutBranch).not.toContain("messagingRequested");
  });

  it("remounts private state at the subject boundary and guards stale settlement before the attempt ref", () => {
    const source = readFileSync(new URL("../components/profile-connect-control.tsx", import.meta.url), "utf8");
    expect(source).not.toContain("useEffect");
    const catchBody = source.slice(source.indexOf("} catch (error)"), source.indexOf("} finally", source.indexOf("} catch (error)")));
    expect(catchBody.indexOf("if (!requestIsCurrent()) return;")).toBeGreaterThanOrEqual(0);
    expect(catchBody.indexOf("if (!requestIsCurrent()) return;")).toBeLessThan(catchBody.indexOf("settleLogicalMutationAttempt"));
    expect(source).toContain("createConnectionRequest(handle, messagingRequested, getToken, requestIsCurrent, attempt.idempotencyKey)");
  });
});
