import { describe, expect, it } from "vitest";

import robots from "../app/robots";
import sitemap from "../app/sitemap";

describe("standalone Vercel discovery", () => {
  it("publishes only the pages that work without a backend", () => {
    expect(sitemap()).toEqual([
      { url: "https://connect.md/", changeFrequency: "weekly", priority: 1 },
      { url: "https://connect.md/human", changeFrequency: "monthly", priority: 0.9 },
      { url: "https://connect.md/md", changeFrequency: "monthly", priority: 0.8 },
      { url: "https://connect.md/trust", changeFrequency: "monthly", priority: 0.7 },
    ]);
  });

  it("allows the standalone site and keeps backend-only routes out of crawlers", () => {
    const value = robots();
    const rules = Array.isArray(value.rules) ? value.rules[0] : value.rules;

    expect(rules?.allow).toEqual(["/", "/human", "/md", "/trust", "/agent-readme.md", "/llms.txt"]);
    expect(rules?.disallow).toEqual(expect.arrayContaining([
      "/discover",
      "/search",
      "/agents",
      "/organizations",
      "/jobs",
      "/workspace",
    ]));
    expect(value.sitemap).toBe("https://connect.md/sitemap.xml");
  });
});
