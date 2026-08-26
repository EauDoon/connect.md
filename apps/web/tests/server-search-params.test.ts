import { describe, expect, it } from "vitest";

import { serverSearchParams } from "../lib/server-search-params";

describe("server search params", () => {
  it("preserves property order, duplicate order, scalar values, and empty strings", () => {
    const params = serverSearchParams({
      q: "payments",
      skill: ["python", "risk"],
      location: "",
      cursor: "signed-page",
    });

    expect([...params.entries()]).toEqual([
      ["q", "payments"],
      ["skill", "python"],
      ["skill", "risk"],
      ["location", ""],
      ["cursor", "signed-page"],
    ]);
  });

  it("omits only undefined values without validating or normalizing input", () => {
    const params = serverSearchParams({
      omitted: undefined,
      whitespace: "  ",
      duplicateEmpty: ["", ""],
    });

    expect(params.has("omitted")).toBe(false);
    expect(params.get("whitespace")).toBe("  ");
    expect(params.getAll("duplicateEmpty")).toEqual(["", ""]);
  });
});
