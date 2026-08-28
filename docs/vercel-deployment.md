# Vercel frontend deployment boundary

Assessment date: 28-08-2026
Source baseline: `f01defbe020378ac212ecbe809f7e02859ed22bb` (`main`)

## Decision

Vercel is suitable for the Next.js frontend in `apps/web`. It is not a replacement for the connect.md API deployment. Keep FastAPI, PostgreSQL, Meilisearch, immutable Markdown storage, the converter, the search projection worker, and the account lifecycle worker on a separately operated HTTPS API host.

The recommended shape is a split origin:

```text
https://<frontend-origin>  -> Vercel, apps/web
https://<api-origin>       -> FastAPI deployment and its Nginx boundary
```

The exact origins are deployment inputs. They must not be inferred from a Vercel preview URL or from the internal Compose address `http://api:8000`.

## Current frontend facts

- `apps/web/package.json` pins Next.js `15.5.22`, uses `npm run build`, and requires Node.js `>=20.9.0`. Its checked-in `package-lock.json` is the install authority.
- The App Router pages and root layout use `dynamic = "force-dynamic"` where live API reads are required. Server rendered reads use the server-only `CONNECTMD_API_BASE_URL` value.
- Browser reads and writes use `NEXT_PUBLIC_API_BASE_URL`. An empty value intentionally means same-origin paths for the existing Nginx topology. A split deployment must provide the API origin explicitly.
- The browser sends Clerk Bearer tokens directly to the API. The API already exposes the required CORS request and response headers when exact CORS origins are configured, with `allow_credentials=false`.
- `middleware.ts` protects the explicit private-route allowlist with Clerk and returns a bounded 404 when the publishable and secret keys are not both configured. The API remains the authority for resource and staff permissions.
- The frontend has no runtime path that writes canonical documents, PostgreSQL, Meilisearch, or persistent files. The `output: "standalone"` setting remains needed by the existing Docker image; do not change it merely to target Vercel.

## Required Vercel project settings

Create or configure one Vercel project for this repository with:

| Setting | Required value |
| --- | --- |
| Root Directory | `apps/web` |
| Framework | Next.js |
| Install Command | `npm ci` |
| Build Command | `npm run build` |
| Node.js | `22.x`, or another supported version satisfying `>=20.9.0` |
| Output Directory | Vercel default, with no static export override |
| Production branch | The approved repository `main` branch |

No root `vercel.json` is required when the Vercel project Root Directory is `apps/web`. Do not configure a second build from the repository root.

## Environment contract

Set these on the Vercel project and deliberately select the Vercel environments in which each value is allowed. Vercel's environment selection is `Production`, `Preview`, or `Development`; the code availability column below describes when the application consumes a value and whether it can reach the browser.

| Variable | Vercel environment selection | Code availability | Value and boundary |
| --- | --- | --- |
| `NEXT_PUBLIC_SITE_URL` | Production, plus deliberately authorized Preview | Build and browser | Exact canonical frontend HTTPS origin, such as `https://connect.example.com` |
| `NEXT_PUBLIC_API_BASE_URL` | Production, plus deliberately authorized Preview | Build and browser | Exact API HTTPS origin, such as `https://api.connect.example.com`; use the origin only, with no `/v1` suffix, credentials, query, or fragment |
| `CONNECTMD_API_BASE_URL` | Production, plus deliberately authorized Preview | Server code and runtime | The same exact API origin for server rendered public reads; keep it server-only |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Every Vercel environment that serves this frontend | Build and browser | The real Clerk publishable key. The CSP derives the Clerk Frontend API origin from this key, so do not replace it with a site-derived hostname. |
| `NEXT_PUBLIC_CLERK_DOMAIN` | Only an explicitly authorized satellite environment | Build and browser | Optional lowercase DNS hostname for Clerk satellite mode. It requires `NEXT_PUBLIC_CLERK_IS_SATELLITE=true` and a production publishable key. |
| `NEXT_PUBLIC_CLERK_IS_SATELLITE` | Only an explicitly authorized satellite environment | Build and browser | Optional literal `true` or `false`. `true` requires `NEXT_PUBLIC_CLERK_DOMAIN` or `NEXT_PUBLIC_CLERK_PROXY_URL`. |
| `NEXT_PUBLIC_CLERK_PROXY_URL` | Only an explicitly authorized proxy environment | Build and browser | Optional relative path such as `/__clerk`, or an explicit HTTPS proxy URL on the exact frontend origin. Requires a production Clerk key and must not be set with `NEXT_PUBLIC_CLERK_DOMAIN`. Query strings, fragments, credentials, cross-origin proxies, and non-HTTPS production URLs are rejected. |
| `CLERK_SECRET_KEY` | Every Vercel environment where private middleware is intentionally enabled | Server code, build and runtime only | The real Clerk secret. Mark it Sensitive in Vercel. Keep it server-only, never a `NEXT_PUBLIC_*` variable and never a browser build argument. |
| `CONNECTMD_RECRUITING_ENABLED` | Production and only an explicitly authorized Preview | Server runtime | `false` until the independent recruiting release gates are complete |
| `NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED` | Production and only an explicitly authorized Preview | Build and browser | `false` until the account lifecycle release gates are complete |

