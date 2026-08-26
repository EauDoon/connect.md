"""Backfill legacy reports and permit successive post-moderation case lineages.

Revision ID: 0011_post_moderation_legacy_backfill
Revises: 0010_post_moderation_casework
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from itertools import groupby
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from alembic import op

revision: str = "0011_post_moderation_legacy_backfill"
down_revision: str | None = "0010_post_moderation_casework"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CASE_STATUS_CHECK = (
    "status IN ('open', 'dismissed', 'withheld', 'appealed', 'appeal_upheld', "
    "'appeal_overturned', 'legacy_withheld', 'legacy_withdrawn')"
)


def _safe_metadata(*, disposition: str, pre_case_event: bool) -> str:
    return json.dumps(
        {
            "disposition": disposition,
            "pre_case_post_moderation_event": pre_case_event,
            "source": "migration_0011",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def upgrade() -> None:
    # 0010 allowed only one case for the lifetime of a post. A post can later
    # receive new evidence after a terminal dismissal or overturn, so retain
    # all closed cases and allow exactly one current open case.
    with op.batch_alter_table("moderation_cases") as batch:
        batch.drop_constraint("uq_moderation_cases_post", type_="unique")
        batch.drop_constraint("ck_moderation_cases_status", type_="check")
        batch.create_check_constraint("ck_moderation_cases_status", _CASE_STATUS_CHECK)
    op.create_index(
        "uq_moderation_cases_open_post",
        "moderation_cases",
        ["post_id"],
        unique=True,
        sqlite_where=sa.text("status = 'open'"),
        postgresql_where=sa.text("status = 'open'"),
    )

    bind = op.get_bind()
    reports = sa.table(
        "post_reports",
        sa.column("id", sa.String()),
        sa.column("post_id", sa.String()),
        sa.column("case_id", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    posts = sa.table(
        "posts",
        sa.column("id", sa.String()),
        sa.column("owner_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("withheld_at", sa.DateTime(timezone=True)),
        sa.column("withdrawn_at", sa.DateTime(timezone=True)),
    )
    cases = sa.table(
        "moderation_cases",
        sa.column("id", sa.String()),
        sa.column("post_id", sa.String()),
        sa.column("subject_owner_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("closed_at", sa.DateTime(timezone=True)),
        sa.column("retention_expires_at", sa.DateTime(timezone=True)),
    )
    audit_events = sa.table(
        "moderation_audit_events",
        sa.column("id", sa.String()),
        sa.column("case_id", sa.String()),
        sa.column("post_id", sa.String()),
        sa.column("event_type", sa.String()),
        sa.column("actor_id", sa.String()),
        sa.column("actor_role", sa.String()),
        sa.column("safe_metadata", sa.Text()),
        sa.column("occurred_at", sa.DateTime(timezone=True)),
    )
    legacy_events = sa.table(
        "post_moderation_events",
        sa.column("id", sa.String()),
        sa.column("post_id", sa.String()),
    )
    rows = bind.execute(
        sa.select(
            reports.c.id.label("report_id"),
            reports.c.post_id,
            reports.c.created_at,
            posts.c.owner_id.label("subject_owner_id"),
            posts.c.status.label("post_status"),
            posts.c.withheld_at,
            posts.c.withdrawn_at,
        )
        .select_from(reports.join(posts, reports.c.post_id == posts.c.id))
        .where(reports.c.case_id.is_(None))
        .order_by(reports.c.post_id.asc(), reports.c.created_at.asc(), reports.c.id.asc())
    ).mappings()
    for post_id, group in groupby(rows, key=lambda row: str(row["post_id"])):
        post_reports = list(group)
        first = post_reports[0]
        last = post_reports[-1]
        existing_case = bind.execute(
            sa.select(cases.c.id).where(cases.c.post_id == post_id).limit(1)
        ).scalar_one_or_none()
        if existing_case is None:
            post_status = str(first["post_status"])
            if post_status == "published":
                status = "open"
                closed_at = None
                retention_expires_at = None
            elif post_status == "withheld":
                status = "legacy_withheld"
                closed_at = first["withheld_at"] or last["created_at"]
                retention_expires_at = closed_at + timedelta(days=90)
            else:
                status = "legacy_withdrawn"
                closed_at = first["withdrawn_at"] or last["created_at"]
                retention_expires_at = closed_at + timedelta(days=90)
            case_id = str(uuid5(NAMESPACE_URL, f"connect.md:0011:moderation-case:{post_id}"))
            legacy_event_exists = (
                bind.execute(
                    sa.select(legacy_events.c.id).where(legacy_events.c.post_id == post_id).limit(1)
                ).scalar_one_or_none()
                is not None
            )
            bind.execute(
                sa.insert(cases).values(
                    id=case_id,
                    post_id=post_id,
                    subject_owner_id=first["subject_owner_id"],
                    status=status,
                    created_at=first["created_at"],
                    updated_at=closed_at or last["created_at"],
                    closed_at=closed_at,
                    retention_expires_at=retention_expires_at,
                )
            )
            bind.execute(
                sa.insert(audit_events).values(
                    id=str(uuid5(NAMESPACE_URL, f"connect.md:0011:case-opened:{post_id}")),
                    case_id=case_id,
                    post_id=post_id,
                    event_type="case_opened",
                    actor_id="system:migration:0011",
                    actor_role="system",
                    safe_metadata=_safe_metadata(
                        disposition=status, pre_case_event=legacy_event_exists
                    ),
                    occurred_at=first["created_at"],
                )
            )
        else:
            case_id = str(existing_case)
        for report in post_reports:
            bind.execute(
                sa.update(reports)
                .where(reports.c.id == report["report_id"])
                .values(case_id=case_id)
            )
            bind.execute(
                sa.insert(audit_events).values(
                    id=str(
                        uuid5(NAMESPACE_URL, f"connect.md:0011:report-linked:{report['report_id']}")
                    ),
                    case_id=case_id,
                    post_id=post_id,
                    event_type="report_linked",
                    actor_id="system:migration:0011",
                    actor_role="system",
                    safe_metadata=json.dumps(
                        {"source": "migration_0011"}, separators=(",", ":"), sort_keys=True
                    ),
                    occurred_at=report["created_at"],
                )
            )

    # Backfill is complete before this irreversible invariant is enforced.
    with op.batch_alter_table("post_reports") as batch:
        batch.alter_column("case_id", existing_type=sa.String(length=36), nullable=False)


def downgrade() -> None:
    raise RuntimeError(
        "0011 moderation-case lineage cannot be downgraded without destroying case history"
    )
