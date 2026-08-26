# Platform feature contract

`platform-features.json` is the machine-readable integration inventory for material connect.md features. Its schema is `platform-feature-registry.schema.json`. `platform-route-ownership.json` assigns every FastAPI route decorator to exactly one registered feature, and `platform-ui-route-ownership.json` does the same for every Next.js App Router page; the checker compares both maps with current source on every run. `platform-evidence-receipt.schema.json` defines the revision-bound receipts required before any feature may claim a verified or releasable stage.

The registry records implementation stage, authority, API and UI surfaces, OpenAPI/capabilities/llms/MCP/A2A/Agent Card visibility, data and persistent-model ownership, export/conceal/erase/retention handling, workers, operations, tests, evidence paths, and deliberate exclusions. It is a coverage and truthfulness gate, not proof that a deployment or release is ready.

When adding or materially changing a feature:

1. update its registry entry and applicable documentation under `docs/platform`;
2. declare every public route and protocol surface, private-data boundary, lifecycle disposition, worker, operational control, and exclusion;
3. add repository-relative implementation and test anchors;
4. keep the stage at the narrowest evidence-backed value; and
5. run `python tools/check_platform_features.py` plus the focused unit tests.

The standard-library checker is the authoritative CI validator; the JSON Schema is the portable feature-format contract for compatible consumers. Per-feature API and UI routes are review anchors, while the API and UI ownership files are exhaustive. The checker fails closed on missing required domains, unowned or multiply owned persistent models, unowned or stale API or UI routes, invalid route owners, malformed entries, unsafe or missing paths, nonexistent API or UI anchors, unsupported stage claims, inconsistent discovery visibility, feature-gated OpenAPI exposure, duplicate identifiers, unclassified data or lifecycle handling, lifecycle discovery leakage, and protocol declarations without implementation and tests.

The checker also binds the current `/llms.txt` raw-Markdown workflow, Human Mode lossless guided-card and inventory-state surfaces, lifecycle default-disabled parity, and deploy/restore/health transition order to explicit feature anchors. These are source and focused-test gates, not live behavior claims. The checker deliberately does not claim semantic authority, complete behavioral test coverage, UI accessibility, migration execution safety, worker runtime health, or deployment and recovery verification. Those manual gates are listed in [coverage gaps and manual gates](../../docs/platform/coverage-gaps.md).

`platform-route-test-ownership.json` records source-witness traceability for every current API and UI route. It binds an owning feature to exact test source spans; it is not behavioral completeness, runtime proof, accessibility evidence, deployment proof, or a substitute for the existing test suites.

For `repository_verified`, `deployment_verified`, or `releasable`, the repository must be at a clean committed revision and evidence entries must be JSON receipts under `evidence/platform`. Each receipt binds the feature and evidence type to that exact current Git revision, real UTC timestamp, reviewer, target, configuration scope, passing commands, and bounded captured-output files under `evidence/platform/outputs` whose SHA-256 digests are recomputed by the checker. Receipt checks have unique lowercase `check_id` values, and repository receipts for the mapped controls must include every feature-specific required identifier. Deployment receipts remain separately scoped and cannot satisfy repository control IDs. An ordinary source, test, README, or deployment script is an anchor—not an evidence receipt.
