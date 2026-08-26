"""Fail CI on high-confidence committed credential material without printing it."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    "Slack token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "Stripe secret": re.compile(rb"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    "Clerk secret": re.compile(rb"\bsk_(?:live|test)_[A-Za-z0-9]{20,}\b"),
}


def find_secret_labels(data: bytes) -> tuple[str, ...]:
    """Return high-confidence labels for every raw blob without exposing it."""

    return tuple(label for label, pattern in PATTERNS.items() if pattern.search(data))


def main() -> int:
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    ).split(b"\0")
    findings: list[tuple[str, str]] = []
    files_scanned = 0
    for raw_name in names:
        if not raw_name:
            continue
        path = Path(raw_name.decode("utf-8"))
        try:
            data = path.read_bytes()
        except OSError:
            continue
        files_scanned += 1
        for label in find_secret_labels(data):
            findings.append((path.as_posix(), label))
    if findings:
        for path, label in findings:
            print(f"{path}: possible {label}")
        return 1
    if files_scanned == 0:
        print("Secret scan failed: no repository text files were enumerated.")
        return 2
    print(f"High-confidence secret scan passed ({files_scanned} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
