import {
  Ban,
  Bell,
  Check,
  LoaderCircle,
  MessageSquare,
  Network,
  ShieldAlert,
  UserPlus,
  X,
} from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  notificationHubAction,
  type Connection,
  type ConnectionRequest,
  type Conversation,
  type Notification,
  type NotificationHubAction,
} from "@/lib/social-api";

export type NetworkPanelLoadState = "loading" | "loaded" | "error";
type NetworkPanelSlice = "requests" | "connections" | "conversations" | "notifications";

type ConnectionsPanelProps = {
  connections: Connection[];
  conversationFor: (connectionId: string) => Conversation | undefined;
  loadState: NetworkPanelLoadState;
  loadError: string;
  busy: string | null;
  loading: boolean;
  cursor: string | null;
  canAct: (slice: NetworkPanelSlice) => boolean;
  onRefresh: () => void;
  onRetry: () => void;
  onStartConversation: (connection: Connection) => void;
  onEndConnection: (connection: Connection, action: "remove" | "block") => void;
  onLoadOlder: () => void;
};

export function ConnectionsPanel({
  connections,
  conversationFor,
  loadState,
  loadError,
  busy,
  loading,
  cursor,
  canAct,
  onRefresh,
  onRetry,
  onStartConversation,
  onEndConnection,
  onLoadOlder,
}: ConnectionsPanelProps) {
  return (
    <section
      aria-labelledby="connections-title"
      className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2
            id="connections-title"
            className="inline-flex items-center gap-2 text-xl font-semibold text-white"
          >
            <Network className="size-5 text-acid" aria-hidden />
            Connections
          </h2>
          <p className="mt-1 text-sm leading-6 text-mist">
            Relationship data stays private; linked profiles remain
            public self-declarations, not social-graph records.
          </p>
        </div>
        <Button
          variant="secondary"
          disabled={loading || busy !== null}
          onClick={onRefresh}
        >
          {loading && (
            <LoaderCircle className="size-4 animate-spin" aria-hidden />
          )}{" "}
          Refresh
        </Button>
      </div>
      {loadState === "loading" && connections.length === 0 ? (
        <Loading label="Loading private connections" />
      ) : loadState === "error" && connections.length === 0 ? (
        <LoadFailure label="Connections could not be loaded" error={loadError} disabled={busy !== null} onRetry={onRetry} />
      ) : loadState === "loaded" && connections.length === 0 ? (
        <Empty
          title="No private connections"
          body="Accepted requests will appear here without creating a public relationship graph."
        />
      ) : (
        <ol className="mt-5 space-y-3">
          {connections.map((connection) => {
            const conversation = conversationFor(connection.id);
            return (
              <li
                key={connection.id}
                className="rounded-xl border border-white/10 bg-black/15 p-4"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="font-semibold text-white">
                      Connection with{" "}
                      <ProfileLink handle={connection.counterpartyProfileHandle} />
                    </h3>
                    <p className="mt-1 text-xs text-mist">
                      Created {formatDate(connection.createdAt)} ·{" "}
                      {connection.messagingEnabled
                        ? "Bilateral messaging enabled"
                        : "Messaging was not mutually enabled"}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {connection.messagingEnabled &&
                      (conversation ? (
                        <Link
                          href={`/messages/${encodeURIComponent(conversation.id)}`}
                          className="inline-flex min-h-11 items-center gap-2 rounded-full bg-acid px-4 text-sm font-bold text-ink"
                        >
                          <MessageSquare className="size-4" aria-hidden />
                          Open conversation
                        </Link>
                      ) : (
                        <Button
                          variant="secondary"
                          disabled={busy !== null || !canAct("connections")}
                          onClick={() => onStartConversation(connection)}
                        >
                          <MessageSquare className="size-4" aria-hidden />
                          Start conversation
                        </Button>
                      ))}
                    <Button
                      variant="ghost"
                      disabled={busy !== null || !canAct("connections")}
                      onClick={() => onEndConnection(connection, "remove")}
                    >
                      Remove
                    </Button>
                    <Button
                      variant="danger"
                      disabled={busy !== null || !canAct("connections")}
                      onClick={() => onEndConnection(connection, "block")}
                    >
                      <Ban className="size-4" aria-hidden />
                      Block
                    </Button>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {loadState === "error" && connections.length > 0 && <LoadFailure label="Connections could not be refreshed" error={loadError} disabled={busy !== null} onRetry={onRetry} />}
      {cursor && (
        <LoadOlder
          disabled={busy !== null || !canAct("connections")}
          loading={busy === "connections-more"}
          onClick={onLoadOlder}
          label="Load older connections"
        />
      )}
    </section>
  );
}

type ConnectionRequestsPanelProps = {
  requests: ConnectionRequest[];
  loadState: NetworkPanelLoadState;
  loadError: string;
  busy: string | null;
  cursor: string | null;
  canAct: (slice: NetworkPanelSlice) => boolean;
  onRetry: () => void;
  onDecide: (
    request: ConnectionRequest,
    action: "accept" | "reject" | "block",
    consent?: boolean,
  ) => void;
  onLoadOlder: () => void;
};

export function ConnectionRequestsPanel({
  requests,
  loadState,
  loadError,
  busy,
  cursor,
  canAct,
  onRetry,
  onDecide,
  onLoadOlder,
}: ConnectionRequestsPanelProps) {
  return (
    <section
      aria-labelledby="requests-title"
      className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"
    >
      <h2
        id="requests-title"
        className="inline-flex items-center gap-2 text-xl font-semibold text-white"
      >
        <UserPlus className="size-5 text-acid" aria-hidden />
        Incoming requests
      </h2>
      <p className="mt-1 text-sm leading-6 text-mist">
        Accepting requires an explicit choice about messaging. The
        requester’s owner subject is never shown.
      </p>
      {loadState === "loading" && requests.length === 0 ? (
        <Loading label="Loading private requests" />
      ) : loadState === "error" && requests.length === 0 ? (
        <LoadFailure label="Connection requests could not be loaded" error={loadError} disabled={busy !== null} onRetry={onRetry} />
      ) : loadState === "loaded" && requests.length === 0 ? (
        <Empty
          title="No incoming requests"
          body="There are no pending human connection requests."
        />
      ) : (
        <ol className="mt-5 space-y-3">
          {requests.map((request) => (
            <li
              key={request.id}
              className="rounded-xl border border-white/10 bg-black/15 p-4"
            >
              <h3 className="font-semibold text-white">
                Connection request from{" "}
                <ProfileLink handle={request.counterpartyProfileHandle} />
              </h3>
              <p className="mt-1 text-sm text-mist">
                {request.messagingRequested
                  ? "The requester asked whether messaging may be enabled."
                  : "The requester did not ask to enable messaging."}
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {request.messagingRequested ? (
                  <>
                    <Button
                      variant="secondary"
                      disabled={busy !== null || !canAct("requests")}
                      onClick={() => onDecide(request, "accept", true)}
                    >
                      <Check className="size-4" aria-hidden />
                      Accept with messaging
                    </Button>
                    <Button
                      variant="secondary"
                      disabled={busy !== null || !canAct("requests")}
                      onClick={() => onDecide(request, "accept", false)}
                    >
                      Accept without messaging
                    </Button>
                  </>
                ) : (
                  <Button
                    variant="secondary"
                    disabled={busy !== null || !canAct("requests")}
                    onClick={() => onDecide(request, "accept", false)}
                  >
                    <Check className="size-4" aria-hidden />
                    Accept
                  </Button>
                )}
                <Button
                  variant="ghost"
                  disabled={busy !== null || !canAct("requests")}
                  onClick={() => onDecide(request, "reject")}
                >
                  <X className="size-4" aria-hidden />
                  Reject
                </Button>
                <Button
                  variant="danger"
                  disabled={busy !== null || !canAct("requests")}
                  onClick={() => onDecide(request, "block")}
                >
                  <Ban className="size-4" aria-hidden />
                  Block
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}
      {loadState === "error" && requests.length > 0 && <LoadFailure label="Connection requests could not be refreshed" error={loadError} disabled={busy !== null} onRetry={onRetry} />}
      {cursor && (
        <LoadOlder
          disabled={busy !== null || !canAct("requests")}
          loading={busy === "requests-more"}
          onClick={onLoadOlder}
          label="Load older requests"
        />
      )}
    </section>
  );
}

type ConversationsPanelProps = {
  conversations: Conversation[];
  loadState: NetworkPanelLoadState;
  loadError: string;
  busy: string | null;
  cursor: string | null;
  canAct: (slice: NetworkPanelSlice) => boolean;
  onRetry: () => void;
  onLoadOlder: () => void;
};

export function ConversationsPanel({
  conversations,
  loadState,
  loadError,
  busy,
  cursor,
  canAct,
  onRetry,
  onLoadOlder,
}: ConversationsPanelProps) {
  return (
    <section
      aria-labelledby="conversations-title"
      className="rounded-[1.5rem] border border-white/10 bg-panel p-5 sm:p-6"
    >
      <h2
        id="conversations-title"
        className="inline-flex items-center gap-2 text-xl font-semibold text-white"
      >
        <MessageSquare className="size-5 text-acid" aria-hidden />
        Conversations
      </h2>
      <p className="mt-1 text-sm leading-6 text-mist">
        Private, human-only Markdown conversations. There are no delivery
        or read indicators.
      </p>
      {loadState === "loading" && conversations.length === 0 ? (
        <Loading label="Loading conversations" />
      ) : loadState === "error" && conversations.length === 0 ? (
        <LoadFailure label="Conversations could not be loaded" error={loadError} disabled={busy !== null} onRetry={onRetry} />
      ) : loadState === "loaded" && conversations.length === 0 ? (
        <Empty
          title="No conversations"
          body="A conversation can begin only after bilateral messaging consent."
        />
      ) : (
        <ol className="mt-5 space-y-3">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <NetworkConversationCard conversation={conversation} />
            </li>
          ))}
        </ol>
      )}
      {loadState === "error" && conversations.length > 0 && <LoadFailure label="Conversations could not be refreshed" error={loadError} disabled={busy !== null} onRetry={onRetry} />}
      {cursor && (
        <LoadOlder
          disabled={busy !== null || !canAct("conversations")}
          loading={busy === "conversations-more"}
          onClick={onLoadOlder}
          label="Load older conversations"
        />
      )}
    </section>
  );
}

type NotificationsPanelProps = {
  notifications: Notification[];
  loadState: NetworkPanelLoadState;
  loadError: string;
  busy: string | null;
  cursor: string | null;
  canAct: (slice: NetworkPanelSlice) => boolean;
  onRetry: () => void;
  onRead: (notification: Notification, destination: NotificationHubAction | null) => void;
  onLoadOlder: () => void;
};

export function NotificationsPanel({
  notifications,
  loadState,
  loadError,
  busy,
  cursor,
  canAct,
  onRetry,
  onRead,
  onLoadOlder,
}: NotificationsPanelProps) {
  return (
    <section
      aria-labelledby="notifications-title"
      className="rounded-[1.5rem] border border-white/10 bg-panel p-5"
    >
      <h2
        id="notifications-title"
        className="inline-flex items-center gap-2 text-lg font-semibold text-white"
      >
        <Bell className="size-5 text-acid" aria-hidden />
        Notifications
      </h2>
      <p className="mt-1 text-sm leading-6 text-mist">
        Recipient-private metadata only. Actor identities and resource IDs
        are not shown.
      </p>
      {loadState === "loading" && notifications.length === 0 ? (
        <Loading label="Loading notifications" />
      ) : loadState === "error" && notifications.length === 0 ? (
        <LoadFailure label="Notifications could not be loaded" error={loadError} disabled={busy !== null} onRetry={onRetry} />
      ) : loadState === "loaded" && notifications.length === 0 ? (
        <Empty
          title="No notifications"
          body="No private network activity is waiting here."
        />
      ) : (
        <ol className="mt-4 space-y-2">
          {notifications.map((notification) => {
            const action = notificationHubAction(notification);
            const label = notificationLabel(notification.type);
            const disabled = busy !== null || !canAct("notifications") || (!action && Boolean(notification.readAt));
            return (
              <li key={notification.id}>
                <button
                  type="button"
                  onClick={() => onRead(notification, action)}
                  disabled={disabled}
                  aria-label={action ? notification.readAt ? action.label : `Mark ${label.toLowerCase()} read and ${action.label.toLowerCase()}` : `Mark ${label.toLowerCase()} read`}
                  className={`w-full rounded-xl border p-3 text-left text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid ${notification.readAt ? "border-white/10 text-mist" : "border-acid/25 bg-acid/[.06] text-white"}`}
                >
                  <span className="font-semibold">{label}</span>
                  <span className="mt-1 block text-xs text-mist">
                    {formatDate(notification.createdAt)}
                    {action ? notification.readAt ? ` · ${action.label.toLowerCase()}` : ` · mark read and ${action.label.toLowerCase()}` : notification.readAt ? " · read" : " · mark read"}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
      {loadState === "error" && notifications.length > 0 && <LoadFailure label="Notifications could not be refreshed" error={loadError} disabled={busy !== null} onRetry={onRetry} />}
      {cursor && (
        <LoadOlder
          disabled={busy !== null || !canAct("notifications")}
          loading={busy === "notifications-more"}
          onClick={onLoadOlder}
          label="Load older notifications"
        />
      )}
    </section>
  );
}

function Loading({ label }: { label: string }) {
  return (
    <p
      role="status"
      className="mt-5 inline-flex items-center gap-2 text-sm text-mist"
    >
      <LoaderCircle className="size-4 animate-spin" aria-hidden />
      {label}
    </p>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="mt-5 rounded-xl border border-dashed border-white/15 p-5 text-center">
      <ShieldAlert className="mx-auto size-5 text-acid" aria-hidden />
      <h3 className="mt-3 font-semibold text-white">{title}</h3>
      <p className="mt-1 text-sm leading-6 text-mist">{body}</p>
    </div>
  );
}

function LoadFailure({ label, error, disabled, onRetry }: { label: string; error: string; disabled: boolean; onRetry: () => void }) {
  return <div role="alert" className="mt-5 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-4"><h3 className="font-semibold text-amber-50">{label}</h3><p className="mt-1 text-sm leading-6 text-amber-100/85">{error}</p><Button variant="secondary" className="mt-3" disabled={disabled} onClick={onRetry}>Retry</Button></div>;
}

function LoadOlder({
  label,
  disabled,
  loading,
  onClick,
}: {
  label: string;
  disabled: boolean;
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      className="mt-5 w-full"
      disabled={disabled}
      onClick={onClick}
    >
      {loading && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
      {label}
    </Button>
  );
}

function ProfileLink({ handle }: { handle: string }) {
  return <Link href={`/p/${encodeURIComponent(handle)}`} className="break-anywhere text-acid underline-offset-4 hover:underline">@{handle}</Link>;
}

export function NetworkConversationCard({ conversation }: { conversation: Conversation }) {
  return <article className="rounded-xl border border-white/10 bg-black/15 p-4 transition hover:border-acid/30">
    <h3 className="font-semibold text-white">Conversation with <ProfileLink handle={conversation.counterpartyProfileHandle} /></h3>
    <p className="mt-1 text-xs text-mist">Created {formatDate(conversation.createdAt)}</p>
    <Link href={`/messages/${encodeURIComponent(conversation.id)}`} aria-label={`Open conversation with @${conversation.counterpartyProfileHandle}`} className="mt-3 inline-flex min-h-11 items-center text-sm font-semibold text-acid underline-offset-4 hover:underline">Open conversation</Link>
  </article>;
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

function notificationLabel(type: string) {
  if (type === "connection_request.received") return "New connection request";
  if (type === "connection_request.accepted")
    return "Connection request accepted";
  if (type === "connection_request.rejected")
    return "Connection request declined";
  if (type === "conversation.created") return "Conversation created";
  if (type === "message.received") return "New private message";
  if (type === "application.under_review") return "Application under review";
  if (type === "application.accepted" || type === "application.rejected")
    return "Application decision available";
  return "Private network update";
}
