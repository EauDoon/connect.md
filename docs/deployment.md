# Deploying connect.md on Hostinger KVM

## Fresh-target boundary

This runbook is exclusively for a newly provisioned, empty Hostinger KVM dedicated to connect.md. Establish a fresh VPS IP, DNS hostname, SSH account/key, `.env`, Docker project, named volumes, networks, and backup directory. Never use Hermes or inspect, connect to, reuse, or change any existing Hostinger instance, deployment, credential, volume, network, DNS record, or backup. This repository contains local deployment artifacts only; it does not authorize or perform a remote action.

This guide deploys one connect.md stack on an Ubuntu Docker VPS. It uses the repository's pinned Compose services and an ACME/Let's Encrypt certificate container; PostgreSQL, Meilisearch, the API, and the frontend never publish host ports.

## 1. Provision the VPS and network boundary

Use Hostinger's Ubuntu 24.04 Docker VPS template. It includes Docker Engine and the Docker Compose plugin; confirm them before continuing:

```bash
docker --version
docker compose version
```

In Hostinger's managed VPS firewall, allow only:

- TCP 22 from the operator's fixed source address or VPN range.
- TCP 80 and 443 from the internet.

Do not create inbound rules for 3000, 5432, 7700, or 8000. Compose also keeps PostgreSQL and Meilisearch on the internal `connectmd_data` network, with no host port mappings. See Hostinger's [Docker VPS template guide](https://www.hostinger.com/support/8306612-how-to-use-the-docker-vps-template-at-hostinger/) and [managed firewall guide](https://www.hostinger.com/support/8172641-how-to-use-a-managed-vps-firewall-at-hostinger/).

The repository reserves `172.31.254.0/24` for the Compose application-edge bridge and pins only Nginx to `172.31.254.2`. Before first deployment on the fresh VPS, compare that subnet with the host's routes and existing Docker networks; if it overlaps, stop and make one reviewed, coordinated change to the Compose subnet, Nginx address, Uvicorn singleton trust value, tests, and documentation. Never replace the trusted address with `*`, a subnet, or a broad container range. The API publishes no host port.

Create an `A` (and, if used, `AAAA`) DNS record for the single hostname in `CONNECTMD_DOMAIN` pointing to the VPS. Use its canonical lowercase ASCII or punycode form; every DNS label must be 1-63 characters, start and end with a letter or digit, and the complete hostname must not exceed 253 characters. Wait for public resolution before requesting a certificate:

```bash
dig +short connectmd.example.com
```

## 2. Prepare the host and environment

Use a dedicated, non-root deploy account with Docker access. The API image runs as UID/GID `10001`, so this fresh host must reserve the same numeric identity for the deploy account that owns the bind-mounted deletion authorities. Keep the repository in a directory owned by that account.

```bash
sudo addgroup --gid 10001 connectmd
sudo adduser --disabled-password --gecos '' --uid 10001 --gid 10001 connectmd
sudo usermod -aG docker connectmd
sudo install -d -m 0750 -o connectmd -g connectmd /srv/connectmd
sudo install -d -m 0700 -o connectmd -g connectmd /var/lib/connectmd/deletion-head-witness
test "$(id -u connectmd)" = 10001
test "$(id -g connectmd)" = 10001
```

Place the approved connect.md repository in `/srv/connectmd/app` through the project's approved source-control workflow, then continue as the `connectmd` account:

```bash
cd /srv/connectmd/app
cp .env.example .env
chmod 600 .env
```

Edit `.env` locally on the VPS. Generate new, URL-safe database, Meilisearch, and API-key-pepper secrets; do not reuse the example values or commit this file.

Every operational script rejects `.env` unless it is a non-symlink regular file owned by the effective `connectmd` deploy account and, on Linux, has mode exactly `0600`. Do not work around this guard with a shared group, a symlink, or a privileged account.

```bash
openssl rand -hex 32
```

