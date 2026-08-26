import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto grid min-h-[70vh] max-w-2xl place-items-center px-5 py-16 text-center">
      <div>
        <p className="eyebrow">404 · Not found</p>
        <h1 className="mt-3 font-display text-5xl font-semibold tracking-[-.05em] text-white">This page is not available.</h1>
        <p className="mx-auto mt-4 max-w-md text-mist">It may have moved, been withdrawn, or never been published.</p>
        <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-mist/75">Private and unpublished records are never exposed through this page.</p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link href="/discover" className="inline-flex min-h-11 items-center rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:bg-[#e5ff92] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Explore public records</Link>
          <Link href="/" className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white transition hover:border-white/30 hover:bg-white/[.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Home</Link>
        </div>
      </div>
    </main>
  );
}
