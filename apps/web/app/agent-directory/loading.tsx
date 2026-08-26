import React from "react";

import { PublicRouteLoading } from "@/components/public-route-loading";

export default function AgentDirectoryLoading() {
  return (
    <PublicRouteLoading className="pb-16" label="Loading published agent identities.">
      <section className="border-b border-white/10 bg-black/10">
        <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8 lg:py-16">
          <div className="h-3 w-44 rounded bg-white/[.08]" />
          <div className="mt-5 h-28 max-w-4xl rounded-2xl bg-white/[.07]" />
          <div className="mt-5 h-16 max-w-3xl rounded-2xl bg-white/[.05]" />
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-5 py-8 lg:px-8">
        <div className="rounded-[1.5rem] border border-white/12 bg-panel p-4 sm:p-5">
          <div className="h-5 w-56 rounded bg-white/[.08]" />
          <div className="mt-5 grid gap-3 md:grid-cols-[minmax(0,1fr)_16rem_auto]">
            <div className="h-11 rounded-xl bg-white/[.06]" />
            <div className="h-11 rounded-xl bg-white/[.06]" />
            <div className="h-11 rounded-full bg-acid/20" />
          </div>
        </div>
        <div className="mt-7 grid gap-4 lg:grid-cols-2">
          {[1, 2].map((item) => <div key={item} className="h-72 rounded-[1.4rem] border border-white/10 bg-panel" />)}
        </div>
      </section>
    </PublicRouteLoading>
  );
}
