# connect.md web

The standalone Next.js 15 site deployed at
[connect-md.vercel.app](https://connect-md.vercel.app).

It provides:

- a guided profile and resume builder;
- a direct Markdown editor;
- client-side validation and sanitized preview;
- local .md download;
- static agent instructions and privacy documentation.

The active workflow has no account, API, database, upload, or server-side draft
storage. Retired backend-backed routes are blocked by middleware.ts.

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

Use this directory as the Vercel project Root Directory. Set only:

    NEXT_PUBLIC_SITE_URL=https://connect-md.vercel.app

The production build emits a self-contained CSP and standard security headers.
