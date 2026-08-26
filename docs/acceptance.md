# connect.md acceptance contract

connect.md is releasable only when every critical assertion below passes against the integrated repository. A check that cannot run in the current environment remains explicitly unverified; it is not silently treated as a pass.

## Agent-first behavior

- `GET /llms.txt` identifies authentication, OpenAPI, Markdown schemas, primary endpoints, content negotiation, and copyable create/read/update/search examples.
- `/llms-full.txt`, `/v1/capabilities`, protected-resource metadata, the A2A Agent Card, and MCP tool discovery agree on implemented capabilities and never advertise an unimplemented privileged path.
- Global public Agent Identity discovery is parity-complete across `GET /v1/agent-directory`, MCP `list_agent_directory`, and A2A `list_agent_directory`; every surface is bounded, active/public/owner-matched, privacy-minimal, and discovery-only.
- Single public Agent Identity reads are parity-complete across `GET /v1/agent-identities/{handle}`, MCP `get_agent_identity`, and A2A `get_agent_identity`; each uses strict bounded handle validation, the active/public/owner-matched predicate, the exact five-field allowlist, and discovery-only authority. Missing, inactive, private, or owner-mismatched identities use bounded transport errors and never disclose private identity state.
- Agent Identity lifecycle writes remain Clerk-human-only HTTP: `POST /v1/agent-identities` and `DELETE /v1/agent-identities/{handle}` require a caller-owned 1-128 visible-ASCII `Idempotency-Key`, replay the safe original `201` or empty `204` result for an identical retry, reject same-key payload/method/path collisions, and fail closed on receipt or live identity/profile drift without exposing owner identifiers or private content. They add no MCP/A2A authority.
- A2A 1.0 `HTTP+JSON` public search works through `/a2a/message:send`; authenticated contact requests reuse the same grant, policy, idempotency, quota, block, and audit boundaries as HTTP.
- `POST /v1/profiles` and `POST /v1/resumes` accept canonical Markdown with minimal JSON or raw Markdown request friction.
- Owners can update a document through a full replacement without supplying server-owned identity or version fields.
- `Accept: text/markdown` and explicit `.md` reads return identical canonical bytes and an appropriate Markdown content type.
- Version history is append-only; a successful update increments the version and preserves the previous bytes.
- Reads emit a strong `ETag`; a stale `If-Match` fails without creating a version.
- Repeating an identical ordinary logical write with its `Idempotency-Key` replays the original response; the documented one-time-secret API-key create exception returns typed recovery metadata without the secret. Reusing that key with different input is rejected without mutation.
- `cnd_...` owner API keys are Clerk-human-managed bootstrap/simple-automation credentials. Continuous agents should prefer a scoped, expiring Agent Grant. `POST /v1/api-keys` and `DELETE /v1/api-keys/{key_id}` are Clerk-only HTTP operations requiring a 1-128 visible-ASCII `Idempotency-Key`. The first create response returns the random API secret once; an identical retry returns `recovery_required=true` metadata with no secret, which is safe recovery, not exact body replay. If the first secret is lost, the owner must revoke the returned key/prefix and create a replacement. The credential row, safe event, and empty receipt commit atomically; receipts and events contain no credential secret, hash, or pepper. Revocation replay is an exact empty `204` and fails closed if its owner-bound key or receipt cannot be reconstructed.
- Proposal acceptance and rejection require a visible-ASCII `Idempotency-Key` bound to the proposal and action; identical retries replay the exact decision JSON and headers, while cross-action reuse is rejected. Decision receipts contain no proposal Markdown or private payload, and acceptance commits the decision, event, document version, projection task, and provisional receipt atomically. The accept receipt binds proposal/action/document/version/SHA-256; provisional-header recovery derives the exact ETag/search state from that matching immutable version, and tamper or corruption fails closed. This decision remains Clerk-owner HTTP only.
- Owner inventory and change feeds use bounded cursors and remain authoritative when search is unavailable.
- `GET /v1/posts?limit=&cursor=` is an anonymous no-store, metadata-only chronological public-post inventory. It applies the current published Post plus exact current public owner/document/handle Profile predicate while locks are held, verifies canonical bytes before exposing title/topics, uses a route-scoped 1..500-character cursor and `published_at DESC, id DESC`, returns no total/ranking/private-feed data, and never calls or indexes post Markdown in Meilisearch, MCP, or A2A.
- Clerk-human employer inventory lists only owned or actively administered organizations and their draft, published, or closed jobs; API keys and Agent Grants receive 403 before tenant queries, cursors are signed and subject/endpoint-bound, and responses omit private membership, verification evidence, application, snapshot, and credential fields.
- Employer inventory is documented in OpenAPI, `/v1/capabilities`, and `/llms-full.txt` as private HTTP only and is absent from concise `/llms.txt`, MCP, A2A, the Agent Card, public search, and sitemap.
- Anonymous reads and search never disclose non-public documents.
- OpenAPI describes request bodies, authentication, response formats, errors, and examples.
- API keys are scoped to an owner, shown once, stored only as a hash plus non-secret prefix, and revocable.
- Agent Grants are named, expiring, revocable, resource-bound, explicitly scoped, and distinguish proposal-only from direct publication authority. `POST /v1/agent-grants` is Clerk-human-only and requires a 1-128 visible-ASCII `Idempotency-Key` before generating a `cng_` secret; the first `201` response shows the random secret once, while an identical same-owner retry returns only typed `recovery_required=true` metadata with `Idempotency-Replayed: true`, never the secret. A different intent or operation collides with `409`. The grant row, safe event, and empty receipt commit atomically; receipts/events contain no secret, verifier/hash, pepper, authorization header, or private body. Recovery reauthorizes the live owner/resource and rejects revoked, expired, mandate-bound, missing, substituted, or corrupted state with bounded `503`; grant issuance is Clerk-owner HTTP only and absent from MCP/A2A.
- Every agent mutation records the effective credential or grant identity in the durable change ledger.
- MCP management tools enforce the same authorization, ownership, idempotency, and conditional-write rules as direct HTTP calls.

