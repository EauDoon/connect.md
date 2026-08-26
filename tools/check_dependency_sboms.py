"""Validate lockfile-backed CycloneDX SBOMs and emit stable receipt inputs.

The CI jobs generate SBOM JSON with the tools already present in their locked
build environments.  This module deliberately uses only the Python standard
library so validation cannot add a runtime or test dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import quote

MAX_INPUT_BYTES = 32 * 1024 * 1024
SUPPORTED_SPEC_VERSIONS = {"1.4", "1.5", "1.6"}
EXPECTED_LOCKFILES = {
    "api": "apps/api/requirements.lock",
    "web": "apps/web/package-lock.json",
}
PYTHON_LOCK_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[^\]]+\])?==(?P<version>[^\s\\;]+)"
)


class SbomValidationError(ValueError):
    """A fail-closed SBOM or lockfile validation error."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SbomValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise SbomValidationError(f"JSON input exceeds {MAX_INPUT_BYTES} bytes")
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_reject_duplicate_pairs)
    except SbomValidationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SbomValidationError("JSON input is unreadable or malformed") from exc
    if not isinstance(value, dict):
        raise SbomValidationError("SBOM must be a JSON object")
    return value


def _normalise_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _component_identity(name: Any, version: Any) -> tuple[str, str]:
    if not isinstance(name, str) or not name.strip():
        raise SbomValidationError("every SBOM component requires a non-empty name")
    if not isinstance(version, str) or not version.strip():
        raise SbomValidationError("every SBOM component requires a non-empty version")
    if any(ord(char) < 0x20 for char in name + version):
        raise SbomValidationError(
            "SBOM component names and versions may not contain controls"
        )
    return _normalise_name(name), version


def _parse_python_lock(path: Path) -> set[tuple[str, str]]:
    entries: set[tuple[str, str]] = set()
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise SbomValidationError("Python lockfile is too large")
        lines = path.read_text(encoding="utf-8").splitlines()
    except SbomValidationError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise SbomValidationError("Python lockfile is unreadable") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", "--hash=")):
            continue
        if line.startswith("-"):
            raise SbomValidationError(
                "Python lockfile contains an unsupported option or include"
            )
        match = PYTHON_LOCK_LINE.match(line)
        if match is None:
            raise SbomValidationError(
                "Python lockfile contains an unpinned or unsupported entry"
            )
        entries.add((_normalise_name(match.group("name")), match.group("version")))
    if not entries:
        raise SbomValidationError("Python lockfile has no pinned components")
    return entries


def _npm_package_name(package_path: str) -> str:
    tail = package_path.rsplit("/node_modules/", 1)[-1]
    if tail.startswith("node_modules/"):
        tail = tail.removeprefix("node_modules/")
    if tail.startswith("@"):
        parts = tail.split("/")
        if len(parts) < 2:
            raise SbomValidationError(
                "npm lockfile contains an invalid scoped package path"
            )
        return "/".join(parts[:2])
    return tail.split("/", 1)[0]


def _parse_npm_lock(path: Path) -> tuple[set[tuple[str, str]], tuple[str, str]]:
    payload = _load_json(path)
    if payload.get("lockfileVersion") != 3:
        raise SbomValidationError("web lockfile must be npm lockfileVersion 3")
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        raise SbomValidationError("web lockfile packages must be an object")
    root = packages.get("")
    if not isinstance(root, dict):
        raise SbomValidationError("web lockfile root package is missing")
    root_identity = _component_identity(root.get("name"), root.get("version"))
    entries: set[tuple[str, str]] = set()
    for package_path, package in packages.items():
        if package_path == "":
            continue
        if not isinstance(package_path, str) or not package_path.startswith(
            "node_modules/"
        ):
            raise SbomValidationError(
                "web lockfile contains an unsupported package path"
            )
        if not isinstance(package, dict):
            raise SbomValidationError("web lockfile package entry is not an object")
        name = _npm_package_name(package_path)
        identity = _component_identity(name, package.get("version"))
        entries.add(identity)
    if not entries:
        raise SbomValidationError("web lockfile has no dependency components")
    return entries, root_identity


