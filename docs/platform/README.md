# Platform integration scaffold

This directory makes cross-domain feature integration reviewable without changing the current architecture. It complements the repository's [architecture](../architecture.md), [trust and safety contract](../trust-safety.md), [acceptance contract](../acceptance.md), and [deployment runbook](../deployment.md); those contracts remain authoritative.

`apps/api/app/main.py` remains the API composition root. Features may add focused models, services, routes, migrations, UI, and tests, then be wired there deliberately. This scaffold does not prescribe a big-bang refactor.

- [Domain map](domain-map.md) assigns boundaries and integration ownership.
- [Feature lifecycle](feature-lifecycle.md) defines evidence-based feature stages and the feature-manifest concept.
- [Release matrix](release-matrix.md) records the required coverage axes and release evidence.
- [Coverage gaps and manual gates](coverage-gaps.md) states what the checker cannot prove.
- [Decision records](../decisions/README.md) preserve consequential architecture decisions.
- The [platform feature contract](../../packages/platform-contract/README.md) makes this coverage machine-checkable in CI.

The scaffold describes how to make future claims. It does not certify a feature, deployment, or release.
