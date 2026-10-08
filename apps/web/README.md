# connect.md web

The standalone Next.js 15 site deployed at
[connect-md.vercel.app](https://connect-md.vercel.app).

It provides:

- a guided profile and resume builder;
- local reopening of an existing UTF-8 `.md` file;
- a direct Markdown editor;
- client-side validation and sanitized preview;
- local .md download;
- static agent instructions and privacy documentation.

The guest drafting workflow (`/human`, `/md`) has no account, API, database,
upload, or server-side draft storage; drafts stay in the browser. Retired
backend-backed routes are blocked by middleware.ts.

The same app also ships an optional, consent-first network
([ADR 0002](../../docs/decisions/0002-consent-first-network-mvp.md)): accounts,
private-by-default profiles with explicit publish (`/p/{handle}`), contact
requests, conversations, and owner-issued agent grants that are scoped,
expiring, and revocable. Its route handlers live under `/api/network/v1` and
its pages are `/account`, `/network`, `/discover`, `/inbox`, `/conversations`,
and `/p`. Without a database, database-dependent network operations answer
503 `network-database-not-configured` and every `/p` address is a 404; the
guest workflow is unaffected.

## Run locally

    cp .env.example .env.local
    npm ci
    npm run dev

## Checks

    npm run lint
    npm run typecheck
    npm test
    npm run build

## Vercel

Use this directory as the Vercel project Root Directory. The guest site needs
one variable:

    NEXT_PUBLIC_SITE_URL=https://connect-md.vercel.app

The optional network also needs:

- `CONNECTMD_NETWORK_DATABASE_URL`: the PostgreSQL URL, resolved from the
  operator vault at deploy time and never committed;
- `CONNECTMD_NETWORK_ORIGIN`: the browser origin allowed to make
  cookie-authenticated changes. When blank it falls back to
  `NEXT_PUBLIC_SITE_URL`.

Apply schema migrations with `npm run network:migrate`. See
[docs/vercel-deployment.md](../../docs/vercel-deployment.md) for the deploy
wrapper, migrations, retention, and launch checks, and `.env.example` for
local development values.

The production build emits a self-contained CSP and standard security headers.
