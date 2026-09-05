"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type Message = { id: string; senderHandle: string; body: string; createdAt: string };

const sendButton =
  "inline-flex min-h-11 items-center rounded-full bg-acid px-5 text-sm font-bold text-ink transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid disabled:opacity-60";

export function ConversationView({ conversationId }: { conversationId: string }) {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [counterpart, setCounterpart] = useState<string>("");
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const response = await fetch(`/api/network/v1/conversations/${conversationId}/messages`);
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { message?: string } | null;
      setMessages([]);
      setNotice(body?.message ?? "This conversation is not available.");
      return;
    }
    const body = (await response.json()) as { messages: Message[]; counterpartHandle: string };
    setMessages(body.messages);
    setCounterpart(body.counterpartHandle);
  }, [conversationId]);

  useEffect(() => {
    void load().catch(() => setNotice("Could not load this conversation."));
  }, [load]);

  async function send(): Promise<void> {
    if (busy || draft.trim() === "") return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`/api/network/v1/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ body: draft }),
      });
      const body = (await response.json()) as { ok?: boolean; message?: string };
      if (response.ok && body.ok === true) {
        setDraft("");
        await load();
      } else {
        setNotice(body.message ?? "Could not send the message.");
      }
    } catch {
      setNotice("The network is unreachable right now.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-white">Conversation with @{counterpart || "…"}</h1>
        <Link href="/inbox" className="text-sm font-semibold text-acid underline-offset-4 hover:underline">Back to inbox</Link>
      </div>
      <ul className="grid gap-2" data-testid="message-list">
        {(messages ?? []).map((message) => (
          <li key={message.id} className="rounded-xl border border-white/10 bg-white/[.02] px-4 py-3">
            <p className="text-xs text-mist">@{message.senderHandle} · {new Date(message.createdAt).toLocaleString()}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-sm text-white">{message.body}</p>
          </li>
        ))}
        {messages !== null && messages.length === 0 ? <li className="text-sm text-mist">No messages yet.</li> : null}
      </ul>
      <div aria-live="polite">
        {notice !== null ? <p role="status" className="rounded-xl border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200" data-testid="conversation-notice">{notice}</p> : null}
      </div>
      <form
        className="grid gap-3"
        onSubmit={(event) => { event.preventDefault(); void send(); }}
      >
        <label htmlFor="message-body" className="sr-only">Message</label>
        <textarea
          id="message-body"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={2000}
          rows={3}
          placeholder="Write a message…"
          className="w-full rounded-2xl border border-white/10 bg-white/[.04] p-4 text-sm text-white placeholder:text-mist/55 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid"
        />
        <button type="submit" className={sendButton + " justify-self-start"} disabled={busy || draft.trim() === ""} data-testid="message-send">
          {busy ? "Sending…" : "Send message"}
        </button>
      </form>
    </div>
  );
}
