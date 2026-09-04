"use client";

import { CheckCircle2, CircleAlert, TriangleAlert } from "lucide-react";
import React from "react";

import { type ValidationIssue } from "@/lib/validation";

export function ValidationPanel({ issues }: { issues: ValidationIssue[] }) {
  const errors = issues.filter((issue) => issue.level === "error");
  const warnings = issues.filter((issue) => issue.level === "warning");
  const status = errors.length
    ? `${errors.length} blocking issue${errors.length === 1 ? "" : "s"}`
    : warnings.length
      ? `Ready with ${warnings.length} warning${warnings.length === 1 ? "" : "s"}`
      : "Ready to download";
  const statusClassName = errors.length
    ? "text-red-200"
    : warnings.length
      ? "text-amber-100"
      : "text-acid";

  return (
    <section aria-labelledby="validation-title" className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-4">
        <h2 id="validation-title" className="min-w-0 text-sm font-semibold text-white">Validation</h2>
        <span className={`min-w-0 break-words text-xs font-medium ${statusClassName}`}>{status}</span>
      </div>
      <ul className="mt-3 space-y-2" aria-live="polite">
        {issues.map((issue, index) => (
          <li key={`${issue.level}-${index}`} className={`flex gap-2 text-sm leading-5 ${issue.level === "error" ? "text-red-200" : issue.level === "warning" ? "text-amber-100" : "text-mist"}`}>
            {issue.level === "error"
              ? <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              : issue.level === "warning"
                ? <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
                : <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-acid" aria-hidden />}
            {issue.message}
          </li>
        ))}
      </ul>
    </section>
  );
}
