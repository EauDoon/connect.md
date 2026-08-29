from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools import check_dependency_sboms as checker


class DependencySbomCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_json(self, name: str, value: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _write_api_fixture(self) -> tuple[Path, Path, dict[str, object]]:
        lock = self.root / "requirements.lock"
        lock.write_text("alpha_pkg==1.0\nbeta==2.0\n", encoding="utf-8")
        payload: dict[str, object] = {
            "$schema": "https://cyclonedx.org/schema/bom-1.4.schema.json",
            "bomFormat": "CycloneDX",
            "specVersion": "1.4",
            "serialNumber": "urn:uuid:volatile",
            "metadata": {"timestamp": "2026-08-13T00:00:00Z"},
            "components": [
                {"type": "library", "name": "alpha-pkg", "version": "1.0"},
                {"type": "library", "name": "beta", "version": "2.0"},
            ],
        }
        return lock, self._write_json("api.json", payload), payload

    def _write_web_fixture(self) -> tuple[Path, Path, dict[str, object]]:
        lock = self.root / "package-lock.json"
        lock.write_text(
            json.dumps(
                {
                    "lockfileVersion": 3,
                    "packages": {
                        "": {"name": "@connectmd/web", "version": "0.1.0"},
                        "node_modules/alpha": {"version": "1.0.0"},
                        "node_modules/@scope/beta": {"version": "2.0.0"},
                    },
                }
            ),
            encoding="utf-8",
        )
        payload: dict[str, object] = {
            "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "serialNumber": "urn:uuid:volatile",
            "metadata": {
                "timestamp": "2026-08-13T00:00:00Z",
                "component": {
                    "type": "application",
                    "name": "@connectmd/web",
                    "version": "0.1.0",
                },
            },
            "components": [
                {"type": "library", "name": "alpha", "version": "1.0.0"},
                {"type": "library", "name": "@scope/beta", "version": "2.0.0"},
            ],
        }
        return lock, self._write_json("web.json", payload), payload

    def test_api_lock_coverage_and_receipt_are_valid(self) -> None:
        lock, sbom, _ = self._write_api_fixture()

        receipt = checker.validate_sbom("api", lock, sbom)

        self.assertEqual(receipt["format"], "connectmd-dependency-sbom-receipt-v1")
        self.assertEqual(receipt["kind"], "api")
        self.assertEqual(receipt["lockfile"], "apps/api/requirements.lock")
        self.assertEqual(receipt["component_count"], 2)
        self.assertRegex(receipt["lock_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(receipt["sbom_sha256"], r"^[0-9a-f]{64}$")

    def test_web_lock_root_and_dependency_coverage_are_valid(self) -> None:
        lock, sbom, _ = self._write_web_fixture()

        receipt = checker.validate_sbom("web", lock, sbom)

        self.assertEqual(receipt["spec_version"], "1.5")
        self.assertEqual(receipt["component_count"], 2)

    def test_web_root_purl_alias_is_bound_to_lock_root(self) -> None:
        lock, _, payload = self._write_web_fixture()
        payload["metadata"]["component"] = {
            "type": "application",
            "name": "web",
            "version": "0.1.0",
            "purl": "pkg:npm/%40connectmd/web@0.1.0",
        }
        sbom = self._write_json("web-purl.json", payload)

        checker.validate_sbom("web", lock, sbom)

        payload["metadata"]["component"]["purl"] = "pkg:npm/%40other/web@0.1.0"
        wrong_purl = self._write_json("web-wrong-purl.json", payload)
        with self.assertRaises(checker.SbomValidationError):
            checker.validate_sbom("web", lock, wrong_purl)

    def test_web_allows_nested_duplicate_identity_bound_to_lock_paths(self) -> None:
        lock, _, payload = self._write_web_fixture()
        lock_payload = json.loads(lock.read_text(encoding="utf-8"))
        lock_payload["packages"]["node_modules/parent/node_modules/alpha"] = {
            "version": "1.0.0"
        }
        lock.write_text(json.dumps(lock_payload), encoding="utf-8")
        candidate = copy.deepcopy(payload)
        candidate["components"] = [
            {
                "type": "library",
                "name": "alpha",
                "version": "1.0.0",
                "properties": [
                    {
                        "name": "cdx:npm:package:path",
                        "value": "node_modules/alpha",
                    }
                ],
            },
            {
                "type": "library",
                "name": "alpha",
                "version": "1.0.0",
                "properties": [
                    {
                        "name": "cdx:npm:package:path",
                        "value": "node_modules/parent/node_modules/alpha",
                    }
                ],
            },
            candidate["components"][1],
        ]
        sbom = self._write_json("web-nested-duplicate.json", candidate)

        receipt = checker.validate_sbom("web", lock, sbom)

        self.assertEqual(receipt["component_count"], 2)
        candidate["components"][1]["properties"][0]["value"] = (
            "node_modules/missing/node_modules/alpha"
        )
        invalid = self._write_json("web-unbound-duplicate.json", candidate)
        with self.assertRaisesRegex(checker.SbomValidationError, "lockfile"):
            checker.validate_sbom("web", lock, invalid)
        candidate["components"][1]["properties"][0]["value"] = "node_modules/alpha"
        repeated_path = self._write_json("web-repeated-path.json", candidate)
        with self.assertRaisesRegex(checker.SbomValidationError, "duplicate package"):
            checker.validate_sbom("web", lock, repeated_path)
        candidate["components"][1].pop("properties")
        missing_path = self._write_json("web-missing-path.json", candidate)
        with self.assertRaisesRegex(checker.SbomValidationError, "package paths"):
            checker.validate_sbom("web", lock, missing_path)

    def test_receipt_hash_ignores_volatile_and_advisory_fields(self) -> None:
        lock, sbom, payload = self._write_api_fixture()
        first = checker.validate_sbom("api", lock, sbom)
        changed = copy.deepcopy(payload)
        changed["serialNumber"] = "urn:uuid:different"
        changed["metadata"] = {"timestamp": "2030-01-01T00:00:00Z"}
        changed["components"][0]["bom-ref"] = "urn:uuid:component-different"
        changed["components"][0]["purl"] = "pkg:pypi/alpha-pkg@1.0"
        changed["vulnerabilities"] = [
            {
                "id": "CVE-2099-0001",
                "ratings": [{"severity": "high"}],
            }
        ]
        changed_path = self._write_json("changed.json", changed)

        second = checker.validate_sbom("api", lock, changed_path)

        self.assertEqual(first["sbom_sha256"], second["sbom_sha256"])

    def test_receipt_is_canonical_json(self) -> None:
        lock, sbom, _ = self._write_api_fixture()
        receipt = checker.validate_sbom("api", lock, sbom)
        output = self.root / "receipt.json"

        checker.write_receipt(output, receipt)

        self.assertEqual(
            output.read_bytes(),
            (
                json.dumps(
                    receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                + "\n"
            ).encode(),
        )

    def test_rejects_missing_extra_duplicate_and_image_components(self) -> None:
        lock, _sbom, payload = self._write_api_fixture()
        cases = {
            "missing": [payload["components"][0]],
            "extra": [
                *payload["components"],
                {"type": "library", "name": "connectmd-api-image", "version": "latest"},
            ],
            "duplicate": [*payload["components"], payload["components"][0]],
            "image": [
                *payload["components"],
                {"type": "application", "name": "connectmd-api", "version": "local"},
            ],
        }
        for name, components in cases.items():
            with self.subTest(name=name):
                candidate = copy.deepcopy(payload)
                candidate["components"] = components
                candidate_path = self._write_json(f"{name}.json", candidate)
                with self.assertRaises(checker.SbomValidationError):
                    checker.validate_sbom("api", lock, candidate_path)

    def test_rejects_malformed_schema_and_spec(self) -> None:
        lock, _sbom, payload = self._write_api_fixture()
        for key, value in (
            ("$schema", "https://example.test/bom.json"),
            ("bomFormat", "SPDX"),
            ("specVersion", "1.3"),
        ):
            with self.subTest(key=key):
                candidate = copy.deepcopy(payload)
                candidate[key] = value
                candidate_path = self._write_json(f"bad-{key}.json", candidate)
                with self.assertRaises(checker.SbomValidationError):
                    checker.validate_sbom("api", lock, candidate_path)

    def test_rejects_unpinned_python_lock_entry(self) -> None:
        lock, sbom, _ = self._write_api_fixture()
        lock.write_text("alpha_pkg>=1.0\n-r nested.txt\n", encoding="utf-8")

        with self.assertRaises(checker.SbomValidationError):
            checker.validate_sbom("api", lock, sbom)

    def test_rejects_wrong_web_root_and_lock_shape(self) -> None:
        lock, sbom, payload = self._write_web_fixture()
        wrong_root = copy.deepcopy(payload)
        wrong_root["metadata"]["component"]["version"] = "9.9.9"
        wrong_root_path = self._write_json("wrong-root.json", wrong_root)
        with self.assertRaises(checker.SbomValidationError):
            checker.validate_sbom("web", lock, wrong_root_path)

        lock.write_text(
            json.dumps(
                {
                    "lockfileVersion": 2,
                    "packages": {
                        "": {"name": "@connectmd/web", "version": "0.1.0"},
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(checker.SbomValidationError):
            checker.validate_sbom("web", lock, sbom)


if __name__ == "__main__":
    unittest.main()
