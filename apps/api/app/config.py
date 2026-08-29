from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.services.database_roles import (
    API_DATABASE_ROLE,
    DatabaseRoleContractError,
    require_database_url_role,
)

_PLACEHOLDER_PREFIXES = ("change-this", "replace-me", "example")
_COMMITTED_PRODUCTION_PLACEHOLDER_HOSTS = frozenset(
    {"example.clerk.accounts.dev", "connectmd.example.com"}
)


def _is_placeholder(value: str) -> bool:
    return value.strip().lower().startswith(_PLACEHOLDER_PREFIXES)


def _has_forbidden_raw_url_characters(value: str) -> bool:
    return (
        "%" in value
        or "\\" in value
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
    )


def _looks_like_legacy_ipv4(host: str) -> bool:
    labels = host.split(".")

    def is_ipv4_like_label(label: str) -> bool:
        return label.isdecimal() or (
            label.startswith("0x")
            and len(label) > 2
            and all(character in "0123456789abcdef" for character in label[2:])
        )

    return bool(labels) and all(is_ipv4_like_label(label) for label in labels)


def _is_canonical_dns_host(host: str) -> bool:
    if not host or len(host) > 253 or host.endswith(".") or _looks_like_legacy_ipv4(host):
        return False
    for label in host.split("."):
        if (
            not 1 <= len(label) <= 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(
                "a" <= character <= "z" or character.isdecimal() or character == "-"
                for character in label
            )
        ):
            return False
        if label.startswith("xn--"):
            try:
                if label.encode("ascii").decode("idna").encode("idna").decode("ascii") != label:
                    return False
            except UnicodeError:
                return False
    return True


def _canonical_ascii_host(host: str) -> str | None:
    if not host:
        return None
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    try:
        address = ip_address(ascii_host)
    except ValueError:
        return ascii_host if _is_canonical_dns_host(ascii_host) else None
    if address.version == 4:
        return str(address)
    return f"[{address.compressed}]"


def _normalize_https_url(
    value: str,
    *,
    allow_path: bool,
    normalize_default_port: bool,
    allow_implicit_root_path: bool = False,
) -> str | None:
    if (
        _has_forbidden_raw_url_characters(value)
        or "?" in value
        or "#" in value
        or not value.startswith("https://")
    ):
        return None
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return None
    if (
        url.scheme != "https"
        or url.hostname is None
        or url.username is not None
        or url.password is not None
        or port is not None
        and (port != 443 or not normalize_default_port)
    ):
        return None
    if allow_path:
        if not url.path.startswith("/") or url.path == "/":
            return None
        if any(segment in {"", ".", ".."} for segment in url.path[1:].split("/")):
            return None
        path = url.path
    elif url.path == "":
        path = ""
    elif allow_implicit_root_path and url.path == "/":
        path = ""
    else:
        return None
    host = _canonical_ascii_host(url.hostname)
    if host is None:
        return None
    return f"https://{host}{path}"


def _canonical_https_origin(value: str, *, allow_implicit_root_path: bool = False) -> str | None:
    canonical = _normalize_https_url(
        value,
        allow_path=False,
        normalize_default_port=False,
        allow_implicit_root_path=allow_implicit_root_path,
    )
    if canonical is None or not value.isascii():
        return None
    if value == canonical:
        return canonical
    if allow_implicit_root_path and value == f"{canonical}/":
        return canonical
    return None


def _canonical_https_jwks_url(value: str) -> str | None:
    canonical = _normalize_https_url(
        value,
        allow_path=True,
        normalize_default_port=False,
    )
    return canonical if canonical is not None and value.isascii() and value == canonical else None


def _uses_committed_production_placeholder(value: str | HttpUrl) -> bool:
    try:
        host = urlsplit(str(value)).hostname
    except ValueError:
        return False
    return host is not None and host.lower().rstrip(".") in _COMMITTED_PRODUCTION_PLACEHOLDER_HOSTS


def _validate_production_cors_origins(origins: list[str]) -> None:
    canonical_origins: set[str] = set()
    for origin in origins:
        if not isinstance(origin, str):
            raise ValueError("production CORS origins must be canonical explicit HTTPS origins")
        duplicate_candidate = _normalize_https_url(
            origin,
            allow_path=False,
            normalize_default_port=True,
        )
        if duplicate_candidate is not None and duplicate_candidate in canonical_origins:
            raise ValueError("production CORS origins must not contain duplicate origins")
        canonical_origin = _canonical_https_origin(origin)
        if canonical_origin is None or _uses_committed_production_placeholder(origin):
            raise ValueError("production CORS origins must be canonical explicit HTTPS origins")
        canonical_origins.add(canonical_origin)


