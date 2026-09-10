"use client";

import React from "react";
import Link from "next/link";
import { useDraft } from "@/components/draft-provider";

export function ReviewSourceLink({ line }: { line: number }) {
  const { requestSourceLine } = useDraft();
  return <Link href="/md" onClick={() => requestSourceLine(line)} aria-label={`Edit line ${line} in Markdown`} className="inline-flex min-h-11 items-center font-semibold underline underline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-acid">Line {line}</Link>;
}
