export default function LoadingDiscover() {
  return (
    <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8" aria-busy="true" aria-label="Loading public discovery." role="status" aria-live="polite" aria-atomic="true">
      <span className="sr-only">Loading public discovery.</span>
      <div className="h-8 w-52 motion-safe:animate-pulse rounded-full bg-white/10" aria-hidden="true" />
      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <div key={index} className="h-24 motion-safe:animate-pulse rounded-2xl bg-white/[.06]" aria-hidden="true" />
        ))}
      </div>
    </div>
  );
}
