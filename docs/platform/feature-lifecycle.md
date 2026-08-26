# Feature lifecycle

Every material feature should have a feature manifest before it is described as available. The manifest is a review record, not a capability by itself: name and owner; domain boundaries; intended users and authority; data classification, persistent-model ownership, and lifecycle; exact API route ownership and representative UI routes; OpenAPI, capabilities, llms, MCP, A2A, and Agent Card visibility; search behavior; exclusions; test and operational evidence; and links to the applicable decision records.

| Stage | Meaning |
| --- | --- |
| `design` | The contract, boundaries, risks, exclusions, and acceptance evidence are specified. |
| `implemented` | The intended code and schema changes exist; this alone is not release evidence. |
| `feature_gated` | Implementation exists behind an explicit disabled-by-default or restricted gate and must not be advertised as generally available. |
| `repository_verified` | Reproducible repository evidence covers the manifest's applicable gates; unrun or failed critical checks keep this stage unavailable. |
| `deployment_verified` | Evidence from the intended deployment configuration demonstrates the applicable runtime and operational controls. |
| `releasable` | A reviewer can trace an accurate public claim to repository and deployment evidence, with remaining exclusions stated. |
| `disabled` | The capability is deliberately unavailable and absent from API, UI, search, protocol discovery, workers, and operational claims. |

Stages are evidence states, not a chronological promise: a change can return to `design`, and an expired control or contradictory evidence removes later-stage claims. A feature manifest must point to the domain map, matrix rows, and any relevant [decision record](../decisions/README.md).

Do not call a feature deployed, available, secure, erased, exported, retained, or releasable solely because an endpoint, migration, schema, or draft UI exists. Use the narrower supported claim.
