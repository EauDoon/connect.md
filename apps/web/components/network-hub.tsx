"use client";

import {
  CircleUserRound,
  LoaderCircle,
  Send,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { useConnectmdAuth } from "@/components/auth-provider";
import { NetworkNotice } from "@/components/network-notice";
import {
  ConnectionsPanel,
  ConnectionRequestsPanel,
  ConversationsPanel,
  NotificationsPanel,
} from "@/components/network-panels";
import { usePrivateNetworkReads } from "@/components/private-network-reads";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ApiRequestError } from "@/lib/api";
import { beginLogicalMutationAttempt, settleLogicalMutationAttempt, type LogicalMutationAttempt } from "@/lib/logical-mutation";
import {
  authSubjectIsCurrent,
  blockConnection,
  createConnectionRequest,
  createConversation,
  decideConnectionRequest,
  markNotificationRead,
  presentSocialError,
  removeConnection,
  type Connection,
  type ConnectionRequest,
  type Conversation,
  type Notification,
  type NotificationHubAction,
} from "@/lib/social-api";

export { NetworkConversationCard } from "@/components/network-panels";
export {
  beginPrivateNetworkRead,
  createPrivateNetworkReadEpoch,
  finishPrivateNetworkRead,
  markPrivateNetworkReadReady,
  privateNetworkReadAllowsDependentAction,
  privateNetworkReadIsCurrent,
  type PrivateNetworkReadEpoch,
} from "@/components/private-network-reads";

export function NetworkHub() {
  const { configured, isLoaded, isSignedIn, subject, getToken } =
    useConnectmdAuth();
  if (!configured || !isLoaded || !isSignedIn || !subject)
    return <PrivateGate configured={configured} loaded={isLoaded} />;
  return (
    <AuthenticatedNetwork key={subject} subject={subject} getToken={getToken} />
  );
}

