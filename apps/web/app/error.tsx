"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="mx-auto grid min-h-[70vh] max-w-2xl place-items-center px-5 py-16 text-center">
      <div>
        <p className="eyebrow">Temporary interruption</p>
        <h1 className="mt-3 font-display text-5xl font-semibold tracking-[-.05em] text-white">This view is temporarily unavailable.</h1>
        <p className="mx-auto mt-4 max-w-md text-mist">Try again shortly. If the problem continues, return home and reopen the page.</p>
        <div className="mt-8 flex flex-wrap justify-center gap-3"><Button onClick={reset}>Try again</Button><Link href="/" className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Home</Link></div>
      </div>
    </main>
  );
}
