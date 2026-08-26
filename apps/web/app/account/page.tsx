import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { AccountPrivacyCenter } from "@/components/account-privacy-center";
import { accountLifecycleFeatureEnabled } from "@/lib/account-lifecycle-api";

export const metadata: Metadata = {
  title: "Account privacy",
  description: "Private account export and deletion controls.",
  robots: { index: false, follow: false }
};

export default function AccountPrivacyPage() {
  if (!accountLifecycleFeatureEnabled()) notFound();
  return <AccountPrivacyCenter />;
}