## Agent outreach and representation

- A profile can disclose structured owner-attested representation and public contactability without exposing private mandates or credentials.
- Agent outreach uses authenticated, idempotent, platform-mediated contact requests gated by the target's contact policy.
- MCP `send_agent_outreach` and `get_agent_outreach_status` reuse the canonical mandate-bound HTTP authority and expose only the safe receipt/status shapes; they never expose message text, mandate or grant identifiers, rejection detail, quota/block state, or external endpoints, and they do not add contact-decision authority.
- Persisted contact-request status is constrained to `pending`, `accepted`, `rejected`, `blocked`, and `reported`; status migration preflight fails closed on unknown rows without coercion, while outreach's external `declined` value remains a projection only.
- Action-level A2A contact and outreach failures use only bounded stable error codes/messages; they do not expose raw HTTP details, headers, private content, recipient state, or authority internals, and they do not change HTTP-equivalent authorization.
- Recipients can accept, reject, block, or report a pending request and every transition is auditable.
- Contact-policy updates and request decisions require a visible-ASCII `Idempotency-Key`; policy responses and decision outcomes replay from atomic, privacy-minimal receipts after lost acknowledgement, with collisions rejected and agent-outreach decisions restricted to Clerk-human recipients.
- Accepting contact never grants document access, profile authority, private contact data, or permission for external outreach.
- Blocked pairs cannot create another request; request size and daily quotas are bounded.
- Profile Markdown is untrusted data and cannot change tool instructions, authorization, contact policy, or server-side egress behavior.
- The service does not fetch or invoke arbitrary profile or representative-agent URLs.

## Trust, safety, and lifecycle gates

The private social graph, owner-attested AgentIdentity directory, verified-organization-gated recruitment, conversations, notifications, post moderation/appeals, and human-only account lifecycle are implemented pre-launch domains and must continue to pass every applicable assertion below. Account export/deletion remains disabled by default until the fresh-VPS provider, backup, restore, worker, and negative-test drills pass; external delivery remains disabled and unimplemented. The detailed contracts are [trust-safety.md](trust-safety.md) and [account-lifecycle.md](account-lifecycle.md).

