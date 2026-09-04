# connect.md

**A private, browser-only builder for portable professional Markdown.**

Production: [connect-md.vercel.app](https://connect-md.vercel.app)

The current product is a standalone Next.js site on Vercel. It helps a person or
their agent prepare a profile or resume, validate it, preview the sanitized
Markdown, and download the .md file. There is no account, database, upload,
publishing, messaging, analytics, or server-side document storage in the live
workflow.

## What works

- Guided profile and resume composition in Human Mode.
- Local reopening of an existing UTF-8 `.md` file.
- Direct Markdown editing with the existing schema validation.
- Sanitized local preview.
- Local .md download after validation.
- A bounded agent drafting runbook at /agent-readme.md.
- A concise machine-readable site map at /llms.txt.

Draft state lives only in React memory. Switching between /human and /md
preserves it during the current page session. After an edit, the browser warns
before a full reload or tab close; download before leaving because accepting
that warning erases the draft.

## Run locally

    cd apps/web
    cp .env.example .env.local
    npm ci
    npm run dev

Open http://localhost:3000. No service account or backend is required.

## Verify

    cd apps/web
    npm run lint
    npm run typecheck
    npm test
    npm run build

## Deploy

The Vercel project uses apps/web as its Root Directory, npm ci to install, and
npm run build to build. Its only application environment variable is:

    NEXT_PUBLIC_SITE_URL=https://connect-md.vercel.app

See [docs/vercel-deployment.md](docs/vercel-deployment.md) for the deployment
and acceptance sequence.

## Repository map

| Path | Status |
| --- | --- |
| apps/web | Active standalone Vercel site |
| packages/markdown-schemas | Active Markdown formats and fixtures |
| apps/api, infra, backend-oriented docs | Retained source; not part of the Vercel production deployment |

Retired backend-backed pages are blocked by the Next.js middleware with a
bounded, non-indexable 404.

## License

Licensed under the [Apache License 2.0](LICENSE).
