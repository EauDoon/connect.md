import type { MetadataRoute } from "next";

import { absoluteSiteUrl } from "@/lib/public-document";
import { recruitingReleaseEnabled } from "@/lib/recruiting-release";

export const dynamic = "force-dynamic";

export default function robots(): MetadataRoute.Robots {
  const recruitingEnabled = recruitingReleaseEnabled();
  return {
    rules: {
      userAgent: "*",
      allow: [
        "/",
        "/search",
        "/discover",
        "/p/",
        "/r/",
        "/posts/",
        "/agents/",
        "/representatives",
        "/agent-directory",
        ...(recruitingEnabled ? ["/organizations", "/jobs"] : []),
      ],
      disallow: [
        "/account",
        "/human",
        "/md",
        "/agents",
        "/feed",
        "/moderation",
        "/moderation-review",
        "/appeal-review",
        "/inbox",
        "/applications",
        "/employer",
        "/verification-review",
        "/network",
        "/messages/",
        ...(recruitingEnabled ? [] : ["/organizations", "/jobs"]),
      ],
    },
    sitemap: [0, 1, 2, 3].map((id) => absoluteSiteUrl(`/sitemap/${id}.xml`))
  };
}