Set at least `POSTGRES_PASSWORD`, all six `CONNECTMD_*_DB_PASSWORD` values shown in `.env.example`, `MEILI_MASTER_KEY`, `CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING`, `CONNECTMD_CLERK_JWKS_URL`, `CONNECTMD_CLERK_ISSUER`, `CONNECTMD_CLERK_AUTHORIZED_PARTIES`, `CONNECTMD_API_KEY_PEPPER`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CONNECTMD_DOMAIN`, `CONNECTMD_PUBLIC_BASE_URL`, `NEXT_PUBLIC_SITE_URL`, and `ACME_EMAIL`. Generate every PostgreSQL password independently with `openssl rand -hex 32`; production rejects non-hexadecimal, short, reused values and any `POSTGRES_USER` other than the operator-only `postgres` bootstrap identity. Both public URL values must be the same canonical HTTPS origin, for example `https://connectmd.example.com`; they prevent reverse-proxy internals from leaking into discovery documents, OAuth metadata, A2A cards, canonical links, or JSON-LD. Leave `CONNECTMD_CLERK_AUDIENCE` blank for normal Clerk session tokens; set it only when the frontend deliberately requests a Clerk JWT template with the same `aud` claim. Database passwords, `MEILI_MASTER_KEY`, runtime Meilisearch keys, the exact-search cursor keyring, `CONNECTMD_API_KEY_PEPPER`, and `CLERK_SECRET_KEY` are server-only and must never be set in `NEXT_PUBLIC_*` variables. `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` must use the `pk_test_` or `pk_live_` family followed by standard Base64 encoding of an ASCII dotted Clerk instance hostname ending in exactly one `$`; malformed prefixes, encodings, and hosts are rejected before release acceptance without echoing the key. `CLERK_SECRET_KEY` must be a real `sk_test_...` or `sk_live_...` Clerk secret with at least 16 URL-safe suffix characters; placeholders and malformed values are rejected before release acceptance. It is passed only to the running frontend container so Clerk middleware can verify a session before returning a private route shell; it is deliberately absent from frontend build arguments and client code. Meilisearch runs in `production` mode and each key must be at least 16 bytes.

Generate the exact-search keyring with the command documented in `.env.example`; do not paste its output into logs or source control. Production preflight accepts strict JSON containing one to three exact `kid`/`secret` objects, unique 1-32-character key IDs, and URL-safe base64 secrets of at least 32 decoded bytes. The first key signs new cursors. To rotate, prepend the new key, retain prior keys for at least `CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS` (default 900, allowed 60-3600), reconfigure, wait out that TTL, then remove retired keys in a second reconfiguration. Never reuse another application secret.

