import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadMarkdown, markdownDownloadName } from "../components/publish-panel";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("local Markdown download", () => {
  it("creates a bounded filename from the canonical identifier", () => {
    expect(markdownDownloadName("profile", " Ada / Lovelace ")).toBe("ada-lovelace.md");
    expect(markdownDownloadName("resume", "../../")).toBe("connectmd-resume.md");
    expect(markdownDownloadName("profile", "ari--chen")).toBe("ari--chen.md");
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
