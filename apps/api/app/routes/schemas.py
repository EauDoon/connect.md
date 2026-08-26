from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.markdown import MarkdownValidationError, load_schema

router = APIRouter()


@router.get("/schemas/{kind}.schema.json", include_in_schema=False)
async def markdown_schema(kind: str) -> Response:
    try:
        schema = load_schema(kind)
    except MarkdownValidationError as exc:
        raise HTTPException(status_code=404, detail="Markdown schema was not found") from exc
    return Response(json.dumps(schema, indent=2) + "\n", media_type="application/schema+json")
