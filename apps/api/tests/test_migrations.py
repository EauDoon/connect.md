from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.db import EXPECTED_ALEMBIC_HEAD


def _alembic(api_root: Path, database: Path, revision: str) -> None:
    environment = os.environ.copy()
    environment["CONNECTMD_DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=api_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def _alembic_downgrade(api_root: Path, database: Path, revision: str) -> None:
    environment = os.environ.copy()
    environment["CONNECTMD_DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", revision],
        cwd=api_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_alembic_rejects_production_sqlite_before_engine_creation(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "production-migrator.db"
    environment = os.environ.copy()
    environment.update(
        {
            "CONNECTMD_ENVIRONMENT": "production",
            "CONNECTMD_DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
            "CONNECTMD_MEILISEARCH_URL": "http://meilisearch:7700",
            "CONNECTMD_MEILISEARCH_API_KEY": "production-test-search-key",
            "CONNECTMD_API_KEY_PEPPER": "production-test-pepper-at-least-thirty-two",
            "CONNECTMD_INGEST_JOBS_PATH": str(tmp_path / "ingest"),
            "CONNECTMD_CLERK_JWKS_URL": "https://clerk.example.test/.well-known/jwks.json",
            "CONNECTMD_CLERK_ISSUER": "https://clerk.example.test",
            "CONNECTMD_CLERK_AUTHORIZED_PARTIES": '["https://connect.example.test"]',
            "CONNECTMD_PUBLIC_BASE_URL": "https://connect.example.test",
            "CONNECTMD_VERIFICATION_REVIEWER_ID": "reviewer:production",
            "CONNECTMD_VERIFICATION_REVIEWER_ROLE": "recruiting_verifier",
            "CONNECTMD_POST_MODERATOR_ID": "moderator:production",
            "CONNECTMD_POST_MODERATOR_ROLE": "content_moderator",
            "CONNECTMD_APPEAL_REVIEWER_ID": "appeals:production",
            "CONNECTMD_APPEAL_REVIEWER_ROLE": "appeal_reviewer",
            "CONNECTMD_LIFECYCLE_HMAC_KEY": "h" * 32,
            "CONNECTMD_LIFECYCLE_AEAD_KEY": "a" * 32,
            "CONNECTMD_DELETION_JOURNAL_PATH": str(tmp_path / "deletion-journal"),
            "CONNECTMD_DELETION_WITNESS_PATH": str(tmp_path / "deletion-witness"),
            "CONNECTMD_DELETION_WITNESS_HMAC_KEY": "w" * 32,
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=api_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "postgresql+asyncpg" in f"{result.stdout}\n{result.stderr}"
    assert not database.exists()


def _insert_contact_request(
    connection: sqlite3.Connection, *, request_id: str, status: str
) -> None:
    connection.execute(
        """
        INSERT INTO contact_requests (
            id, sender_owner_id, recipient_owner_id, sender_actor_id,
            sender_actor_method, target_document_id, purpose, message,
            status, created_at, retention_expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            f"sender-{request_id}",
            f"recipient-{request_id}",
            f"actor-{request_id}",
            "clerk_jwt",
            f"document-{request_id}",
            "Introduction",
            "A bounded request",
            status,
            "2026-08-05T00:00:00+00:00",
            "2027-08-05T00:00:00+00:00",
        ),
    )


def test_0018_organization_memberships_store_the_invited_profile_handle(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "organization-membership-profile-handle.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row[2]
            for row in connection.execute(
                "PRAGMA table_info('organization_memberships')"
            ).fetchall()
        }
    assert columns["member_profile_handle"].upper() == "VARCHAR(100)"


def test_0019_backfills_existing_current_documents_into_content_free_projection_tasks(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "search-projection-outbox.db"
    _alembic(api_root, database, "0018_organization_membership_profile_handle")
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO documents (
                id, kind, owner_id, public_identifier, visibility,
                current_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "document-before-0019",
                "profile",
                "owner-before-0019",
                "before-0019",
                "public",
                3,
                "2026-08-04T00:00:00Z",
                "2026-08-04T00:00:00Z",
            ),
        )
        connection.commit()
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        task = connection.execute(
            """
            SELECT document_id, version, state, attempts
            FROM search_projection_tasks
            """
        ).fetchone()
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info('search_projection_tasks')")
        }
        foreign_keys = list(
            connection.execute("PRAGMA foreign_key_list('search_projection_tasks')")
        )
    assert task == ("document-before-0019", 3, "pending", 0)
    assert columns.isdisjoint({"markdown", "content", "owner_id", "public_identifier"})
    assert foreign_keys == []


def test_0019_does_not_manage_cluster_roles() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0019_search_projection_outbox.py"
    ).read_text(encoding="utf-8")
    assert "CREATE ROLE" not in migration
    assert "ALTER ROLE" not in migration
    assert "DROP ROLE" not in migration


def test_0020_backfills_current_jobs_into_immutable_version_receipts(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "job-version-receipts.db"
    _alembic(api_root, database, "0019_search_projection_outbox")
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO organizations (
                id, owner_id, slug, name, description, website_url, visibility,
                verification_status, verification_material_version, version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "organization-before-0020",
                "owner-before-0020",
                "before-0020",
                "Before 0020",
                None,
                None,
                "private",
                "unverified",
                1,
                1,
                "2026-08-04T00:00:00Z",
                "2026-08-04T00:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO jobs (
                id, organization_id, slug, title, description, location,
                work_mode, employment_type, status, version, published_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-before-0020",
                "organization-before-0020",
                "operator",
                "Operator",
                "Operate the system.",
                "Singapore",
                "hybrid",
                "full_time",
                "published",
                4,
                "2026-08-04T01:00:00Z",
                "2026-08-04T00:00:00Z",
                "2026-08-04T01:00:00Z",
            ),
        )
        connection.commit()

    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        receipt = connection.execute(
            """
            SELECT
                job_id, version, organization_slug, organization_name, slug,
                title, description, status, published_at, created_at, updated_at,
                response_body, response_sha256
            FROM job_versions
            """
        ).fetchone()

    assert receipt == (
        "job-before-0020",
        4,
        "before-0020",
        "Before 0020",
        "operator",
        "Operator",
        "Operate the system.",
        "published",
        "2026-08-04T01:00:00Z",
        "2026-08-04T00:00:00Z",
        "2026-08-04T01:00:00Z",
        "",
        "",
    )


def test_0014_account_lifecycle_stage_one_schema_is_constrained_and_non_destructive(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "account-lifecycle.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        lifecycle_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'account_lifecycles'"
        ).fetchone()[0]
        reverification_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'account_reverification_uses'"
        ).fetchone()[0]
        lifecycle_indexes = connection.execute("PRAGMA index_list('account_lifecycles')").fetchall()
        lifecycle_columns = connection.execute("PRAGMA table_info('account_lifecycles')").fetchall()
        receipt_rate_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'account_lifecycle_receipt_rate_limits'"
        ).fetchone()[0]
        outreach_recipient_columns = connection.execute(
            "PRAGMA table_info('agent_outreach_recipient_rate_buckets')"
        ).fetchall()
        outreach_direct_peer_columns = connection.execute(
            "PRAGMA table_info('agent_outreach_direct_peer_rate_buckets')"
        ).fetchall()
        erasure_indexes = connection.execute(
            "PRAGMA index_list('account_erasure_items')"
        ).fetchall()
    assert {
        "account_lifecycles",
        "account_access_denies",
        "account_reverification_uses",
        "account_erasure_items",
        "account_backup_manifests",
        "account_backup_authority",
        "account_backup_obligations",
        "account_lifecycle_tombstones",
        "account_lifecycle_receipt_rate_limits",
        "agent_outreach_recipient_rate_buckets",
        "agent_outreach_direct_peer_rate_buckets",
    }.issubset(tables)
    assert "ck_account_lifecycles_state" in lifecycle_sql
    assert "ck_account_lifecycles_provider_state" in lifecycle_sql
    assert "ck_account_lifecycles_backup_state" in lifecycle_sql
    assert "provider_subject_ciphertext" in lifecycle_sql
    assert {
        "receipt_hmac",
        "receipt_ciphertext",
        "receipt_recovery_idempotency_hmac",
    }.issubset({row[1] for row in lifecycle_columns})
    assert "ck_account_reverification_uses_purpose" in reverification_sql
    assert "delete_receipt_recover" in reverification_sql
    assert "uq_lifecycle_receipt_rate" in receipt_rate_sql
    assert "ON DELETE CASCADE" in receipt_rate_sql
    assert [(row[1], row[5]) for row in outreach_recipient_columns] == [
        ("recipient_owner_id", 1),
        ("bucket_date", 2),
        ("request_count", 0),
        ("updated_at", 0),
    ]
    assert [(row[1], row[5]) for row in outreach_direct_peer_columns] == [
        ("direct_peer_hmac", 1),
        ("bucket_date", 2),
        ("request_count", 0),
        ("updated_at", 0),
    ]
    assert any(row[1] == "ix_account_lifecycles_subject_state" for row in lifecycle_indexes)
    assert any(row[1] == "ix_account_lifecycles_receipt_hmac" for row in lifecycle_indexes)
    assert any(row[1] == "ix_account_erasure_items_available" for row in erasure_indexes)


def test_0015_stage_two_backup_authority_and_proof_pair_constraints(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "account-lifecycle-stage-two.db"
    _alembic(api_root, database, "0014_account_lifecycle_stage_one")
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        manifest_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'account_backup_manifests'"
        ).fetchone()[0]
        authority_columns = connection.execute(
            "PRAGMA table_info('account_backup_authority')"
        ).fetchall()
        assert "ck_account_backup_manifests_proof_pair" in manifest_sql
        assert {row[1] for row in authority_columns} == {
            "id",
            "current_generation_id",
            "registered_at",
            "updated_at",
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO account_backup_manifests (
                    id, generation_id, created_at, expires_at, state,
                    db_manifest_digest, markdown_manifest_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "invalid",
                    "invalid",
                    "2026-08-01T00:00:00Z",
                    "2026-08-02T00:00:00Z",
                    "expired",
                    "a" * 64,
                    "b" * 64,
                ),
            )


def test_0003_reconciles_legacy_duplicate_pending_contact_requests(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "migration.db"
    _alembic(api_root, database, "0002_protocol_core")
    with sqlite3.connect(database) as connection:
        common = (
            "sender",
            "recipient",
            "actor",
            "clerk_jwt",
            "document",
            "Introduction",
            "A bounded request",
            "pending",
        )
        connection.executemany(
            """
            INSERT INTO contact_requests (
                id, sender_owner_id, recipient_owner_id, sender_actor_id,
                sender_actor_method, target_document_id, purpose, message,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("request-oldest", *common, "2026-08-01T00:00:00Z"),
                ("request-newer", *common, "2026-08-02T00:00:00Z"),
            ],
        )
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT id, status, decision_actor_id, decided_at
            FROM contact_requests
            ORDER BY created_at ASC
            """
        ).fetchall()
        indexes = connection.execute("PRAGMA index_list('contact_requests')").fetchall()
    assert rows[0] == ("request-oldest", "pending", None, None)
    assert rows[1][0:3] == ("request-newer", "rejected", "system:migration:0003")
    assert rows[1][3] is not None
    assert any(row[1] == "uq_contact_requests_pending_pair" and row[2] == 1 for row in indexes)


def test_0005_private_social_graph_schema_has_required_constraints(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "private-social.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        request_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'connection_requests'"
        ).fetchone()[0]
        connection_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'connections'"
        ).fetchone()[0]
        conversation_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()[0]
        message_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0]
        active_request_indexes = connection.execute(
            "PRAGMA index_list('connection_requests')"
        ).fetchall()
        active_connection_indexes = connection.execute(
            "PRAGMA index_list('connections')"
        ).fetchall()
        request_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('connection_requests')").fetchall()
        }
        connection_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('connections')").fetchall()
        }
    assert {
        "connection_blocks",
        "connection_requests",
        "connections",
        "conversations",
        "messages",
        "notifications",
        "connection_request_rate_buckets",
        "message_rate_buckets",
    }.issubset(tables)
    assert "ck_connection_requests_pair_order" in request_sql
    assert "ck_connection_requests_status" in request_sql
    assert "ck_connections_pair_order" in connection_sql
    assert "ck_connections_status" in connection_sql
    assert "ck_conversations_pair_order" in conversation_sql
    assert "ck_conversations_status" in conversation_sql
    assert "ck_messages_status" in message_sql
    assert {"requester_profile_handle", "recipient_profile_handle"}.issubset(request_columns)
    assert {"requester_profile_handle", "recipient_profile_handle"}.issubset(connection_columns)
    assert any(
        row[1] == "uq_connection_requests_active_pair" and row[2] == 1
        for row in active_request_indexes
    )
    assert any(
        row[1] == "uq_connections_active_pair" and row[2] == 1 for row in active_connection_indexes
    )


def test_0006_organization_verification_schema_is_append_only_and_bounded(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "organization-verification.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        organization_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('organizations')").fetchall()
        }
        evidence_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'organization_verification_evidence'"
        ).fetchone()[0]
        event_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'organization_verification_events'"
        ).fetchone()[0]
        evidence_indexes = connection.execute(
            "PRAGMA index_list('organization_verification_evidence')"
        ).fetchall()
        evidence_unique_columns = {
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info('{index[1]}')").fetchall()
            )
            for index in evidence_indexes
            if index[2] == 1
        }
    assert {
        "organization_verifications",
        "organization_verification_evidence",
        "organization_verification_events",
    }.issubset(tables)
    assert "verification_material_version" in organization_columns
    assert "ck_organization_verification_evidence_size" in evidence_sql
    assert "ck_organization_verification_evidence_content_type" in evidence_sql
    assert "ck_organization_verification_events_state" in event_sql
    assert "ck_organization_verification_events_actor_role" in event_sql
    assert ("verification_id",) in evidence_unique_columns


def test_0007_retention_executor_schema_has_durable_worker_controls(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "retention-executor.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        contact_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('contact_requests')").fetchall()
        }
        evidence_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('organization_verification_evidence')"
            ).fetchall()
        }
        task_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'lifecycle_tasks'"
        ).fetchone()[0]
        hold_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'retention_holds'"
        ).fetchone()[0]
        tombstone_indexes = connection.execute(
            "PRAGMA index_list('retention_tombstones')"
        ).fetchall()
        tombstone_unique_columns = {
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info('{index[1]}')").fetchall()
            )
            for index in tombstone_indexes
            if index[2] == 1
        }
    assert {"lifecycle_tasks", "retention_holds", "retention_tombstones"}.issubset(tables)
    assert "retention_expires_at" in contact_columns
    assert "retention_expires_at" in evidence_columns
    assert "ck_lifecycle_tasks_state" in task_sql
    assert "ck_lifecycle_tasks_attempts" in task_sql
    assert "ck_retention_holds_review_before_expiry" in hold_sql
    assert ("resource_type", "resource_id") in tombstone_unique_columns


def test_0008_agent_identity_mandate_schema_is_private_and_bound(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "agent-identity-mandates.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        grant_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('agent_grants')").fetchall()
        }
        contact_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('contact_requests')").fetchall()
        }
        mandate_indexes = connection.execute("PRAGMA index_list('agent_mandates')").fetchall()
        mandate_unique_columns = {
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info('{index[1]}')").fetchall()
            )
            for index in mandate_indexes
            if index[2] == 1
        }
    assert {"agent_identities", "agent_mandates"}.issubset(tables)
    assert "mandate_id" in grant_columns
    assert {
        "origin",
        "sender_mandate_id",
        "sender_identity_handle",
        "target_identity_handle",
    }.issubset(contact_columns)
    assert ("identity_id", "scope") in mandate_unique_columns


def test_0012_agent_identity_directory_indexes_match_the_public_page_orders(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "agent-identity-directory.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        indexes = connection.execute("PRAGMA index_list('agent_identities')").fetchall()
        index_columns = {
            row[1]: tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info('{row[1]}')").fetchall()
            )
            for row in indexes
        }
    assert index_columns["ix_agent_identities_status_created"] == ("status", "created_at", "id")
    assert index_columns["ix_agent_identities_profile_status_created"] == (
        "profile_document_id",
        "status",
        "created_at",
        "id",
    )


def test_0013_scrubs_historical_sensitive_idempotency_bodies_and_keeps_replay_metadata(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "idempotency-scrub.db"
    _alembic(api_root, database, "0012_agent_identity_directory")
    sensitive_types = (
        "profile",
        "resume",
        "contact_request",
        "proposal",
        "organization",
        "organization_membership",
        "job",
        "post_report",
        "moderation_appeal",
    )
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO idempotency_records (
                id, owner_id, idempotency_key, operation, request_hash,
                response_status, response_body, response_headers, resource_type, resource_id
            ) VALUES (?, 'owner', ?, 'POST:/v1/test', 'a' || ?, 201, ?, '{"ETag":"safe"}', ?, ?)
            """,
            [
                (
                    f"sensitive-{index}",
                    f"sensitive-key-{index}",
                    "0" * 63 + str(index),
                    '{"version":7,"private":"sentinel-private-content"}',
                    resource_type,
                    f"resource-{index}",
                )
                for index, resource_type in enumerate(sensitive_types)
            ],
        )
        connection.execute(
            """
            INSERT INTO idempotency_records (
                id, owner_id, idempotency_key, operation, request_hash,
                response_status, response_body, response_headers, resource_type, resource_id
            ) VALUES ('public-post', 'owner', 'public-post-key', 'POST:/v1/posts', ?, 201,
                      '{"id":"public","body":"public canonical receipt"}', '{}', 'post', 'public')
            """,
            ("b" * 64,),
        )
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        scrubbed = connection.execute(
            """
            SELECT resource_type, response_body, response_headers, resource_id
            FROM idempotency_records
            WHERE resource_type != 'post'
            ORDER BY resource_type
            """
        ).fetchall()
        public_body = connection.execute(
            "SELECT response_body FROM idempotency_records WHERE resource_type = 'post'"
        ).fetchone()[0]
    assert all(body == "" and headers == '{"ETag":"safe"}' for _, body, headers, _ in scrubbed)
    scrubbed_by_type = {resource_type: resource_id for resource_type, _, _, resource_id in scrubbed}
    assert scrubbed_by_type["profile"].endswith("@7")
    assert scrubbed_by_type["resume"].endswith("@7")
    assert public_body == '{"id":"public","body":"public canonical receipt"}'


def test_0010_post_moderation_casework_schema_is_private_and_constrained(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "post-moderation-casework.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        report_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('post_reports')").fetchall()
        }
        decision_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'moderation_decisions'"
        ).fetchone()[0]
        appeal_indexes = connection.execute("PRAGMA index_list('moderation_appeals')").fetchall()
        appeal_unique_columns = {
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info('{index[1]}')").fetchall()
            )
            for index in appeal_indexes
            if index[2] == 1
        }
    assert {
        "moderation_cases",
        "moderation_decisions",
        "moderation_appeals",
        "moderation_audit_events",
    }.issubset(tables)
    assert "case_id" in report_columns
    assert "content_moderator" in decision_sql
    assert ("decision_id",) in appeal_unique_columns


