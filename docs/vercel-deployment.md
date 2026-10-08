# Vercel deployment: guest builder and optional network

The active application is Next.js in apps/web. Its guest builder requires no
backend. The optional network MVP uses Next.js route handlers and a separate
PostgreSQL database. FastAPI, Meilisearch, workers, Clerk, and the retained
compose stack are not dependencies of either Vercel mode.

## Project settings

| Setting | Value |
| --- | --- |
| Root Directory | apps/web |
| Framework | Next.js |
| Install Command | npm ci |
| Build Command | npm run build |
| Node.js | 22.x |
| Production branch | main |

For a guest-only deployment, the application environment variable is:

    NEXT_PUBLIC_SITE_URL=https://connect-md.vercel.app

Keep retained FastAPI, Clerk, recruiting, lifecycle, storage, and worker
settings out of this Vercel app. Leave CONNECTMD_NETWORK_DATABASE_URL unset
for guest-only operation. The production CSP permits only same-origin
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
- retired routes such as /workspace and /jobs return a non-indexable, no-store
  404. /discover is an active optional-network page; without a database it
  explains that discovery is unavailable.
- CSP, HSTS, X-Content-Type-Options, X-Frame-Options, Referrer Policy, and
  Permissions Policy are present.
- the browser console shows no failed API requests.
- a valid draft downloads locally and invalid Markdown remains blocked.

The guest drafting workflow never writes document content to a server.
An explicitly saved network profile is separate and is stored in PostgreSQL.

## Network MVP database

The network MVP (ADR 0002) requires one PostgreSQL database exposed to the
app as `CONNECTMD_NETWORK_DATABASE_URL`. Two deployment states are valid:

- **No database configured**: database-dependent API operations answer 503
  with `x-connectmd-network: unavailable`. Signed-out session reads may still
  return 401, and /discover explains the unavailable state. Guest routes are
  unaffected. Source and CI do not establish the current production state.
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


## Network launch and verification

Network activation is a separate operator-approved action. Do not infer that
it is enabled from a successful guest build or the presence of route source.
Set CONNECTMD_NETWORK_ORIGIN to the canonical browser origin. A blank value
falls back to NEXT_PUBLIC_SITE_URL. Surrounding whitespace and a bare trailing
slash are normalized away, so `https://host/` and `https://host` are the same
origin. A path, query, fragment, credentials, or a scheme other than http(s)
makes the value untrusted and every cookie mutation is refused; a malformed
CONNECTMD_NETWORK_ORIGIN never falls back to NEXT_PUBLIC_SITE_URL. Cookie
mutations require that Origin, including login, logout, publication, and
bodyless contact actions. Bearer-agent routes use their explicit scopes instead
of browser Origin.

An unexpected network-route failure answers 503 `network-unavailable` with an
`errorId` in the body and an `x-connectmd-error-id` header. The same id is
logged as `[connectmd-network] unexpected route failure` with the error class
and code only, never its message or query parameters, so a support report can
be matched to the server log without personal data reaching the log.

The app trusts Vercel's overwritten x-vercel-forwarded-for only when VERCEL=1.
Other hosts share a conservative rate bucket, so a caller cannot choose its own
IP quota by setting a forwarded header. See the [Vercel header contract](https://vercel.com/docs/headers/request-headers#x-vercel-forwarded-for).

Before accepting real network accounts, the operator must document and review:

- Database provider, region, operator contact, retention, deletion, and backup
  treatment for email, profiles, contact decisions, and messages.
- A network-specific notice at collection. The guest-only guarantees on /trust
  do not apply to deliberately submitted account/profile/message data.
- Account recovery and email verification. They are not implemented in this
  MVP; do not represent email ownership as verified or promise password reset.
- The bounded registration enumeration risk: responses do not identify which
  account detail conflicts, but successful registration necessarily differs
  from a conflict until a verified email onboarding flow is implemented.
- Current exact-commit network test evidence and a dated deployment receipt.

Use a fresh, disposable loopback database with a name ending in _test for
network checks. The integration suite truncates its network tables and refuses
other hosts/names. Never point it at production or a shared development store.

```text
npm --prefix apps/web run network:migrate
npm --prefix apps/web test -- tests/network-acceptance.test.ts
npm --prefix apps/web run test:network-e2e
```

CONNECTMD_NETWORK_DATABASE_URL must identify that disposable test database.
The browser check starts its own loopback development server, uses actual
Next.js handlers/PostgreSQL, and covers first save, two-tab conflict preservation,
publish/unpublish, and termination by the original contact requester. The
separate guest browser harness continues to verify the production build and
guest egress boundary. Neither test result is evidence of a production promotion.

Migrations use one serialized transaction and extensionless identities compatible
with existing databases. The unordered active-contact uniqueness migration fails
without deleting history if an older database contains conflicting active pairs.
Inspect and reconcile those records with the affected owners before retrying.

Published profile bytes cannot be edited in place by humans or agents. The
human owner unpublishes, saves a private revision with its current ETag, and
explicitly republishes it with that saved ETag. A stale edit or publication
attempt preserves the browser draft until the owner chooses to discard it.
Conversations open at their newest bounded page, with older messages available
through a stable cursor. Accepted contacts retain Close and Block controls;
older relationships remain reachable through paginated contact history.
