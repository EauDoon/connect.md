import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SourceSearch } from "../components/source-search";
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
    expect(() => replaceSourceMatches("😀".repeat(32768), "\ud83d", "AB", 0)).toThrow("128 KiB");
  });
});

it("supports optional case folding and Unicode whole-word boundaries", () => {
  expect(findSourceMatches("Cat cat scatter cat_ caté", "cat", { matchCase: false, wholeWord: true }).matches).toEqual([0, 4]);
  expect(replaceSourceMatches("CAT cat", "cat", "$&", "all", { matchCase: false })).toBe("$& $&");
  expect(findSourceMatches("İx a", "a", { matchCase: false }).matches).toEqual([3]);
});

it("body-only replacement preserves metadata and fails closed on unclosed frontmatter", () => {
  const source = "---\nname: Cat\n---\nCat";
  expect(replaceSourceMatches(source, "Cat", "Dog", "all", { bodyOnly: true })).toBe("---\nname: Cat\n---\nDog");
  expect(findSourceMatches("---\nname: Cat", "Cat", { bodyOnly: true }).matches).toEqual([]);
  expect(findSourceMatches("Cat", "Cat", { bodyOnly: true }).matches).toEqual([0]);
});

it("offers a review step before changing source", () => {
  const html = renderToStaticMarkup(React.createElement(SourceSearch, { markdown: "Cat", onChange: () => {}, onSelect: () => {} }));
  expect(html).toContain("Review all replacements");
  expect(html).toContain("Review match replacement");
});
