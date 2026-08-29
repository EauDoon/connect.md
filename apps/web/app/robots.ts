import type { MetadataRoute } from "next";

import { absoluteSiteUrl } from "@/lib/public-document";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: ["/", "/human", "/md", "/trust", "/agent-readme.md", "/llms.txt"],
      disallow: [
        "/account",
        "/agents",
        "/agent-directory",
        "/appeal-review",
        "/applications",
        "/discover",
        "/employer",
        "/feed",
        "/inbox",
        "/jobs",
        "/messages/",
        "/moderation",
        "/network",
        "/organizations",
        "/p/",
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
