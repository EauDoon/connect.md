"use client";

import React, { useMemo } from "react";
import { useDraft } from "@/components/draft-provider";
import { reviewPrivacy } from "@/lib/privacy-review";

export function PrivacyReview() {
  const { markdown } = useDraft();
  const review = useMemo(() => reviewPrivacy(markdown), [markdown]);
  return <details className="mt-4 border-t border-white/10 pt-3">
    <summary className="cursor-pointer text-sm font-semibold text-white">Sharing review ({review.findings.length}{review.limited ? "+" : ""})</summary>
    <p className="mt-2 text-xs leading-5 text-mist">Local pattern checks for contact details, possible secrets, local paths, and links. This is not a complete security scan. It neither redacts nor blocks your download.</p>
    {!review.findings.length && !review.limited && <p className="mt-2 text-xs text-mist">No configured patterns were found. Review the full file yourself before sharing it.</p>}
    <ul className="mt-2 space-y-2">{review.findings.map((finding) => <li key={`${finding.code}-${finding.line}`} className="text-xs leading-5 text-amber-100"><span className="font-semibold">Line {finding.line}:</span> {finding.message}</li>)}</ul>
    {review.limited && <p className="mt-2 text-xs text-amber-100">Review is limited to 128 KiB and 20 findings. Additional content may not be represented.</p>}
  </details>;
}