- Public profiles, structured data, search, rankings, and badges distinguish self-attested representation/employer claims from active organization confirmation and platform verification; a URL, domain, display name, Markdown field, or Agent Grant name cannot establish a verified fact.
- Effective sensitive authority is the intersection of authenticated human, active credential, scope/mode, exact resource, accepted organization membership when applicable, active human-issued mandate, target consent, and operation state. Agents cannot issue or expand their own mandates, memberships, verification, moderation authority, or account-level privacy controls.
- Organization membership is invited, accepted, role-bound, auditable, revocable, and enforced server-side. Unverified organizations cannot present as employers/recruiters, publish jobs, receive applications, or create public representative authority.
- Connections, follows, conversations, messages, and notifications have distinct private records and explicit state transitions. They neither enumerate graph data nor expose presence, read state, contact channels, or message content through cursors, counts, feeds, search, logs, or notifications.
- Follow and content-block mutations are Clerk-human HTTP operations requiring a caller-owned 1-128 visible-ASCII `Idempotency-Key`; exact retries preserve the existing follow `200` JSON or empty `204` contracts, same-key method/handle collisions return `409`, and receipts reauthorize live target/state before replay. Blocks remove both follow directions atomically, while missing or changed receipt/state/authority fails closed without exposing graph identifiers. These mutations are absent from MCP and A2A.
- An application is human-confirmed and gives an employer only an immutable applicant-selected snapshot for the named job and organization. Review, acceptance, rejection, and withdrawal are Clerk-human HTTP decisions requiring a visible-ASCII `Idempotency-Key`; organization and job locks precede live recruiting-authority checks, and authority loss prevents even an old employer decision receipt from replaying. Each transition commits its status, two safe events, and an empty-body receipt atomically; lost-ack retries reconstruct the exact public `ApplicationResponse` only while the owner/authority, relationships, retention, status, immutable snapshot digest, and decision facts still match. Withdrawal immediately removes ordinary access; retention, closure, deletion, and any exception are explicit and auditable. Application receipts bind application/job/organization/action and a SHA-256 of the exact response and immutable facts without storing Markdown, messages, or other private payload.
- Private message, application, report, contact, and verification content is excluded from public/search projections, generic audit payloads, idempotency replay data, logs, and error reports.
- Reports create separate, least-privilege moderation cases with attributable actions and appeals. A report alone does not establish wrongdoing or automatically impose a global sanction.
- Hidden post-moderation reviewer routes are Clerk-JWT-only, reject impersonation, require a constant-time match to the exact independently configured moderator or appeal reviewer, and are absent from OpenAPI, `llms`, capabilities, MCP, and A2A. Queues expose only current open cases or submitted/current appeals; details use shared read locks and verified canonical Markdown, return the same strong evidence-snapshot `ETag` in the header and body, and exclude raw identities, storage/digests, grants, audit rows, and internal evidence. Decisions require a single strong `If-Match` and visible-ASCII idempotency key, commit exactly one empty `204` receipt transaction, and replay only after validating configured staff, route ID, terminal action, and digest.
- Only the human data subject initiates export or deletion. Export excludes third-party private data and secrets; deletion immediately removes public/search/sitemap visibility, tracks erasure, revokes relevant authority, and states shared-record and backup-retention treatment.
- Any future user-controlled outbound URL passes only through a default-deny egress broker that enforces destination validation, private-network and redirect defenses, limits, credential isolation, quotas, and auditable outbox delivery. Internal contact acceptance never enables external delivery.
- Contract tests cover forged claims, expired/revoked/self-issued authority, unaccepted membership, graph enumeration, quota/idempotency races, application over-disclosure and withdrawal, moderation abuse, deletion/export leakage, and SSRF/DNS-rebinding attempts. API/OpenAPI, MCP/A2A discovery, UI, and operations material advertise only controls that pass those tests.

## Markdown integrity

- Profile and Resume schemas require their schema identifier, schema version, server identity fields, structured discovery fields, and deterministic heading hierarchy.
- Invalid YAML, duplicate keys, unknown required-contract fields, wrong schema identifiers, invalid visibility, malformed timestamps, and missing headings fail with actionable errors.
- Server-owned fields cannot be forged during creation or update.
- Valid examples pass and intentionally invalid fixtures fail.
- Stored bytes are UTF-8 with LF endings and a stable SHA-256 in the version ledger.
- Profile/Resume canonical Markdown enforces the package-owned 131072 UTF-8-byte (128 KiB) limit after LF canonicalization. The input is byte-bounded and YAML aliases are rejected before frontmatter construction. The API returns bounded 413 for HTTP create/update/proposal submit/accept, MCP returns `payload_too_large`, and ingestion returns a structured unpublished 422 without truncated output. Raw transport limits and JSON Schema character limits are separate controls; oversized historical artifacts fail closed during validation and projection rebuilds.

