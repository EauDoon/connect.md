# connect.md social-network contract

## Product boundary

connect.md is a professional network whose durable public identity is Markdown. A person's canonical Profile and Resume remain the source of truth for who they are and what they have done. Social features may project or reference those documents, but may not silently rewrite them or turn inferred data into owner-authored claims.

The network is designed for people and for explicitly authorized representative agents. Agent actions are authenticated, scoped, attributable, rate-bounded, and visible to the represented owner. Public Markdown, posts, organization descriptions, job descriptions, applications, and messages are untrusted content and never grant authority.

## Social domains

### People and representatives

- Public people discovery is backed by canonical public Profile and Resume documents.
- Representation is owner-attested unless connect.md records a separate verification state.
- Public representative records expose identity, declared capabilities, supported connect.md operations, and public discovery links only. They never expose grant secrets, private mandates, private contact data, or internal owner identifiers.
- A representative acts through a named, expiring Agent Grant. A public representative record is not itself a credential.

### Organizations, jobs, and applications

- An organization has explicit role-bound membership. Owners invite a current public profile handle, recipients accept from a private subject-only inbox, and owners inspect or revoke by membership ID without seeing raw account identifiers. A public organization record is owner-attested unless separately verified.
- A job belongs to exactly one organization and has an auditable draft, published, closed, or archived lifecycle.
- A signed-in human candidate applies as themselves and explicitly confirms one public Profile or Resume. Submission materializes the exact selected Markdown bytes as a private immutable application snapshot, fixed to the recorded version and SHA-256. Agents cannot submit or withdraw applications in the current implementation. The applicant retains their private application record; only a signed-in organization member with active recruiting-control authority and the explicit job-review purpose may read the snapshot. Withdrawal, expiry, failed integrity, or loss of that authority denies employer access.
- Application status changes are auditable and do not expose reviewer notes to public search.

### Following, connections, and activity

- Following is directed and revocable. A block overrides follows, connection state, feed delivery, and new outreach.
- A professional connection requires explicit recipient acceptance. Contact acceptance and connection acceptance are separate operations and never imply one another.
- Posts are Markdown, sanitized when rendered, and carry explicit visibility. Feed assembly is a projection over allowed posts; it is not a source of truth or an authorization channel.
- Ranking must be explainable and may not infer sensitive traits. The initial feed is chronological with bounded pagination.
- `GET /v1/posts?limit=&cursor=` is a separate anonymous, no-store chronological public-post inventory for discovery and sitemaps. It returns only allowlisted metadata from a currently public, owner- and handle-matched author Profile, is strictly ordered by `published_at DESC, id DESC`, and has neither ranking, totals, nor private-feed semantics. Post Markdown bodies are never indexed in Meilisearch, and the inventory adds no MCP or A2A action.

### Conversations and notifications

- A conversation is created only from an active bilateral human connection after messaging was requested and explicitly accepted. Contact-request acceptance changes only the outreach request state and never creates a connection or conversation.
- Conversation participants are fixed by server-side relationship state. Messages cannot add participants, grant document access, or authorize external contact.
- connect.md does not fetch URLs embedded in messages and does not relay arbitrary outbound A2A, email, or webhook traffic.
- Notifications contain the minimum useful event metadata and link to an authorization-checked resource. They are not a second copy of private message or application content.

## Authority matrix

| Action | Human owner | Owner API key | Direct Agent Grant | Proposal-only Agent Grant | Anonymous |
| --- | --- | --- | --- | --- | --- |
| Read public network data | yes | yes | yes | yes | yes |
| Read owner-private social, recruitment, or moderation data | yes, when participant/subject/member rules allow | no | no | no | no |
| Publish/update canonical Profile or Resume | yes | scoped | scoped and resource-bound | no; proposal only | no |
| Manage organization or job draft | authorized human owner/admin | scoped owner key | scoped exact-organization direct grant | no | no |
| Publish a job or change organization authority | applicable authorized human | no | no | no | no |
| Submit a job application | human confirmation required | prepare a draft only | prepare a draft only | no | no |
| Follow or request a connection | yes | no | no | no | no |
| Send a message | signed-in human participant | no | no | no | no |
| Create, withdraw, report, or moderate a post | applicable signed-in human | no | no | no | no |
| Change trust, verification, moderation, or another user's policy | no | no | no | no | no |

Every mutation must enforce ownership independently of the frontend, require the applicable scope, reject proposal-only credentials for direct actions, emit an attributable change event, and use idempotency for retryable creates. Private social actions, organization membership and verification, job publication, application submission, post publishing/moderation, export, and deletion are human-gated. The current API permits scoped organization and job-draft automation, but does not grant agents membership, publication, application, private-network, post, moderation, or trust authority.

The hidden reviewer workspace comprises only the six Clerk-human routes under `/v1/internal/post-moderation/` for the configured content moderator's open-case queue/detail/decision and the independent configured appeal reviewer's submitted-appeal queue/detail/decision. It is no-store and absent from OpenAPI, `llms` discovery, capabilities, MCP, and A2A. Exact configured identity, non-impersonation, subject/reviewer independence, a strong evidence-snapshot `ETag`, one strict `If-Match`, and a visible-ASCII idempotency key are mandatory. Reviewer payloads expose only canonical post Markdown, bounded report evidence, and safe case/appeal fields; they never return raw identities, storage paths, digests, grants, audit records, or internal rationale/evidence. Each review transition is one empty `204` transaction with a replayable receipt revalidated against configured staff, route, terminal row, action, and snapshot digest.

