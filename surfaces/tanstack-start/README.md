# surfaces/tanstack-start

**Status:** experimental parallel live-preview surface (v2.20.1).
**Not** a replacement for `apps/web` (Next.js) or `apps/api` (FastAPI).

This directory archives the Grok-built TanStack Start (React + Vite + Tailwind v4) preview used during the 2026-08-27 perfection loops. It demonstrates:

- Paper light design (`#F6F5F1` paper / `#141413` ink, Newsreader + Inter)
- Unique-prefix public handles (`/p/maya` → 307 → `/p/maya-chen` when unique)
- Honest agent protocol: `writesOffered: false` in agent-card and llms.txt
- CTA: **Paste this into your agent** (discovery only)
- Canonical Markdown profiles with `## Focus` sections only — no invented employers, titles, or metrics
- Auth surface present (`RequireAuth`); private mutations require session
- Contact is a request (outreach); drafts stay private; no agent-submitted applications

## How this relates to the monorepo

| Path | Role |
| --- | --- |
| `apps/api` | Authoritative FastAPI service — **do not replace** |
| `apps/web` | Authoritative Next.js human UI — **do not replace** |
| `surfaces/tanstack-start` | Optional preview / experiment only |

See also: `docs/live-surface.md`, `docs/publication.md`, root `llms.txt`.

## Protocol honesty

```
writesOffered: false
```

Discovery is information, never permission. Agents must not invent credentials or submit applications without explicit human action.
