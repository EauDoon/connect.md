import type { Metadata } from "next";

import { ModerationCaseReviewQueue } from "@/components/moderation-case-review-queue";

export const metadata: Metadata = {
  title: "Private moderation review",
  description: "Human-only, server-authorized professional-post moderation review.",
  robots: { index: false, follow: false }
};

export default function ModerationReviewPage() { return <ModerationCaseReviewQueue />; }
