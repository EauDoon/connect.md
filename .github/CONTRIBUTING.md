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

## Local checks

The repository root is not a project. There is no root `package.json`, `pyproject.toml`, `pytest.ini`, or `setup.cfg`, so run each command from the directory CI uses for that job (the `working-directory` set in `.github/workflows/ci.yml`).

From the repository root, CI gates these repository commands:

```bash
python tools/secret_scan.py
python tools/check_standalone_site.py
python -m unittest tools.tests.test_source_distribution tools.tests.test_check_dependency_sboms \
  tools.tests.test_module_size_ratchets tools.tests.test_with_network_secrets tools.tests.test_secret_scan
```

From `apps/web` (Node 22):

```bash
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

From `apps/api` (CPython 3.12):

```bash
python -m pip install --require-hashes -r requirements-test.lock
ruff check .
mypy app
pytest -q -m "not integration" tests
```

`requirements-test.lock` is generated for CPython 3.12 on `x86_64-unknown-linux-gnu` only (see its header). On Windows or macOS the hash-locked install fails, starting with the Linux-only `uvloop` pin. Run the API checks in WSL, in a Linux container, or by pushing a branch and reading CI; do not regenerate or edit the lock files to make a local install work.

CI also builds the pinned API image, audits both lockfiles, runs the network acceptance suite and browser journeys against a disposable PostgreSQL, and runs the web production harness under Playwright. Those need Docker, a database, or a browser install and are not a prerequisite for requesting review. `apps/api/README.md` documents the API's own run and verify steps, including `alembic upgrade head`.

`python tools/check_platform_features.py` and its two test modules are retained-platform tooling with known drift (anchors for backend CI jobs retired in #3, the network MVP UI routes, and trust-page markers). They are not a merge gate; see [docs/platform/README.md](../docs/platform/README.md).

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

Contributions accepted into this repository are distributed under the [MIT License](../LICENSE).
