"""Behavioral tests for deploy/with-network-secrets.sh.

The wrapper is the last guard before production network secrets are resolved,
so it is exercised end to end under bash with a stub `gringotts` on PATH. The
stub records its arguments instead of resolving anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "with-network-secrets.sh"
PLAINTEXT_SECRET = "hunter2-plaintext-value"


def _bash() -> str | None:
    bash = shutil.which("bash")
    if bash is None:
        return None
    # On Windows, System32\bash.exe is the WSL launcher, which cannot see this
    # checkout's paths; only a Git for Windows or MSYS bash is usable here.
    if sys.platform == "win32" and Path(bash).parent.name.lower() == "system32":
        return None
    return bash


BASH = _bash()


@unittest.skipIf(BASH is None, "bash is not available")
class WithNetworkSecretsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.temp = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        stub_dir = self.temp / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "gringotts"
        stub.write_bytes(b'#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$STUB_ARGS_FILE"\n')
        stub.chmod(0o755)
        self.args_file = self.temp / "gringotts-args.txt"
        self.env = dict(os.environ)
        # Prepend with the platform separator; bash converts a Windows PATH itself.
        self.env["PATH"] = str(stub_dir) + os.pathsep + self.env.get("PATH", "")
        self.env["STUB_ARGS_FILE"] = self.args_file.as_posix()

    def run_wrapper(self, env_file_text: str | None, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if env_file_text is not None:
            env_file = self.temp / "gringotts.env"
            env_file.write_bytes(env_file_text.encode("utf-8"))
            env["GRINGOTTS_ENV_FILE"] = env_file.as_posix()
        else:
            env["GRINGOTTS_ENV_FILE"] = (self.temp / "missing.env").as_posix()
        assert BASH is not None
        return subprocess.run(
            [BASH, SCRIPT.as_posix(), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_no_arguments_prints_usage_and_exits_64(self) -> None:
        for args in ((), ("echo", "ok"), ("--",)):
            with self.subTest(args=args):
                result = self.run_wrapper("A=gringotts://apps/a\n", *args)
                self.assertEqual(result.returncode, 64)
                self.assertIn("usage: deploy/with-network-secrets.sh -- <command> [args...]", result.stderr)
                self.assertFalse(self.args_file.exists())

    def test_missing_env_file_is_refused(self) -> None:
        result = self.run_wrapper(None, "--", "echo", "ok")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is missing", result.stderr)
        self.assertFalse(self.args_file.exists())

    def test_plaintext_value_is_refused_without_echoing_it(self) -> None:
        text = f"# operator notes\n\nCONNECTMD_NETWORK_DATABASE_URL=gringotts://apps/db\nPGPASSWORD={PLAINTEXT_SECRET}\n"
        result = self.run_wrapper(text, "--", "echo", "ok")
        self.assertEqual(result.returncode, 1)
        self.assertIn("line 4 is not a gringotts:// reference", result.stderr)
        self.assertNotIn(PLAINTEXT_SECRET, result.stderr)
        self.assertNotIn(PLAINTEXT_SECRET, result.stdout)
        self.assertFalse(self.args_file.exists())

    def test_malformed_reference_lines_are_refused(self) -> None:
        for line in (
            "lowercase=gringotts://apps/db",
            "export CONNECTMD_NETWORK_DATABASE_URL=gringotts://apps/db",
            "CONNECTMD_NETWORK_DATABASE_URL = gringotts://apps/db",
            "CONNECTMD_NETWORK_DATABASE_URL=gringotts://apps/db extra",
            "CONNECTMD_NETWORK_DATABASE_URL=gringotts://",
            "CONNECTMD_NETWORK_DATABASE_URL=",
            "not an assignment",
        ):
            with self.subTest(line=line):
                result = self.run_wrapper(line + "\n", "--", "echo", "ok")
                self.assertEqual(result.returncode, 1)
                self.assertIn("line 1 is not a gringotts:// reference", result.stderr)
                self.assertFalse(self.args_file.exists())

    def test_connection_string_is_refused(self) -> None:
        text = "CONNECTMD_NETWORK_DATABASE_URL=postgres://user:pass@db.example/connectmd\n"
        result = self.run_wrapper(text, "--", "echo", "ok")
        self.assertEqual(result.returncode, 1)
        self.assertIn("real connection strings", result.stderr)
        self.assertNotIn("user:pass", result.stderr)
        self.assertFalse(self.args_file.exists())

    def test_references_only_file_reaches_gringotts(self) -> None:
        text = (
            "# references only\r\n"
            "\r\n"
            "CONNECTMD_NETWORK_DATABASE_URL=gringotts://apps/connectmd/network-database-url\r\n"
            "  # indented comment\n"
            "CONNECTMD_NETWORK_ORIGIN=gringotts://apps/connectmd/origin_v2.1"
        )
        result = self.run_wrapper(text, "--", "echo", "ok")
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = self.args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            recorded,
            ["run", "--env-file", (self.temp / "gringotts.env").as_posix(), "--", "echo", "ok"],
        )

    def test_shipped_example_passes_the_guard(self) -> None:
        example = (REPO_ROOT / "deploy" / "gringotts.env.example").read_text(encoding="utf-8")
        result = self.run_wrapper(example, "--", "true")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
