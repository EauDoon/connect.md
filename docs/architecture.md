# connect.md architecture

## Product invariant

Every public profile and resume has one canonical Markdown representation. Human-mode forms, Monaco editing, API writes, public pages, and search all read from or produce the same validated document. PostgreSQL exact-search/taxonomy projections and Meilisearch are projections around that source, not competing content stores.

## Runtime topology

```text
Internet
   |
 Nginx (TLS termination, limits, security headers)
   |-- /v1, /mcp, /a2a, /schemas, /.well-known, docs, OpenAPI, llms --> FastAPI
   `-- everything else ------------------------------------------------> Next.js
                                                      |
FastAPI --> Clerk JWKS / API-key and Agent-Grant verifier
   |                                                   |
   |-- converter (default; isolated with no network access)
   |-- PostgreSQL 16: identity, grants, outreach, idempotency, change ledger,
   |                  taxonomy and exact public-search projections, outbox
   |-- local storage: immutable canonical-document versions and application-scoped snapshots
   |-- search-projection-worker (default) --> Meilisearch: rebuildable public projection
   `-- account-erasure-worker (optional; disabled by default)

Next.js --> FastAPI only; it never writes directly to PostgreSQL or storage
```

Docker Compose runs `frontend`, `api`, a default isolated no-network `converter`, the default `search-projection-worker`, `postgres`, `meilisearch`, and `nginx`, plus an explicit one-off `search-admin` profile and the optional disabled-by-default `account-erasure-worker`. PostgreSQL, Meilisearch, and Markdown storage use named volumes. Only Nginx publishes host ports in production. The projection worker receives only a restricted database login, read-only Markdown storage, one index-scoped projection key, and the internal data network; it receives no Clerk, application API-key, or master-key authority. Today there are exactly three distinct release image identities: `connectmd-api`, shared by the API, converter, projection, admin, and worker processes; `connectmd-web`; and `connectmd-nginx`. The converter is not a separate release image, and this source-level topology is not live-deployment evidence.

## Repository boundaries

| Path | Responsibility | May depend on |
| --- | --- | --- |
| `apps/api` | HTTP API, auth, validation, versioned persistence, conversion, indexing | `packages/markdown-schemas`, PostgreSQL, Meilisearch, Clerk |
| `apps/web` | Human mode, Markdown mode, public rendering | public API and Clerk frontend SDK |
| `packages/markdown-schemas` | format specifications, JSON Schemas, examples, fixtures | no application runtime |
| `infra` | Compose-facing proxy and deployment operations | built application images |
| `docs` | architecture, API and operator guidance | repository artifacts |
| `storage` | runtime data contract only | mounted exclusively by API |

Each implementation lane owns only its path. Cross-boundary changes are integrated centrally.

## Document contract

Canonical documents use UTF-8, LF line endings, YAML frontmatter, and deterministic hierarchical headings. Their frontmatter includes:

- `schema`: stable schema identifier (`connect.md/profile` or `connect.md/resume`)

The final rendered canonical Profile/Resume Markdown is limited to 131072 UTF-8 bytes (128 KiB) after LF canonicalization. `packages/markdown-schemas/canonical-markdown-limits.json` is the single numeric source for this pre-launch contract; schema character limits and raw upload limits are separate controls. Markdown is byte-bounded and YAML aliases are rejected before frontmatter construction. Fresh HTTP writes over the limit return 413, fresh MCP document writes return structured `payload_too_large`, and ingestion returns its structured unpublished 422 without truncated output. Oversized historical artifacts fail closed during validation and projection rebuilds.
- `schema_version`: document contract version
- `id`: immutable UUID
- `owner_id`: immutable connect.md-local pseudonymous owner identifier; the Clerk subject binding remains private in PostgreSQL
- `slug` or `handle`: public identifier
- `version`: monotonically increasing integer
- `updated_at`: UTC timestamp
- structured search fields such as name, headline, location, skills, and visibility

