"""Create and verify a deterministic, clean-HEAD Connect.md source archive.

The exporter reads blobs from a committed Git tree only.  It never packages the
working directory, and it refuses a missing HEAD, a dirty checkout, unexpected
tracked paths, non-regular Git entries, or secret/runtime/dependency paths.

The archive is accompanied by a JSON manifest and a two-line SHA-256 sidecar.
Verification rechecks the archive bytes, manifest binding, archive member types,
paths, order, modes, content hashes, and an extracted temporary tree.  A caller
can additionally bind verification to a clean local checkout or an expected
commit/digest without granting the verifier any write authority over Git.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    from tools.secret_scan import find_secret_labels
except ImportError:  # pragma: no cover - supports direct script execution
    from secret_scan import find_secret_labels

SCHEMA = "connect.md/source-distribution"
SCHEMA_VERSION = 1
ARCHIVE_FORMAT = "tar.gz"
ARCHIVE_PREFIX = "connectmd-source/"
MAX_SOURCE_FILES = 50_000
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_PATH_CHARS = 512

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FORBIDDEN_SUFFIXES = (
    ".bak",
    ".crt",
    ".db",
    ".key",
    ".log",
    ".pem",
    ".pfx",
    ".p12",
    ".secret",
    ".secrets",
    ".sqlite",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".zip",
)
_FORBIDDEN_COMPONENTS = frozenset(
    {
        ".cache",
        ".connectmd",
        ".git",
        ".mypy_cache",
        ".next",
        ".pnpm-store",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "backups",
        "build",
        "cache",
        "caches",
        "coverage",
        "data",
        "dist",
        "logs",
        "node_modules",
        ".eslintcache",
        ".nyc_output",
        ".turbo",
        ".vitest",
        "playwright-report",
        "receipts",
        "runtime",
        "secrets",
        "test-results",
        "temp",
        "tmp",
        "venv",
    }
)
_FORBIDDEN_NAMES = frozenset(
    {
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "service-account.json",
    }
)
_ALLOWED_ROOT_FILES = frozenset(
    {
        ".dockerignore",
        ".editorconfig",
        ".env.example",
        ".gitignore",
        "LICENSE",
        "README.md",
        "compose.prod.yaml",
        "compose.yaml",
    }
)
_ALLOWED_PREFIXES = (
    ".github/",
    "apps/",
    "docs/",
    "examples/",
    "infra/",
    "packages/",
    "tools/",
)
_ALLOWED_SPECIAL_PATHS = frozenset({"storage/README.md"})
_FORBIDDEN_PATH_PREFIXES = ("apps/web/public/monaco/",)


class SourceDistributionError(RuntimeError):
    """A bounded, fail-closed source distribution error."""


@dataclass(frozen=True)
class TrackedFile:
    path: str
    mode: int
    object_id: str
    data: bytes


@dataclass(frozen=True)
class DistributionResult:
    commit: str
    archive: Path
    manifest: Path
    digest: Path
    archive_sha256: str
    manifest_sha256: str
    file_count: int
    source_bytes: int


GitRunner = Callable[[Path, Sequence[str]], bytes]


def _run_git(repository: Path, arguments: Sequence[str]) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SourceDistributionError(
            "Git read failed: " + (detail[:160] if detail else "command rejected")
        )
    return completed.stdout


def _decode_utf8(value: bytes, label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceDistributionError(f"{label} is not valid UTF-8") from exc


def _validate_relative_path(path: str, *, allow_special: bool = True) -> None:
    if not path or len(path) > MAX_PATH_CHARS or "\\" in path:
        raise SourceDistributionError("source path is malformed")
    if path.startswith("/") or path.endswith("/") or "//" in path:
        raise SourceDistributionError("source path is malformed")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in path):
        raise SourceDistributionError("source path contains a control character")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SourceDistributionError("source path is not a normalized relative path")
    if unicodedata.normalize("NFC", path) != path:
        raise SourceDistributionError("source path is not Unicode-normalized")
    if allow_special and path in _ALLOWED_SPECIAL_PATHS:
        return


def _is_allowed_source_path(path: str) -> bool:
    _validate_relative_path(path)
    if path in _ALLOWED_SPECIAL_PATHS:
        return True
    if "/" not in path:
        return path in _ALLOWED_ROOT_FILES
    if not path.startswith(_ALLOWED_PREFIXES):
        return False
    lowered_path = path.casefold()
    if any(lowered_path.startswith(prefix) for prefix in _FORBIDDEN_PATH_PREFIXES):
        return False
    parts = path.split("/")
    lowered_parts = [part.casefold() for part in parts]
    if any(
        part in _FORBIDDEN_COMPONENTS or part.startswith(".connectmd-")
        for part in lowered_parts
    ):
        return False
    name = parts[-1]
    lowered_name = name.casefold()
    if lowered_name in _FORBIDDEN_NAMES:
        return False
    if lowered_name == ".env" or (
        lowered_name.startswith(".env.") and lowered_name != ".env.example"
    ):
        return False
    return not lowered_name.endswith(_FORBIDDEN_SUFFIXES)


def _path_sort_key(path: str) -> bytes:
    return path.encode("utf-8")


def _validate_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise SourceDistributionError(f"{label} is not a lowercase SHA-256 digest")
    return value


def _validate_commit(value: object, label: str = "commit") -> str:
    if not isinstance(value, str) or _COMMIT_RE.fullmatch(value) is None:
        raise SourceDistributionError(f"{label} is not a full lowercase commit")
    return value


def _read_clean_head(repository: Path, git_runner: GitRunner) -> str:
    commit = _decode_utf8(
        git_runner(repository, ("rev-parse", "--verify", "HEAD^{commit}")),
        "Git revision",
    ).strip()
    _validate_commit(commit)
    status = _decode_utf8(
        git_runner(
            repository,
            ("status", "--porcelain=v1", "--untracked-files=all"),
        ),
        "Git status",
    )
    if status:
        raise SourceDistributionError("working tree is not clean")
    return commit


def _parse_tree(
    repository: Path, commit: str, git_runner: GitRunner
) -> list[TrackedFile]:
    raw_tree = git_runner(repository, ("ls-tree", "-r", "-z", "--full-tree", commit))
    files: list[TrackedFile] = []
    seen_paths: set[str] = set()
    seen_normalized_paths: set[str] = set()
    total_bytes = 0
    for record in raw_tree.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode_bytes, kind_bytes, object_bytes = header.split(b" ", 2)
            mode = int(mode_bytes, 8)
            kind = kind_bytes.decode("ascii")
            object_id = object_bytes.decode("ascii")
            path = _decode_utf8(raw_path, "tracked path")
        except (UnicodeDecodeError, ValueError) as exc:
            raise SourceDistributionError("Git tree entry is malformed") from exc
        _validate_relative_path(path)
        if path in seen_paths:
            raise SourceDistributionError("Git tree contains a duplicate path")
        normalized_key = unicodedata.normalize("NFC", path).casefold()
        if normalized_key in seen_normalized_paths:
            raise SourceDistributionError(
                "Git tree contains a case or Unicode path collision"
            )
        seen_paths.add(path)
        seen_normalized_paths.add(normalized_key)
        if kind != "blob" or mode not in {0o100644, 0o100755}:
            raise SourceDistributionError("tracked source contains a non-regular file")
        if not _is_allowed_source_path(path):
            raise SourceDistributionError(
                f"tracked path is outside the source allowlist: {path}"
            )
        data = git_runner(repository, ("cat-file", "blob", object_id))
        if len(data) > MAX_FILE_BYTES:
            raise SourceDistributionError(
                "tracked source file exceeds the per-file bound"
            )
        total_bytes += len(data)
        if total_bytes > MAX_SOURCE_BYTES:
            raise SourceDistributionError("tracked source exceeds the total size bound")
        files.append(TrackedFile(path, mode, object_id, data))
        if len(files) > MAX_SOURCE_FILES:
            raise SourceDistributionError("tracked source exceeds the file-count bound")
    if not files:
        raise SourceDistributionError("committed source tree is empty")
    return sorted(files, key=lambda item: _path_sort_key(item.path))


def _artifact_name(path: Path) -> str:
    name = path.name
    if _ARTIFACT_NAME_RE.fullmatch(name) is None or not name.endswith(".tar.gz"):
        raise SourceDistributionError("archive name must be a bounded .tar.gz filename")
    return name


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _ensure_external_output(repository: Path, archive: Path) -> None:
    repository_resolved = repository.resolve()
    archive_resolved = archive.resolve()
    if _is_relative_to(archive_resolved, repository_resolved):
        raise SourceDistributionError(
            "distribution output must be outside the repository"
        )
    if archive.exists():
        raise SourceDistributionError("distribution archive already exists")
    manifest = Path(str(archive) + ".manifest.json")
    digest = Path(str(archive) + ".sha256")
    if manifest.exists() or digest.exists():
        raise SourceDistributionError("distribution sidecar already exists")


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _manifest_payload(
    *, commit: str, archive_name: str, archive_sha256: str, files: Iterable[TrackedFile]
) -> dict[str, object]:
    return {
        "archive_format": ARCHIVE_FORMAT,
        "archive_name": archive_name,
        "archive_prefix": ARCHIVE_PREFIX,
        "archive_sha256": archive_sha256,
        "commit": commit,
        "files": [
            {
                "bytes": len(item.data),
                "mode": f"{item.mode:06o}",
                "path": item.path,
                "sha256": hashlib.sha256(item.data).hexdigest(),
            }
            for item in files
        ],
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
    }


def _deterministic_gzip(data: bytes) -> bytes:
    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15)
    compressed = compressor.compress(data) + compressor.flush()
    header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    trailer = (zlib.crc32(data) & 0xFFFFFFFF).to_bytes(4, "little") + (
        len(data) & 0xFFFFFFFF
    ).to_bytes(4, "little")
    return header + compressed + trailer


def _build_archive(files: Sequence[TrackedFile]) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(
        fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT
    ) as archive:
        for item in files:
            info = tarfile.TarInfo(ARCHIVE_PREFIX + item.path)
            info.size = len(item.data)
            info.mode = stat.S_IMODE(item.mode)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.pax_headers = {}
            archive.addfile(info, io.BytesIO(item.data))
    return _deterministic_gzip(tar_buffer.getvalue())


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise SourceDistributionError(f"output already exists: {path.name}") from exc


def export_distribution(
    repository: Path,
    archive_path: Path,
    *,
    git_runner: GitRunner = _run_git,
) -> DistributionResult:
    repository = repository.resolve()
    archive_path = archive_path.resolve()
    _artifact_name(archive_path)
    _ensure_external_output(repository, archive_path)
    commit = _read_clean_head(repository, git_runner)
    files = _parse_tree(repository, commit, git_runner)
    archive_bytes = _build_archive(files)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    manifest_path = Path(str(archive_path) + ".manifest.json")
    digest_path = Path(str(archive_path) + ".sha256")
    manifest_bytes = _canonical_json(
        _manifest_payload(
            commit=commit,
            archive_name=archive_path.name,
            archive_sha256=archive_sha256,
            files=files,
        )
    )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    digest_bytes = (
        f"{archive_sha256}  {archive_path.name}\n"
        f"{manifest_sha256}  {manifest_path.name}\n"
    ).encode("ascii")
    _verify_bytes(
        archive_bytes,
        manifest_bytes,
        digest_bytes,
        expected_commit=commit,
        expected_archive_sha256=archive_sha256,
        expected_manifest_sha256=manifest_sha256,
    )
    _write_new(archive_path, archive_bytes)
    _write_new(manifest_path, manifest_bytes)
    _write_new(digest_path, digest_bytes)
    return DistributionResult(
        commit=commit,
        archive=archive_path,
        manifest=manifest_path,
        digest=digest_path,
        archive_sha256=archive_sha256,
        manifest_sha256=manifest_sha256,
        file_count=len(files),
        source_bytes=sum(len(item.data) for item in files),
    )


def _read_manifest(manifest_bytes: bytes) -> dict[str, object]:
    try:
        value = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceDistributionError("source manifest is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SourceDistributionError("source manifest must be an object")
    expected_keys = {
        "archive_format",
        "archive_name",
        "archive_prefix",
        "archive_sha256",
        "commit",
        "files",
        "schema",
        "schema_version",
    }
    if set(value) != expected_keys:
        raise SourceDistributionError("source manifest fields are not exact")
    if value["schema"] != SCHEMA or value["schema_version"] != SCHEMA_VERSION:
        raise SourceDistributionError("source manifest contract is unsupported")
    if (
        value["archive_format"] != ARCHIVE_FORMAT
        or value["archive_prefix"] != ARCHIVE_PREFIX
    ):
        raise SourceDistributionError("source manifest archive contract is invalid")
    _validate_commit(value["commit"])
    _validate_sha256(value["archive_sha256"], "manifest archive digest")
    if (
        not isinstance(value["archive_name"], str)
        or _ARTIFACT_NAME_RE.fullmatch(value["archive_name"]) is None
        or not value["archive_name"].endswith(".tar.gz")
    ):
        raise SourceDistributionError("manifest archive name is invalid")
    raw_files = value["files"]
    if (
        not isinstance(raw_files, list)
        or not raw_files
        or len(raw_files) > MAX_SOURCE_FILES
    ):
        raise SourceDistributionError("manifest file list is invalid")
    last_path: str | None = None
    seen: set[str] = set()
    total_bytes = 0
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != {
            "bytes",
            "mode",
            "path",
            "sha256",
        }:
            raise SourceDistributionError("manifest file entry is invalid")
        path = raw_file["path"]
        _validate_relative_path(path if isinstance(path, str) else "")
        if not isinstance(path, str) or not _is_allowed_source_path(path):
            raise SourceDistributionError(
                "manifest contains a path outside the source allowlist"
            )
        if last_path is not None and _path_sort_key(path) <= _path_sort_key(last_path):
            raise SourceDistributionError("manifest paths are not strictly sorted")
        last_path = path
        normalized_key = unicodedata.normalize("NFC", path).casefold()
        if normalized_key in seen:
            raise SourceDistributionError("manifest contains duplicate paths")
        seen.add(normalized_key)
        size = raw_file["bytes"]
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > MAX_FILE_BYTES
        ):
            raise SourceDistributionError("manifest file size is invalid")
        mode = raw_file["mode"]
        if mode not in {"100644", "100755"}:
            raise SourceDistributionError("manifest file mode is invalid")
        _validate_sha256(raw_file["sha256"], "manifest file digest")
        total_bytes += size
        if total_bytes > MAX_SOURCE_BYTES:
            raise SourceDistributionError("manifest source size exceeds the bound")
    return value


def _read_digest_sidecar(
    digest_bytes: bytes, archive_name: str, manifest_name: str
) -> tuple[str, str]:
    try:
        lines = digest_bytes.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise SourceDistributionError("digest sidecar is not ASCII") from exc
    if len(lines) != 2:
        raise SourceDistributionError("digest sidecar must contain two records")
    records: list[tuple[str, str]] = []
    for line in lines:
        parts = line.split("  ")
        if len(parts) != 2:
            raise SourceDistributionError("digest sidecar record is malformed")
        digest, name = parts
        records.append((_validate_sha256(digest, "sidecar digest"), name))
    if records[0][1] != archive_name or records[1][1] != manifest_name:
        raise SourceDistributionError(
            "digest sidecar names do not match the distribution"
        )
    return records[0][0], records[1][0]


def _archive_members(archive_bytes: bytes) -> list[tuple[str, int, bytes]]:
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise SourceDistributionError("source archive exceeds the size bound")
    members: list[tuple[str, int, bytes]] = []
    total_bytes = 0
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as handle:
            raw_members = handle.getmembers()
            if not raw_members or len(raw_members) > MAX_SOURCE_FILES:
                raise SourceDistributionError("source archive member count is invalid")
            for member in raw_members:
                if (
                    not member.isfile()
                    or member.issym()
                    or member.islnk()
                    or member.mode & 0o170000
                ):
                    raise SourceDistributionError(
                        "source archive contains a non-regular member"
                    )
                if not member.name.startswith(ARCHIVE_PREFIX):
                    raise SourceDistributionError(
                        "source archive member is outside its prefix"
                    )
                path = member.name[len(ARCHIVE_PREFIX) :]
                _validate_relative_path(path)
                if not _is_allowed_source_path(path):
                    raise SourceDistributionError(
                        "source archive contains a forbidden path"
                    )
                normalized_key = unicodedata.normalize("NFC", path).casefold()
                if normalized_key in seen:
                    raise SourceDistributionError(
                        "source archive contains duplicate paths"
                    )
                seen.add(normalized_key)
                if member.mode not in {0o644, 0o755}:
                    raise SourceDistributionError(
                        "source archive member mode is invalid"
                    )
                if member.uid != 0 or member.gid != 0 or member.mtime != 0:
                    raise SourceDistributionError(
                        "source archive member metadata is not deterministic"
                    )
                if member.uname or member.gname:
                    raise SourceDistributionError(
                        "source archive member identity is not deterministic"
                    )
                if member.size < 0 or member.size > MAX_FILE_BYTES:
                    raise SourceDistributionError(
                        "source archive member size is invalid"
                    )
                source = handle.extractfile(member)
                if source is None:
                    raise SourceDistributionError(
                        "source archive member cannot be read"
                    )
                data = source.read(member.size + 1)
                if len(data) != member.size:
                    raise SourceDistributionError(
                        "source archive member size does not match content"
                    )
                total_bytes += len(data)
                if total_bytes > MAX_SOURCE_BYTES:
                    raise SourceDistributionError(
                        "source archive content exceeds the size bound"
                    )
                members.append((path, member.mode, data))
    except (tarfile.TarError, OSError) as exc:
        raise SourceDistributionError("source archive is not a valid gzip tar") from exc
    if [item[0] for item in members] != sorted(
        (item[0] for item in members), key=_path_sort_key
    ):
        raise SourceDistributionError("source archive members are not strictly sorted")
    return members


def _verify_extracted_tree(
    root: Path, manifest_files: Sequence[dict[str, object]]
) -> None:
    if not root.is_dir() or root.is_symlink():
        raise SourceDistributionError("extracted source root is not a directory")
    expected: dict[str, dict[str, object]] = {
        str(item["path"]): item for item in manifest_files
    }
    actual: set[str] = set()
    for current, directories, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                raise SourceDistributionError("extracted source contains a symlink")
            relative = candidate.relative_to(root).as_posix()
            _validate_relative_path(relative)
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in filenames:
            candidate = current_path / name
            if candidate.is_symlink() or not candidate.is_file():
                raise SourceDistributionError(
                    "extracted source contains a non-regular file"
                )
            relative = candidate.relative_to(root).as_posix()
            if not _is_allowed_source_path(relative):
                raise SourceDistributionError(
                    "extracted source contains a forbidden path"
                )
            actual.add(relative)
            if relative not in expected:
                raise SourceDistributionError(
                    "extracted source contains an unexpected file"
                )
            data = candidate.read_bytes()
            entry = expected[relative]
            if (
                len(data) != entry["bytes"]
                or hashlib.sha256(data).hexdigest() != entry["sha256"]
            ):
                raise SourceDistributionError(
                    "extracted source content does not match the manifest"
                )
            if find_secret_labels(data):
                raise SourceDistributionError(
                    "extracted source contains secret material"
                )
    if actual != set(expected):
        raise SourceDistributionError("extracted source is missing a manifest file")


def _verify_bytes(
    archive_bytes: bytes,
    manifest_bytes: bytes,
    digest_bytes: bytes,
    *,
    expected_commit: str | None = None,
    expected_archive_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> tuple[dict[str, object], str, str]:
    manifest = _read_manifest(manifest_bytes)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest["archive_sha256"] != archive_sha256:
        raise SourceDistributionError("archive digest does not match the manifest")
    sidecar_archive, sidecar_manifest = _read_digest_sidecar(
        digest_bytes,
        str(manifest["archive_name"]),
        str(manifest["archive_name"]) + ".manifest.json",
    )
    if sidecar_archive != archive_sha256 or sidecar_manifest != manifest_sha256:
        raise SourceDistributionError("digest sidecar does not match the distribution")
    if expected_commit is not None and manifest["commit"] != _validate_commit(
        expected_commit, "expected commit"
    ):
        raise SourceDistributionError(
            "distribution commit does not match the expected commit"
        )
    if expected_archive_sha256 is not None and archive_sha256 != _validate_sha256(
        expected_archive_sha256, "expected archive digest"
    ):
        raise SourceDistributionError(
            "archive digest does not match the expected digest"
        )
    if expected_manifest_sha256 is not None and manifest_sha256 != _validate_sha256(
        expected_manifest_sha256, "expected manifest digest"
    ):
        raise SourceDistributionError(
            "manifest digest does not match the expected digest"
        )
    archive_members = _archive_members(archive_bytes)
    manifest_files = manifest["files"]
    assert isinstance(manifest_files, list)
    if len(archive_members) != len(manifest_files):
        raise SourceDistributionError(
            "archive member count does not match the manifest"
        )
    for (path, mode, data), raw_file in zip(
        archive_members, manifest_files, strict=True
    ):
        if path != raw_file["path"] or f"{0o100000 | mode:06o}" != raw_file["mode"]:
            raise SourceDistributionError(
                "archive member metadata does not match the manifest"
            )
        if (
            len(data) != raw_file["bytes"]
            or hashlib.sha256(data).hexdigest() != raw_file["sha256"]
        ):
            raise SourceDistributionError(
                "archive member content does not match the manifest"
            )
        if find_secret_labels(data):
            raise SourceDistributionError("source archive contains secret material")
    return manifest, archive_sha256, manifest_sha256


def verify_distribution(
    archive_path: Path,
    *,
    manifest_path: Path | None = None,
    digest_path: Path | None = None,
    repository: Path | None = None,
    expected_commit: str | None = None,
    expected_archive_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
    extract_dir: Path | None = None,
    git_runner: GitRunner = _run_git,
) -> DistributionResult:
    archive_path = archive_path.resolve()
    manifest_path = (
        manifest_path or Path(str(archive_path) + ".manifest.json")
    ).resolve()
    digest_path = (digest_path or Path(str(archive_path) + ".sha256")).resolve()
    if (
        not archive_path.is_file()
        or not manifest_path.is_file()
        or not digest_path.is_file()
    ):
        raise SourceDistributionError("distribution archive or sidecar is missing")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise SourceDistributionError("source archive exceeds the size bound")
    archive_bytes = archive_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    digest_bytes = digest_path.read_bytes()
    manifest, archive_sha256, manifest_sha256 = _verify_bytes(
        archive_bytes,
        manifest_bytes,
        digest_bytes,
        expected_commit=expected_commit,
        expected_archive_sha256=expected_archive_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    if manifest["archive_name"] != archive_path.name:
        raise SourceDistributionError(
            "manifest archive name does not match the archive path"
        )
    if repository is not None:
        current_commit = _read_clean_head(repository.resolve(), git_runner)
        if current_commit != manifest["commit"]:
            raise SourceDistributionError(
                "distribution commit does not match the clean repository HEAD"
            )
    manifest_files = manifest["files"]
    assert isinstance(manifest_files, list)
    with tempfile.TemporaryDirectory(prefix="connectmd-source-verify-") as temporary:
        temporary_root = Path(temporary)
        for path, mode, data in _archive_members(archive_bytes):
            destination = temporary_root.joinpath(*path.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o755 if mode == 0o755 else 0o644)
        _verify_extracted_tree(temporary_root, manifest_files)
    if extract_dir is not None:
        _verify_extracted_tree(extract_dir.resolve(), manifest_files)
    source_bytes = sum(int(item["bytes"]) for item in manifest_files)
    return DistributionResult(
        commit=str(manifest["commit"]),
        archive=archive_path,
        manifest=manifest_path,
        digest=digest_path,
        archive_sha256=archive_sha256,
        manifest_sha256=manifest_sha256,
        file_count=len(manifest_files),
        source_bytes=source_bytes,
    )


def _result_json(result: DistributionResult) -> str:
    return json.dumps(
        {
            "archive": str(result.archive),
            "archive_sha256": result.archive_sha256,
            "commit": result.commit,
            "digest": str(result.digest),
            "file_count": result.file_count,
            "manifest": str(result.manifest),
            "manifest_sha256": result.manifest_sha256,
            "source_bytes": result.source_bytes,
        },
        ensure_ascii=True,
        sort_keys=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export and verify a deterministic clean-HEAD Connect.md source distribution."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser(
        "export", help="export blobs from a clean committed HEAD"
    )
    export.add_argument("--repo", type=Path, default=Path("."), help="repository root")
    export.add_argument(
        "--output", type=Path, required=True, help="external .tar.gz output path"
    )
    verify = commands.add_parser(
        "verify", help="verify an archive, sidecars, and extracted tree"
    )
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--manifest", type=Path)
    verify.add_argument("--digest", type=Path)
    verify.add_argument(
        "--repo",
        type=Path,
        help="also require a clean repository with the manifest commit",
    )
    verify.add_argument("--expected-commit")
    verify.add_argument("--expected-archive-sha256")
    verify.add_argument("--expected-manifest-sha256")
    verify.add_argument("--extract-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "export":
            result = export_distribution(arguments.repo, arguments.output)
        else:
            result = verify_distribution(
                arguments.archive,
                manifest_path=arguments.manifest,
                digest_path=arguments.digest,
                repository=arguments.repo,
                expected_commit=arguments.expected_commit,
                expected_archive_sha256=arguments.expected_archive_sha256,
                expected_manifest_sha256=arguments.expected_manifest_sha256,
                extract_dir=arguments.extract_dir,
            )
    except (OSError, SourceDistributionError) as exc:
        print(f"source distribution: {exc}", file=sys.stderr)
        return 2
    print(_result_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
