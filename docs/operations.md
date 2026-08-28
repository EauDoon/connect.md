# connect.md operations

## Fresh-target boundary

Use these procedures only on the dedicated, freshly provisioned connect.md KVM created through [deployment.md](deployment.md). Use that target's fresh VPS IP, domain, SSH context, `.env`, Docker volumes/networks, and backup directory. Never run them on Hermes or point them at any existing Hostinger server, deployment, credential, volume, network, DNS record, or backup. The commands are operator instructions only; this repository performs no remote action.

Run these commands from the repository root as the dedicated deploy account. All commands use `.env` locally and never print its values.

## Health and logs

The API contract is `/healthz` for process liveness and `/readyz` for canonical storage, bounded artifact-staging reconciliation, PostgreSQL, the current-public-v2 taxonomy projection, migration-`0025` canonical exact-search readiness, and configured authenticated-Meilisearch readiness. Production performs one bounded startup scan and then a bounded periodic scan of at most 100 strict signed staging entries; a missing staging directory is healthy, while an attempted scan that finds an invalid, overbound, stale-unreconciled, or unavailable entry reports `storage: reconciliation_unavailable` and HTTP 503. Invalid, unknown, fresh, ambiguous, or unverified entries are preserved. Local/test processes do not run the reconciler unless both storage root and database are explicitly configured. Production returns HTTP 503 while exact search is absent, `backfill_required`, or fails integrity; it never starts publicly with partial exact authority. Local development may deliberately leave Meilisearch unconfigured and receive `search: not_configured`; once search is configured, an unavailable or unauthorized Meilisearch index returns HTTP 503 with `search: unavailable`. Nginx uses `/nginx-health` for its Docker health check. When account lifecycle is enabled, its profile worker writes a content-free mode-`0600` heartbeat only after the external deletion journal and live database mirror match exactly, a database queue snapshot and bounded backlog/dead-letter evaluation pass, and authenticated Clerk deletion-provider plus exact-index Meilisearch readiness succeed. A journal mismatch also suppresses lifecycle processing. Missing, stale, degraded, or dependency-failed evidence makes the container unhealthy; disabled lifecycle requires no worker. The health script retries each ordinary service for at most 30 two-second checks and the lifecycle worker for at most 30 one-second checks including three stable observations, then emits one explicit service/probe result before the final pass marker. A terminal unhealthy, exited, or dead state still fails immediately. Check the stack after deploy, update, restore, or certificate renewal:

```bash
bash infra/scripts/health.sh
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml ps
```

Inspect recent bounded container output without exposing environment values:

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml logs --since 30m --tail 200 nginx api frontend
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml logs --since 30m --tail 200 postgres meilisearch
```

For a public-path check after TLS is enabled:

```bash
curl --fail --silent --show-error https://connectmd.example.com/openapi.json >/dev/null
curl --fail --silent --show-error https://connectmd.example.com/llms.txt >/dev/null
curl --fail --silent --show-error https://connectmd.example.com/llms-full.txt >/dev/null
curl --fail --silent --show-error https://connectmd.example.com/.well-known/agent-card.json >/dev/null
curl --fail --silent --show-error https://connectmd.example.com/.well-known/oauth-protected-resource >/dev/null
curl --fail --silent --show-error https://connectmd.example.com/.well-known/oauth-protected-resource/mcp >/dev/null
curl --fail --silent --show-error https://connectmd.example.com/v1/taxonomies >/dev/null
curl --fail --silent --show-error 'https://connectmd.example.com/v1/taxonomies/skill?limit=1' >/dev/null
curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --data '{"q":"engineer","limit":1}' \
  https://connectmd.example.com/v1/search/query >/dev/null
curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --data '{"mode":"exact","q":"engineer","limit":1}' \
  https://connectmd.example.com/v1/search/query >/dev/null
curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --header 'MCP-Protocol-Version: 2025-06-18' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_documents","arguments":{"q":"engineer","limit":1}}}' \
  https://connectmd.example.com/mcp >/dev/null
curl --fail --silent --show-error \
  --header 'Content-Type: application/a2a+json' \
  --header 'A2A-Version: 1.0' \
  --data '{"message":{"messageId":"ops-search","role":"ROLE_USER","parts":[{"data":{"action":"search","q":"engineer","limit":1}}]}}' \
  https://connectmd.example.com/a2a/message:send >/dev/null
