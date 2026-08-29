import type { MetadataRoute } from "next";

import { absoluteSiteUrl } from "@/lib/public-document";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: absoluteSiteUrl("/"), changeFrequency: "weekly", priority: 1 },
    { url: absoluteSiteUrl("/human"), changeFrequency: "monthly", priority: 0.9 },
    { url: absoluteSiteUrl("/md"), changeFrequency: "monthly", priority: 0.8 },
    { url: absoluteSiteUrl("/trust"), changeFrequency: "monthly", priority: 0.7 },
  ];
}
