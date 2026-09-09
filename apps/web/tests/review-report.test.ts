import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { buildReviewReport } from "../lib/review-report";
import { profileStarter } from "../lib/markdown";

describe("local review receipt", () => {
  it("fingerprints exact UTF-8 source and excludes personal text", async () => {
    const source = profileStarter.replaceAll("Your Name", "Zoë Example") + "\nContact person@example.invalid\n";
    const report = await buildReviewReport(source, "profile");
    expect(report).toContain(createHash("sha256").update(source, "utf8").digest("hex"));
    expect(report).toContain("- Errors: 0");
    expect(report).toContain("An email address may be included");
    expect(report).not.toContain("Zoë");
    expect(report).not.toContain("person@example.invalid");
    const next = await buildReviewReport(source + "\n", "profile");
    expect(next).not.toEqual(report);
  });
  it("reports invalid schema honestly and bounds oversized work", async () => {
    expect(await buildReviewReport("# Incomplete\n", "profile")).not.toContain("- Errors: 0");
    await expect(buildReviewReport("x".repeat(131073), "profile")).rejects.toThrow("128 KiB");
  });
});