## Ingestion and search

- Ingestion enforces type and size limits before expensive parsing.
- PDF, DOCX, Markdown, and plain text return a validated draft or an explicit structured failure with warnings and provenance.
- Ingestion never publishes or mutates a profile/resume implicitly.
- Deterministic conversion works without an LLM credential.
- Search supports query, kind, occupation, industry, normalized skills, language, structured location, seniority, work mode, availability, `open_to`, organization, representation, contactability, update time, and bounded pagination controls.
- Search exposes compact `GET /v1/search` and structured `POST /v1/search/query` with the canonical `q` field; MCP/A2A accept only the deprecated query-only alias and reject dual `q`/`query`. GET values are bounded to 80 characters, structured canonical IDs to 336, and the aggregate submitted list-value cap is 50 before deduplication.
- Search defaults to `mode=projection`, retaining the rebuildable Meilisearch 1050-candidate window and truthful bounded-completeness warning. Explicit `mode=exact` uses only the ready canonical PostgreSQL projection, never calls or falls back to Meilisearch, materializes at most 50,001 candidates, returns a fixed narrow-query `422` at that boundary, and is complete through 50,000 matches. Exact cursors are signed over revision/filter/taxonomy state, max 2048 characters, and require `offset=0`; exact totals and full-set deterministic facets support `facet_limit` 1..500. Missing, stale, or integrity-mismatched exact state fails closed without partial results, consistently across REST, MCP, and A2A.
- Exact snapshots retain current public v1 and v2 Profile/Resume documents for untyped `q`, `kind`, `skills`, `location`, and update-time searches. V1 documents have no taxonomy memberships and are excluded from typed taxonomy filters; taxonomy discovery and typed authority remain current public v2 PostgreSQL data.
- `/v1/taxonomies` and `/v1/taxonomies/{taxonomy}?q=&cursor=&limit=` enumerate only current public-v2 PostgreSQL terms with signed revision-bound cursors, no counts/source documents/owners/private facts, and no outreach authority. MCP and A2A registry actions mirror these routes.
- Search results expose stable identifiers, headline/title, structured metadata, timestamps, excerpts, facets, and canonical HTML and Markdown URLs.
- Taxonomy facets are hydrated from PostgreSQL current-term authority after canonical reauthorization and agent filtering; each surviving document contributes at most once per alias, and conflicting labels/versions are represented as null/conflict rather than guessed.
- Public search accepts only the optional literal `agent_capability=internal_contact_request`; eligible profile hits expose at most ten current active public Agent Identity references with only `handle` and the fixed capability list, resumes expose none, and the discovery reference never authorizes outreach. REST, MCP, and A2A apply the filter before facets/count/pagination using bounded SQL enrichment; completeness remains limited to the 1050-document candidate window.
- Meilisearch loss does not corrupt canonical documents; intentionally unconfigured local search is explicitly non-blocking, while a configured unavailable or unauthorized exact index fails `/readyz` with HTTP 503 and write/search degradation semantics remain explicit.
- Every document create/update durably enqueues its version in the same database transaction; the worker retries expired leases with bounded backoff, supersedes stale tasks, indexes only current public bytes, and idempotently removes non-public documents.
- Projection retries, dead letters, logs, and operator recovery remain content-free and bounded; duplicate enqueue or replay cannot strand canonical work.
- The API is a read-only Meilisearch client using a search-only exact-index key; the sole general projection writer uses a separate restricted database role and projection key. The default-disabled lifecycle worker retains only its separately keyed delete-attestation exception, and no Meilisearch payload/settings field contains the account subject.
- Fresh deploy, restore, rollback, and manual rebuild hold application writers stopped until the master-key one-off admin rebuild succeeds; missing-document tombstones survive canonical deletion, and account lifecycle cannot become terminal before those tombstones attest final search absence.
- Enabled account lifecycle cannot pass deploy, health, backup, rebuild, reconfigure, or rollback on process state alone: a fresh content-free mode-`0600` heartbeat must prove bounded database work plus provider and exact-index search readiness. Enabled-but-absent is should-run intent, prior paused intent is restored only after health passes, and disabled-plus-absent remains profile-absent.
- Every deletion-aware gate authenticates the complete separately keyed head-witness chain and requires exact equality with the current deletion-journal head. The API alone mounts both authorities read-write; search administration and lifecycle work mount them read-only. Activation additionally requires a tested independently administered off-host immutable/WORM witness copy because local root can destroy all local authorities together.
- Rollback and destructive restore reject any target API image that does not declare the witness-aware deletion-authority contract; destructive restore also requires a pre-existing, exact, durable registration receipt.
- Fresh-host initialization requires the dedicated deploy account and both bind-mounted deletion-authority roots to share the API image's fixed UID/GID `10001`; ownership mismatches fail before containerized initialization.

