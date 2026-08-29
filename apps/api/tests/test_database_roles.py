from __future__ import annotations

from argparse import Namespace
from inspect import getsource
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app import cli
from app.services.backup_authority import (
    register_backup_generation as register_backup_generation_implementation,
)
from app.services.backup_authority import (
    transition_backup_generation as transition_backup_generation_implementation,
)
from app.services.database_roles import (
    _ROLE_ATTESTATION_SQL,
    API_DATABASE_ROLE,
    MIGRATOR_DATABASE_ROLE,
    PROJECTION_ADMIN_DATABASE_ROLE,
    DatabaseRoleContractError,
    require_database_role,
    require_database_role_sync,
    require_database_url_role,
)
from app.services.post_moderation_authority import (
    inspect_post_moderation_case as inspect_post_moderation_case_implementation,
)
from app.services.post_moderation_authority import (
    list_post_moderation_cases as list_post_moderation_cases_implementation,
)
from app.services.post_moderation_authority import (
    moderate_post as moderate_post_implementation,
)
from app.services.post_moderation_authority import (
    review_post_appeal as review_post_appeal_implementation,
)


def _valid_row(role: str, *, schema_owner: bool = False) -> dict[str, Any]:
    return {
        "session_login": role,
        "login": role,
        "can_login": True,
        "is_superuser": False,
        "can_create_database_role_attribute": False,
        "can_create_role": False,
        "inherits_roles": False,
        "can_replicate": False,
        "can_bypass_rls": False,
        "has_memberships": False,
        "can_connect": True,
        "can_create_database": False,
        "can_create_temp": False,
        "can_use_public_schema": True,
        "can_create_public_schema": schema_owner,
        "owns_database": False,
        "owns_public_schema": schema_owner,
        "has_public_objects": True,
        "has_effective_role_owned_public_objects": schema_owner,
        "has_foreign_public_objects": not schema_owner,
    }


class _Dialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _Bind:
    def __init__(self, name: str) -> None:
        self.dialect = _Dialect(name)


class _MappingResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> _MappingResult:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.row


class _AsyncSession:
    def __init__(
        self,
        row: dict[str, Any] | None,
        *,
        dialect: str = "postgresql",
        error: Exception | None = None,
    ) -> None:
        self.bind = _Bind(dialect)
        self.row = row
        self.error = error
        self.execute_calls = 0

    def get_bind(self) -> _Bind:
        return self.bind

    async def execute(self, _statement: object) -> _MappingResult:
        self.execute_calls += 1
        if self.error is not None:
            raise self.error
        return _MappingResult(self.row)


class _Connection:
    def __init__(self, row: dict[str, Any] | None, *, dialect: str = "postgresql") -> None:
        self.dialect = _Dialect(dialect)
        self.row = row
        self.execute_calls = 0

    def execute(self, _statement: object) -> _MappingResult:
        self.execute_calls += 1
        return _MappingResult(self.row)


def test_database_url_role_check_is_postgresql_only_and_sanitized() -> None:
    require_database_url_role("sqlite+aiosqlite:///local.db", API_DATABASE_ROLE)
    require_database_url_role(
        "postgresql+asyncpg://connectmd_api:secret@postgres/connectmd", API_DATABASE_ROLE
    )

    with pytest.raises(DatabaseRoleContractError) as exc_info:
        require_database_url_role(
            "postgresql+asyncpg://connectmd:do-not-render@postgres/connectmd",
            API_DATABASE_ROLE,
        )

    assert str(exc_info.value) == "database role contract is not satisfied"
    assert "do-not-render" not in str(exc_info.value)


def test_attestation_sql_uses_non_keyword_role_alias_and_explicit_object_sets() -> None:
    statement = str(_ROLE_ATTESTATION_SQL)

    assert "current_role" not in statement
    assert "session_user AS session_login" in statement
    assert "current_user AS login" in statement
    assert "FROM pg_roles effective_role" in statement
    assert "membership.member = effective_role.oid" in statement
    assert "membership.roleid = effective_role.oid" in statement
    assert "AS has_public_objects" in statement
    assert "AS has_effective_role_owned_public_objects" in statement
    assert "AS has_foreign_public_objects" in statement


