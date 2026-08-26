"""Contact-durability platform evidence checks.

The checker supplies generic source/marker helpers so this domain remains
dependency-light and cannot import the composition root.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def contact_durability_errors(
    root: Path,
    *,
    _error: Callable[..., Any],
    _read_anchor_source: Callable[..., str],
    _require_source_markers: Callable[..., None],
    _function_source: Callable[..., str],
    _route_decorator: Callable[..., str | None],
) -> list[str]:
    """Bind private contact policy and decision writes to their durable contract."""
    errors: list[str] = []
    main_path = "apps/api/app/main.py"
    main_source = _read_anchor_source(root, main_path, errors)
    idempotency_header = {
        "Idempotency-Key name": '"name": "Idempotency-Key"',
        "Idempotency-Key header location": '"in": "header"',
        "required Idempotency-Key header": '"required": True',
        "minimum Idempotency-Key bound": '"minLength": 1',
        "maximum Idempotency-Key bound": '"maxLength": 128',
        "visible-ASCII Idempotency-Key pattern": "_IDEMPOTENCY_KEY_PATTERN",
    }
    policy_route = _route_decorator("PUT /v1/contact-policy", main_source)
    if policy_route is None:
        _error(
            errors,
            "repository.contact_durability.contact-policy-route",
            "cannot locate the implemented contact-policy route",
        )
    else:
        _require_source_markers(
            policy_route,
            f"{main_path}#contact-policy-route",
            {
                **idempotency_header,
                "mandatory exact If-Match header": '"name": "If-Match"',
                "200 response ETag": '"description": "Contact policy with the current ETag."',
            },
            errors,
        )
        if_match_start = policy_route.find('"name": "If-Match"')
        if_match = policy_route[if_match_start:] if if_match_start >= 0 else ""
        _require_source_markers(
            if_match,
            f"{main_path}#contact-policy-if-match",
            {
                "mandatory If-Match flag": '"required": True',
                "exact strong If-Match pattern": '"pattern": r\'^"policy-(0|[1-9][0-9]*)"$\'',
                "If-Match string schema": '"type": "string"',
            },
            errors,
        )
    decision_route = _route_decorator(
        "POST /v1/contact-requests/{contact_request_id}/{action}", main_source
    )
    if decision_route is None:
        _error(
            errors,
            "repository.contact_durability.contact-decision-route",
            "cannot locate the implemented contact decision route",
        )
    else:
        _require_source_markers(
            decision_route,
            f"{main_path}#contact-decision-route",
            idempotency_header,
            errors,
        )

    def require_order(
        source: str, function_name: str, anchors: list[tuple[str, str]]
    ) -> None:
        position = -1
        for label, marker in anchors:
            position = source.find(marker, position + 1)
            if position < 0:
                _error(
                    errors,
                    f"repository.contact_durability.{main_path}#{function_name}",
                    f"is missing {label} anchor {marker!r}",
                )
                return

    policy = _function_source(main_source, "update_contact_policy", main_path, errors)
    _require_source_markers(
        policy,
        f"{main_path}#update_contact_policy",
        {
            "direct contact-policy authority": "assert_direct(principal)",
            "owner-bound grant restriction": 'principal.resource_type != "owner"',
            "required contact-policy key": "key = idempotency_key(request, required=True)",
            "contact-policy operation": 'operation = "PUT:/v1/contact-policy"',
            "If-Match read": 'supplied = request.headers.get("If-Match")',
            "mandatory If-Match guard": "if supplied is None:",
            "conditional fingerprint": "conditional_fingerprint = json.dumps(",
            "body and conditional fingerprint": "body.model_dump_json(), conditional_fingerprint",
            "contact-policy stale If-Match guard": "if not compare_digest(supplied, current.etag)",
            "owner advisory lock call": "await lock_contact_policy_owner(session, principal.subject)",
            "contact-policy row lock": "select(ContactPolicy)",
            "safe policy receipt": 'resource_type="contact_policy"',
            "owner and body digest receipt": "public_owner_id(principal.subject)}:{sha256(response_body.encode()).hexdigest()}",
            "exact policy response ETag": 'headers={"ETag": result.etag}',
        },
        errors,
    )
    require_order(
        policy,
        "update_contact_policy",
        [
            ("direct authority", "assert_direct(principal)"),
            ("idempotency key", "key = idempotency_key(request, required=True)"),
            ("If-Match read", 'supplied = request.headers.get("If-Match")'),
            ("mandatory If-Match guard", "if supplied is None:"),
            ("conditional fingerprint", "conditional_fingerprint = json.dumps("),
            ("first replay", "replay = await idempotency_replay"),
            (
                "owner advisory lock",
                "await lock_contact_policy_owner(session, principal.subject)",
            ),
            ("second replay", "replay = await idempotency_replay"),
            ("policy lock", "select(ContactPolicy)"),
            (
                "stale If-Match precondition",
                "if not compare_digest(supplied, current.etag)",
            ),
        ],
    )
    if policy.find(".with_for_update()", policy.find("select(ContactPolicy)")) < 0:
        _error(
            errors,
            f"repository.contact_durability.{main_path}#update_contact_policy",
            "must lock ContactPolicy before the stale If-Match precondition",
        )
    owner_lock = _function_source(
        main_source, "lock_contact_policy_owner", main_path, errors
    )
    _require_source_markers(
        owner_lock,
        f"{main_path}#lock_contact_policy_owner",
        {
            "PostgreSQL dialect guard": 'session.get_bind().dialect.name == "postgresql"',
            "owner-namespaced advisory lock": "pg_advisory_xact_lock(hashtextextended(:lock_key, 0))",
            "owner lock namespace": '"lock_key": f"contact-policy:{owner_id}"',
        },
        errors,
    )

    def web_section(source: str, start_marker: str, end_marker: str, path: str) -> str:
        start = source.find(start_marker)
        end = source.find(end_marker, start + len(start_marker)) if start >= 0 else -1
        if start < 0 or end < 0:
            _error(
                errors,
                f"repository.contact_durability.{path}",
                f"cannot isolate {start_marker!r} section",
            )
            return ""
        return source[start:end]

    outreach_path = "apps/web/lib/outreach-api.ts"
    outreach_source = _read_anchor_source(root, outreach_path, errors)
    update_contact_policy = web_section(
        outreach_source,
        "export async function updateContactPolicy",
        "export async function listOutreach",
        outreach_path,
    )
    _require_source_markers(
        update_contact_policy,
        f"{outreach_path}#updateContactPolicy",
        {
            "explicit caller idempotency key": "idempotencyKey: string",
            "strong ETag preflight": "requiredContactPolicyEtag(policy.etag)",
            "caller Idempotency-Key forwarding": '"Idempotency-Key": idempotencyKey',
            "caller If-Match forwarding": '"If-Match": etag',
            "response ETag validation": 'parseContactPolicyMutationResponse(response.body, response.headers.get("ETag"))',
        },
        errors,
    )
    inbox_path = "apps/web/components/outreach-inbox.tsx"
    inbox_source = _read_anchor_source(root, inbox_path, errors)
    save_policy = web_section(
        inbox_source, "async function savePolicy()", "async function act(", inbox_path
    )
    _require_source_markers(
        save_policy,
        f"{inbox_path}#savePolicy",
        {
            "successful policy-read gate": 'policyLoadState !== "loaded"',
            "policy read-epoch gate": "privateReadAllowsDependentWrite(policyReadEpochRef.current)",
            "captured request subject": "const requestSubject = subject",
            "subject-current completion guard": "const requestIsCurrent = () => requestSubject === subject && isSubjectCurrent()",
            "ETag-bound logical intent": "etag: policy.etag",
            "logical attempt ownership": 'beginAttempt("policy", requestSubject',
            "caller key dispatch": "attempt.idempotencyKey",
            "stale completion return": "if (!requestIsCurrent()) return;",
            "successful attempt clearance": 'mutationAttemptsRef.current.delete("policy")',
        },
        errors,
    )
    agent_outreach_tests_path = "apps/web/tests/agent-outreach-api.test.ts"
    agent_outreach_tests = _read_anchor_source(root, agent_outreach_tests_path, errors)
    _require_source_markers(
        agent_outreach_tests,
        agent_outreach_tests_path,
        {
            "policy If-Match forwarding assertion": 'expect(headers.get("If-Match")).toBe(\'"policy-2"\')',
            "policy ETag response assertion": "etag: '\"policy-3\"'",
            "policy precondition key retention": "etag: '\"policy-4\"'",
        },
        errors,
    )
    inbox_tests_path = "apps/web/tests/outreach-inbox.test.ts"
    inbox_tests = _read_anchor_source(root, inbox_tests_path, errors)
    _require_source_markers(
        inbox_tests,
        inbox_tests_path,
        {
            "policy precondition logical-attempt test": "retains a lost-ack policy key only while its exact policy precondition stays unchanged",
            "policy ETag intent fixture": "etag: '\"policy-4\"'",
        },
        errors,
    )
    decision = _function_source(
        main_source, "decide_contact_request", main_path, errors
    )
    _require_source_markers(
        decision,
        f"{main_path}#decide_contact_request",
        {
            "direct decision authority": "assert_direct(principal)",
            "owner-bound decision grant": 'principal.resource_type != "owner"',
            "report-reason guard": 'if action == "report" and not reason:',
            "required decision key": "key = idempotency_key(request, required=True)",
            "decision operation": 'operation = f"POST:/v1/contact-requests/{contact_request_id}/{action}"',
            "decision body fingerprint": "normalized_body.model_dump_json()",
            "pre-lock decision replay": "replay = await idempotency_replay",
            "non-Clerk outreach query exclusion": 'row_conditions.append(ContactRequest.origin != "agent_outreach")',
            "ContactRequest row lock": "select(ContactRequest)",
            "post-lock decision replay": "replay = await idempotency_replay",
            "agent-outreach human-only defense": 'row.origin == "agent_outreach" and principal.method != "clerk_jwt"',
            "ContactPolicy row lock": "select(ContactPolicy)",
            "ContactBlock row lock": "select(ContactBlock)",
            "empty decision receipt": 'status_code=200,\n            body="",\n            headers={}',
            "decision receipt type": 'resource_type="contact_request_decision"',
            "hash-bound decision receipt": "_contact_decision_receipt_digest(row, action, response_body)",
        },
        errors,
    )
    require_order(
        decision,
        "decide_contact_request",
        [
            ("direct authority", "assert_direct(principal)"),
            ("required key", "key = idempotency_key(request, required=True)"),
            ("first replay", "replay = await idempotency_replay"),
            (
                "non-Clerk outreach exclusion",
                'row_conditions.append(ContactRequest.origin != "agent_outreach")',
            ),
            ("ContactRequest lock", "select(ContactRequest)"),
            ("second replay", "replay = await idempotency_replay"),
            ("ContactPolicy lock", "select(ContactPolicy)"),
            ("ContactBlock lock", "select(ContactBlock)"),
        ],
    )
    request_lock = decision.find("select(ContactRequest)")
    if request_lock < 0 or decision.find(".with_for_update()", request_lock) < 0:
        _error(
            errors,
            f"repository.contact_durability.{main_path}#decide_contact_request",
            "must lock ContactRequest before policy or block state",
        )
    replay = _function_source(main_source, "idempotency_replay", main_path, errors)
    decision_replay_start = replay.find(
        'if operation.startswith("POST:/v1/contact-requests/"):'
    )
    if decision_replay_start < 0:
        _error(
            errors,
            f"repository.contact_durability.{main_path}#idempotency_replay",
            "is missing the contact decision replay branch",
        )
        decision_replay = ""
    else:
        decision_replay_end = replay.find(
            'if (\n            record.operation == "POST:/v1/contact-requests"',
            decision_replay_start,
        )
        decision_replay = (
            replay[decision_replay_start:]
            if decision_replay_end < 0
            else replay[decision_replay_start:decision_replay_end]
        )
    policy_helper_path = "apps/api/app/services/contact_policy_replay.py"
    policy_helper = _function_source(
        _read_anchor_source(root, policy_helper_path, errors),
        "replay_contact_policy_receipt",
        policy_helper_path,
        errors,
    )
    if not all(
        marker in replay
        for marker in (
            'if operation == "PUT:/v1/contact-policy":',
            "replay_contact_policy_receipt(",
        )
    ):
        _error(
            errors,
            f"{main_path}#idempotency_replay-contact-policy-adapter",
            "is missing a policy replay adapter marker",
        )
    policy_markers = ['record.resource_type != "contact_policy"', "record.response_status != 200", "canonical_body != record.response_body", 'stored_headers != {"ETag": policy_receipt.etag}', "resource_parts[0] != expected_owner", "sha256(record.response_body.encode()).hexdigest()", "content=record.response_body", '"Idempotency-Replayed": "true"']  # fmt: skip
    missing_policy_marker = next(
        (marker for marker in policy_markers if marker not in policy_helper), None
    )
    if missing_policy_marker is not None:
        _error(
            errors,
            f"{policy_helper_path}#replay_contact_policy_receipt",
            f"is missing a contact-policy replay marker {missing_policy_marker!r}",
        )
    _require_source_markers(
        decision_replay,
        f"{main_path}#idempotency_replay-contact-decision",
        {
            "decision receipt type guard": 'record.resource_type != "contact_request_decision"',
            "empty decision body guard": 'record.response_body != ""',
            "empty decision header guard": 'record.response_headers != "{}"',
            "non-Clerk outreach replay exclusion": 'receipt_parts[2] == "agent_outreach" and principal.method != "clerk_jwt"',
            "retention replay guard": "retention_expired(row.retention_expires_at)",
            "report-reason replay guard": 'operation_parts[1] == "report" and not row.report_reason',
            "hash-bound decision replay": "_contact_decision_receipt_digest(row, operation_parts[1], response_body)",
        },
        errors,
    )
    durability_test_path = "apps/api/tests/test_contact_durability.py"
    durability_tests = _read_anchor_source(root, durability_test_path, errors)
    _require_source_markers(
        durability_tests,
        durability_test_path,
        {
            "policy receipt 4/4 test": "test_contact_policy_receipt_replay_concurrency_and_integrity",
            "policy OpenAPI mandatory If-Match and ETag assertions": 'if_match_parameter["required"] is True',
            "policy body and If-Match collision": "if_match_collision.status_code == 409",
            "policy receipt corruption": "corrupt_policy_receipt",
            "opaque nonhuman outreach 404 test": "test_nonhuman_agent_outreach_decisions_are_opaque_404",
            "real opaque 404 comparison": "response.status_code == nonexistent.status_code == 404",
            "opaque 404 leaves no receipt": "assert receipts == []",
            "decision receipt 4/4 test": "test_contact_decision_receipts_authority_integrity_and_redaction",
            "decision corruption": "corrupt_decision_receipt",
            "report-reason privacy": '"private report reason" not in field',
            "report-reason corruption": "changed_reason.status_code == 503",
            "deletion replay failure": "deleted.status_code == 503",
            "retention replay failure": "expired.status_code == 503",
            "same-key durable decision 4/4 test": "test_contact_decision_same_key_replay_and_transition_conflict_are_durable",
            "SQLite same-key limitation": "SQLite cannot prove PostgreSQL FOR UPDATE behavior",
            "PostgreSQL different-key limitation": "PostgreSQL serialization is source-designed but not proven by this SQLite test.",
        },
        errors,
    )
    fixture_path = "apps/api/tests/conftest.py"
    fixture = _read_anchor_source(root, fixture_path, errors)
    _require_source_markers(
        fixture,
        fixture_path,
        {"SQLite-only repository test fixture": "sqlite+aiosqlite:///"},
        errors,
    )

    mcp = _function_source(main_source, "mcp_tools", main_path, errors)
    if "decide_contact_request" in mcp or "contact_request_decision" in mcp:
        _error(
            errors,
            f"repository.contact_durability.{main_path}#mcp_tools",
            "MCP must not expose contact decision authority",
        )
    a2a = _function_source(main_source, "a2a_send_message", main_path, errors)
    if "decide_contact_request" in a2a or '"contact_decision"' in a2a:
        _error(
            errors,
            f"repository.contact_durability.{main_path}#a2a_send_message",
            "A2A must not expose contact decision authority",
        )
    return errors
