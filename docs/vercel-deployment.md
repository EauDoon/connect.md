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
