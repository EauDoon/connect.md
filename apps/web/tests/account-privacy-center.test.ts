import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { accountPrivacyAuthBoundaryKey, saveSubjectBoundExport } from "../components/account-privacy-center";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("account privacy center", () => {
  it("keeps the lifecycle route private, typed, and explicit about receipt-scoped later states", () => {
    const center = readFileSync(new URL("../components/account-privacy-center.tsx", import.meta.url), "utf8");
    const page = readFileSync(new URL("../app/account/page.tsx", import.meta.url), "utf8");
    const robots = readFileSync(new URL("../app/robots.ts", import.meta.url), "utf8");
    expect(page).toContain("if (!accountLifecycleFeatureEnabled()) notFound()");
    expect(robots).toContain('"/account"');
    expect(center).toContain("Download account export");
    expect(center).toContain("Request account deletion");
    expect(center).toContain("Type {ACCOUNT_DELETION_INTENT} to confirm");
    expect(center).toContain("Lifecycle Receipt");
    expect(center).toContain("Check sanitized status");
    expect(center).toContain("I have saved this Lifecycle Receipt securely");
    expect(center).toContain("recoverWithReverification");
    expect(center).not.toContain("The current API exposes no secure lifecycle-status receipt");
    expect(center).toContain("Erasing, held, failed, and terminal");
    expect(center).not.toContain("Account fully erased");
  });

  it("uses Clerk reverification for export and both protected deletion actions without browser persistence", () => {
    const center = readFileSync(new URL("../components/account-privacy-center.tsx", import.meta.url), "utf8");
    expect(center.match(/useReverification\(/gu)).toHaveLength(4);
    expect(center).toContain("requestWithReverification");
    expect(center).toContain("confirmWithReverification");
    expect(center).toContain("recoverWithReverification");
    expect(center).not.toMatch(/localStorage|sessionStorage|URLSearchParams|console\./u);
  });

  it("remounts all receipt and deletion state at every exact auth boundary", () => {
    expect([
      accountPrivacyAuthBoundaryKey(false, true, false, null),
      accountPrivacyAuthBoundaryKey(true, false, true, "alpha"),
      accountPrivacyAuthBoundaryKey(true, true, true, "alpha"),
      accountPrivacyAuthBoundaryKey(true, true, true, "beta"),
      accountPrivacyAuthBoundaryKey(true, true, false, null),
    ]).toEqual(["unconfigured", "loading", "user:alpha", "user:beta", "signed-out"]);
    const center = readFileSync(new URL("../components/account-privacy-center.tsx", import.meta.url), "utf8");
    expect(center).toContain("<AccountPrivacyBoundary key={authBoundary}");
    expect(center.indexOf("function AccountPrivacyBoundary")).toBeLessThan(center.indexOf("const [deletion"));
    expect(center).not.toContain("The prior account session is unavailable");
    expect(center).not.toContain("statusReceipt={statusReceipt}");
    expect(center.match(/<LifecycleReceiptPanel key=\{deletion\.statusReceipt\} statusReceipt=\{deletion\.statusReceipt\} \/>/gu)).toHaveLength(2);
  });

  it("stops a stale export before URL creation or download after its blob resolves", async () => {
    let current = true;
    let resolveBlob!: (blob: Blob) => void;
    const blob = new Promise<Blob>((resolve) => { resolveBlob = resolve; });
    const response = new Response("", { headers: { "Content-Type": "application/x-ndjson" } });
    vi.spyOn(response, "blob").mockReturnValue(blob);
    const createObjectUrl = vi.spyOn(URL, "createObjectURL");

    const saving = saveSubjectBoundExport(response, () => current);
    current = false;
    resolveBlob(new Blob(["secret"]));

    await expect(saving).resolves.toBe(false);
    expect(createObjectUrl).not.toHaveBeenCalled();
  });

  it("cleans a URL and never clicks when the subject changes after URL creation", async () => {
    let guardCalls = 0;
    const response = new Response("secret", { headers: { "Content-Type": "application/x-ndjson" } });
    const createObjectUrl = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:account-export");
    const revokeObjectUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const append = vi.fn();
    vi.stubGlobal("document", { body: { append }, createElement: vi.fn() });

    await expect(saveSubjectBoundExport(response, () => ++guardCalls < 5)).resolves.toBe(false);
    expect(createObjectUrl).toHaveBeenCalledOnce();
    expect(append).not.toHaveBeenCalled();
    expect(revokeObjectUrl).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:account-export");
  });

  it("downloads once for the unchanged subject and always performs exact cleanup", async () => {
    const response = new Response("safe", { headers: { "Content-Type": "application/x-ndjson" } });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:account-export");
    const revokeObjectUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.fn();
    const remove = vi.fn();
    const anchor = { href: "", download: "", style: { display: "" }, click, remove };
    const append = vi.fn();
    vi.stubGlobal("document", { body: { append }, createElement: vi.fn(() => anchor) });

    await expect(saveSubjectBoundExport(response, () => true)).resolves.toBe(true);
    expect(click).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledOnce();
    expect(append).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:account-export");
  });

  it("consumes the typed first or replayed confirmation once and reaches the accepted phase", () => {
    const center = readFileSync(new URL("../components/account-privacy-center.tsx", import.meta.url), "utf8");
    expect(center).toContain("const confirmed = await confirmWithReverification(requestDeletionId, attempt.idempotencyKey);");
    expect(center).toContain('phase: "confirmation_accepted"');
    expect(center).not.toContain("parseDeletionConfirmation(await confirmWithReverification");
  });

  it("owns one guarded confirmation attempt and retains ambiguous offline/server outcomes", () => {
    const center = readFileSync(new URL("../components/account-privacy-center.tsx", import.meta.url), "utf8");
    expect(center).toContain("const claim = claimLogicalMutation(confirmMutationClaimSlotRef.current);");
    expect(center).toContain("const attempt = beginLogicalMutationAttempt(confirmAttemptRef.current, requestSubject, { operation: \"confirm-account-deletion\", deletionId: requestDeletionId, intent: ACCOUNT_DELETION_INTENT });");
    expect(center).toContain("confirmAttemptRef.current = settleConfirmationAttempt(attempt, caught);");
    expect(center).toContain("error instanceof ApiRequestError && error.code === \"offline\" ? attempt : null");
    expect(center).toContain("if (busy !== null || !deletion");
    expect(center).toContain("deletionRef.current?.id === requestDeletionId");
    expect(center).not.toContain("confirmAccountDeletion(requestDeletionId, getToken, isSubjectCurrent)");
    expect(center).toContain("exportAbortRef.current?.abort()");
    expect(center).toContain("if (exportIsCurrent()) setError");
  });

  it("compiles the disabled-by-default public lifecycle flag into the frontend image", () => {
    const dockerfile = readFileSync(new URL("../Dockerfile", import.meta.url), "utf8");
    expect(dockerfile).toContain("ARG NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=false");
    expect(dockerfile).toContain("ENV NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=$NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED");
  });
});
