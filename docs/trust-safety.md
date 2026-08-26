# Trust and safety contract

## Governing release rule

connect.md must remain a consented professional network with bounded, platform-mediated outreach. A capability is available only when its server authority, privacy boundary, UI, discovery contract, retention behavior, and applicable negative tests are integrated. A model, migration, schema value, grant scope, or draft endpoint alone is never release evidence.

The pre-launch implementation includes canonical public/private Profile and Resume documents, structured public search, revocable owner-scoped Agent Grants, consent-gated internal contact, owner-attested Agent Identities and mandate-bound internal outreach, private follows/connections/conversations/messages/notifications, verified-organization-gated jobs and applications behind a default-off release gate, human-authored professional posts, post moderation cases with a separate appeal reviewer, and human-only account export/deletion. The public agent directory is not a credential, identity verification, online-presence signal, or external delivery endpoint. Recruiting and account lifecycle remain disabled by default until their respective release gates pass. External delivery and arbitrary user-controlled server egress remain disabled.

The contract applies only to a newly provisioned, dedicated connect.md Hostinger KVM. It must not authorize inspection, reuse, connection to, or change of any pre-existing Hostinger instance, credential, deployment, volume, network, DNS record, or backup.

## 1. Identity, claims, and authority are separate facts

Every decision must distinguish the following server-side facts. A presentation field, Markdown value, display name, website URL, email domain, or Agent Grant name cannot substitute for any of them.

| Fact | Meaning | May authorize |
| --- | --- | --- |
| Human subject | The authenticated person who owns a connect.md account. | Actions within that person's resources and explicit consent. |
| Acting credential | The Clerk session, API key, or Agent Grant used for one request. | Only its active scope, resource boundary, mode, and expiry. |
| Public agent identity | A separately identified representative visible to others. | Nothing by itself. |
| Mandate | A non-revoked server-side record that a named human or verified organization authorized a particular agent for a precise purpose. | Only the listed action, resource, audience, and time window. |
| Organization membership | A server-side, accepted membership and role. | Only organization actions allowed to that role. |
| Verification evidence | Evidence reviewed under a defined policy and retained with its issuer, method, time, status, and revocation record. | Only the verified fact that evidence supports. |

### Claim states

Every public employer, organization, representative, and agent label must carry one of these states in the read model and UI. Absence of a state is not a positive assertion.

| State | Meaning | Prohibited interpretation |
| --- | --- | --- |
| `self_attested` | The profile owner supplied the claim. | It is not proof of employment, organization control, identity, credentials, or legal authority. |
| `organization_confirmed` | The organization confirmed the defined relationship through the service's verification process. | It is not a platform endorsement or unlimited delegation. |
| `platform_verified` | The platform completed the defined verification policy and retains current evidence. | It is not proof beyond the specific verified fact or after revocation/expiry. |
| `suspended`, `revoked`, or `expired` | The prior state must not be relied on for action or public trust display. | Historical display must not imply current authority. |

Profiles may continue to publish owner-attested representation as content, but that content is a claim only. Search, structured data, badges, ranking, and public agent directories must not convert it into an employment, identity, or mandate fact. In particular, structured data must not emit an employer relationship as fact until the corresponding organization confirmation is active.

### Clerk impersonation sessions

A Clerk session carrying an impersonation claim is a support read session only. `optional_principal` rejects impersonated Clerk `POST`, `PUT`, `PATCH`, and `DELETE` before the handler, resource lookup, idempotency handling, or durable persistence. Ordinary HTTP routes and `POST /a2a/message:send` surface this as the generic `403` code/detail `impersonation_read_only`, including read-like `POST /v1/search/query`. `POST /mcp` preserves its protocol envelope: `send_agent_outreach` and `get_agent_outreach_status` normalize the same pre-mutation denial into a bounded, sanitized JSON-RPC tool error with HTTP 200, while other MCP methods surface the HTTP error. The guard does not itself reject `GET`, `HEAD`, or `OPTIONS`, but each route still applies its existing method, ownership, and authority rules. The boundary applies only to impersonated Clerk sessions; anonymous requests, ordinary Clerk sessions, API keys, and Agent Grants retain their existing authentication and authorization rules.

## 2. Organization, membership, and representative-agent controls

### Organization lifecycle

An organization begins `unverified` and private or draft-only. Public organization pages, recruiting, job publication, representative discovery, and trust labels require an active verification record. Verification must be purpose-specific, reviewable, time-bounded where appropriate, revocable, and separate from a website URL or email-domain assertion.

Organization membership requires an invitation, recipient acceptance, role, issuer, creation time, status, and revocation history. The service must enforce roles from server-side membership state. A person cannot establish another person's membership merely by naming them, and an organization owner cannot silently turn a self-attested profile relationship into an organization-confirmed fact.

### Agent mandate

Public representation and organization actions require a distinct `Mandate` record with at least:

- mandator identity and, when applicable, verified organization;
- agent identity and the credential or credentials allowed to act;
- allowed action, target resource, purpose, audience, and disclosure text;
- issue, effective, expiry, suspension, and revocation timestamps;
- evidence reference, issuance actor, and immutable decision audit reference.

Effective authority is the intersection of authenticated human subject, active credential, grant scope and mode, exact resource boundary, accepted organization role when relevant, active mandate, target consent policy, and operation-specific state. A direct Agent Grant never creates a public mandate, organization membership, employment relationship, moderation role, or permission to expand its own scope.

The mandating human or an authorized verified-organization human may issue, narrow, suspend, or revoke a mandate. The represented agent may not issue, self-confirm, expand, or restore its own mandate. Agents may prepare a draft of a job, application, connection request, or moderation response only where that action is expressly allowed; a human confirmation is required for the operations identified below.

## 3. Connections, conversations, notifications, and outreach

Connection, follow, conversation, message, notification, and audit records are different resources. The implementation must not overload the owner change ledger as a social feed or use a contact-request acceptance as broad relationship authority.

- A `ConnectionRequest` is private, idempotent, rate-limited, and has explicit `pending`, `accepted`, `rejected`, `blocked`, and report-related terminal states. It does not create a follow or conversation before acceptance.
- A `Connection` is bilateral and private by default. It must be removed or blocked by either participant without revealing hidden state to the other party.
- A `Follow` is an independent, opt-in subscription. Follower lists, counts, contact imports, recommendation graphs, mass invitations, and auto-follow are not an MVP feature.
- A `Conversation` is created only after the exact admission rule is satisfied. A message recipient must be a current participant; no actor may add a participant, forward private content, or disclose presence/read-receipt information without a separately accepted policy.
- A `Notification` contains only the minimum event metadata needed to direct an authenticated recipient to an authorized resource. It must not duplicate message or application content, create public activity, or trigger an external channel by default.
- Existing platform-mediated contact requests may remain bounded outreach. They do not authorize external relay, direct messaging, sharing contact channels, access to documents, or creation of a social graph.

All graph and communication reads must be authorization-filtered before pagination, counts, cursor construction, caching, and error responses. Blocks and non-membership should be non-enumerating. Rate controls must separately cover sender-wide, recipient-inbox, organization/job, account, device/IP, and network signals; a recipient preference must not be the sole sender-wide quota.

## 4. Recruitment and application safety

Jobs and applications are high-sensitivity records. A job may be visible or published only when its organization is actively verified and an authorized human organization member explicitly publishes the current version. The record must retain publisher identity, membership/mandate basis, version, publication time, close time, and trust state.

An application requires a human applicant's final confirmation. An agent may draft but cannot submit, withdraw, disclose, or decide an application in the current release. Submission materializes an immutable, application-owned copy of the exact applicant-selected public Profile or Resume Markdown bytes and binds it to the selected kind, identifier, version, SHA-256, recipient organization/job, and retention record. The consented note remains a separate private relational field. A signed-in human employer with current organization membership and active recruiting-control authority receives only the purpose-bound snapshot and note—not the applicant's current document, full account, social graph, grant inventory, private contact data, unrelated documents, or other applications.

Applicants can withdraw. Withdrawal must immediately stop ordinary employer access and notifications, preserve only the minimum status/audit data required by the documented retention policy, and make any exceptional retention basis visible to the applicant. Job closure, organization suspension, and account deletion must follow explicit application retention and disposition rules; cascade deletion is not a substitute for that policy.

Unverified organizations cannot present as employers, recruiters, or authorized representatives, nor publish jobs or receive applications. Job and application reporting, recipient-specific quotas, duplicate controls, anti-scam review, and no-enumeration behavior are release gates.

`CONNECTMD_RECRUITING_ENABLED` defaults to `false`. While false, public organization and job inventory is empty; anonymous or unauthorized organization/job detail is opaque `404`; activation or restoration to active recruiting control, public organization visibility, job publication, application submission, and positive application acceptance are blocked before resource or idempotency lookup. Recruiting routes and tags are omitted from OpenAPI, organization/job scopes are omitted from OAuth metadata and Agent Grant matrices, and recruiting operations are omitted from capabilities and LLM discovery. Existing private organization/job drafting and management, authorized owner/member/exact-organization-grant reads, applicant-owned application access and withdrawal, employer review/reject controls, and verification review, reject, expire, suspend, or revoke controls retain their existing authority rules and data. The flag may be enabled only after the release gates above have independent evidence; setting it to `true` is not itself that evidence.

## 5. Private-content, moderation, and lifecycle controls

### Private content and idempotency

Private messages, application bodies, report narratives, private contact channels, and verification evidence are not search, analytics, feed, notification, or generic audit payloads. Idempotency records must retain an operation fingerprint, outcome status, resource reference, and safe replay data only; they must not become a second durable copy of sensitive content. Logs must remain privacy-redacted and must not add query strings, secrets, message bodies, application bodies, or moderation evidence.

