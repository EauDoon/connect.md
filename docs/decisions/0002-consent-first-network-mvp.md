# ADR 0002: Consent-first network MVP

Status: Accepted (owner-authorized 2026-09-06)
Supersedes: the "standalone forever" reading of ADR 0001's scope for network
features; the guest builder boundary it records is unchanged and preserved.

## Context

The owner has authorized development beyond the standalone Markdown builder
toward the consent-first professional network for humans and agents. The
retained backend (apps/api, FastAPI) contains a sound, tested consent-first
core — triple-credential auth (humans, owner keys, scoped agent grants),
document visibility with explicit publish state, contact-request consent flow,
and rate-bucket discipline — but it is a 21k-line monolith bound to Clerk,
Meilisearch, recruiting, moderation, and lifecycle machinery that this MVP
must not reactivate.

The live deployment is a Vercel-hosted Next.js site whose guest workflow
(compose, validate, preview, local download, local reopen) keeps all draft
state in the browser by design.

## Decision

Build a narrow network MVP **inside the existing Next.js app** as new route
handlers and pages, backed by one Postgres database, with a deliberately small
domain module (`lib/network/`) that reuses the retained design's *contracts*
rather than its code volume:

- **Accounts**: email + password (argon-family KDF from Node's libcrypto:
  scrypt with memory-hard parameters, per-user salt, constant-time verify),
  server-side sessions (random 256-bit tokens, SHA-256 at rest, HttpOnly
  Secure SameSite=Lax cookie, server-side revocation). No third-party
  identity provider for the MVP; email verification is a recorded follow-up.
- **Profiles**: one canonical Markdown profile per account, validated by the
  same canonical v2 Markdown contract the guest builder uses. Private by
  default. Publishing is an explicit, reversible action; unpublishing
  conceals the profile without destroying the draft.
- **Discovery**: only explicitly published profiles, listed newest-first with
  exact/prefix handle lookup. Private data never enters discovery. There is
  no search index, no ranking, and no feed in the MVP.
- **Contact**: a contact request is the consent primitive. Sender may revoke
  a pending request; recipient may accept, reject, or block. Blocking is
  total for contact and messaging in both directions and cannot be undone by
  the blocked party. Acceptance opens exactly one conversation channel
  between the two accounts; that is the MVP's messaging consent.
- **Conversations**: minimal bounded text messages between the two parties of
  an accepted contact. Any party can close the channel by blocking or by
  revoking contact (revocation ends future messages but keeps history for the
  other party? No — history stays visible to both; the channel simply stops
  accepting new messages until contact is re-established).
- **Agent access**: versioned, token-authenticated API (`/api/network/v1`).
  Owner-issued grants are named, scoped (`profile:read`, `profile:write`,
  `contacts:read`), expiring, revocable, and shown once (only a SHA-256
  digest is stored). Agents can never send contact requests, never message,
  and never grant themselves consent — the authority matrix from
  docs/social-network.md carries over.
- **Privacy boundary**: the guest builder keeps working exactly as before.
  Local drafts are never uploaded by network code; the network reads profile
  Markdown only when its owner explicitly saves it to their account.
- **Operational posture**: rate-limited auth endpoints (per-account and
  per-IP, durable buckets), bounded request bodies, fail-closed authorization
  on every route, and 503-with-clear-contract when the database is not
  configured (so guest-only deploys stay green).

## Consequences

- `tools/check_standalone_site.py` and `middleware.ts` evolve from "network
  routes are retired" to "the retired backend surfaces stay retired; the new
  network MVP routes are active." The guest-only promises (no draft upload,
  no analytics on /human and /md) stay enforced by the same tooling.
- Production requires one Postgres database. Until it is provisioned
  (owner action queued), network routes answer 503 with an explicit
  configuration contract; guest routes are unaffected.
- Secrets (database URL, API-key pepper) are stored in the operator vault
  (gringotts) and injected at deploy time; only non-secret references live
  in configuration.
- Deliberately excluded from the MVP (roadmap, per docs/social-network.md):
  organizations, recruitment, feeds, notifications, moderation casework,
  follows, verification, MCP/A2A mirrors.
