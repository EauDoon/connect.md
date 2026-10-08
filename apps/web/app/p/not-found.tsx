import Link from "next/link";

/**
 * Rendered for every /p/{handle} that is not an explicitly published profile.
 * It lives above the [handle] segment because the not-found is raised by that
 * segment's layout, whose nearest boundary is this one. Next answers with HTTP
 * 404 and a noindex robots tag, so missing, private, and unconfigured profiles
 * never become indexable thin pages.
 */
export default function PublicProfileNotFound() {
  return (
    <main className="mx-auto grid min-h-[70vh] max-w-2xl place-items-center px-5 py-16 text-center">
      <div data-testid="profile-unavailable">
        <p className="eyebrow">404</p>
        <h1 className="mt-3 font-display text-5xl font-semibold tracking-[-.05em] text-white">No published profile at this address.</h1>
        <p className="mx-auto mt-4 max-w-md text-mist">The profile may be private, unpublished, or nonexistent.</p>
        <Link href="/discover" className="mt-8 inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white hover:bg-white/[.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid">
          Back to discovery
        </Link>
      </div>
    </main>
  );
}
