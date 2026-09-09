"use client";

import React, { useMemo } from "react";
import { useDraft } from "@/components/draft-provider";
import { reviewWriting } from "@/lib/writing-review";

export function WritingReview() {
  const { markdown } = useDraft();
  const review = useMemo(() => reviewWriting(markdown), [markdown]);
  return <details className="mt-4 border-t border-white/10 pt-3">
    <summary className="cursor-pointer text-sm font-semibold text-white">Writing review ({review.findings.length}{review.limited ? "+" : ""})</summary>
    <p className="mt-2 text-xs leading-5 text-mist">Suggestions for unfinished text, empty sections, repetition, and readability. These checks do not verify facts, judge your experience, or affect schema validation.</p>
    {!review.findings.length && !review.limited && <p className="mt-2 text-xs text-mist">No configured writing patterns were found. Check that your claims and outcomes are accurate.</p>}
    <ol className="mt-2 space-y-2">{review.findings.map((finding, index) => <li key={`${finding.code}-${finding.line}-${index}`} className="text-xs leading-5 text-mist"><span className="font-semibold text-white">Line {finding.line}:</span> {finding.message}</li>)}</ol>
    {review.limited && <p className="mt-2 text-xs text-amber-100">Review is limited to 128 KiB and 20 suggestions. Additional content may not be represented.</p>}
  </details>;
}
