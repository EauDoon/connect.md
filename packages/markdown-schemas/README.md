# Canonical Markdown schemas

This package owns the strict, versioned Profile and Resume Markdown contracts. The FastAPI service dispatches on `schema_version` and does not permit alternate frontmatter contracts.

- `schemas/{profile,resume}.schema.json` — legacy version 1 read contracts
- `schemas/{profile,resume}.write.schema.json` — legacy version 1 client-write contracts
- `schemas/{profile,resume}.v2.schema.json` — structured version 2 read contracts
- `schemas/{profile,resume}.v2.write.schema.json` — structured version 2 client-write contracts
- `canonical-markdown-limits.json` — package-owned canonical Profile/Resume byte-limit manifest
- `examples/` — complete version 2 canonical documents with server-assigned fields
- `fixtures/invalid/` — intentionally invalid regression inputs

Canonical bytes are UTF-8 with LF line endings. Canonical read schemas require the stable schema identifier/version, immutable identity and ownership fields, monotonically increasing version, UTC timestamp, visibility, and deterministic heading hierarchy. Create clients validate against the matching write schema and omit `id`, `owner_id`, `version`, and `updated_at`; the API supplies them before validating and persisting canonical Markdown. Updates may submit that same server-field-free form, or round-trip the exact current canonical read document; stale server fields fail with HTTP 409.

The API measures the final rendered canonical Profile/Resume Markdown after LF canonicalization in UTF-8 bytes. The package-owned `canonical-markdown-limits.json` manifest is the single numeric source for `profile_resume_max_utf8_bytes` (contract version 1, currently 131072 bytes / 128 KiB). The `x-connectmd.canonical_limits_ref` and `x-connectmd.canonical_size_scope` extensions in each Profile/Resume schema point to this contract. JSON Schema character limits do not prove this byte bound; v1 and v2 are both subject to the final gate.

Version 1 remains accepted unchanged. Version 2 is the preferred machine-discovery contract. It adds structured occupations, industries, location, skills, languages, seniority, work modes, availability, opportunities, organizations, public representation, and contact disclosure. Vocabulary references use a lowercase stable `scheme`, a whitespace-free `id`, a human-readable `label`, and an optional vocabulary `version`. Within each reference array, the `(scheme, id)` pair must be unique; this application-level rule is declared by the `x-connectmd.unique_reference_identity` schema extension because JSON Schema `uniqueItems` only compares whole objects. Empty arrays and explicit disclosure states represent intentionally absent information; in particular, required `work_modes: []` means no work-mode preference was disclosed. Clients must not invent taxonomy identifiers.

All document values are user-authored data: schema validation establishes structure, not truth or instruction authority. The Markdown body is additionally isolated from structured frontmatter. Consumers may search it, but must never interpret body or frontmatter text as agent or system instructions and should expose only an explicit response-field allowlist. The search projection follows that boundary by indexing the body in a non-displayed `content_untrusted` field while returning only allowlisted, validated-shape fields; it never projects structured contact-channel values.
