"use client";

import { ArrowLeft, LoaderCircle, LockKeyhole, Send } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { MarkdownPreview } from "@/components/markdown-preview";
import { NetworkNotice } from "@/components/network-notice";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import {
  MESSAGE_MAX_LENGTH,
  appendCursorPage,
  authSubjectIsCurrent,
  listMessagesForSubject,
  presentSocialError,
  sendMessage,
  type SocialMessage,
} from "@/lib/social-api";

export type ConversationOperationClaim = {
  id: number;
  scope: string;
  generation: number;
  kind: "primary" | "cursor" | "send";
};

export type ConversationReadCoordinator = {
  scope: string;
  generation: number;
  nextClaimId: number;
  primaryClaimId: number | null;
  interactionClaimId: number | null;
};

function conversationScope(subject: string, conversationId: string): string {
  return `${subject.length}:${subject}${conversationId.length}:${conversationId}`;
}

export function createConversationReadCoordinator(
  subject: string,
  conversationId: string,
): ConversationReadCoordinator {
  return {
    scope: conversationScope(subject, conversationId),
    generation: 0,
    nextClaimId: 0,
    primaryClaimId: null,
    interactionClaimId: null,
  };
}

export function resetConversationReadCoordinator(
  coordinator: ConversationReadCoordinator,
  subject: string,
  conversationId: string,
): void {
  coordinator.scope = conversationScope(subject, conversationId);
  coordinator.generation += 1;
  coordinator.primaryClaimId = null;
  coordinator.interactionClaimId = null;
}

function claimConversationOperation(
  coordinator: ConversationReadCoordinator,
  subject: string,
  conversationId: string,
  kind: ConversationOperationClaim["kind"],
): ConversationOperationClaim | null {
  if (coordinator.scope !== conversationScope(subject, conversationId)) {
    return null;
  }
  if (kind === "primary") {
    if (coordinator.primaryClaimId !== null) return null;
    coordinator.generation += 1;
  } else if (
    coordinator.primaryClaimId !== null ||
    coordinator.interactionClaimId !== null
  ) {
    return null;
  }
  const id = coordinator.nextClaimId + 1;
  coordinator.nextClaimId = id;
  if (kind === "primary") coordinator.primaryClaimId = id;
  else coordinator.interactionClaimId = id;
  return { id, scope: coordinator.scope, generation: coordinator.generation, kind };
}

export function claimConversationPrimary(
  coordinator: ConversationReadCoordinator,
  subject: string,
  conversationId: string,
): ConversationOperationClaim | null {
  return claimConversationOperation(coordinator, subject, conversationId, "primary");
}

export function claimConversationCursor(
  coordinator: ConversationReadCoordinator,
  subject: string,
  conversationId: string,
): ConversationOperationClaim | null {
  return claimConversationOperation(coordinator, subject, conversationId, "cursor");
}

export function claimConversationSend(
  coordinator: ConversationReadCoordinator,
  subject: string,
  conversationId: string,
): ConversationOperationClaim | null {
  return claimConversationOperation(coordinator, subject, conversationId, "send");
}

export function isCurrentConversationRead(
  coordinator: ConversationReadCoordinator,
  claim: ConversationOperationClaim,
): boolean {
  return coordinator.scope === claim.scope && coordinator.generation === claim.generation;
}

export function releaseConversationOperation(
  coordinator: ConversationReadCoordinator,
  claim: ConversationOperationClaim,
): boolean {
  if (coordinator.scope !== claim.scope) return false;
  if (claim.kind === "primary" && coordinator.primaryClaimId === claim.id) {
    coordinator.primaryClaimId = null;
    return true;
  }
  if (claim.kind !== "primary" && coordinator.interactionClaimId === claim.id) {
    coordinator.interactionClaimId = null;
    return true;
  }
  return false;
}

