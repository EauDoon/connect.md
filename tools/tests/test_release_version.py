from __future__ import annotations

import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tools import release_version as rv

REPO_ROOT = Path(__file__).resolve().parents[2]
URL = "https://github.com/EauDoon/connect.md"

PACKAGE_JSON = """{
  "name": "@connectmd/web",
  "version": "1.2.3",
  "private": true,
  "engines": {
    "node": "22.x"
  },
  "devDependencies": {
    "vitest": "4.1.11"
  }
}
"""

PACKAGE_LOCK = """{
  "name": "@connectmd/web",
  "version": "1.2.3",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "@connectmd/web",
      "version": "1.2.3",
      "devDependencies": {
        "vitest": "4.1.11"
      },
      "engines": {
        "node": "22.x"
      }
    },
    "node_modules/vitest": {
      "version": "4.1.11",
      "dev": true
    }
  }
}
"""

PYPROJECT = """[build-system]
requires = ["hatchling>=1.25,<2"]
build-backend = "hatchling.build"

[project]
name = "connectmd-api"
version = "1.2.3"
dependencies = [
  "fastapi>=0.115,<1",
]

[tool.example]
version = "9.9.9"
"""

API_INIT = '''"""connect.md API package."""

__version__ = "1.2.3"
'''

CHANGELOG = f"""# Changelog

## [Unreleased]

### Added

- Something not yet released.

## [1.2.3] - 2026-10-01

### Fixed

- A fix.

## [1.2.2] - 2026-09-01

### Added

- An older addition.

[unreleased]: {URL}/compare/v1.2.3...HEAD
[1.2.3]: {URL}/compare/v1.2.2...v1.2.3
[1.2.2]: {URL}/releases/tag/v1.2.2
"""


