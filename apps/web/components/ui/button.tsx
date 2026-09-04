import React, { Children, forwardRef, isValidElement, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/utils";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "primary", type = "button", children, "aria-busy": ariaBusy, ...props },
  ref
) {
  const resolvedAriaBusy = ariaBusy ?? (containsPendingIndicator(children) ? true : undefined);

  return (
    <button
      ref={ref}
      type={type}
      aria-busy={resolvedAriaBusy}
      className={cn(
        "inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-5 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-acid text-ink hover:bg-[#e5ff92]",
        variant === "secondary" && "border border-white/15 bg-white/[.055] text-white hover:border-white/30 hover:bg-white/[.1]",
        variant === "ghost" && "text-mist hover:bg-white/[.06] hover:text-white",
        variant === "danger" && "border border-red-400/30 bg-red-400/10 text-red-100 hover:bg-red-400/20",
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
});

function containsPendingIndicator(children: ReactNode): boolean {
  return Children.toArray(children).some((child) => {
    if (!isValidElement<{ children?: ReactNode; className?: unknown }>(child)) return false;
    const className = child.props.className;
    if (typeof className === "string" && className.split(/\s+/u).includes("animate-spin")) return true;
    return containsPendingIndicator(child.props.children);
  });
}
