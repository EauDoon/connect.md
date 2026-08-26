#!/usr/bin/env python3
"""Fail-closed API and UI route ownership registry validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from .platform_contract_inventory import ROUTE_RE
except ImportError:
    import sys as _sys

    _tools_directory = str(Path(__file__).resolve().parent)
    if _tools_directory not in _sys.path:
        _sys.path.insert(0, _tools_directory)
    from platform_contract_inventory import ROUTE_RE


UI_ROUTE_RE = re.compile(
    r"^/(?:[A-Za-z0-9._~-]+|\{[A-Za-z][A-Za-z0-9_]*\})(?:/(?:[A-Za-z0-9._~-]+|\{[A-Za-z][A-Za-z0-9_]*\}))*$|^/$"
)


def _error(errors: list[str], location: str, message: str) -> None:
    errors.append(f"{location}: {message}")


def _object(
    value: Any, location: str, errors: list[str], required: set[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        _error(errors, location, "must be an object")
        return None
    unknown = set(value) - required
    missing = required - set(value)
    if unknown:
        _error(errors, location, f"has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        _error(errors, location, f"is missing fields: {', '.join(sorted(missing))}")
    return value


def _strings(
    value: Any, location: str, errors: list[str], *, nonempty: bool = False
) -> list[str]:
    if not isinstance(value, list):
        _error(errors, location, "must be an array")
        return []
    if nonempty and not value:
        _error(errors, location, "must not be empty")
    if not all(isinstance(item, str) and item for item in value):
        _error(errors, location, "must contain non-empty strings only")
    return [item for item in value if isinstance(item, str) and item]


def load_route_ownership(
    route_ownership_path: Path, ui_route_ownership_path: Path
) -> tuple[dict[str, str], dict[str, str], list[str], bool]:
    """Load both closed-shape ownership registries.

    The final boolean preserves the checker's existing early-return behavior for
    unreadable or malformed JSON files while allowing shape errors to continue
    through the normal registry validation path.
    """

    errors: list[str] = []
    try:
        route_registry = json.loads(route_ownership_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, {}, [f"route registry: cannot load JSON: {exc}"], True
    route_root = _object(
        route_registry,
        "route_registry",
        errors,
        {"schema_version", "registry_id", "routes"},
    )
    route_ownership: dict[str, str] = {}
    if route_root is not None:
        if route_root.get("schema_version") != 1:
            _error(errors, "route_registry.schema_version", "must equal 1")
        if route_root.get("registry_id") != "connect-md-platform-route-ownership":
            _error(
                errors,
                "route_registry.registry_id",
                "must equal 'connect-md-platform-route-ownership'",
            )
        raw_routes = route_root.get("routes")
        if not isinstance(raw_routes, dict) or not raw_routes:
            _error(errors, "route_registry.routes", "must be a non-empty object")
        else:
            for route, owner in raw_routes.items():
                if not isinstance(route, str) or not ROUTE_RE.fullmatch(route):
                    _error(
                        errors,
                        "route_registry.routes",
                        f"has invalid route key: {route!r}",
                    )
                elif not isinstance(owner, str) or not re.fullmatch(
                    r"^[a-z0-9]+(?:-[a-z0-9]+)*$", owner
                ):
                    _error(
                        errors,
                        f"route_registry.routes.{route}",
                        f"has invalid feature owner: {owner!r}",
                    )
                else:
                    route_ownership[route] = owner

    try:
        ui_route_registry = json.loads(
            ui_route_ownership_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return (
            route_ownership,
            {},
            [*errors, f"UI route registry: cannot load JSON: {exc}"],
            True,
        )
    ui_route_root = _object(
        ui_route_registry,
        "ui_route_registry",
        errors,
        {"schema_version", "registry_id", "routes"},
    )
    ui_route_ownership: dict[str, str] = {}
    if ui_route_root is not None:
        if ui_route_root.get("schema_version") != 1:
            _error(errors, "ui_route_registry.schema_version", "must equal 1")
        if ui_route_root.get("registry_id") != "connect-md-platform-ui-route-ownership":
            _error(
                errors,
                "ui_route_registry.registry_id",
                "must equal 'connect-md-platform-ui-route-ownership'",
            )
        raw_ui_routes = ui_route_root.get("routes")
        if not isinstance(raw_ui_routes, dict) or not raw_ui_routes:
            _error(errors, "ui_route_registry.routes", "must be a non-empty object")
        else:
            for route, owner in raw_ui_routes.items():
                if not isinstance(route, str) or not UI_ROUTE_RE.fullmatch(route):
                    _error(
                        errors,
                        "ui_route_registry.routes",
                        f"has invalid UI route key: {route!r}",
                    )
                elif not isinstance(owner, str) or not re.fullmatch(
                    r"^[a-z0-9]+(?:-[a-z0-9]+)*$", owner
                ):
                    _error(
                        errors,
                        f"ui_route_registry.routes.{route}",
                        f"has invalid feature owner: {owner!r}",
                    )
                else:
                    ui_route_ownership[route] = owner
    return route_ownership, ui_route_ownership, errors, False


def route_ownership_parity_errors(
    route_ownership: dict[str, str],
    route_anchors: dict[str, str],
    route_inventory: Any,
    feature_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    implemented_routes = {
        route: record.hidden for route, record in route_inventory.routes.items()
    }
    missing_route_owners = sorted(set(implemented_routes) - set(route_ownership))
    unknown_owned_routes = sorted(set(route_ownership) - set(implemented_routes))
    if missing_route_owners:
        _error(
            errors,
            "route_registry.routes",
            f"does not own implemented routes: {', '.join(missing_route_owners)}",
        )
    if unknown_owned_routes:
        _error(
            errors,
            "route_registry.routes",
            f"owns routes absent from apps/api/app/main.py: {', '.join(unknown_owned_routes)}",
        )
    for route, owner in sorted(route_ownership.items()):
        if owner not in feature_ids:
            _error(
                errors,
                f"route_registry.routes.{route}",
                f"references unregistered feature {owner!r}",
            )
    for route, anchor_owner in sorted(route_anchors.items()):
        owned_by = route_ownership.get(route)
        if owned_by is not None and owned_by != anchor_owner:
            _error(
                errors,
                f"registry.features.{anchor_owner}.surfaces.api.routes",
                f"route {route!r} is owned by feature {owned_by!r}",
            )
    return errors


def ui_route_ownership_parity_errors(
    ui_route_ownership: dict[str, str],
    implemented_ui_routes: set[str],
    feature_ui_routes: dict[str, set[str]],
    feature_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    missing_ui_route_owners = sorted(implemented_ui_routes - set(ui_route_ownership))
    unknown_owned_ui_routes = sorted(set(ui_route_ownership) - implemented_ui_routes)
    if missing_ui_route_owners:
        _error(
            errors,
            "ui_route_registry.routes",
            f"does not own implemented UI routes: {', '.join(missing_ui_route_owners)}",
        )
    if unknown_owned_ui_routes:
        _error(
            errors,
            "ui_route_registry.routes",
            f"owns UI routes absent from apps/web/app: {', '.join(unknown_owned_ui_routes)}",
        )
    for route, owner in sorted(ui_route_ownership.items()):
        if owner not in feature_ids:
            _error(
                errors,
                f"ui_route_registry.routes.{route}",
                f"references unregistered feature {owner!r}",
            )
        elif route not in feature_ui_routes.get(owner, set()):
            _error(
                errors,
                f"ui_route_registry.routes.{route}",
                f"owner feature {owner!r} does not declare the UI route",
            )
    return errors


__all__ = [
    "UI_ROUTE_RE",
    "load_route_ownership",
    "route_ownership_parity_errors",
    "ui_route_ownership_parity_errors",
]
