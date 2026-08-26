# connect.md account export and deletion contract

## Release status

Account export and deletion are prelaunch, disabled-by-default human actions. The API and public flags default to `false`, and the deletion worker is absent unless the `account-lifecycle` Compose profile is explicitly selected. They must not be advertised or enabled until the API models, step-up authentication, concealment transaction, erasure worker, real Clerk provider integration, registered backup evidence, UI, and negative tests in this contract have passed together on the fresh dedicated connect.md VPS.

An account-level action is never an API-key, Agent Grant, MCP, A2A, organization, representative-agent, or moderator action. Only the signed-in Clerk human who owns the account may initiate it.

## Truthful lifecycle states

The durable account state machine is:

```text
active
  -> confirmation_pending
  -> concealed
  -> erasure_planned
  -> erasing
  -> held | failed | live_erasure_complete
  -> backup_expiry_pending
  -> fully_erased
```

The Clerk-provider state is tracked separately as `unsupported`, `pending`, `verified`, or `failed`. The backup state is tracked separately as `expiry_pending` or `verified`.

`live_erasure_complete` means only that the current connect.md PostgreSQL rows, local versioned files, Meilisearch projections, credentials, and live read models passed their erasure postconditions. It does not claim that Clerk or a backup generation has completed. `fully_erased` is available only after provider deletion is verified and every registered backup generation containing the data has expired or been cryptographically destroyed.

An active hold prevents a held item from being erased; it never prevents immediate concealment or credential revocation. A dead-lettered, failed, provider-pending, search-pending, file-pending, or backup-pending item prevents a terminal completion claim.

## Step-up authentication and confirmation

Export and deletion require a currently valid Clerk JWT plus provider-verified recent authentication. The API verifies the signed Clerk freshness claims and a unique reverification identifier; frontend text confirmation is an additional intent check, not a substitute for step-up authentication.

A deletion request is idempotent and initially enters `confirmation_pending`. Confirmation is a second, step-up-authenticated, human-only request and requires a caller-owned visible-ASCII `Idempotency-Key`. The key is retained only as a versioned HMAC bound to the deletion and lifecycle subject; raw keys, subjects, request bodies, and receipts never enter storage. After every database constraint is flushed but before the concealment transaction commits, the API appends an immutable HMAC-chained commitment outside PostgreSQL and Markdown storage, advances a separately keyed append-only head witness in an independently mounted host directory, and verifies exact full continuity and current-head equality across both authorities. Sequence zero is explicitly authenticated during one-time initialization. The commitment contains only deletion/generation identifiers, policy and timestamps, a keyed subject digest, and AEAD-encrypted recovery subject—not plaintext subject or user content; the witness contains only key fingerprints, sequence, journal-head and prior-witness digests, and observation time. Readiness closes across the append-to-commit window. Confirmation then changes the account to `concealed`, revokes every connect.md API key, Agent Grant, and active mandate, disables public Agent Identities, and removes ordinary public/search/social/recruitment visibility before asynchronous work starts. A crash or one-sided authority update is fail closed on restart because public start and search rebuild require the current witness, journal, and matching lifecycle/access-deny database mirror. Cancellation is allowed only before the external commitment boundary.

Every authenticated API path must consult account lifecycle state. Old Clerk JWTs, API keys, and Agent Grants for a concealed or later account are denied even if their signatures or expiry times would otherwise pass.

## Lifecycle Receipt contract

Creating a deletion request returns its opaque deletion identifier plus a one-time `lr1_` Lifecycle Receipt. The receipt is a bearer-like status credential: it grants no document access, cancellation authority, agent authority, or account session, but anyone holding it can read the request's sanitized lifecycle state. The account UI therefore shows it only in the current private view, requires the human to acknowledge saving it before confirmation, and never writes it to browser persistence, a URL, analytics, or logs.

`POST /v1/account/lifecycle-status` accepts only `Authorization: LifecycleReceipt <receipt>` and returns the bounded `account_lifecycle_status.v1` contract. The response exposes the lifecycle state, observation and policy timestamps, a stable condition code where applicable, a suggested next-check interval, and receipt expiry; it does not expose the Clerk subject, content, providers, holds, queue details, evidence, or internal errors. Invalid, cancelled, rotated, expired, and unknown receipts share the same `404` response. The endpoint is non-cacheable, rate limited, excluded from OpenAPI and agent discovery, and never polled automatically by the web client.

While a request remains `confirmation_pending`, a freshly reverified Clerk human may rotate and recover its receipt. Rotation invalidates the old receipt. Confirmation clears the request marker and stores only the confirmation-key HMAC. A lost acknowledgement can therefore replay the exact `202` deletion response during the bounded terminal receipt window, but only through a fresh route-private Clerk-JWT verifier that rechecks the deny row, provider state, tombstone, journal/witness, and full live mirror; it never accepts an API key, Agent Grant, impersonated token, or second-step-up bypass. Cancellation invalidates pending state. After the terminal receipt window, exact tombstone and mirror validation atomically scrubs the confirmation/status HMACs and receipt-rate counters while retaining the lifecycle, deny, journal, and tombstone authorities; subsequent confirmation replay returns uniform `404`.

