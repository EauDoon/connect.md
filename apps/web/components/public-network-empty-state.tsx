import Link from "next/link";
import { Bot, Sparkles } from "lucide-react";
import React from "react";

import { publicDiscoveryUrl } from "@/lib/api";

export function PublicNetworkEarlyState({
  detail,
  headingLevel = 2,
}: {
  detail: string;
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";

  return (
    <div className="rounded-[1.4rem] border border-dashed border-acid/25 bg-acid/[.045] p-7 text-center sm:p-8">
      <Sparkles className="mx-auto size-6 text-acid" aria-hidden />
      <Heading className="mt-4 text-lg font-semibold text-white">The public network is early.</Heading>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-mist">{detail}</p>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-mist">
        Start a private draft through the agent onboarding guide, or build it directly in Human Mode. Publication remains a separate, explicit action.
      </p>
      <div className="mt-5 flex flex-wrap justify-center gap-3">
        <a
          href={publicDiscoveryUrl("/agent-readme.md")}
          type="text/markdown"
          className="inline-flex min-h-11 items-center gap-2 rounded-full bg-acid px-5 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
        >
          <Bot className="size-4" aria-hidden />
          Agent-first onboarding
        </a>
        <Link
          href="/human"
          className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
        >
          Open Human Mode
        </Link>
      </div>
    </div>
  );
}
