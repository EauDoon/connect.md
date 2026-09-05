/**
 * Conversation service: minimal consent-gated messaging.
 *
 * A conversation exists only between the two parties of an accepted contact
 * request. Messages are bounded text from a signed-in human participant.
 * Agents can never send messages (authority invariant). A block ends the
 * channel by transitioning the contact state, which the send path re-checks
 * on every message.
 */

import postgres from "postgres";
import { takeRateBucket } from "./auth-service";

export type ConversationRecord = {
  id: string;
  counterpartHandle: string;
  counterpartStatus: string;
  createdAt: string;
  messageCount: number;
  lastMessageAt: string | null;
  lastMessagePreview: string | null;
};

export type MessageRecord = {
  id: string;
  senderHandle: string;
  body: string;
  createdAt: string;
};

export class ConversationError extends Error {
  readonly code: "not-found" | "forbidden" | "blocked" | "invalid" | "rate-limited";

  constructor(code: ConversationError["code"], message: string) {
    super(message);
    this.code = code;
    this.name = "ConversationError";
  }
}

const MAX_MESSAGE_BYTES = 4096;
const MAX_MESSAGE_LENGTH = 2000;

export async function listConversations(sql: postgres.Sql, accountId: string): Promise<ConversationRecord[]> {
  const rows = await sql`
    SELECT c.id, c.created_at,
      other.handle AS counterpart_handle, other.status AS counterpart_status,
      (SELECT COUNT(*)::int FROM network_messages m WHERE m.conversation_id = c.id) AS message_count,
      (SELECT MAX(m.created_at) FROM network_messages m WHERE m.conversation_id = c.id) AS last_message_at,
      (SELECT m.body FROM network_messages m WHERE m.conversation_id = c.id ORDER BY m.created_at DESC LIMIT 1) AS last_message_preview
    FROM network_conversations c
    JOIN network_accounts other ON other.id = CASE WHEN c.account_a = ${accountId} THEN c.account_b ELSE c.account_a END
    WHERE c.account_a = ${accountId} OR c.account_b = ${accountId}
    ORDER BY last_message_at DESC NULLS LAST, c.created_at DESC
    LIMIT 100
  `;
  return rows.map((row) => ({
    id: row.id as string,
    counterpartHandle: row.counterpart_handle as string,
    counterpartStatus: row.counterpart_status as string,
    createdAt: (row.created_at as Date).toISOString(),
    messageCount: Number(row.message_count ?? 0),
    lastMessageAt: row.last_message_at === null ? null : (row.last_message_at as Date).toISOString(),
    lastMessagePreview: row.last_message_preview === null ? null : String(row.last_message_preview).slice(0, 120),
  }));
}

export async function listMessages(
  sql: postgres.Sql,
  accountId: string,
  conversationId: string,
): Promise<{ counterpartHandle: string; messages: MessageRecord[] }> {
  const conversationRows = await sql`
    SELECT other.handle AS counterpart_handle, other.status AS counterpart_status
    FROM network_conversations c
    JOIN network_accounts other ON other.id = CASE WHEN c.account_a = ${accountId} THEN c.account_b ELSE c.account_a END
    WHERE c.id = ${conversationId} AND (c.account_a = ${accountId} OR c.account_b = ${accountId})
    LIMIT 1
  `;
  const conversation = conversationRows[0];
  if (conversation === undefined) {
    throw new ConversationError("not-found", "No such conversation.");
  }
  const messageRows = await sql`
    SELECT m.id, m.body, m.created_at, a.handle AS sender_handle
    FROM network_messages m JOIN network_accounts a ON a.id = m.sender_id
    WHERE m.conversation_id = ${conversationId}
    ORDER BY m.created_at ASC LIMIT 500
  `;
  return {
    counterpartHandle: conversation.counterpart_handle as string,
    messages: messageRows.map((row) => ({
      id: row.id as string,
      senderHandle: row.sender_handle as string,
      body: row.body as string,
      createdAt: (row.created_at as Date).toISOString(),
    })),
  };
}

export async function sendMessage(
  sql: postgres.Sql,
  accountId: string,
  conversationId: string,
  body: unknown,
): Promise<MessageRecord> {
  if (typeof body !== "string") throw new ConversationError("invalid", "Message must be text.");
  const trimmed = body.trim();
  if (trimmed.length === 0) throw new ConversationError("invalid", "Message must not be empty.");
  if (trimmed.length > MAX_MESSAGE_LENGTH || Buffer.byteLength(trimmed, "utf8") > MAX_MESSAGE_BYTES) {
    throw new ConversationError("invalid", `Message must be at most ${String(MAX_MESSAGE_LENGTH)} characters.`);
  }
  const bucket = await takeRateBucket(sql, `message:account:${accountId}`, 120, 3600);
  if (!bucket.allowed) throw new ConversationError("rate-limited", "You are sending messages too quickly. Try again later.");

  // Consent gate, re-checked atomically with the insert: the conversation
  // must exist for this account AND its contact state must still be accepted.
  const inserted = await sql`
    INSERT INTO network_messages (conversation_id, sender_id, body)
    SELECT c.id, ${accountId}, ${trimmed}
    FROM network_conversations c
    WHERE c.id = ${conversationId} AND (c.account_a = ${accountId} OR c.account_b = ${accountId})
      AND EXISTS (
        SELECT 1 FROM network_contact_requests cr
        WHERE ((cr.requester_id = c.account_a AND cr.recipient_id = c.account_b)
            OR (cr.requester_id = c.account_b AND cr.recipient_id = c.account_a))
          AND cr.status = 'accepted'
      )
    RETURNING id, body, created_at
  `;
  if (inserted.length === 0) {
    // Distinguish a missing conversation from a closed channel.
    const exists = await sql`
      SELECT 1 FROM network_conversations c
      WHERE c.id = ${conversationId} AND (c.account_a = ${accountId} OR c.account_b = ${accountId})
      LIMIT 1
    `;
    if (exists.length === 0) throw new ConversationError("not-found", "No such conversation.");
    throw new ConversationError("blocked", "The conversation channel is closed. Contact must be re-established.");
  }
  const senderRows = await sql`SELECT handle FROM network_accounts WHERE id = ${accountId} LIMIT 1`;
  return {
    id: inserted[0]!.id as string,
    senderHandle: (senderRows[0]?.handle as string) ?? "(unknown)",
    body: inserted[0]!.body as string,
    createdAt: (inserted[0]!.created_at as Date).toISOString(),
  };
}
