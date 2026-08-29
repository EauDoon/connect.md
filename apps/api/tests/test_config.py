from __future__ import annotations

import pytest
from pydantic import HttpUrl, ValidationError

from app.config import Settings
from app.services.database_roles import PROJECTION_ADMIN_DATABASE_ROLE


def _production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "database_url": (
            "postgresql+asyncpg://connectmd_api:strong-ci-password@postgres/connectmd"
        ),
        "meilisearch_url": "http://meilisearch:7700",
        "meilisearch_api_key": "production-test-search-key",
        "api_key_pepper": "production-test-pepper-at-least-thirty-two",
        "ingest_jobs_path": "/tmp/connectmd-ingest-test",
        "clerk_jwks_url": "https://clerk.example.test/.well-known/jwks.json",
        "clerk_issuer": "https://clerk.example.test",
        "clerk_authorized_parties": ["https://connect.example.test"],
        "public_base_url": "https://connect.example.test",
        "verification_reviewer_id": "reviewer:production",
        "verification_reviewer_role": "recruiting_verifier",
        "post_moderator_id": "moderator:production",
        "post_moderator_role": "content_moderator",
        "appeal_reviewer_id": "appeals:production",
        "appeal_reviewer_role": "appeal_reviewer",
        "lifecycle_hmac_key": "h" * 32,
        "lifecycle_aead_key": "a" * 32,
        "deletion_journal_path": "/deletion-journal",
        "deletion_witness_path": "/deletion-head-witness",
        "deletion_witness_hmac_key": "w" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_auth_configuration_is_complete() -> None:
    settings = _production()
    settings.require_api_runtime_configuration()
    assert settings.is_production


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///production.db",
        "postgresql://connectmd:strong-ci-password@postgres/connectmd",
        "postgresql+psycopg://connectmd:strong-ci-password@postgres/connectmd",
        "not-a-database-url",
    ],
)
def test_production_api_requires_exact_asyncpg_database_driver(database_url: str) -> None:
    settings = _production(database_url=database_url)

    with pytest.raises(ValueError, match=r"postgresql\+asyncpg"):
        settings.require_api_runtime_configuration()


def test_production_api_requires_the_dedicated_database_login() -> None:
    settings = _production(
        database_url="postgresql+asyncpg://connectmd:secret-that-must-not-render@postgres/connectmd"
    )

    with pytest.raises(ValueError, match="database role is invalid") as exc_info:
        settings.require_api_runtime_configuration()

    assert "secret-that-must-not-render" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"meilisearch_url": None, "meilisearch_api_key": None},
            "CONNECTMD_MEILISEARCH_URL",
        ),
        ({"meilisearch_api_key": " " * 16}, "CONNECTMD_MEILISEARCH_API_KEY"),
        ({"meilisearch_index": "   "}, "CONNECTMD_MEILISEARCH_INDEX"),
    ],
)
def test_production_api_requires_configured_keyed_meilisearch(
    overrides: dict[str, object], message: str
) -> None:
    settings = _production(**overrides)

    with pytest.raises(ValueError, match=message):
        settings.require_api_runtime_configuration()


def test_non_api_production_settings_do_not_globally_require_meilisearch() -> None:
    settings = _production(meilisearch_url=None, meilisearch_api_key=None)

    assert settings.is_production


def test_dedicated_database_role_check_preserves_sqlite_but_gates_postgresql() -> None:
    Settings(database_url="sqlite+aiosqlite:///local.db").require_database_role_configuration(
        "connectmd_api"
    )

    with pytest.raises(ValueError, match="database role is invalid"):
        Settings(
            database_url="postgresql+asyncpg://connectmd:secret@postgres/connectmd"
        ).require_database_role_configuration("connectmd_api")


def test_production_non_api_database_role_requires_exact_asyncpg_driver() -> None:
    settings = _production(database_url="sqlite+aiosqlite:///production.db")

    with pytest.raises(ValueError, match=r"postgresql\+asyncpg"):
        settings.require_database_role_configuration(PROJECTION_ADMIN_DATABASE_ROLE)


