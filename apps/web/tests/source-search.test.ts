import { describe, expect, it } from "vitest";
import { findSourceMatches, replaceSourceMatches } from "../lib/source-search";

describe("literal source editing", () => {
  it("finds exact nonoverlapping UTF-16 selections including punctuation and emoji", () => {
    expect(findSourceMatches("😀 a.b A.b a.b", "a.b").matches).toEqual([3, 11]);
    expect(findSourceMatches("aaa", "aa").matches).toEqual([0]);
    expect(findSourceMatches("abc", "").matches).toEqual([]);
  });
  it("replaces literal dollar text without regex replacement interpolation", () => {
    expect(replaceSourceMatches("a.b a.b", "a.b", "$&", "all")).toBe("$& $&");
    expect(replaceSourceMatches("a.b a.b", "a.b", "x", 1)).toBe("a.b x");
  });
  it("rejects incomplete and oversized replacements without a partial result", () => {
    expect(findSourceMatches("a".repeat(1001), "a").limited).toBe(true);
    expect(() => replaceSourceMatches("a".repeat(1001), "a", "b", "all")).toThrow("1,000");
    expect(() => replaceSourceMatches("a".repeat(500), "a", "é".repeat(256), "all")).toThrow("128 KiB");
    expect(() => replaceSourceMatches("abc", "a", "x", 8)).toThrow("match");
  });
});
