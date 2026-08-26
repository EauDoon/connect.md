import type { Metadata } from "next";
import { NetworkHub } from "@/components/network-hub";

export const metadata: Metadata = { title: "Private network", description: "Human-only private connections, conversations, and notifications.", robots: { index: false, follow: false } };
export default function NetworkPage() { return <NetworkHub />; }