`NEXT_PUBLIC_*` values are embedded into the browser bundle at build time. Change them only through a new deployment. Vercel environment selection controls which deployments receive a variable; it does not convert a server-only value into browser data. A server-only value such as `CLERK_SECRET_KEY` may be available to selected build and runtime processes when the framework evaluates server code, while remaining unavailable to client code. Keep database credentials, Meilisearch keys, API-key peppers, lifecycle authorities, and other backend-only secrets on the API host.

This CSP profile supports production-mode Next.js builds for Vercel Production and Preview. It rejects a Next.js development server instead of enabling `unsafe-eval`. Use the existing `npm run dev` with `VERCEL` unset for local development; `vercel dev` is not supported by this profile.

In Vercel Project Settings, enable **Automatically expose System Environment Variables**. The frontend uses the exposed `VERCEL` value to activate the Vercel security headers through `next.config.ts`; `VERCEL_ENV` identifies the selected deployment environment. A Vercel deployment with those system variables disabled can still compile but must fail live acceptance because the required headers will be absent. A Vercel production build also fails closed if either public origin or the publishable key is missing or malformed. Docker builds do not emit this second policy, so the existing Nginx security header and CSP boundary remains authoritative there.

## Required API and Clerk changes

The existing API must be configured for the exact Vercel frontend origin before authenticated browser calls can work:

1. Add the frontend HTTPS origin to `CONNECTMD_CORS_ORIGINS` as an exact canonical origin. Do not use `*`, a wildcard subdomain, an origin with a path, or credentials in the value.
2. Add the API public origin and the frontend origin to `CONNECTMD_CLERK_AUTHORIZED_PARTIES` as exact canonical origins. The API configuration requires its `CONNECTMD_PUBLIC_BASE_URL` to be present in this list.
3. Configure the same frontend origin in Clerk's allowed origins and redirect settings. Use exact preview origins only when a preview is deliberately authorized.
4. Set the API's `CONNECTMD_PUBLIC_BASE_URL` to the origin that should own protocol discovery, OAuth metadata, Agent Card links, canonical Markdown URLs, and API-generated absolute links. If that is the API origin, keep the frontend URL in `NEXT_PUBLIC_SITE_URL` and use the API origin for both API base variables.
5. Ensure the API's public HTTPS endpoint is reachable from Vercel and retains its TLS, request limits, authentication, idempotency, and rate-limit controls. Vercel cannot reach the private Compose hostname `api:8000`.

