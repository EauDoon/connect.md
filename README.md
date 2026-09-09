# connect.md

**A private, browser-only builder for portable professional Markdown.**

Production: [connect-md.vercel.app](https://connect-md.vercel.app)

The guest product is a self-contained Next.js workflow on Vercel. It helps a person or
their agent prepare a profile or resume, validate it, preview the sanitized
Markdown, and download the .md file. There is no account, database, upload,
publishing, messaging, analytics, or server-side document storage in the guest
drafting workflow at `/human` and `/md`.

The source also contains optional network account, discovery, and messaging
routes. Those require a separately configured network database. Route source
and a successful build do not establish that a production network service is
configured or operational. The guest workflow does not depend on those services.

## What works

- Guided profile and resume composition in Human Mode.
- Local reopening of an existing UTF-8 `.md` file.
- Direct Markdown editing with the existing schema validation.
- Sanitized local preview.
- Local .md download after validation.
- A bounded agent drafting runbook at /agent-readme.md.
- A concise machine-readable site map at /llms.txt.
- Five named, in-memory checkpoints with comparison and deliberate restoration.
- Local import of complete pasted profile or resume Markdown.
- A plain-text editor alternative and a clickable source outline.
- Draft length, advisory writing suggestions, and sensitive-sharing pattern checks.
- Exact Markdown copy to the system clipboard, with explicit failure feedback.
- Sanitized print preview, excluding frontmatter and editor controls.
- Local review reports with a SHA-256 fingerprint of the exact draft bytes.

Draft state lives only in React memory. Switching between /human and /md
preserves it during the current page session. After an edit, the browser warns
before a full reload or tab close; download before leaving because accepting
that warning erases the draft. Checkpoints also disappear on reload, even after
the current draft is downloaded. A clipboard copy or review report does not save
the source file. Clipboard history and synchronization follow your device settings.

## Try a revision safely

1. Open or paste your `.md` file in either drafting mode.
2. Expand Session checkpoints, name the current version, and keep it.
3. Edit in Guided Mode, the code editor, or the plain-text editor.
4. Compare the checkpoint against the current draft before deciding to restore it.
5. Inspect validation, Sharing review, and Writing review. The latter two are
   bounded heuristics, not fact checking or a security clearance.
6. Download the Markdown source. Optionally print its sanitized body or save a
   review report. The report excludes document text and must be regenerated after edits.

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

Seventeen retired backend-era route families are blocked by Next.js middleware
with a bounded, non-indexable 404. The optional network routes (`/account`,
`/network`, `/discover`, `/inbox`, `/conversations`, `/p`, and `/api/network/v1`)
are outside that retired-route list. Database-dependent operations require
configuration; this repository alone is not evidence of their live availability.

## License

Licensed under the [Apache License 2.0](LICENSE).
