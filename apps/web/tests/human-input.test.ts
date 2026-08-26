import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HUMAN_INPUT_DEBOUNCE_MS, commitBufferedInputValue, createBufferedCommitter, createBufferedInputCommitter, nextBufferedInputValue } from "../lib/human-input";
import { frontmatterParseIssue, patchHumanFields, profileStarter } from "../lib/markdown";

afterEach(() => vi.useRealTimers());

describe("Human Mode buffered input", () => {
  it("keeps word-separating spaces while Product manager is typed incrementally and commits after its debounce", () => {
    vi.useFakeTimers();
    const commit = vi.fn();
    const committer = createBufferedInputCommitter(commit);
    const typed = [..."Product manager"].reduce((value, character) => nextBufferedInputValue(`${value}${character}`), "");
    [..."Product manager"].reduce((value, character) => { const next = nextBufferedInputValue(`${value}${character}`); committer.update(next); return next; }, "");
    expect(typed).toBe("Product manager");
    expect(commit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(HUMAN_INPUT_DEBOUNCE_MS);
    expect(commit).toHaveBeenCalledWith("Product manager");
    expect(committer.flush()).toBe(false);
    expect(commit).toHaveBeenCalledOnce();
    expect(commitBufferedInputValue(`${typed} `)).toBe("Product manager");
  });

  it("flushes the latest pending scalar exactly once when its owner cleans up before blur", () => {
    vi.useFakeTimers();
    const commit = vi.fn();
    const committer = createBufferedInputCommitter(commit);

    committer.update("Product");
    committer.update("Product manager ");

    expect(committer.flush()).toBe(true);
    expect(commit).toHaveBeenCalledWith("Product manager");
    vi.advanceTimersByTime(HUMAN_INPUT_DEBOUNCE_MS);
    expect(committer.flush()).toBe(false);
    expect(commit).toHaveBeenCalledOnce();
  });

  it("does not duplicate a pending scalar after blur then cleanup", () => {
    const commit = vi.fn();
    const committer = createBufferedInputCommitter(commit);

    committer.update("  Product manager  ");
    expect(committer.flush()).toBe(true);
    expect(committer.flush()).toBe(false);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith("Product manager");
  });

  it("keeps exact multiline and empty removal values for career highlights and advanced source", () => {
    const commit = vi.fn();
    const committer = createBufferedCommitter(commit);

    committer.update("First outcome\n\n- exact Markdown\n");
    expect(committer.flush()).toBe(true);
    committer.update("");
    expect(committer.flush()).toBe(true);
    expect(committer.flush()).toBe(false);
    expect(commit).toHaveBeenNthCalledWith(1, "First outcome\n\n- exact Markdown\n");
    expect(commit).toHaveBeenNthCalledWith(2, "");
    expect(commit).toHaveBeenCalledTimes(2);
  });

  it("flushes into the latest canonical patch without removing unknown source", () => {
    let markdown = profileStarter
      .replace("visibility: private", "import_context:\n  source: user-upload\nvisibility: private")
      .replace("## Skills", "## Projects\n\nAn independent section.\n\n## Skills");
    const committer = createBufferedInputCommitter((headline) => {
      markdown = patchHumanFields(markdown, "profile", { headline });
    });

    committer.update("  Product manager  ");
    expect(committer.flush()).toBe(true);

    expect(markdown).toContain("headline: Product manager");
    expect(markdown).toContain("import_context:");
    expect(markdown).toContain("## Projects\n\nAn independent section.");
  });

  it("keeps malformed Markdown fail-closed instead of applying a flushed guided patch", () => {
    const malformed = profileStarter.replace("name: Your Name", "name: [unterminated");
    expect(frontmatterParseIssue(malformed)).toContain("invalid");
    expect(() => patchHumanFields(malformed, "profile", { name: "Ada" })).toThrow("cannot edit this draft");
  });

  it("wires pending scalar and textarea values through idempotent blur and cleanup flushes", () => {
    const source = readFileSync(new URL("../components/human-buffered-fields.tsx", import.meta.url), "utf8");
    expect(source).toContain("committer.update(nextValue)");
    expect(source).toContain("useEffect(() => () => { committer.flush(); }, [committer]);");
    expect(source).toContain("onBlur={() => { const committed = commitBufferedInputValue(draftValue); setDraftValue(committed); committer.flush(); }}");
    expect(source).toContain("onChange={(event) => { const nextValue = event.target.value; setDraftValue(nextValue); committer.update(nextValue); }}");
    expect(source).toContain("const onCommitRef = useRef(onCommit);");
  });
});
