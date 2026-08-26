#!/usr/bin/env python3
"""Write release-notes.md from CHANGELOG.md for a vX.Y.Z tag."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: extract-changelog-section.py vX.Y.Z")
    tag = sys.argv[1]
    version = tag[1:] if tag.startswith("v") else tag
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(
        rf"^## \[{re.escape(version)}\](?:\s+-.*)?\s*$",
        re.MULTILINE,
    )
    match = heading.search(changelog)
    if match is None:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    rest = changelog[match.end() :]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    section = changelog[match.start() : match.end() + (nxt.start() if nxt else len(rest))]
    Path("release-notes.md").write_text(section.strip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
