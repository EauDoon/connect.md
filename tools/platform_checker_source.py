"""Shared source-reading primitives for fail-closed platform checks."""

from __future__ import annotations

from pathlib import Path


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
