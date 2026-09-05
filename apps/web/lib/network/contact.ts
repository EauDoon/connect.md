/**
 * Contact-request state machine for the network MVP.
 *
 * Consent primitive: a contact request moves through a small, closed state
 * machine. Every transition is explicit and auditable; blocking is total and
 * irreversible by the blocked party. Pure functions here are shared by the
 * API routes and the tests that pin the boundaries.
 *
 *   pending ──accept──> accepted           (opens the conversation channel)
 *   pending ──reject──> rejected           (recipient declines)
 *   pending ──revoke──> revoked            (sender withdraws)
 *   pending ──block───> blocked            (recipient blocks; total)
 *   accepted ──block──> blocked            (a later block closes the channel)
 *
 * A rejected requester may send a new request later (bounded by rate limits);
 * a blocked pair may never re-request in either direction.
 */

export type ContactRequestStatus = "pending" | "accepted" | "rejected" | "revoked" | "blocked";

export type ContactActor = "requester" | "recipient";

export type ContactTransition =
  | { ok: true; status: ContactRequestStatus }
  | { ok: false; reason: "unknown-action" | "wrong-actor" | "not-pending" | "already-terminal" };

export function contactTransition(
  current: ContactRequestStatus,
  actor: ContactActor,
  action: "accept" | "reject" | "revoke" | "block",
): ContactTransition {
  if (current === "blocked") {
    return { ok: false, reason: "already-terminal" };
  }
  switch (action) {
    case "accept":
      if (current !== "pending") return { ok: false, reason: "not-pending" };
      if (actor !== "recipient") return { ok: false, reason: "wrong-actor" };
      return { ok: true, status: "accepted" };
    case "reject":
      if (current !== "pending") return { ok: false, reason: "not-pending" };
      if (actor !== "recipient") return { ok: false, reason: "wrong-actor" };
      return { ok: true, status: "rejected" };
    case "revoke":
      if (current !== "pending") return { ok: false, reason: "not-pending" };
      if (actor !== "requester") return { ok: false, reason: "wrong-actor" };
      return { ok: true, status: "revoked" };
    case "block":
      // A block is valid from pending (decline and block) and accepted
      // (close the channel). Terminal non-blocked states cannot be blocked
      // because no active relationship exists to block.
      if (current !== "pending" && current !== "accepted") {
        return { ok: false, reason: "already-terminal" };
      }
      if (actor !== "recipient") return { ok: false, reason: "wrong-actor" };
      return { ok: true, status: "blocked" };
    default:
      return { ok: false, reason: "unknown-action" };
  }
}

/** Whether a NEW contact request from requester to recipient may be created. */
export function canRequestContact(
  prior: ContactRequestStatus | null,
  blocked: boolean,
): { ok: true } | { ok: false; reason: "blocked" | "pending-exists" | "accepted-exists" | "rate-limited" } {
  if (blocked) return { ok: false, reason: "blocked" };
  if (prior === "pending") return { ok: false, reason: "pending-exists" };
  if (prior === "accepted") return { ok: false, reason: "accepted-exists" };
  return { ok: true };
}

/** Agents are never allowed to send contact requests or messages. */
export const AGENT_FORBIDDEN_ACTIONS = ["contact-request", "message-send"] as const;
