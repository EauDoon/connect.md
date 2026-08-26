import React, { type ReactNode } from "react";

export function AsyncBoundaryMessage({
  children,
  className,
  loading,
}: {
  children: ReactNode;
  className: string;
  loading: boolean;
}) {
  return (
    <p
      className={className}
      role={loading ? "status" : undefined}
      aria-live={loading ? "polite" : undefined}
      aria-atomic={loading ? "true" : undefined}
    >
      {children}
    </p>
  );
}
