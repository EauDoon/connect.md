"""Fail-closed PostgreSQL process-role attestation.

Role provisioning and grants belong to the deployment control plane.  This
module only proves that a connected process received the one expected login
without inherited or DDL-capable authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, RowMapping, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

API_DATABASE_ROLE = "connectmd_api"
MIGRATOR_DATABASE_ROLE = "connectmd_migrator"
SEARCH_PROJECTION_DATABASE_ROLE = "connectmd_search_projection"
ACCOUNT_ERASURE_DATABASE_ROLE = "connectmd_account_erasure"
PROJECTION_ADMIN_DATABASE_ROLE = "connectmd_projection_admin"


class DatabaseRoleContractError(RuntimeError):
    """The effective database authority does not match its process contract."""


_ROLE_ATTESTATION_SQL = text(
    """
    SELECT
        session_user AS session_login,
        current_user AS login,
        effective_role.rolcanlogin AS can_login,
        effective_role.rolsuper AS is_superuser,
        effective_role.rolcreatedb AS can_create_database_role_attribute,
        effective_role.rolcreaterole AS can_create_role,
        effective_role.rolinherit AS inherits_roles,
        effective_role.rolreplication AS can_replicate,
        effective_role.rolbypassrls AS can_bypass_rls,
        EXISTS (
            SELECT 1
            FROM pg_auth_members membership
            WHERE membership.member = effective_role.oid
               OR membership.roleid = effective_role.oid
        ) AS has_memberships,
        has_database_privilege(current_user, current_database(), 'CONNECT') AS can_connect,
        has_database_privilege(current_user, current_database(), 'CREATE') AS can_create_database,
        has_database_privilege(current_user, current_database(), 'TEMPORARY') AS can_create_temp,
        has_schema_privilege(current_user, 'public', 'USAGE') AS can_use_public_schema,
        has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_public_schema,
        current_database_row.datdba = effective_role.oid AS owns_database,
        public_schema.nspowner = effective_role.oid AS owns_public_schema,
        public_objects.has_public_objects,
        public_objects.has_effective_role_owned_public_objects,
        public_objects.has_foreign_public_objects
    FROM pg_roles effective_role
    JOIN pg_database current_database_row
      ON current_database_row.datname = current_database()
    JOIN pg_namespace public_schema ON public_schema.nspname = 'public'
    CROSS JOIN LATERAL (
        SELECT
            COUNT(*) > 0 AS has_public_objects,
            COALESCE(
                BOOL_OR(public_object.owner_oid = effective_role.oid),
                FALSE
            ) AS has_effective_role_owned_public_objects,
            COALESCE(
                BOOL_OR(public_object.owner_oid <> effective_role.oid),
                FALSE
            ) AS has_foreign_public_objects
        FROM (
            SELECT relation.relowner AS owner_oid
            FROM pg_class relation
            WHERE relation.relnamespace = public_schema.oid
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
            UNION ALL
            SELECT procedure.proowner AS owner_oid
            FROM pg_proc procedure
            WHERE procedure.pronamespace = public_schema.oid
            UNION ALL
            SELECT data_type.typowner AS owner_oid
            FROM pg_type data_type
            WHERE data_type.typnamespace = public_schema.oid
        ) public_object
    ) public_objects
    WHERE effective_role.rolname = current_user
    """
)


def require_database_url_role(database_url: str, expected_role: str) -> None:
    """Reject a PostgreSQL DSN that names a different process login."""
    try:
        url = make_url(database_url)
    except ArgumentError:
        raise DatabaseRoleContractError("database role contract is not satisfied") from None
    if url.get_backend_name() == "postgresql" and url.username != expected_role:
        raise DatabaseRoleContractError("database role contract is not satisfied")


def _require_attestation(
    row: RowMapping | Mapping[str, Any] | None,
    expected_role: str,
    *,
    require_schema_owner: bool,
) -> None:
    if row is None:
        raise DatabaseRoleContractError("database role contract is not satisfied")
    always_true = ("can_login", "can_connect", "can_use_public_schema")
    always_false = (
        "is_superuser",
        "can_create_database_role_attribute",
        "can_create_role",
        "inherits_roles",
        "can_replicate",
        "can_bypass_rls",
        "has_memberships",
        "can_create_database",
        "can_create_temp",
        "owns_database",
    )
    has_public_objects = row.get("has_public_objects")
    has_effective_role_owned_public_objects = row.get("has_effective_role_owned_public_objects")
    has_foreign_public_objects = row.get("has_foreign_public_objects")
    public_object_state = (
        has_public_objects,
        has_effective_role_owned_public_objects,
        has_foreign_public_objects,
    )
    public_object_state_is_boolean = all(
        value is True or value is False for value in public_object_state
    )
    public_object_state_is_consistent = public_object_state_is_boolean and (
        has_public_objects
        == (has_effective_role_owned_public_objects or has_foreign_public_objects)
    )
    public_object_ownership_is_valid = (
        has_foreign_public_objects is False
        if require_schema_owner
        else has_effective_role_owned_public_objects is False
    )
    if (
        row.get("session_login") != expected_role
        or row.get("login") != expected_role
        or any(row.get(field) is not True for field in always_true)
        or any(row.get(field) is not False for field in always_false)
        or row.get("can_create_public_schema") is not require_schema_owner
        or row.get("owns_public_schema") is not require_schema_owner
        or not public_object_state_is_consistent
        or not public_object_ownership_is_valid
    ):
        raise DatabaseRoleContractError("database role contract is not satisfied")


async def require_database_role(
    session: AsyncSession,
    expected_role: str,
    *,
    require_schema_owner: bool = False,
) -> None:
    """Attest one async PostgreSQL session; non-PostgreSQL development is unchanged."""
    if session.get_bind().dialect.name != "postgresql":
        return
    try:
        result = await session.execute(_ROLE_ATTESTATION_SQL)
        row = result.mappings().one_or_none()
    except SQLAlchemyError:
        raise DatabaseRoleContractError("database role contract is not satisfied") from None
    _require_attestation(row, expected_role, require_schema_owner=require_schema_owner)


def require_database_role_sync(
    connection: Connection,
    expected_role: str,
    *,
    require_schema_owner: bool = False,
) -> None:
    """Attest the synchronous connection Alembic uses for online migrations."""
    if connection.dialect.name != "postgresql":
        return
    try:
        row = connection.execute(_ROLE_ATTESTATION_SQL).mappings().one_or_none()
    except SQLAlchemyError:
        raise DatabaseRoleContractError("database role contract is not satisfied") from None
    _require_attestation(row, expected_role, require_schema_owner=require_schema_owner)
