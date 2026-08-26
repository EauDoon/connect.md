# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-27

### Added

- Source versioning: `VERSION`, Keep a Changelog, [`docs/versioning.md`](docs/versioning.md), and a tag-driven GitHub Release workflow (`.github/workflows/release.yml`).
- Source-distribution allowlist now includes `CHANGELOG.md` and `VERSION`.

### Changed

- README documents Semantic Versioning, annotated `v*` source tags, and content-addressed Markdown document versions (SHA-256, stored outside git).

This is a source-version upgrade of the pre-launch foundation. It is not a production-deployment claim.

## [0.1.0] - 2026-08-26

First recorded pre-launch foundation (commit `55cfc6e`). Immutable document versions live at runtime under `storage/` (gitignored) and in PostgreSQL. This version records the published source; it is not a production-deployment claim.

[unreleased]: https://github.com/EauDoon/connect.md/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/EauDoon/connect.md/releases/tag/v0.2.0
[0.1.0]: https://github.com/EauDoon/connect.md/releases/tag/v0.1.0