## Agent discovery contract

Agent-facing social capabilities must be advertised only after they are implemented and tested in all applicable surfaces:

- `/openapi.json` for the exact HTTP contract
- `/llms.txt` for concise discovery and safe first calls
- `/llms-full.txt` for authority, pagination, synchronization, and safety semantics
- `/v1/capabilities` for structured feature detection
- MCP tools for bounded operations that preserve the HTTP authorization model
- A2A skills only where the A2A handler actually implements the same policy and audit boundaries

Search results use stable identifiers, explicit entity types, structured industry/occupation/skill/location fields, visibility-safe excerpts, canonical URLs, and opaque bounded cursors. Search is a rebuildable public projection; owner inventories and change feeds are authoritative for synchronization.

Public search has one shared HTTP/protocol contract. Compact `GET /v1/search` accepts `q` plus direct canonical IDs or `tx1_` aliases up to 80 characters; `POST /v1/search/query` accepts the same named filters as bounded JSON with canonical IDs up to 336 characters. MCP `search_documents` and A2A `search` use the structured contract, accept legacy `query` only when `q` is absent, and reject dual `q`/`query`. Every transport enforces an aggregate 50 submitted list-value cap before deduplication. Search resolves typed values and authoritative facets from the ready current-public-v2 PostgreSQL taxonomy projection, while Meilisearch remains candidate/ranking only. Unknown, stale, or wrong-type typed values return zero without a Meilisearch request; malformed values are validation failures.

The default `mode=projection` preserves the bounded Meilisearch candidate/ranking behavior. Explicit `mode=exact` is canonical PostgreSQL-only and never falls back to Meilisearch: it requires PostgreSQL and a ready exact projection, is complete through 50,000 matches, materializes 50,001 only to reject an over-broad query with a fixed `422`, and uses signed revision/filter/taxonomy-bound cursors up to 2048 with `offset=0`. Exact search returns exact totals and full-set deterministic facets (`facet_limit` 1..500), and fails closed on missing, stale, or integrity-mismatched projection state. REST, MCP, and A2A share these semantics.

Exact snapshots retain current public v1 and v2 Profile/Resume documents for untyped `q`, `kind`, `skills`, `location`, and update-time searches. V1 documents have no taxonomy memberships and are excluded from typed taxonomy filters; taxonomy discovery and typed authority remain current public v2 PostgreSQL data.

`GET /v1/taxonomies` and `GET /v1/taxonomies/{taxonomy}?q=&cursor=&limit=` are anonymous, no-store, signed revision-bound discovery reads of currently observed public-v2 terms. They return no owner/source-document/count/private data. MCP `list_taxonomies`/`list_taxonomy_terms` and A2A actions with the same names mirror those routes. Taxonomy terms, labels, and representative claims never establish identity, verification, mandate, grant, consent, or outreach authority.

Public search also accepts the literal `agent_capability=internal_contact_request` discovery filter. Eligible profile hits may expose only `agent_identities: [{handle, capabilities: ["internal_contact_request"]}]`; resumes expose `[]`. The reference is not a credential or proof of mandate, grant, consent, contact policy, quota, block state, or recipient decision. The filter is SQL-only, uses the canonical public eligibility predicate, is applied before pagination, and is bounded to the 1050-document candidate window; AgentIdentity records never become standalone search hits or Meilisearch projection fields.

## Trust, privacy, and abuse controls

- Owner-attested, platform-verified, and externally verified claims are visibly distinct states.
- No URL supplied by a profile, representative, job, application, post, or message is fetched server-side without a separately designed allowlisted egress service.
- Pair blocks and moderation restrictions are checked before creating follows, connections, contact requests, conversations, messages, or applications.
- Rate limits are durable for high-abuse mutations and cannot be bypassed by concurrent requests or credential rotation under one owner.
- Private applications, messages, notifications, grant metadata, raw Clerk subjects, and moderation evidence never enter public search, sitemaps, Markdown routes, logs, or public activity feeds.
- Account export and deletion are not yet enabled. Their required immediate-concealment, erasure, provider, and backup semantics are defined in [account-lifecycle.md](account-lifecycle.md).

## Human information architecture

- **Discover**: people, representative agents, organizations, and jobs with URL-backed filters.
- **Network**: follows, connection requests, accepted connections, and a chronological activity feed.
- **Work**: employer organization/job management and candidate application tracking.
- **Inbox**: consent-gated contact, accepted conversations, and notifications.
- **Create**: Human Mode and MD Mode continue to edit one canonical Markdown buffer.

Human screens must expose the same authority distinctions as the API, especially owner-attested representation, proposal-only agents, application privacy, connection consent, and the difference between local readiness and a successful server mutation.

## Deployment isolation

This project is deployed only to a new, dedicated connect.md Hostinger instance. Hermes and every existing Hostinger instance, credential, service, volume, network, DNS record, backup, and deployment remain outside scope and must not be inspected, reused, connected to, or changed.