def test_0009_reports_backfill_to_case_lineage_without_sanctioning_legacy_posts(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "legacy-post-reports.db"
    _alembic(api_root, database, "0009_professional_posts")
    created_at = "2026-08-01T00:00:00+00:00"
    with sqlite3.connect(database) as connection:
        documents = [
            (f"profile-{suffix}", owner, f"{suffix}-profile")
            for suffix, owner in (
                ("published", "owner-published"),
                ("withheld", "owner-withheld"),
                ("withdrawn", "owner-withdrawn"),
            )
        ]
        connection.executemany(
            """
            INSERT INTO documents (id, kind, owner_id, public_identifier, visibility, current_version, created_at, updated_at)
            VALUES (?, 'profile', ?, ?, 'public', 1, ?, ?)
            """,
            [
                (document_id, owner_id, handle, created_at, created_at)
                for document_id, owner_id, handle in documents
            ],
        )
        connection.executemany(
            """
            INSERT INTO posts (
                id, owner_id, author_profile_document_id, author_profile_handle, status, current_version,
                sha256, storage_path, published_at, created_at, updated_at, withdrawn_at, withheld_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "post-published",
                    "owner-published",
                    "profile-published",
                    "published-profile",
                    "published",
                    "a" * 64,
                    "posts/post-published/versions/000001.md",
                    created_at,
                    created_at,
                    created_at,
                    None,
                    None,
                ),
                (
                    "post-withheld",
                    "owner-withheld",
                    "profile-withheld",
                    "withheld-profile",
                    "withheld",
                    "b" * 64,
                    "posts/post-withheld/versions/000001.md",
                    created_at,
                    created_at,
                    created_at,
                    None,
                    created_at,
                ),
                (
                    "post-withdrawn",
                    "owner-withdrawn",
                    "profile-withdrawn",
                    "withdrawn-profile",
                    "withdrawn",
                    "c" * 64,
                    "posts/post-withdrawn/versions/000001.md",
                    created_at,
                    created_at,
                    created_at,
                    created_at,
                    None,
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO post_reports (id, post_id, reporter_owner_id, reason_code, narrative, created_at)
            VALUES (?, ?, ?, 'spam', ?, ?)
            """,
            [
                ("report-published-a", "post-published", "reporter-a", "private-a", created_at),
                ("report-published-b", "post-published", "reporter-b", "private-b", created_at),
                ("report-withheld", "post-withheld", "reporter-c", "private-c", created_at),
                ("report-withdrawn", "post-withdrawn", "reporter-d", "private-d", created_at),
            ],
        )
        connection.execute(
            """
            INSERT INTO post_moderation_events (id, post_id, actor_id, actor_role, action, occurred_at)
            VALUES ('legacy-event', 'post-withheld', 'legacy-moderator', 'content_moderator', 'withhold', ?)
            """,
            (created_at,),
        )
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        reports = connection.execute(
            "SELECT id, post_id, case_id FROM post_reports ORDER BY id"
        ).fetchall()
        cases = connection.execute(
            "SELECT post_id, status, closed_at, retention_expires_at FROM moderation_cases ORDER BY post_id"
        ).fetchall()
        audits = connection.execute(
            "SELECT post_id, event_type, safe_metadata FROM moderation_audit_events ORDER BY id"
        ).fetchall()
        legacy_events = connection.execute(
            "SELECT id, post_id, action FROM post_moderation_events"
        ).fetchall()
        decisions = connection.execute("SELECT id FROM moderation_decisions").fetchall()
        posts = connection.execute("SELECT id, status FROM posts ORDER BY id").fetchall()
        partial_index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'uq_moderation_cases_open_post'"
        ).fetchone()[0]
    assert all(case_id is not None for _, _, case_id in reports)
    assert len({case_id for _, post_id, case_id in reports if post_id == "post-published"}) == 1
    assert [(post_id, status) for post_id, status, _, _ in cases] == [
        ("post-published", "open"),
        ("post-withdrawn", "legacy_withdrawn"),
        ("post-withheld", "legacy_withheld"),
    ]
    assert cases[0][2:] == (None, None)
    assert all(
        closed_at is not None and retention_expires_at is not None
        for _, _, closed_at, retention_expires_at in cases[1:]
    )
    assert sum(event_type == "case_opened" for _, event_type, _ in audits) == 3
    assert sum(event_type == "report_linked" for _, event_type, _ in audits) == 4
    assert any("pre_case_post_moderation_event" in metadata for _, _, metadata in audits)
    assert legacy_events == [("legacy-event", "post-withheld", "withhold")]
    assert decisions == []
    assert posts == [
        ("post-published", "published"),
        ("post-withdrawn", "withdrawn"),
        ("post-withheld", "withheld"),
    ]
    assert "WHERE status = 'open'" in partial_index_sql


