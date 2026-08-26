import type { Metadata } from "next";
import { CandidateApplications } from "@/components/candidate-applications";

export const metadata: Metadata = { title: "My applications", description: "Private, candidate-owned application records.", robots: { index: false, follow: false } };
export default function ApplicationsPage() { return <CandidateApplications />; }