def _is_valid_production_reviewer(value: str | None) -> bool:
    return (
        value is not None
        and value == value.strip()
        and 1 <= len(value) <= 255
        and not _is_placeholder(value)
    )


def _is_allowed_clerk_backend_origin(url: HttpUrl, *, production: bool) -> bool:
    host = (url.host or "").lower().rstrip(".")
    if (
        url.scheme != "https"
        or url.username is not None
        or url.password is not None
        or url.port not in {None, 443}
        or url.path not in {None, "", "/"}
        or url.query is not None
        or url.fragment is not None
    ):
        return False
    if host == "api.clerk.com":
        return True
    return not production and host == "clerk.example.test"


class Settings(BaseSettings):
    """Runtime settings. Secrets are injected by the environment only."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CONNECTMD_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "connect.md API"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://connectmd:connectmd@postgres:5432/connectmd"
    storage_path: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "storage"
    )
    max_upload_bytes: int = 10 * 1024 * 1024
    max_extracted_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    # The production converter is intentionally a single serial worker. Keeping
    # the API limiter aligned prevents queued jobs from consuming their timeout.
    max_ingest_concurrency: int = Field(default=1, ge=1, le=1)
    ingest_jobs_path: Path | None = None
    ingest_timeout_seconds: int = Field(default=45, ge=5, le=120)
    max_docx_entries: int = Field(default=1000, ge=1, le=10000)
    max_docx_uncompressed_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    clerk_jwks_url: HttpUrl | None = None
    clerk_issuer: str | None = None
    clerk_audience: str | None = None
    clerk_authorized_parties: list[str] = Field(default_factory=list)
    jwks_cache_seconds: int = 300
    api_key_pepper: str | None = None
    meilisearch_url: HttpUrl | None = None
    meilisearch_api_key: str | None = None
    meilisearch_index: str = "documents"
    search_projection_worker_id: str = Field(
        default="search-projection-worker", min_length=1, max_length=128
    )
    search_projection_poll_seconds: int = Field(default=2, ge=1, le=60)
    search_projection_lease_seconds: int = Field(default=60, ge=15, le=300)
    search_projection_max_attempts: int = Field(default=8, ge=1, le=32)
    search_projection_max_backoff_seconds: int = Field(default=300, ge=1, le=3600)
    exact_search_cursor_keyring: str | None = None
    exact_search_cursor_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    cors_origins: list[str] = Field(default_factory=list)
    public_base_url: HttpUrl | None = None
    verification_reviewer_id: str | None = None
    verification_reviewer_role: Literal["recruiting_verifier"] | None = None
    recruiting_enabled: bool = False
    post_moderator_id: str | None = None
    post_moderator_role: Literal["content_moderator"] | None = None
    appeal_reviewer_id: str | None = None
    appeal_reviewer_role: Literal["appeal_reviewer"] | None = None
    post_moderation_operator_output_enabled: bool = False
    agent_outreach_direct_peer_daily_limit: int = Field(default=100, ge=1, le=10_000)
    retention_worker_id: str = "retention-service"
    account_lifecycle_enabled: bool = False
    lifecycle_hmac_key: str | None = None
    lifecycle_aead_key: str | None = None
    deletion_journal_path: Path | None = None
    deletion_witness_path: Path | None = None
    deletion_witness_hmac_key: str | None = None
    clerk_backend_secret: str | None = None
    clerk_backend_base_url: HttpUrl | None = None
    account_export_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    account_lifecycle_worker_id: str = Field(
        default="account-lifecycle-worker", min_length=1, max_length=128
    )
    account_lifecycle_poll_seconds: int = Field(default=10, ge=1, le=30)
    account_lifecycle_max_healthy_backlog: int = Field(default=10_000, ge=1, le=1_000_000)
    account_lifecycle_max_healthy_dead_letters: int = Field(default=0, ge=0, le=10_000)
    account_lifecycle_max_healthy_eligible_age_seconds: int = Field(default=600, ge=30, le=86_400)
    account_lifecycle_heartbeat_path: Path = Path("/tmp/connectmd-account-lifecycle-worker-ready")
    account_lifecycle_policy_version: str = Field(
        default="account-lifecycle-v1", min_length=1, max_length=64
    )

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def require_api_runtime_configuration(self) -> None:
        """Fail closed on dependencies required by the production API process."""
        if not self.is_production:
            return
        try:
            database_driver = make_url(self.database_url).drivername
        except ArgumentError:
            raise ValueError("production API database must use postgresql+asyncpg") from None
        if database_driver != "postgresql+asyncpg":
            raise ValueError("production API database must use postgresql+asyncpg")
        self.require_database_role_configuration(API_DATABASE_ROLE)
        if self.meilisearch_url is None:
            raise ValueError("production API requires CONNECTMD_MEILISEARCH_URL")
        if (
            not self.meilisearch_api_key
            or not self.meilisearch_api_key.strip()
            or len(self.meilisearch_api_key) < 16
            or self.meilisearch_api_key.lower().startswith(("change-this", "replace-me", "example"))
        ):
            raise ValueError(
                "production API requires a non-placeholder CONNECTMD_MEILISEARCH_API_KEY "
                "of at least 16 characters"
            )
        if not self.meilisearch_index.strip():
            raise ValueError("production API requires a non-empty CONNECTMD_MEILISEARCH_INDEX")

    def require_database_role_configuration(self, expected_role: str) -> None:
        """Bind any PostgreSQL process to its dedicated login; SQLite stays local."""
        if self.is_production:
            try:
                database_driver = make_url(self.database_url).drivername
            except ArgumentError:
                raise ValueError("production database must use postgresql+asyncpg") from None
            if database_driver != "postgresql+asyncpg":
                raise ValueError("production database must use postgresql+asyncpg")
        try:
            require_database_url_role(self.database_url, expected_role)
        except DatabaseRoleContractError:
            raise ValueError("database role is invalid") from None

    def require_clerk_backend_configuration(self) -> None:
        """Restrict the Clerk secret to the canonical Backend API origin."""
        if (
            self.clerk_backend_secret is None
            or len(self.clerk_backend_secret.encode("utf-8")) < 32
            or _is_placeholder(self.clerk_backend_secret)
        ):
            raise ValueError(
                "CONNECTMD_CLERK_BACKEND_SECRET must be non-placeholder and at least 32 bytes"
            )
        if self.clerk_backend_base_url is None or not _is_allowed_clerk_backend_origin(
            self.clerk_backend_base_url, production=self.is_production
        ):
            raise ValueError(
                "CONNECTMD_CLERK_BACKEND_BASE_URL must be the canonical Clerk Backend API origin"
            )

    @model_validator(mode="before")
    @classmethod
    def validate_raw_production_security_urls(cls, values: Any) -> Any:
        """Reject noncanonical raw URLs before Pydantic's URL coercion normalizes them."""
        if not isinstance(values, dict):
            return values
        if any(
            values.get(field) == ""
            for field in ("clerk_jwks_url", "clerk_issuer", "clerk_audience")
        ):
            values = dict(values)
            for field in ("clerk_jwks_url", "clerk_issuer", "clerk_audience"):
                if values.get(field) == "":
                    values[field] = None
        environment = values.get("environment", "development")
        if not isinstance(environment, str) or environment.lower() != "production":
            return values

        raw_jwks_url = values.get("clerk_jwks_url")
        if raw_jwks_url is not None and not isinstance(raw_jwks_url, str):
            raise ValueError("production Clerk JWKS URL must be a raw canonical HTTPS URL")
        if isinstance(raw_jwks_url, str):
            if _canonical_https_jwks_url(raw_jwks_url) is None:
                raise ValueError("production Clerk JWKS URL must be a canonical HTTPS URL")
            if _uses_committed_production_placeholder(raw_jwks_url):
                raise ValueError("production Clerk JWKS URL must not use a committed placeholder")

        raw_issuer = values.get("clerk_issuer")
        if isinstance(raw_issuer, str):
            if _canonical_https_origin(raw_issuer) is None:
                raise ValueError("production Clerk issuer must be a canonical HTTPS origin")
            if _uses_committed_production_placeholder(raw_issuer):
                raise ValueError("production Clerk issuer must not use a committed placeholder")

        raw_authorized_parties = values.get("clerk_authorized_parties")
        if isinstance(raw_authorized_parties, list):
            for party in raw_authorized_parties:
                if (
                    not isinstance(party, str)
                    or _canonical_https_origin(party) is None
                    or _uses_committed_production_placeholder(party)
                ):
                    raise ValueError(
                        "production Clerk authorized parties must be canonical explicit HTTPS origins"
                    )

        raw_cors_origins = values.get("cors_origins")
        if isinstance(raw_cors_origins, list):
            _validate_production_cors_origins(raw_cors_origins)

        raw_public_base_url = values.get("public_base_url")
        if raw_public_base_url is not None and not isinstance(raw_public_base_url, str):
            raise ValueError(
                "production CONNECTMD_PUBLIC_BASE_URL must be a raw canonical HTTPS origin"
            )
        if isinstance(raw_public_base_url, str):
            canonical_public_origin = _canonical_https_origin(
                raw_public_base_url, allow_implicit_root_path=True
            )
            if canonical_public_origin is None:
                raise ValueError(
                    "production CONNECTMD_PUBLIC_BASE_URL must be a canonical HTTPS origin"
                )
            if _uses_committed_production_placeholder(raw_public_base_url):
                raise ValueError(
                    "production CONNECTMD_PUBLIC_BASE_URL must not use a committed placeholder"
                )
            if (
                isinstance(raw_authorized_parties, list)
                and raw_authorized_parties
                and canonical_public_origin not in raw_authorized_parties
            ):
                raise ValueError(
                    "production Clerk authorized parties must include CONNECTMD_PUBLIC_BASE_URL"
                )
        return values

    def model_post_init(self, __context: object) -> None:
        if self.deletion_journal_path is not None and self.deletion_witness_path is not None:
            journal_path = self.deletion_journal_path.resolve(strict=False)
            witness_path = self.deletion_witness_path.resolve(strict=False)
            if (
                journal_path == witness_path
                or journal_path.is_relative_to(witness_path)
                or witness_path.is_relative_to(journal_path)
            ):
                raise ValueError(
                    "deletion witness path must be independent from deletion journal path"
                )
        if self.account_lifecycle_enabled:
            for name, value in (
                ("CONNECTMD_LIFECYCLE_HMAC_KEY", self.lifecycle_hmac_key),
                ("CONNECTMD_LIFECYCLE_AEAD_KEY", self.lifecycle_aead_key),
            ):
                if value is None or len(value.encode("utf-8")) < 32:
                    raise ValueError(
                        f"{name} must contain at least 32 bytes when lifecycle is enabled"
                    )
            if self.deletion_journal_path is None:
                raise ValueError(
                    "CONNECTMD_DELETION_JOURNAL_PATH is required when lifecycle is enabled"
                )
            if self.deletion_witness_path is None:
                raise ValueError(
                    "CONNECTMD_DELETION_WITNESS_PATH is required when lifecycle is enabled"
                )
            if (
                self.deletion_witness_hmac_key is None
                or len(self.deletion_witness_hmac_key.encode("utf-8")) < 32
            ):
                raise ValueError(
                    "CONNECTMD_DELETION_WITNESS_HMAC_KEY must contain at least 32 bytes "
                    "when lifecycle is enabled"
                )
            if (
                len(
                    {
                        self.lifecycle_hmac_key,
                        self.lifecycle_aead_key,
                        self.deletion_witness_hmac_key,
                    }
                )
                != 3
            ):
                raise ValueError("lifecycle authority keys must be pairwise distinct")
        if self.is_production:
            for name, value in (
                ("CONNECTMD_LIFECYCLE_HMAC_KEY", self.lifecycle_hmac_key),
                ("CONNECTMD_LIFECYCLE_AEAD_KEY", self.lifecycle_aead_key),
            ):
                if value is None or len(value.encode("utf-8")) < 32:
                    raise ValueError(f"{name} must contain at least 32 bytes in production")
            if self.deletion_journal_path is None:
                raise ValueError("production requires CONNECTMD_DELETION_JOURNAL_PATH")
            if self.deletion_witness_path is None:
                raise ValueError("production requires CONNECTMD_DELETION_WITNESS_PATH")
            if (
                self.deletion_witness_hmac_key is None
                or len(self.deletion_witness_hmac_key.encode("utf-8")) < 32
            ):
                raise ValueError(
                    "CONNECTMD_DELETION_WITNESS_HMAC_KEY must contain at least 32 bytes "
                    "in production"
                )
            if (
                len(
                    {
                        self.lifecycle_hmac_key,
                        self.lifecycle_aead_key,
                        self.deletion_witness_hmac_key,
                    }
                )
                != 3
            ):
                raise ValueError("lifecycle authority keys must be pairwise distinct")
            if (
                not self.api_key_pepper
                or len(self.api_key_pepper) < 32
                or self.api_key_pepper.lower().startswith(("change-this", "replace-me", "example"))
            ):
                raise ValueError(
                    "production agent API keys require CONNECTMD_API_KEY_PEPPER of at least 32 characters"
                )
            if (
                "change-this" in self.database_url.lower()
                or "://connectmd:connectmd@" in self.database_url
            ):
                raise ValueError(
                    "production database credentials must not use defaults or placeholders"
                )
            if self.ingest_jobs_path is None:
                raise ValueError("production binary ingestion requires CONNECTMD_INGEST_JOBS_PATH")
            clerk_configured = any(
                (
                    self.clerk_jwks_url is not None,
                    bool(self.clerk_issuer),
                    bool(self.clerk_audience),
                    bool(self.clerk_authorized_parties),
                )
            )
            if clerk_configured:
                if self.clerk_jwks_url is None:
                    raise ValueError("production Clerk configuration requires a JWKS URL")
                if _canonical_https_jwks_url(str(self.clerk_jwks_url)) is None:
                    raise ValueError("production Clerk JWKS URL must be a canonical HTTPS URL")
                if _uses_committed_production_placeholder(self.clerk_jwks_url):
                    raise ValueError(
                        "production Clerk JWKS URL must not use a committed placeholder"
                    )
                if not self.clerk_issuer:
                    raise ValueError("production Clerk configuration requires an issuer")
                if _canonical_https_origin(self.clerk_issuer) is None:
                    raise ValueError("production Clerk issuer must be a canonical HTTPS origin")
                if _uses_committed_production_placeholder(self.clerk_issuer):
                    raise ValueError("production Clerk issuer must not use a committed placeholder")
                if not self.clerk_authorized_parties:
                    raise ValueError("production Clerk configuration requires authorized parties")
                if any(
                    _canonical_https_origin(party) is None
                    or _uses_committed_production_placeholder(party)
                    for party in self.clerk_authorized_parties
                ):
                    raise ValueError(
                        "production Clerk authorized parties must be canonical explicit HTTPS origins"
                    )
            elif self.account_lifecycle_enabled:
                raise ValueError("production account lifecycle requires Clerk authentication")
            _validate_production_cors_origins(self.cors_origins)
            if self.public_base_url is None:
                raise ValueError(
                    "production CONNECTMD_PUBLIC_BASE_URL is required and must use HTTPS"
                )
            if (
                _canonical_https_origin(str(self.public_base_url), allow_implicit_root_path=True)
                is None
            ):
                raise ValueError(
                    "production CONNECTMD_PUBLIC_BASE_URL must be a canonical HTTPS origin"
                )
            if _uses_committed_production_placeholder(self.public_base_url):
                raise ValueError(
                    "production CONNECTMD_PUBLIC_BASE_URL must not use a committed placeholder"
                )
            if (
                not _is_valid_production_reviewer(self.verification_reviewer_id)
                or self.verification_reviewer_role is None
            ):
                raise ValueError(
                    "production verification decisions require a non-placeholder "
                    "pre-provisioned reviewer identity and role"
                )
            if (
                not _is_valid_production_reviewer(self.post_moderator_id)
                or self.post_moderator_role is None
            ):
                raise ValueError(
                    "production post moderation requires a non-placeholder "
                    "pre-provisioned moderator identity and role"
                )
            if (
                not _is_valid_production_reviewer(self.appeal_reviewer_id)
                or self.appeal_reviewer_role is None
            ):
                raise ValueError(
                    "production post appeals require a non-placeholder pre-provisioned "
                    "independent reviewer identity and role"
                )
            if self.verification_reviewer_id == self.post_moderator_id:
                raise ValueError(
                    "production verification reviewer must differ from the post moderator"
                )
            if self.appeal_reviewer_id == self.post_moderator_id:
                raise ValueError("production post appeal reviewer must differ from the moderator")
        if self.is_production and self.meilisearch_url is not None:
            host = self.meilisearch_url.host or ""
            internal_host = host in {"localhost", "meilisearch"}
            try:
                internal_host = (
                    internal_host or ip_address(host).is_private or ip_address(host).is_loopback
                )
            except ValueError:
                pass
            if self.meilisearch_url.scheme != "https" and not internal_host:
                raise ValueError(
                    "production CONNECTMD_MEILISEARCH_URL must use HTTPS unless it is an internal host"
                )
            if (
                not self.meilisearch_api_key
                or len(self.meilisearch_api_key) < 16
                or self.meilisearch_api_key.lower().startswith(
                    ("change-this", "replace-me", "example")
                )
            ):
                raise ValueError(
                    "production Meilisearch requires a non-empty key of at least 16 characters"
                )


@lru_cache
def get_settings() -> Settings:
    return Settings()
