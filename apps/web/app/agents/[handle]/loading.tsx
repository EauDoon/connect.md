import React from "react";

import { PublicRouteLoading } from "@/components/public-route-loading";

export default function PublicAgentIdentityLoading() {
  return (
    <PublicRouteLoading className="mx-auto max-w-4xl px-5 py-12 lg:px-8 lg:py-16" label="Loading the public Agent Identity.">
        <div className="h-3 w-40 rounded bg-white/10" />
        <div className="mt-5 rounded-[1.8rem] border border-white/10 bg-panel p-6 sm:p-9">
          <div className="h-3 w-44 rounded bg-acid/15" />
          <div className="mt-5 h-12 max-w-2xl rounded bg-white/10" />
          <div className="mt-4 h-5 w-40 rounded bg-white/10" />
          <div className="mt-8 space-y-3">
            <div className="h-4 rounded bg-white/10" />
            <div className="h-4 w-11/12 rounded bg-white/10" />
            <div className="h-4 w-4/5 rounded bg-white/10" />
          </div>
          <div className="mt-8 h-28 rounded-2xl border border-white/10 bg-black/15" />
        </div>
    </PublicRouteLoading>
  );
}