def test_0023_contact_request_status_migration_has_bounded_preflight_and_named_sql_shape() -> None:
    api_root = Path(__file__).resolve().parents[1]
    migration = (
        api_root / "alembic" / "versions" / "0023_contact_request_status_constraint.py"
    ).read_text(encoding="utf-8")
    model = (api_root / "app" / "models.py").read_text(encoding="utf-8")

    assert 'revision: str = "0023_contact_request_status_constraint"' in migration
    assert 'down_revision: str | None = "0022_public_taxonomy_projection"' in migration
    assert "SELECT 1" in migration
    assert "status IS NULL" in migration
    assert "status NOT IN" in migration
    assert "LIMIT 1" in migration
    assert '"ck_contact_requests_status"' in migration
    assert "create_check_constraint" in migration
    assert "drop_constraint" in migration
    assert "status IN ('pending', 'accepted', 'rejected', 'blocked', 'reported')" in migration
    assert 'name="ck_contact_requests_status"' in model


def test_0023_contact_request_status_constraint_preserves_schema_and_validates_writes(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "contact-request-status.db"
    _alembic(api_root, database, "0022_public_taxonomy_projection")
    valid_statuses = ("pending", "accepted", "rejected", "blocked", "reported")
    with sqlite3.connect(database) as connection:
        for index, status in enumerate(valid_statuses):
            _insert_contact_request(
                connection,
                request_id=f"valid-status-{index}",
                status=status,
            )
    _alembic(api_root, database, "head")

    with sqlite3.connect(database) as connection:
        status_column = next(
            row
            for row in connection.execute("PRAGMA table_info('contact_requests')")
            if row[1] == "status"
        )
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'contact_requests'"
        ).fetchone()[0]
        index_rows = connection.execute("PRAGMA index_list('contact_requests')").fetchall()
        index_names = {row[1] for row in index_rows}
        foreign_keys = connection.execute("PRAGMA foreign_key_list('contact_requests')").fetchall()
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM contact_requests ORDER BY id"
            ).fetchall()
        ]
        assert status_column[3] == 1
        assert status_column[4].strip("'\"") == "pending"
        assert "ck_contact_requests_status" in table_sql
        assert all(status in table_sql for status in valid_statuses)
        assert {
            "ix_contact_requests_recipient_created",
            "ix_contact_requests_sender_created",
            "ix_contact_requests_origin_created",
            "uq_contact_requests_pending_pair",
        }.issubset(index_names)
        assert any(
            foreign_key[2] == "agent_mandates" and foreign_key[3] == "sender_mandate_id"
            for foreign_key in foreign_keys
        )
        assert statuses == list(valid_statuses)

        connection.execute(
            """
            INSERT INTO contact_requests (
                id, sender_owner_id, recipient_owner_id, sender_actor_id,
                sender_actor_method, target_document_id, purpose, message,
                created_at, retention_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "default-status",
                "sender-default-status",
                "recipient-default-status",
                "actor-default-status",
                "clerk_jwt",
                "document-default-status",
                "Introduction",
                "A bounded request",
                "2026-08-05T00:00:00+00:00",
                "2027-08-05T00:00:00+00:00",
            ),
        )
        assert connection.execute(
            "SELECT status FROM contact_requests WHERE id = 'default-status'"
        ).fetchone() == ("pending",)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_contact_request(
                connection,
                request_id="invalid-insert",
                status="unknown",
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE contact_requests SET status = 'unknown' WHERE id = ?",
                ("valid-status-0",),
            )
        connection.rollback()
        connection.execute(
            "UPDATE contact_requests SET status = 'accepted' WHERE id = ?",
            ("valid-status-0",),
        )


def test_0023_invalid_contact_request_status_aborts_upgrade_without_advancing_revision(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "contact-request-status-invalid-upgrade.db"
    _alembic(api_root, database, "0022_public_taxonomy_projection")
    with sqlite3.connect(database) as connection:
        _insert_contact_request(connection, request_id="invalid-upgrade", status="legacy")

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        _alembic(api_root, database, "head")
    assert "contact request status invariant preflight failed" in exc_info.value.stderr
    assert "invalid-upgrade" not in exc_info.value.stderr
    assert "legacy" not in exc_info.value.stderr

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0022_public_taxonomy_projection",
        )
        assert connection.execute(
            "SELECT status FROM contact_requests WHERE id = 'invalid-upgrade'"
        ).fetchone() == ("legacy",)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'contact_requests'"
        ).fetchone()[0]
        assert "ck_contact_requests_status" not in table_sql


def test_0023_downgrade_preserves_rows_and_reupgrade_preflights_again(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "contact-request-status-downgrade.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        _insert_contact_request(connection, request_id="downgrade-row", status="accepted")

    _alembic_downgrade(api_root, database, "0022_public_taxonomy_projection")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0022_public_taxonomy_projection",
        )
        assert connection.execute(
            "SELECT status FROM contact_requests WHERE id = 'downgrade-row'"
        ).fetchone() == ("accepted",)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'contact_requests'"
        ).fetchone()[0]
        status_column = next(
            row
            for row in connection.execute("PRAGMA table_info('contact_requests')")
            if row[1] == "status"
        )
        index_names = {
            row[1] for row in connection.execute("PRAGMA index_list('contact_requests')").fetchall()
        }
        foreign_keys = connection.execute("PRAGMA foreign_key_list('contact_requests')").fetchall()
        assert "ck_contact_requests_status" not in table_sql
        assert status_column[3] == 1
        assert status_column[4].strip("'\"") == "pending"
        assert {
            "ix_contact_requests_recipient_created",
            "ix_contact_requests_sender_created",
            "ix_contact_requests_origin_created",
            "uq_contact_requests_pending_pair",
        }.issubset(index_names)
        assert any(
            foreign_key[2] == "agent_mandates" and foreign_key[3] == "sender_mandate_id"
            for foreign_key in foreign_keys
        )
        connection.execute(
            "UPDATE contact_requests SET status = 'legacy' WHERE id = 'downgrade-row'"
        )

    with pytest.raises(subprocess.CalledProcessError):
        _alembic(api_root, database, "head")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0022_public_taxonomy_projection",
        )
        assert connection.execute(
            "SELECT status FROM contact_requests WHERE id = 'downgrade-row'"
        ).fetchone() == ("legacy",)


def test_0023_preflight_rejects_null_with_fixed_non_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    migration_path = api_root / "alembic" / "versions" / "0023_contact_request_status_constraint.py"
    spec = importlib.util.spec_from_file_location("migration_0023_under_test", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE contact_requests (status TEXT NULL)"))
        connection.execute(sa.text("INSERT INTO contact_requests (status) VALUES (NULL)"))
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        with pytest.raises(RuntimeError) as exc_info:
            migration._preflight_statuses()
    assert str(exc_info.value) == "contact request status invariant preflight failed"


def test_0025_exact_public_search_projection_starts_non_ready_and_is_content_free(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "exact-public-search-projection.db"
    _alembic(api_root, database, "0025_exact_public_search")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        state = connection.execute(
            "SELECT scope, revision, status FROM public_exact_search_projection_state"
        ).fetchone()
        snapshot_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('public_exact_search_document_snapshots')"
            ).fetchall()
        }
        snapshot_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'public_exact_search_document_snapshots'"
        ).fetchone()[0]
        snapshot_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list('public_exact_search_document_snapshots')"
            ).fetchall()
        }
        compact_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list('public_exact_search_compact_values')"
            ).fetchall()
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert revision == ("0025_exact_public_search",)
    assert {
        "public_exact_search_projection_state",
        "public_exact_search_document_snapshots",
        "public_exact_search_compact_values",
    }.issubset(tables)
    assert state == ("documents", 0, "backfill_required")
    assert {
        "document_id",
        "document_version",
        "source_sha256",
        "search_sha256",
        "kind",
        "schema_version",
        "identifier",
        "name",
        "headline",
        "title",
        "location",
        "availability_status",
        "availability_from",
        "representation_status",
        "contact_disclosure",
        "updated_at",
        "normalized_search_text",
        "search_vector",
    } == snapshot_columns
    assert {"owner_id", "visibility", "markdown", "content"}.isdisjoint(snapshot_columns)
    assert "ck_public_exact_search_snapshot_schema_version" in snapshot_sql
    assert "schema_version IN (1, 2)" in snapshot_sql
    assert "ix_public_exact_search_snapshots_kind_updated_id" in snapshot_indexes
    assert "ix_public_exact_search_snapshots_updated_id" in snapshot_indexes
    assert "ix_public_exact_search_compact_values_lookup" in compact_indexes


def test_0026_moderation_snapshot_digests_are_legacy_safe_and_reversible(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "moderation-evidence-snapshots.db"
    _alembic(api_root, database, "0025_exact_public_search")
    timestamp = "2026-08-06T12:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO documents (
                id, kind, owner_id, public_identifier, visibility,
                current_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-profile-0026",
                "profile",
                "legacy-subject-0026",
                "legacy-subject-0026",
                "public",
                1,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO posts (
                id, owner_id, author_profile_document_id, author_profile_handle,
                status, current_version, sha256, storage_path, published_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-post-0026",
                "legacy-subject-0026",
                "legacy-profile-0026",
                "legacy-subject-0026",
                "withheld",
                1,
                "a" * 64,
                "posts/legacy-post-0026/versions/000001.md",
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO moderation_cases (
                id, post_id, subject_owner_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-case-0026",
                "legacy-post-0026",
                "legacy-subject-0026",
                "appealed",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO moderation_decisions (
                id, case_id, post_id, moderator_id, moderator_role, action,
                reason_code, subject_explanation, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-decision-0026",
                "legacy-case-0026",
                "legacy-post-0026",
                "legacy-moderator-0026",
                "content_moderator",
                "withhold",
                "privacy",
                "Legacy explanation",
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO moderation_appeals (
                id, case_id, decision_id, subject_owner_id, rationale,
                status, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-appeal-0026",
                "legacy-case-0026",
                "legacy-decision-0026",
                "legacy-subject-0026",
                "Legacy appeal",
                "submitted",
                timestamp,
            ),
        )
        connection.commit()

    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        decision_columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info('moderation_decisions')").fetchall()
        }
        appeal_columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info('moderation_appeals')").fetchall()
        }
        legacy = connection.execute(
            """
            SELECT d.evidence_snapshot_sha256, a.review_snapshot_sha256
            FROM moderation_decisions AS d
            JOIN moderation_appeals AS a ON a.decision_id = d.id
            WHERE d.id = 'legacy-decision-0026'
            """
        ).fetchone()
        decision_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'moderation_decisions'"
        ).fetchone()[0]
        appeal_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'moderation_appeals'"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE moderation_decisions SET evidence_snapshot_sha256 = 'short'")
        connection.rollback()
        connection.execute(
            "UPDATE moderation_decisions SET evidence_snapshot_sha256 = ?",
            ("b" * 64,),
        )
        connection.execute(
            "UPDATE moderation_appeals SET review_snapshot_sha256 = ?",
            ("c" * 64,),
        )
        connection.commit()

    assert decision_columns["evidence_snapshot_sha256"].upper() == "VARCHAR(64)"
    assert appeal_columns["review_snapshot_sha256"].upper() == "VARCHAR(64)"
    assert legacy == (None, None)
    assert "ck_moderation_decisions_evidence_snapshot_sha256" in decision_sql
    assert "ck_moderation_appeals_review_snapshot_sha256" in appeal_sql

    _alembic_downgrade(api_root, database, "0025_exact_public_search")
    with sqlite3.connect(database) as connection:
        decision_columns_after = {
            row[1]
            for row in connection.execute("PRAGMA table_info('moderation_decisions')").fetchall()
        }
        appeal_columns_after = {
            row[1]
            for row in connection.execute("PRAGMA table_info('moderation_appeals')").fetchall()
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        preserved = connection.execute(
            "SELECT id FROM moderation_decisions WHERE id = 'legacy-decision-0026'"
        ).fetchone()
    assert "evidence_snapshot_sha256" not in decision_columns_after
    assert "review_snapshot_sha256" not in appeal_columns_after
    assert revision == ("0025_exact_public_search",)
    assert preserved == ("legacy-decision-0026",)


def test_0027_application_snapshot_size_is_legacy_safe_reversible_and_single_head(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "application-snapshot-size.db"
    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info('applications')").fetchall()
        }
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()[0]
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert columns["snapshot_size_bytes"] == 0
    assert "ck_applications_snapshot_size" in table_sql
    assert revision == (EXPECTED_ALEMBIC_HEAD,)

    environment = os.environ.copy()
    environment["CONNECTMD_DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    heads = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=api_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert heads == [f"{EXPECTED_ALEMBIC_HEAD} (head)"]

    _alembic_downgrade(api_root, database, "0026_moderation_evidence_snapshots")
    with sqlite3.connect(database) as connection:
        columns_after = {
            row[1] for row in connection.execute("PRAGMA table_info('applications')").fetchall()
        }
        revision_after = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert "snapshot_size_bytes" not in columns_after
    assert revision_after == ("0026_moderation_evidence_snapshots",)


def _insert_verification_change(
    connection: sqlite3.Connection,
    *,
    resource_id: str,
    event_type: str,
    resource_type: str,
    payload: str,
) -> None:
    connection.execute(
        """
        INSERT INTO change_events (
            owner_id, event_type, resource_type, resource_id, actor_id,
            actor_method, grant_id, payload, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            "owner-0028",
            event_type,
            resource_type,
            resource_id,
            "actor-0028",
            "clerk_jwt",
            payload,
            "2026-08-12T00:00:00+00:00",
        ),
    )