async def test_non_postgresql_attestation_is_a_noop() -> None:
    session = _AsyncSession(None, dialect="sqlite")

    await require_database_role(session, API_DATABASE_ROLE)  # type: ignore[arg-type]

    assert session.execute_calls == 0


async def test_runtime_and_migrator_contracts_accept_exact_authority() -> None:
    runtime = _AsyncSession(_valid_row(API_DATABASE_ROLE))
    migrator = _AsyncSession(_valid_row(MIGRATOR_DATABASE_ROLE, schema_owner=True))

    await require_database_role(runtime, API_DATABASE_ROLE)  # type: ignore[arg-type]
    await require_database_role(  # type: ignore[arg-type]
        migrator, MIGRATOR_DATABASE_ROLE, require_schema_owner=True
    )


async def test_migrator_contract_accepts_a_fresh_empty_public_schema() -> None:
    row = _valid_row(MIGRATOR_DATABASE_ROLE, schema_owner=True)
    row.update(
        has_public_objects=False,
        has_effective_role_owned_public_objects=False,
        has_foreign_public_objects=False,
    )

    await require_database_role(  # type: ignore[arg-type]
        _AsyncSession(row), MIGRATOR_DATABASE_ROLE, require_schema_owner=True
    )


@pytest.mark.parametrize(
    "missing_owner_capability",
    [
        "can_create_public_schema",
        "owns_public_schema",
    ],
)
async def test_migrator_contract_requires_each_owner_capability(
    missing_owner_capability: str,
) -> None:
    row = _valid_row(MIGRATOR_DATABASE_ROLE, schema_owner=True)
    row[missing_owner_capability] = False

    with pytest.raises(DatabaseRoleContractError, match="role contract"):
        await require_database_role(  # type: ignore[arg-type]
            _AsyncSession(row), MIGRATOR_DATABASE_ROLE, require_schema_owner=True
        )


async def test_migrator_contract_rejects_mixed_public_object_ownership() -> None:
    row = _valid_row(MIGRATOR_DATABASE_ROLE, schema_owner=True)
    row["has_foreign_public_objects"] = True

    with pytest.raises(DatabaseRoleContractError, match="role contract"):
        await require_database_role(  # type: ignore[arg-type]
            _AsyncSession(row), MIGRATOR_DATABASE_ROLE, require_schema_owner=True
        )


@pytest.mark.parametrize(
    ("has_public_objects", "owns_objects", "has_foreign_objects"),
    [
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ],
)
async def test_migrator_contract_rejects_inconsistent_public_object_sets(
    has_public_objects: bool,
    owns_objects: bool,
    has_foreign_objects: bool,
) -> None:
    row = _valid_row(MIGRATOR_DATABASE_ROLE, schema_owner=True)
    row.update(
        has_public_objects=has_public_objects,
        has_effective_role_owned_public_objects=owns_objects,
        has_foreign_public_objects=has_foreign_objects,
    )

    with pytest.raises(DatabaseRoleContractError, match="role contract"):
        await require_database_role(  # type: ignore[arg-type]
            _AsyncSession(row), MIGRATOR_DATABASE_ROLE, require_schema_owner=True
        )


