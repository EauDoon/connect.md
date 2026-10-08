# Platform integration scaffold

This directory makes cross-domain feature integration reviewable without changing the current architecture. It complements the repository's [architecture](../architecture.md), [trust and safety contract](../trust-safety.md), [acceptance contract](../acceptance.md), and [deployment runbook](../deployment.md); those contracts remain authoritative.

`apps/api/app/main.py` remains the API composition root. Features may add focused models, services, routes, migrations, UI, and tests, then be wired there deliberately. This scaffold does not prescribe a big-bang refactor.

- [Domain map](domain-map.md) assigns boundaries and integration ownership.
- [Feature lifecycle](feature-lifecycle.md) defines evidence-based feature stages and the feature-manifest concept.
- [Release matrix](release-matrix.md) records the required coverage axes and release evidence.
- [Coverage gaps and manual gates](coverage-gaps.md) states what the checker cannot prove.
- [Decision records](../decisions/README.md) preserve consequential architecture decisions.
- The [platform feature contract](../../packages/platform-contract/README.md) makes this coverage machine-checkable on demand.

The platform checker (`tools/check_platform_features.py`) and its two test modules (`tools/tests/test_check_platform_features.py` and `tools/tests/test_platform_route_test_ownership.py`) are retained-platform tooling and are **not a merge gate**. They carry known drift that is documented rather than repaired: anchors for the backend CI jobs retired in #3, the network MVP UI routes, and trust-page markers. The checker is also frozen by its module-size ratchet. CI gates these repository commands instead:

```bash
python tools/secret_scan.py
python tools/check_standalone_site.py
python -m unittest tools.tests.test_source_distribution tools.tests.test_check_dependency_sboms \
  tools.tests.test_module_size_ratchets tools.tests.test_with_network_secrets tools.tests.test_secret_scan
```

The scaffold describes how to make future claims. It does not certify a feature, deployment, or release.
