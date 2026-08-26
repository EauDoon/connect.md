# Contributing to connect.md

Thank you for helping improve connect.md. The project is an integrated pre-launch foundation, so every change must preserve its authority, privacy, and release boundaries.

## Before you start

- Read the [architecture](../docs/architecture.md), [social-network contract](../docs/social-network.md), [agent interoperability contract](../docs/agent-interoperability.md), and [trust and safety contract](../docs/trust-safety.md).
- Use synthetic examples only. Never commit credentials, private identities, personal records, production data, or infrastructure secrets.
- Keep unrelated cleanup out of a focused change.
- Open a small issue before a broad schema, authorization, lifecycle, deployment, or protocol change.

## Product invariants

A contribution must not weaken these boundaries:

1. Canonical Markdown remains the public profile and resume content authority.
2. Human Mode and Markdown Mode preserve the same document without silent data loss.
3. The social graph and private workspaces remain private by default.
4. Recruitment, representation, outreach, and agent actions require explicit authority and consent.
5. State-changing operations preserve validation, ownership, idempotency, version checks, and auditability.
6. Search remains a rebuildable projection rather than a competing content store.
7. Missing, stale, contradictory, or unverifiable authority fails closed.
8. Local tests and fixtures never become claims of live deployment or production readiness.

## Development paths

| Area | Start here | Core checks |
| --- | --- | --- |
| API | [`apps/api/README.md`](../apps/api/README.md) | Ruff, mypy, pytest, migration checks |
| Web | [`apps/web/README.md`](../apps/web/README.md) | ESLint, TypeScript, Vitest, build |
| Markdown contracts | [`packages/markdown-schemas/README.md`](../packages/markdown-schemas/README.md) | Schema examples, invalid fixtures, canonical byte checks |
| Agent integration | [`examples/agent-clients/README.md`](../examples/agent-clients/README.md) | Hermetic client checker and unit tests |
| Platform contract | [`docs/platform/README.md`](../docs/platform/README.md) | Feature registry, ownership anchors, release-state checks |
| Infrastructure | [`docs/deployment.md`](../docs/deployment.md) | Static configuration and operational contract tests only unless a dedicated environment is authorized |

From the repository root, run the checks that cover your change. The platform registry and high-confidence secret scan are useful baseline gates:

```bash
python tools/check_platform_features.py
python tools/secret_scan.py
```

## Pull request checklist

- [ ] The change has one clear objective and the smallest sufficient file scope.
- [ ] New behavior includes focused positive, negative, authorization, and failure-state tests.
- [ ] Public copy is truthful about pre-launch and deployment status.
- [ ] UI controls remain keyboard accessible, screen-reader understandable, responsive, reduced-motion aware, and at least 44 pixels where they are discrete touch targets.
- [ ] Markdown round trips remain lossless and canonical content is not inferred from projections.
- [ ] No private data, secrets, local paths, generated reports, or runtime artifacts are included.
- [ ] Relevant linting, type checking, tests, builds, and repository contract checks pass.
- [ ] Documentation and release-state evidence are updated when behavior or authority changes.

## Security-sensitive changes

Do not place vulnerability details in a public issue or pull request. Follow the [security policy](SECURITY.md) for private-reporting guidance and safe research boundaries.

## License

Contributions accepted into this repository are distributed under the [Apache License 2.0](../LICENSE).