## Export contract

An export has one consistent database cutoff and is streamed directly to the reverified human through a bounded, short-lived artifact. If a temporary artifact is required, it is encrypted, single-use, separately access-controlled, and placed under its own expiry task and tombstone.

The export may include:

- the requester's canonical Profile and Resume Markdown and safe version metadata;
- the requester's canonical professional posts;
- the requester's own authored messages, applications, proposals, contact requests, and safe relationship metadata;
- the subject-facing status and explanations for moderation and organization-verification submissions.

The export excludes:

- raw Clerk subjects, session identifiers, API keys, grant secrets, verifiers, prefixes, request hashes, idempotency bodies, and internal change payloads;
- another participant's private message or application content;
- reporter identity or narrative, hidden moderation evidence, staff identity, internal rationale, and private organization-verification evidence;
- Meilisearch projections, worker state, operational counters, holds, backup media, and infrastructure configuration.

Public profile handles or other public-safe counterparty references may appear only where needed to make the requester's own record understandable.

## Deletion plan and disposition rules

Each deletion request freezes a policy version and creates one durable item per resource. An item records only an opaque request identifier, resource type and identifier, phase, state, attempt count, stable error code, hold state, and timestamps. The lifecycle and access-deny rows remain durable authorities; terminal cleanup removes only expired confirmation/status HMAC markers and receipt-rate counters after exact journal/witness/mirror proof. No raw key, subject, body, receipt, or content is stored in a queue, log, error message, or idempotency record.

The dependency order is explicit:

1. conceal public documents, posts, organizations, jobs, applications, representative identities, and social/contact state;
2. revoke API keys, Agent Grants, mandates, and provider sessions;
3. delete subject-owned idempotency receipts, private change payloads, rate buckets, follows, blocks, notifications, and operational pair locks (the lifecycle/deny authorities and their terminal tombstone remain);
4. dispose or detach shared contact, application, moderation, organization, connection, conversation, and message records according to the rules below;
5. delete exact Markdown and evidence paths, their version rows, and then parent rows;
6. delete each Meilisearch document by ID and wait for task acknowledgement;
7. run the complete raw-subject, filesystem, search, public-route, and credential postcondition sweep;
8. delete the Clerk provider user and verify the provider result;
9. wait for registered backup generations to expire or be cryptographically destroyed.

Blind foreign-key cascades do not decide privacy policy. The worker handles each dependency only after its export, counterparty, audit, and hold disposition is explicit.

### Shared records

- Subject-authored message bodies are erased. Counterparty-authored bodies may be retained only in a per-party or detached record that contains no raw subject, deleted handle, or subject-authored content.
- Applications are immediately withdrawn and ordinary employer access ends. Subject-authored message and snapshot links are erased unless a documented hold requires a detached, content-minimal record.
- Contact and connection records are closed and hidden. Any retained counterparty receipt is no-content and uses a deletion-request-scoped opaque pseudonym, never a raw or publicly reversible owner identifier.
- Reports and moderation cases are erased unless an explicit safety or legal hold requires a detached minimum record. A subject export never receives reporter material or internal evidence.
- Organization membership is revoked. An account that owns an active organization is held after concealment until a separately authorized transfer or closure plan exists; ownership is never silently transferred and applicant content is never erased merely because an organization owner requested deletion.
- Verification evidence is deleted at its exact ledger path unless an explicit hold authorizes a detached minimum record with purpose, authority, review date, and expiry.

## Search, storage, provider, and backup boundaries

Meilisearch is an external privacy boundary because its document projection contains searchable public frontmatter and untrusted public body text. When lifecycle is explicitly enabled, its worker receives a separate exact-index key limited to document get/delete, task get, and index get. The privacy-critical unindex phase retains direct delete-and-absence attestation only after canonical visibility is private and before hard deletion; an unconfigured search client is retryable dependency failure, never absence proof, and no API create/update path writes Meilisearch. A content-free tombstone outbox row survives hard deletion so the ordinary projection worker idempotently reconciles absence again. The lifecycle remains non-terminal while any such tombstone exists and fails closed if one dead-letters; only a configured delete/absence attestation followed by tombstone consumption permits live or full erasure completion. Updating PostgreSQL visibility alone is not an erasure claim, although canonical reauthorization must immediately suppress a stale search hit.

The enabled worker's Docker health is evidence-based rather than process-based. It atomically refreshes a mode-`0600`, content-free heartbeat only after validating the external deletion-journal chain and exact live database mirror, querying aggregate queue/dead-letter state from PostgreSQL, staying within configured backlog and eligible-age bounds, authenticating the Clerk deletion credential without retaining provider content, and authenticating its exact Meilisearch index. A journal mismatch suppresses lifecycle processing. Any dead letter, failed lifecycle, stale eligible work, excessive backlog, invalid credential, unavailable dependency, stale heartbeat, or malformed heartbeat fails health. Paused intent is restored only after the worker first reaches healthy state.

