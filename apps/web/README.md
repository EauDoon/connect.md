# connect.md web

The Next.js 15 App Router frontend for connect.md. It provides a guided Human Mode,
Markdown Mode, and public-profile rendering. Every write goes through the connect.md
API; the browser never talks to storage or PostgreSQL.

## Run locally

```bash
cp .env.example .env.local
npm install
npm run dev
```

`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` enables authenticated publishing. The editor
and preview remain usable without it, but publishing is intentionally disabled.
Set `NEXT_PUBLIC_API_BASE_URL` for browser requests when the API is on a
different origin. Server-rendered public profiles use `CONNECTMD_API_BASE_URL`;
inside Compose this is normally `http://api:8000`.

## Checks

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```

## Container

Build from this directory so the Dockerfile uses the standalone Next.js output:

```bash
docker build -t connectmd-web .
docker run --rm -p 3000:3000 --env-file .env connectmd-web
```

The runtime image is non-root and contains no secrets. Supply configuration only
at runtime.
