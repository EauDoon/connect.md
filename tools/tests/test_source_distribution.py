from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import secret_scan
from tools import source_distribution as distribution

COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeGit:
    def __init__(
        self, entries: list[tuple[str, int, str, bytes]], *, status: bytes = b""
    ) -> None:
        self.entries = entries
        self.status = status
        self.calls: list[tuple[str, ...]] = []
        self.blobs = {object_id: data for _, _, object_id, data in entries}

    def __call__(self, _repository: Path, arguments: tuple[str, ...]) -> bytes:
        self.calls.append(arguments)
        if arguments[:2] == ("rev-parse", "--verify"):
            return (COMMIT + "\n").encode()
        if arguments[:1] == ("status",):
            return self.status
        if arguments[:1] == ("ls-tree",):
            return b"".join(
                f"{mode:06o} blob {object_id}\t{path}".encode() + b"\0"
                for path, mode, object_id, _ in self.entries
            )
        if arguments[:2] == ("cat-file", "blob"):
            return self.blobs[arguments[2]]
        raise AssertionError(arguments)


def entry(
    path: str,
    data: bytes = b"source\n",
    mode: int = 0o100644,
    object_id: str = "a" * 40,
):
    return path, mode, object_id, data


def secret_payloads(secret: bytes) -> tuple[tuple[str, bytes], ...]:
    """Inputs that the scanner must inspect as raw bytes, not classify as text."""

    return (
        ("ordinary", secret),
        ("over-five-megabytes", b"x" * (5 * 1024 * 1024 + 1) + b"\n" + secret),
        ("nul-prefixed", b"\0" + secret),
    )


