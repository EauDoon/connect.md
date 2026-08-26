# connect.md

connect.md is a Markdown-native professional network built for agent-first access and a polished human experience.

The repository is organized as a small monorepo:

```text
apps/
  api/                    FastAPI service, ingestion, persistence, and search
  web/                    Next.js 15 App Router frontend
packages/
  markdown-schemas/       Canonical Profile and Resume Markdown contracts
  platform-contract/      Machine-readable feature coverage and release-state registry
infra/
  nginx/                  Production reverse-proxy configuration
  scripts/                VPS deployment and operational helpers
docs/                     Architecture and Hostinger runbooks
storage/                  Runtime Markdown versions (ignored except README)
.github/workflows/        CI checks
```

Start with the [architecture](docs/architecture.md), [platform integration scaffold](docs/platform/README.md), [social-network contract](docs/social-network.md), [trust and safety gates](docs/trust-safety.md), [agent interoperability contract](docs/agent-interoperability.md), [account lifecycle contract](docs/account-lifecycle.md), and [acceptance contract](docs/acceptance.md). Local API and web commands live in [apps/api/README.md](apps/api/README.md) and [apps/web/README.md](apps/web/README.md); the fresh-VPS-only production path is documented in [deployment.md](docs/deployment.md) and [operations.md](docs/operations.md).

Run `python tools/check_platform_features.py` to validate the feature registry and its repository anchors. The registry is an integration and truthfulness gate; it does not by itself prove deployment or release readiness.

The repository is an integrated pre-launch foundation, not evidence of a live deployment. Human-only account export/deletion is implemented but disabled by default. Production still requires fresh pinned lifecycle HMAC/AEAD and independent deletion-witness keys plus one-time `infra/scripts/init-deletion-journal.sh` initialization while the flags remain false. The journal and its separately mounted monotonic head witness must both be preserved, with every witness update durably replicated off-host to approved immutable/WORM storage. Restore uses strict witnessed-journal checkpoint refusal and exact live-mirror verification, not automatic replay of older generations. Real fresh-VPS Clerk-provider, PostgreSQL, Meilisearch, TLS, witness preservation, worker, backup, and restore verification remain release gates. No existing Hostinger instance is part of this project.
