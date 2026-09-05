import type postgres from "postgres";

/**
 * Contact service: the consent primitive of the network MVP.
 *
 * Sending is rate-limited and forbidden across blocks in either direction.
 * Decisions follow the pure state machine in contact.ts. Acceptance opens
 * exactly one conversation; blocking closes it and forbids any future
 * contact in either direction.
 */

import {
  canRequestContact,
  contactTransition,
  type ContactActor,
  type ContactRequestStatus,
} from "./contact";
import { takeRateBucket } from "./auth-service";

export type ContactRequestRecord = {
  id: string;
  requesterHandle: string;
  recipientHandle: string;
  status: ContactRequestStatus;
  createdAt: string;
  decidedAt: string | null;
};

export class ContactError extends Error {
  readonly code: "invalid" | "not-found" | "conflict" | "blocked" | "rate-limited" | "forbidden";

  constructor(code: ContactError["code"], message: string) {
    super(message);
    this.code = code;
    this.name = "ContactError";
  }
}

type ContactRow = {
  id: string;
  status: ContactRequestStatus;
  created_at: Date;
  decided_at: Date | null;
  requester_id: string;
  recipient_id: string;
  requester_handle: string;
  recipient_handle: string;
};

export async function sendContactRequest(
  sql: postgres.Sql,
  accountId: string,
  recipientHandle: string,
): Promise<ContactRequestRecord> {
  const ipIndependentBucket = await takeRateBucket(sql, `contact:account:${accountId}`, 20, 3600);
  if (!ipIndependentBucket.allowed) {
    throw new ContactError("rate-limited", "You have sent too many contact requests recently. Try again later.");
  }
  const recipientRows = await sql`
    SELECT id, status FROM network_accounts WHERE handle = ${recipientHandle.toLowerCase()} AND status = 'active' LIMIT 1
  `;
  const recipient = recipientRows[0];
  if (recipient === undefined) {
    // Uniform response for unknown handles: no existence leak beyond what
    // discovery already shows for published profiles.
    throw new ContactError("not-found", "No account holds that handle, or it cannot be contacted.");
  }
  if (recipient.id === accountId) {
    throw new ContactError("invalid", "You cannot send a contact request to yourself.");
  }

  const blocked = await sql`
    SELECT 1 FROM network_contact_blocks
    WHERE (blocker_id = ${recipient.id} AND blocked_id = ${accountId})
       OR (blocker_id = ${accountId} AND blocked_id = ${recipient.id})
    LIMIT 1
  `;
  const priorRows = await sql`
    SELECT status, requester_id FROM network_contact_requests
    WHERE (requester_id = ${accountId} AND recipient_id = ${recipient.id})
       OR (requester_id = ${recipient.id} AND recipient_id = ${accountId})
    ORDER BY created_at DESC LIMIT 1
  `;
  const priorStatus = (priorRows[0]?.status as ContactRequestStatus | undefined) ?? null;
  const eligibility = canRequestContact(priorStatus, blocked.length > 0);
  if (!eligibility.ok) {
    const messages: Record<string, string> = {
      blocked: "Contact with this account is blocked.",
      "pending-exists": "A pending request to this account already exists.",
      "accepted-exists": "You are already connected with this account.",
      "rate-limited": "Too many contact requests. Try again later.",
    };
    throw new ContactError(
      eligibility.reason === "blocked" ? "blocked" : "conflict",
      messages[eligibility.reason] ?? "Contact request not allowed.",
    );
  }

  const inserted = await sql`
    INSERT INTO network_contact_requests (requester_id, recipient_id)
    VALUES (${accountId}, ${recipient.id})
    RETURNING id, status, created_at, decided_at
  `;
  return {
    id: inserted[0]!.id as string,
    requesterHandle: "(you)",
    recipientHandle: recipientHandle.toLowerCase(),
    status: inserted[0]!.status as ContactRequestStatus,
    createdAt: (inserted[0]!.created_at as Date).toISOString(),
    decidedAt: null,
  };
}

async function loadRequest(sql: postgres.Sql, requestId: string): Promise<ContactRow | null> {
  const rows = await sql`
    SELECT cr.id, cr.status, cr.created_at, cr.decided_at, cr.requester_id, cr.recipient_id,
           ra.handle AS requester_handle, sa.handle AS recipient_handle
    FROM network_contact_requests cr
    JOIN network_accounts ra ON ra.id = cr.requester_id
    JOIN network_accounts sa ON sa.id = cr.recipient_id
    WHERE cr.id = ${requestId} LIMIT 1
  `;
  return (rows[0] as ContactRow | undefined) ?? null;
}

