import type { Metadata } from "next";

import { WorkspaceHub } from "@/components/workspace-hub";

export const metadata: Metadata = {
  title: "Your workspace",
  description: "Private navigation for your connect.md documents, network, work, and safety controls.",
  robots: { index: false, follow: false },
};

export default function WorkspacePage() {
  return <WorkspaceHub />;
}
