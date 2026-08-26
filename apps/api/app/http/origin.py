from __future__ import annotations

from fastapi import Request

from app.config import Settings


def public_base_url(request: Request) -> str:
    """Resolve the public origin from this app instance without global settings."""
    settings: Settings = request.app.state.settings
    configured = settings.public_base_url
    return (str(configured) if configured is not None else str(request.base_url)).rstrip("/")
