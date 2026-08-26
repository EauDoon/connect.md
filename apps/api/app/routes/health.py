from __future__ import annotations

import json

import anyio
from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from app.db import require_current_database_schema
from app.services.artifact_durability import ArtifactReconciler
from app.services.exact_search import ExactSearchUnavailable
from app.services.search import MeiliSearchProjection
from app.services.storage import StorageIntegrityError
from app.services.taxonomy import TaxonomyUnavailable

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False, response_model=None)
async def readyz(request: Request) -> Response | dict[str, str]:
    if not request.app.state.deletion_journal_consistent:
        return Response(
            content=json.dumps(
                {
                    "status": "not_ready",
                    "database": "reconciliation_required",
                    "storage": "unknown",
                    "search": "unknown",
                }
            ),
            status_code=503,
            media_type="application/json",
        )
    reconciler: ArtifactReconciler = request.app.state.artifact_reconciler
    if reconciler.status == "unavailable":
        return Response(
            content=json.dumps(
                {
                    "status": "not_ready",
                    "database": "unknown",
                    "storage": "reconciliation_unavailable",
                    "search": "unknown",
                }
            ),
            status_code=503,
            media_type="application/json",
        )
    try:
        await anyio.to_thread.run_sync(request.app.state.store.check_ready)
    except StorageIntegrityError:
        return Response(
            content=json.dumps(
                {
                    "status": "not_ready",
                    "database": "unknown",
                    "storage": "unavailable",
                    "search": "unknown",
                }
            ),
            status_code=503,
            media_type="application/json",
        )
    taxonomy_ready: bool | None = None
    exact_installed = False
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
            if request.app.state.settings.is_production:
                await require_current_database_schema(session)
            taxonomy_ready = await request.app.state.taxonomy.check_ready(session)
            exact_installed = await request.app.state.exact_search.is_installed(session)
            if exact_installed:
                await request.app.state.exact_search.require_ready(session, require_postgresql=True)
    except TaxonomyUnavailable:
        return Response(
            content=json.dumps(
                {
                    "status": "not_ready",
                    "database": "ok",
                    "storage": "ok",
                    "search": "unknown",
                    "taxonomy": "unavailable",
                }
            ),
            status_code=503,
            media_type="application/json",
        )
    except ExactSearchUnavailable:
        payload: dict[str, str] = {
            "status": "not_ready",
            "database": "ok",
            "storage": "ok",
            "search": "unknown",
            "exact_search": "unavailable",
        }
        if taxonomy_ready is True:
            payload["taxonomy"] = "ok"
        return Response(
            content=json.dumps(payload),
            status_code=503,
            media_type="application/json",
        )
    except Exception:
        return Response(
            content=json.dumps(
                {
                    "status": "not_ready",
                    "database": "unavailable",
                    "storage": "ok",
                    "search": "unknown",
                }
            ),
            status_code=503,
            media_type="application/json",
        )
    if taxonomy_ready is True:
        taxonomy_status = "ok"
    else:
        taxonomy_status = None
    exact_status = "ok" if exact_installed else None
    search: MeiliSearchProjection = request.app.state.search
    if not search.enabled:
        response: dict[str, str] = {
            "status": "ready",
            "database": "ok",
            "storage": "ok",
            "search": "not_configured",
        }
        if taxonomy_status is not None:
            response["taxonomy"] = taxonomy_status
        if exact_status is not None:
            response["exact_search"] = exact_status
        return response
    if not await search.health():
        return Response(
            content=json.dumps(
                {
                    "status": "not_ready",
                    "database": "ok",
                    "storage": "ok",
                    "search": "unavailable",
                }
            ),
            status_code=503,
            media_type="application/json",
        )
    ready_response: dict[str, str] = {
        "status": "ready",
        "database": "ok",
        "storage": "ok",
        "search": "ok",
    }
    if taxonomy_status is not None:
        ready_response["taxonomy"] = taxonomy_status
    if exact_status is not None:
        ready_response["exact_search"] = exact_status
    return ready_response
