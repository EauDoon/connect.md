export default function LoadingNetwork() {
  return (
    <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8" aria-busy="true" aria-label="Loading your network." role="status" aria-live="polite" aria-atomic="true">
      <span className="sr-only">Loading your network.</span>
      <div className="h-8 w-48 motion-safe:animate-pulse rounded-full bg-white/10" />
      <div className="mt-6 h-72 motion-safe:animate-pulse rounded-3xl bg-white/[.06]" aria-hidden="true" />
    </div>
  );
}