Writes validate frontmatter and required heading structure before persistence. The API assigns identity, ownership, version, and timestamps; clients cannot forge them. Unknown frontmatter is rejected for strictness. Published content is sanitized when rendered.

Storage layout:

```text
storage/
  profiles/<document-id>/versions/000001.md
  resumes/<document-id>/versions/000001.md
  applications/<application-id>/snapshot.md
  verification-evidence/<organization-id>/<verification-id>/<sha256>.bin
  .connectmd-artifact-staging/v1/<intent-id>/<created-ns>-<nonce>.{bin,json}
```

Version rows are append-only and record the content SHA-256, actor, creation time, and storage path. Updating a document creates a new file, database version, and version-keyed projection task in one database transaction. The API reports the projection as queued and performs no direct Meilisearch writes. The sole long-lived projection writer leases those content-free tasks, retries with bounded backoff, supersedes stale versions, and indexes only the canonical current version when it is public. A current non-public or missing document causes idempotent removal without loading or transmitting private Markdown. Lifecycle erasure retains a separate delete-only attestation exception before hard deletion. Tombstone tasks survive canonical deletion and converge that deletion again.

Application submission and organization-verification submission use one durability protocol for local immutable bytes plus their PostgreSQL authority graph. A versioned, length-framed HMAC intent derives a deterministic UUID from the continuity-pinned API pepper, flow, owner, stable target, and idempotency key. The API writes a signed, content-free descriptor and exact payload into the strict staging namespace, fsyncs them, promotes without overwrite, and retains staging authority until the complete relational graph and idempotency receipt receive a commit acknowledgement. A lost acknowledgement is resolved in a fresh session under the same PostgreSQL advisory-intent and Organization-then-Job lock order: only a complete path/digest/size/byte/graph/receipt match replays success; only proven total absence permits exact digest-and-size deletion; split or unavailable authority preserves bytes and fails closed.

Application submission copies the exact applicant-selected public Profile or Resume bytes into a private, immutable application-owned path and records that copy's version, digest, byte length, and path in PostgreSQL. Legacy application rows without an authoritative byte length fail closed. The snapshot is not a second canonical document and never enters search. Only a signed-in human member of an organization with active `recruiting_control` authority may read it, with the explicit `X-Connectmd-Purpose: job_application_review` header. Withdrawal, retention expiry, missing or altered bytes, and loss of recruiting authority all deny employer access. Retention and applicant account erasure remove the snapshot file before deleting its application row; deleting an employer account does not erase an applicant-owned snapshot before normal application retention.

## API surface

All machine endpoints are versioned under `/v1` and return JSON by default. A document read returns raw Markdown for `Accept: text/markdown`; explicit `.md` aliases make discovery and scripting trivial.

