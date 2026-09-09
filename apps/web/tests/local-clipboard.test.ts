import { afterEach, describe, expect, it, vi } from "vitest";
import { copyLocalMarkdown } from "../lib/local-clipboard";

afterEach(() => vi.unstubAllGlobals());
describe("explicit Markdown clipboard export", () => {
  it("writes exact Unicode bytes without a network or storage fallback", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await copyLocalMarkdown("---\nname: Zoë\n---\n# Draft 📝\n");
    expect(writeText).toHaveBeenCalledExactlyOnceWith("---\nname: Zoë\n---\n# Draft 📝\n");
  });
  it("gives an actionable error when unsupported or denied", async () => {
    vi.stubGlobal("navigator", {});
    await expect(copyLocalMarkdown("draft")).rejects.toThrow("Download the .md");
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    await expect(copyLocalMarkdown("draft")).rejects.toThrow("denied clipboard access");
  });
});
