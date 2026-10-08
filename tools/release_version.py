#!/usr/bin/env python3
"""Keep connect.md's release metadata consistent. Standard library only.

The root VERSION file is the single source of truth. These files carry derived
copies that must always equal it:

- apps/web/package.json                "version"
- apps/web/package-lock.json           "version" and packages[""].version
- apps/api/pyproject.toml              [project] version
- apps/api/app/__init__.py             __version__ (feeds the FastAPI app,
                                       OpenAPI, the Agent Card, MCP serverInfo,
                                       and `python -m app.cli --version`)

Subcommands:

    check [--tag vX.Y.Z]                verify every copy, the CHANGELOG section
                                        for VERSION, and its compare links
    set X.Y.Z                           rewrite VERSION and every copy in place
    notes --tag vX.Y.Z --output PATH    write that version's CHANGELOG section

CI runs `check` on every pull request and push, the mint workflow runs it
before tagging, and release.yml runs `check --tag` on a pushed tag.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

REPOSITORY_URL = "https://github.com/EauDoon/connect.md"
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
TAG_PATTERN = re.compile(r"v([0-9]+\.[0-9]+\.[0-9]+)")
DEFAULT_ROOT = Path(__file__).resolve().parents[1]

VERSION_FILE = "VERSION"
CHANGELOG = "CHANGELOG.md"
PACKAGE_JSON = "apps/web/package.json"
PACKAGE_LOCK = "apps/web/package-lock.json"
PYPROJECT = "apps/api/pyproject.toml"
API_INIT = "apps/api/app/__init__.py"

_PACKAGE_VERSION = re.compile(r'^(  "version": ")([^"]*)(",?)$', re.MULTILINE)
_LOCK_ROOT_PACKAGE = re.compile(
    r'^(    "": \{\n(?:      .*\n)*?      "version": ")([^"]*)(",?)$', re.MULTILINE
)
_API_VERSION = re.compile(r'^(__version__ = ")([^"]*)(")$', re.MULTILINE)
_PROJECT_TABLE = re.compile(r"^\[project\][ \t]*$", re.MULTILINE)
_NEXT_TABLE = re.compile(r"^\[", re.MULTILINE)
_PROJECT_VERSION = re.compile(r'^(version = ")([^"]*)(")$', re.MULTILINE)
_LINK_DEFINITION = re.compile(r"^\[[^\]]+\]: \S+$")


class ReleaseMetadataError(Exception):
    """Release metadata is missing, malformed, or inconsistent."""


def _read(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_bytes().decode("utf-8")
    except OSError as error:
        raise ReleaseMetadataError(f"{relative}: cannot read ({error.strerror})") from error


def _write(root: Path, relative: str, text: str) -> None:
    (root / relative).write_bytes(text.encode("utf-8"))


def _only(pattern: re.Pattern[str], text: str, label: str) -> re.Match[str]:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ReleaseMetadataError(f"{label}: expected exactly one version field, found {len(matches)}")
    return matches[0]


def read_version(root: Path) -> str:
    version = _read(root, VERSION_FILE).strip()
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseMetadataError(f"{VERSION_FILE}: {version!r} is not MAJOR.MINOR.PATCH")
    return version


def _project_bounds(text: str) -> tuple[int, int]:
    start = _PROJECT_TABLE.search(text)
    if start is None:
        raise ReleaseMetadataError(f"{PYPROJECT}: no [project] table")
    following = _NEXT_TABLE.search(text, start.end())
    return start.end(), following.start() if following else len(text)


def component_versions(root: Path) -> dict[str, str | None]:
    """Every derived copy of the version, keyed by a readable location."""

    found: dict[str, str | None] = {}
    try:
        package = json.loads(_read(root, PACKAGE_JSON))
        found[f'{PACKAGE_JSON} "version"'] = package.get("version")
    except json.JSONDecodeError:
        found[f'{PACKAGE_JSON} "version"'] = None
    try:
        lock = json.loads(_read(root, PACKAGE_LOCK))
        found[f'{PACKAGE_LOCK} "version"'] = lock.get("version")
        found[f'{PACKAGE_LOCK} packages[""].version'] = lock.get("packages", {}).get("", {}).get("version")
    except json.JSONDecodeError:
        found[f'{PACKAGE_LOCK} "version"'] = None
        found[f'{PACKAGE_LOCK} packages[""].version'] = None
    try:
        pyproject = tomllib.loads(_read(root, PYPROJECT))
        found[f"{PYPROJECT} [project] version"] = pyproject.get("project", {}).get("version")
    except tomllib.TOMLDecodeError:
        found[f"{PYPROJECT} [project] version"] = None
    api = _API_VERSION.findall(_read(root, API_INIT))
    found[f"{API_INIT} __version__"] = api[0][1] if len(api) == 1 else None
    return found


def changelog_problems(changelog: str, version: str) -> list[str]:
    problems: list[str] = []
    if re.search(r"^## \[Unreleased\][ \t]*$", changelog, re.MULTILINE) is None:
        problems.append(f"{CHANGELOG}: missing the '## [Unreleased]' section")
    heading = re.search(
        rf"^## \[{re.escape(version)}\] - ([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})[ \t]*$",
        changelog,
        re.MULTILINE,
    )
    if heading is None:
        problems.append(f"{CHANGELOG}: missing a dated '## [{version}] - YYYY-MM-DD' section")
    else:
        try:
            dt.date.fromisoformat(heading.group(1))
        except ValueError:
            problems.append(f"{CHANGELOG}: '{heading.group(1)}' in the {version} heading is not a valid date")
    if re.search(rf"^\[{re.escape(version)}\]: {re.escape(REPOSITORY_URL)}/\S+$", changelog, re.MULTILINE) is None:
        problems.append(f"{CHANGELOG}: missing the '[{version}]: {REPOSITORY_URL}/...' link")
    unreleased = f"[unreleased]: {REPOSITORY_URL}/compare/v{version}...HEAD"
    if re.search(rf"^{re.escape(unreleased)}$", changelog, re.MULTILINE) is None:
        problems.append(f"{CHANGELOG}: the unreleased link must be '{unreleased}'")
    return problems


def check(root: Path, tag: str | None = None) -> list[str]:
    """Return every consistency problem; an empty list means release-ready metadata."""

    try:
        version = read_version(root)
    except ReleaseMetadataError as error:
        return [str(error)]
    problems: list[str] = []
    try:
        for location, value in component_versions(root).items():
            if value != version:
                problems.append(f"{location} is {value!r}, expected {version!r} from {VERSION_FILE}")
        problems.extend(changelog_problems(_read(root, CHANGELOG), version))
    except ReleaseMetadataError as error:
        problems.append(str(error))
    if tag is not None and tag != f"v{version}":
        problems.append(f"tag {tag!r} does not match {VERSION_FILE} {version!r} (expected 'v{version}')")
    return problems


def _replace_group(text: str, match: re.Match[str], value: str) -> str:
    return text[: match.start(2)] + value + text[match.end(2) :]


def set_version(root: Path, version: str) -> None:
    """Rewrite VERSION and its derived copies, touching only the version text."""

    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseMetadataError(f"{version!r} is not MAJOR.MINOR.PATCH")

    def package(text: str) -> str:
        return _replace_group(text, _only(_PACKAGE_VERSION, text, PACKAGE_JSON), version)

    def lock(text: str) -> str:
        text = _replace_group(text, _only(_PACKAGE_VERSION, text, PACKAGE_LOCK), version)
        return _replace_group(text, _only(_LOCK_ROOT_PACKAGE, text, f'{PACKAGE_LOCK} packages[""]'), version)

    def pyproject(text: str) -> str:
        start, end = _project_bounds(text)
        matches = list(_PROJECT_VERSION.finditer(text, start, end))
        if len(matches) != 1:
            raise ReleaseMetadataError(f"{PYPROJECT}: expected one static [project] version, found {len(matches)}")
        return _replace_group(text, matches[0], version)

    def api(text: str) -> str:
        return _replace_group(text, _only(_API_VERSION, text, API_INIT), version)

    edits: list[tuple[str, Callable[[str], str]]] = [
        (PACKAGE_JSON, package),
        (PACKAGE_LOCK, lock),
        (PYPROJECT, pyproject),
        (API_INIT, api),
    ]
    # Compute every rewrite before writing any, so a malformed file leaves the tree unchanged.
    rewritten = [(relative, edit(_read(root, relative))) for relative, edit in edits]
    _write(root, VERSION_FILE, version + "\n")
    for relative, text in rewritten:
        _write(root, relative, text)
    for relative in (PACKAGE_JSON, PACKAGE_LOCK):
        json.loads(_read(root, relative))


def release_notes(root: Path, tag: str) -> str:
    """The CHANGELOG section for a vX.Y.Z tag, heading included, links excluded."""

    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ReleaseMetadataError(f"{tag!r} is not a vMAJOR.MINOR.PATCH tag")
    version = match.group(1)
    changelog = _read(root, CHANGELOG)
    heading = re.search(rf"^## \[{re.escape(version)}\](?:[ \t]+-.*)?[ \t]*$", changelog, re.MULTILINE)
    if heading is None:
        raise ReleaseMetadataError(f"{CHANGELOG} has no section for {version}")
    following = re.search(r"^## ", changelog[heading.end() :], re.MULTILINE)
    end = heading.end() + following.start() if following else len(changelog)
    lines = changelog[heading.start() : end].rstrip().splitlines()
    # The last section runs into the compare-link definitions; they are not notes.
    while lines and (not lines[-1].strip() or _LINK_DEFINITION.match(lines[-1])):
        lines.pop()
    if len(lines) < 2 or not "\n".join(lines[1:]).strip():
        raise ReleaseMetadataError(f"{CHANGELOG}: the {version} section is empty")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/release_version.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="verify release metadata consistency")
    check_parser.add_argument("--tag", help="also require this tag to equal v + VERSION")
    set_parser = commands.add_parser("set", help="rewrite VERSION and every derived copy")
    set_parser.add_argument("version")
    notes_parser = commands.add_parser("notes", help="write one version's CHANGELOG section")
    notes_parser.add_argument("--tag", required=True)
    notes_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root: Path = args.root

    try:
        if args.command == "check":
            problems = check(root, args.tag)
            for problem in problems:
                print(f"release metadata: {problem}", file=sys.stderr)
            if problems:
                return 1
            print(f"release metadata: consistent at {read_version(root)}")
        elif args.command == "set":
            set_version(root, args.version)
            print(f"release metadata: set {args.version} in {VERSION_FILE} and every derived copy")
        else:
            args.output.write_bytes(release_notes(root, args.tag).encode("utf-8"))
            print(f"release metadata: wrote {args.tag} notes to {args.output}")
    except ReleaseMetadataError as error:
        print(f"release metadata: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