def test_0028_scrubs_only_canonical_verification_evidence_commitments(
    tmp_path: Path,
) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "verification-change-scrub.db"
    _alembic(api_root, database, "0027_application_snapshot_size")
    old_payload = '{"artifact_sha256": "' + ("a" * 64) + '", "state": "submitted"}'
    sanitized_payload = '{"state": "submitted"}'
    unrelated_payload = '{"artifact_sha256": "' + ("b" * 64) + '", "state": "submitted"}'
    with sqlite3.connect(database) as connection:
        _insert_verification_change(
            connection,
            resource_id="verification-old-0028",
            event_type="organization_verification.submitted",
            resource_type="organization_verification",
            payload=old_payload,
        )
        _insert_verification_change(
            connection,
            resource_id="verification-new-0028",
            event_type="organization_verification.submitted",
            resource_type="organization_verification",
            payload=sanitized_payload,
        )
        _insert_verification_change(
            connection,
            resource_id="unrelated-0028",
            event_type="document.created",
            resource_type="document",
            payload=unrelated_payload,
        )
        connection.commit()

    _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        payloads = dict(
            connection.execute("SELECT resource_id, payload FROM change_events").fetchall()
        )
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert payloads["verification-old-0028"] == sanitized_payload
    assert payloads["verification-new-0028"] == sanitized_payload
    assert payloads["unrelated-0028"] == unrelated_payload
    assert revision == (EXPECTED_ALEMBIC_HEAD,)

    _alembic_downgrade(api_root, database, "0027_application_snapshot_size")
    with sqlite3.connect(database) as connection:
        preserved = connection.execute(
            "SELECT payload FROM change_events WHERE resource_id = 'verification-old-0028'"
        ).fetchone()
        revision_after = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert preserved == (sanitized_payload,)
    assert revision_after == ("0027_application_snapshot_size",)