def _lock_entries(
    kind: str, path: Path
) -> tuple[set[tuple[str, str]], tuple[str, str] | None]:
    if kind == "api":
        return _parse_python_lock(path), None
    if kind == "web":
        return _parse_npm_lock(path)
    raise SbomValidationError("kind must be api or web")


def _web_metadata_matches_root(
    metadata_component: Any, root_identity: tuple[str, str]
) -> bool:
    if not isinstance(metadata_component, dict):
        return False
    try:
        metadata_identity = _component_identity(
            metadata_component.get("name"), metadata_component.get("version")
        )
    except SbomValidationError:
        return False
    if metadata_identity == root_identity:
        return True
    expected_purl = f"pkg:npm/{quote(root_identity[0], safe='/')}@{root_identity[1]}"
    return (
        metadata_identity[1] == root_identity[1]
        and metadata_component.get("purl") == expected_purl
    )


def _validate_bom(payload: dict[str, Any]) -> list[tuple[str, str]]:
    if payload.get("bomFormat") != "CycloneDX":
        raise SbomValidationError("SBOM bomFormat must be CycloneDX")
    spec_version = payload.get("specVersion")
    if spec_version not in SUPPORTED_SPEC_VERSIONS:
        raise SbomValidationError("SBOM specVersion is unsupported")
    schema = payload.get("$schema")
    if not isinstance(schema, str) or not re.fullmatch(
        r"https?://cyclonedx\.org/schema/bom-1\.(4|5|6)\.schema\.json", schema
    ):
        raise SbomValidationError("SBOM must identify its CycloneDX schema")
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise SbomValidationError("SBOM components must be a non-empty array")
    identities: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for component in components:
        if not isinstance(component, dict):
            raise SbomValidationError("SBOM components must be objects")
        if component.get("type") != "library":
            raise SbomValidationError("SBOM components must be libraries")
        identity = _component_identity(component.get("name"), component.get("version"))
        if identity in seen:
            raise SbomValidationError("SBOM contains duplicate component identities")
        seen.add(identity)
        identities.append(identity)
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise SbomValidationError("SBOM metadata must be an object when present")
    return identities


def _canonical_inventory(
    kind: str,
    identities: set[tuple[str, str]],
    root_identity: tuple[str, str] | None,
) -> dict[str, Any]:
    inventory: dict[str, Any] = {
        "kind": kind,
        "components": [
            {"name": name, "version": version} for name, version in sorted(identities)
        ],
    }
    if root_identity is not None:
        inventory["root"] = {
            "name": root_identity[0],
            "version": root_identity[1],
        }
    return inventory


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def validate_sbom(kind: str, lock_path: Path, sbom_path: Path) -> dict[str, Any]:
    expected, root_identity = _lock_entries(kind, lock_path)
    payload = _load_json(sbom_path)
    actual = set(_validate_bom(payload))
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing[:5]}")
        if extra:
            details.append(f"extra={extra[:5]}")
        raise SbomValidationError(
            "SBOM component coverage mismatch: " + ", ".join(details)
        )
    if kind == "web":
        metadata = payload.get("metadata")
        component = metadata.get("component") if isinstance(metadata, dict) else None
        if not _web_metadata_matches_root(component, root_identity):
            raise SbomValidationError(
                "web SBOM metadata component does not match lockfile root"
            )
    canonical = _canonical_json(_canonical_inventory(kind, actual, root_identity))
    return {
        "format": "connectmd-dependency-sbom-receipt-v1",
        "kind": kind,
        "lockfile": EXPECTED_LOCKFILES[kind],
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "sbom_sha256": hashlib.sha256(canonical).hexdigest(),
        "component_count": len(actual),
        "spec_version": payload["specVersion"],
    }


def write_receipt(receipt_path: Path, receipt: dict[str, Any]) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(_canonical_json(receipt))


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("api", "web"), required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        receipt = validate_sbom(args.kind, args.lock, args.sbom)
        write_receipt(args.receipt, receipt)
    except (OSError, SbomValidationError) as exc:
        print(f"SBOM_CHECK_FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"SBOM_CHECK_PASS kind={receipt['kind']} sha256={receipt['sbom_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
