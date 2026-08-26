import type { Metadata } from "next";
import { EmployerWorkspace } from "@/components/employer-workspace";

export const metadata: Metadata = { title: "Employer workspace", description: "Private human organization and application management.", robots: { index: false, follow: false } };
export default function EmployerPage() { return <EmployerWorkspace />; }
