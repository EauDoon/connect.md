import { describe, expect, it } from "vitest";

import { inboxLoadNotice } from "@/components/network/inbox-panel";

describe("inbox load notice", () => {
  it("stays silent when both reads succeed, so action confirmations survive a reload", () => {
    expect(inboxLoadNotice(200, 200)).toBeNull();
  });

  it("asks the owner to sign in again when the session has ended", () => {
    expect(inboxLoadNotice(401, 401)).toBe("Your session ended. Sign in again.");
    expect(inboxLoadNotice(200, 401)).toBe("Your session ended. Sign in again.");
    expect(inboxLoadNotice(401, 503)).toBe("Your session ended. Sign in again.");
  });

  it("never shows a failed load as an empty inbox", () => {
    expect(inboxLoadNotice(503, 200)).toBe("Could not load your inbox. Try again.");
    expect(inboxLoadNotice(200, 500)).toBe("Could not load your inbox. Try again.");
    expect(inboxLoadNotice(429, 429)).toBe("Could not load your inbox. Try again.");
  });
});
