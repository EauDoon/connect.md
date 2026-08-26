"use client";

import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { ProfessionalPostCard } from "@/components/professional-post-card";
import { listProfilePosts, listProfilePostsForSubject, presentPostsError, type PostPage, type ProfessionalPost } from "@/lib/posts-api";

type ArchiveRequest = { cursor: string | null; append: boolean };
const alwaysCurrent = () => true;

export function mergePostsById(existing: ProfessionalPost[], incoming: ProfessionalPost[]) {
  const known = new Set(existing.map((post) => post.id));
  return [...existing, ...incoming.filter((post) => { if (known.has(post.id)) return false; known.add(post.id); return true; })];
}

export function ProfilePostArchive({ handle, initialPage }: { handle: string; initialPage: PostPage | null }) {
  const { subject, getToken, isLoaded, isSignedIn } = useConnectmdAuth();
  const subjectRef = useRef<string | null>(subject);
  subjectRef.current = subject;
  const isSubjectCurrent = useCallback(() => subjectRef.current === subject, [subject]);

  if (!isLoaded) return <ArchivePage handle={handle} loading={initialPage === null} posts={initialPage?.posts ?? []} cursor={initialPage?.nextCursor ?? null} error="" onLoad={() => undefined} />;
  if (isSignedIn && subject) return <AuthenticatedProfilePostArchive key={`authenticated:${subject}:${handle}`} handle={handle} getToken={getToken} isSubjectCurrent={isSubjectCurrent} />;
  return <AnonymousProfilePostArchive key={`anonymous:${handle}`} handle={handle} initialPage={initialPage} />;
}

function AuthenticatedProfilePostArchive({ handle, getToken, isSubjectCurrent }: { handle: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean }) {
  const fetchPage = useCallback((cursor: string | null) => listProfilePostsForSubject(handle, getToken, isSubjectCurrent, cursor), [getToken, handle, isSubjectCurrent]);
  return <ProfilePostArchiveList handle={handle} fetchPage={fetchPage} isSubjectCurrent={isSubjectCurrent} />;
}

function AnonymousProfilePostArchive({ handle, initialPage }: { handle: string; initialPage: PostPage | null }) {
  const fetchPage = useCallback((cursor: string | null) => listProfilePosts(handle, undefined, cursor), [handle]);
  return <ProfilePostArchiveList handle={handle} fetchPage={fetchPage} isSubjectCurrent={alwaysCurrent} initialPage={initialPage} />;
}

function ProfilePostArchiveList({ handle, fetchPage, isSubjectCurrent, initialPage = null }: { handle: string; fetchPage: (cursor: string | null) => Promise<PostPage>; isSubjectCurrent: () => boolean; initialPage?: PostPage | null }) {
  const [posts, setPosts] = useState<ProfessionalPost[]>(initialPage?.posts ?? []);
  const [cursor, setCursor] = useState<string | null>(initialPage?.nextCursor ?? null);
  const [loading, setLoading] = useState(initialPage === null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState<ArchiveRequest | null>(null);
  const inFlightRef = useRef<string | null>(null);
  const deliveredCursorsRef = useRef(new Set<string>(initialPage ? ["__first_page__"] : []));
  const mountedRef = useRef(false);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);
  const stillCurrent = useCallback(() => mountedRef.current && isSubjectCurrent(), [isSubjectCurrent]);

  const load = useCallback(async (nextCursor: string | null, append: boolean) => {
    const requestKey = nextCursor ?? "__first_page__";
    if (!stillCurrent() || inFlightRef.current !== null) return;
    if (append && deliveredCursorsRef.current.has(requestKey)) {
      setCursor(null); setRetry(null); setError("The archive returned a cursor that did not advance. Already loaded posts remain visible.");
      return;
    }
    inFlightRef.current = requestKey;
    setLoading(true); setError(""); setRetry(null);
    try {
      const page = await fetchPage(nextCursor);
      if (!stillCurrent()) return;
      const delivered = append ? new Set(deliveredCursorsRef.current) : new Set<string>();
      delivered.add(requestKey);
      const nonProgress = page.nextCursor !== null && delivered.has(page.nextCursor);
      deliveredCursorsRef.current = delivered;
      setPosts((current) => append ? mergePostsById(current, page.posts) : mergePostsById([], page.posts));
      setCursor(nonProgress ? null : page.nextCursor);
      if (nonProgress) setError("The archive returned a cursor that did not advance. Already loaded posts remain visible.");
    } catch (cause) {
      if (stillCurrent()) {
        setError(presentPostsError(cause));
        setRetry({ cursor: nextCursor, append });
      }
    } finally {
      if (stillCurrent()) setLoading(false);
      if (inFlightRef.current === requestKey) inFlightRef.current = null;
    }
  }, [fetchPage, stillCurrent]);
  useEffect(() => { if (initialPage === null) void load(null, false); }, [initialPage, load]);

  return <ArchivePage handle={handle} loading={loading} posts={posts} cursor={cursor} error={error} retry={retry} onLoad={load} />;
}

function ArchivePage({ handle, loading, posts, cursor, error, retry, onLoad }: { handle: string; loading: boolean; posts: ProfessionalPost[]; cursor: string | null; error: string; retry?: ArchiveRequest | null; onLoad: (cursor: string | null, append: boolean) => void }) {
  const retryInitial = retry && !retry.append;
  const retryAppend = retry?.append ? retry : null;
  const nextCursor = retryAppend?.cursor ?? cursor;
  return <main className="mx-auto max-w-5xl px-5 py-10 lg:px-8 lg:py-14"><div className="flex flex-wrap items-end justify-between gap-4"><div className="min-w-0"><p className="eyebrow">Public profile archive</p><h1 className="mt-3 break-anywhere font-display text-4xl font-semibold tracking-[-.05em] text-white sm:text-6xl">Posts by @{handle}</h1><p className="mt-4 max-w-2xl break-anywhere text-base leading-7 text-mist">Published professional posts are immutable public Markdown. This is a profile archive, not a global timeline.</p></div><Link href={`/p/${encodeURIComponent(handle)}`} className="inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white">View profile</Link></div>{loading && posts.length === 0 && <p role="status" className="mt-7 text-sm text-mist"><LoaderCircle className="mr-2 inline size-4 animate-spin text-acid" aria-hidden />Loading published posts…</p>}{error && <p role="alert" className="mt-7 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4 text-sm leading-6 text-amber-100/90">{error}</p>}{!loading && posts.length === 0 && !error && <p className="mt-7 rounded-2xl border border-dashed border-white/15 p-7 text-center text-sm leading-6 text-mist">No published professional posts are available from this profile.</p>}{posts.length > 0 && <ol className="mt-7 space-y-5">{posts.map((post) => <li key={post.id}><ProfessionalPostCard post={post} /></li>)}</ol>}{retryInitial && <button type="button" aria-busy={loading || undefined} disabled={loading} onClick={() => onLoad(null, false)} className="mt-7 inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white disabled:opacity-50">{loading && <LoaderCircle className="mr-2 size-4 animate-spin" aria-hidden />} Retry archive</button>}{nextCursor && <button type="button" aria-busy={loading || undefined} disabled={loading} onClick={() => onLoad(nextCursor, true)} className="mt-7 inline-flex min-h-11 items-center rounded-full border border-white/15 px-5 text-sm font-semibold text-white disabled:opacity-50">{loading && <LoaderCircle className="mr-2 size-4 animate-spin" aria-hidden />}{retryAppend ? "Retry loading older posts" : "Load more posts"}</button>}</main>;
}
