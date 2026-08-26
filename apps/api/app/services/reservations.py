"""Opaque, permanent namespace reservations created by account erasure."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import lifecycle_hmac
from app.config import Settings
from app.models import IdentifierReservation


def normalize_identifier(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError("identifier is invalid")
    return normalized


def identifier_reservation_hmac(settings: Settings, *, namespace: str, identifier: str) -> str:
    return lifecycle_hmac(
        settings, "identifier-reservation", f"{namespace}:{normalize_identifier(identifier)}"
    )


async def identifier_is_reserved(
    session: AsyncSession, settings: Settings, *, namespace: str, identifier: str
) -> bool:
    # Reservations outlive the feature toggle. If an operator removes the key,
    # all allocation fails closed once any opaque reservation exists.
    if settings.lifecycle_hmac_key is None:
        return (await session.scalar(select(IdentifierReservation.id).limit(1))) is not None
    digest = identifier_reservation_hmac(settings, namespace=namespace, identifier=identifier)
    return (
        await session.scalar(
            select(IdentifierReservation.id).where(
                IdentifierReservation.namespace == namespace,
                IdentifierReservation.identifier_hmac == digest,
            )
        )
        is not None
    )
