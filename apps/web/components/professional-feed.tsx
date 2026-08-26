"use client";

import { Clock3, LoaderCircle, Scale, UserMinus, UsersRound } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AsyncBoundaryMessage } from "@/components/async-boundary-message";
import { useConnectmdAuth } from "@/components/auth-provider";
import { PostComposer } from "@/components/post-composer";
import { ProfessionalPostCard } from "@/components/professional-post-card";
import { Button } from "@/components/ui/button";
import { beginLogicalMutationAttempt, claimLogicalMutation, settleLogicalMutationAttempt, type LogicalMutationAttempt, type LogicalMutationClaimSlot } from "@/lib/logical-mutation";
import { listFeedForSubject, listFollowsForSubject, presentPostsError, unfollowProfile, type ProfessionalPost, type ProfileFollow } from "@/lib/posts-api";

export function ProfessionalFeed() {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  const isSubjectCurrent = useCallback(() => subjectRef.current === subject, [subject]);
  if (!configured || !isLoaded || !isSignedIn || !subject) return <FeedGate configured={configured} loading={!isLoaded} />;
  return <AuthenticatedFeed key={subject} subject={subject} getToken={getToken} isSubjectCurrent={isSubjectCurrent} />;
}

function AuthenticatedFeed({ subject, getToken, isSubjectCurrent }: { subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean }) {
  const [posts, setPosts] = useState<ProfessionalPost[]>([]); const [feedCursor, setFeedCursor] = useState<string | null>(null); const [follows, setFollows] = useState<ProfileFollow[]>([]); const [followCursor, setFollowCursor] = useState<string | null>(null); const [feedLoadState, setFeedLoadState] = useState<"loading" | "loaded" | "error">("loading"); const [followLoadState, setFollowLoadState] = useState<"loading" | "loaded" | "error">("loading"); const [feedLoadError, setFeedLoadError] = useState(""); const [followLoadError, setFollowLoadError] = useState(""); const [busy, setBusy] = useState<string | null>(null); const [notice, setNotice] = useState(""); const [retryMore, setRetryMore] = useState<"feed" | "follows" | null>(null);
  const moreInFlightRef = useRef(new Set<"feed" | "follows">());
  const deliveredFeedCursorsRef = useRef(new Set<string>());
  const deliveredFollowCursorsRef = useRef(new Set<string>());
  const initialLoadInFlightRef = useRef(new Set<"feed" | "follows">());
  const unfollowAttemptsRef = useRef(new Map<string, LogicalMutationAttempt>());
  const mutationClaimSlotRef = useRef<LogicalMutationClaimSlot>({ current: null });
  const load = useCallback(async (target?: "feed" | "follows") => {
    if (!isSubjectCurrent()) return;
    const requested = (target ? [target] : ["feed", "follows"]) as Array<"feed" | "follows">;
    const pending = requested.filter((kind) => !initialLoadInFlightRef.current.has(kind));
    if (pending.length === 0) return;
    pending.forEach((kind) => initialLoadInFlightRef.current.add(kind));
    if (pending.includes("feed")) { setFeedLoadState("loading"); setFeedLoadError(""); }
    if (pending.includes("follows")) { setFollowLoadState("loading"); setFollowLoadError(""); }
    const results = await Promise.allSettled(pending.map((kind) => kind === "feed" ? listFeedForSubject(getToken, isSubjectCurrent) : listFollowsForSubject(getToken, isSubjectCurrent)));
    if (!isSubjectCurrent()) return;
    results.forEach((result, index) => {
      const kind = pending[index];
      initialLoadInFlightRef.current.delete(kind);
      if (kind === "feed") {
        if (result.status === "fulfilled" && "posts" in result.value) { setPosts(uniquePosts([], result.value.posts)); setFeedCursor(result.value.nextCursor); deliveredFeedCursorsRef.current = new Set(["__first_page__"]); setFeedLoadState("loaded"); }
        else if (result.status === "rejected") { setFeedLoadState("error"); setFeedLoadError(presentPostsError(result.reason)); }
      } else {
        if (result.status === "fulfilled" && "follows" in result.value) { setFollows(uniqueFollows([], result.value.follows)); setFollowCursor(result.value.nextCursor); deliveredFollowCursorsRef.current = new Set(["__first_page__"]); setFollowLoadState("loaded"); }
        else if (result.status === "rejected") { setFollowLoadState("error"); setFollowLoadError(presentPostsError(result.reason)); }
      }
    });
  }, [getToken, isSubjectCurrent]);
  useEffect(() => { void load(); }, [load]);
  const loading = feedLoadState === "loading" || followLoadState === "loading";

  async function loadMore(kind: "feed" | "follows") {
    const cursor = kind === "feed" ? feedCursor : followCursor;
    const delivered = kind === "feed" ? deliveredFeedCursorsRef : deliveredFollowCursorsRef;
    if (!cursor || !isSubjectCurrent() || moreInFlightRef.current.has(kind)) return;
    if (delivered.current.has(cursor)) {
      if (kind === "feed") setFeedCursor(null); else setFollowCursor(null);
      setRetryMore(null); setNotice("The feed returned a cursor that did not advance. Already loaded items remain visible.");
      return;
    }
    moreInFlightRef.current.add(kind);
    setBusy(kind); setNotice(""); setRetryMore(null);
    try {
      if (kind === "feed") {
        const page = await listFeedForSubject(getToken, isSubjectCurrent, cursor); if (!isSubjectCurrent()) return;
        const nextDelivered = new Set(delivered.current); nextDelivered.add(cursor); delivered.current = nextDelivered;
        const nonProgress = page.nextCursor !== null && nextDelivered.has(page.nextCursor);
        setPosts((current) => uniquePosts(current, page.posts)); setFeedCursor(nonProgress ? null : page.nextCursor);
        if (nonProgress) setNotice("The feed returned a cursor that did not advance. Already loaded items remain visible.");
      } else {
        const page = await listFollowsForSubject(getToken, isSubjectCurrent, cursor); if (!isSubjectCurrent()) return;
        const nextDelivered = new Set(delivered.current); nextDelivered.add(cursor); delivered.current = nextDelivered;
        const nonProgress = page.nextCursor !== null && nextDelivered.has(page.nextCursor);
        setFollows((current) => uniqueFollows(current, page.follows)); setFollowCursor(nonProgress ? null : page.nextCursor);
        if (nonProgress) setNotice("The feed returned a cursor that did not advance. Already loaded items remain visible.");
      }
    } catch (error) { if (isSubjectCurrent()) { setNotice(presentPostsError(error)); setRetryMore(kind); } }
    finally { moreInFlightRef.current.delete(kind); if (isSubjectCurrent()) setBusy(null); }
  }

  async function unfollow(follow: ProfileFollow) {
    if (busy || !window.confirm(`Stop following @${follow.profileHandle}?`) || !isSubjectCurrent()) return;
    const claim = claimLogicalMutation(mutationClaimSlotRef.current);
    if (!claim) return;
    const requestSubject = subject;
    const requestIsCurrent = () => isSubjectCurrent() && claim.isCurrent();
    setBusy(`unfollow:${follow.profileHandle}`); setNotice("");
    try {
      const slot = follow.profileHandle; const attempt = beginLogicalMutationAttempt(unfollowAttemptsRef.current.get(slot) ?? null, requestSubject, { operation: "unfollow-profile", handle: follow.profileHandle }); unfollowAttemptsRef.current.set(slot, attempt);
      await unfollowProfile(follow.profileHandle, getToken, requestIsCurrent, attempt.idempotencyKey);
      if (!requestIsCurrent()) return;
      unfollowAttemptsRef.current.delete(slot);
      setFollows((current) => current.filter((item) => item.profileHandle !== follow.profileHandle)); setPosts((current) => current.filter((post) => post.authorProfileHandle !== follow.profileHandle));
      setNotice(`Stopped following @${follow.profileHandle}.`);
    } catch (error) {
      if (!requestIsCurrent()) return;
      const slot = follow.profileHandle;
      const attempt = unfollowAttemptsRef.current.get(slot);
      if (attempt) {
        const next = settleLogicalMutationAttempt(attempt, error);
        if (next) unfollowAttemptsRef.current.set(slot, next); else unfollowAttemptsRef.current.delete(slot);
      }
      setNotice(unfollowAttemptsRef.current.has(slot) ? "The unfollow action may have completed. Retry the unchanged action to recover the same result. " + presentPostsError(error) : presentPostsError(error));
    }
    finally { if (requestIsCurrent()) { claim.release(); setBusy(null); } }
  }

  return <main className="mx-auto max-w-7xl px-5 py-10 pb-16 lg:px-8 lg:py-14"><section className="max-w-4xl"><p className="eyebrow">Private chronological feed</p><h1 className="mt-3 font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">Professional posts, in time order.</h1><p className="mt-4 max-w-3xl text-base leading-7 text-mist">Your feed contains your posts and posts from profiles you privately follow, newest first. It has no ranking, recommendations, counts, tracking, or presence—and it is never a public timeline.</p></section>{notice && <p role="status" className="mt-6 rounded-xl border border-white/10 bg-panel p-4 text-sm leading-6 text-mist">{notice}</p>}<div className="mt-8 grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]"><div className="space-y-6"><PostComposer onPublished={(post) => setPosts((current) => [post, ...current.filter((item) => item.id !== post.id)])} /><section aria-labelledby="feed-posts-title"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="eyebrow">Pulled posts</p><h2 id="feed-posts-title" className="mt-2 text-2xl font-semibold text-white">Strictly chronological</h2></div><Button variant="secondary" disabled={loading || busy !== null} onClick={() => void load()}>{loading && <LoaderCircle className="size-4 animate-spin" aria-hidden />} Refresh</Button></div>{feedLoadState === "loading" && posts.length === 0 ? <p role="status" className="mt-6 text-sm text-mist"><LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />Loading your private feed…</p> : feedLoadState === "error" && posts.length === 0 ? <PrivateLoadFailure label="Your private feed could not be loaded" error={feedLoadError} onRetry={() => void load("feed")} /> : feedLoadState === "loaded" && posts.length === 0 ? <p className="mt-6 rounded-2xl border border-dashed border-white/15 p-7 text-center text-sm leading-6 text-mist">No posts yet. Publish your first professional post or follow a public profile with an archive.</p> : <ol className="mt-6 space-y-5">{posts.map((post) => <li key={post.id}><ProfessionalPostCard post={post} /></li>)}</ol>}{feedLoadState === "error" && posts.length > 0 && <PrivateLoadFailure label="Your private feed could not be refreshed" error={feedLoadError} onRetry={() => void load("feed")} />}{feedCursor && <Button variant="secondary" className="mt-6" disabled={busy !== null} onClick={() => void loadMore("feed")}>{busy === "feed" && <LoaderCircle className="size-4 animate-spin" aria-hidden />}{retryMore === "feed" ? "Retry loading older posts" : "Load older posts"}</Button>}</section></div><aside className="h-fit space-y-5 xl:sticky xl:top-24"><section aria-labelledby="following-title" className="rounded-[1.5rem] border border-white/10 bg-panel p-5"><div className="flex items-center gap-2"><UsersRound className="size-5 text-acid" aria-hidden /><h2 id="following-title" className="font-semibold text-white">Private following</h2></div><p className="mt-2 text-sm leading-6 text-mist">Only you can view or manage these follows. Public profiles never expose follower counts or lists.</p>{followLoadState === "loading" && follows.length === 0 ? <AsyncBoundaryMessage className="mt-4 text-sm text-mist" loading>Loading follows…</AsyncBoundaryMessage> : followLoadState === "error" && follows.length === 0 ? <PrivateLoadFailure label="Private follows could not be loaded" error={followLoadError} onRetry={() => void load("follows")} /> : followLoadState === "loaded" && follows.length === 0 ? <p className="mt-4 text-sm leading-6 text-mist">You are not following any public profiles.</p> : <ul className="mt-4 space-y-2">{follows.map((follow) => <li key={follow.profileHandle} className="flex items-center justify-between gap-2 rounded-xl border border-white/10 px-3 py-3"><Link href={`/p/${encodeURIComponent(follow.profileHandle)}`} className="min-w-0 truncate text-sm font-semibold text-acid underline-offset-4 hover:underline">@{follow.profileHandle}</Link><Button variant="ghost" className="min-h-11 shrink-0 px-2 text-xs" disabled={busy !== null} onClick={() => void unfollow(follow)}>{busy === `unfollow:${follow.profileHandle}` ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <UserMinus className="size-4" aria-hidden />} Unfollow</Button></li>)}</ul>}{followLoadState === "error" && follows.length > 0 && <PrivateLoadFailure label="Private follows could not be refreshed" error={followLoadError} onRetry={() => void load("follows")} />}{followCursor && <Button variant="ghost" className="mt-3 px-2 text-xs" disabled={busy !== null} onClick={() => void loadMore("follows")}>{busy === "follows" && <LoaderCircle className="size-4 animate-spin" aria-hidden />}{retryMore === "follows" ? "Retry loading more" : "Load more"}</Button>}</section><section className="rounded-[1.5rem] border border-acid/20 bg-acid/[.05] p-5"><Scale className="size-5 text-acid" aria-hidden /><h2 className="mt-3 font-semibold text-white">Private post case status</h2><p className="mt-2 text-sm leading-6 text-mist">Review only the moderation status and explanation addressed to you, or submit an eligible appeal.</p><Link href="/moderation" className="mt-4 inline-flex min-h-11 items-center text-sm font-semibold text-acid underline-offset-4 hover:underline">Review post case status</Link></section><section className="rounded-[1.5rem] border border-white/10 bg-panel p-5"><Clock3 className="size-5 text-acid" aria-hidden /><h2 className="mt-3 font-semibold text-white">How this stays bounded</h2><p className="mt-2 text-sm leading-6 text-mist">Posts are immutable Markdown. Following is private and directed. Content blocks are managed from public profiles and suppress signed-in feeds and archives in either direction.</p></section></aside></div></main>;
}

