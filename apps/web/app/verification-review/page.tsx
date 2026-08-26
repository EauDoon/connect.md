import type { Metadata } from "next";

import { VerificationReviewQueue } from "@/components/verification-review-queue";

export const metadata: Metadata = { title: "Private verification review", description: "Human-only, server-authorized recruiting-control verification review.", robots: { index: false, follow: false } };

export default function VerificationReviewPage() { return <VerificationReviewQueue />; }
