# ADR 0001: Compose the HTTP API from in-process routers

- Status: accepted
- Date: 2026-08-14
- Owners: API and platform maintainers
- Related feature manifest: [`platform-features.json`](../../packages/platform-contract/platform-features.json)
- Supersedes / superseded by: none

## Context

`apps/api/app/main.py` had 133 HTTP route functions and more than two hundred nested helper functions inside one `create_app` closure. This made unrelated route changes collide even though the deployment, authority boundary, and database remained shared. MCP, A2A, HTTP, workers, and canonical services must continue to apply one authorization and durability policy within the existing small-VPS resource envelope.

## Decision

Keep one FastAPI process and one `create_app` composition root. Move cohesive HTTP surfaces into module-level `APIRouter` objects and include them statically from `main.py`. Routers use `Request.app.state` and FastAPI dependencies for the app instance's configured services; they must not import a global app, call global `get_settings`, create a second authorization layer, or capture process-global configuration.

The first router owns only `/agent-readme.md`, `/llms.txt`, and `/llms-full.txt`. Their shared public-origin helper reads `request.app.state.settings`, preserving explicit `create_app(settings)` instances. Capabilities, schemas, MCP, A2A, authority, persistence, and write behavior remain unchanged.

## Consequences

- Route paths, names, response classes, OpenAPI visibility, registration order, and public representations remain compatibility contracts.
- Platform route inventory must follow statically imported routers without importing or executing them.
- `main.py` and the platform-checker entry point receive exact post-change line-count ratchets. Each later extraction lowers the applicable ratchet; new route behavior belongs in a domain module.
- The intended steady state is a wiring-only `main.py` below 800 lines and a checker front controller below 300 lines, reached through independently verified tranches rather than a single rewrite.
- This decision adds no BFF, microservice, process, dependency, network path, credential type, or competing source of truth.

## Alternatives considered

- A router factory closed over `Settings` was rejected because it obscures static route inventory and risks process-global configuration coupling.
- Splitting the API into services or adding a BFF was rejected because it duplicates authority policy and exceeds the intended deployment envelope.
- Moving canonical document routes first was rejected because their current nested-helper dependency closure crosses idempotency, recruiting, moderation, and outreach recovery.

## Validation and follow-up

The extraction requires canonical OpenAPI and ordered-route digest parity, focused protocol tests, platform checker mutation tests, Ruff, mypy, and the full API suite. Route inventory and discovery checker internals should be split into focused checker modules in a separate behavior-neutral tranche while preserving `python tools/check_platform_features.py` as the entry point. Deployment, live Clerk, live PostgreSQL concurrency, MCP client interoperability, and dedicated-VPS evidence remain separate release gates.
