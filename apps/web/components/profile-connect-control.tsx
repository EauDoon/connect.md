"use client";

import { Link2, LoaderCircle, Send } from "lucide-react";
import { SignInButton } from "@clerk/nextjs";
import Link from "next/link";
import React, { useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { buildProfileActionReturnPath } from "@/lib/auth-return-intent";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import { authSubjectIsCurrent, createConnectionRequest, presentSocialError } from "@/lib/social-api";

export function ProfileConnectControl({ handle }: { handle: string }) {
  const { configured, isLoaded, isSignedIn, subject, getToken } = useConnectmdAuth();
  const returnPath = buildProfileActionReturnPath(handle, "connect");
  const subjectRef = useRef<string | null>(subject); subjectRef.current = subject;
  if (!configured) return <p className="mt-5 text-sm leading-6 text-mist">Private human connection controls are unavailable in this deployment.</p>;
  if (!isLoaded) return <p role="status" className="mt-5 inline-flex items-center gap-2 text-sm text-mist"><LoaderCircle className="size-4 animate-spin" aria-hidden />Checking your signed-in session…</p>;
  if (!isSignedIn || !subject) return <div className="mt-5 rounded-xl border border-white/10 bg-black/15 p-4"><p className="text-sm leading-6 text-mist">Sign in as a human to request a private connection. Agents and API keys cannot use this control. After authentication, you will return here to confirm the request.</p><div className="mt-3 flex flex-wrap items-center gap-4">{returnPath && <SignInButton mode="modal" forceRedirectUrl={returnPath} signUpForceRedirectUrl={returnPath}><button type="button" className="inline-flex min-h-11 items-center rounded-full bg-acid px-4 text-sm font-bold text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Sign in to continue</button></SignInButton>}<Link href="/network" className="inline-flex min-h-11 items-center text-sm font-semibold text-acid underline-offset-4 hover:underline">Open private network</Link></div></div>;
  return <AuthenticatedProfileConnectControl key={`${subject}:${handle}`} handle={handle} subject={subject} getToken={getToken} isSubjectCurrent={() => authSubjectIsCurrent(subjectRef.current, subject)} />;
}

function AuthenticatedProfileConnectControl({ handle, subject, getToken, isSubjectCurrent }: { handle: string; subject: string; getToken: ReturnType<typeof useConnectmdAuth>["getToken"]; isSubjectCurrent: () => boolean }) {
  const [messagingRequested, setMessagingRequested] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const attemptRef = useRef<LogicalMutationAttempt | null>(null);

  const submit = async () => {
    if (busy) return;
    const requestSubject = subject;
    const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent();
    if (!requestIsCurrent()) return;
    setBusy(true); setNotice(null);
    try {
      attemptRef.current = beginLogicalMutationAttempt(attemptRef.current, requestSubject, { operation: "connection-request", targetProfileHandle: handle, messagingRequested });
      const attempt = attemptRef.current;
      await createConnectionRequest(handle, messagingRequested, getToken, requestIsCurrent, attempt.idempotencyKey);
      if (!requestIsCurrent()) return;
      attemptRef.current = null;
      setNotice("Private connection request sent. Manage requests and connections in your network.");
    } catch (error) {
      if (!requestIsCurrent()) return;
      attemptRef.current = settleLogicalMutationAttempt(attemptRef.current!, error);
      setNotice(attemptRef.current ? "The connection request may have been recorded. Retry the unchanged request to recover the same result. " + presentSocialError(error) : presentSocialError(error));
    } finally {
      if (requestIsCurrent()) setBusy(false);
    }
  };

  return <section aria-labelledby="connect-title" className="mt-6 rounded-2xl border border-acid/20 bg-acid/[.06] p-4"><h2 id="connect-title" className="inline-flex items-center gap-2 text-sm font-semibold text-white"><Link2 className="size-4 text-acid" aria-hidden />Private connection</h2><p className="mt-2 text-sm leading-6 text-mist">Request a private connection with this public profile. Nothing is added to a public graph.</p>{notice && <p role="status" className="mt-3 rounded-xl border border-white/10 bg-black/15 p-3 text-sm leading-6 text-mist">{notice}</p>}<label className="mt-4 flex gap-3 text-sm leading-6 text-mist"><input type="checkbox" className="mt-1 size-4 accent-acid" checked={messagingRequested} onChange={(event) => setMessagingRequested(event.target.checked)} disabled={busy} /><span>Request messaging. The profile owner must explicitly consent before a private conversation can start.</span></label><div className="mt-4 flex flex-wrap gap-3"><Button disabled={busy} onClick={() => void submit()}>{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}<Send className="size-4" aria-hidden />Request connection</Button><Link href="/network" className="inline-flex min-h-11 items-center rounded-full px-4 text-sm font-semibold text-mist hover:text-white">Manage network</Link></div></section>;
}
