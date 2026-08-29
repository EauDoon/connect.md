# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- YAML frontmatter parse failures now name the document line and column, including aliases, duplicate keys, and scanner/parser causes, in the browser validator and API exceptions.
- Standalone Human/Markdown Mode now fails closed on YAML aliases, duplicate keys, unknown frontmatter fields, malformed server timestamps, and drafts over the package 128 KiB UTF-8 limit, and CI covers the canonical invalid fixtures.
- `/agent-readme.md` now documents a complete private v2 starter that the browser validator accepts, plus fail-closed alias, unknown-field, and 128 KiB rules. Validation copy no longer claims an API is the authority on the standalone Vercel site.

### Added

- [`surfaces/tanstack-start/`](surfaces/tanstack-start/): experimental parallel TanStack Start live-preview surface (v2.20.1). Paper light UI, unique-prefix handles, honest `writesOffered: false`, CTA "Paste this into your agent". Demo Focus sections only — no invented employers/titles/metrics. **Does not replace** `apps/web` or `apps/api`.

## [0.2.4] - 2026-08-27

### Added

- Unique-prefix handles in the publication contract: a live surface may resolve `/p/maya` to `/p/maya-chen` when that is the only public `maya-*` handle. Ambiguous prefixes 404. Canonical JSON always returns the stored handle.

### Changed

- [`docs/publication.md`](docs/publication.md) and [`docs/live-surface.md`](docs/live-surface.md) record the unique-prefix rule.
- [`docs/versioning.md`](docs/versioning.md) records source tag `v0.2.4`.

### Notes

This is a source-version upgrade. It does not replace `apps/web` or `apps/api`.
It is not a production-deployment claim. Never rewrite `55cfc6e`.

## [0.2.3] - 2026-08-26

### Added

- [`docs/publication.md`](docs/publication.md): the human-gated publication contract any live surface must honor.
  Drafts stay private. Publish is explicit. Handle is chosen before the first save.
  After publish, the human is shown the public URL, canonical Markdown, and JSON.
  Unpublish conceals the profile and rewrites visibility in the stored Markdown.
  Directory catalogs must set `writesOffered: false` until a running API issues scoped grants.

### Changed

- [`docs/live-surface.md`](docs/live-surface.md) points at the publication contract.
- [`docs/versioning.md`](docs/versioning.md) records source tag `v0.2.3`.

### Notes

This is a source-version upgrade. It does not replace `apps/web` or `apps/api`.
It is not a production-deployment claim. Never rewrite `55cfc6e`.

## [0.2.2] - 2026-08-26

### Added

- [`docs/live-surface.md`](docs/live-surface.md) distinguishing this source tree (`apps/web`, `apps/api`) from any separately hosted live network. The git tree is not that live surface.
- Mint-source-tags workflow now also mints the current `VERSION` at the pushing commit when the annotated tag is missing (historical `v0.1.0` / `v0.2.0` SHAs stay hardcoded and immutable).

### Changed

- Root [`llms.txt`](llms.txt) states more explicitly that MCP/A2A write tools are not granted by cloning, and that applications and contact stay human-gated.

### Notes

This is a source-version upgrade. It is not a production-deployment claim. Never replace `apps/web` or `apps/api` with an unrelated app. Never rewrite `55cfc6e`.

## [0.2.1] - 2026-08-26

### Added

- Root [`llms.txt`](llms.txt) describing this source repository for agents: schemas, examples, and the rule that discovery is not permission.
- [`docs/agent-source-map.md`](docs/agent-source-map.md) pointing agents at those files.

### Notes

This is a source-version upgrade. It is not a production-deployment claim. Write tools (MCP, A2A) still require a running API with grants.

## [0.2.0] - 2026-08-27

### Added

- Source versioning: `VERSION`, Keep a Changelog, [`docs/versioning.md`](docs/versioning.md), and a tag-driven GitHub Release workflow (`.github/workflows/release.yml`).
- Source-distribution allowlist now includes `CHANGELOG.md` and `VERSION`.

### Changed

- README documents Semantic Versioning, annotated `v*` source tags, and content-addressed Markdown document versions (SHA-256, stored outside git).

This is a source-version upgrade of the pre-launch foundation. It is not a production-deployment claim.

## [0.1.0] - 2026-08-26

First recorded pre-launch foundation (commit `55cfc6e`). Immutable document versions live at runtime under `storage/` (gitignored) and in PostgreSQL. This version records the published source; it is not a production-deployment claim.

[unreleased]: https://github.com/EauDoon/connect.md/compare/v0.2.4...HEAD
[0.2.4]: https://github.com/EauDoon/connect.md/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/EauDoon/connect.md/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/EauDoon/connect.md/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/EauDoon/connect.md/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/EauDoon/connect.md/releases/tag/v0.2.0
[0.1.0]: https://github.com/EauDoon/connect.md/releases/tag/v0.1.0
