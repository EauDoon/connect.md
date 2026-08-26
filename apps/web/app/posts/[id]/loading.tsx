import React from "react";

import { PublicRouteLoading } from "@/components/public-route-loading";

export default function PublicPostLoading() {
  return (
    <PublicRouteLoading className="mx-auto max-w-4xl px-5 py-9 sm:py-14 lg:px-8" label="Loading the public professional post.">
        <div className="h-4 w-36 rounded bg-white/10" />
        <article className="mt-7 overflow-hidden rounded-[2rem] border border-white/10 bg-panel">
          <header className="border-b border-white/10 px-6 py-9 sm:px-10 sm:py-12">
            <div className="h-3 w-44 rounded bg-acid/15" />
            <div className="mt-5 h-12 max-w-3xl rounded bg-white/10" />
            <div className="mt-5 h-5 w-64 rounded bg-white/10" />
          </header>
          <div className="space-y-3 px-6 py-8 sm:px-10 sm:py-11">
            <div className="h-4 rounded bg-white/10" />
            <div className="h-4 w-11/12 rounded bg-white/10" />
            <div className="h-4 w-4/5 rounded bg-white/10" />
          </div>
        </article>
    </PublicRouteLoading>
  );
}
