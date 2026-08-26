import { describe, expect, it } from "vitest";

import { appendCursorPage } from "../lib/cursor-page";

describe("cursor page merging", () => {
  it("deduplicates items while preserving the existing and incoming order", () => {
    expect(
      appendCursorPage(
        [{ id: "known" }],
        { items: [{ id: "known" }, { id: "older" }, { id: "newer" }], nextCursor: "next" },
        "current",
      ),
    ).toEqual({
      items: [{ id: "known" }, { id: "older" }, { id: "newer" }],
      nextCursor: "next",
      cursorDidNotProgress: false,
    });
  });

  it.each(["current", "delivered"])('stops when the next cursor is %s', (reason) => {
    const delivered = reason === "delivered" ? new Set(["same"]) : new Set<string>();
    expect(appendCursorPage([], { items: [], nextCursor: "same" }, reason === "current" ? "same" : "current", delivered)).toEqual({
      items: [],
      nextCursor: null,
      cursorDidNotProgress: true,
    });
  });
});