A Clerk proxy URL only configures the SDK and CSP. It does not create a proxy route. Any selected proxy must already be separately configured and verified before use. Clerk requires the proxy on the application domain and uses it instead of `domain` for satellite configuration ([Clerk proxy guide](https://clerk.com/docs/guides/dashboard/dns-domains/proxy-fapi)).

The API remains the authority for Clerk subject binding, document ownership, staff roles, grants, consent, and every durable mutation. A frontend deployment must not weaken any of those checks.

## Why this does not add rewrites

The current frontend already sends browser requests to a configured split API origin and sends server rendered reads to the server-only API origin. Direct cross-origin requests make the backend CORS policy explicit and keep all protocol paths on the API origin.

An external rewrite could proxy ordinary API paths, but it would not provide a complete protocol boundary. Vercel documents that the `/.well-known` path is reserved and cannot be redirected or rewritten ([Vercel routing documentation](https://vercel.com/docs/routing/rewrites)). The API's Agent Card and OAuth metadata therefore must remain available at the API origin. A broad catch-all rewrite would also obscure the API security boundary and add Vercel's proxied request timeout to uploads and long API calls. No `vercel.json` rewrite or Next.js rewrite is justified by the current code.

The current API ingress accepts request bodies up to 12 MiB and allows up to 180 seconds for API upload and streaming responses. Vercel's current proxied request limit is 120 seconds. Keeping uploads on direct browser-to-API requests avoids making Vercel the upload proxy and preserves the API's existing size, timeout, and rate limit controls. Server rendered reads still run inside a Vercel Function, so live latency and function duration checks remain required.

## Preview and production boundaries

Vercel preview deployments receive changing origins. The API and Clerk allowlists are intentionally exact, so an arbitrary preview cannot be treated as authenticated production. Use a fixed preview domain, or add one exact preview origin to both API and Clerk configuration for a deliberate test, then remove it after the test. Never solve this with wildcard CORS or wildcard authorized parties.

An unauthenticated preview can still be useful for public rendering only if `CONNECTMD_API_BASE_URL` and `NEXT_PUBLIC_API_BASE_URL` point to a reachable API environment whose public data and policy are approved for preview use. Do not point a public preview at private production data without an explicit data and access decision.

## Vercel feasibility limits

Vercel Functions use a read-only filesystem with temporary `/tmp` scratch space. That is compatible with this frontend because canonical persistence remains in the API, but it cannot host the repository's persistent Markdown volume, PostgreSQL, Meilisearch, or long-lived workers. Do not implement a Vercel API route that writes a local file or attempts to run the FastAPI worker model.

The dynamic sitemap and several public pages make live API calls. Their latency, pagination bounds, API TLS, and Vercel function duration must be checked against the real API before production acceptance. Local fixture, loopback, or Next build evidence does not prove this path. Vercel's current proxied-request and function-duration limits are deployment-plan constraints, not reasons to move persistence into the frontend.

## Acceptance checks

Run the existing frontend checks from `apps/web` with the approved environment contract:

```bash
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
```

Then validate a Vercel preview and the API origin separately:

- Confirm **Automatically expose System Environment Variables** is enabled, then inspect the live Vercel response for the exact CSP and security headers. Missing headers are a failed acceptance result even when `npm run build` succeeds.
- `GET /`, `/robots.txt`, `/sitemap/0.xml`, `/sitemap/1.xml`, `/sitemap/2.xml`, `/sitemap/3.xml`, `/search`, one public profile, one public resume, and one public post render from the live API without exposing server configuration.
- Anonymous public reads remain available only for public projections. Private routes remain unavailable without a Clerk session, and missing Clerk configuration still fails closed.
- A representative authenticated read and one guarded write succeed only with a valid Clerk token, exact CORS origin, and API-side authority. Verify the `Authorization`, `Idempotency-Key`, `If-Match`, and exposed validator headers across the real origin.
- `/agent-readme.md`, `/llms.txt`, `/llms-full.txt`, `/openapi.json`, `/mcp`, A2A, Agent Card, and OAuth metadata are checked at the configured API origin. Do not claim that the Vercel origin serves `/.well-known` through a rewrite.
- Inspect Vercel function logs and API logs for timeouts, CORS failures, unexpected origin values, leaked secrets, and request-limit regressions.

The Vercel `npm run build` result is frontend compilation evidence only. It does not emit the repository's hermetic browser receipt or Docker image identity and cannot satisfy the existing release-accept image and receipt gates.

Until the API host, Clerk configuration, exact origins, and live preview checks are supplied and verified, Vercel frontend readiness is `configuration pending`, not production readiness. This document does not authorize deployment, external configuration changes, merge, or publication.

## Official references

- [Next.js rewrites](https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites)
- [Vercel rewrites and external origins](https://vercel.com/docs/routing/rewrites)
- [Vercel runtimes](https://vercel.com/docs/functions/runtimes)
- [Vercel function limits](https://vercel.com/docs/functions/limitations)
- [Vercel general limits](https://vercel.com/docs/limits)
- [Next.js response headers](https://nextjs.org/docs/app/api-reference/config/next-config-js/headers)
- [Vercel monorepos and Root Directory](https://vercel.com/docs/monorepos)
- [Vercel build configuration](https://vercel.com/docs/builds/configure-a-build)
- [Vercel framework environment variables](https://vercel.com/docs/environment-variables/framework-environment-variables)
- [Vercel environment variables](https://vercel.com/docs/environment-variables)
- [Vercel system environment variables](https://vercel.com/docs/environment-variables/system-environment-variables)