@pytest.mark.parametrize("forbidden_database_authority", ["owns_database", "can_create_temp"])
async def test_migrator_rejects_database_owner_and_inherent_temporary_authority(
    forbidden_database_authority: str,
) -> None:
    row = _valid_row(MIGRATOR_DATABASE_ROLE, schema_owner=True)
    row[forbidden_database_authority] = True

    with pytest.raises(DatabaseRoleContractError, match="role contract"):
        await require_database_role(  # type: ignore[arg-type]
            _AsyncSession(row), MIGRATOR_DATABASE_ROLE, require_schema_owner=True
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_login", "connectmd"),
        ("login", "connectmd"),
        ("can_login", False),
        ("is_superuser", True),
        ("can_create_database_role_attribute", True),
        ("can_create_role", True),
        ("inherits_roles", True),
        ("can_replicate", True),
        ("can_bypass_rls", True),
        ("has_memberships", True),
        ("can_connect", False),
        ("can_create_database", True),
        ("can_create_temp", True),
        ("can_use_public_schema", False),
        ("can_create_public_schema", True),
        ("owns_database", True),
        ("owns_public_schema", True),
        ("has_effective_role_owned_public_objects", True),
    ],
)
async def test_runtime_contract_rejects_each_privilege_expansion(field: str, value: object) -> None:
    row = _valid_row(API_DATABASE_ROLE)
    row[field] = value

    with pytest.raises(DatabaseRoleContractError, match="role contract"):
        await require_database_role(  # type: ignore[arg-type]
            _AsyncSession(row), API_DATABASE_ROLE
        )


async def test_runtime_contract_rejects_public_object_ownership_even_without_foreign_objects() -> (
    None
):
    row = _valid_row(API_DATABASE_ROLE)
    row["has_effective_role_owned_public_objects"] = True
    row["has_foreign_public_objects"] = False

    with pytest.raises(DatabaseRoleContractError, match="role contract"):
        await require_database_role(  # type: ignore[arg-type]
            _AsyncSession(row), API_DATABASE_ROLE
        )


async def test_attestation_failure_does_not_expose_database_error() -> None:
    session = _AsyncSession(
        None, error=SQLAlchemyError("postgresql://connectmd_api:do-not-render@postgres/connectmd")
    )

    with pytest.raises(DatabaseRoleContractError) as exc_info:
        await require_database_role(session, API_DATABASE_ROLE)  # type: ignore[arg-type]

    assert str(exc_info.value) == "database role contract is not satisfied"
    assert "do-not-render" not in str(exc_info.value)


def test_synchronous_migrator_attestation_and_sqlite_noop() -> None:
    migrator = _Connection(_valid_row(MIGRATOR_DATABASE_ROLE, schema_owner=True))
    sqlite = _Connection(None, dialect="sqlite")

    require_database_role_sync(  # type: ignore[arg-type]
        migrator, MIGRATOR_DATABASE_ROLE, require_schema_owner=True
    )
    require_database_role_sync(sqlite, MIGRATOR_DATABASE_ROLE)  # type: ignore[arg-type]

    assert migrator.execute_calls == 1
    assert sqlite.execute_calls == 0


def test_exact_search_admin_settings_keep_production_authority_scoped(tmp_path) -> None:
    settings = cli.ExactSearchAdminSettings(
        environment="production",
        database_url=(
            "postgresql+asyncpg://connectmd_projection_admin:secret@postgres:5432/connectmd"
        ),
        storage_path=tmp_path,
        exact_search_cursor_keyring=(
            '[{"kid":"v1","secret":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}]'
        ),
    )

    settings.require_database_role_configuration(PROJECTION_ADMIN_DATABASE_ROLE)
    assert set(type(settings).model_fields) == {
        "environment",
        "database_url",
        "storage_path",
        "exact_search_cursor_keyring",
        "exact_search_cursor_ttl_seconds",
    }


@pytest.mark.parametrize(
    ("command", "args", "settings_factory"),
    [
        (cli.rebuild_search, (), "get_settings"),
        (
            cli.run_taxonomy,
            (Namespace(taxonomy_action="verify", if_required=False),),
            "get_settings",
        ),
        (
            cli.run_exact_search,
            (Namespace(exact_search_action="verify", if_required=False),),
            "get_exact_search_admin_settings",
        ),
    ],
)
async def test_projection_admin_commands_reject_the_wrong_configured_role_before_work(
    monkeypatch, command, args: tuple[object, ...], settings_factory: str
) -> None:
    seen: list[str] = []

    class _Settings:
        def require_database_role_configuration(self, expected_role: str) -> None:
            seen.append(expected_role)
            raise ValueError("production database role is invalid")

    monkeypatch.setattr(cli, settings_factory, lambda: _Settings())

    with pytest.raises(ValueError, match="database role is invalid"):
        await command(*args)

    assert seen == ["connectmd_projection_admin"]


