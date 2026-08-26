import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { loadApplicationDocumentInventory } from "@/lib/application-document-inventory";
import { nextApplicationSubmissionAttempt, shouldRetainApplicationSubmissionAttempt } from "@/components/job-application-panel";
import { ApiRequestError } from "@/lib/api";
import type { ApplicationDocument } from "@/lib/recruitment-api";

const publicProfile: ApplicationDocument = {
  id: "profile-1",
  kind: "profile",
  identifier: "ada-lovelace",
  version: 3,
  visibility: "public",
};

const privateResume: ApplicationDocument = {
  id: "resume-1",
  kind: "resume",
  identifier: "private-resume",
  version: 1,
  visibility: "private",
};

const presentError = (error: unknown) =>
  error instanceof Error ? error.message : "Document inventory unavailable.";

describe("job application document inventory", () => {
  it("retains one application key after a lost acknowledgement, then rotates it only when intent changes", () => {
    const intent = {
      organizationSlug: "acme",
      jobSlug: "product-lead",
      message: "I can help.",
      snapshotKind: "profile" as const,
      snapshotIdentifier: "ada",
    };
    const first = nextApplicationSubmissionAttempt(null, intent, () => "application-key-1");
    const replay = nextApplicationSubmissionAttempt(first, intent, () => "application-key-2");
    const changed = nextApplicationSubmissionAttempt(first, { ...intent, message: "Updated intent." }, () => "application-key-2");

    expect(replay).toBe(first);
    expect(changed).toEqual({ fingerprint: JSON.stringify({ ...intent, message: "Updated intent." }), idempotencyKey: "application-key-2" });
    expect(shouldRetainApplicationSubmissionAttempt(new ApiRequestError("Confirmation lost.", undefined, "request"))).toBe(true);
    expect(shouldRetainApplicationSubmissionAttempt(new ApiRequestError("Server failure.", 503, "server"))).toBe(true);
    expect(shouldRetainApplicationSubmissionAttempt(new ApiRequestError("Offline.", undefined, "offline"))).toBe(false);
    expect(shouldRetainApplicationSubmissionAttempt(new ApiRequestError("Rejected.", 422, "request"))).toBe(false);
  });

  it("returns an error state when document retrieval rejects", async () => {
    const result = await loadApplicationDocumentInventory(
      async () => {
        throw new Error("Could not load public documents.");
      },
      presentError,
    );

    expect(result).toEqual({
      status: "error",
      documents: [],
      selected: "",
      error: "Could not load public documents.",
    });
  });

  it("returns a genuine ready-empty state only after a successful load", async () => {
    const result = await loadApplicationDocumentInventory(
      async () => [privateResume],
      presentError,
    );

    expect(result).toEqual({
      status: "ready",
      documents: [],
      selected: "",
    });
  });

  it("recovers from rejection on retry with a selected public document", async () => {
    let attempts = 0;
    const load = async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("Temporary failure.");
      return [privateResume, publicProfile];
    };

    const failed = await loadApplicationDocumentInventory(load, presentError);
    const recovered = await loadApplicationDocumentInventory(load, presentError);

    expect(failed.status).toBe("error");
    expect(recovered).toEqual({
      status: "ready",
      documents: [publicProfile],
      selected: "profile:ada-lovelace",
    });
  });

  it("wires the accessible retry without weakening subject guards", () => {
    const source = readFileSync(
      new URL("../components/job-application-panel.tsx", import.meta.url),
      "utf8",
    );

    expect(source).toContain('role="alert"');
    expect(source).toContain("Retry public documents");
    expect(source).toContain("onClick={() => void loadDocuments()}");
    expect(source).toContain("listApplicationDocuments(getToken, isCurrent)");
    expect(source).toContain("key={`${subject}:${job.id}`}");
    expect(source).toContain("inventoryRequestRef.current === requestId");
    expect(source).toContain(
      "authSubjectIsCurrent(subjectRef.current, requestSubject)",
    );
  });
});
