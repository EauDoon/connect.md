import type { Metadata } from "next";

import { ModerationAppealReviewQueue } from "@/components/moderation-appeal-review-queue";

export const metadata: Metadata = {
  title: "Private appeal review",
  description: "Human-only, server-authorized independent moderation appeal review.",
  robots: { index: false, follow: false }
};

export default function AppealReviewPage() { return <ModerationAppealReviewQueue />; }
