import type { Metadata } from "next";

import { ModerationCaseManager } from "@/components/moderation-case-manager";

export const metadata: Metadata = { title: "Private post case status", description: "Human-only private post moderation status and eligible appeals.", robots: { index: false, follow: false } };

export default function ModerationPage() { return <ModerationCaseManager />; }