function AuthenticatedNetwork({
  subject,
  getToken,
}: {
  subject: string;
  getToken: ReturnType<typeof useConnectmdAuth>["getToken"];
}) {
  const router = useRouter();
  const subjectRef = useRef<string | null>(subject);
  subjectRef.current = subject;
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [handle, setHandle] = useState("");
  const [messagingRequested, setMessagingRequested] = useState(false);
  const busyRef = useRef<string | null>(null);
  const mutationAttemptsRef = useRef(new Map<string, LogicalMutationAttempt>());
  const current = useCallback(
    (requestSubject: string) => authSubjectIsCurrent(subjectRef.current, requestSubject),
    [],
  );
  const beginAttempt = (slot: string, requestSubject: string, intent: unknown) => {
    const attempt = beginLogicalMutationAttempt(mutationAttemptsRef.current.get(slot) ?? null, requestSubject, intent);
    mutationAttemptsRef.current.set(slot, attempt);
    return attempt;
  };
  const settleAttempt = (slot: string, attempt: LogicalMutationAttempt, error: unknown) => {
    const next = settleLogicalMutationAttempt(attempt, error);
    if (next) mutationAttemptsRef.current.set(slot, next); else mutationAttemptsRef.current.delete(slot);
    return next;
  };
  const beginBusy = (slot: string) => {
    if (busyRef.current !== null) return false;
    busyRef.current = slot;
    setBusy(slot);
    return true;
  };
  const endBusy = (slot: string) => {
    if (busyRef.current !== slot) return;
    busyRef.current = null;
    setBusy(null);
  };
  const {
    requests,
    connections,
    conversations,
    notifications,
    requestsRef,
    connectionsRef,
    conversationsRef,
    notificationsRef,
    requestCursor,
    connectionCursor,
    conversationCursor,
    notificationCursor,
    requestLoadState,
    connectionLoadState,
    conversationLoadState,
    notificationLoadState,
    requestLoadError,
    connectionLoadError,
    conversationLoadError,
    notificationLoadError,
    loading,
    refresh,
    loadRequests,
    loadConnections,
    loadConversations,
    loadNotifications,
    loadOlderRequests,
    loadOlderConnections,
    loadOlderConversations,
    loadOlderNotifications,
    setRequests,
    setConnections,
    setConversations,
    setNotifications,
    readEpoch,
    readIsCurrent,
    readAllowsDependentAction,
  } = usePrivateNetworkReads({ subject, getToken, current, beginBusy, endBusy, onNotice: setNotice });
  useEffect(() => {
    subjectRef.current = subject;
    return () => {
      subjectRef.current = null;
    };
  }, [subject]);
  const sendRequest = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!handle.trim()) return;
    const requestSubject = subject;
    if (!current(requestSubject)) return;
    const busySlot = "request";
    if (!beginBusy(busySlot)) return;
    setNotice(null);
    try {
      const attempt = beginAttempt("connection-request", requestSubject, { operation: "connection-request", targetProfileHandle: handle.trim().toLowerCase(), messagingRequested });
      await createConnectionRequest(
        handle.trim().toLowerCase(),
        messagingRequested,
        getToken,
        () => current(requestSubject),
        attempt.idempotencyKey,
      );
      if (!current(requestSubject)) return;
      mutationAttemptsRef.current.delete("connection-request");
      setHandle("");
      setMessagingRequested(false);
      setNotice(
        "Connection request sent. The profile target is not otherwise revealed here.",
      );
    } catch (error) {
      const attempt = mutationAttemptsRef.current.get("connection-request"); if (attempt) settleAttempt("connection-request", attempt, error);
      if (current(requestSubject)) setNotice(mutationAttemptsRef.current.has("connection-request") ? "The connection request may have been recorded. Retry the unchanged request to recover the same result. " + presentSocialError(error) : presentSocialError(error));
    } finally {
      if (current(requestSubject)) endBusy(busySlot);
    }
  };
  const decide = async (
    request: ConnectionRequest,
    action: "accept" | "reject" | "block",
    consent: boolean | null = null,
  ) => {
    if (
      (action === "reject" || action === "block") &&
      !window.confirm(
        `${action === "block" ? "Block" : "Reject"} this private request?`,
      )
    )
      return;
    const requestSubject = subject;
    if (
      !current(requestSubject) ||
      !readAllowsDependentAction("requests")
    ) return;
    if (!requestsRef.current.some((item) => item.id === request.id && item.status === "pending")) return;
    const requestEpoch = readEpoch("requests");
    const busySlot = `request:${request.id}`;
    if (!beginBusy(busySlot)) return;
    try {
      const attempt = beginAttempt(busySlot, requestSubject, { operation: "connection-decision", requestId: request.id, action, consent });
      const updated = await decideConnectionRequest(
        request.id,
        action,
        consent,
        getToken,
        () => current(requestSubject),
        attempt.idempotencyKey,
      );
      if (!current(requestSubject)) return;
      mutationAttemptsRef.current.delete(busySlot);
      if (!readIsCurrent("requests", requestEpoch)) {
        setNotice(`Connection request ${updated.status}. Refreshing private network state.`);
        await refresh();
        return;
      }
      const nextRequests = requestsRef.current
          .map((item) => (item.id === updated.id ? updated : item))
          .filter((item) => item.status === "pending");
      requestsRef.current = nextRequests;
      setRequests(nextRequests);
      setNotice(`Connection request ${updated.status}.`);
      if (updated.status === "accepted") await refresh();
    } catch (error) {
      const attempt = mutationAttemptsRef.current.get(busySlot); if (attempt) settleAttempt(busySlot, attempt, error);
      if (current(requestSubject)) setNotice(mutationAttemptsRef.current.has(busySlot) ? "The connection decision may have been recorded. Retry the unchanged decision to recover the same result. " + presentSocialError(error) : presentSocialError(error));
    } finally {
      if (current(requestSubject)) endBusy(busySlot);
    }
  };
  const endConnection = async (
    connection: Connection,
    action: "remove" | "block",
  ) => {
    if (!window.confirm(
        `${action === "block" ? "Block" : "Remove"} this private connection?`,
      )) return;
    const requestSubject = subject;
    if (
      !current(requestSubject) ||
      !readAllowsDependentAction("connections")
    ) return;
    if (!connectionsRef.current.some((item) => item.id === connection.id)) return;
    const connectionEpoch = readEpoch("connections");
    const conversationEpoch = readEpoch("conversations");
    const busySlot = `connection:${connection.id}`;
    if (!beginBusy(busySlot)) return;
    try {
      const attempt = beginAttempt(busySlot, requestSubject, { operation: action === "block" ? "block-connection" : "remove-connection", connectionId: connection.id });
      if (action === "block") await blockConnection(connection.id, getToken, () => current(requestSubject), attempt.idempotencyKey);
      else await removeConnection(connection.id, getToken, () => current(requestSubject), attempt.idempotencyKey);
      if (!current(requestSubject)) return;
      mutationAttemptsRef.current.delete(busySlot);
      if (readIsCurrent("connections", connectionEpoch)) {
        const nextConnections = connectionsRef.current.filter((item) => item.id !== connection.id);
        connectionsRef.current = nextConnections;
        setConnections(nextConnections);
      } else {
        await loadConnections();
      }
      if (
        readIsCurrent("conversations", conversationEpoch) &&
        readAllowsDependentAction("conversations")
      ) {
        const nextConversations = conversationsRef.current.filter((item) => item.connectionId !== connection.id);
        conversationsRef.current = nextConversations;
        setConversations(nextConversations);
      } else {
        await loadConversations();
      }
      setNotice(`Connection ${action === "remove" ? "removed" : "blocked"}.`);
    } catch (error) {
      const attempt = mutationAttemptsRef.current.get(busySlot); if (attempt) settleAttempt(busySlot, attempt, error);
      if (current(requestSubject)) setNotice(mutationAttemptsRef.current.has(busySlot) ? "The connection action may have been recorded. Retry the unchanged action to recover the same result. " + presentSocialError(error) : presentSocialError(error));
    } finally {
      if (current(requestSubject)) endBusy(busySlot);
    }
  };
  const startConversation = async (connection: Connection) => {
    const requestSubject = subject;
    if (
      !current(requestSubject) ||
      !readAllowsDependentAction("connections")
    ) return;
    const currentConnection = connectionsRef.current.find((item) => item.id === connection.id);
    if (!currentConnection?.messagingEnabled) return;
    const busySlot = `conversation:${connection.id}`;
    if (!beginBusy(busySlot)) return;
    try {
      const attempt = beginAttempt(busySlot, requestSubject, { operation: "create-conversation", connectionId: connection.id });
      const conversation = await createConversation(connection.id, getToken, () => current(requestSubject), attempt.idempotencyKey);
      if (!current(requestSubject)) return;
      mutationAttemptsRef.current.delete(busySlot);
      router.push(`/messages/${encodeURIComponent(conversation.id)}`);
    } catch (error) {
      const attempt = mutationAttemptsRef.current.get(busySlot); if (attempt) settleAttempt(busySlot, attempt, error);
      if (current(requestSubject)) setNotice(mutationAttemptsRef.current.has(busySlot) ? "The conversation may have been created. Retry the unchanged action to recover the same result. " + presentSocialError(error) : presentSocialError(error));
    } finally {
      if (current(requestSubject)) endBusy(busySlot);
    }
  };
  const readNotification = async (
    notification: Notification,
    destination: NotificationHubAction | null = null,
  ) => {
    const requestSubject = subject;
    if (
      !current(requestSubject) ||
      !readAllowsDependentAction("notifications")
    ) return;
    const currentNotification = notificationsRef.current.find((item) => item.id === notification.id);
    if (!currentNotification) return;
    const notificationEpoch = readEpoch("notifications");
    const busySlot = `notification:${notification.id}`;
    if (!beginBusy(busySlot)) return;
    try {
      const attempt = beginAttempt(busySlot, requestSubject, { operation: "mark-notification-read", notificationId: notification.id });
      const updated = await markNotificationRead(notification.id, getToken, () => current(requestSubject), attempt.idempotencyKey);
      if (!current(requestSubject)) return;
      if (updated.id !== currentNotification.id || !updated.readAt) {
        throw new ApiRequestError("The notification read confirmation did not match the requested record.", undefined, "server");
      }
      mutationAttemptsRef.current.delete(busySlot);
      if (readIsCurrent("notifications", notificationEpoch)) {
        const nextNotifications = notificationsRef.current.map((item) =>
          item.id === updated.id ? updated : item,
        );
        notificationsRef.current = nextNotifications;
        setNotifications(nextNotifications);
      } else {
        void loadNotifications();
      }
      if (destination) router.push(destination.href);
    } catch (error) {
      const attempt = mutationAttemptsRef.current.get(busySlot); if (attempt) settleAttempt(busySlot, attempt, error);
      if (current(requestSubject)) setNotice(mutationAttemptsRef.current.has(busySlot) ? "The notification action may have completed. Retry the unchanged action to recover the same result. " + presentSocialError(error) : presentSocialError(error));
    } finally {
      if (current(requestSubject)) endBusy(busySlot);
    }
  };
  const conversationFor = (connectionId: string) =>
    conversations.find(
      (conversation) => conversation.connectionId === connectionId,
    );
  return (
    <main className="mx-auto max-w-7xl px-5 py-10 pb-16 lg:px-8">
      <section className="max-w-3xl">
        <p className="eyebrow">Private human network</p>
        <h1 className="mt-3 font-display text-5xl font-semibold tracking-[-.06em] text-white sm:text-6xl">
          Relationships stay private.
        </h1>
        <p className="mt-4 text-lg leading-8 text-mist">
          This is not a public graph: no counts, presence, read receipts, owner
          IDs, or external relays. Only a signed-in human can request, decide,
          connect, or message.
        </p>
        <NetworkNotice label="Your private network" />
      </section>
      {notice && (
        <p
          role="status"
          className="mt-6 rounded-xl border border-white/10 bg-panel p-4 text-sm leading-6 text-mist"
        >
          {notice}
        </p>
      )}
      <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <section className="space-y-6">
          <ConnectionsPanel
            connections={connections}
            conversationFor={conversationFor}
            loadState={connectionLoadState}
            loadError={connectionLoadError}
            busy={busy}
            loading={loading}
            cursor={connectionCursor}
            canAct={readAllowsDependentAction}
            onRefresh={() => void refresh()}
            onRetry={() => void loadConnections()}
            onStartConversation={(connection) => void startConversation(connection)}
            onEndConnection={(connection, action) => void endConnection(connection, action)}
            onLoadOlder={() => void loadOlderConnections()}
          />
          <ConnectionRequestsPanel
            requests={requests}
            loadState={requestLoadState}
            loadError={requestLoadError}
            busy={busy}
            cursor={requestCursor}
            canAct={readAllowsDependentAction}
            onRetry={() => void loadRequests()}
            onDecide={(request, action, consent) => void decide(request, action, consent)}
            onLoadOlder={() => void loadOlderRequests()}
          />
          <ConversationsPanel
            conversations={conversations}
            loadState={conversationLoadState}
            loadError={conversationLoadError}
            busy={busy}
            cursor={conversationCursor}
            canAct={readAllowsDependentAction}
            onRetry={() => void loadConversations()}
            onLoadOlder={() => void loadOlderConversations()}
          />
        </section>
        <aside className="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <section
            aria-labelledby="request-title"
            className="rounded-[1.5rem] border border-acid/20 bg-acid/[.06] p-5"
          >
            <h2
              id="request-title"
              className="inline-flex items-center gap-2 text-lg font-semibold text-white"
            >
              <Send className="size-5 text-acid" aria-hidden />
              Request a connection
            </h2>
            <p className="mt-2 text-sm leading-6 text-mist">
              Enter a public profile handle. An unavailable or blocked target is
              deliberately not distinguishable.
            </p>
            <form
              className="mt-4"
              onSubmit={(event) => void sendRequest(event)}
            >
              <label className="block text-sm font-semibold text-white">
                Public profile handle
                <Input
                  className="mt-1.5"
                  value={handle}
                  onChange={(event) =>
                    setHandle(
                      event.target.value.toLowerCase().replace(/\s+/gu, "-"),
                    )
                  }
                  maxLength={100}
                  autoCapitalize="none"
                  spellCheck={false}
                  required
                />
              </label>
              <label className="mt-4 flex gap-3 text-sm leading-6 text-mist">
                <input
                  type="checkbox"
                  className="mt-1 size-4 accent-acid"
                  checked={messagingRequested}
                  onChange={(event) =>
                    setMessagingRequested(event.target.checked)
                  }
                />
                <span>
                  Request messaging. The recipient must explicitly consent
                  before a conversation can be created.
                </span>
              </label>
              <Button
                className="mt-5 w-full"
                type="submit"
                disabled={busy !== null || !handle.trim()}
              >
                {busy === "request" && (
                  <LoaderCircle className="size-4 animate-spin" aria-hidden />
                )}{" "}
                Send human connection request
              </Button>
            </form>
          </section>
          <NotificationsPanel
            notifications={notifications}
            loadState={notificationLoadState}
            loadError={notificationLoadError}
            busy={busy}
            cursor={notificationCursor}
            canAct={readAllowsDependentAction}
            onRetry={() => void loadNotifications()}
            onRead={(notification, destination) => void readNotification(notification, destination)}
            onLoadOlder={() => void loadOlderNotifications()}
          />
          <Link
            href="/inbox"
            className="block rounded-2xl border border-white/10 p-4 text-sm text-mist transition hover:border-acid/30 hover:text-white"
          >
            Need contact-request controls? Open the separate private outreach
            inbox.
          </Link>
        </aside>
      </div>
    </main>
  );
}

function PrivateGate({
  configured,
  loaded,
}: {
  configured: boolean;
  loaded: boolean;
}) {
  return (
    <main className="mx-auto max-w-5xl px-5 py-16 lg:px-8">
      <CircleUserRound className="size-7 text-acid" aria-hidden />
      <h1 className="mt-4 font-display text-4xl font-semibold text-white">
        Private network
      </h1>
      <p role="status" className="mt-3 max-w-xl text-mist">
        {!configured
          ? "This deployment has no signed-in human network configured."
          : !loaded
            ? "Checking your signed-in session…"
            : "Sign in as a human to access your private connection requests, conversations, and notifications."}
      </p>
    </main>
  );
}
