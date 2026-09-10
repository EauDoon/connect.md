import { describe, expect, it } from "vitest";
import { encodeRecoveryBundle, parseRecoveryBundle } from "../lib/session-recovery";

describe("portable session recovery", () => {
  const draft = { kind: "profile" as const, markdown: "invalid unfinished source\n" };
  it("backs up unfinished source and named checkpoints without claiming validation", () => {
    const checkpoint = { id: 4, label: "Earlier", kind: "resume" as const, markdown: "# Earlier\n" };
    expect(JSON.parse(encodeRecoveryBundle(draft, [checkpoint]))).toEqual({
      format: "connectmd-recovery", version: 1, draft,
      checkpoints: [{ label: "Earlier", kind: "resume", markdown: "# Earlier\n" }],
    });
  });
  it("bounds each UTF-8 source and count without evicting work", () => {
    expect(() => encodeRecoveryBundle({ ...draft, markdown: "é".repeat(65537) }, [])).toThrow("128 KiB");
    expect(() => encodeRecoveryBundle(draft, Array.from({ length: 6 }, (_, id) => ({ ...draft, id, label: String(id) })))).toThrow("five");
  });
  it("restores exact unfinished source and assigns no imported runtime identifiers", () => {
    const parsed = parseRecoveryBundle(encodeRecoveryBundle(draft, []));
    expect(parsed.draft).toEqual(draft);
    expect(parsed.checkpoints).toEqual([]);
  });
  it("rejects malformed, unsupported, overlong and ambiguous bundles", () => {
    for (const value of ["null", "[]", "{", JSON.stringify({ format: "connectmd-recovery", version: 2, draft }),
      JSON.stringify({ format: "connectmd-recovery", version: 1, draft: { kind: "post", markdown: "x" }, checkpoints: [] }),
      JSON.stringify({ format: "connectmd-recovery", version: 1, draft, checkpoints: [{ ...draft, label: "A" }, { ...draft, label: " a " }] }),
    ]) expect(() => parseRecoveryBundle(value)).toThrow();
  });
});
