import React, { type ReactNode } from "react";

export function PublicRouteLoading({ children, className, label }: { children: ReactNode; className: string; label: string }) {
  return (
    <main className={className} aria-busy="true" aria-label={label}>
      <span role="status" aria-live="polite" aria-atomic="true" className="sr-only">{label}</span>
      <div aria-hidden="true" className="motion-safe:animate-pulse">{children}</div>
    </main>
  );
}
