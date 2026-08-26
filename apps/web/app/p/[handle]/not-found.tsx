import Link from "next/link";

export default function PublicProfileNotFound() {
  return (
    <main className="mx-auto grid min-h-[70vh] max-w-2xl place-items-center px-5 py-16 text-center">
      <div>
        <p className="eyebrow">404</p>
        <h1 className="mt-3 font-display text-5xl font-semibold tracking-[-.05em] text-white">This profile is not available.</h1>
        <p className="mx-auto mt-4 max-w-md text-mist">It may be private, unpublished, or no longer exist.</p>
        <Link href="/" className="mt-8 inline-flex rounded-full bg-acid px-5 py-3 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">Return to connect.md</Link>
      </div>
    </main>
  );
}
