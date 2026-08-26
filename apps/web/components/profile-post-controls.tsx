"use client";

import { Ban, LoaderCircle, RefreshCw, UserPlus, UserRoundCheck } from "lucide-react";
import { SignInButton } from "@clerk/nextjs";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { buildProfileActionReturnPath } from "@/lib/auth-return-intent";
import { beginLogicalMutationAttempt, claimLogicalMutation, settleLogicalMutationAttempt, type LogicalMutationAttempt, type LogicalMutationClaimSlot } from "@/lib/logical-mutation";
import { blockProfileContent, followProfile, getProfilePostControls, presentPostsError, unblockProfileContent, unfollowProfile, type ProfilePostControlState } from "@/lib/posts-api";

export function ProfilePostControls({ handle }: { handle: string }) {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const returnPath = buildProfileActionReturnPath(handle, "follow");
  const subjectRef = useRef<string | null>(subject);
  subjectRef.current = subject;
  const isSubjectCurrent = useCallback(() => subjectRef.current === subject, [subject]);

  if (!configured) return null;
  if (!isLoaded) return <p role="status" className="mt-5 text-sm text-mist"><LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />Checking private follow controls…</p>;
  if (!isSignedIn || !subject) return <section className="mt-5 rounded-xl border border-white/10 bg-black/15 p-4"><p className="text-sm leading-6 text-mist">Sign in as a human to follow this profile or hide its posts from your private feed. Nothing creates a public follower graph. After authentication, you will return here to choose the action.</p><div className="mt-3 flex flex-wrap items-center gap-4">{returnPath && <SignInButton mode="modal" forceRedirectUrl={returnPath} signUpForceRedirectUrl={returnPath}><button type="button" className="inline-flex min-h-11 items-center rounded-full bg-acid px-4 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Sign in to continue</button></SignInButton>}<Link href="/feed" className="inline-flex min-h-11 items-center text-sm font-semibold text-acid underline-offset-4 hover:underline">Open private feed</Link></div></section>;

  return <AuthenticatedProfilePostControls key={`${subject}:${handle}`} handle={handle} subject={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} />;
}

function AuthenticatedProfilePostControls({ handle, subject, getToken, isSubjectCurrent }: { handle: string; subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean }) {
  const [state, setState] = useState<ProfilePostControlState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"follow" | "block" | null>(null);
  const [notice, setNotice] = useState(""); const followAttemptRef = useRef<LogicalMutationAttempt | null>(null); const blockAttemptRef = useRef<LogicalMutationAttempt | null>(null); const mutationClaimSlotRef = useRef<LogicalMutationClaimSlot>({ current: null });
  const load = useCallback(async () => {
    if (!isSubjectCurrent()) return;
    setState(null); setLoading(true); setNotice("");
    try {
      const next = await getProfilePostControls(handle, getToken, isSubjectCurrent);
      if (isSubjectCurrent()) setState(next);
    } catch (error) {
      if (isSubjectCurrent()) setNotice(presentPostsError(error));
    } finally {
      if (isSubjectCurrent()) setLoading(false);
    }
  }, [getToken, handle, isSubjectCurrent]);
  useEffect(() => { void load(); }, [load]);

  async function toggleFollow() {
    if (!state || busy || !isSubjectCurrent()) return;
    const requestSubject = subject;
    const requestFollowing = state.following;
    const claim = claimLogicalMutation(mutationClaimSlotRef.current);
    if (!claim) return;
    const requestIsCurrent = () => isSubjectCurrent() && claim.isCurrent();
    setBusy("follow"); setNotice("");
    try {
      followAttemptRef.current = beginLogicalMutationAttempt(followAttemptRef.current, requestSubject, { operation: requestFollowing ? "unfollow-profile" : "follow-profile", handle }); const attempt = followAttemptRef.current;
      if (requestFollowing) await unfollowProfile(handle, getToken, requestIsCurrent, attempt.idempotencyKey); else await followProfile(handle, getToken, requestIsCurrent, attempt.idempotencyKey);
      if (!requestIsCurrent()) return;
      followAttemptRef.current = null;
      setState((current) => current ? { ...current, following: !requestFollowing } : current);
      setNotice(requestFollowing ? "Stopped following this profile. Its posts are no longer pulled into your private feed." : "Following this profile. Its new posts can appear in your private chronological feed.");
    } catch (error) {
      if (!requestIsCurrent()) return;
      if (followAttemptRef.current) followAttemptRef.current = settleLogicalMutationAttempt(followAttemptRef.current, error);
      setNotice(followAttemptRef.current ? "The follow action may have completed. Retry the unchanged action to recover the same result. " + presentPostsError(error) : presentPostsError(error));
    } finally {
      if (requestIsCurrent()) { claim.release(); setBusy(null); }
    }
  }

  async function toggleBlock() {
    if (!state || busy || (!state.contentBlocked && !window.confirm(`Hide @${handle}'s posts from your private feed and signed-in archives? This also removes follows in both directions.`)) || !isSubjectCurrent()) return;
    const requestSubject = subject;
    const requestBlocked = state.contentBlocked;
    const claim = claimLogicalMutation(mutationClaimSlotRef.current);
    if (!claim) return;
    const requestIsCurrent = () => isSubjectCurrent() && claim.isCurrent();
    setBusy("block"); setNotice("");
    try {
      blockAttemptRef.current = beginLogicalMutationAttempt(blockAttemptRef.current, requestSubject, { operation: requestBlocked ? "unblock-profile" : "block-profile", handle }); const attempt = blockAttemptRef.current;
      if (requestBlocked) await unblockProfileContent(handle, getToken, requestIsCurrent, attempt.idempotencyKey); else await blockProfileContent(handle, getToken, requestIsCurrent, attempt.idempotencyKey);
      if (!requestIsCurrent()) return;
      blockAttemptRef.current = null;
      setState(requestBlocked ? { following: false, contentBlocked: false } : { following: false, contentBlocked: true });
      setNotice(requestBlocked ? "Restored this profile's posts to your private feed and signed-in archives." : "This profile's posts are hidden from your private feed and signed-in archives.");
    } catch (error) {
      if (!requestIsCurrent()) return;
      if (blockAttemptRef.current) blockAttemptRef.current = settleLogicalMutationAttempt(blockAttemptRef.current, error);
      setNotice(blockAttemptRef.current ? "The content-control action may have completed. Retry the unchanged action to recover the same result. " + presentPostsError(error) : presentPostsError(error));
    } finally {
      if (requestIsCurrent()) { claim.release(); setBusy(null); }
    }
  }

  return <section aria-labelledby="profile-post-controls-title" className="mt-5 rounded-2xl border border-white/10 bg-black/15 p-4"><h2 id="profile-post-controls-title" className="text-sm font-semibold text-white">Private post controls</h2><p className="mt-2 text-sm leading-6 text-mist">Following and content blocks are human-only and private. They do not create counts, presence, rankings, or a public social graph.</p>{loading && <p role="status" className="mt-3 text-sm text-mist"><LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />Loading your private state…</p>}{notice && <p role="status" className="mt-3 text-sm leading-6 text-mist">{notice}</p>}{!loading && !state && <Button variant="secondary" className="mt-4" disabled={busy !== null} onClick={() => void load()}><RefreshCw className="size-4" aria-hidden />Retry private state</Button>}{state && <div className="mt-4 flex flex-wrap gap-2"><Button variant="secondary" disabled={busy !== null || state.contentBlocked} onClick={() => void toggleFollow()}>{busy === "follow" ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : state.following ? <UserRoundCheck className="size-4" aria-hidden /> : <UserPlus className="size-4" aria-hidden />}{state.following ? "Unfollow" : "Follow profile"}</Button><Button variant="danger" disabled={busy !== null} onClick={() => void toggleBlock()}>{busy === "block" && <LoaderCircle className="size-4 animate-spin" aria-hidden />}{!busy && <Ban className="size-4" aria-hidden />}{state.contentBlocked ? "Restore post visibility" : "Hide this profile's posts"}</Button></div>}</section>;
}
