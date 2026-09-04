import type { Metadata } from "next";
import { type ReactNode } from "react";

import "@/app/globals.css";
import { DraftProvider } from "@/components/draft-provider";
import { SiteHeader } from "@/components/site-header";
import { publicSiteOrigin } from "@/lib/public-document";

export const metadata: Metadata = {
  metadataBase: new URL(publicSiteOrigin()),
  title: { default: "connect.md: work, in Markdown", template: "%s · connect.md" },
  description: "Build a portable professional profile or resume as Markdown, privately in your browser.",
  alternates: { canonical: "/" }
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <DraftProvider>
          <a href="#main-content" className="skip-link">Skip to main content</a>
          <SiteHeader />
          <div id="main-content" tabIndex={-1}>{children}</div>
        </DraftProvider>
      </body>
    </html>
  );
}
