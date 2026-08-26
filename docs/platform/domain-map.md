# Platform domain map

Use this map with the [feature lifecycle](feature-lifecycle.md) when a change crosses a boundary. The API composition root is [`apps/api/app/main.py`](../../apps/api/app/main.py); it assembles integrations and includes statically declared in-process routers such as [`routes/discovery.py`](../../apps/api/app/routes/discovery.py), but it is not a reason to move every domain into one module.

| Domain | Boundary | Primary repository area |
| --- | --- | --- |
| Canonical documents | Markdown contracts, versioned persistence, rendering | `packages/markdown-schemas`, `apps/api/app/services/documents.py`, `storage` |
| Identity and authority | sessions, API keys, grants, mandates, ownership decisions | `apps/api/app/auth.py`, models, API routes |
| Discovery and protocols | public reads, search, OpenAPI, MCP, A2A, capability documents | API routes, `apps/api/app/services/search.py`, `docs/agent-interoperability.md` |
| Social and recruitment | contact, graph, messages, organizations, jobs, applications | API models, routes, `docs/social-network.md` |
| Trust and moderation | verification, reports, casework, appeals, audit boundaries | API models and routes, `docs/trust-safety.md` |
| Account lifecycle | export, concealment, erasure, retention state | `docs/account-lifecycle.md` and its implementation lanes |
| Ingestion | bounded conversion and draft creation | `apps/api/app/ingest.py`, `apps/api/app/ingest_worker.py` |
| Operations | deployment, backups, proxy, health, recovery | `infra`, Compose files, `docs/deployment.md`, `docs/operations.md` |
| Human experience | authenticated and public workflows | `apps/web` |

No domain may bypass server-side authority, publish private data through search or protocols, or make a lifecycle claim outside the applicable contract. Cross-domain work needs one feature manifest and the coverage review in the [release matrix](release-matrix.md).
