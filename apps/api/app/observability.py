from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import TextIO

from fastapi import Request

REQUEST_LOGGER_NAME = "connectmd.api.requests"
_MAX_TRACEBACK_FRAMES = 32


class _ConnectmdJsonHandler(logging.StreamHandler[TextIO]):
    """Marker handler so repeated app construction does not duplicate output."""


def configure_request_logger() -> logging.Logger:
    """Return an idempotently configured JSON-lines request logger."""
    logger = logging.getLogger(REQUEST_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, _ConnectmdJsonHandler) for handler in logger.handlers):
        handler = _ConnectmdJsonHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def request_route_template(request: Request) -> str:
    """Return only a code-defined route template, never a caller-supplied URL."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.startswith("/"):
        return template
    return "__unmatched__"


def _safe_traceback(traceback: TracebackType | None) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    current = traceback
    while current is not None and len(frames) < _MAX_TRACEBACK_FRAMES:
        code = current.tb_frame.f_code
        module = current.tb_frame.f_globals.get("__name__")
        frames.append(
            {
                "function": code.co_name,
                "line": current.tb_lineno,
                "module": module if isinstance(module, str) else "unknown",
            }
        )
        current = current.tb_next
    return frames


def emit_request_log(
    logger: logging.Logger,
    request: Request,
    *,
    request_id: str,
    status: int,
    duration_ms: float,
    exception: BaseException | None = None,
) -> None:
    """Emit an allowlisted request event without URLs, headers, bodies, or identities."""
    event: dict[str, object] = {
        "duration_ms": round(max(duration_ms, 0.0), 3),
        "event": "api.request.completed",
        "method": request.method,
        "request_id": request_id,
        "route": request_route_template(request),
        "status": status,
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    if exception is not None:
        event["error"] = "unhandled_exception"
        event["exception_type"] = type(exception).__name__
        event["traceback"] = _safe_traceback(exception.__traceback__)
    level = logging.ERROR if exception is not None else logging.INFO
    logger.log(level, json.dumps(event, separators=(",", ":"), sort_keys=True))