@pytest.mark.parametrize(
    ("command", "role_constant"),
    [
        (register_backup_generation_implementation, "API_DATABASE_ROLE"),
        (transition_backup_generation_implementation, "API_DATABASE_ROLE"),
        (cli.rebuild_search, "PROJECTION_ADMIN_DATABASE_ROLE"),
        (cli.run_taxonomy, "PROJECTION_ADMIN_DATABASE_ROLE"),
        (cli.run_exact_search, "PROJECTION_ADMIN_DATABASE_ROLE"),
        (cli.run_deletion_journal, "API_DATABASE_ROLE"),
        (cli.apply_verification_transition, "API_DATABASE_ROLE"),
        (cli.run_retention, "API_DATABASE_ROLE"),
        (cli.run_account_erasure, "ACCOUNT_ERASURE_DATABASE_ROLE"),
        (cli.create_retention_hold, "API_DATABASE_ROLE"),
        (cli.release_retention_hold, "API_DATABASE_ROLE"),
        (cli.transition_agent_identity, "API_DATABASE_ROLE"),
        (cli.transition_agent_mandate, "API_DATABASE_ROLE"),
        (moderate_post_implementation, "API_DATABASE_ROLE"),
        (review_post_appeal_implementation, "API_DATABASE_ROLE"),
        (list_post_moderation_cases_implementation, "API_DATABASE_ROLE"),
        (inspect_post_moderation_case_implementation, "API_DATABASE_ROLE"),
    ],
)
def test_every_database_cli_implementation_has_dsn_and_live_role_gates(
    command: object, role_constant: str
) -> None:
    source = getsource(command)

    assert f"require_database_role_configuration({role_constant})" in source
    assert f"require_database_role(session, {role_constant})" in source


@pytest.mark.parametrize(
    ("wrapper", "delegate_name"),
    [
        (cli.register_backup_generation, "register_backup_generation_with_settings"),
        (cli.transition_backup_generation, "transition_backup_generation_with_settings"),
        (cli.moderate_post, "moderate_post_with_settings"),
        (cli.review_post_appeal, "review_post_appeal_with_settings"),
        (cli.list_post_moderation_cases, "list_post_moderation_cases_with_settings"),
        (cli.inspect_post_moderation_case, "inspect_post_moderation_case_with_settings"),
    ],
)
def test_delegated_database_cli_wrappers_keep_runtime_settings_mapping(
    wrapper: object, delegate_name: str
) -> None:
    source = getsource(wrapper)

    assert f"await {delegate_name}(" in source
    assert "get_settings" in source


async def test_retention_and_erasure_cli_reject_wrong_roles_before_executor_work(
    monkeypatch,
) -> None:
    seen: list[tuple[str, str]] = []

    class _Settings:
        account_lifecycle_enabled = True

        def require_clerk_backend_configuration(self) -> None:
            return None

        def require_database_role_configuration(self, expected_role: str) -> None:
            seen.append(("configured", expected_role))
            raise ValueError("production database role is invalid")

    monkeypatch.setattr(cli, "get_settings", lambda: _Settings())

    with pytest.raises(ValueError, match="database role is invalid"):
        await cli.run_retention(Namespace(limit=1))
    with pytest.raises(ValueError, match="database role is invalid"):
        await cli.run_account_erasure(Namespace(limit=1))

    assert seen == [
        ("configured", API_DATABASE_ROLE),
        ("configured", "connectmd_account_erasure"),
    ]
