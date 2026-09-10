import { describe, expect, it } from "vitest";
import { createCheckpoint, renameCheckpoint } from "../lib/draft-checkpoints";

describe("session checkpoints", () => {
  const draft = { kind: "profile" as const, markdown: "# An exact draft\n" };
  it("retains exact bytes and trims the display label", () => {
    expect(createCheckpoint([], draft, " First version ", 1)).toEqual({ ...draft, label: "First version", id: 1 });
  });
  it("bounds count and UTF-8 size without evicting existing versions", () => {
    const existing = Array.from({ length: 5 }, (_, id) => ({ ...draft, label: "saved", id }));
    expect(() => createCheckpoint(existing, draft, "sixth", 6)).toThrow("Five checkpoints");
    expect(existing).toHaveLength(5);
    expect(() => createCheckpoint([], { ...draft, markdown: "😀".repeat(32769) }, "large", 1)).toThrow("128 KiB");
  });
  it("rejects empty and overlong names", () => {
    for (const label of ["  ", "a".repeat(61)]) expect(() => createCheckpoint([], draft, label, 1)).toThrow("1 to 60");
  });
  it("requires distinguishable names for deliberate restoration", () => {
    const first = createCheckpoint([], draft, "First version", 1);
    expect(() => createCheckpoint([first], draft, " FIRST VERSION ", 2)).toThrow("distinct name");
  });
  it("renames without replacing content, ids, or neighboring checkpoints", () => {
    const first = createCheckpoint([], draft, "First", 1);
    const second = createCheckpoint([first], draft, "Second", 2);
    const renamed = renameCheckpoint([first, second], 1, " New name ");
    expect(renamed).toEqual([{ ...first, label: "New name" }, second]);
    expect(first.label).toBe("First");
    expect(() => renameCheckpoint([first, second], 1, "SECOND")).toThrow("distinct");
    expect(() => renameCheckpoint([first], 99, "Lost")).toThrow("available");
  });
});
