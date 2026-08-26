import type { Metadata } from "next";

import { AgentDelegationManager } from "@/components/agent-delegation-manager";
import { AgentIdentityManager } from "@/components/agent-identity-manager";
import { AgentIntegrationPanel } from "@/components/agent-integration-panel";

export const metadata: Metadata = { title: "Agent management", robots: { index: false, follow: false } };

export default function AgentsPage() {
  return <main className="mx-auto max-w-7xl px-5 py-10 lg:px-8 lg:py-14"><section className="mb-8 max-w-3xl"><p className="eyebrow">Bounded continuity</p><h1 className="mt-3 font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">Let an agent maintain the record—inside a mandate.</h1><p className="mt-4 text-base leading-7 text-mist">Grant the smallest useful authority, inspect every recorded change, and revoke access without touching your canonical document.</p></section><AgentIdentityManager /><AgentIntegrationPanel /><AgentDelegationManager /></main>;
}