def test_settings_validation_errors_hide_secret_inputs() -> None:
    secret = "do-not-render-this-database-password"

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            environment="production",
            database_url=f"postgresql+asyncpg://connectmd:{secret}@postgres/connectmd",
        )

    assert secret not in str(exc_info.value)
    assert "input_value" not in str(exc_info.value)


def test_recruiting_release_defaults_off_and_requires_explicit_environment_opt_in(
    monkeypatch,
) -> None:
    monkeypatch.delenv("CONNECTMD_RECRUITING_ENABLED", raising=False)
    assert Settings(_env_file=None).recruiting_enabled is False

    monkeypatch.setenv("CONNECTMD_RECRUITING_ENABLED", "true")
    assert Settings(_env_file=None).recruiting_enabled is True


@pytest.mark.parametrize(
    ("first_key", "second_key"),
    [
        ("lifecycle_hmac_key", "lifecycle_aead_key"),
        ("lifecycle_hmac_key", "deletion_witness_hmac_key"),
        ("lifecycle_aead_key", "deletion_witness_hmac_key"),
    ],
)
@pytest.mark.parametrize("configuration", ["production", "lifecycle"])
def test_lifecycle_authority_keys_must_be_pairwise_distinct(
    first_key: str, second_key: str, configuration: str
) -> None:
    duplicate = "shared-authority-key-material-32-bytes"
    overrides = {first_key: duplicate, second_key: duplicate}
    values: dict[str, object] = {
        "account_lifecycle_enabled": True,
        "lifecycle_hmac_key": "h" * 32,
        "lifecycle_aead_key": "a" * 32,
        "deletion_journal_path": "/deletion-journal",
        "deletion_witness_path": "/deletion-head-witness",
        "deletion_witness_hmac_key": "w" * 32,
    }
    values.update(overrides)
    with pytest.raises((ValueError, ValidationError), match="pairwise distinct"):
        if configuration == "production":
            _production(**overrides)
        else:
            Settings(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"lifecycle_hmac_key": None},
        {"lifecycle_aead_key": None},
        {"deletion_journal_path": None},
        {"deletion_witness_path": None},
        {"deletion_witness_hmac_key": None},
    ],
)
def test_production_requires_journal_authority_while_lifecycle_is_disabled(
    overrides: dict[str, object],
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        _production(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"lifecycle_hmac_key": "short"},
        {"lifecycle_aead_key": "short"},
        {"deletion_journal_path": None},
        {"deletion_witness_path": None},
        {"deletion_witness_hmac_key": "short"},
    ],
)
def test_enabled_account_lifecycle_fails_closed_without_its_journal_authorities(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "account_lifecycle_enabled": True,
        "lifecycle_hmac_key": "h" * 32,
        "lifecycle_aead_key": "a" * 32,
        "deletion_journal_path": "/deletion-journal",
        "deletion_witness_path": "/deletion-head-witness",
        "deletion_witness_hmac_key": "w" * 32,
    }
    values.update(overrides)
    with pytest.raises((ValueError, ValidationError)):
        Settings(**values)


def test_lifecycle_enabled_search_admin_settings_do_not_require_clerk_provider_credentials() -> (
    None
):
    settings = Settings(
        database_url=(
            "postgresql+asyncpg://connectmd_projection_admin:"
            "search-admin-test-password@postgres/connectmd"
        ),
        meilisearch_url="http://meilisearch:7700",
        meilisearch_api_key="search-admin-test-key",
        account_lifecycle_enabled=True,
        lifecycle_hmac_key="h" * 32,
        lifecycle_aead_key="a" * 32,
        deletion_journal_path="/deletion-journal",
        deletion_witness_path="/deletion-head-witness",
        deletion_witness_hmac_key="w" * 32,
    )

    settings.require_database_role_configuration(PROJECTION_ADMIN_DATABASE_ROLE)
    assert settings.clerk_backend_secret is None
    assert settings.clerk_backend_base_url is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"clerk_backend_secret": None},
        {"clerk_backend_secret": "change-this-clerk-backend-secret-value"},
        {"clerk_backend_base_url": None},
        {"clerk_backend_base_url": "https://example.com"},
        {"clerk_backend_base_url": "https://attacker.example.test"},
        {"clerk_backend_base_url": "https://api.clerk.com.attacker.example"},
        {"clerk_backend_base_url": "https://api.clerk.com/tenant"},
    ],
)
def test_clerk_backend_configuration_rejects_placeholder_or_non_clerk_backend(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "account_lifecycle_enabled": True,
        "lifecycle_hmac_key": "h" * 32,
        "lifecycle_aead_key": "a" * 32,
        "deletion_journal_path": "/deletion-journal",
        "deletion_witness_path": "/deletion-head-witness",
        "deletion_witness_hmac_key": "w" * 32,
        "clerk_backend_secret": "b" * 32,
        "clerk_backend_base_url": "https://api.clerk.com",
    }
    values.update(overrides)

    settings = Settings(**values)

    with pytest.raises(ValueError, match="CLERK_BACKEND"):
        settings.require_clerk_backend_configuration()


