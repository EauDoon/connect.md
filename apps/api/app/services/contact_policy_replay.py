"""Reconstruction of durable contact-policy idempotency receipts."""

import json
import re
from collections.abc import Callable
from hashlib import sha256
from hmac import compare_digest

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from app.models import IdempotencyRecord
from app.schemas import ContactPolicyResponse


def replay_contact_policy_receipt(
    *,
    principal_subject: str,
    record: IdempotencyRecord,
    owner_id_factory: Callable[[str], str],
    sha256_hex_pattern: str,
    serialize_response: Callable[[BaseModel], str],
) -> Response:
    """Reconstruct a policy response only from its owner- and digest-bound receipt."""

    if (
        record.resource_type != "contact_policy"
        or record.response_status != 200
        or not record.response_body
    ):
        raise HTTPException(
            status_code=503,
            detail="idempotent contact-policy receipt cannot be reconstructed",
        )
    try:
        raw_policy = json.loads(record.response_body)
        stored_headers = json.loads(record.response_headers)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="idempotent contact-policy receipt cannot be reconstructed",
        ) from exc
    if (
        not isinstance(raw_policy, dict)
        or set(raw_policy)
        != {
            "allow_agent_requests",
            "daily_request_limit",
            "version",
            "updated_at",
            "etag",
        }
        or not isinstance(raw_policy.get("allow_agent_requests"), bool)
        or not isinstance(raw_policy.get("daily_request_limit"), int)
        or isinstance(raw_policy.get("daily_request_limit"), bool)
        or not isinstance(raw_policy.get("version"), int)
        or isinstance(raw_policy.get("version"), bool)
        or raw_policy["version"] < 1
        or not isinstance(raw_policy.get("etag"), str)
        or raw_policy["etag"] != f'"policy-{raw_policy["version"]}"'
        or raw_policy.get("updated_at") is None
        or not isinstance(stored_headers, dict)
    ):
        raise HTTPException(
            status_code=503,
            detail="idempotent contact-policy receipt cannot be reconstructed",
        )
    try:
        policy_receipt = ContactPolicyResponse.model_validate(raw_policy)
    except ValidationError as exc:
        raise HTTPException(
            status_code=503,
            detail="idempotent contact-policy receipt cannot be reconstructed",
        ) from exc
    canonical_body = serialize_response(policy_receipt)
    resource_parts = (record.resource_id or "").split(":")
    expected_owner = owner_id_factory(principal_subject)
    if (
        canonical_body != record.response_body
        or stored_headers != {"ETag": policy_receipt.etag}
        or len(resource_parts) != 2
        or resource_parts[0] != expected_owner
        or not re.fullmatch(sha256_hex_pattern, resource_parts[1])
        or not compare_digest(resource_parts[1], sha256(record.response_body.encode()).hexdigest())
    ):
        raise HTTPException(
            status_code=503,
            detail="idempotent contact-policy receipt cannot be reconstructed",
        )
    return Response(
        content=record.response_body,
        status_code=200,
        media_type="application/json",
        headers={"ETag": policy_receipt.etag, "Idempotency-Replayed": "true"},
    )
