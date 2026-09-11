import { expect, it } from "vitest";
import { sourceLineRange } from "../lib/source-line";

it("selects the exact requested line without interpreting Markdown or exposing source in a URL", () => {
  expect(sourceLineRange("---\nname: 😀\n---\n# About", 2)).toEqual({ start: 4, end: 12 });
  expect(sourceLineRange("a\nb", 2)).toEqual({ start: 2, end: 3 });
  for (const line of [0, -1, 1.5, Infinity, 99]) expect(sourceLineRange("a\nb", line)).toBeNull();
});

it("supports blank final lines, CRLF source, and large invalid line requests", () => {
  expect(sourceLineRange("a\n", 2)).toEqual({ start: 2, end: 2 });
  expect(sourceLineRange("a\r\nb", 2)).toEqual({ start: 3, end: 4 });
  expect(sourceLineRange("a", Number.MAX_SAFE_INTEGER)).toBeNull();
});