## Human experience

- Human Mode supports guided profile/resume entry and drag-and-drop ingestion with visible progress, errors, validation, preview, and an explicit save/publish action. Markdown and plain text remain directly ingestible; PDF and DOCX fail closed with a structured unpublished 503 unless a configured isolated worker has a fresh heartbeat, and each accepted binary job is subject to the advertised hard conversion timeout and protocol-file cleanup.
- MD Mode loads Monaco client-side and provides a full canonical Markdown buffer, validation feedback, and sanitized live preview.
- Switching modes is lossless because both operate on one Markdown draft.
- Public pages render only canonical API content and sanitize untrusted Markdown/HTML.
- A public search route exposes URL-backed structured filters with explicit loading, degraded, empty, and result states.
- Owners can inspect and revoke Agent Grants, review grant activity, configure contact policy, and act on outreach from the frontend.
- Signed-in humans can discover and accept only their own organization invitations with a required visible-ASCII `Idempotency-Key`; identical acceptance retries replay the active owner-matched response, while organization owners revoke by membership ID only after the current owner check, with an exact empty `204` replay before deleted-member lookup. Invitation and removal changes commit atomically; invitation/acceptance receipts bind membership generation, and removal replay fails closed if the membership reappears, without exposing raw account identifiers or the inbox to agents. This app-level proof excludes a malicious byte-identical database clone.
- Public pages expose canonical and Markdown alternate links, appropriate JSON-LD, version/update evidence, and accurate owner-attested representation language.
- Authentication, loading, empty, unauthorized, offline, and server-error states are deliberate.
- Keyboard operation, focus visibility, labels, reduced motion, contrast, and responsive layouts receive baseline coverage.

## Production operations

- Compose defines API, a least-privilege search projection worker, frontend, PostgreSQL 16, Meilisearch, and Nginx with health checks, private data services, persistent volumes, and restart behavior.
- Only Nginx publishes public application ports; secrets are absent from images and source.
- Nginx routes discovery/API paths correctly, applies body/time/rate bounds, preserves forwarding headers, and emits security headers without breaking Clerk or Monaco.
- API migrations run as an explicit deployment step and are not raced by multiple replicas.
- The Hostinger guide covers Ubuntu/Docker, DNS, firewall, TLS, environment setup, deploy, health validation, update, logs, backup, restore verification, rollback, and search rebuild.
- Backups couple PostgreSQL and Markdown storage closely enough to restore a consistent version ledger; input verification is automated, while a destructive restore drill remains a fresh-target operator acceptance gate.
- CI performs schema fixtures, API tests/static checks, frontend lint/type/build, Compose validation, and a self-signed HTTPS Nginx discovery/MCP/A2A smoke test without deployment credentials.
- The HTTPS smoke provisions scratch journal and witness authorities, three distinct lifecycle keys, an isolated restricted search key, sequence-zero journal plus checkpoint, migration, and an empty-index rebuild before starting public services.

## Final deterministic checks

The exact commands may use the repository's documented package tooling, but final evidence must cover:

```text
API:       format/lint + type check + unit/integration tests + migration import/upgrade check
Frontend:  install from lockfile + lint + type check + production build + focused tests
Schemas:   valid fixtures pass + invalid fixtures fail
Infra:     docker compose config for base and production overlays + Nginx config and HTTPS discovery/MCP/A2A smoke tests
Security:  secret-pattern scan + dependency audit results (with triaged exceptions)
Contract:  end-to-end documents, conditional/idempotent writes, grants, changes, search, MCP/A2A discovery, and outreach smoke tests
```
