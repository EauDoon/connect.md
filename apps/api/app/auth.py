"""Clerk JWT and opaque connect.md agent-key authentication."""

from __future__ import annotations

import asyncio
import binascii
import json
import re
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new
from time import monotonic
from typing import Any

import anyio
import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.models import AccountAccessDeny, AccountLifecycle, AgentGrant, ApiKey, ChangeEvent


class AuthenticationUnavailable(RuntimeError):
    pass


_JWKS_UNKNOWN_KID_COOLDOWN_SECONDS = 30.0
_CLERK_SUBJECT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}\Z")
IMPERSONATION_READ_ONLY_CODE = "impersonation_read_only"


@dataclass(frozen=True)
class Principal:
    subject: str
    method: str
    scopes: frozenset[str]
    actor_id: str | None = None
    grant_id: str | None = None
    mandate_id: str | None = None
    grant_name: str | None = None
    grant_mode: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    factor_verification_age: tuple[int, int] | None = None
    reverification_id: str | None = None
    session_id: str | None = None
    token_id: str | None = None
    is_impersonated: bool = False

    @property
    def audit_actor_id(self) -> str:
        return self.actor_id or self.subject


@dataclass(frozen=True)
class LifecycleConfirmationClaims:
    """Minimal Clerk claims accepted only by the lifecycle confirmation route."""

    subject: str
    factor_verification_age: tuple[int, int] | None = None
    reverification_id: str | None = None
    session_id: str | None = None
    token_id: str | None = None
    is_impersonated: bool = False


AGENT_GRANT_RESOURCE_SCOPES: dict[str, frozenset[str]] = {
    "owner": frozenset(
        {
            "documents:write",
            "documents:read",
            "search:read",
            "inventory:read",
            "changes:read",
            "contacts:read",
            "contacts:write",
            "proposals:write",
        }
    ),
    "document": frozenset(
        {
            "documents:write",
            "documents:read",
            "inventory:read",
            "changes:read",
            "proposals:write",
        }
    ),
    "organization": frozenset(
        {
            "organizations:read",
            "organizations:write",
            "jobs:read",
            "jobs:write",
        }
    ),
}


def agent_grant_definition_is_valid(
    *,
    resource_type: str,
    resource_id: str | None,
    scopes: frozenset[str],
    mode: str,
    mandate_id: str | None,
) -> bool:
    """Fail closed when an Agent Grant crosses its declared resource domain."""
    if mode not in {"proposal_only", "direct"}:
        return False
    if mandate_id is not None:
        return (
            resource_type == "owner"
            and resource_id is None
            and mode == "direct"
            and scopes == frozenset({"contacts:write"})
        )
    allowed = AGENT_GRANT_RESOURCE_SCOPES.get(resource_type)
    resource_shape_is_valid = (
        resource_id is None
        if resource_type == "owner"
        else isinstance(resource_id, str) and bool(resource_id)
    )
    return bool(scopes) and allowed is not None and resource_shape_is_valid and scopes <= allowed


def lifecycle_hmac(settings: Settings, label: str, value: str) -> str:
    key = settings.lifecycle_hmac_key
    if key is None:
        raise AuthenticationUnavailable("account lifecycle HMAC is not configured")
    return hmac_new(
        key.encode("utf-8"), f"connect.md:lifecycle:{label}:v1:{value}".encode(), sha256
    ).hexdigest()


def _lifecycle_aead(settings: Settings) -> AESGCM:
    key = settings.lifecycle_aead_key
    if key is None:
        raise AuthenticationUnavailable("account lifecycle AEAD is not configured")
    derived_key = sha256(b"connect.md:lifecycle:aead:v1:" + key.encode("utf-8")).digest()
    return AESGCM(derived_key)


def _lifecycle_provider_context(deletion_id: str, field: str) -> bytes:
    return f"connect.md:lifecycle:provider-{field}:v1:{deletion_id}".encode()


