from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

import app.auth as auth_module
from app.auth import (
    AuthenticationUnavailable,
    ClerkVerifier,
    LifecycleConfirmationClaims,
    Principal,
    require_lifecycle_confirmation_claims,
)
from app.config import Settings


class MockJwksProvider:
    def __init__(self) -> None:
        self.payload: dict[str, object] = {"keys": []}
        self.status_code = 200
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url == httpx.URL("https://clerk.example.test/.well-known/jwks.json")
        return httpx.Response(self.status_code, json=self.payload)


@pytest.fixture
def jwks_provider(monkeypatch: pytest.MonkeyPatch) -> MockJwksProvider:
    provider = MockJwksProvider()
    real_async_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        assert kwargs.get("timeout") == 5.0
        assert kwargs.get("follow_redirects") is False
        return real_async_client(
            *args,
            transport=httpx.MockTransport(provider.handle),
            **kwargs,
        )

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", client_factory)
    return provider


def clerk_settings() -> Settings:
    return Settings(
        clerk_jwks_url="https://clerk.example.test/.well-known/jwks.json",
        clerk_issuer="https://clerk.example.test",
        clerk_audience="connectmd-api",
        clerk_authorized_parties=["https://connect.example.test"],
        jwks_cache_seconds=300,
    )


def rsa_material(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return private_key, jwk


def issue_token(
    private_key: rsa.RSAPrivateKey,
    kid: str,
    settings: Settings,
    **overrides: object,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "user_clerk_test",
        "iss": settings.clerk_issuer,
        "aud": settings.clerk_audience,
        "azp": settings.clerk_authorized_parties[0],
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def lifecycle_auth_request(app: object, authorization: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/account-deletion-requests/id/confirm",
            "query_string": b"",
            "headers": headers,
            "client": ("test", 1),
            "server": ("testserver", 80),
            "scheme": "http",
            "app": app,
        }
    )


async def test_clerk_verifier_accepts_valid_token_and_caches_jwks(
    jwks_provider: MockJwksProvider,
) -> None:
    settings = clerk_settings()
    private_key, jwk = rsa_material("key-a")
    jwks_provider.payload = {"keys": [jwk]}
    verifier = ClerkVerifier(settings)
    token = issue_token(
        private_key,
        "key-a",
        settings,
        fva=[0, -1],
        reverification_id="reverification_test",
        sid="session_test",
        jti="token_test",
        act={"sub": "impersonator_test"},
    )

    principal = await verifier.verify(token)
    repeated = await verifier.verify(token)

    assert principal == repeated
    assert principal.subject == "user_clerk_test"
    assert principal.method == "clerk_jwt"
    assert principal.scopes == frozenset({"*"})
    assert principal.audit_actor_id == "user_clerk_test"
    assert principal.factor_verification_age == (0, -1)
    assert principal.reverification_id == "reverification_test"
    assert principal.session_id == "session_test"
    assert principal.token_id == "token_test"
    assert principal.is_impersonated is True
    assert len(jwks_provider.requests) == 1


async def test_clerk_verifier_rejects_expired_forged_and_wrong_party_tokens(
    jwks_provider: MockJwksProvider,
) -> None:
    settings = clerk_settings()
    trusted_key, trusted_jwk = rsa_material("key-a")
    attacker_key, _ = rsa_material("attacker")
    jwks_provider.payload = {"keys": [trusted_jwk]}
    verifier = ClerkVerifier(settings)

    expired = issue_token(
        trusted_key,
        "key-a",
        settings,
        exp=datetime.now(UTC) - timedelta(seconds=1),
    )
    forged = issue_token(attacker_key, "key-a", settings)
    wrong_party = issue_token(
        trusted_key,
        "key-a",
        settings,
        azp="https://attacker.example.test",
    )

    for token in (expired, forged):
        with pytest.raises(HTTPException) as caught:
            await verifier.verify(token)
        assert caught.value.status_code == 401
        assert caught.value.detail == "invalid or expired JWT"

    with pytest.raises(HTTPException) as caught:
        await verifier.verify(wrong_party)
    assert caught.value.status_code == 401
    assert caught.value.detail == "JWT authorized party is not allowed"


@pytest.mark.parametrize(
    "subject",
    [
        "",
        "x" * 256,
        "user/other",
        "user?other",
        "user#other",
        "user other",
        "usér_other",
    ],
)
async def test_clerk_verifier_rejects_unbounded_or_path_unsafe_subjects(
    jwks_provider: MockJwksProvider, subject: str
) -> None:
    settings = clerk_settings()
    private_key, jwk = rsa_material("key-subject")
    jwks_provider.payload = {"keys": [jwk]}

    with pytest.raises(HTTPException) as caught:
        await ClerkVerifier(settings).verify(
            issue_token(private_key, "key-subject", settings, sub=subject)
        )

    assert caught.value.status_code == 401
    assert caught.value.detail == "JWT subject is invalid"


async def test_clerk_verifier_accepts_maximum_safe_subject(
    jwks_provider: MockJwksProvider,
) -> None:
    settings = clerk_settings()
    private_key, jwk = rsa_material("key-subject")
    jwks_provider.payload = {"keys": [jwk]}
    subject = "u" + "a" * 254

    principal = await ClerkVerifier(settings).verify(
        issue_token(private_key, "key-subject", settings, sub=subject)
    )

    assert principal.subject == subject


async def test_clerk_verifier_refetches_unknown_kid_after_rotation(
    jwks_provider: MockJwksProvider,
) -> None:
    settings = clerk_settings()
    old_key, old_jwk = rsa_material("key-a")
    new_key, new_jwk = rsa_material("key-b")
    verifier = ClerkVerifier(settings)
    jwks_provider.payload = {"keys": [old_jwk]}

    old_principal = await verifier.verify(issue_token(old_key, "key-a", settings))
    assert old_principal.subject == "user_clerk_test"
    assert len(jwks_provider.requests) == 1

    jwks_provider.payload = {"keys": [new_jwk]}
    new_principal = await verifier.verify(issue_token(new_key, "key-b", settings))

    assert new_principal.subject == "user_clerk_test"
    assert len(jwks_provider.requests) == 2


async def test_clerk_verifier_bounds_sequential_unknown_kid_refreshes(
    jwks_provider: MockJwksProvider,
) -> None:
    settings = clerk_settings()
    attacker_key, _ = rsa_material("attacker")
    _, trusted_jwk = rsa_material("trusted")
    jwks_provider.payload = {"keys": [trusted_jwk]}
    verifier = ClerkVerifier(settings)

    for key_id in ("unknown-a", "unknown-b", "unknown-c"):
        with pytest.raises(HTTPException) as caught:
            await verifier.verify(issue_token(attacker_key, key_id, settings))
        assert caught.value.status_code == 401
        assert caught.value.detail == "unknown JWT signing key"

    assert len(jwks_provider.requests) == 1


async def test_clerk_verifier_coalesces_concurrent_unknown_kid_refreshes(
    jwks_provider: MockJwksProvider,
) -> None:
    settings = clerk_settings()
    attacker_key, _ = rsa_material("attacker")
    trusted_key, trusted_jwk = rsa_material("trusted")
    jwks_provider.payload = {"keys": [trusted_jwk]}
    verifier = ClerkVerifier(settings)
    await verifier.verify(issue_token(trusted_key, "trusted", settings))

    async def verify_unknown_key(key_id: str) -> None:
        with pytest.raises(HTTPException) as caught:
            await verifier.verify(issue_token(attacker_key, key_id, settings))
        assert caught.value.status_code == 401
        assert caught.value.detail == "unknown JWT signing key"

    await asyncio.gather(*(verify_unknown_key(f"unknown-{index}") for index in range(10)))

    assert len(jwks_provider.requests) == 2


async def test_clerk_verifier_accepts_rotation_after_unknown_kid_cooldown(
    jwks_provider: MockJwksProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic_time = 100.0
    monkeypatch.setattr(auth_module, "monotonic", lambda: monotonic_time)
    settings = clerk_settings()
    old_key, old_jwk = rsa_material("key-a")
    new_key, new_jwk = rsa_material("key-b")
    attacker_key, _ = rsa_material("attacker")
    verifier = ClerkVerifier(settings)
    jwks_provider.payload = {"keys": [old_jwk]}

    await verifier.verify(issue_token(old_key, "key-a", settings))
    with pytest.raises(HTTPException) as caught:
        await verifier.verify(issue_token(attacker_key, "unknown", settings))
    assert caught.value.status_code == 401
    assert caught.value.detail == "unknown JWT signing key"
    assert len(jwks_provider.requests) == 2

    jwks_provider.payload = {"keys": [new_jwk]}
    with pytest.raises(HTTPException) as caught:
        await verifier.verify(issue_token(new_key, "key-b", settings))
    assert caught.value.status_code == 401
    assert caught.value.detail == "unknown JWT signing key"
    assert len(jwks_provider.requests) == 2

    monotonic_time += 31.0
    principal = await verifier.verify(issue_token(new_key, "key-b", settings))

    assert principal.subject == "user_clerk_test"
    assert len(jwks_provider.requests) == 3


async def test_clerk_verifier_fails_closed_for_unavailable_or_malformed_jwks(
    jwks_provider: MockJwksProvider,
) -> None:
    settings = clerk_settings()
    private_key, _ = rsa_material("key-a")
    token = issue_token(private_key, "key-a", settings)
    jwks_provider.status_code = 503

    with pytest.raises(AuthenticationUnavailable, match="temporarily unavailable"):
        await ClerkVerifier(settings).verify(token)

    jwks_provider.status_code = 200
    jwks_provider.payload = {"keys": "not-a-list"}
    with pytest.raises(AuthenticationUnavailable, match="malformed"):
        await ClerkVerifier(settings).verify(token)


@pytest.mark.parametrize(
    ("authorization", "status_code"),
    [
        (None, 401),
        ("Basic opaque", 401),
        ("Bearer", 401),
        ("Bearer ", 401),
        ("Bearer cnd_api_key", 403),
        ("Bearer cng_agent_grant", 403),
    ],
)
async def test_lifecycle_confirmation_verifier_rejects_non_clerk_credentials(
    authorization: str | None, status_code: int
) -> None:
    async def unexpected_verifier(_: str) -> LifecycleConfirmationClaims:
        raise AssertionError("route-private verifier should not be bypassed")

    app = SimpleNamespace(
        state=SimpleNamespace(
            clerk=SimpleNamespace(verify_lifecycle_confirmation=unexpected_verifier)
        )
    )
    with pytest.raises(HTTPException) as caught:
        await require_lifecycle_confirmation_claims(lifecycle_auth_request(app, authorization))
    assert caught.value.status_code == status_code


async def test_lifecycle_confirmation_verifier_accepts_only_fresh_non_impersonated_clerk_claims(
    jwks_provider: MockJwksProvider,
) -> None:
    settings = clerk_settings()
    private_key, jwk = rsa_material("lifecycle-key")
    jwks_provider.payload = {"keys": [jwk]}
    verifier = ClerkVerifier(settings)
    app = SimpleNamespace(state=SimpleNamespace(clerk=verifier))
    valid = issue_token(private_key, "lifecycle-key", settings, sub="lifecycle_subject")
    claims = await require_lifecycle_confirmation_claims(
        lifecycle_auth_request(app, f"Bearer {valid}")
    )
    assert isinstance(claims, LifecycleConfirmationClaims)
    assert not isinstance(claims, Principal)
    assert claims.subject == "lifecycle_subject"

    impersonated = issue_token(
        private_key,
        "lifecycle-key",
        settings,
        sub="lifecycle_subject",
        act={"sub": "operator_subject"},
    )
    with pytest.raises(HTTPException) as caught:
        await require_lifecycle_confirmation_claims(
            lifecycle_auth_request(app, f"Bearer {impersonated}")
        )
    assert caught.value.status_code == 403
    assert caught.value.detail == "account_lifecycle_impersonation_forbidden"