Before the first validated deploy, create the two required runtime Meilisearch keys through the isolated bootstrap profile. This service receives `MEILI_MASTER_KEY` but mounts no Markdown, deletion-journal, witness, or database authority; the API and long-lived projection worker never receive the master key.

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml build api
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml up -d meilisearch
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml --profile search-bootstrap run --rm --no-deps search-key-bootstrap python -m app.search_key_bootstrap search
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml --profile search-bootstrap run --rm --no-deps search-key-bootstrap python -m app.search_key_bootstrap projection
```

Each command prints its key exactly once. Put the first value in `CONNECTMD_MEILISEARCH_SEARCH_KEY` and the second in `CONNECTMD_SEARCH_PROJECTION_MEILI_KEY`, then keep `.env` mode `0600`. The fixed contracts are index-scoped to `CONNECTMD_MEILISEARCH_INDEX`: API search gets only `search` and `indexes.get`; projection gets `documents.add`, `documents.get`, `documents.delete`, `tasks.get`, and `indexes.get`. Re-running for an existing fixed key UID fails closed because Meilisearch does not provide a safe second retrieval path. Leave `CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY` blank while lifecycle is disabled. If lifecycle is later approved, provision `erasure` the same way; it receives only `documents.get`, `documents.delete`, `tasks.get`, and `indexes.get`.

Database roles are operator state, not Alembic state: migration `0019` creates only the outbox schema. `infra/postgres/database-role-contract.sql` creates and reconciles six hard-coded non-superuser, no-membership login roles. The long-lived API uses `connectmd_api`; the projection worker uses `connectmd_search_projection`; projection rebuilds use `connectmd_projection_admin`; lifecycle erasure uses `connectmd_account_erasure`; backups use `connectmd_backup`; and migrations/restores use one-shot `connectmd_migrator`. The cluster `postgres` login remains the database owner and appears only in the database container and operator-side bootstrap/restore path. The migrator owns the `public` schema and application tables/sequences, but has database `CONNECT` only—never database `CREATE` or `TEMPORARY`. Reconciliation repairs database ownership to the locked `postgres` operator if an earlier partial run changed it. Secrets cross stdin/environment without being printed. The contract removes prior ACLs before applying exact grants, then compares actual table, column, sequence, database, schema, membership, ownership, role-attribute, function, and `PUBLIC` authority in both directions.

Replace the three non-secret authority ID placeholders with pre-provisioned internal identities: `CONNECTMD_VERIFICATION_REVIEWER_ID`, `CONNECTMD_POST_MODERATOR_ID`, and `CONNECTMD_APPEAL_REVIEWER_ID`. Keep their fixed roles as `recruiting_verifier`, `content_moderator`, and `appeal_reviewer`, respectively, and keep the appeal reviewer ID different from the post moderator ID. These values are passed to both production API processes so startup and lifecycle work share the same trust-authority contract. Leave `CONNECTMD_RECRUITING_ENABLED`, `CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED`, and `NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED` set to `false`, but replace `CONNECTMD_LIFECYCLE_HMAC_KEY`, `CONNECTMD_LIFECYCLE_AEAD_KEY`, and `CONNECTMD_DELETION_WITNESS_HMAC_KEY` with three independent fresh values before the first deploy. Set `CONNECTMD_DELETION_WITNESS_DIR` to a canonical absolute host path outside `CONNECTMD_BACKUP_DIR`. The first two keys authenticate and conceal the always-present external deletion journal; the third authenticates its independent monotonic head witness. Generic deploy, reconfigure, and rollback cannot rotate these keys or move the witness path; after a release records lifecycle as enabled, the flag cannot return to `false`.

The recruiting flag is a release barrier, not an availability preference. With `CONNECTMD_RECRUITING_ENABLED=false`, the API returns empty public organization/job inventories; hides recruiting routes/tags, OAuth scopes, Agent Grant organization scope matrices, capabilities, and LLM discovery; returns opaque `404` for public detail and new application attempts; and rejects public visibility, publication, positive application acceptance, activation, or restoration before durable mutation. Private drafting, authorized management, applicant access/withdrawal, employer review/reject, and defensive verification transitions remain available under their existing authority checks. Do not set the flag to `true` until the reporting, recipient-quota, duplicate-control, anti-scam-review, and no-enumeration gates in [trust-safety.md](trust-safety.md) have independent release evidence. A configuration change alone is not release evidence.

The administrator-created witness directory above matches the default `.env.example` path. If a different canonical path is required, substitute it in both that administrator command and `CONNECTMD_DELETION_WITNESS_DIR` before switching to the `connectmd` account. Initialize the empty external deletion authority exactly once before the first deploy while logged in as UID/GID `10001`. The initializer requires a committed clean checkout, rejects any other current UID/GID, and rejects either authority root unless its numeric owner matches the API container. It refuses a pre-existing exact source-tagged API image because it cannot prove that image belongs to the current initialization attempt. The source-tagged image it builds is identity-checked and removed before success so it cannot become an unreceipted partial release image set; an ownership or cleanup failure retains the exact tag for explicit recovery and never reports success. Compose uses `create_host_path: false`, so deletion-aware services refuse absent host authorities instead of creating root-owned directories. Absence is never interpreted as an empty journal:

```bash
bash infra/scripts/init-deletion-journal.sh
```

Initialization creates authenticated sequence zero in the witness and an empty journal head. The journal lives below `CONNECTMD_BACKUP_DIR/.connectmd-lifecycle/deletion-journal`, outside PostgreSQL and the restored Markdown volume. Its witness lives at the separately configured `CONNECTMD_DELETION_WITNESS_DIR`; Compose mounts it read-write only in the API and read-only in `search-admin` and `account-erasure-worker`. The API and every deletion-aware operational gate require exact full witness continuity and equality with the current journal head.

`CONNECTMD_BACKUP_DIR` is a dedicated operator authority. It may be absent on first initialization and will then be created under `umask 077`; once present it must be a non-symlink directory owned by the effective deploy account and, on Linux, mode exactly `0700`. The scripts refuse a broad, substituted, or foreign-owned backup root rather than changing its permissions automatically.

Before lifecycle activation, establish and drill an independently administered copy path that durably preserves every witness entry off-host on immutable/WORM storage and can retrieve the exact current chain after local loss. A local root operator can destroy or replace the database, journal, witness, and release fingerprints together, so separate local directories alone do not provide hostile-root rollback resistance. The repository does not provision or attest the required off-host system; retain both flags as `false` until that deployment evidence exists.

With the disabled lifecycle profile omitted, the production overlay caps the normal runtime at roughly 2.3 GiB, including a 768 MiB no-network conversion worker and a 192 MiB least-privilege search projection worker. The optional lifecycle worker adds a 384 MiB limit only when its profile is explicitly enabled. Meilisearch indexing is limited to one thread and 192 MiB inside a 384 MiB container. This fits KVM 2; KVM 4 provides safer headroom for concurrent conversion, indexing, builds, and backups. Raise limits only after observing real load.

Validate the fully interpolated configuration without starting containers:

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml config -q
```

