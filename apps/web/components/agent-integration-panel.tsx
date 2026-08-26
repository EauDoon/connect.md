"use client";

import { Check, Copy, FileCode2, LockKeyhole } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  CONTINUOUS_AGENT_HANDOFF_GUIDE,
  continuousAgentHandoffGuide,
  internalAgentOutreachGuide,
} from "@/lib/agent-contract-guides";

export { CONTINUOUS_AGENT_HANDOFF_GUIDE };

export function AgentIntegrationPanel() {
  const integrationContract = internalAgentOutreachGuide();
  const continuousGuide = continuousAgentHandoffGuide();
  const [outreachCopied, setOutreachCopied] = useState(false);
  const [handoffCopied, setHandoffCopied] = useState(false);

  async function copyOutreachContract() {
    try {
      await navigator.clipboard.writeText(integrationContract);
      setOutreachCopied(true);
    } catch { setOutreachCopied(false); }
  }

  async function copyContinuousHandoff() {
    try {
      await navigator.clipboard.writeText(continuousGuide);
      setHandoffCopied(true);
    } catch { setHandoffCopied(false); }
  }

  return <section aria-labelledby="agent-integration-title" className="mt-5 rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"><div className="flex gap-3"><FileCode2 className="mt-0.5 size-5 shrink-0 text-acid" aria-hidden /><div><p className="eyebrow">Integration contract</p><h2 id="agent-integration-title" className="mt-2 text-2xl font-semibold text-white">Hand an agent the right boundaries.</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-mist">Copy a secret-free operating guide before you supply a separately stored credential. The guide explains synchronization, proposal review, direct-write preconditions, expiry, and revocation without creating a new grant or sending an action.</p></div></div><section aria-labelledby="continuous-handoff-title" className="mt-6 rounded-2xl border border-acid/20 bg-acid/[.045] p-4"><div className="flex flex-wrap items-start justify-between gap-4"><div><h3 id="continuous-handoff-title" className="text-sm font-semibold text-white">Continuous maintenance handoff</h3><p className="mt-1 max-w-3xl text-xs leading-5 text-mist">The per-grant handoff below adds that grant&apos;s exact boundary, mode, scopes, and expiry. Neither guide contains a credential.</p></div><Button variant="secondary" onClick={() => void copyContinuousHandoff()}>{handoffCopied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}{handoffCopied ? "Copied" : "Copy maintenance guide"}</Button></div><pre className="mt-4 overflow-x-auto rounded-xl border border-white/10 bg-black/25 p-4 text-xs leading-6 text-mist"><code>{continuousGuide}</code></pre></section><section aria-labelledby="outreach-contract-title" className="mt-5 rounded-2xl border border-white/10 bg-black/15 p-4"><div className="flex flex-wrap items-start justify-between gap-4"><div><h3 id="outreach-contract-title" className="text-sm font-semibold text-white">Internal outreach contract</h3><p className="mt-1 text-xs leading-5 text-mist">A separate mandate-bound capability. It does not come from a document-maintenance grant.</p></div><Button variant="secondary" onClick={() => void copyOutreachContract()}>{outreachCopied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}{outreachCopied ? "Copied" : "Copy outreach contract"}</Button></div><pre className="mt-4 overflow-x-auto rounded-xl border border-white/10 bg-black/25 p-4 text-xs leading-6 text-mist"><code>{integrationContract}</code></pre></section><p className="mt-4 flex gap-2 text-xs leading-5 text-mist"><LockKeyhole className="mt-0.5 size-4 shrink-0 text-acid" aria-hidden />Keep credentials only in a runtime secret manager. This workspace does not store credentials or outreach messages, and copied handoffs never include them.</p></section>;
}
