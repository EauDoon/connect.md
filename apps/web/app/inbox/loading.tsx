export default function LoadingInbox() {
  return (
    <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8" aria-busy="true" aria-label="Loading your inbox." role="status" aria-live="polite" aria-atomic="true">
      <span className="sr-only">Loading your inbox.</span>
      <div className="h-8 w-40 motion-safe:animate-pulse rounded-full bg-white/10" aria-hidden="true" />
      <div className="mt-6 grid gap-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <div key={index} className="h-16 motion-safe:animate-pulse rounded-xl bg-white/[.06]" aria-hidden="true" />
        ))}
      </div>
    </div>
  );
}
