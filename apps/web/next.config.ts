import type { NextConfig } from "next";

import { vercelSecurityHeaders } from "./lib/deployment-config";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  images: { remotePatterns: [] },
  headers: async () => {
    const headers = vercelSecurityHeaders();
    return headers.length > 0 ? [{ source: "/(.*)", headers }] : [];
  }
};

export default nextConfig;
