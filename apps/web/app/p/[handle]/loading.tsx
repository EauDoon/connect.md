export default function LoadingPublicProfile() {
  return (
    <div className="mx-auto max-w-4xl px-5 py-12 lg:px-8" aria-busy="true" aria-label="Loading public profile." role="status" aria-live="polite" aria-atomic="true">
      <span className="sr-only">Loading public profile.</span>
      <div className="h-8 w-40 motion-safe:animate-pulse rounded-full bg-white/10" aria-hidden="true" />
      <div className="mt-8 h-64 motion-safe:animate-pulse rounded-3xl bg-white/[.06]" />
    </div>
  );
}
