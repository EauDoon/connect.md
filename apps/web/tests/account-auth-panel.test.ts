import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

vi.stubGlobal("React", React);
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

const { AccountAuthPanel, HANDLE_INPUT_PATTERN, nextAuthMode } = await import("@/components/network/account-auth-panel");
const { validateHandle } = await import("@/lib/network/identity");

describe("account auth tabs", () => {
  it("moves between tabs with wrapping arrows and Home and End", () => {
    expect(nextAuthMode("register", "ArrowRight")).toBe("login");
    expect(nextAuthMode("login", "ArrowRight")).toBe("register");
    expect(nextAuthMode("register", "ArrowLeft")).toBe("login");
    expect(nextAuthMode("login", "ArrowLeft")).toBe("register");
    expect(nextAuthMode("login", "Home")).toBe("register");
    expect(nextAuthMode("register", "End")).toBe("login");
    expect(nextAuthMode("register", "Enter")).toBeNull();
    expect(nextAuthMode("register", "a")).toBeNull();
  });

  it("renders a complete WAI-ARIA tab set with described fields", () => {
    const markup = renderToStaticMarkup(createElement(AccountAuthPanel));

    expect(markup).toContain('role="tablist"');
    expect(markup.match(/role="tab"/gu)).toHaveLength(2);
    expect(markup.match(/aria-controls="account-auth-form"/gu)).toHaveLength(2);
    expect(markup).toContain('id="account-tab-register"');
    expect(markup).toContain('id="account-tab-login"');
    expect(markup).toMatch(/id="account-tab-register"[^>]*tabindex="0"|tabindex="0"[^>]*id="account-tab-register"/u);
    expect(markup).toMatch(/id="account-tab-login"[^>]*tabindex="-1"|tabindex="-1"[^>]*id="account-tab-login"/u);
    expect(markup).toContain('role="tabpanel" id="account-auth-form" aria-labelledby="account-tab-register"');

    for (const [, target] of markup.matchAll(/aria-describedby="([^"]+)"/gu)) {
      expect(markup, target).toContain(`id="${target}"`);
    }
    expect(markup).toContain('aria-describedby="account-handle-help"');
    expect(markup).toContain('aria-describedby="account-password-help"');
    expect(markup).toContain('autoCapitalize="none"');
    expect(markup).toContain('spellCheck="false"');
    expect(markup).toContain("single hyphens");

    const pattern = /pattern="([^"]+)"/u.exec(markup)?.[1];
    expect(pattern).toBe(HANDLE_INPUT_PATTERN);
    expect(pattern).not.toMatch(/A-Z/u);
  });

  it("announces errors once, through the alert alone", () => {
    const source = readFileSync(new URL("../components/network/account-auth-panel.tsx", import.meta.url), "utf8");
    expect(source).toContain('role="alert"');
    expect(source).not.toContain("aria-live");
  });

  it("uses a handle pattern browsers can compile and that matches the server rule", () => {
    // Browsers compile `pattern` as ^(?:pattern)$ with the v flag. A pattern
    // that fails to compile there is silently ignored, which is how the old
    // unescaped [a-zA-Z0-9-] class let uppercase handles through.
    const browserPattern = new RegExp(`^(?:${HANDLE_INPUT_PATTERN})$`, "v");
    const corpus = [
      "ada", "ada-lovelace", "a1b", "0x0", "a".repeat(30), "a-b-c", "abc123",
      "Ada", "ADA-LOVELACE", "ab", "a".repeat(31), "-ada", "ada-", "a--b", "ada_lovelace", "ada lovelace", "ada.l",
    ];
    for (const handle of corpus) {
      expect(browserPattern.test(handle), handle).toBe(validateHandle(handle).ok);
    }
  });
});
