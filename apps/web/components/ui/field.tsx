import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const fieldClass = "w-full rounded-xl border border-white/12 bg-black/25 px-3.5 py-3 text-sm text-white outline-none transition placeholder:text-mist/55 focus:border-acid/70 focus:ring-2 focus:ring-acid/15 disabled:cursor-not-allowed disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn(fieldClass, className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...props }, ref) {
  return <textarea ref={ref} className={cn(fieldClass, "min-h-28 resize-y", className)} {...props} />;
});
