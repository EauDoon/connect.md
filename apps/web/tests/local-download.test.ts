import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadMarkdown, localDownloadFreshness, markdownDownloadName, preferredMarkdownName } from "../components/publish-panel";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("local Markdown download", () => {
  it("distinguishes a current local download from changed document bytes", () => {
    const receipt = { kind: "profile" as const, markdown: "# Ada\n" };

    expect(localDownloadFreshness(null, "profile", "# Ada\n")).toBe("none");
    expect(localDownloadFreshness(receipt, "profile", "# Ada\n")).toBe("current");
    expect(localDownloadFreshness(receipt, "profile", "# Ada Lovelace\n")).toBe("stale");
    expect(localDownloadFreshness(receipt, "resume", "# Ada\n")).toBe("none");
  });

  it("creates a bounded filename from the canonical identifier", () => {
    expect(markdownDownloadName("profile", " Ada / Lovelace ")).toBe("ada-lovelace.md");
    expect(markdownDownloadName("resume", "../../")).toBe("connectmd-resume.md");
    expect(markdownDownloadName("profile", "ari--chen")).toBe("ari--chen.md");
  });
  it("lets authors name a local export without changing the document identity", () => {
    expect(preferredMarkdownName("profile", "original-id", " Revision Two.md ")).toBe("revision-two.md");
    expect(preferredMarkdownName("profile", "original-id", "")).toBe("original-id.md");
    expect(preferredMarkdownName("resume", "original-id", "../../CON.md")).toBe("connectmd-con.md");
    expect(preferredMarkdownName("profile", "original-id", "a".repeat(500))).toHaveLength(83);
  });
  it("cleans up a failed download without pretending the browser saved it", () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:failed");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const anchor = { style: {}, click: () => { throw new Error("blocked"); }, remove: vi.fn() };
    vi.stubGlobal("document", { body: { append: vi.fn() }, createElement: () => anchor });
    expect(() => downloadMarkdown("# Kept\n", "kept.md")).toThrow("blocked");
    expect(anchor.remove).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith("blob:failed");
  });

  it("clicks one local download and always releases the temporary URL", () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:connectmd-download");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const anchor = { href: "", download: "", style: { display: "" }, click: vi.fn(), remove: vi.fn() };
    const append = vi.fn();
    vi.stubGlobal("document", { body: { append }, createElement: vi.fn(() => anchor) });

    downloadMarkdown("# Ada\n", "ada.md");

    expect(anchor).toMatchObject({ href: "blob:connectmd-download", download: "ada.md" });
    expect(append).toHaveBeenCalledOnce();
    expect(anchor.click).toHaveBeenCalledOnce();
    expect(anchor.remove).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith("blob:connectmd-download");
  });
});