```text
POST   /v1/profiles
GET    /v1/profiles/{handle}
GET    /v1/profiles/{handle}.md
PUT    /v1/profiles/{handle}
GET    /v1/profiles/{handle}/versions
GET    /v1/profiles/{handle}/versions/{version}
GET    /v1/profiles/{handle}/versions/{version}.md

POST   /v1/resumes
GET    /v1/resumes/{slug}
GET    /v1/resumes/{slug}.md
PUT    /v1/resumes/{slug}
GET    /v1/resumes/{slug}/versions
GET    /v1/resumes/{slug}/versions/{version}
GET    /v1/resumes/{slug}/versions/{version}.md

POST   /v1/ingest
GET    /v1/search
POST   /v1/search/query
GET    /v1/taxonomies
GET    /v1/taxonomies/{taxonomy}
GET    /v1/public-documents
GET    /v1/capabilities
GET    /v1/me
GET    /v1/documents
GET    /v1/changes
POST   /v1/api-keys
GET    /v1/api-keys
DELETE /v1/api-keys/{id}
POST   /v1/agent-grants
GET    /v1/agent-grants
DELETE /v1/agent-grants/{id}
POST   /v1/proposals
GET    /v1/proposals
POST   /v1/proposals/{id}/accept
POST   /v1/proposals/{id}/reject
GET    /v1/contact-policy
PUT    /v1/contact-policy
POST   /v1/contact-requests
GET    /v1/contact-requests/inbox
POST   /v1/contact-requests/{id}/{action}

GET    /v1/agent-directory
GET    /v1/profiles/{handle}/agent-identities
POST   /v1/agent-outreach

POST   /v1/posts
GET    /v1/posts/{id}[.md]
GET    /v1/feed
POST   /v1/follows/{handle}
POST   /v1/connection-requests
GET    /v1/connections
GET    /v1/conversations
POST   /v1/conversations/{id}/messages
GET    /v1/notifications

POST   /v1/organizations
GET    /v1/employer/organizations
GET    /v1/employer/jobs
POST   /v1/organizations/{slug}/verification-submissions
POST   /v1/organizations/{slug}/jobs
GET    /v1/jobs
POST   /v1/organizations/{slug}/jobs/{job}/applications
GET    /v1/organizations/{slug}/jobs/{job}/applications/{id}/snapshot
GET    /v1/organizations/{slug}/jobs/{job}/applications/{id}/snapshot.md
GET    /v1/applications
GET    /v1/moderation/cases
POST   /v1/moderation/cases/{id}/appeals

POST   /mcp
POST   /a2a/message:send
GET    /.well-known/agent-card.json
GET    /.well-known/oauth-protected-resource
GET    /.well-known/oauth-protected-resource/mcp
```

The OpenAPI document is available at `/openapi.json`; `/llms.txt` gives concise discovery while `/llms-full.txt` contains the complete integration and safety contract. MCP exposes bounded HTTP-equivalent operations as tools. The A2A 1.0 Agent Card advertises the implemented `HTTP+JSON` interface for public search and authenticated, policy-gated internal contact requests.

Reads emit strong validators and content digests. Logical writes support durable idempotency and conditional updates. Owner inventory and the durable change ledger use opaque keyset cursors; search is never used as a synchronization mechanism.

Explicit `mode=exact` selects the canonical PostgreSQL search projection rather than Meilisearch. It stores current public v1 and v2, version- and SHA-256-bound display/search fields and compact filter values, requires PostgreSQL, materializes at most 50,001 candidates, and returns a fixed narrow-query `422` at that boundary. Exact results are complete through 50,000 matches, use signed 2048-character cursors with `offset=0`, and compute deterministic full-set facets with `facet_limit` 1..500. V1 snapshots support untyped `q`, `kind`, `skills`, `location`, and update-time searches; they carry no taxonomy memberships and are excluded from typed taxonomy filters. Public writes, concealment, erasure, backfill, and verification keep the exact projection synchronized or fail closed; exact mode never falls back to Meilisearch or returns partial integrity failures.

Employer inventory is a separate private, read-only HTTP surface for signed-in Clerk humans. `GET /v1/employer/organizations` returns only organizations the caller owns or actively administers, while `GET /v1/employer/jobs` returns all draft, published, and closed jobs in those organizations. Both use endpoint- and subject-bound signed keyset cursors, return privacy-minimal summaries, create no mutation or audit records, and are intentionally unavailable to API keys, Agent Grants, MCP, A2A, the Agent Card, public search, sitemap, and concise `llms.txt`.

## Authentication and authority

Human sessions use Clerk-issued Bearer JWTs verified by the API against Clerk JWKS. Simple agent calls may use connect.md-issued owner API keys. Continuous agents use named, expiring Agent Grants constrained to an owner, exact document, or exact organization, explicit scopes, and `proposal_only` or `direct` publication mode. Only a prefix and strong one-way verifier are stored; secrets are returned once. Every durable event records the effective human, API-key, or grant identity. Public canonical Markdown exposes only a connect.md-local pseudonymous owner ID; the raw Clerk subject and private mandate remain server-side.

Anonymous callers may read public documents and search only public projections. Authenticated owners may create and update their resources. Server-assigned fields and ownership checks are enforced independently of the frontend.

