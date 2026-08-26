import type { Metadata } from "next";

import { ProfessionalFeed } from "@/components/professional-feed";

export const metadata: Metadata = { title: "Private feed", description: "Human-only private chronological professional posts and follows.", robots: { index: false, follow: false } };

export default function FeedPage() { return <ProfessionalFeed />; }