```

Nginx limits API traffic to 10 requests/second per source with a burst of 20, limits request bodies to 12 MiB, and gives API streaming/upload responses a 180-second upstream timeout. It forwards `Host`, real client address, forwarded scheme, and a request ID. Uvicorn trusts those headers only when the direct peer is the fixed Nginx address `172.31.254.2`; direct requests from other containers ignore forged forwarding headers, and Nginx's appended chain is evaluated from the right to the first untrusted hop. Never broaden this to `*` or a subnet. The API has no host port. The Next.js HTML proxy enforces a Content-Security-Policy that limits browser egress to the same-origin API and the conventional Clerk frontend, image, and challenge services. Monaco is copied from the exact locked npm artifact and served from the same-origin `/monaco/vs` path; production CSP contains no Monaco CDN executable origin. Next's statically rendered hydration scripts require `unsafe-inline` for script elements; inline event handlers and `unsafe-eval` remain blocked. API JSON, Markdown, and interactive OpenAPI documentation retain the shared security headers and HSTS without inheriting the frontend-only policy.

Long-lived services use Docker `json-file` rotation capped at five 10 MiB files each. Nginx emits JSON access records with time, method, URI path, protocol, status, response bytes, request duration, upstream duration/status, and the request ID. It deliberately omits client IPs, raw request lines, query strings, referrers, raw User-Agent/Authorization/Cookie headers, and request bodies; JSON escaping remains enabled. Source-address rate limiting remains in memory and does not require persisting the address in access logs. Uvicorn access logging is disabled so search terms and document parameters are not duplicated in API logs.

The public API, frontend, and Nginx services have explicit PID ceilings of 128, 128, and 64 respectively in both Compose layers. The frontend image runs as its existing UID 1001 with a read-only root, all default capabilities dropped, `no-new-privileges`, and only a bounded `/tmp` tmpfs. Nginx retains its root-starting entrypoint because it must bind 80/443, render the selected HTTP/TLS template, and transition workers to the configured `nginx` user; it drops all default capabilities and retains only `NET_BIND_SERVICE`, `SETGID`, and `SETUID`. Its read-only root has bounded tmpfs mounts only for the generated `conf.d` file, Nginx cache, and PID directory. Certificate and ACME volumes remain read-only for Nginx. These are repository Compose contracts; they are not a live kernel-isolation or runtime receipt.

## Dependency SBOM evidence

CI generates dependency-only CycloneDX inventories from the committed API
`requirements.lock` and web `package-lock.json`. The API job uses the already
locked `pip-audit` tool and the frontend job uses the Node/npm tool supplied by
the pinned Node runtime; the frontend validation invokes the runner's
stdlib-only `python3` checker without another action or install. Neither step
adds a runtime dependency or changes a package manifest. The repository checker requires the expected lockfile
format, exact name/version coverage, library component types, CycloneDX
schema metadata, and the web application root identity. API duplicate
identities are rejected. Repeated web identities require distinct npm package
paths that match the lockfile; unbound or repeated paths are rejected.
A complete image SBOM with additional non-library/image components or a
partial dependency list therefore cannot pass as one of these dependency
receipts; the checker does not claim to authenticate an image boundary.

Each job prints a canonical receipt to the CI log. Its SHA-256 inputs include
the lockfile bytes and a normalized inventory, while omitting SBOM serial
numbers, generation timestamps, and vulnerability/advisory arrays so advisory
feed changes do not change the lock-derived inventory identity. CI does not
upload or retain these files as artifacts. This is repository/CI evidence only:
it is not a complete image SBOM, a live registry digest, a vulnerability-free
claim, or proof of a deployed VPS runtime.

The API Dockerfile separately pins the Debian trixie repository to immutable
snapshot `20260805T010740Z` and pins the three direct converter packages to
exact versions. A base-image refresh must update the OCI digest, confirm the
base suite and snapshot timestamp, verify those direct versions for every
supported target architecture, and update the Dockerfile plus its supply-chain
tests together. This source contract is not a substitute for a Linux image
build, an installed-package inspection, a complete image SBOM, or the
converter built-image gate on the fresh dedicated VPS.

## Backup

The database dump is produced only after the live database-role contract passes. `backup.sh` runs `pg_dump --no-owner --no-privileges` as the one-shot, read-only `connectmd_backup` login; it never injects the cluster bootstrap or API credential into that container.

Canonical state consists of PostgreSQL—including the version/change ledgers, Agent Grants, idempotency receipts, contact policy, outreach state, account-lifecycle manifests, application snapshot byte lengths, and organization-verification evidence authority—and the Markdown storage volume. The volume can contain strict signed artifact-stage pairs retained across a crash; only the API may reconcile them under the same intent locks. Operators must not manually delete `.connectmd-artifact-staging`, infer ownership from names or modification times, or remove a canonical artifact to make readiness pass. Resolve artifact-reconciliation readiness before backup: after proving the API and lifecycle worker are stopped, `backup.sh` uses the current API image to run a bounded, content-free `VersionStore` scan and rejects any descriptor, incomplete payload, invalid or unknown entry, scan failure, or overbound namespace before the backup mutation boundary. Only an absent or exactly empty staging namespace can proceed, so unresolved cross-authority state is never captured. `backup.sh` requires an accepted format-v3 release marker and rejects every pending staged release; an internally healthy but unaccepted candidate cannot become backup authority. Before checking or capturing either authority, it records the worker's absent, running, restarting, or paused state and stops both the API and any active `account-erasure-worker`. Lifecycle enablement is independent should-run intent, so an enabled but absent worker must return healthy after the backup; only an actually paused prior worker is re-paused. The frontend cannot write directly to either data store. Meilisearch is intentionally excluded because it is a rebuildable projection.

```bash
bash infra/scripts/backup.sh
```

By default, backups are created under `./backups/connectmd-<UTC timestamp>/` with owner-only permissions inherited from the deploy account's umask. A backup contains:

- `postgres.dump`: PostgreSQL custom-format dump.
- `markdown-storage.tar.gz`: versioned canonical Markdown files.
- `metadata.env`: generation ID, creation and expiry times, exact clean source revision, image tag, locally inspected API/web/Nginx image IDs, local release-receipt digest, immutable acceptance-receipt digest, and database name.
- `SHA256SUMS`: integrity checks for metadata and both data artifacts.

The backup root itself is an authority boundary: on Linux it must be a non-symlink, effective-deploy-account-owned directory with mode exactly `0700`. A missing dedicated root is created under `umask 077`; an existing root with broad permissions or different ownership is refused and is never repaired automatically by an operational script.

Set `CONNECTMD_BACKUP_DIR` to a dedicated mounted backup directory when needed. The script refuses to start below `CONNECTMD_BACKUP_MIN_FREE_BYTES` (2 GiB by default); `CONNECTMD_BACKUP_RETENTION_DAYS` must be a positive integer and defines each generation's immutable registered expiry (30 days by default).

While the API and erasure worker are stopped, the script fails closed on any direct-child `.connectmd-*` staging directory, malformed `connectmd-*` generation, missing or mismatched registration receipt, pending retirement proof, missing/tampered deletion journal, or missing/tampered/non-current witness chain. For every valid existing generation, it checksum-verifies the content, matches its owner-only local receipt, and reruns the exact idempotent `account-backup register` CLI command to reconcile the PostgreSQL registry before capture. It records the authenticated, witness-verified deletion-journal head in both the checksummed generation metadata and immutable registration receipt, then captures and verifies the new PostgreSQL-plus-Markdown generation, registers it with `python -m app.cli account-backup register`, and atomically writes a mode-`0600` non-content receipt outside the prunable generation directory at `.connectmd-lifecycle/registrations/<generation-id>.env`. A failure after services stop but before registration reconciliation or artifact creation restores the API and should-run worker to fresh health, then re-pauses only an actually paused prior worker. Once that mutation boundary is crossed, a failure leaves both stopped and preserves the unknown or unregistered bytes for explicit recovery. Successful completion applies the same health and paused-state gate before reporting the backup path.

Retention is based on the registered `expires_at`, not directory modification time. The script retires only an immediate-child generation whose directory, checksums, metadata, and receipt all match. It first writes a durable non-content proof at `.connectmd-lifecycle/retirements/<generation-id>.proof`, deletes only that revalidated directory, then calls `python -m app.cli account-backup expire --generation-id ... --proof-digest ...`. A transition failure leaves the PostgreSQL manifest safely active, records a pending proof, aborts, and keeps both deletion-capable services stopped. Legacy, staged, malformed, unregistered, or mismatched backup data is never pruned automatically.

Retain backups outside the VPS through an already approved encrypted backup destination; a backup left only on the VPS does not survive VPS loss. Preserve every append-only file under `CONNECTMD_DELETION_WITNESS_DIR` separately in an independently administered off-host immutable/WORM destination before treating its corresponding deletion transition as operationally accepted. Verify retrieval and full-chain authentication regularly. Do not place that destination in the backup/journal authority or treat ordinary mutable backup copies as the witness. The local lifecycle receipts, proofs, and witness are recovery evidence, not replacement backup content or protection from a hostile local root. Daily capture is an operator-installed acceptance requirement, not a scheduler provisioned by this repository. Install it explicitly, for example:

```cron
11 2 * * * cd /srv/connectmd/app && bash infra/scripts/backup.sh >> /srv/connectmd/backup.log 2>&1
```

Verify an existing backup without changing long-lived services. Verification rejects symlinked backup artifacts, checks the exact checksum-manifest coverage, all three artifact hashes, metadata format/database match, and a parseable PostgreSQL custom dump in ephemeral no-network containers. Both verify-only and destructive modes first match the clean source, immutable release and acceptance receipts, and local tag-to-image identities. Only then may the exact authenticated API image read the single read-only archive mount for a no-network, read-only streaming inspection. It permits only canonical ASCII relative regular files and directories, and rejects duplicate normalized names (including the root marker), regular-file/descendant conflicts, traversal, absolute or control-character names, links, devices, FIFOs, sockets, sparse entries, an archive larger than 64 GiB, more than 200,000 members, or more than 64 GiB of expanded regular-file content. It never extracts an unvalidated archive or gives the validator access to the rest of the backup generation:

```bash
bash infra/scripts/restore.sh backups/connectmd-YYYYMMDDTHHMMSSZ --verify-only
```

## Restore and recovery drill

Restore is destructive to the current PostgreSQL database and Markdown named volume. First take a fresh backup. Confirm the selected backup's checksum and recorded release identity:

```bash
bash infra/scripts/restore.sh backups/connectmd-YYYYMMDDTHHMMSSZ --verify-only
grep -E '^(source_revision|image_tag|api_image_id|web_image_id|nginx_image_id|release_receipt_digest)=' backups/connectmd-YYYYMMDDTHHMMSSZ/metadata.env
```

`--verify-only` can inspect an otherwise valid generation without a receipt, but it does not authorize restoration. `--yes-restore` requires the already-existing mode-`0600` durable registration receipt at `.connectmd-lifecycle/registrations/<generation-id>.env`, with every generation, timestamp, checksum, and deletion-head field matching before any destructive action. Restore never creates this authority after mutation.

Set `recorded_source_revision` to the `source_revision` value in the selected backup metadata. Before destructive restore, switch the fresh dedicated Connect.md VPS checkout to that exact source revision and confirm it is clean. `--yes-restore` then requires the retained local historical release receipt and all three recorded local image IDs to match before it stops any consumer. It never fetches, rebuilds, pulls, or accepts a mutable tag as a substitute. Use the normal deployment gate to consume the completed restore state, apply migrations, rebuild and verify projections, and only then re-establish the recorded application-service topology:

```bash
git switch --detach "$recorded_source_revision"
bash infra/scripts/restore.sh backups/connectmd-YYYYMMDDTHHMMSSZ --yes-restore
bash infra/scripts/deploy.sh
# If deploy reports STAGED_IMAGE_TAG (for example a legacy v2 backup), first
# restore public TLS and then run: bash infra/scripts/release-accept.sh --yes-accept
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml ps
# When the recorded prior topology had every ordinary service running, also run:
bash infra/scripts/health.sh
```

The restore script checksum-verifies the artifacts, rejects unsafe archive paths, strictly validates dates and evidence paths, authenticates the complete independent witness chain, and validates the checksummed source/tag/API-web-Nginx identity tuple against the locally retained release-history receipt before any consumer stop. It also rejects a target API image that lacks migration `0025`, the exact-search CLI, or the exact-search contract. A v3 backup additionally binds and verifies its immutable acceptance receipt; deploy may relaunch an existing accepted marker only when its source, image identities, release-receipt digest, and acceptance-receipt digest exactly match that v3 backup. A legacy v2 backup records `acceptance_receipt_digest=none` in restore state, always yields a staged candidate, and never silently reuses a retained marker; it requires the normal explicit public acceptance command. It requires the target API image declaring the witness-aware deletion-authority contract, and requires its current journal head plus the backup's checkpoint to equal the current external journal before any mutation. A generation created before any later deletion commitment is therefore ineligible for restore, even if its database and Markdown checksums are valid; rolling that database and journal back together is also rejected while the later witness is preserved. Before replacement, the script requires every one-shot canonical-state admin to be inactive, records the running, paused, stopped, or absent state and exact active image of Nginx, frontend, API, converter, projection worker, and lifecycle worker, stops Nginx and frontend first, then stops every application worker, and proves all six consumers inactive. It replaces only the Markdown named volume and configured database, re-registers the restored generation, and requires bidirectional parity between every journal commitment and an exactly timestamped/policy-bound concealed lifecycle plus access-deny row before reporting completion. A failure before mutation re-establishes only the previously active exact images and paused states in dependency-safe order; after mutation begins, any failure leaves every consumer stopped for deliberate recovery. A mode-`0600` format-v3 `.connectmd-restore-state.env` begins as `in_progress` with `search_rebuild_pending=true`, preserves the exact prior service topology, and is atomically replaced with source-and-identity-bound `complete` evidence only after registration, receipt, witnessed-journal-head, and live-mirror verification. `deploy.sh` then applies migrations, backfills and verifies taxonomy and exact search, rebuilds Meilisearch, and only after that safety barrier recreates services recorded as running or paused while proving stopped or absent services remain inactive. Legacy format-v2 restore state remains readable with its historical core-services-running behavior. A staged restore is cleared only by `release-accept.sh --yes-accept` after durable acceptance and marker promotion. The current recovery policy deliberately sacrifices availability: an orphaned journal commitment, missing or mismatched witness, missing historical release receipt, missing local recorded image, or any older pre-commitment generation remains non-launchable until a separately reviewed reconciliation drill is implemented; operators must not delete, truncate, reinitialize, or bypass either deletion authority to recover service.

At the destructive database boundary only the operator `postgres` identity terminates sessions and recreates the database. `connectmd_migrator` restores with `--no-owner --no-privileges`, and the exact role/ACL contract is reapplied and verified before any application CLI runs.

## Meilisearch rebuild

If Meilisearch data is lost, corrupted, or intentionally cleared, canonical PostgreSQL and Markdown data remain authoritative. Start the service and rebuild its projection through the API image:

```bash
bash infra/scripts/rebuild-search.sh
bash infra/scripts/health.sh
```

The command is `python -m app.cli rebuild-search`. The wrapper requires healthy PostgreSQL and Meilisearch services and stops the API, the sole long-lived projection writer, and any active account-erasure worker for a stable snapshot. Before resetting the index it authenticates the deletion authority and requires the PostgreSQL taxonomy projection to pass its read-only verifier; it never silently repairs taxonomy state during an ordinary Meilisearch recovery. The one-off `search-admin` profile alone receives the master key, resets/settings-configures the index, and retires the satisfied projection outbox only after every public canonical document is indexed successfully. A deletion-authority, taxonomy-verification, or rebuild failure leaves all writers stopped. A successful rebuild restarts and health-checks the API and projection worker; lifecycle enabled is should-run intent even when the prior container was absent, while only an actually paused prior worker is re-paused after its fresh functional heartbeat passes. Do not expose port 7700 or pass `MEILI_MASTER_KEY` to any long-lived application service or frontend during diagnosis.

## Taxonomy projection recovery

The public taxonomy registry is a rebuildable PostgreSQL projection of only current public schema-v2 profile and resume Markdown. It is not an external vocabulary authority and does not retain owner IDs, source document IDs, counts, private records, or labels after their final public membership disappears. Normal canonical writes maintain it transactionally; an explicit rebuild is for migration or diagnosed projection corruption only.

```bash
bash infra/scripts/rebuild-taxonomy.sh
bash infra/scripts/health.sh
```

The wrapper obtains the shared operations lock, stops the API, search projection worker, and any active lifecycle worker, authenticates the live deletion authority, rebuilds taxonomy from verified current Markdown through the isolated `taxonomy-admin` profile, verifies the ready relational contract, and then rebuilds Meilisearch from the same stable writer epoch. Any failure leaves every writer stopped. `taxonomy-admin` has the scoped `connectmd_projection_admin` connection and read-only Markdown storage on the data network only; it has no Meilisearch key, Clerk configuration, lifecycle secret, deletion-journal/witness mount, application network, or host port. Do not run the CLI directly while an application writer is active.

## Exact search recovery

Migration `0025` maintains a canonical PostgreSQL exact-search projection separate from Meilisearch. Normal writes update it transactionally. Deploy, rollback, restore-through-deploy, and environment reconfiguration run `exact-search backfill --if-required` and `exact-search verify` while application writers are stopped; a non-ready or corrupt result leaves public services stopped. The one-shot `exact-search-admin` has the scoped `connectmd_projection_admin` connection, read-only Markdown, and cursor authority on the internal data network only. Do not invoke its mutation command while application writers are active. Cursor-key rotation follows the prepend, wait-at-least-TTL, then retire sequence in [deployment.md](deployment.md); never log the JSON keyring.

`exact-search-admin` likewise uses `connectmd_projection_admin`, limited to canonical reads, projection-table maintenance, outbox deletion, and the single `documents.schema_version` repair column.

## Search projection recovery

Each document create or update commits a content-free `(document_id, version)` task with the canonical database change. The least-privilege worker leases tasks, recovers expired leases, retries transient failures with bounded backoff, and projects only public bytes from the canonical current version. Stale tasks are superseded; current non-public or missing documents are removed idempotently. Account erasure re-arms a missing-document tombstone in the canonical delete transaction and cannot report live or full erasure complete until the worker consumes every such tombstone after confirmed Meilisearch absence; a projection dead letter fails that lifecycle closed. Worker logs and dead-letter records contain identifiers, attempt counts, and bounded error codes only—not Markdown, search payloads, credentials, URLs, or exception text.

Inspect the bounded dead-letter queue without changing it:

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml run --rm --no-deps search-projection-worker python -m app.search_projection_worker list-dead --limit 50
```

