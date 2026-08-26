"""CI-friendly safety, current-byte parity, compile, and hermetic test gate."""

from __future__ import annotations

import io
import re
import sys
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent
REPOSITORY = KIT_DIR.parents[1]
EXPECTED_FILES = {
    "README.md",
    "agent_client.py",
    "fake_server.py",
    "test_agent_client.py",
    "check_agent_client.py",
}
SOURCE_FILES = (KIT_DIR / "agent_client.py", KIT_DIR / "fake_server.py")
PYTHON_FILES = SOURCE_FILES + (KIT_DIR / "test_agent_client.py", KIT_DIR / "check_agent_client.py")
FORBIDDEN_IMPORTS = ("requests", "httpx", "aiohttp", "urllib3", "subprocess", "docker")


def static_errors() -> list[str]:
    errors: list[str] = []
    actual = {path.name for path in KIT_DIR.iterdir() if path.is_file()}
    missing = sorted(EXPECTED_FILES - actual)
    if missing:
        errors.append("missing files in the approved five-path lane: " + ", ".join(missing))
    unexpected = sorted(actual - EXPECTED_FILES)
    if unexpected:
        errors.append("unexpected files in the approved five-path lane: " + ", ".join(unexpected))
    for path in SOURCE_FILES:
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_IMPORTS:
            if re.search(rf"(?:from|import)\s+{re.escape(forbidden)}\b", source):
                errors.append(f"non-stdlib or process dependency in {path.name}: {forbidden}")
        if "logging." in source or "logger." in source:
            errors.append(f"logging API present in {path.name}")
        if re.search(r"\bprint\s*\(", source):
            errors.append(f"printing/logging present in {path.name}")
        if re.search(r"Bearer\s+(?:eyJ|cnd_[A-Za-z0-9_-]{8,}|cng_[A-Za-z0-9_-]{8,})", source):
            errors.append(f"credential-like literal present in {path.name}")
    fake = (KIT_DIR / "fake_server.py").read_text(encoding="utf-8")
    for marker in (
        '("127.0.0.1", 0)',
        "shutdown()",
        "server_close()",
        '"/agent-readme.md"',
        "_fixture_mcp_tools",
        "if body is None:\n            return",
    ):
        if marker not in fake:
            errors.append(f"fake server safety marker is missing: {marker}")
    client = (KIT_DIR / "agent_client.py").read_text(encoding="utf-8")
    for marker in (
        "class UrllibTransport",
        "class LiveWritesDisabled",
        "class LostAcknowledgement",
        "retry_lost_ack",
        "STRONG_ETAG_RE",
        "MCP_TOOL_SCHEMA_EXPECTATIONS",
        "_validate_mcp_tool_inventory",
        "_validate_mcp_arguments",
        "_validate_a2a_arguments",
        "_validate_agent_readme",
    ):
        if marker not in client:
            errors.append(f"client safety marker is missing: {marker}")
    readme = (KIT_DIR / "README.md").read_text(encoding="utf-8")
    for marker in (
        "/agent-readme.md",
        "tools/list",
        "additionalProperties: false",
        "authority boundaries",
        "action's fields and limits",
    ):
        if marker not in readme:
            errors.append(f"README safety marker is missing: {marker}")
    from agent_client import current_byte_parity_errors

    errors.extend(current_byte_parity_errors(REPOSITORY))
    return errors


def compile_errors() -> list[str]:
    errors: list[str] = []
    for path in PYTHON_FILES:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            errors.append(f"compile failed for {path.name}: line {exc.lineno}")
    return errors


def test_errors() -> list[str]:
    sys.path.insert(0, str(KIT_DIR))
    suite = unittest.defaultTestLoader.discover(
        str(KIT_DIR), pattern="test_*.py", top_level_dir=str(KIT_DIR)
    )
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=0).run(suite)
    if result.testsRun == 0:
        return ["hermetic unittest suite discovered zero tests"]
    if result.wasSuccessful():
        return []
    failures = [f"failure: {case.id()}" for case, _traceback in result.failures]
    failures.extend(f"error: {case.id()}" for case, _traceback in result.errors)
    return [
        f"hermetic unittest failures: {result.testsRun} run, {len(result.failures)} failures, {len(result.errors)} errors",
        *failures,
    ]


def main() -> int:
    errors = static_errors() + compile_errors() + test_errors()
    if errors:
        for error in errors:
            sys.stderr.write(f"check_agent_client: FAIL: {error}\n")
        return 1
    sys.stdout.write("check_agent_client: PASS: five-path safety/parity/compile/hermetic gate\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