Local file disposal uses only the exact server-generated path already recorded for the resource. Path validation, hold checks, durable retries, and a final recorded-path sweep are required. No recursive broad delete is part of account erasure.

Clerk session revocation and user deletion run through a dedicated server-side provider adapter. Provider tokens and response bodies never enter application logs or lifecycle rows. A provider failure leaves connect.md concealment and credential denial in force and the request retryable.

Generic lifecycle-aware processes validate only the lifecycle, journal, and witness authorities they use. The Clerk Backend secret and base URL are injected only into `account-erasure-worker`; the public API and `search-admin` never receive them. The worker and the explicit `account-erasure` CLI validate that provider configuration before creating an engine or provider client.

Every accepted PostgreSQL-plus-Markdown backup generation has an immutable registry manifest with creation and expiry timestamps and both artifact digests. Its checksummed metadata and external immutable receipt also bind the exact authenticated, witness-verified deletion-journal head captured while deletion-capable services are stopped. Meilisearch is rebuildable and is not backed up as an authority. Restore refuses a generation whose checkpoint is not the current journal head, and restore/deploy/rebuild/API/worker startup authenticate the full witness chain, require its current head to equal the full journal chain, and require exact bidirectional parity between journal commitments and concealed lifecycle/access-deny rows. Rolling PostgreSQL and the journal back together while preserving the later witness is therefore rejected. This is a refusal gate, not a full restored-data replay engine: an older generation, orphan commitment, missing witness, or one-sided authority state remains unavailable pending an explicit recovery procedure. `backup.sh` records lifecycle enablement and exact running/restarting/paused worker intent, reconciles every receipt-verified existing generation through the idempotent registration CLI, verifies and registers the new generation, and writes a mode-`0600` non-content receipt before success. A failure before the first registration or artifact mutation restores the should-run worker to fresh health and re-pauses only prior paused intent; after that boundary, unknown finalized or staged data, missing or mismatched receipts, registration failure, or pending retirement proof leaves both deletion-capable services stopped for explicit recovery.

The API alone mounts both authorities read-write. `search-admin` and `account-erasure-worker` mount both read-only and can only verify them. The witness host path and key are separately configured, pinned in release state, and cannot be nested within or moved into `CONNECTMD_BACKUP_DIR` or the journal. This raises the rollback bar but does not defeat control of the host: a root operator can destroy or replace the local journal, witness, database, and release pins together. Lifecycle activation therefore requires proof that every witness entry is durably preserved and retrievable from an independently administered off-host immutable/WORM destination. This repository does not provision that external destination, so repository tests alone cannot satisfy that deployment gate.

Expired local generations are not removed by age or a wildcard prune. The operator script validates the exact immediate-child directory and its receipt, writes a durable non-content proof, deletes only that directory, and then supplies the proof digest to `account-backup expire`. If the transition fails, the database manifest remains active and the deletion-capable services remain stopped. Until the last registered generation that may contain the erased data expires or is cryptographically destroyed, the truthful state is `backup_expiry_pending`. The default operator retention is 30 days, but the recorded state follows the actual configured on-VPS and approved off-VPS encrypted retention.

## Completion postconditions

Before `live_erasure_complete`, the worker must prove all of the following:

- no current table contains the raw Clerk subject in any owner, participant, actor, reviewer, confirmer, inviter, or rate-bucket field;
- no public or private ordinary-user route can read a deleted document, post, identity, organization, job, application, contact, connection, conversation, message, notification, proposal, or case;
- every old Clerk JWT, API key, Agent Grant, and mandate is denied;
- every recorded canonical Markdown, post, temporary export, and disposable evidence path is absent;
- every deleted document ID is absent from Meilisearch;
- no queued, leased, retrying, dead-lettered, or unreviewed held deletion item remains;
- remaining audit or hold records contain only an opaque deletion pseudonym and minimum non-content metadata.

The final tombstone contains only an opaque deletion ID, policy version, phase results, and timestamps. Re-registration creates a new account, restores no content or authority, and cannot silently reclaim retired profile handles, Agent Identity handles, or organization control. Lifecycle and access-deny rows are retained indefinitely; only the expired receipt/confirmation marker material is scrubbed after the exact terminal proof and retention window.

## Deployment isolation

This lifecycle applies only to a fresh dedicated connect.md Hostinger VPS. It must never inspect, connect to, reuse, modify, delete from, or make claims about Hermes or any existing Hostinger instance, credential, service, network, volume, DNS record, or backup. The feature remains disabled until the first generation is registered and the real Clerk provider, PostgreSQL, internal Meilisearch, public TLS, backup, isolated restore, and worker drills all pass.
