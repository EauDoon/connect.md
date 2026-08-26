import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-3xl border border-white/10 bg-panel/75 shadow-[0_18px_60px_rgba(0,0,0,.18)] backdrop-blur", className)} {...props} />;
}