class SourceDistributionTests(unittest.TestCase):
    def test_export_rejects_missing_head_before_tree_access(self) -> None:
        fake = FakeGit([])

        def missing_head(_repository: Path, arguments: tuple[str, ...]) -> bytes:
            fake.calls.append(arguments)
            raise distribution.SourceDistributionError("Git revision missing")

        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaises(distribution.SourceDistributionError),
        ):
            distribution.export_distribution(
                Path(temporary) / "repo",
                Path(temporary) / "out" / "source.tar.gz",
                git_runner=missing_head,
            )
        self.assertEqual(fake.calls, [("rev-parse", "--verify", "HEAD^{commit}")])

    def test_export_rejects_dirty_head(self) -> None:
        fake = FakeGit([entry("README.md")], status=b"?? untracked.txt\n")
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(distribution.SourceDistributionError, "not clean"),
        ):
            distribution.export_distribution(
                Path(temporary) / "repo",
                Path(temporary) / "out" / "source.tar.gz",
                git_runner=fake,
            )

    def test_export_allows_root_license(self) -> None:
        fake = FakeGit(
            [
                entry("LICENSE", b"Apache License\n"),
                entry("README.md", b"# Connect.md\n"),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = distribution.export_distribution(
                Path(temporary) / "repo",
                Path(temporary) / "out" / "source.tar.gz",
                git_runner=fake,
            )
        self.assertEqual(result.file_count, 2)

    def test_export_rejects_forbidden_tracked_paths_and_modes(self) -> None:
        cases = [
            [entry("README.md"), entry("apps/web/node_modules/package/index.js")],
            [entry("README.md"), entry(".env")],
            [entry("README.md", mode=0o120000)],
            [entry("README.md"), entry("apps/web/test-results/report.json")],
            [entry("README.md"), entry("apps/web/playwright-report/index.html")],
            [entry("README.md"), entry("apps/web/public/monaco/loader.js")],
            [entry("README.md"), entry("apps/web/.vitest/cache.json")],
            [entry("README.md"), entry("apps/web/.turbo/cache.json")],
            [entry("README.md"), entry("apps/web/.nyc_output/coverage.json")],
            [entry("README.md"), entry("apps/web/.eslintcache")],
        ]
        for entries in cases:
            with self.subTest(entries=entries):
                fake = FakeGit(entries)
                with (
                    tempfile.TemporaryDirectory() as temporary,
                    self.assertRaises(distribution.SourceDistributionError),
                ):
                    distribution.export_distribution(
                        Path(temporary) / "repo",
                        Path(temporary) / "out" / "source.tar.gz",
                        git_runner=fake,
                    )

    def test_shared_secret_policy_accepts_safe_text_and_rejects_every_raw_secret_blob(
        self,
    ) -> None:
        safe = b"ordinary source text without credentials\n"
        self.assertEqual(secret_scan.find_secret_labels(safe), ())

        synthetic_secret = b"sk_" + b"live_" + b"a" * 20
        for label, payload in secret_payloads(synthetic_secret):
            with self.subTest(label=label):
                self.assertEqual(
                    secret_scan.find_secret_labels(payload),
                    ("Stripe secret", "Clerk secret"),
                )

    def test_cli_scans_every_raw_secret_blob_without_echoing_its_contents(
        self,
    ) -> None:
        synthetic_secret = b"sk_" + b"live_" + b"d" * 20
        for label, payload in secret_payloads(synthetic_secret):
            with self.subTest(label=label):
                output = io.StringIO()
                with (
                    patch.object(
                        secret_scan.subprocess,
                        "check_output",
                        return_value=b"credential.bin\0",
                    ),
                    patch.object(secret_scan.Path, "read_bytes", return_value=payload),
                    redirect_stdout(output),
                ):
                    self.assertEqual(secret_scan.main(), 1)
                self.assertIn(
                    "credential.bin: possible Stripe secret", output.getvalue()
                )
                self.assertIn(
                    "credential.bin: possible Clerk secret", output.getvalue()
                )
                self.assertNotIn(synthetic_secret.decode(), output.getvalue())

    def test_export_rejects_secret_material_without_leaking_path_or_content(
        self,
    ) -> None:
        synthetic_secret = b"sk_" + b"live_" + b"b" * 20
        for label, payload in secret_payloads(synthetic_secret):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                fake = FakeGit(
                    [
                        entry("README.md"),
                        entry("apps/api/app/config.py", payload),
                    ]
                )
                with self.assertRaisesRegex(
                    distribution.SourceDistributionError, "secret material"
                ) as caught:
                    distribution.export_distribution(
                        Path(temporary) / "repo",
                        Path(temporary) / "source.tar.gz",
                        git_runner=fake,
                    )
                self.assertNotIn("config.py", str(caught.exception))
                self.assertNotIn(synthetic_secret.decode(), str(caught.exception))

    def test_verify_rejects_secret_material_from_a_self_consistent_archive(
        self,
    ) -> None:
        synthetic_secret = b"sk_" + b"live_" + b"c" * 20
        for label, payload in secret_payloads(synthetic_secret):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                tracked = [
                    distribution.TrackedFile(
                        "apps/api/app/config.py", 0o100644, "d" * 40, payload
                    )
                ]
                archive_path = Path(temporary) / "secret.tar.gz"
                archive_bytes = distribution._build_archive(tracked)
                archive_path.write_bytes(archive_bytes)
                manifest_bytes = distribution._canonical_json(
                    distribution._manifest_payload(
                        commit=COMMIT,
                        archive_name=archive_path.name,
                        archive_sha256=distribution.hashlib.sha256(
                            archive_bytes
                        ).hexdigest(),
                        files=tracked,
                    )
                )
                manifest_path = Path(str(archive_path) + ".manifest.json")
                manifest_path.write_bytes(manifest_bytes)
                digest_path = Path(str(archive_path) + ".sha256")
                digest_path.write_text(
                    f"{distribution.hashlib.sha256(archive_bytes).hexdigest()}  {archive_path.name}\n"
                    f"{distribution.hashlib.sha256(manifest_bytes).hexdigest()}  {manifest_path.name}\n",
                    encoding="ascii",
                )
                with self.assertRaisesRegex(
                    distribution.SourceDistributionError, "secret material"
                ) as caught:
                    distribution.verify_distribution(archive_path)
                self.assertNotIn("config.py", str(caught.exception))
                self.assertNotIn(synthetic_secret.decode(), str(caught.exception))

    def test_export_is_deterministic_and_verifies_sidecars(self) -> None:
        fake = FakeGit(
            [
                entry("apps/api/app/main.py", b"print('ok')\n", object_id="b" * 40),
                entry("README.md", b"# Connect.md\n", object_id="a" * 40),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repo"
            first = distribution.export_distribution(
                repository,
                Path(temporary) / "one" / "source.tar.gz",
                git_runner=fake,
            )
            second = distribution.export_distribution(
                repository,
                Path(temporary) / "two" / "source.tar.gz",
                git_runner=fake,
            )
            self.assertEqual(first.archive.read_bytes(), second.archive.read_bytes())
            self.assertEqual(first.manifest.read_bytes(), second.manifest.read_bytes())
            self.assertEqual(first.digest.read_bytes(), second.digest.read_bytes())
            verified = distribution.verify_distribution(
                first.archive,
                repository=repository,
                expected_commit=COMMIT,
                expected_archive_sha256=first.archive_sha256,
                expected_manifest_sha256=first.manifest_sha256,
                git_runner=fake,
            )
            self.assertEqual(verified.file_count, 2)
            self.assertEqual(
                verified.source_bytes, len(b"print('ok')\n") + len(b"# Connect.md\n")
            )

    def test_export_rejects_output_inside_repository(self) -> None:
        fake = FakeGit([entry("README.md")])
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repo"
            with self.assertRaisesRegex(
                distribution.SourceDistributionError, "outside"
            ):
                distribution.export_distribution(
                    repository,
                    repository / "source.tar.gz",
                    git_runner=fake,
                )

    def test_verify_rejects_tampered_manifest_and_archive_member(self) -> None:
        fake = FakeGit([entry("README.md")])
        with tempfile.TemporaryDirectory() as temporary:
            archive = distribution.export_distribution(
                Path(temporary) / "repo",
                Path(temporary) / "source.tar.gz",
                git_runner=fake,
            ).archive
            manifest_path = Path(str(archive) + ".manifest.json")
            original_manifest = manifest_path.read_bytes()
            payload = json.loads(original_manifest)
            payload["commit"] = "f" * 40
            manifest_path.write_bytes(distribution._canonical_json(payload))
            with self.assertRaisesRegex(
                distribution.SourceDistributionError, "digest sidecar"
            ):
                distribution.verify_distribution(archive)
            manifest_path.write_bytes(original_manifest)

            raw = bytearray(archive.read_bytes())
            raw[-1] ^= 1
            archive.write_bytes(raw)
            with self.assertRaises(distribution.SourceDistributionError):
                distribution.verify_distribution(archive)

    def test_verify_rejects_symlink_member_even_with_a_matching_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "unsafe.tar.gz"
            manifest_path = Path(str(archive_path) + ".manifest.json")
            digest_path = Path(str(archive_path) + ".sha256")
            tar_buffer = io.BytesIO()
            with tarfile.open(
                fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT
            ) as archive:
                info = tarfile.TarInfo(distribution.ARCHIVE_PREFIX + "README.md")
                info.type = tarfile.SYMTYPE
                info.linkname = "outside"
                info.mode = 0o777
                info.uid = 0
                info.gid = 0
                info.mtime = 0
                archive.addfile(info)
            archive_bytes = distribution._deterministic_gzip(tar_buffer.getvalue())
            archive_path.write_bytes(archive_bytes)
            manifest_bytes = distribution._canonical_json(
                distribution._manifest_payload(
                    commit=COMMIT,
                    archive_name=archive_path.name,
                    archive_sha256=distribution.hashlib.sha256(
                        archive_bytes
                    ).hexdigest(),
                    files=[
                        distribution.TrackedFile("README.md", 0o100644, "c" * 40, b"")
                    ],
                )
            )
            manifest_path.write_bytes(manifest_bytes)
            digest_path.write_text(
                f"{distribution.hashlib.sha256(archive_bytes).hexdigest()}  {archive_path.name}\n"
                f"{distribution.hashlib.sha256(manifest_bytes).hexdigest()}  {manifest_path.name}\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                distribution.SourceDistributionError, "non-regular"
            ):
                distribution.verify_distribution(archive_path)

    def test_verify_rejects_extra_extracted_files(self) -> None:
        fake = FakeGit([entry("README.md")])
        with tempfile.TemporaryDirectory() as temporary:
            archive = distribution.export_distribution(
                Path(temporary) / "repo",
                Path(temporary) / "source.tar.gz",
                git_runner=fake,
            ).archive
            extracted = Path(temporary) / "extracted"
            extracted.mkdir()
            (extracted / "README.md").write_bytes(b"source\n")
            (extracted / "unexpected.txt").write_bytes(b"no\n")
            with self.assertRaisesRegex(
                distribution.SourceDistributionError, "forbidden"
            ):
                distribution.verify_distribution(archive, extract_dir=extracted)

    def test_ci_exports_and_verifies_the_clean_committed_source(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        export = (
            'python tools/source_distribution.py export --repo . --output "$archive"'
        )
        verify = (
            'python tools/source_distribution.py verify --archive "$archive" --repo .'
        )
        self.assertIn('archive="$RUNNER_TEMP/connectmd-source.tar.gz"', workflow)
        self.assertIn(export, workflow)
        self.assertIn(verify, workflow)
        self.assertLess(workflow.index(export), workflow.index(verify))


if __name__ == "__main__":
    unittest.main()