## 3. Deploy, migrate, and enable TLS

Build the three application images, start private dependencies, bootstrap the scoped database roles, run exactly one migration process as `connectmd_migrator`, reconcile and verify exact ACLs, backfill and verify the current-public taxonomy projection, backfill and verify migration-`0025` canonical exact search through its data-network-only `exact-search-admin`, reset/reindex Meilisearch through the separate one-off master-key admin, and only then start the no-network converter, least-privilege search projection worker, and public services. A fresh database marked `backfill_required` cannot start the API, frontend, projection worker, or Nginx:

```bash
bash infra/scripts/deploy.sh
```

`deploy.sh` ends in **staged**, not accepted, authority. It may start a locally healthy candidate and write an immutable source/image release receipt, but it never changes `.connectmd-release.env`. After public DNS and a publicly trusted certificate are ready, explicitly verify and promote the same staged candidate in the TLS sequence below.

The acceptance command first revalidates healthy PostgreSQL, Meilisearch, converter, search-projection worker, API, frontend, and Nginx containers, plus the account-lifecycle worker when that feature is enabled. This bounded in-process gate runs after the exact staged image identities are confirmed and before any acceptance receipt or active-marker mutation. It then uses the normal public DNS path and public CA trust only; it rejects name overrides, self-signed/custom trust, wrong TLS identity, a non-canonical HTTP redirect, missing HSTS, a wrong `X-Connectmd-Release-Tag`, or an invalid OpenAPI, agent discovery, OAuth, MCP, A2A, projection-search, or canonical `mode=exact` response. New immutable mode-`0600` evidence and stage-scoped receipts use acceptance format v2 and bind the exact-search response digest before atomically writing accepted marker format v3. Historical format-v1 acceptance receipts remain valid for the exact historical release they already bind; they are never upgraded or rewritten. A failed runtime-health or public probe leaves the previous accepted marker unchanged and the candidate staged for deliberate retry or recovery.

The initial Nginx start is HTTP-only until a certificate is available. With port 80 open and DNS resolved, obtain the certificate and reload Nginx into its TLS configuration:

```bash
bash infra/scripts/tls.sh issue
bash infra/scripts/health.sh
bash infra/scripts/release-accept.sh --yes-accept
curl --fail --silent --show-error https://connectmd.example.com/openapi.json >/dev/null
```

`tls.sh issue` uses HTTP-01 and the shared Compose webroot. It does not expose a certificate-management port or require host-level Nginx. Install a renewal schedule as the deploy account; it is safe to run daily because Certbot renews only certificates close to expiry:

```cron
17 3 * * * cd /srv/connectmd/app && bash infra/scripts/tls.sh renew >> /srv/connectmd/tls-renew.log 2>&1
```

On this fresh, dedicated connect.md VPS only, the accepted live API container must also run retention hourly. After the first release is accepted, run `bash infra/scripts/retention.sh` once and require a successful exit before treating the deployment as launch-ready, then install this exact schedule as the `connectmd` deploy account:

```cron
23 * * * * cd /srv/connectmd/app && bash infra/scripts/retention.sh >> /srv/connectmd/retention.log 2>&1
```

The wrapper takes the shared fail-fast operational lock, validates the active accepted release marker and the running API container's exact recorded image identity, pins Compose to that accepted image tag, and uses `compose exec`; it never starts a one-off or staged image. Every run prunes direct-peer abuse-control HMAC buckets older than the current UTC day before ordinary content-retention discovery. Monitor the cron exit status and log: a missing or failed hourly run is an operational retention failure and blocks launch or continued launch acceptance until a successful run is observed.

## 4. Update and rollback

