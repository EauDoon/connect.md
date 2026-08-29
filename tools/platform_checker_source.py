"""Shared source-reading primitives for fail-closed platform checks."""

from __future__ import annotations

import ast
import re
from pathlib import Path


_FUNCTION_INVENTORY: dict[str, tuple[str, dict[str, tuple[str, ...]]]] = {}


def append_error(errors: list[str], location: str, message: str) -> None:
    errors.append(f"{location}: {message}")


def read_anchor_source(root: Path, relative_path: str, errors: list[str]) -> str:
    try:
        return (root / relative_path).read_text(encoding="utf-8")
    except OSError as exc:
        append_error(
            errors, f"repository.anchors.{relative_path}", f"cannot read file: {exc}"
        )
        return ""


def _function_inventory(source: str, relative_path: str) -> dict[str, tuple[str, ...]]:
    cached = _FUNCTION_INVENTORY.get(relative_path)
    if cached is not None and cached[0] == source:
        return cached[1]

    tree = ast.parse(source)
    lines = tuple(line.encode("utf-8") for line in source.splitlines(keepends=True))
    functions: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.end_lineno is None or node.end_col_offset is None:
            segment = ast.get_source_segment(source, node) or ""
        else:
            start = node.lineno - 1
            end = node.end_lineno - 1
            if start == end:
                encoded = lines[start][node.col_offset : node.end_col_offset]
            else:
                encoded = b"".join(
                    (
                        lines[start][node.col_offset :],
                        *lines[start + 1 : end],
                        lines[end][: node.end_col_offset],
                    )
                )
            segment = encoded.decode("utf-8")
        functions.setdefault(node.name, []).append(segment)
    inventory = {name: tuple(matches) for name, matches in functions.items()}
    _FUNCTION_INVENTORY[relative_path] = (source, inventory)
    return inventory


def function_source(
    source: str, function_name: str, relative_path: str, errors: list[str]
) -> str:
    """Return one named Python function, caching each file's AST inventory."""
    try:
        functions = _function_inventory(source, relative_path).get(function_name, ())
    except SyntaxError as exc:
        append_error(
            errors, f"repository.anchors.{relative_path}", f"cannot parse source: {exc}"
        )
        return ""
    if len(functions) != 1:
        append_error(
            errors,
            f"repository.anchors.{relative_path}",
            f"must define exactly one {function_name!r} function",
        )
        return ""
    return functions[0]


def typescript_function_source(
    source: str, function_name: str, relative_path: str, errors: list[str]
) -> str:
    """Return one exported TypeScript function without parsing TS as Python."""
    marker = f"export function {function_name}"
    starts = [match.start() for match in re.finditer(re.escape(marker), source)]
    if len(starts) != 1:
        append_error(
            errors,
            f"repository.anchors.{relative_path}",
            f"must define exactly one {function_name!r} function",
        )
        return ""
    start = starts[0]
    next_start = source.find("\nexport function ", start + len(marker))
    return source[start : next_start if next_start >= 0 else len(source)]


def require_source_markers(
    source: str,
    relative_path: str,
    markers: dict[str, str],
    errors: list[str],
) -> None:
    for label, marker in markers.items():
        if marker not in source:
            append_error(
                errors,
                f"repository.anchors.{relative_path}",
                f"is missing {label} marker {marker!r}",
            )


def ordered_anchor_positions(
    source: str,
    relative_path: str,
    anchors: list[tuple[str, str]],
    errors: list[str],
) -> dict[str, int]:
    positions: dict[str, int] = {}
    for label, marker in anchors:
        position = source.find(marker)
        if position < 0:
            append_error(
                errors,
                f"repository.operations.{relative_path}",
                f"is missing {label} anchor {marker!r}",
            )
        else:
            positions[label] = position
    if len(positions) == len(anchors):
        ordered = [positions[label] for label, _ in anchors]
        if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
            append_error(
                errors,
                f"repository.operations.{relative_path}",
                "does not preserve the required fail-closed control order",
            )
    return positions
