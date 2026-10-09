# Source versioning

Git tags are source releases of this repository. They name an immutable Git commit of the published tree. They do not claim a production deployment, a live Hostinger instance, or a `releasable` [feature-lifecycle](platform/feature-lifecycle.md) stage.

Document versions are a different object. Canonical Profile and Resume Markdown is content-addressed by SHA-256, stored as append-only files under runtime `storage/` (gitignored except its README) and recorded in PostgreSQL. Updating a document writes a new file and a new version row; it does not rewrite Git history. See [architecture](architecture.md).

## Source release objects

- `VERSION` is the single source of truth for the current source version, as `MAJOR.MINOR.PATCH` under [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the project is pre-launch (`0.y.z`), a minor bump marks new features or changed behavior and a patch bump marks fixes.
- `CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/): an `## [Unreleased]` section, one `## [X.Y.Z] - YYYY-MM-DD` section per release, and compare links at the bottom.
- Annotated Git tags use the `vX.Y.Z` form and point at the commit that introduces that changelog section.
- GitHub Releases carry the matching `CHANGELOG.md` section as their notes. Every published tag and Release is listed on the [Releases page](https://github.com/EauDoon/connect.md/releases).

### Derived version copies

These files repeat the version and must always equal `VERSION`:

| File | Field |
| --- | --- |
| `apps/web/package.json` | `"version"` |
| `apps/web/package-lock.json` | `"version"` and `packages[""].version` |
| `apps/api/pyproject.toml` | `[project]` `version` (kept static so the Docker install and the lock regeneration stay unchanged) |
| `apps/api/app/__init__.py` | `__version__`, which the API serves as the OpenAPI `info.version`, the Agent Card `version`, the MCP `serverInfo.version`, and `python -m app.cli --version` |

[`tools/release_version.py`](../tools/release_version.py) keeps them together:

```bash
python tools/release_version.py set 0.4.0            # rewrite VERSION and every derived copy
python tools/release_version.py check                # verify copies, CHANGELOG section, and links
python tools/release_version.py check --tag v0.4.0   # also require the tag to match
python tools/release_version.py notes --tag v0.4.0 --output release-notes.md
```

`check` runs in the required `Standalone Vercel contract` CI job on every pull request and push, so a bump with a stale component, an undated section, or an old `[unreleased]` link fails CI instead of merging. The archived preview under `surfaces/tanstack-start` keeps its own historical 2.20.x and 2.21.x labels; they are not connect.md versions.

## Release procedure

1. In one pull request, run `python tools/release_version.py set X.Y.Z`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` above a new empty `## [Unreleased]`, add the `[X.Y.Z]: https://github.com/EauDoon/connect.md/compare/vPREVIOUS...vX.Y.Z` link, and point `[unreleased]` at `compare/vX.Y.Z...HEAD`. CI must be green.
2. Merge it to `main` with a merge commit.
3. CI runs on the merge commit. When it succeeds, [`.github/workflows/mint-source-tags.yml`](../.github/workflows/mint-source-tags.yml) runs `check --tag`, creates the annotated `vX.Y.Z` tag at the commit CI tested, and publishes the GitHub Release from that CHANGELOG section. Nothing is tagged from a red or cancelled run; the next green run on `main` mints instead.
4. If the automatic run does not start, run `gh workflow run "Mint source tags" -R EauDoon/connect.md --ref main`, which mints at `main` HEAD.

Manual path: a maintainer may push an annotated `vX.Y.Z` tag instead. [`.github/workflows/release.yml`](../.github/workflows/release.yml) then runs `check --tag` against the tagged tree and creates the Release, and does nothing if that Release already exists. Do not push a tag by hand while a mint run is in flight.

A source tag records the published source. It is not evidence that production Clerk, PostgreSQL, Meilisearch, TLS, witness, worker, backup, or restore gates have passed.

## History policy

Never rewrite tagged history. Do not force-push `main` over a tagged commit, delete a published `v*` tag, or retarget a tag that already has a GitHub Release. Correct a mistake with a new patch version and a new annotated tag. Never rewrite `55cfc6e`.