Before a deploy or update, ensure the fresh dedicated Connect.md VPS checkout is committed and completely clean, including no untracked files. A release tag combines the full Git revision with a fingerprint of the frontend's public build configuration, but a tag is never sufficient release authority. Local health produces only a mode-`0600` staged record bound to the immutable source/image release-history receipt and the prior marker digest. The active marker is format v3 and is written only by `release-accept.sh --yes-accept` after content-bound public HTTPS evidence. Both records carry the exact full clean source revision and the locally inspected Docker image IDs for API, web, and Nginx. They do not claim an external registry digest. An interrupted deploy reuses an existing image set only when its historical receipt already validates that exact source/tag/three-ID tuple; an orphaned, partial, retargeted, or missing local image set fails closed.

`update.sh` requires the approved exact full target source revision. It refuses a pending stage, fetches its configured `origin`, proves the target is an ancestry-valid fast-forward reachable from the configured origin upstream, takes a consistent backup of the prior **accepted** release, and fast-forwards specifically to that revision before the migration-first deployment sequence. It ends with a staged candidate; run explicit public acceptance before treating it as updated authority. Mutating scripts share one fail-fast host lock, so deploy, update, backup, restore, rollback, TLS, acceptance, and search rebuild cannot overlap.

```bash
bash infra/scripts/update.sh FULL_TARGET_REVISION
```

Keep the printed `PREVIOUS_IMAGE_TAG` and `PREVIOUS_SOURCE_REVISION` until the release is accepted. A rollback resolves the retained historical receipt for that tag; it requires the checkout to be clean and exactly at the receipt's source revision and requires the recorded API/web/Nginx image IDs to exist locally and match their tags before any writer stops. Set `previous_image_tag` and `previous_source_revision` to those exact values, then run:

```bash
git switch --detach "$previous_source_revision"
bash infra/scripts/rollback.sh "$previous_image_tag"
bash infra/scripts/health.sh
```

Do not use image rollback to reverse a schema migration. Before stopping the active release, `rollback.sh` requires the target's immutable acceptance receipt, migration `0025`, exact-search CLI/contract, durable projection contract, an exact-search integrity pass, and an Alembic head matching the live database; when lifecycle must run, it also requires the target lifecycle-health contract. A target unable to preserve the live exact-search authority is rejected before mutation. While a newer candidate is staged, rollback accepts only the stage's recorded prior accepted target. After the rollback barrier, exact search is backfilled if required and verified before Meilisearch is rebuilt or any public service starts; any failure leaves every application writer stopped for explicit recovery. Rollback restores disabled/absent, running, or paused lifecycle intent as authorized, but it reports success for an active worker only after fresh database, queue, provider, and search heartbeat evidence passes. Releases must preserve backward compatibility until an explicitly planned database recovery is available. The restore procedure in [operations.md](operations.md) restores both database and Markdown storage from one backup while all writers are stopped.

For an environment-only change that does not alter any PostgreSQL password, `MEILI_MASTER_KEY`, or the frontend's public build variables, validate, reconcile the role contract, and recreate the application containers without rebuilding images:

```bash
bash infra/scripts/reconfigure.sh
```

The script refuses stateful credential rotation, lifecycle HMAC/AEAD rotation, lifecycle deactivation after activation, public frontend configuration drift, and `CONNECTMD_RECRUITING_ENABLED=true`. Recruiting enablement requires a newly staged and accepted release after the independent trust-and-safety evidence above; it cannot be activated through reconfiguration. It records absent, running, restarting, or paused lifecycle-worker intent, stops every application writer, then backfills exact search only if its durable contract requires it and verifies integrity before recreating services. An enabled but absent worker is created and must pass its fresh functional heartbeat; an actually paused worker passes that health gate before being re-paused; disabled plus absent remains absent. Any failure after the barrier leaves the API, Nginx, frontend, search projection worker, converter, and account-erasure worker stopped for explicit recovery. PostgreSQL/Meilisearch or lifecycle-journal credential rotation needs a separately planned, backup-backed procedure; public Clerk/API URL build changes require a new committed release and `deploy.sh`.

## Account lifecycle release gate

The `account-erasure-worker` Compose service is in the disabled-by-default `account-lifecycle` profile, runs `python -m app.account_erasure_worker`, mounts the canonical Markdown volume, uses the application network for Clerk provider egress, and reaches PostgreSQL and Meilisearch on the internal data network. PostgreSQL and Meilisearch remain without host ports. When enabled, deploy requires this worker to be active and Docker-healthy from its fresh content-free heartbeat; when disabled, the profile remains absent. The public lifecycle flag is supplied to the frontend as both a build argument and runtime environment value, and it is included in the release image fingerprint; the API and public flags must match.

Do not set either lifecycle flag to `true` and do not start the worker until all of the following pass on this fresh, dedicated connect.md VPS:

- the first real PostgreSQL-plus-Markdown generation completes `backup.sh`, including CLI registration and its mode-`0600` local receipt;
- the complete current deletion-witness chain is retrieved and authenticated from the approved independently administered off-host immutable/WORM destination after a simulated local database-plus-journal rollback;
- the real Clerk backend deletion provider and fresh lifecycle HMAC/AEAD secrets are configured and provider deletion/reverification is exercised without logging provider secrets or response bodies;
- real PostgreSQL, internal Meilisearch, public TLS, backup, isolated restore, and worker failure/retry drills pass together;
- the frontend image is verified to consume the build-time `NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED` value, and the enabled API/UI negative tests pass.

Only after those gates pass may an operator build a new release with both flags set to `true` and explicitly start the profile:

```bash
docker compose --env-file .env -f compose.yaml -f compose.prod.yaml --profile account-lifecycle up -d --no-build account-erasure-worker
```

That command starts the worker; run `infra/scripts/health.sh` to require its authenticated functional heartbeat. Backup records enabled and exact running/restarting/paused worker intent before stopping deletion-capable services. A read-only pre-mutation failure restores required services to health and re-pauses only prior paused intent; successful capture applies the same gate, while a failure after registration reconciliation or artifact mutation begins leaves both services stopped for explicit recovery.

## Deployment invariants

- `deploy.sh` runs `alembic upgrade head` once through the one-shot `db-migrate` service before ACL reconciliation and API rollout; do not run a second migration process during that step.
- Every deploy performs an offline taxonomy backfill when its PostgreSQL projection contract is absent or outdated, then verifies the ready state before rebuilding search or starting public services. Missing, corrupt, private, stale, superseded, or invalid canonical Markdown leaves the projection non-ready and the writers stopped.
- Every deploy performs a fail-closed public-only search rebuild after taxonomy verification and before the API, frontend, projection worker, or Nginx starts. An empty restored or fresh Meilisearch index cannot pass rollout merely because the process is alive.
- Deploy stops Nginx, frontend, the search projection worker, and the old API before migration. If migration fails, the application stack remains stopped; inspect the error and use the verified pre-update backup rather than serving through a partially applied schema.
- Only Nginx binds ports 80 and 443. The API needs outbound access to Clerk JWKS, so it has an application network in addition to the isolated data network.
- PDF/DOCX parsing runs in the separately capped `converter` service with no network, database credentials, Meilisearch key, Clerk configuration, or canonical Markdown volume. The API exchanges only bounded ephemeral jobs with it.
- The search projection worker has no host port or application egress network. It receives only its restricted PostgreSQL login, read-only Markdown storage, its index-scoped projection key, and bounded worker configuration. The API uses a separate search-only key; the lifecycle worker receives a separate delete-only key only when enabled; all paths use the same `CONNECTMD_MEILISEARCH_INDEX` value.
- The `taxonomy-admin` profile is one-shot only. It receives the application database connection, a read-only Markdown mount, and the data network; it receives no exact-search cursor keyring, Meilisearch key, Clerk setting, lifecycle secret, journal/witness mount, application-egress network, or host port. No long-running worker receives taxonomy-administration authority.
- The `exact-search-admin` profile is one-shot only. It receives the application database connection, read-only Markdown, the cursor keyring, and the internal data network; it receives no Meilisearch, Clerk, lifecycle, deletion-journal/witness, application-edge, or host-port authority.
- Uvicorn accepts forwarding headers only from the fixed Nginx address `172.31.254.2`. Nginx appends the direct peer to `X-Forwarded-For`; Uvicorn's right-to-left trusted-hop walk selects the first untrusted address, so a client-supplied leading value is not accepted as the client. Other containers on the bridge remain untrusted.
- The account-erasure worker has no host port and is absent unless the `account-lifecycle` profile is explicitly selected. Its presence never widens the fresh dedicated connect.md target boundary.
- Before TLS is issued, the port-80 bootstrap serves only ACME challenges and `/nginx-health`; application and authenticated API traffic return 503 rather than crossing plaintext HTTP.
- Nginx automatically uses TLS whenever the named certificate volume contains a valid certificate for `CONNECTMD_DOMAIN`; otherwise it exposes only the HTTP ACME bootstrap server.
- The certificate setup supports one hostname. Change `CONNECTMD_DOMAIN` only through a planned DNS and certificate replacement.