export async function releaseSendClaimBeforeRefresh(
  coordinator: ConversationReadCoordinator,
  claim: ConversationOperationClaim,
  isCurrent: () => boolean,
  reload: () => Promise<void>,
  onReleased: () => void,
): Promise<boolean> {
  const released = releaseConversationOperation(coordinator, claim);
  if (released) onReleased();
  if (!released || !isCurrent()) return false;
  await reload();
  return true;
}

export function ConversationThread({
  conversationId,
}: {
  conversationId: string;
}) {
  const { configured, isLoaded, isSignedIn, subject, getToken } =
    useConnectmdAuth();
  if (!configured || !isLoaded || !isSignedIn || !subject)
    return (
      <main className="mx-auto max-w-4xl px-5 py-16 lg:px-8">
        <h1 className="font-display text-4xl font-semibold text-white">
          Private conversation
        </h1>
        <p role="status" className="mt-4 text-mist">
          {!configured
            ? "This deployment has no signed-in human messaging configured."
            : !isLoaded
              ? "Checking your signed-in session…"
              : "Sign in as a human to open a private conversation."}
        </p>
      </main>
    );
  return (
    <AuthenticatedThread
      key={`${subject}:${conversationId}`}
      subject={subject}
      conversationId={conversationId}
      getToken={getToken}
    />
  );
}