Agent-to-agent outreach is consent-based and platform-mediated. A contact policy gates structured requests; recipients explicitly accept, reject, block, or report them. Contact acceptance never implies document authority. The API does not fetch or invoke arbitrary representative-agent URLs, preventing an untrusted profile from turning the service into an SSRF or spam relay.

## Social and trust boundary

The integrated pre-launch foundation now includes private follows and connections, consented conversations and messages, minimal notifications, human-authored Markdown posts and a chronological feed, verified-organization-gated jobs and private applications, owner-attested public Agent Identities with human-issued outreach mandates, and post moderation cases with independent appeals. These are relational authority records; none of them makes Markdown content authoritative. Public Agent Identities are discovery records, not verified credentials, online-presence claims, or permission to contact an external endpoint.

[Trust and safety contract](trust-safety.md) defines the controls that apply to those implemented domains and the remaining launch gates. [Account lifecycle contract](account-lifecycle.md) is implemented behind matching API and frontend flags that default to disabled: human-only export, immediate concealment, tracked live erasure, Clerk-provider deletion, and backup-expiry evidence remain unavailable in a release until the fresh-VPS provider, backup, restore, worker, UI, and negative-test drills pass together. External webhook, email, arbitrary A2A relay, URL-preview, and other user-controlled egress remain disabled.

## Ingestion pipeline

`POST /v1/ingest` accepts bounded PDF, DOCX, Markdown, or plain-text uploads. Markdown and plain text use the direct UTF-8 path. PDF and DOCX conversion is enabled only when `CONNECTMD_INGEST_JOBS_PATH` is configured and the no-network worker has a fresh heartbeat; otherwise binary ingestion fails closed with a structured 503 and no draft. The worker gives each conversion its own process and enforces `conversion_timeout_seconds`; the API cleans its job files after its bounded wait, while worker cleanup and recovery handle claimed or stale worker artifacts. `/v1/capabilities` reports worker configuration and heartbeat liveness separately, so configuration is not presented as runtime readiness. Binary conversion uses `markitdown` and layout-aware `unstructured` extraction when useful, then all paths perform deterministic normalization into the requested schema and strict validation. The MVP is deterministic and does not require or invoke an LLM. Ingestion returns a draft plus warnings and provenance; it does not publish automatically.

## Frontend contract

Human Mode provides guided, cinematic onboarding, drag-and-drop conversion, structured v2 discovery fields, preview, public-readiness checks, and an explicit save/publish step. MD Mode provides Monaco editing, schema-aware starter content, validation feedback, and live sanitized preview. Switching modes preserves a single draft buffer; no lossy round-trip through form state is allowed. Public profile and resume pages are rendered from the API's canonical Markdown.

## Production controls

- container health checks and restart policies
- non-root application containers where supported
- secrets supplied only through VPS environment configuration
- Nginx request limits, timeouts, rate-limit zones, and security headers
- database migrations before API rollout
- operator-installed daily PostgreSQL and Markdown-volume backups with documented restore checks
- Meilisearch treated as disposable and rebuildable, with durable content-free outbox convergence and bounded dead-letter recovery
- privacy-redacted bounded logs, request IDs, readiness/liveness endpoints, and bounded upload processing
- CI gates for API tests/type checks, frontend lint/type/build, schema fixtures, Compose validation, and an HTTPS Nginx discovery/MCP/A2A smoke test

## Deployment target

connect.md is deployed only to a brand-new, dedicated Hostinger KVM instance. Existing Hostinger instances, credentials, deployments, volumes, networks, DNS records, and backups are outside scope and must not be inspected, reused, connected to, or changed.

KVM 2 is suitable for light MVP traffic when conversion concurrency is constrained. KVM 4 is preferred for production headroom because Next.js builds, document extraction, PostgreSQL, and Meilisearch compete for memory and CPU. Images are built in CI or on the fresh VPS, then run behind Nginx using Docker Compose.