function PrivateLoadFailure({ label, error, onRetry }: { label: string; error: string; onRetry: () => void }) { return <div role="alert" className="mt-4 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4"><p className="font-semibold text-amber-50">{label}</p><p className="mt-1 text-sm leading-6 text-amber-100/85">{error}</p><Button variant="secondary" className="mt-3" onClick={onRetry}>Retry</Button></div>; }

function uniquePosts(existing: ProfessionalPost[], incoming: ProfessionalPost[]) { const known = new Set(existing.map((post) => post.id)); return [...existing, ...incoming.filter((post) => { if (known.has(post.id)) return false; known.add(post.id); return true; })]; }
function uniqueFollows(existing: ProfileFollow[], incoming: ProfileFollow[]) { const known = new Set(existing.map((follow) => follow.profileHandle)); return [...existing, ...incoming.filter((follow) => { if (known.has(follow.profileHandle)) return false; known.add(follow.profileHandle); return true; })]; }

function FeedGate({ configured, loading }: { configured: boolean; loading: boolean }) { return <main className="mx-auto max-w-4xl px-5 py-10 lg:px-8 lg:py-14"><section className="rounded-[1.5rem] border border-white/10 bg-panel p-6"><h1 className="text-2xl font-semibold text-white">Private feed</h1><AsyncBoundaryMessage className="mt-3 text-sm leading-6 text-mist" loading={loading}>{loading ? "Checking your signed-in human session…" : configured ? "Sign in as a human to publish, follow profiles, and read your private chronological feed." : "Human authentication is not configured for this deployment."}</AsyncBoundaryMessage></section></main>; }