def make_tree(root: Path) -> Path:
    files = {
        "VERSION": "1.2.3\n",
        "CHANGELOG.md": CHANGELOG,
        "apps/web/package.json": PACKAGE_JSON,
        "apps/web/package-lock.json": PACKAGE_LOCK,
        "apps/api/pyproject.toml": PYPROJECT,
        "apps/api/app/__init__.py": API_INIT,
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def tree(self, files: dict[str, str] | None = None) -> Path:
        make_tree(self.root)
        for relative, text in (files or {}).items():
            (self.root / relative).write_bytes(text.encode("utf-8"))
        return self.root

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = rv.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def test_consistent_tree_passes(self) -> None:
        self.tree()
        self.assertEqual(rv.check(self.root), [])
        self.assertEqual(rv.check(self.root, "v1.2.3"), [])
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 0)
        self.assertIn("consistent at 1.2.3", out)

    def test_package_json_mismatch_fails(self) -> None:
        self.tree({"apps/web/package.json": PACKAGE_JSON.replace('"version": "1.2.3"', '"version": "1.2.2"')})
        problems = rv.check(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn('apps/web/package.json "version" is \'1.2.2\'', problems[0])

    def test_lock_root_package_mismatch_fails(self) -> None:
        lock = PACKAGE_LOCK.replace('      "version": "1.2.3",', '      "version": "0.1.0",')
        self.tree({"apps/web/package-lock.json": lock})
        problems = rv.check(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn('packages[""].version', problems[0])

    def test_pyproject_and_api_mismatches_fail(self) -> None:
        self.tree({
            "apps/api/pyproject.toml": PYPROJECT.replace('version = "1.2.3"', 'version = "0.1.0"'),
            "apps/api/app/__init__.py": API_INIT.replace("1.2.3", "0.3.0"),
        })
        problems = "\n".join(rv.check(self.root))
        self.assertIn("apps/api/pyproject.toml [project] version is '0.1.0'", problems)
        self.assertIn("apps/api/app/__init__.py __version__ is '0.3.0'", problems)

    def test_missing_dated_section_fails(self) -> None:
        for changelog in (
            CHANGELOG.replace("## [1.2.3] - 2026-10-01", "## [1.2.3]"),
            CHANGELOG.replace("## [1.2.3] - 2026-10-01", "## [1.2.3] - 2026-13-45"),
            CHANGELOG.replace("## [1.2.3] - 2026-10-01\n\n### Fixed\n\n- A fix.\n\n", ""),
        ):
            with self.subTest(changelog=changelog[30:80]):
                self.tree({"CHANGELOG.md": changelog})
                problems = "\n".join(rv.check(self.root))
                self.assertIn("1.2.3", problems)
                self.assertTrue("dated" in problems or "not a valid date" in problems, problems)

    def test_stale_unreleased_link_and_missing_version_link_fail(self) -> None:
        stale = CHANGELOG.replace("compare/v1.2.3...HEAD", "compare/v1.2.2...HEAD")
        self.tree({"CHANGELOG.md": stale})
        self.assertIn("the unreleased link must be", "\n".join(rv.check(self.root)))
        missing = CHANGELOG.replace(f"[1.2.3]: {URL}/compare/v1.2.2...v1.2.3\n", "")
        self.tree({"CHANGELOG.md": missing})
        self.assertIn("missing the '[1.2.3]:", "\n".join(rv.check(self.root)))
        no_unreleased = CHANGELOG.replace("## [Unreleased]\n\n### Added\n\n- Something not yet released.\n\n", "")
        self.tree({"CHANGELOG.md": no_unreleased})
        self.assertIn("missing the '## [Unreleased]' section", "\n".join(rv.check(self.root)))

    def test_tag_mismatch_fails(self) -> None:
        self.tree()
        self.assertEqual(rv.check(self.root, "v1.2.4"), ["tag 'v1.2.4' does not match VERSION '1.2.3' (expected 'v1.2.3')"])
        code, _, err = self.run_cli("check", "--tag", "v9.9.9")
        self.assertEqual(code, 1)
        self.assertIn("tag 'v9.9.9'", err)

    def test_malformed_version_file_fails(self) -> None:
        self.tree({"VERSION": "1.2\n"})
        self.assertEqual(rv.check(self.root), ["VERSION: '1.2' is not MAJOR.MINOR.PATCH"])

    def test_set_round_trip_preserves_formatting(self) -> None:
        self.tree()
        before = {relative: (self.root / relative).read_bytes() for relative in (
            "apps/web/package.json", "apps/web/package-lock.json", "apps/api/pyproject.toml", "apps/api/app/__init__.py",
        )}
        rv.set_version(self.root, "2.0.0")
        self.assertEqual((self.root / "VERSION").read_bytes(), b"2.0.0\n")
        for relative, original in before.items():
            changed = (self.root / relative).read_bytes()
            # Only version strings change: same line count, same bytes elsewhere.
            self.assertEqual(changed.replace(b"2.0.0", b"1.2.3"), original, relative)
        self.assertEqual(json.loads((self.root / "apps/web/package-lock.json").read_text())["packages"]["node_modules/vitest"]["version"], "4.1.11")
        pyproject = tomllib.loads((self.root / "apps/api/pyproject.toml").read_text())
        self.assertEqual(pyproject["project"]["version"], "2.0.0")
        self.assertEqual(pyproject["tool"]["example"]["version"], "9.9.9")
        components = rv.component_versions(self.root)
        self.assertEqual(set(components.values()), {"2.0.0"})
        rv.set_version(self.root, "1.2.3")
        for relative, original in before.items():
            self.assertEqual((self.root / relative).read_bytes(), original, relative)

    def test_set_refuses_bad_input_without_touching_the_tree(self) -> None:
        self.tree({"apps/api/app/__init__.py": '"""No version here."""\n'})
        with self.assertRaises(rv.ReleaseMetadataError):
            rv.set_version(self.root, "2.0.0")
        self.assertEqual((self.root / "VERSION").read_bytes(), b"1.2.3\n")
        self.assertIn(b'"version": "1.2.3"', (self.root / "apps/web/package.json").read_bytes())
        with self.assertRaises(rv.ReleaseMetadataError):
            rv.set_version(self.root, "2.0")

    def test_notes_extract_one_section_without_link_definitions(self) -> None:
        self.tree()
        self.assertEqual(rv.release_notes(self.root, "v1.2.3"), "## [1.2.3] - 2026-10-01\n\n### Fixed\n\n- A fix.\n")
        self.assertEqual(rv.release_notes(self.root, "v1.2.2"), "## [1.2.2] - 2026-09-01\n\n### Added\n\n- An older addition.\n")
        output = self.root / "notes.md"
        code, _, _ = self.run_cli("notes", "--tag", "v1.2.3", "--output", str(output))
        self.assertEqual(code, 0)
        self.assertEqual(output.read_bytes(), b"## [1.2.3] - 2026-10-01\n\n### Fixed\n\n- A fix.\n")

    def test_notes_fail_on_an_empty_or_missing_section(self) -> None:
        empty = CHANGELOG.replace("## [1.2.3] - 2026-10-01\n\n### Fixed\n\n- A fix.\n", "## [1.2.3] - 2026-10-01\n")
        self.tree({"CHANGELOG.md": empty})
        with self.assertRaisesRegex(rv.ReleaseMetadataError, "section is empty"):
            rv.release_notes(self.root, "v1.2.3")
        with self.assertRaisesRegex(rv.ReleaseMetadataError, "no section for 7.7.7"):
            rv.release_notes(self.root, "v7.7.7")
        with self.assertRaisesRegex(rv.ReleaseMetadataError, "not a vMAJOR.MINOR.PATCH tag"):
            rv.release_notes(self.root, "1.2.3")

    def test_repository_metadata_is_consistent(self) -> None:
        self.assertEqual(rv.check(REPO_ROOT), [])


if __name__ == "__main__":
    unittest.main()
