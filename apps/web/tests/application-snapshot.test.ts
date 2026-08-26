import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  ApplicationSnapshotIntegrityError,
  presentApplicationSnapshotError,
} from "@/lib/recruitment-api";
import { ApiRequestError } from "@/lib/api";

const source = readFileSync(
  new URL("../components/application-snapshot.tsx", import.meta.url),
  "utf8",
);

describe("employer application snapshot control", () => {
  it("requires explicit human confirmation before the purpose-bound snapshot request", () => {
    const confirmation = source.indexOf(
      "Open this immutable applicant-selected Markdown snapshot solely for this job review?",
    );
    const request = source.indexOf("getEmployerApplicationSnapshot(");

    expect(confirmation).toBeGreaterThan(-1);
    expect(request).toBeGreaterThan(confirmation);
  });

  it("keeps snapshot work subject-bound and verifies before any local .md download", () => {
    const verification = source.indexOf("verifyApplicationSnapshotMarkdown(markdown");
    const blob = source.indexOf("URL.createObjectURL(");

    expect(source).toContain("isCurrentRequest(requestId, requestSubject)");
    expect(source).toContain("requestRef.current += 1");
    expect(verification).toBeGreaterThan(-1);
    expect(blob).toBeGreaterThan(verification);
    expect(source).toContain('anchor.download = `connectmd-${job.slug}-${snapshot.snapshotKind}-snapshot.md`');
    expect(source).toContain("URL.revokeObjectURL(objectUrl)");
    expect(source).not.toContain("href={snapshot.markdownUrl}");
  });

  it("describes unauthorized, withdrawn, expired, missing, and tampered snapshots without rendering them", () => {
    expect(presentApplicationSnapshotError(new ApiRequestError("forbidden", 403, "unauthorized"))).toContain("not authorized");
    expect(presentApplicationSnapshotError(new ApiRequestError("withdrawn application", 404, "not_found"))).toContain("withdrawn");
    expect(presentApplicationSnapshotError(new ApiRequestError("gone", 410, "not_found"))).toContain("expired");
    expect(presentApplicationSnapshotError(new ApiRequestError("not found", 404, "not_found"))).toContain("missing");
    expect(presentApplicationSnapshotError(new ApplicationSnapshotIntegrityError())).toContain("integrity check");
  });
});
