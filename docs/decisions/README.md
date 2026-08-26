# Architecture decision records

Architecture decision records (ADRs) capture durable, repository-wide choices that are costly to reverse or that constrain multiple domains. They supplement, but do not replace, the product and safety contracts in `docs/`.

Create the next numbered record from [0000-template.md](0000-template.md) when a decision changes domain boundaries, authority, data lifecycle, public protocol behavior, operations, or a release claim. Keep the record factual: state the decision, alternatives, consequences, evidence, and any follow-up gate.

ADRs are append-only history. A later ADR supersedes an earlier one; do not silently rewrite an accepted decision. A feature manifest should link relevant ADRs, and its status must still satisfy the [platform feature lifecycle](../platform/feature-lifecycle.md) and [release matrix](../platform/release-matrix.md).
