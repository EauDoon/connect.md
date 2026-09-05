import { describe, expect, it } from "vitest";

import robots from "../app/robots";
import sitemap from "../app/sitemap";

describe("standalone Vercel discovery", () => {
  it("publishes the guest pages and discovery, and nothing account-scoped", () => {
    expect(sitemap()).toEqual([
      { url: "https://connect.md/", changeFrequency: "weekly", priority: 1 },
      { url: "https://connect.md/human", changeFrequency: "monthly", priority: 0.9 },
      { url: "https://connect.md/md", changeFrequency: "monthly", priority: 0.8 },
      { url: "https://connect.md/trust", changeFrequency: "monthly", priority: 0.7 },
      { url: "https://connect.md/discover", changeFrequency: "hourly", priority: 0.6 },
    ]);
  });

  it("allows the public surfaces and keeps account-scoped and retired routes out of crawlers", () => {
    const value = robots();
    const rules = Array.isArray(value.rules) ? value.rules[0] : value.rules;

    expect(rules?.allow).toEqual(["/", "/human", "/md", "/trust", "/agent-readme.md", "/llms.txt", "/discover", "/p/"]);
    expect(rules?.disallow).toEqual(expect.arrayContaining([
      "/account",
      "/network",
      "/inbox",
      "/conversations/",
      "/api/",
      "/search",
      "/agents",
      "/organizations",
      "/jobs",
      "/posts/",
      "/workspace",
    ]));
    expect(value.sitemap).toBe("https://connect.md/sitemap.xml");
  });
});
