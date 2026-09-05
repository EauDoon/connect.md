# Standalone Vercel deployment

The production site is the Next.js application in apps/web. FastAPI,
PostgreSQL, Meilisearch, workers, Clerk, and Hostinger are not part of this
deployment.

## Project settings

| Setting | Value |
| --- | --- |
| Root Directory | apps/web |
| Framework | Next.js |
| Install Command | npm ci |
| Build Command | npm run build |
| Node.js | 22.x |
| Production branch | main |

The only application environment variable is:

    NEXT_PUBLIC_SITE_URL=https://connect-md.vercel.app

Remove old API, Clerk, recruiting, lifecycle, database, storage, and worker
variables from the Vercel project. The production CSP permits only same-origin
network connections and the blob URLs needed for editor workers and downloads.

## Release sequence

From apps/web:

    npm ci
    npm run lint
    npm run typecheck
    npm test
    npm run build
    vercel deploy --prod --skip-domain
    vercel inspect <candidate-url>
    vercel promote <candidate-url>

Using --skip-domain keeps the current production alias untouched until the
candidate has passed inspection.

## Acceptance

Verify the promoted production origin:

- /, /human, /md, and /trust return 200.
- /agent-readme.md and /llms.txt return their expected text content.
- /robots.txt and /sitemap.xml name only the standalone public routes.
- retired routes such as /discover, /workspace, and /jobs return a
  non-indexable, no-store 404.
- CSP, HSTS, X-Content-Type-Options, X-Frame-Options, Referrer Policy, and
  Permissions Policy are present.
- the browser console shows no failed API requests.
- a valid draft downloads locally and invalid Markdown remains blocked.

The live site never writes document content to a server. Its only durable
artifact is the file the user explicitly downloads.

## Network MVP database

The network MVP (ADR 0002) requires one PostgreSQL database exposed to the
app as `CONNECTMD_NETWORK_DATABASE_URL`. Two deployment states are valid:

- **No database configured** (current production until the owner provisions
  one): network routes answer 503 with `x-connectmd-network: unavailable`
  and an explicit JSON reason; guest routes are unaffected.
- **Database configured**: the URL is stored in the operator vault
  (gringotts) as `apps/connectmd/network-database-url` and resolved at
  deploy time. It is never committed, never printed, and never set in
  browser-reachable configuration.

Deploy with vault-resolved secrets:

    deploy/with-network-secrets.sh -- vercel deploy --prod --skip-domain

Apply migrations against the production database (run from a machine with
network access to it):

    gringotts run --env-file deploy/gringotts.env -- \
      env CONNECTMD_NETWORK_DATABASE_URL="$CONNECTMD_NETWORK_DATABASE_URL" \
      npm --prefix apps/web run network:migrate

Rotate the database credential by updating the value in gringotts (a new
version) and redeploying; revoke access by revoking the grant's token;
restore by `gringotts restore` from a passphrase-encrypted backup.
