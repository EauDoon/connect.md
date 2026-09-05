"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type ContactRequest = {
  id: string;
  requesterHandle: string;
  recipientHandle: string;
  status: "pending" | "accepted" | "rejected" | "revoked" | "blocked";
  createdAt: string;
  decidedAt: string | null;
};

type Conversation = {
  id: string;
  counterpartHandle: string;
  messageCount: number;
  lastMessageAt: string | null;
  lastMessagePreview: string | null;
};

const secondaryButton =
  "inline-flex min-h-11 items-center rounded-full border border-white/15 px-4 text-xs font-semibold text-white transition hover:bg-white/[.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:opacity-60";

export function InboxPanel() {
  const [incoming, setIncoming] = useState<ContactRequest[]>([]);
  const [outgoing, setOutgoing] = useState<ContactRequest[]>([]);
  const [blocked, setBlocked] = useState<string[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [sendHandle, setSendHandle] = useState("");
  const [notice, setNotice] = useState<{ kind: "error" | "success"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [contactsResponse, conversationsResponse] = await Promise.all([
      fetch("/api/network/v1/contacts"),
      fetch("/api/network/v1/conversations"),
    ]);
    if (contactsResponse.ok) {
      const body = (await contactsResponse.json()) as { incoming: ContactRequest[]; outgoing: ContactRequest[]; blockedHandles: string[] };
      setIncoming(body.incoming);
      setOutgoing(body.outgoing);
      setBlocked(body.blockedHandles);
    }
    if (conversationsResponse.ok) {
      const body = (await conversationsResponse.json()) as { conversations: Conversation[] };
      setConversations(body.conversations);
    }
  }, []);

  useEffect(() => {
    void load().catch(() => setNotice({ kind: "error", message: "Could not load your inbox." }));
  }, [load]);

  async function sendRequest(): Promise<void> {
    if (busy || sendHandle.trim() === "") return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch("/api/network/v1/contacts", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ handle: sendHandle.trim().toLowerCase() }),
      });
      const body = (await response.json()) as { ok?: boolean; message?: string };
      if (response.ok && body.ok === true) {
        setSendHandle("");
        setNotice({ kind: "success", message: "Contact request sent." });
        await load();
      } else {
        setNotice({ kind: "error", message: body.message ?? "Could not send the request." });
      }
    } catch {
      setNotice({ kind: "error", message: "The network is unreachable right now." });
    } finally {
      setBusy(false);
    }
  }

  async function decide(id: string, action: "accept" | "reject" | "revoke" | "block"): Promise<void> {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`/api/network/v1/contacts/${id}/${action}`, { method: "POST" });
      const body = (await response.json()) as { ok?: boolean; message?: string };
      if (response.ok && body.ok === true) {
        await load();
      } else {
        setNotice({ kind: "error", message: body.message ?? `Could not ${action}.` });
      }
    } catch {
      setNotice({ kind: "error", message: "The network is unreachable right now." });
    } finally {
      setBusy(false);
    }
  }

  const pendingIncoming = incoming.filter((request) => request.status === "pending");
  const activeOutgoing = outgoing.filter((request) => request.status === "pending");

  return (
    <div className="grid gap-8">
      <div className="rounded-3xl border border-white/10 bg-white/[.03] p-6 sm:p-8" data-testid="send-contact">
        <h2 className="text-xl font-semibold text-white">Request contact</h2>
        <div className="mt-4 flex flex-wrap gap-2">
          <label htmlFor="contact-handle" className="sr-only">Handle to contact</label>
          <input
            id="contact-handle"
            value={sendHandle}
            onChange={(event) => setSendHandle(event.target.value)}
            placeholder="handle to contact"
            className="min-h-11 w-full max-w-xs rounded-xl border border-white/10 bg-white/[.04] px-4 text-white placeholder:text-mist/55 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
          />
          <button type="button" className={secondaryButton} onClick={() => void sendRequest()} disabled={busy || sendHandle.trim() === ""} data-testid="contact-send">
            Send request
          </button>
        </div>
        <div aria-live="polite" className="mt-3">
          {notice !== null ? (
            <p role="status" className={"rounded-xl border px-4 py-3 text-sm " + (notice.kind === "error" ? "border-red-400/30 bg-red-400/10 text-red-200" : "border-acid/30 bg-acid/10 text-acid")} data-testid="inbox-notice">
              {notice.message}
            </p>
          ) : null}
        </div>
      </div>

      <div className="rounded-3xl border border-white/10 bg-white/[.03] p-6 sm:p-8" data-testid="contact-requests">
        <h2 className="text-xl font-semibold text-white">Incoming requests</h2>
        <ul className="mt-4 grid gap-2">
          {pendingIncoming.map((request) => (
            <li key={request.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[.02] px-4 py-3">
              <span className="text-sm text-white">@{request.requesterHandle}</span>
              <span className="flex flex-wrap gap-2">
                <button type="button" className={secondaryButton} onClick={() => void decide(request.id, "accept")} disabled={busy} data-testid={`accept-${request.requesterHandle}`}>Accept</button>
                <button type="button" className={secondaryButton} onClick={() => void decide(request.id, "reject")} disabled={busy} data-testid={`reject-${request.requesterHandle}`}>Reject</button>
                <button type="button" className={secondaryButton} onClick={() => void decide(request.id, "block")} disabled={busy} data-testid={`block-${request.requesterHandle}`}>Block</button>
              </span>
            </li>
          ))}
          {pendingIncoming.length === 0 ? <li className="text-sm text-mist" data-testid="no-incoming">No pending requests.</li> : null}
        </ul>

        <h3 className="mt-8 text-lg font-semibold text-white">Sent requests</h3>
        <ul className="mt-3 grid gap-2">
          {activeOutgoing.map((request) => (
            <li key={request.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[.02] px-4 py-3">
              <span className="text-sm text-white">To @{request.recipientHandle}</span>
              <button type="button" className={secondaryButton} onClick={() => void decide(request.id, "revoke")} disabled={busy} data-testid={`revoke-${request.recipientHandle}`}>
                Revoke
              </button>
            </li>
          ))}
          {activeOutgoing.length === 0 ? <li className="text-sm text-mist" data-testid="no-outgoing">No pending sent requests.</li> : null}
        </ul>

        <h3 className="mt-8 text-lg font-semibold text-white">History</h3>
        <ul className="mt-3 grid gap-1">
          {[...incoming, ...outgoing].filter((request) => request.status !== "pending").slice(0, 20).map((request) => (
            <li key={request.id} className="text-sm text-mist">
              @{request.requesterHandle} → @{request.recipientHandle}: <span className="font-semibold">{request.status}</span>
            </li>
          ))}
        </ul>
        {blocked.length > 0 ? (
          <p className="mt-6 text-xs text-mist" data-testid="blocked-list">Blocked: {blocked.map((handle) => `@${handle}`).join(", ")}</p>
        ) : null}
      </div>

      <div className="rounded-3xl border border-white/10 bg-white/[.03] p-6 sm:p-8" data-testid="conversations">
        <h2 className="text-xl font-semibold text-white">Conversations</h2>
        <ul className="mt-4 grid gap-2">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <Link
                href={`/conversations/${conversation.id}`}
                className="block rounded-xl border border-white/10 bg-white/[.02] px-4 py-3 transition hover:border-acid/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
              >
                <p className="text-sm font-semibold text-white">@{conversation.counterpartHandle} <span className="ml-2 text-xs font-normal text-mist">{String(conversation.messageCount)} messages</span></p>
                {conversation.lastMessagePreview !== null ? (
                  <p className="mt-1 truncate text-xs text-mist">{conversation.lastMessagePreview}</p>
                ) : null}
              </Link>
            </li>
          ))}
          {conversations.length === 0 ? <li className="text-sm text-mist" data-testid="no-conversations">No conversations yet. Accept a contact request to open one.</li> : null}
        </ul>
      </div>
    </div>
  );
}
