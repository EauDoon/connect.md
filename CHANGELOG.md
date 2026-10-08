# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-09

### Added

- Consent-first network MVP in `apps/web` ([ADR 0002](docs/decisions/0002-consent-first-network-mvp.md), #23): accounts with server-side sessions, private-by-default Markdown profiles with explicit publish and unpublish at `/p/{handle}`, contact requests that the recipient accepts, rejects, or blocks, conversations between accepted contacts, and owner-issued agent grants with explicit scopes, all under `/api/network/v1` on one PostgreSQL database. Without a configured database the network answers 503 and the guest workflow is unaffected.
- `deploy/with-network-secrets.sh` resolves `gringotts://` references into the deploy environment at deploy time, so network secrets are never committed (#24).
- Local drafting tools in the guest workflow, none of which upload a draft (#16 to #22, #29 to #31): reopen a local `.md` file, session checkpoints with a comparison before restoring, local recovery files, exact Markdown copy, paste import, a plain-text editor, a document outline and length, literal find and replace with reviewed replacements, sensitive-sharing and writing-review findings that link to source lines, print, review-report downloads, per-section export, and a record of whether the last download is current.
- Bounded offline PDF and DOCX ingestion in the retained API, with native OCR verified in the pinned API image in CI (#29).
- `tools/release_version.py` keeps `VERSION`, the web and API component versions, and this changelog in step (`check`, `set`, `notes`), and `python -m app.cli --version` reports the API version.
- Dependabot for GitHub Actions and the `apps/web` npm tree, weekly and grouped. Exactly pinned test engines and Monaco are excluded, and framework major versions are left for deliberate migrations.
- Security and contributing guides, issue and pull request templates, an architecture diagram, a quick start, and README badges (#36 to #38, #41, #44).
- [`surfaces/tanstack-start/`](surfaces/tanstack-start/): experimental parallel TanStack Start live-preview surface, archived. Paper light UI, unique-prefix handles, honest `writesOffered: false`, CTA "Paste this into your agent". Demo Focus sections only — no invented employers/titles/metrics. Its 2.20.x and 2.21.x labels are historical labels of the preview, not connect.md versions. **Does not replace** `apps/web` or `apps/api`.

### Changed

- The production surface is the standalone Vercel site in `apps/web` (#2). Legacy backend release jobs are retired (#3), and an API CI job gates `apps/api` with Ruff, mypy, pytest, and pip-audit (#14).
- The project license is MIT, and documentation that still said Apache 2.0 is corrected (#71). CI enforces the production npm audit only.
- Component versions follow `VERSION`: the web package, the API package, and the version the API serves in OpenAPI, its Agent Card, and MCP `serverInfo`, which previously advertised an unreleased 0.3.0.
- Releases are tagged and published only after CI passes on `main`, with notes taken from this file. A missing or inconsistent section now fails the release instead of skipping it.
- CI runs the repository tooling tests (module-size ratchets, source distribution, SBOM checker, secret gate, deploy wrapper, and release tool) and bounds every job's runtime. The retained platform registry checker is documented as not a merge gate.
- Owner-issued agent grants created without an expiry now expire after 90 days. Explicit expiries stay limited to one year, and grants created earlier with no expiry stay valid until revoked.
- Network password hashing runs off the request event loop, with the same scrypt parameters and stored format, so existing accounts keep signing in.
- Expired network sessions and rate-limit buckets are pruned about one day after they end. Network migration `0003_network_retention_indexes` adds the supporting indexes.
- Migration `0029_retention_residue_resource_indexes` adds `(resource_type, resource_id)` indexes to `change_events` and `idempotency_records`. The retention worker deletes idempotency and change-event residues for every disposed resource, and both tables previously had no index covering that predicate (#46).
- The contributing guide merges the per-directory checks with the product invariants and states that the API test lock is Linux-only; the root `SECURITY.md` and `CONTRIBUTING.md` point to the `.github` copies.

### Removed

- `scripts/extract-changelog-section.py`. Release notes come from `tools/release_version.py notes`.
- The 7-day and 14-day response commitments in the root `SECURITY.md`. The project is pre-launch and offers no service-level commitment yet.

### Fixed

- Browser draft validation now rejects wrong-typed and unexpected nested v2 frontmatter fields, including malformed contact channels and non-http(s) URL or email values, matching the write schema (#11).
- YAML frontmatter parse failures now name the document line and column, including aliases, duplicate keys, and scanner/parser causes, in the browser validator and API exceptions (#10).
- Standalone Human/Markdown Mode now fails closed on YAML aliases, duplicate keys, unknown frontmatter fields, malformed server timestamps, and drafts over the package 128 KiB UTF-8 limit, and CI covers the canonical invalid fixtures (#7). Empty drafts get explicit editor help (#9).
- `/agent-readme.md` now documents a complete private v2 starter that the browser validator accepts, plus fail-closed alias, unknown-field, and 128 KiB rules. Validation copy no longer claims an API is the authority on the standalone Vercel site (#8).
- Guest drafting keeps source whitespace (#45), protects edited drafts from accidental reloads (#5), sets a per-page canonical on `/human` and `/md` (#15), and has corrected contrast, landing heading semantics, and public copy (#6, #13, #25).
- The API honors documented upload and outreach limits from its environment (#12).
- Job and organization search escape LIKE metacharacters (#52, #57).
- Consent boundaries in the retained API: a connection block now stops contact requests, connection acceptance, job applications, and organization membership, and hides in-flight requests and existing applications; members cannot apply to their own organization, deciders cannot decide their own application, authors cannot report their own posts, a removed connection or an expired pending request frees the pair for a new request, and a recipient can list the requests they blocked (#53 to #68).
- Network consent, publication, and request boundaries are repaired, and network forms keep the user's input and report failed changes (#69, #70).
- Network cookie changes no longer fail with 403 when `NEXT_PUBLIC_SITE_URL` has a trailing slash or `CONNECTMD_NETWORK_ORIGIN` is blank. Unexpected network failures return a correlation id and are logged without personal data.
- `/p/{handle}` answers HTTP 404 with `noindex` for anything that is not a published profile, instead of an indexable 200 page.
- Public profile lookups validate the handle first: a prefix containing LIKE wildcards answers 400 and a malformed handle 404, never a 503 outage.
- Network forms: the handle field enforces the server's rule in current browsers, the account tabs follow the WAI-ARIA tabs pattern, an over-long message names the byte limit it hit, and a failed inbox load shows an error instead of an empty inbox.

### Security

- The secret gate recognizes connect.md bearer tokens (`cnag_`, `cnd_`, `cng_`), fine-grained GitHub tokens, npm tokens, and encrypted or PGP private key blocks.
- The network deploy wrapper refuses any env-file line that is not a `gringotts://` reference and never prints the value, and `deploy/gringotts.env` is gitignored.
- Stored scrypt parameters are bounded before a password is verified.
- Dependency advisories are patched in the web runtime and test tree, the API YAML constraint is tightened for CVE-2026-33532 (#34), and the API lock is pip-audit clean (#39).
- The web runtime moves to Next.js 15.5.27 with sharp 0.35.5 and source-map-js 1.2.2, clearing GHSA-4jqv-mc3x-m676, GHSA-mcj8-r9mp-w47p, GHSA-wq5f-xc86-pv6w, and GHSA-68fv-2mgg-jv7q from the production dependency audit.

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

[unreleased]: https://github.com/EauDoon/connect.md/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/EauDoon/connect.md/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/EauDoon/connect.md/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/EauDoon/connect.md/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/EauDoon/connect.md/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/EauDoon/connect.md/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/EauDoon/connect.md/releases/tag/v0.2.0
[0.1.0]: https://github.com/EauDoon/connect.md/releases/tag/v0.1.0