export async function decideContactRequest(
  sql: postgres.Sql,
  accountId: string,
  requestId: string,
  action: "accept" | "reject" | "revoke" | "block",
): Promise<ContactRequestRecord> {
  const request = await loadRequest(sql, requestId);
  if (request === null) throw new ContactError("not-found", "No such contact request.");
  const actor: ContactActor | null =
    request.requester_id === accountId ? "requester"
    : request.recipient_id === accountId ? "recipient"
    : null;
  if (actor === null) throw new ContactError("forbidden", "This request does not involve your account.");
  const transition = contactTransition(request.status, actor, action);
  if (!transition.ok) {
    const messages: Record<string, string> = {
      "unknown-action": "Unknown action.",
      "wrong-actor": "Only the recipient can decide a request; only the sender can revoke it.",
      "not-pending": "Only pending requests can be decided this way.",
      "already-terminal": "This request has already reached a final state.",
    };
    throw new ContactError(transition.reason === "wrong-actor" ? "forbidden" : "conflict", messages[transition.reason] ?? "Not allowed.");
  }

  const updated = await sql`
    UPDATE network_contact_requests SET status = ${transition.status}, decided_at = now()
    WHERE id = ${requestId} AND status = ${request.status}
    RETURNING id, status, created_at, decided_at
  `;
  if (updated.length === 0) {
    throw new ContactError("conflict", "The request changed state concurrently. Reload and retry.");
  }

  if (transition.status === "blocked") {
    await sql`
      INSERT INTO network_contact_blocks (blocker_id, blocked_id)
      VALUES (${accountId}, ${actor === "recipient" ? request.requester_id : request.recipient_id})
      ON CONFLICT DO NOTHING
    `;
  }
  if (transition.status === "accepted") {
    const [lowId, highId] = [request.requester_id, request.recipient_id].sort();
    await sql`
      INSERT INTO network_conversations (account_a, account_b)
      VALUES (${lowId}, ${highId})
      ON CONFLICT DO NOTHING
    `;
  }
  return {
    id: request.id,
    requesterHandle: request.requester_handle,
    recipientHandle: request.recipient_handle,
    status: transition.status,
    createdAt: request.created_at.toISOString(),
    decidedAt: (updated[0]!.decided_at as Date).toISOString(),
  };
}

export async function listContactRequests(
  sql: postgres.Sql,
  accountId: string,
): Promise<{ incoming: ContactRequestRecord[]; outgoing: ContactRequestRecord[] }> {
  const rows = await sql`
    SELECT cr.id, cr.status, cr.created_at, cr.decided_at, cr.requester_id, cr.recipient_id,
           ra.handle AS requester_handle, sa.handle AS recipient_handle
    FROM network_contact_requests cr
    JOIN network_accounts ra ON ra.id = cr.requester_id
    JOIN network_accounts sa ON sa.id = cr.recipient_id
    WHERE cr.requester_id = ${accountId} OR cr.recipient_id = ${accountId}
    ORDER BY cr.created_at DESC LIMIT 200
  `;
  const incoming: ContactRequestRecord[] = [];
  const outgoing: ContactRequestRecord[] = [];
  for (const row of rows as unknown as ContactRow[]) {
    const record: ContactRequestRecord = {
      id: row.id,
      requesterHandle: row.requester_handle,
      recipientHandle: row.recipient_handle,
      status: row.status,
      createdAt: row.created_at.toISOString(),
      decidedAt: row.decided_at === null ? null : row.decided_at.toISOString(),
    };
    if (row.requester_id === accountId) outgoing.push(record);
    else incoming.push(record);
  }
  return { incoming, outgoing };
}

export async function listBlocks(sql: postgres.Sql, accountId: string): Promise<string[]> {
  const rows = await sql`
    SELECT a.handle FROM network_contact_blocks b
    JOIN network_accounts a ON a.id = b.blocked_id
    WHERE b.blocker_id = ${accountId}
    ORDER BY a.handle
  `;
  return rows.map((row) => row.handle as string);
}

export async function blockAccount(sql: postgres.Sql, accountId: string, targetHandle: string): Promise<void> {
  const rows = await sql`SELECT id FROM network_accounts WHERE handle = ${targetHandle.toLowerCase()} LIMIT 1`;
  const target = rows[0];
  if (target === undefined) throw new ContactError("not-found", "No account holds that handle.");
  if (target.id === accountId) throw new ContactError("invalid", "You cannot block yourself.");
  await sql`
    INSERT INTO network_contact_blocks (blocker_id, blocked_id)
    VALUES (${accountId}, ${target.id})
    ON CONFLICT DO NOTHING
  `;
  // A block also ends any active pending or accepted request in either direction.
  await sql`
    UPDATE network_contact_requests SET status = 'blocked', decided_at = now()
    WHERE status IN ('pending', 'accepted')
      AND ((requester_id = ${accountId} AND recipient_id = ${target.id})
        OR (requester_id = ${target.id} AND recipient_id = ${accountId}))
  `;
}
