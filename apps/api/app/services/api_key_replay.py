"""Reconstruction of durable API-key idempotency receipts."""

import json
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, IdempotencyRecord


async def replay_api_key_receipt(
    session: AsyncSession,
    *,
    principal_subject: str,
    record: IdempotencyRecord,
    operation: str,
    recovery_response_factory: Callable[..., BaseModel],
    serialize_response: Callable[[BaseModel], str],
) -> Response:
    """Reconstruct an API-key create or revoke response from its owner-bound receipt."""

    if operation == "POST:/v1/api-keys":
        if (
            record.resource_type != "api_key"
            or record.response_status != 201
            or record.response_body
            or record.response_headers != "{}"
            or not record.resource_id
        ):
            raise HTTPException(
                status_code=503,
                detail="idempotent API-key creation receipt cannot be reconstructed",
            )
        api_key = await session.scalar(
            select(ApiKey).where(
                ApiKey.id == record.resource_id,
                ApiKey.owner_id == principal_subject,
            )
        )
        if api_key is None:
            raise HTTPException(
                status_code=503,
                detail="idempotent API-key creation committed but its receipt cannot be reconstructed",
            )
        try:
            scopes = json.loads(api_key.scopes)
            if (
                not isinstance(scopes, list)
                or not scopes
                or any(not isinstance(scope, str) or not scope for scope in scopes)
                or scopes != sorted(set(scopes))
                or not isinstance(api_key.prefix, str)
                or not api_key.prefix
                or len(api_key.prefix) > 24
                or not isinstance(api_key.created_at, datetime)
            ):
                raise ValueError("invalid API-key metadata")
            recovery = recovery_response_factory(
                id=api_key.id,
                prefix=api_key.prefix,
                scopes=scopes,
                created_at=(
                    api_key.created_at
                    if api_key.created_at.tzinfo is not None
                    else api_key.created_at.replace(tzinfo=UTC)
                ),
            )
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=503,
                detail="idempotent API-key creation receipt cannot be reconstructed",
            ) from exc
        return Response(
            content=serialize_response(recovery),
            status_code=201,
            media_type="application/json",
            headers={"Idempotency-Replayed": "true"},
        )

    if operation.startswith("DELETE:/v1/api-keys/"):
        recorded_key_id = operation.removeprefix("DELETE:/v1/api-keys/")
        if (
            record.resource_type != "api_key"
            or record.response_status != 204
            or record.response_body
            or record.response_headers != "{}"
            or not recorded_key_id
            or record.resource_id != recorded_key_id
        ):
            raise HTTPException(
                status_code=503,
                detail="idempotent API-key revocation receipt cannot be reconstructed",
            )
        api_key = await session.scalar(
            select(ApiKey).where(
                ApiKey.id == record.resource_id,
                ApiKey.owner_id == principal_subject,
            )
        )
        if api_key is None or not api_key.revoked:
            raise HTTPException(
                status_code=503,
                detail="idempotent API-key revocation receipt cannot be reconstructed",
            )
        return Response(
            status_code=204,
            headers={"Idempotency-Replayed": "true"},
        )

    raise AssertionError("unsupported API-key idempotency operation")
