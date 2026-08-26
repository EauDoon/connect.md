# Source versioning

Git tags are source releases of this repository. They name an immutable Git commit of the published tree. They do not claim a production deployment, a live Hostinger instance, or a `releasable` [feature-lifecycle](platform/feature-lifecycle.md) stage.

Document versions are a different object. Canonical Profile and Resume Markdown is content-addressed by SHA-256, stored as append-only files under runtime `storage/` (gitignored except its README) and recorded in PostgreSQL. Updating a document writes a new file and a new version row; it does not rewrite Git history. See [architecture](architecture.md).

## Source release objects

- `VERSION` holds the current source version as `MAJOR.MINOR.PATCH` (currently `0.2.0`).
- `CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
- Annotated Git tags use the `v*` form, for example `v0.2.0`, and must point at the commit that introduces that changelog section.
- GitHub Releases are created from those annotated tags by [`.github/workflows/release.yml`](../.github/workflows/release.yml), which copies the matching `CHANGELOG.md` section into the release notes.

A source tag records the published source. It is not evidence that production Clerk, PostgreSQL, Meilisearch, TLS, witness, worker, backup, or restore gates have passed.

## History policy

Never rewrite tagged history. Do not force-push `main` over a tagged commit, delete a published `v*` tag, or retarget a tag that already has a GitHub Release. Correct a mistake with a new patch version and a new annotated tag.
