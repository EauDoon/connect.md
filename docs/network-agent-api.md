# Network agent API

The consent-first network ([ADR 0002](decisions/0002-consent-first-network-mvp.md))
lets an account owner issue a scoped, expiring, revocable grant to an agent.
This page is the contract for an agent holding such a grant. The network is
optional: a deployment without its database answers every route below with
503 `network-database-not-configured`, and the public connect.md site does not
run the network today.

## Authentication

Send the grant token as a bearer credential:

    Authorization: Bearer cnag_...

Tokens start with `cnag_` and are shown to the owner exactly once, when the
grant is created on `/network` under **Agent access**. Only a SHA-256 digest is
stored. A token that is unknown, revoked, or past its expiry, or whose owner's
account is deactivated, gets:

    401 {"ok": false, "reason": "agent-unauthorized"}
    WWW-Authenticate: Bearer

Agent routes ignore session cookies, and cookie routes ignore bearer tokens.
Every response is JSON with `Cache-Control: no-store`.

## Scopes

| Scope | Allows |
|---|---|
| `profile:read` | `GET /api/network/v1/agent/profile` |
| `profile:write` | `PUT /api/network/v1/agent/profile` while the profile is private |
| `contacts:read` | `GET /api/network/v1/agent/contacts` |

There is no wildcard. The dashboard offers each scope as a separate checkbox,
with `profile:read` alone ticked by default, and a lifetime of 7, 30, 90
(default), or 365 days. A grant created through the API without `expiresAt`
lasts 90 days, and an explicit expiry may be at most one year out. Grants stored
before expiry became mandatory may have none and stay live until revoked.

A call outside the grant's scopes gets:

    403 {"ok": false, "reason": "scope-denied", "required": "<scope>"}

## Endpoints

All paths are under `/api/network/v1`.

### GET agent/grant

Describes the grant behind the token, so an agent can check what it may do
before acting. Any live grant may call it; no scope is required.

    200 {
      "ok": true,
      "account": {"handle": "ada"},
      "grant": {
        "name": "profile-agent",
        "scopes": ["profile:read"],
        "createdAt": "2026-10-31T12:00:00.000Z",
        "expiresAt": "2027-01-29T12:00:00.000Z"
      }
    }

`expiresAt` is `null` only for a legacy grant without expiry. The token, its
display prefix, and its digest are never returned.

### GET agent/profile (`profile:read`)

Returns the owner's saved profile, including while it is private:

    200 {"ok": true, "account": {"handle": "ada"},
         "profile": {"markdown": "...", "etag": "\"<64 hex>\"", "visibility": "private", "updatedAt": "..."}}

`profile` is `null` when nothing has been saved yet.

### PUT agent/profile (`profile:write`)

Replaces the saved profile. The body is `{"markdown": "<canonical profile>"}`
with `Content-Type: application/json`; no other field is accepted. The Markdown
must pass the same validation as the guest builder.

- To create the first profile, send no `If-Match` header.
- To edit an existing profile, send `If-Match` with the `etag` from the last
  read. A missing, malformed, or stale ETag gets
  `412 {"ok": false, "reason": "precondition", "message": "..."}`. Read again
  and reapply the edit.
- A published profile cannot be edited by anyone until the owner unpublishes
  it: `409 {"ok": false, "reason": "conflict", "message": "..."}`. Saving never
  publishes; only the owner publishes.

Success returns `200 {"ok": true, "etag": "...", "updatedAt": "..."}`. Invalid
Markdown gets `400 {"ok": false, "reason": "invalid", "message": "..."}`.

### GET agent/contacts (`contacts:read`)

Lists the owner's contact requests, newest first, up to 200 per page:

    200 {"ok": true, "account": {"handle": "ada"},
         "incoming": [{"id": "...", "requesterHandle": "...", "recipientHandle": "ada",
                       "status": "pending", "createdAt": "...", "decidedAt": null}],
         "outgoing": [...],
         "nextCursor": "<uuid>" }

Pass `?before=<nextCursor>` for the next page; `nextCursor` is `null` on the
last page. A malformed cursor gets `400 {"ok": false, "reason": "request-invalid"}`.

## Errors

| Status | `reason` | Meaning |
|---|---|---|
| 400 | `request-body-invalid` | Wrong content type, unknown or duplicate fields, nested objects, invalid UTF-8, or an oversized body |
| 400 | `request-invalid` | A malformed query parameter or identifier |
| 400 | `invalid` | Profile Markdown failed validation |
| 401 | `agent-unauthorized` | No usable bearer token |
| 403 | `scope-denied` | The grant lacks the scope named in `required` |
| 409 | `conflict` | The profile is published |
| 412 | `precondition` | The `If-Match` ETag is missing, malformed, or stale |
| 503 | `network-database-not-configured` | The deployment has no network database |
| 503 | `network-unavailable` | An unexpected failure; the body carries an `errorId` |

## What an agent can never do

Per ADR 0002, no scope lets an agent send, accept, reject, or block contact
requests, send messages, publish or unpublish a profile, or issue, change, or
revoke grants. The owner manages grants, sees each grant's state and last use,
and revokes any active grant on `/network`; revocation takes effect on the
next request.
