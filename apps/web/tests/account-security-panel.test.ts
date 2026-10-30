import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
const { AccountSecurityPanel, confirmMismatch, otherSessionsLine } = await import("../components/network/account-security-panel");

describe("account security panel", () => {
  it("labels every password field and explains what a change signs out", () => {
    const markup = renderToStaticMarkup(createElement(AccountSecurityPanel, { otherSessions: 2 }));
    for (const [id, label] of [["security-current-password", "Current password"], ["security-new-password", "New password"], ["security-confirm-password", "Confirm new password"]]) {
      expect(markup).toContain(`<label for="${id}" class="mb-1 block text-sm font-medium text-white">${label}</label>`);
      expect(markup).toContain(`id="${id}" type="password"`);
    }
    expect(markup.match(/="current-password"/gu)).toHaveLength(1);
    expect(markup.match(/="new-password"/gu)).toHaveLength(2);
    expect(markup).toContain("Changing your password signs out every other browser.");
    expect(markup).toContain("Agent grants are separate credentials");
    expect(markup).toContain("Signed in on 2 other sessions.");
    expect(markup).toContain("Sign out other sessions");
    expect(markup).not.toContain('role="alert"');
  });

  it("describes the session count in plain words", () => {
    expect(otherSessionsLine(0)).toBe("No other browser is signed in to this account.");
    expect(otherSessionsLine(1)).toBe("Signed in on 1 other session.");
    expect(otherSessionsLine(5)).toBe("Signed in on 5 other sessions.");
  });

  it("flags a confirmation only once it is typed and differs", () => {
    expect(confirmMismatch("NewPassword456", "")).toBe(false);
    expect(confirmMismatch("NewPassword456", "NewPassword456")).toBe(false);
    expect(confirmMismatch("NewPassword456", "NewPassword45")).toBe(true);
  });
});
