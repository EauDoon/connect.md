# connect.md API

FastAPI service for canonical Markdown CRUD, immutable local-file versions and private application snapshots, Clerk/API-key authentication, bounded ingestion, and a rebuildable Meilisearch projection.

## Run and verify

Use Python 3.12:

```powershell
pip install -e ".[test]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
ruff check .
mypy app
pytest
```

Build the container from the repository root so the canonical schema package is included:

```powershell
docker build -f apps/api/Dockerfile -t connectmd-api .
```

`/healthz` is process liveness. `/readyz` verifies canonical storage and PostgreSQL; an intentionally unconfigured local Meilisearch dependency reports `not_configured` without failing readiness, while configured authenticated search must report `ok` or the endpoint fails closed with HTTP 503 and `search: unavailable`. The API binds port 8000. Production runs migrations as an explicit deployment step, not during application startup.

## Environment contract

All settings use the `CONNECTMD_` prefix. `CONNECTMD_DATABASE_URL` must be an async SQLAlchemy URL such as `postgresql+asyncpg://connectmd:...@postgres:5432/connectmd`. Production Clerk verification is all-or-none: configure `CONNECTMD_CLERK_JWKS_URL`, `CONNECTMD_CLERK_ISSUER`, and `CONNECTMD_CLERK_AUTHORIZED_PARTIES` (JSON list) together, with optional `CONNECTMD_CLERK_AUDIENCE`, or omit the URLs and audience and use an empty authorized-party list for public-only mode. Public-only mode keeps protected operations unavailable and cannot enable account lifecycle. JWT authentication is Bearer-only; the API does not accept Clerk session cookies. Same-origin deployment needs no CORS setting; a separate browser origin must be listed exactly in `CONNECTMD_CORS_ORIGINS`.

`CONNECTMD_API_KEY_PEPPER` is required and at least 32 characters in production. It is combined with opaque agent keys before Argon2id hashing; only a non-secret prefix and verifier are retained. A newly created agent key is returned once and cannot be retrieved again. If Meilisearch is configured, set `CONNECTMD_MEILISEARCH_URL` and `CONNECTMD_MEILISEARCH_API_KEY`; that internal search key is never returned by an endpoint. Production accepts HTTP only for an internal `meilisearch`, loopback, or private-IP host; external search endpoints must use HTTPS.

Mandate-bound agent outreach has separate durable sender, recipient-inbox, and direct-peer daily controls. `CONNECTMD_AGENT_OUTREACH_DIRECT_PEER_DAILY_LIMIT` sets the direct-peer limit (default `100`, allowed `1..10000`); the API HMACs the direct peer with the API-key pepper and never trusts forwarded client-IP headers for this control. Without a spoof-resistant trusted-proxy contract, a reverse proxy may be the shared direct peer, so this is coarse proxy-wide protection rather than end-user IP protection.

Production recruiting-control decisions additionally require `CONNECTMD_VERIFICATION_REVIEWER_ID` and `CONNECTMD_VERIFICATION_REVIEWER_ROLE=recruiting_verifier`. Organization owners submit bounded private evidence only; review, activation, rejection, expiry, suspension, revocation, and restoration are append-only internal operations restricted to that configured reviewer authority. The hidden reviewer workspace exposes the routine review, activation, and rejection actions; `python -m app.cli verification ...` remains the controlled operational path for the full lifecycle. Neither path accepts a caller-supplied reviewer identity, and activation is refused if organization material claims changed after submission. Evidence is never fetched, public, searchable, or returned by a read route.

Production post moderation additionally requires `CONNECTMD_POST_MODERATOR_ID` and `CONNECTMD_POST_MODERATOR_ROLE=content_moderator`. `python -m app.cli post-moderation withhold|restore --post-id ...` accepts no actor argument and fails closed unless that distinct internal authority is configured; each transition records that configured actor and role. Post reports never trigger moderation automatically.

## Retention operator runbook