def test_0028_rejects_malformed_target_payload_without_rewriting_it(tmp_path: Path) -> None:
    api_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "verification-change-malformed.db"
    _alembic(api_root, database, "0027_application_snapshot_size")
    valid_payload = '{"artifact_sha256": "' + ("c" * 64) + '", "state": "submitted"}'
    malformed_payload = (
        '{"artifact_sha256":"'
        + ("d" * 64)
        + '","artifact_sha256":"'
        + ("e" * 64)
        + '","state":"submitted"}'
    )
    with sqlite3.connect(database) as connection:
        _insert_verification_change(
            connection,
            resource_id="verification-valid-before-malformed-0028",
            event_type="organization_verification.submitted",
            resource_type="organization_verification",
            payload=valid_payload,
        )
        _insert_verification_change(
            connection,
            resource_id="verification-malformed-0028",
            event_type="organization_verification.submitted",
            resource_type="organization_verification",
            payload=malformed_payload,
        )
        connection.commit()

    with pytest.raises(subprocess.CalledProcessError):
        _alembic(api_root, database, "head")
    with sqlite3.connect(database) as connection:
        preserved = dict(
            connection.execute("SELECT resource_id, payload FROM change_events").fetchall()
        )
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert preserved["verification-valid-before-malformed-0028"] == valid_payload
    assert preserved["verification-malformed-0028"] == malformed_payload
    assert revision == ("0027_application_snapshot_size",)