def test_production_lifecycle_requires_canonical_clerk_backend_origin() -> None:
    settings = _production(
        account_lifecycle_enabled=True,
        clerk_backend_secret="b" * 32,
        clerk_backend_base_url="https://clerk.example.test",
    )

    with pytest.raises((ValueError, ValidationError), match="CLERK_BACKEND_BASE_URL"):
        settings.require_clerk_backend_configuration()


@pytest.mark.parametrize(
    "witness_path",
    [
        "/deletion-journal",
        "/deletion-journal/witness",
        "/",
    ],
)
def test_deletion_witness_path_must_be_independent_from_journal(witness_path: str) -> None:
    with pytest.raises((ValueError, ValidationError), match="independent"):
        _production(deletion_witness_path=witness_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "postgresql+asyncpg://connectmd:connectmd@postgres/connectmd"},
        {"api_key_pepper": "change-this-to-a-unique-hex-secret"},
        {
            "meilisearch_url": "http://meilisearch:7700",
            "meilisearch_api_key": "change-this-to-a-unique-hex-secret",
        },
    ],
)
def test_production_rejects_default_or_placeholder_credentials(
    overrides: dict[str, object],
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        _production(**overrides)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"clerk_jwks_url": ("https://example.clerk.accounts.dev/.well-known/jwks.json")},
            "JWKS.*placeholder",
        ),
        ({"clerk_issuer": "https://example.clerk.accounts.dev"}, "issuer.*placeholder"),
        ({"public_base_url": "https://connectmd.example.com"}, "PUBLIC_BASE_URL.*placeholder"),
    ],
)
def test_production_rejects_committed_example_auth_and_public_url_placeholders(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises((ValueError, ValidationError), match=message):
        _production(**overrides)


@pytest.mark.parametrize(
    "origin",
    [
        "http://app.example.test",
        "null",
        "*",
        "https://*.example.test",
        "https://user:password@app.example.test",
        "https://app.example.test:8443",
        "https://app.example.test/console",
        "https://app.example.test/",
        "https://app.example.test?preview=true",
        "https://app.example.test#fragment",
        "https://connectmd.example.com",
        "https://",
        "https://App.example.test",
        "https://app.example.test.",
        "https://app%2eexample.test",
        "https://app\x00.example.test",
        "https://app .example.test",
        "https://127.000.000.001",
        "https://0x7f.0.0.1",
        "https://[2001:0db8:0000:0000:0000:0000:0000:0001]",
        "https://[fe80::1%25eth0]",
    ],
)
def test_production_rejects_noncanonical_cors_origins(origin: str) -> None:
    with pytest.raises((ValueError, ValidationError), match="canonical explicit HTTPS origins"):
        _production(cors_origins=[origin])


def test_production_rejects_cors_origins_with_an_explicit_default_port() -> None:
    with pytest.raises((ValueError, ValidationError), match="canonical explicit HTTPS origins"):
        _production(cors_origins=["https://app.example.test:443"])


def test_production_rejects_duplicate_exact_cors_origins() -> None:
    with pytest.raises((ValueError, ValidationError), match="duplicate origins"):
        _production(cors_origins=["https://app.example.test", "https://app.example.test"])


def test_production_rejects_canonical_equivalent_cors_origins() -> None:
    with pytest.raises((ValueError, ValidationError), match="duplicate origins"):
        _production(cors_origins=["https://app.example.test", "https://app.example.test:443"])


def test_production_rejects_a_unicode_idn_cors_origin() -> None:
    with pytest.raises((ValueError, ValidationError), match="canonical explicit HTTPS origins"):
        _production(cors_origins=["https://bücher.example"])


def test_production_rejects_unicode_and_punycode_equivalent_cors_origins() -> None:
    with pytest.raises((ValueError, ValidationError), match="duplicate origins"):
        _production(cors_origins=["https://xn--bcher-kva.example", "https://bücher.example"])


@pytest.mark.parametrize(
    "jwks_url",
    [
        "https://user:password@clerk.example.test/.well-known/jwks.json",
        "https://clerk.example.test:443/.well-known/jwks.json",
        "https://clerk.example.test/.well-known/jwks.json?version=1",
        "https://clerk.example.test/.well-known/jwks.json#fragment",
        "https://Clerk.example.test/.well-known/jwks.json",
        "https://clerk.example.test./.well-known/jwks.json",
        "https://clerk.example.test",
        "https://clerk.example.test/",
        "https://clerk.example.test//.well-known/jwks.json",
        "https://clerk.example.test/.well-known/../attacker",
        "https://clerk.example.test/.well-known/./jwks.json",
        r"https://clerk.example.test/.well-known\attacker",
        "https://clerk.example.test/.well-known/jwks.json/",
    ],
)
def test_production_rejects_noncanonical_clerk_jwks_urls(jwks_url: str) -> None:
    with pytest.raises((ValueError, ValidationError), match="JWKS.*canonical HTTPS URL"):
        _production(clerk_jwks_url=jwks_url)


@pytest.mark.parametrize(
    "issuer",
    [
        "https://user:password@clerk.example.test",
        "https://clerk.example.test/issuer",
        "https://clerk.example.test?version=1",
        "https://clerk.example.test#fragment",
        "https://example%2eclerk.accounts.dev",
    ],
)
def test_production_rejects_noncanonical_clerk_issuer(issuer: str) -> None:
    with pytest.raises((ValueError, ValidationError), match="issuer"):
        _production(clerk_issuer=issuer)


@pytest.mark.parametrize(
    "public_base_url",
    [
        "https://user:password@connect.example.test",
        "https://connect.example.test/path",
        "https://connect.example.test?preview=true",
        "https://connect.example.test#fragment",
    ],
)
def test_production_rejects_noncanonical_public_base_url(public_base_url: str) -> None:
    with pytest.raises((ValueError, ValidationError), match="PUBLIC_BASE_URL"):
        _production(public_base_url=public_base_url)


def test_production_allows_public_base_url_with_implicit_root_path() -> None:
    settings = _production(public_base_url="https://connect.example.test/")

    assert str(settings.public_base_url) == "https://connect.example.test/"


@pytest.mark.parametrize(
    "authorized_party",
    [
        "http://connect.example.test",
        "https://user:password@connect.example.test",
        "https://connect.example.test/path",
        "https://connect.example.test?preview=true",
        "https://connect.example.test#fragment",
        "https://connect.example.test:443",
        "https://",
    ],
)
def test_production_rejects_noncanonical_clerk_authorized_parties(authorized_party: str) -> None:
    with pytest.raises((ValueError, ValidationError), match="authorized parties"):
        _production(clerk_authorized_parties=[authorized_party])


def test_production_requires_public_origin_in_clerk_authorized_parties() -> None:
    with pytest.raises(
        (ValueError, ValidationError), match="must include CONNECTMD_PUBLIC_BASE_URL"
    ):
        _production(clerk_authorized_parties=["https://other.example.test"])


def test_production_allows_additional_clerk_authorized_parties() -> None:
    settings = _production(
        clerk_authorized_parties=[
            "https://connect.example.test",
            "https://admin.example.test",
        ]
    )

    assert settings.clerk_authorized_parties == [
        "https://connect.example.test",
        "https://admin.example.test",
    ]


def test_production_allows_public_only_authentication() -> None:
    settings = _production(
        clerk_jwks_url="",
        clerk_issuer="",
        clerk_audience="",
        clerk_authorized_parties=[],
    )

    settings.require_api_runtime_configuration()
    assert settings.clerk_jwks_url is None
    assert settings.clerk_authorized_parties == []


def test_production_public_only_mode_rejects_lifecycle_enablement() -> None:
    with pytest.raises((ValueError, ValidationError), match="lifecycle requires Clerk"):
        _production(
            clerk_jwks_url=None,
            clerk_issuer=None,
            clerk_audience=None,
            clerk_authorized_parties=[],
            account_lifecycle_enabled=True,
        )


def test_production_rejects_a_lone_clerk_audience() -> None:
    with pytest.raises((ValueError, ValidationError), match="requires a JWKS URL"):
        _production(
            clerk_jwks_url=None,
            clerk_issuer=None,
            clerk_audience="connectmd-api",
            clerk_authorized_parties=[],
        )


def test_production_allows_empty_or_canonical_explicit_cors_origins() -> None:
    assert _production(cors_origins=[]).cors_origins == []
    assert _production(
        cors_origins=[
            "https://app.example.test",
            "https://admin.example.test",
            "https://xn--bcher-kva.example",
        ]
    ).cors_origins == [
        "https://app.example.test",
        "https://admin.example.test",
        "https://xn--bcher-kva.example",
    ]


def test_production_allows_canonical_security_urls_with_jwks_path_and_ip_hosts() -> None:
    settings = _production(
        clerk_jwks_url="https://clerk.example.test/.well-known/jwks.json",
        clerk_issuer="https://xn--bcher-kva.example",
        clerk_authorized_parties=["https://127.0.0.1", "https://[2001:db8::1]"],
        cors_origins=[
            "https://xn--bcher-kva.example",
            "https://127.0.0.1",
            "https://[2001:db8::1]",
        ],
        public_base_url="https://[2001:db8::1]",
    )

    assert settings.is_production


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "clerk_jwks_url",
            HttpUrl("https://clerk.example.test/.well-known/jwks.json"),
        ),
        ("public_base_url", HttpUrl("https://connect.example.test")),
    ],
)
def test_production_rejects_preparsed_security_url_inputs(field: str, value: HttpUrl) -> None:
    with pytest.raises((ValueError, ValidationError), match="raw canonical HTTPS"):
        _production(**{field: value})


def test_nonproduction_settings_preserve_existing_cors_passthrough() -> None:
    origins = ["http://localhost:3000", "null", "*"]

    assert Settings(cors_origins=origins).cors_origins == origins


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"clerk_jwks_url": None}, "JWKS"),
        ({"clerk_jwks_url": "http://clerk.example.test/jwks"}, "HTTPS"),
        ({"clerk_issuer": None}, "issuer"),
        ({"clerk_issuer": "http://clerk.example.test"}, "HTTPS"),
        ({"clerk_authorized_parties": []}, "authorized parties"),
        ({"public_base_url": None}, "PUBLIC_BASE_URL"),
        ({"public_base_url": "http://connect.example.test"}, "HTTPS"),
        ({"verification_reviewer_id": None}, "reviewer identity"),
        ({"verification_reviewer_role": None}, "reviewer identity"),
        ({"post_moderator_id": None}, "moderator identity"),
        ({"post_moderator_role": None}, "moderator identity"),
        ({"appeal_reviewer_id": None}, "independent reviewer identity"),
        ({"appeal_reviewer_role": None}, "independent reviewer identity"),
        ({"verification_reviewer_id": "replace-me-reviewer"}, "non-placeholder"),
        ({"post_moderator_id": "replace-me-moderator"}, "non-placeholder"),
        ({"appeal_reviewer_id": "replace-me-appeals"}, "non-placeholder"),
        ({"post_moderator_id": "reviewer:production"}, "must differ"),
        ({"appeal_reviewer_id": "moderator:production"}, "must differ"),
    ],
)
def test_production_rejects_incomplete_auth_configuration(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises((ValueError, ValidationError), match=message):
        _production(**overrides)