Retention has no HTTP, MCP, A2A, OpenAPI, or public discovery control plane. Run the service-only worker with `python -m app.cli retention run --limit 100`. It queues expired applications, contact and connection requests, connections, conversations, messages, notifications, and eligible verification evidence; claims one task at a time with a lease; deletes only the exact validated server-generated evidence or application-snapshot path before deleting its owning row; then records a non-reconstructible tombstone. A legacy application without a materialized snapshot is never reconstructed from a mutable document. Expiry already denies reads before this worker runs. Re-running the worker is safe, and bounded failures are retried before becoming a content-free dead letter. The worker does not scan for or delete unowned-looking Markdown files; submission compensates its exact newly created path if the database transaction fails.

The agent-outreach direct-peer quota stores a stable HMAC of the normalized client IP, never the raw IP, under a UTC calendar-day key. It is pseudonymous abuse-control data needed only for the current UTC day. At the start of every retention run, the worker atomically deletes all prior-day direct-peer buckets while preserving current- and future-dated rows; this idempotent prune does not create lifecycle tasks or tombstones and is not included in content-retention result counts. Production must invoke the locked `infra/scripts/retention.sh` wrapper hourly so healthy-scheduler retention remains below roughly 25 hours; a missing or failed run is an operational retention failure.

For a narrow internal preservation hold, use `python -m app.cli retention hold --resource-type <type> --resource-id <id> --purpose <purpose> --authority <authority> --expires-at <RFC3339> --review-at <RFC3339>`. Release it with the same recorded authority using `python -m app.cli retention release --hold-id <id> --authority <authority>`. Holds delay disposal only; they never make expired data visible again.

`CONNECTMD_STORAGE_PATH` is the mounted version volume. It defaults to the repository `storage` directory in local development and `/app/storage` in the container. Do not put any secret in this repository.

## API behavior

Creates and full-replacement updates accept either `application/json` with `{"markdown":"..."}` or a raw `text/markdown` body. Creates omit server-owned `id`, `owner_id`, `version`, and `updated_at`. Updates may also round-trip the exact current canonical document; stale server fields return 409. JSON is the default read format. `Accept: text/markdown` and `.md` routes return identical canonical UTF-8/LF bytes.

A human-confirmed job application materializes the exact selected public Profile or Resume bytes at `applications/<application-id>/snapshot.md` and records the immutable path, version, and SHA-256 in PostgreSQL. Employer list, note, JSON snapshot, and `.md` snapshot reads are signed-in-human-only, require active recruiting-control authority and `X-Connectmd-Purpose: job_application_review`, and fail closed after withdrawal, expiry, authority loss, missing storage, or digest mismatch. The browser-facing JSON route embeds the verified Markdown; `Accept: text/markdown` and the explicit `.md` alias return the same stored bytes. Application snapshots never enter public search.

Ingestion accepts PDF, DOCX, Markdown, and text within `CONNECTMD_MAX_UPLOAD_BYTES` (10 MiB by default). In production, PDF/DOCX conversion runs through the no-network, no-secret `converter` service with a hard child-process timeout and bounded ephemeral job volume. It returns a validated unpublished v2 draft, warnings, and provenance, and never writes a document. Defaults use only source-derived names, headlines, and skill labels; all unavailable structured fields are neutral `connectmd-user-*` references or explicit non-disclosures (including `work_modes: []`). Use `connect.md/profile/v1` or `connect.md/resume/v1` only when an explicit legacy v1 draft is required.

Search is deliberately non-authoritative. A successful profile/resume write remains successful if indexing fails, with `X-Connectmd-Search: degraded`; search then returns an empty result set with `indexing_available: false`. Rebuild the projection from canonical files and PostgreSQL with:

```powershell
python -m app.cli rebuild-search
```

## Canonical contracts

The source contracts, valid examples, and invalid fixtures live in [`../../packages/markdown-schemas`](../../packages/markdown-schemas). Canonical read schemas are served at `/schemas/profile.schema.json` and `/schemas/resume.schema.json`; legal create frontmatter schemas are served at `/schemas/profile.write.schema.json` and `/schemas/resume.write.schema.json`. `/llms.txt` contains concise route and authentication discovery.
