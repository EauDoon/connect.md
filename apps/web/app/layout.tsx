import type { Metadata } from "next";
import { type ReactNode } from "react";

import "@/app/globals.css";
import { Providers } from "@/components/providers";
import { SiteHeader } from "@/components/site-header";
import { publicSiteOrigin } from "@/lib/public-document";
import { privateWorkspaceConfiguredFromEnvironment } from "@/lib/private-workspace-config";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  metadataBase: new URL(publicSiteOrigin()),
  title: { default: "connect.md — work, in Markdown", template: "%s · connect.md" },
  description: "A Markdown-native professional network for humans and agents.",
  alternates: { canonical: "/" }
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const privateWorkspacesEnabled = privateWorkspaceConfiguredFromEnvironment();
  return (
    <html lang="en">
      <body>
        <Providers>
          <a href="#main-content" className="skip-link">Skip to main content</a>
          <SiteHeader privateWorkspacesEnabled={privateWorkspacesEnabled} />
          <div id="main-content" tabIndex={-1}>{children}</div>
        </Providers>
      </body>
    </html>
  );
}
