import { describe, expect, it } from "vitest";
import { reviewPrivacy } from "../lib/privacy-review";

describe("local sharing review", () => {
  it("reports locations and categories without copying sensitive matches", () => {
    const review = reviewPrivacy("Email: person@example.invalid\napi_key: synthetic-example-only\nhttps://example.invalid/?token=synthetic\nC:\\Users\\Example\\private.md\n![photo](https://example.invalid/photo.png)");
    expect(review.findings.map(({ code, line }) => [code, line])).toEqual([["email", 1], ["credential", 2], ["url-secret", 3], ["local-path", 4], ["image", 5]]);
    expect(JSON.stringify(review)).not.toContain("synthetic");
    expect(JSON.stringify(review)).not.toContain("person@");
  });
  it("checks URL userinfo and HTTP but leaves ordinary HTTPS alone", () => {
    expect(reviewPrivacy("http://user:example@example.invalid").findings.map(({ code }) => code)).toEqual(["url-secret", "http"]);
    expect(reviewPrivacy("https://example.invalid/work").findings).toEqual([]);
  });
  it("bounds oversized inputs and repeated findings", () => {
    expect(reviewPrivacy("a".repeat(131073))).toEqual({ findings: [], limited: true });
    const review = reviewPrivacy("a@example.invalid\n".repeat(100));
    expect(review.findings).toHaveLength(20);
    expect(review.limited).toBe(true);
  });
});
