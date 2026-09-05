import type { MetadataRoute } from "next";

import { absoluteSiteUrl } from "@/lib/public-document";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      // Discovery and published profile pages are intentionally crawlable;
      // everything account-scoped stays out of robots.
      allow: ["/", "/human", "/md", "/trust", "/agent-readme.md", "/llms.txt", "/discover", "/p/"],
      disallow: [
        "/account",
        "/network",
        "/inbox",
        "/conversations/",
        "/api/",
        "/agents",
        "/agent-directory",
        "/appeal-review",
        "/applications",
        "/employer",
        "/feed",
        "/jobs",
        "/messages/",
        "/moderation",
        "/organizations",
        "/posts/",
        "/r/",
        "/representatives",
        "/search",
        "/verification-review",
        "/workspace",
      ],
    },
    sitemap: absoluteSiteUrl("/sitemap.xml"),
  };
}