function AuthenticatedThread({
  subject,
  conversationId,
  getToken,
}: {
  subject: string;
  conversationId: string;
  getToken: ReturnType<typeof useConnectmdAuth>["getToken"];
}) {
  const subjectRef = useRef<string | null>(subject);
  subjectRef.current = subject;
  const conversationRef = useRef(conversationId);
  conversationRef.current = conversationId;
  const [messages, setMessages] = useState<SocialMessage[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "loaded" | "error">("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const messagesRef = useRef(messages);
  const loadStateRef = useRef(loadState);
  const deliveredCursorsRef = useRef(new Set<string>());
  const readCoordinatorRef = useRef(
    createConversationReadCoordinator(subject, conversationId),
  );
  const sendAttemptRef = useRef<LogicalMutationAttempt | null>(null);
  messagesRef.current = messages;
  loadStateRef.current = loadState;
  const current = useCallback(
    (requestSubject: string) =>
      authSubjectIsCurrent(subjectRef.current, requestSubject),
    [],
  );
  const currentConversation = useCallback(
    () => conversationRef.current === conversationId,
    [conversationId],
  );
  const currentRequest = useCallback(
    (requestSubject: string) =>
      current(requestSubject) && currentConversation(),
    [current, currentConversation],
  );
  useEffect(
    () => () => {
      subjectRef.current = null;
    },
    [],
  );
  useEffect(() => {
    resetConversationReadCoordinator(
      readCoordinatorRef.current,
      subject,
      conversationId,
    );
    setMessages([]);
    setNextCursor(null);
    setBusy(false);
    setDraft("");
    setNotice(null);
    setLoadState("loading");
    setLoadError(null);
    sendAttemptRef.current = null;
    deliveredCursorsRef.current = new Set();
  }, [conversationId, subject]);
  const load = useCallback(
    async (cursor: string | null = null) => {
      const requestSubject = subject;
      if (cursor && loadStateRef.current !== "loaded") return;
      const coordinator = readCoordinatorRef.current;
      const claim = cursor
        ? claimConversationCursor(coordinator, subject, conversationId)
        : claimConversationPrimary(coordinator, subject, conversationId);
      if (!claim) return;
      const currentRead = () =>
        currentRequest(requestSubject) &&
        isCurrentConversationRead(coordinator, claim);
      if (cursor && deliveredCursorsRef.current.has(cursor)) {
        releaseConversationOperation(coordinator, claim);
        if (currentRead()) {
          setNextCursor(null);
          setNotice("This conversation returned a cursor that did not advance. Loaded messages remain available.");
        }
        return;
      }
      if (cursor) {
        setBusy(true);
      } else {
        setLoadState("loading");
        setLoadError(null);
      }
      try {
        const page = await listMessagesForSubject(
          conversationId,
          getToken,
          currentRead,
          cursor,
        );
        if (!currentRead()) return;
        if (!cursor) {
          setMessages(page.items);
          setNextCursor(page.nextCursor);
          setLoadState("loaded");
          setLoadError(null);
          deliveredCursorsRef.current = new Set();
        } else {
          const delivered = new Set(deliveredCursorsRef.current);
          delivered.add(cursor);
          deliveredCursorsRef.current = delivered;
          const next = appendCursorPage(
            messagesRef.current,
            page,
            cursor,
            delivered,
          );
          setMessages(next.items);
          setNextCursor(next.nextCursor);
          if (next.cursorDidNotProgress) {
            setNotice(
              "This conversation returned a cursor that did not advance. Loaded messages remain available.",
            );
            return;
          }
        }
        setNotice(null);
      } catch (error) {
        if (currentRead()) {
          if (cursor) setNotice(presentSocialError(error));
          else {
            setLoadState("error");
            setLoadError(presentSocialError(error));
          }
        }
      } finally {
        const released = releaseConversationOperation(coordinator, claim);
        if (
          claim.kind !== "primary" &&
          released &&
          currentRequest(requestSubject) &&
          coordinator.interactionClaimId === null
        ) {
          setBusy(false);
        }
      }
    },
    [conversationId, currentRequest, getToken, subject],
  );
  useEffect(() => {
    void load();
  }, [load]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (loadState !== "loaded" || !draft.trim() || busy) return;
    const requestSubject = subject;
    if (!currentRequest(requestSubject)) return;
    const coordinator = readCoordinatorRef.current;
    const claim = claimConversationSend(coordinator, subject, conversationId);
    if (!claim) return;
    const currentSend = () =>
      currentRequest(requestSubject) &&
      isCurrentConversationRead(coordinator, claim);
    setBusy(true);
    let sendClaimReleased = false;
    try {
      sendAttemptRef.current = beginLogicalMutationAttempt(sendAttemptRef.current, requestSubject, { operation: "send-message", conversationId, markdown: draft.trim() });
      const attempt = sendAttemptRef.current;
      await sendMessage(conversationId, draft.trim(), getToken, () =>
        currentSend(),
        attempt.idempotencyKey,
      );
      if (!currentSend()) return;
      sendAttemptRef.current = null;
      setDraft("");
      const refreshed = await releaseSendClaimBeforeRefresh(
        coordinator,
        claim,
        () => currentRequest(requestSubject),
        load,
        () => { sendClaimReleased = true; },
      );
      if (!refreshed) return;
    } catch (error) {
      sendAttemptRef.current = settleLogicalMutationAttempt(sendAttemptRef.current!, error); if (currentSend()) setNotice(sendAttemptRef.current ? "The message may have been sent. Retry the unchanged message to recover the same result. " + presentSocialError(error) : presentSocialError(error));
    } finally {
      if (!sendClaimReleased) sendClaimReleased = releaseConversationOperation(coordinator, claim);
      if (
        sendClaimReleased &&
        currentRequest(requestSubject) &&
        coordinator.interactionClaimId === null
      ) {
        setBusy(false);
      }
    }
  };
  return (
    <main className="mx-auto max-w-4xl px-5 py-8 pb-16 lg:px-8">
      <Link
        href="/network"
        className="-mx-2 inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm text-mist transition hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Private network
      </Link>
      <section className="mt-6 rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6">
        <div className="flex gap-3">
          <LockKeyhole className="mt-0.5 size-5 text-acid" aria-hidden />
          <div>
            <h1 className="text-2xl font-semibold text-white">
              Private conversation
            </h1>
            <p className="mt-1 text-sm leading-6 text-mist">
              Chronological, human-only Markdown messages. There are no public
              counters, presence, external relay, or read receipts.
            </p>
          </div>
        </div>
        <NetworkNotice label="This private conversation" />
        {notice && (
          <p
            role="status"
            className="mt-5 rounded-xl border border-white/10 bg-black/15 p-3 text-sm leading-6 text-mist"
          >
            {notice}
          </p>
        )}
        {loadState === "loading" && messages.length === 0 ? (
          <p
            role="status"
            className="mt-8 inline-flex items-center gap-2 text-sm text-mist"
          >
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
            Loading private messages
          </p>
        ) : loadState === "error" && messages.length === 0 ? (
          <PrivateLoadFailure
            label="Private messages could not be loaded"
            error={loadError ?? "The conversation is temporarily unavailable."}
            disabled={busy}
            onRetry={() => void load()}
          />
        ) : loadState === "loaded" && messages.length === 0 ? (
          <div className="mt-8 rounded-xl border border-dashed border-white/15 p-7 text-center">
            <h2 className="font-semibold text-white">No messages yet</h2>
            <p className="mt-2 text-sm text-mist">
              Write the first message as the signed-in human.
            </p>
          </div>
        ) : (
          <ol className="mt-7 space-y-4" aria-label="Chronological messages">
            {messages.map((message) => (
              <li
                key={message.id}
                className={`rounded-2xl border p-4 sm:p-5 ${message.direction === "sent" ? "border-acid/25 bg-acid/[.06]" : "border-white/10 bg-black/15"}`}
              >
                <article>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold text-white">
                      {message.direction === "sent"
                        ? "You"
                        : "Other participant"}
                    </h2>
                    <time
                      className="text-xs text-mist"
                      dateTime={message.createdAt}
                    >
                      {formatDate(message.createdAt)}
                    </time>
                  </div>
                  <MarkdownPreview
                    markdown={message.markdown}
                    className="mt-4 text-sm"
                    headingOffset={2}
                  />
                </article>
              </li>
            ))}
            </ol>
          )}
        {loadState === "loading" && messages.length > 0 && (
          <p role="status" className="mt-5 text-sm text-mist">
            <LoaderCircle className="mr-2 inline size-4 animate-spin" aria-hidden />
            Refreshing private messages
          </p>
        )}
        {loadState === "error" && messages.length > 0 && (
          <PrivateLoadFailure
            label="Private messages could not be refreshed"
            error={loadError ?? "The conversation is temporarily unavailable."}
            disabled={busy}
            onRetry={() => void load()}
          />
        )}
        {nextCursor && (
          <Button
            variant="ghost"
            className="mt-5"
            disabled={busy || loadState !== "loaded"}
            onClick={() => void load(nextCursor)}
          >
            {busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
            Load newer messages
          </Button>
        )}
        <form
          className="mt-8 border-t border-white/10 pt-6"
          onSubmit={(event) => void submit(event)}
        >
          <label className="block text-sm font-semibold text-white">
            Message in Markdown
            <Textarea
              className="mt-2"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              minLength={1}
              maxLength={MESSAGE_MAX_LENGTH}
              required
              disabled={busy || loadState !== "loaded"}
              placeholder="Write a private message."
              aria-describedby="message-privacy"
            />
          </label>
          <p id="message-privacy" className="mt-2 text-xs leading-5 text-mist">
            Sent only through this admitted conversation; images and raw HTML
            are blocked in the rendered thread.
          </p>
          <Button
            className="mt-4"
            type="submit"
            disabled={busy || loadState !== "loaded" || !draft.trim()}
          >
            {busy && (
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
            )}
            <Send className="size-4" aria-hidden />
            Send human message
          </Button>
        </form>
      </section>
    </main>
  );
}

function PrivateLoadFailure({ label, error, disabled, onRetry }: { label: string; error: string; disabled: boolean; onRetry: () => void }) {
  return (
    <div role="alert" className="mt-8 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4">
      <p className="font-semibold text-amber-50">{label}</p>
      <p className="mt-1 text-sm leading-6 text-amber-100/85">{error}</p>
      <Button variant="secondary" className="mt-3" disabled={disabled} onClick={onRetry}>Retry</Button>
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}
