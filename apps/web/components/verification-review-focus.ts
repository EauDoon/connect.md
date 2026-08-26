"use client";

import { useCallback, useEffect, useId, useRef } from "react";

import type { ReviewerVerificationAction } from "@/lib/recruitment-api";

export type ReviewerSelection = {
  verificationId: string;
  action: ReviewerVerificationAction;
};

export type ReviewerFocusTarget = "review" | "trigger" | "card" | null;

export function useReviewerFocus(
  verificationId: string,
  selection: ReviewerSelection | null,
  decisionBusy: boolean,
) {
  const recordHeadingId = useId();
  const reviewRegionId = useId();
  const cardRef = useRef<HTMLElement | null>(null);
  const reviewRegionRef = useRef<HTMLElement | null>(null);
  const actionButtonRefs = useRef(new Map<ReviewerVerificationAction, HTMLButtonElement>());
  const previousSelectionRef = useRef<ReviewerSelection | null>(null);

  useEffect(() => {
    const previous = previousSelectionRef.current;
    previousSelectionRef.current = selection;
    const target = reviewerFocusTarget(previous, selection, verificationId, decisionBusy);
    if (target === "review") {
      reviewRegionRef.current?.focus({ preventScroll: true });
      reviewRegionRef.current?.scrollIntoView({ block: "nearest", behavior: "auto" });
      return;
    }
    if (target === "trigger" && previous) {
      actionButtonRefs.current.get(previous.action)?.focus({ preventScroll: true });
      return;
    }
    if (target === "card") cardRef.current?.focus({ preventScroll: true });
  }, [decisionBusy, selection, verificationId]);

  const setActionButtonRef = useCallback(
    (action: ReviewerVerificationAction, button: HTMLButtonElement | null) => {
      if (button) actionButtonRefs.current.set(action, button);
      else actionButtonRefs.current.delete(action);
    },
    [],
  );

  return {
    cardRef,
    recordHeadingId,
    reviewRegionId,
    reviewRegionRef,
    setActionButtonRef,
  };
}

export function reviewerFocusTarget(
  previous: ReviewerSelection | null,
  current: ReviewerSelection | null,
  verificationId: string,
  decisionBusy: boolean,
): ReviewerFocusTarget {
  const previousBelongsToRecord = previous?.verificationId === verificationId;
  const currentBelongsToRecord = current?.verificationId === verificationId;
  if (
    currentBelongsToRecord &&
    (!previousBelongsToRecord || previous?.action !== current?.action)
  ) {
    return "review";
  }
  if (previousBelongsToRecord && !currentBelongsToRecord) {
    return decisionBusy ? "card" : "trigger";
  }
  return null;
}