After correcting the underlying storage, database, or Meilisearch condition, requeue one exact version:

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml run --rm --no-deps search-projection-worker python -m app.search_projection_worker retry-dead --document-id DOCUMENT_UUID --version VERSION
```

Requeueing is idempotent for the selected dead letter. Confirm worker health and metadata-only logs afterward. Repository tests exercise lease recovery, retries, stale supersession, non-public removal, duplicate enqueue, and dead-letter recovery, but do not claim a live Docker or production convergence drill.

Inspect live bounded worker status—the total backlog, currently eligible work, total dead letters, and oldest backlog age—without exposing content:

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml run --rm --no-deps search-projection-worker python -m app.search_projection_worker status
```

The default health contract is degraded when any current dead letter exists or the oldest backlog exceeds 600 seconds. The daemon refreshes its mode-`0600` heartbeat only after both a database status query and an authenticated exact-index Meilisearch probe succeed; invalid database or Meilisearch credentials remove the heartbeat and fail container health. `list-dead --limit` reports both the returned page size and the full dead-letter total. A new canonical version removes only older dead-letter rows for that document; pending and leased work is never hidden.

## Incident boundaries

- Use `docker compose ... logs`, `ps`, and the health script first; do not inspect or paste `.env` into tickets or terminals shared with others.
- A failed migration means stop before application rollout, inspect the migration error, and restore only with a tested backup if necessary.
- A certificate renewal failure leaves the existing certificate untouched. Check public DNS and firewall rules for 80/443 before retrying `tls.sh issue` or `tls.sh renew`.
- For an unauthorized agent action, revoke the affected Agent Grant first, preserve its grant ID, change-event ID, request ID, and document version, and inspect the bounded audit record without copying secrets or private contact messages into an incident ticket.
- For outreach abuse, close the recipient contact policy or block the sender, preserve request/event identifiers, and leave the underlying audit rows intact.
- `docker compose down -v` destroys named volumes and is not an operational recovery command. Do not run it on the production project.
