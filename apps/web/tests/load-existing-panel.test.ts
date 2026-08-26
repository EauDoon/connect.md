import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";


describe("returning-user document selection", () => {
  it("lists owned documents with bounded account-change and dirty-draft protections", () => {
    const source = readFileSync(new URL("../components/load-existing-panel.tsx", import.meta.url), "utf8");
    expect(source).toContain("listOwnedDocumentPageForSubject(getToken, isSubjectCurrent");
    expect(source).toContain("Your saved {kind}s");
    expect(source).toContain("Loading saved documents…");
    expect(source).toContain("Continue below to create your first one.");
    expect(source).toContain("inventoryRequestRef.current !== requestId");
    expect(source).toContain("authIdentityRef.current !== authIdentity");
    expect(source).toContain("shouldConfirmDraftReplacement");
    expect(source).toContain("The local draft changed while the saved document was loading");
    expect(source).toContain("Retry list");
    expect(source).toContain("Load more");
    expect(source).toContain("inventorySeenCursorsRef.current.has(page.nextCursor)");
  });
});