def _encrypt_lifecycle_provider_value(
    settings: Settings, *, deletion_id: str, field: str, value: str
) -> str:
    if not 1 <= len(value) <= 255:
        raise AuthenticationUnavailable("account lifecycle provider value is invalid")
    nonce = secrets.token_bytes(12)
    ciphertext = _lifecycle_aead(settings).encrypt(
        nonce, value.encode("utf-8"), _lifecycle_provider_context(deletion_id, field)
    )
    return "v1." + urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def _decrypt_lifecycle_provider_value(
    settings: Settings, *, deletion_id: str, field: str, ciphertext: str
) -> str:
    try:
        version, encoded = ciphertext.split(".", 1)
        if version != "v1":
            raise ValueError
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = urlsafe_b64decode(padded.encode("ascii"))
        if len(payload) <= 12 + 16:
            raise ValueError
        value = (
            _lifecycle_aead(settings)
            .decrypt(payload[:12], payload[12:], _lifecycle_provider_context(deletion_id, field))
            .decode("utf-8")
        )
        if not value:
            raise ValueError
        return value
    except (InvalidTag, UnicodeDecodeError, UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise AuthenticationUnavailable("account lifecycle provider ciphertext is invalid") from exc


def encrypt_lifecycle_provider_subject(
    settings: Settings, *, deletion_id: str, subject: str
) -> str:
    """Return a versioned AEAD envelope for the later provider-only action."""
    return _encrypt_lifecycle_provider_value(
        settings, deletion_id=deletion_id, field="subject", value=subject
    )


def decrypt_lifecycle_provider_subject(
    settings: Settings, *, deletion_id: str, ciphertext: str
) -> str:
    return _decrypt_lifecycle_provider_value(
        settings, deletion_id=deletion_id, field="subject", ciphertext=ciphertext
    )


def encrypt_lifecycle_provider_session(
    settings: Settings, *, deletion_id: str, session_id: str
) -> str:
    return _encrypt_lifecycle_provider_value(
        settings, deletion_id=deletion_id, field="session", value=session_id
    )


def decrypt_lifecycle_provider_session(
    settings: Settings, *, deletion_id: str, ciphertext: str
) -> str:
    return _decrypt_lifecycle_provider_value(
        settings, deletion_id=deletion_id, field="session", ciphertext=ciphertext
    )


def encrypt_lifecycle_receipt(settings: Settings, *, deletion_id: str, receipt: str) -> str:
    return _encrypt_lifecycle_provider_value(
        settings, deletion_id=deletion_id, field="receipt", value=receipt
    )


def decrypt_lifecycle_receipt(settings: Settings, *, deletion_id: str, ciphertext: str) -> str:
    return _decrypt_lifecycle_provider_value(
        settings, deletion_id=deletion_id, field="receipt", ciphertext=ciphertext
    )


async def assert_account_access(
    session: AsyncSession, settings: Settings, subject: str, *, mutation: bool
) -> None:
    """Serialize credential mutation with lifecycle confirmation, then enforce denial."""
    if not settings.account_lifecycle_enabled:
        return
    await session.scalar(
        select(AccountLifecycle.id)
        .where(AccountLifecycle.subject_hmac == lifecycle_hmac(settings, "subject", subject))
        .with_for_update()
    )
    statement = select(AccountAccessDeny.id).where(
        AccountAccessDeny.subject_hmac == lifecycle_hmac(settings, "subject", subject)
    )
    if mutation:
        statement = statement.with_for_update()
    if await session.scalar(statement) is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_access_denied")


def _is_mutation(request: Request) -> bool:
    return request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


def _claim_string(claims: dict[str, Any], name: str) -> str | None:
    value = claims.get(name)
    return value if isinstance(value, str) and 1 <= len(value) <= 255 else None


def _factor_verification_age(claims: dict[str, Any]) -> tuple[int, int] | None:
    value = claims.get("fva")
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    first, second = value
    if (
        isinstance(first, bool)
        or isinstance(second, bool)
        or not isinstance(first, int)
        or not isinstance(second, int)
        or first < 0
        or second < -1
    ):
        return None
    return first, second


class ClerkVerifier:
    """JWKS verifier with TTL caching and configurable Clerk claim checks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._keys: dict[str, dict[str, Any]] = {}
        self._expires_at = datetime.min.replace(tzinfo=UTC)
        self._cache_generation = 0
        self._unknown_kid_generation = -1
        self._unknown_kid_retry_after = 0.0
        self._lock = asyncio.Lock()

    async def _load_jwks_locked(self) -> None:
        if self.settings.clerk_jwks_url is None:
            raise AuthenticationUnavailable("Clerk JWT validation is not configured")
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(str(self.settings.clerk_jwks_url))
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthenticationUnavailable("Clerk JWKS is temporarily unavailable") from exc
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list):
            raise AuthenticationUnavailable("Clerk JWKS response is malformed")
        self._keys = {key["kid"]: key for key in keys if isinstance(key, dict) and key.get("kid")}
        self._expires_at = datetime.now(UTC) + timedelta(seconds=self.settings.jwks_cache_seconds)
        self._cache_generation += 1

    async def _refresh(self) -> None:
        async with self._lock:
            if datetime.now(UTC) < self._expires_at:
                return
            await self._load_jwks_locked()

    async def _refresh_for_unknown_kid(
        self, key_id: str, observed_generation: int, *, cache_was_refreshed: bool
    ) -> None:
        async with self._lock:
            if key_id in self._keys or self._cache_generation != observed_generation:
                return
            now = monotonic()
            if cache_was_refreshed:
                self._unknown_kid_generation = self._cache_generation
                self._unknown_kid_retry_after = now + _JWKS_UNKNOWN_KID_COOLDOWN_SECONDS
                return
            if (
                self._unknown_kid_generation == self._cache_generation
                and now < self._unknown_kid_retry_after
            ):
                return
            self._unknown_kid_generation = self._cache_generation
            self._unknown_kid_retry_after = now + _JWKS_UNKNOWN_KID_COOLDOWN_SECONDS
            await self._load_jwks_locked()
            self._unknown_kid_generation = self._cache_generation

    async def _verify_claims(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            key_id = header["kid"]
            if not isinstance(key_id, str) or not 1 <= len(key_id) <= 255:
                raise KeyError("kid")
        except (jwt.PyJWTError, KeyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid JWT header"
            ) from exc
        generation_before_refresh = self._cache_generation
        if datetime.now(UTC) >= self._expires_at:
            await self._refresh()
        jwk = self._keys.get(key_id)
        if jwk is None:
            observed_generation = self._cache_generation
            await self._refresh_for_unknown_kid(
                key_id,
                observed_generation,
                cache_was_refreshed=observed_generation != generation_before_refresh,
            )
            jwk = self._keys.get(key_id)
        if jwk is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown JWT signing key"
            )
        try:
            algorithm = header.get("alg")
            if algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
                raise jwt.InvalidAlgorithmError("unsupported JWT algorithm")
            claims = jwt.decode(
                token,
                jwt.PyJWK.from_dict(jwk).key,
                algorithms=[algorithm],
                issuer=self.settings.clerk_issuer,
                audience=self.settings.clerk_audience or None,
                options={
                    "verify_iss": self.settings.clerk_issuer is not None,
                    "verify_aud": bool(self.settings.clerk_audience),
                    "require": ["exp", "nbf", "sub"],
                },
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired JWT"
            ) from exc
        if self.settings.clerk_authorized_parties and claims.get("azp") not in set(
            self.settings.clerk_authorized_parties
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT authorized party is not allowed",
            )
        subject = claims.get("sub")
        if not isinstance(subject, str) or _CLERK_SUBJECT_PATTERN.fullmatch(subject) is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="JWT subject is invalid"
            )
        return claims

    async def verify(self, token: str) -> Principal:
        claims = await self._verify_claims(token)
        subject = claims["sub"]
        assert isinstance(subject, str)
        return Principal(
            subject=subject,
            method="clerk_jwt",
            scopes=frozenset({"*"}),
            actor_id=subject,
            factor_verification_age=_factor_verification_age(claims),
            reverification_id=_claim_string(claims, "reverification_id"),
            session_id=_claim_string(claims, "sid"),
            token_id=_claim_string(claims, "jti"),
            is_impersonated="act" in claims,
        )

    async def verify_lifecycle_confirmation(self, token: str) -> LifecycleConfirmationClaims:
        claims = await self._verify_claims(token)
        subject = claims["sub"]
        assert isinstance(subject, str)
        return LifecycleConfirmationClaims(
            subject=subject,
            factor_verification_age=_factor_verification_age(claims),
            reverification_id=_claim_string(claims, "reverification_id"),
            session_id=_claim_string(claims, "sid"),
            token_id=_claim_string(claims, "jti"),
            is_impersonated="act" in claims,
        )


async def require_lifecycle_confirmation_claims(
    request: Request,
) -> LifecycleConfirmationClaims:
    """Verify only a Clerk JWT for the post-concealment confirmation replay path."""
    authorization = request.headers.get("Authorization")
    if authorization is None:
        raise HTTPException(status_code=401, detail="authentication is required")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential:
        raise HTTPException(status_code=401, detail="use Bearer authentication")
    if credential.startswith(("cnd_", "cng_")):
        raise HTTPException(status_code=403, detail="account_lifecycle_clerk_human_required")
    try:
        claims = await request.app.state.clerk.verify_lifecycle_confirmation(credential)
    except AuthenticationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if claims.is_impersonated:
        raise HTTPException(status_code=403, detail="account_lifecycle_impersonation_forbidden")
    return claims


class ApiKeyManager:
    """Stores only an Argon2id verifier and a non-secret lookup prefix."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.hasher = PasswordHasher()
        # One memory-hard operation at a time keeps the event loop responsive
        # without multiplying Argon2's memory cost on a small VPS.
        self._argon_limiter = anyio.CapacityLimiter(1)

    def _peppered(self, raw_key: str) -> str:
        if not self.settings.api_key_pepper:
            raise AuthenticationUnavailable("agent API keys are not configured")
        return raw_key + self.settings.api_key_pepper

    async def create(
        self,
        session: AsyncSession,
        owner_id: str,
        scopes: list[str],
        *,
        commit: bool = True,
    ) -> tuple[ApiKey, str]:
        raw_key = "cnd_" + secrets.token_urlsafe(32)
        secret_hash = await anyio.to_thread.run_sync(
            self.hasher.hash, self._peppered(raw_key), limiter=self._argon_limiter
        )
        record = ApiKey(
            owner_id=owner_id,
            prefix=raw_key[:20],
            secret_hash=secret_hash,
            scopes=json.dumps(sorted(set(scopes))),
        )
        session.add(record)
        await session.flush()
        if commit:
            await session.commit()
            await session.refresh(record)
        return record, raw_key

    async def verify(self, session: AsyncSession, raw_key: str) -> Principal | None:
        if not raw_key.startswith("cnd_"):
            return None
        prefix = raw_key[:20]
        candidates = (await session.scalars(select(ApiKey).where(ApiKey.prefix == prefix))).all()
        for candidate in candidates:
            try:
                await anyio.to_thread.run_sync(
                    self.hasher.verify,
                    candidate.secret_hash,
                    self._peppered(raw_key),
                    limiter=self._argon_limiter,
                )
            except (InvalidHashError, VerifyMismatchError):
                continue
            await assert_account_access(session, self.settings, candidate.owner_id, mutation=True)
            locked_candidate = await session.scalar(
                select(ApiKey).where(ApiKey.id == candidate.id).with_for_update()
            )
            if locked_candidate is None:
                continue
            if locked_candidate.revoked:
                continue
            locked_candidate.last_used_at = datetime.now(UTC)
            await session.flush()
            session.info["connectmd_auth_last_used"] = True
            scopes = json.loads(locked_candidate.scopes)
            return Principal(
                locked_candidate.owner_id,
                "agent_api_key",
                frozenset(scopes),
                actor_id=f"api-key:{locked_candidate.id}",
            )
        return None


class AgentGrantManager:
    """Issues expiring, named, resource-bound opaque agent credentials."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.hasher = PasswordHasher()
        self._argon_limiter = anyio.CapacityLimiter(1)

    def _peppered(self, raw_key: str) -> str:
        if not self.settings.api_key_pepper:
            raise AuthenticationUnavailable("agent grants are not configured")
        return raw_key + self.settings.api_key_pepper

    async def create(
        self,
        session: AsyncSession,
        *,
        owner_id: str,
        actor_id: str,
        name: str,
        scopes: list[str],
        mode: str,
        resource_type: str,
        resource_id: str | None,
        expires_at: datetime,
        mandate_id: str | None = None,
        commit: bool = True,
    ) -> tuple[AgentGrant, str]:
        normalized_scopes = frozenset(scopes)
        if not agent_grant_definition_is_valid(
            resource_type=resource_type,
            resource_id=resource_id,
            scopes=normalized_scopes,
            mode=mode,
            mandate_id=mandate_id,
        ):
            raise ValueError("agent grant scopes are incompatible with its resource")
        raw_key = "cng_" + secrets.token_urlsafe(32)
        secret_hash = await anyio.to_thread.run_sync(
            self.hasher.hash, self._peppered(raw_key), limiter=self._argon_limiter
        )
        now = datetime.now(UTC)
        record = AgentGrant(
            owner_id=owner_id,
            name=name.strip(),
            prefix=raw_key[:20],
            secret_hash=secret_hash,
            scopes=json.dumps(sorted(normalized_scopes)),
            mode=mode,
            resource_type=resource_type,
            resource_id=resource_id,
            mandate_id=mandate_id,
            expires_at=expires_at,
            created_at=now,
        )
        session.add(record)
        await session.flush()
        session.add(
            ChangeEvent(
                owner_id=owner_id,
                event_type="agent_grant.created",
                resource_type="agent_grant",
                resource_id=record.id,
                actor_id=actor_id,
                actor_method="clerk_jwt",
                payload=json.dumps(
                    {
                        "name": record.name,
                        "mode": mode,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "expires_at": expires_at.isoformat(),
                    },
                    sort_keys=True,
                ),
                occurred_at=now,
            )
        )
        if commit:
            await session.commit()
            await session.refresh(record)
        return record, raw_key

    async def verify(self, session: AsyncSession, raw_key: str) -> Principal | None:
        if not raw_key.startswith("cng_"):
            return None
        candidates = (
            await session.scalars(select(AgentGrant).where(AgentGrant.prefix == raw_key[:20]))
        ).all()
        now = datetime.now(UTC)
        for candidate in candidates:
            try:
                await anyio.to_thread.run_sync(
                    self.hasher.verify,
                    candidate.secret_hash,
                    self._peppered(raw_key),
                    limiter=self._argon_limiter,
                )
            except (InvalidHashError, VerifyMismatchError):
                continue
            await assert_account_access(session, self.settings, candidate.owner_id, mutation=True)
            locked_candidate = await session.scalar(
                select(AgentGrant).where(AgentGrant.id == candidate.id).with_for_update()
            )
            if locked_candidate is None:
                continue
            if locked_candidate.revoked:
                continue
            expires_at = locked_candidate.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                continue
            try:
                raw_scopes = json.loads(locked_candidate.scopes)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(raw_scopes, list) or not all(
                isinstance(scope, str) for scope in raw_scopes
            ):
                continue
            scopes = frozenset(raw_scopes)
            if not agent_grant_definition_is_valid(
                resource_type=locked_candidate.resource_type,
                resource_id=locked_candidate.resource_id,
                scopes=scopes,
                mode=locked_candidate.mode,
                mandate_id=locked_candidate.mandate_id,
            ):
                continue
            locked_candidate.last_used_at = now
            await session.flush()
            session.info["connectmd_auth_last_used"] = True
            return Principal(
                subject=locked_candidate.owner_id,
                method="agent_grant",
                scopes=scopes,
                actor_id=f"agent-grant:{locked_candidate.id}",
                grant_id=locked_candidate.id,
                mandate_id=locked_candidate.mandate_id,
                grant_name=locked_candidate.name,
                grant_mode=locked_candidate.mode,
                resource_type=locked_candidate.resource_type,
                resource_id=locked_candidate.resource_id,
            )
        return None


async def optional_principal(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal | None:
    authorization = request.headers.get("Authorization")
    credential: str | None = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value:
            raise HTTPException(status_code=401, detail="use Bearer authentication")
        credential = value
    if credential is None:
        return None
    key_manager: ApiKeyManager = request.app.state.api_keys
    if credential.startswith("cng_"):
        try:
            agent = await request.app.state.agent_grants.verify(session, credential)
        except AuthenticationUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if agent is None:
            raise HTTPException(status_code=401, detail="invalid, revoked, or expired agent grant")
        return agent
    if credential.startswith("cnd_"):
        try:
            agent = await key_manager.verify(session, credential)
        except AuthenticationUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if agent is None:
            raise HTTPException(status_code=401, detail="invalid or revoked agent API key")
        return agent
    try:
        clerk_principal = await request.app.state.clerk.verify(credential)
        if clerk_principal.is_impersonated and _is_mutation(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=IMPERSONATION_READ_ONLY_CODE,
            )
        await assert_account_access(
            session,
            request.app.state.settings,
            clerk_principal.subject,
            mutation=_is_mutation(request),
        )
        return clerk_principal
    except AuthenticationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def require_principal(principal: Principal | None = Depends(optional_principal)) -> Principal:
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal
