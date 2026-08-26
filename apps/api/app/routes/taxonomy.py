from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import TaxonomyCatalogEntry, TaxonomyTermListResponse
from app.services.taxonomy import (
    TaxonomyCursorMalformed,
    TaxonomyCursorStale,
    TaxonomyInvalidValue,
    TaxonomyUnknown,
)

router = APIRouter()


def reject_duplicate_cursor_query_parameter(request: Request) -> None:
    if len(request.query_params.getlist("cursor")) > 1:
        raise HTTPException(status_code=422, detail="cursor accepts one value")


@router.get(
    "/v1/taxonomies",
    response_model=list[TaxonomyCatalogEntry],
    tags=["taxonomy"],
    summary="List public search taxonomy types",
)
async def list_taxonomies(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> list[TaxonomyCatalogEntry]:
    response.headers["Cache-Control"] = "no-store"
    return [
        TaxonomyCatalogEntry.model_validate(item)
        for item in await request.app.state.taxonomy.catalog(session)
    ]


@router.get(
    "/v1/taxonomies/{taxonomy}",
    response_model=TaxonomyTermListResponse,
    tags=["taxonomy"],
    summary="List current public search taxonomy terms",
)
async def list_taxonomy_terms(
    taxonomy: str,
    request: Request,
    response: Response,
    q: Annotated[str, Query(max_length=100)] = "",
    cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    session: AsyncSession = Depends(get_session),
) -> TaxonomyTermListResponse:
    response.headers["Cache-Control"] = "no-store"
    reject_duplicate_cursor_query_parameter(request)
    try:
        terms, next_cursor, revision = await request.app.state.taxonomy.terms(
            session,
            taxonomy=taxonomy,
            query=q,
            cursor=cursor,
            limit=limit,
        )
    except TaxonomyCursorStale as exc:
        raise HTTPException(status_code=409, detail="taxonomy cursor is stale") from exc
    except TaxonomyCursorMalformed as exc:
        raise HTTPException(status_code=400, detail="taxonomy cursor is malformed") from exc
    except TaxonomyUnknown as exc:
        raise HTTPException(status_code=404, detail="taxonomy was not found") from exc
    except TaxonomyInvalidValue as exc:
        raise HTTPException(status_code=422, detail="taxonomy query is invalid") from exc
    return TaxonomyTermListResponse(
        terms=terms,
        next_cursor=next_cursor,
        revision=revision,
    )