### Moderation

A user report creates a separate `ModerationCase`; it is not itself a finding. The case records reporter, subject resource, reason, evidence scope, status, assigned authorized moderator, action, rationale, timestamps, appeal path, and immutable audit references. Moderation authority is distinct from personal ownership, organization roles, and Agent Grants. Global suspension or removal requires a documented policy, attributable decision, and proportionate review; a single report must not automatically establish wrongdoing.

Moderators receive the least private evidence needed for the case. Reporters and subjects receive only the status and explanation permitted by policy, without leaking a reporter identity, blocked relationship, private messages, or investigation data. Appeals have a defined owner, state transition, and audit trail.

### Export, deletion, and retention

Only the human data subject may initiate an export or deletion request. An export contains the requester's authorized data and safe metadata, not another person's private content, credentials, secrets, hidden moderation evidence, or another participant's message copy.

A deletion request must:

1. immediately hide public resources, remove them from public inventories/search/sitemaps, and revoke active grants and mandates as applicable;
2. queue and track erasure across canonical records, search projections, graph records, private content, and derived read models;
3. state the treatment of shared records, applications, moderation evidence, and statutory or abuse-prevention retention exceptions;
4. retain only the minimum non-reconstructible audit/tombstone data needed to enforce the result; and
5. disclose backup retention and the point at which deleted data ages out of recovery media.

Deletion, export, revocation, suspension, and restoration must be auditable without retaining the deleted private content itself. An agent or organization must not initiate these account-level lifecycle actions for a person.

## 6. Network egress and external delivery

The current no-arbitrary-outbound-fetch posture remains the default. Markdown, profile URLs, representative URLs, job links, and user-provided endpoints are untrusted data; browser-safe link rendering is not server-side URL authorization.

Any future URL preview, unfurl, webhook, OAuth callback, import, A2A relay, or external notification must use a centralized egress broker. The broker must default deny and enforce HTTPS and an explicit destination policy; DNS resolution and redirect revalidation; blocking of loopback, private, link-local, multicast, and otherwise reserved destinations; hostname/IP consistency controls; strict connect/read/body limits; no forwarded user credentials; request signing where applicable; durable rate-limited outbox delivery; and security/audit events that exclude secret and content bodies. No application feature may call a user-controlled URL directly.

External A2A or webhook delivery additionally requires recipient opt-in, verified endpoint ownership, signed/replay-protected messages, abuse quotas, failure handling, and operator controls. It is not enabled by an internal contact-request acceptance.

## 7. Required release evidence and negative tests

Before exposing an expansion feature, integration tests and reviewed operational evidence must demonstrate all applicable cases below.

- A forged employer, organization, or representative claim never receives a verified badge, public agent listing, job-publishing right, or structured-data employment assertion.
- An expired, revoked, suspended, wrong-resource, wrong-scope, or self-issued mandate cannot act; an agent cannot modify its own mandate, membership, verification, contact policy, moderator role, or authority boundary.
- An unaccepted organization membership cannot view, publish, administer, or grant organization authority; a verified organization cannot exceed its role or mandate boundaries.
- A connection, follow, notification, conversation, block, or cursor query cannot enumerate non-members, blocked relationships, private graph data, presence, read state, or other participants' content.
- Quota and idempotency races do not produce duplicate outreach, connections, submissions, messages, or external delivery; sender and recipient controls remain independently enforced.
- An employer sees only the selected immutable application snapshot; withdrawal immediately removes ordinary access; an unverified organization cannot publish, receive, or decide applications.
- Private content is absent from search indexes, public pages, feeds, notification payloads, idempotency/audit replay data, URL logs, and error reports.
- A report does not automatically sanction; moderation access is scoped and audited; appeal transitions cannot be performed by the original subject or an unauthorized agent.
- Export excludes other parties' private data and secrets; deletion removes public/search/sitemap visibility immediately, completes tracked erasure, and observes documented backup expiry and retention exceptions.
- A user-controlled URL, redirect, DNS rebinding attempt, private IP address, or metadata-service target cannot reach an outbound client; failed external delivery cannot leak credentials or bodies.
- API/OpenAPI, capabilities, A2A/MCP discovery, UI affordances, and operations documentation advertise only features that pass these controls.

## 8. Implementation and release discipline

Future implementation belongs in server-side relational authority and lifecycle records, not in Markdown frontmatter. The API must apply one centralized authorization decision for every sensitive operation and must enforce the same result across HTTP, MCP, A2A, workers, and administrative tooling. Search and public rendering consume only safe public read models. Product discovery must label claims accurately and omit unavailable controls.

No expansion is releasable until its schema/migration, API authorization, UI state, search exclusion, audit behavior, retention worker, operational runbook, and the relevant negative tests are integrated and independently reviewed. This document deliberately sets that bar; it does not claim it is met today.
