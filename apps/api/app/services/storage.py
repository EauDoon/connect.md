from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

_PENDING_FILE = re.compile(
    r"^\.[^/\\]+\.pending-(?P<created_ns>[0-9]{19})-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PENDING_GRACE_NS = 60 * 60 * 1_000_000_000
_PENDING_CLEANUP_MAX_ENTRIES = 100_000
_ARTIFACT_STAGE_ROOT = ".connectmd-artifact-staging/v1"
_ARTIFACT_STAGE_FILE = re.compile(
    r"^(?P<created_ns>[0-9]{19})-"
    r"(?P<nonce>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"\.(?P<suffix>bin|json)$"
)
_ARTIFACT_STAGE_SCAN_MAX = 100
_ARTIFACT_DESCRIPTOR_MAX_BYTES = 16_384


class StorageIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class StagedArtifactFiles:
    payload_path: str
    descriptor_path: str
    created_ns: int


@dataclass(frozen=True)
class StagedArtifactScan:
    descriptors: tuple[str, ...]
    incomplete_payloads: tuple[str, ...]
    invalid_entry: bool
    overbound: bool


class VersionStore:
    """Append-only local store for canonical Markdown bytes."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._cleanup_stale_pending_files(recursive=True)

    def relative_path(self, kind: str, document_id: str, version: int) -> str:
        if kind not in {"profile", "resume", "post"} or version < 1:
            raise ValueError("invalid version store target")
        directory = {"profile": "profiles", "resume": "resumes", "post": "posts"}[kind]
        return f"{directory}/{document_id}/versions/{version:06d}.md"

    def application_snapshot_relative_path(self, application_id: str) -> str:
        """Return the one immutable Markdown copy owned by an application.

        This deliberately does not reuse a document-version path: an applicant's
        source document can subsequently change, become private, or be erased.
        The application copy has its own retention and account-erasure lifecycle.
        """
        try:
            UUID(application_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid application snapshot target") from exc
        return f"applications/{application_id}/snapshot.md"

    def _absolute(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        if self.root not in target.parents:
            raise StorageIntegrityError("invalid version storage path")
        return target

    def write_immutable(self, relative_path: str, markdown: str) -> str:
        return self.write_immutable_bytes(relative_path, markdown.encode("utf-8"))

    def write_immutable_bytes(self, relative_path: str, payload: bytes) -> str:
        target = self._absolute(relative_path)
        digest = hashlib.sha256(payload).hexdigest()
        pending: Path | None = None
        try:
            self._mkdir_parents_durable(target.parent)
            self._cleanup_stale_pending_files(directory=target.parent, recursive=False)
            pending = target.with_name(f".{target.name}.pending-{time.time_ns():019d}-{uuid4()}")
            with pending.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            try:
                # Hard-link promotion is atomic and, unlike replace(), cannot
                # overwrite an already committed immutable version.
                os.link(pending, target)
            except FileExistsError as exc:
                existing = target.read_bytes()
                if existing != payload:
                    raise StorageIntegrityError(
                        "immutable version path contains different bytes"
                    ) from exc
            self._fsync_directory(target.parent)
        except FileExistsError as exc:
            raise StorageIntegrityError("immutable version file already exists") from exc
        except OSError as exc:
            raise StorageIntegrityError("immutable version file could not be written") from exc
        finally:
            try:
                if pending is not None:
                    pending.unlink(missing_ok=True)
            except OSError:
                pass
        return digest

    def stage_artifact(
        self,
        intent_id: str,
        payload: bytes,
        descriptor_bytes: bytes,
        *,
        created_ns: int,
        nonce: str,
    ) -> StagedArtifactFiles:
        """Durably create one strict payload/descriptor pair without following links."""

        try:
            UUID(intent_id)
            UUID(nonce)
        except (TypeError, ValueError) as exc:
            raise StorageIntegrityError("artifact staging target is unavailable") from exc
        if created_ns < 1 or len(descriptor_bytes) > _ARTIFACT_DESCRIPTOR_MAX_BYTES:
            raise StorageIntegrityError("artifact staging target is unavailable")
        stem = f"{created_ns:019d}-{nonce}"
        payload_path = f"{_ARTIFACT_STAGE_ROOT}/{intent_id}/{stem}.bin"
        descriptor_path = f"{_ARTIFACT_STAGE_ROOT}/{intent_id}/{stem}.json"
        payload_target = self._strict_stage_target(payload_path, expected_suffix="bin")
        descriptor_target = self._strict_stage_target(descriptor_path, expected_suffix="json")
        try:
            self._mkdir_parents_durable(payload_target.parent)
            self._write_exclusive_durable(payload_target, payload)
            try:
                self._write_exclusive_durable(descriptor_target, descriptor_bytes)
            except BaseException:
                payload_target.unlink(missing_ok=True)
                self._fsync_directory(payload_target.parent)
                raise
            self._fsync_directory(payload_target.parent)
        except StorageIntegrityError:
            raise
        except OSError as exc:
            raise StorageIntegrityError("artifact staging target is unavailable") from exc
        return StagedArtifactFiles(payload_path, descriptor_path, created_ns)

    @staticmethod
    def _write_exclusive_durable(target: Path, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags, 0o600)
        try:
            written = 0
            while written < len(payload):
                written += os.write(descriptor, payload[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def promote_staged_artifact(
        self,
        staged_payload_path: str,
        canonical_path: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> None:
        self.read_verified_bytes(
            staged_payload_path,
            expected_sha256,
            expected_size_bytes=expected_size_bytes,
            max_size_bytes=max_size_bytes,
        )
        source = self._strict_stage_target(staged_payload_path, expected_suffix="bin")
        target = self._canonical_artifact_target(canonical_path, expected_sha256)
        try:
            if os.name == "posix":
                self._link_artifact_posix(source, target)
            else:
                self._mkdir_parents_durable(target.parent)
                self._require_no_symlink_directory_chain(target.parent)
                try:
                    os.link(source, target, follow_symlinks=False)
                except FileExistsError:
                    pass
                self._require_no_symlink_directory_chain(target.parent)
                self._fsync_directory(target.parent)
            self.read_verified_bytes(
                canonical_path,
                expected_sha256,
                expected_size_bytes=expected_size_bytes,
                max_size_bytes=max_size_bytes,
            )
        except StorageIntegrityError:
            raise
        except OSError as exc:
            raise StorageIntegrityError("artifact promotion is unavailable") from exc

    def _canonical_artifact_target(self, relative_path: str, expected_sha256: str) -> Path:
        try:
            raw = PurePosixPath(relative_path)
            valid_application = (
                len(raw.parts) == 3
                and raw.parts[0] == "applications"
                and raw.parts[2] == "snapshot.md"
            )
            valid_evidence = (
                len(raw.parts) == 4
                and raw.parts[0] == "verification-evidence"
                and raw.parts[3] == f"{expected_sha256}.bin"
            )
            valid_document = (
                len(raw.parts) == 4
                and raw.parts[0] in {"profiles", "resumes"}
                and raw.parts[2] == "versions"
                and re.fullmatch(r"[0-9]{6}\.md", raw.parts[3]) is not None
                and int(raw.parts[3].removesuffix(".md")) >= 1
            )
            valid_post = (
                len(raw.parts) == 4
                and raw.parts[0] == "posts"
                and raw.parts[2:] == ("versions", "000001.md")
            )
            if (
                raw.is_absolute()
                or "\\" in relative_path
                or not (valid_application or valid_evidence or valid_document or valid_post)
            ):
                raise ValueError
            resource_id = UUID(raw.parts[1])
            if (valid_document or valid_post) and (
                resource_id.version != 4 or str(resource_id) != raw.parts[1]
            ):
                raise ValueError
            if valid_evidence:
                UUID(raw.parts[2])
            target = self.root.joinpath(*raw.parts)
            if target.relative_to(self.root).as_posix() != relative_path:
                raise ValueError
            return target
        except (OSError, TypeError, ValueError) as exc:
            raise StorageIntegrityError("artifact promotion is unavailable") from exc

    def _require_no_symlink_directory_chain(self, directory: Path) -> None:
        relative = directory.relative_to(self.root)
        cursor = self.root
        for part in relative.parts:
            cursor = cursor / part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise StorageIntegrityError("artifact promotion is unavailable")

    def _link_artifact_posix(self, source: Path, target: Path) -> None:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        directory_only = getattr(os, "O_DIRECTORY", 0)
        if not no_follow or not directory_only:
            raise StorageIntegrityError("artifact promotion is unavailable")

        def open_chain(parts: tuple[str, ...], *, create: bool) -> int:
            descriptor = os.open(self.root, os.O_RDONLY | directory_only | no_follow)
            try:
                for part in parts:
                    try:
                        next_descriptor = os.open(
                            part,
                            os.O_RDONLY | directory_only | no_follow,
                            dir_fd=descriptor,
                        )
                    except FileNotFoundError:
                        if not create:
                            raise
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                        os.fsync(descriptor)
                        next_descriptor = os.open(
                            part,
                            os.O_RDONLY | directory_only | no_follow,
                            dir_fd=descriptor,
                        )
                    os.close(descriptor)
                    descriptor = next_descriptor
                return descriptor
            except BaseException:
                os.close(descriptor)
                raise

        source_parts = source.relative_to(self.root).parts
        target_parts = target.relative_to(self.root).parts
        source_directory = open_chain(source_parts[:-1], create=False)
        target_directory: int | None = None
        try:
            target_directory = open_chain(target_parts[:-1], create=True)
            try:
                os.link(
                    source_parts[-1],
                    target_parts[-1],
                    src_dir_fd=source_directory,
                    dst_dir_fd=target_directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                pass
            os.fsync(target_directory)
        finally:
            os.close(source_directory)
            if target_directory is not None:
                os.close(target_directory)

    def read_staged_descriptor(self, relative_path: str) -> bytes:
        target = self._strict_stage_target(relative_path, expected_suffix="json")
        try:
            parts, verified_target = self._verified_byte_target(relative_path)
            info = target.lstat()
            size = info.st_size
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or size < 1
                or size > _ARTIFACT_DESCRIPTOR_MAX_BYTES
            ):
                raise StorageIntegrityError("artifact staging descriptor is unavailable")
            if os.name == "posix":
                return self._read_verified_bytes_posix(
                    parts,
                    expected_size_bytes=size,
                    max_size_bytes=_ARTIFACT_DESCRIPTOR_MAX_BYTES,
                )
            return self._read_verified_bytes_portable(
                parts,
                verified_target,
                expected_size_bytes=size,
                max_size_bytes=_ARTIFACT_DESCRIPTOR_MAX_BYTES,
            )
        except StorageIntegrityError:
            raise
        except OSError as exc:
            raise StorageIntegrityError("artifact staging descriptor is unavailable") from exc

    def scan_staged_artifacts(self, *, limit: int = _ARTIFACT_STAGE_SCAN_MAX) -> StagedArtifactScan:
        if limit < 1 or limit > _ARTIFACT_STAGE_SCAN_MAX:
            raise StorageIntegrityError("artifact staging scan is unavailable")
        root = self.root / PurePosixPath(_ARTIFACT_STAGE_ROOT)
        try:
            cursor = self.root
            for part in PurePosixPath(_ARTIFACT_STAGE_ROOT).parts:
                cursor = cursor / part
                if not cursor.exists():
                    return StagedArtifactScan((), (), False, False)
                component = cursor.lstat()
                if stat.S_ISLNK(component.st_mode) or not stat.S_ISDIR(component.st_mode):
                    return StagedArtifactScan((), (), True, False)
            descriptors: list[str] = []
            incomplete_payloads: list[str] = []
            invalid = False
            count = 0
            for intent_directory in root.iterdir():
                count += 1
                if count > limit:
                    return StagedArtifactScan(
                        tuple(descriptors), tuple(incomplete_payloads), invalid, True
                    )
                try:
                    UUID(intent_directory.name)
                    info = intent_directory.lstat()
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                        invalid = True
                        continue
                    seen: dict[str, set[str]] = {}
                    for entry in intent_directory.iterdir():
                        count += 1
                        if count > limit:
                            return StagedArtifactScan(
                                tuple(descriptors), tuple(incomplete_payloads), invalid, True
                            )
                        entry_info = entry.lstat()
                        match = _ARTIFACT_STAGE_FILE.fullmatch(entry.name)
                        if (
                            match is None
                            or stat.S_ISLNK(entry_info.st_mode)
                            or not stat.S_ISREG(entry_info.st_mode)
                        ):
                            invalid = True
                            continue
                        stem = entry.name.rsplit(".", 1)[0]
                        seen.setdefault(stem, set()).add(match.group("suffix"))
                        if match.group("suffix") == "json":
                            descriptors.append(entry.relative_to(self.root).as_posix())
                    for stem, suffixes in seen.items():
                        if suffixes == {"bin"}:
                            incomplete_payloads.append(
                                (intent_directory / f"{stem}.bin").relative_to(self.root).as_posix()
                            )
                        elif suffixes != {"bin", "json"}:
                            invalid = True
                except (OSError, ValueError):
                    invalid = True
            return StagedArtifactScan(
                tuple(sorted(descriptors)),
                tuple(sorted(incomplete_payloads)),
                invalid,
                False,
            )
        except OSError as exc:
            raise StorageIntegrityError("artifact staging scan is unavailable") from exc

    def retire_staged_artifact(self, payload_path: str, descriptor_path: str) -> None:
        payload = self._strict_stage_target(payload_path, expected_suffix="bin")
        descriptor = self._strict_stage_target(descriptor_path, expected_suffix="json")
        try:
            descriptor.unlink(missing_ok=True)
            payload.unlink(missing_ok=True)
            self._fsync_directory(payload.parent)
        except OSError as exc:
            raise StorageIntegrityError("artifact staging cleanup is unavailable") from exc

    def retire_incomplete_staged_payload(
        self, payload_path: str, *, max_size_bytes: int = 262_144
    ) -> None:
        target = self._strict_stage_target(payload_path, expected_suffix="bin")
        try:
            info = target.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise StorageIntegrityError("artifact staging cleanup is unavailable")
            size = info.st_size
            if size < 1 or size > max_size_bytes:
                raise StorageIntegrityError("artifact staging cleanup is unavailable")
            parts, verified_target = self._verified_byte_target(payload_path)
            payload = (
                self._read_verified_bytes_posix(
                    parts,
                    expected_size_bytes=size,
                    max_size_bytes=max_size_bytes,
                )
                if os.name == "posix"
                else self._read_verified_bytes_portable(
                    parts,
                    verified_target,
                    expected_size_bytes=size,
                    max_size_bytes=max_size_bytes,
                )
            )
            self.delete_verified_exact(
                payload_path,
                hashlib.sha256(payload).hexdigest(),
                expected_size_bytes=size,
                max_size_bytes=max_size_bytes,
            )
        except StorageIntegrityError:
            raise
        except OSError as exc:
            raise StorageIntegrityError("artifact staging cleanup is unavailable") from exc

    def delete_verified_exact(
        self,
        relative_path: str,
        expected_sha256: str,
        *,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> None:
        """Delete only the exact regular bytes named by a durable authority decision."""

        try:
            target = self._verified_byte_target(relative_path)[1]
            if not target.exists():
                return
            self.read_verified_bytes(
                relative_path,
                expected_sha256,
                expected_size_bytes=expected_size_bytes,
                max_size_bytes=max_size_bytes,
            )
            target.unlink()
            self._fsync_directory(target.parent)
        except StorageIntegrityError:
            raise
        except OSError as exc:
            raise StorageIntegrityError("exact artifact deletion is unavailable") from exc

    def _strict_stage_target(self, relative_path: str, *, expected_suffix: str) -> Path:
        try:
            raw = PurePosixPath(relative_path)
            if (
                raw.is_absolute()
                or "\\" in relative_path
                or len(raw.parts) != 4
                or raw.parts[:2] != (".connectmd-artifact-staging", "v1")
                or raw.parts[3].rsplit(".", 1)[-1] != expected_suffix
                or _ARTIFACT_STAGE_FILE.fullmatch(raw.parts[3]) is None
            ):
                raise ValueError
            UUID(raw.parts[2])
            target = self.root.joinpath(*raw.parts)
            if target.relative_to(self.root).as_posix() != relative_path:
                raise ValueError
            cursor = self.root
            for part in raw.parts[:-1]:
                cursor = cursor / part
                if cursor.exists() and cursor.is_symlink():
                    raise ValueError
            return target
        except (OSError, TypeError, ValueError) as exc:
            raise StorageIntegrityError("artifact staging target is unavailable") from exc

    def _cleanup_stale_pending_files(
        self, *, directory: Path | None = None, recursive: bool
    ) -> None:
        """Remove only old files in this store's strict pending namespace."""

        cleanup_root = self.root if directory is None else directory
        try:
            if not self.root.exists():
                return
            root_stat = self.root.lstat()
            if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
                raise StorageIntegrityError("storage pending cleanup is unavailable")
            self._assert_contained_without_symlinks(cleanup_root, require_directory=True)
            pending_directories = [cleanup_root]
            entries_seen = 0
            oldest_owned_ns = time.time_ns() - _PENDING_GRACE_NS
            while pending_directories:
                current = pending_directories.pop()
                with os.scandir(current) as entries:
                    for entry in entries:
                        # Artifact staging has its own signed, database-backed
                        # reconciliation protocol.  Generic pending-file cleanup
                        # must not infer disposal authority from a filename or
                        # timestamp anywhere inside that direct namespace.
                        if (
                            current == self.root
                            and entry.name == PurePosixPath(_ARTIFACT_STAGE_ROOT).parts[0]
                        ):
                            continue
                        entries_seen += 1
                        if entries_seen > _PENDING_CLEANUP_MAX_ENTRIES:
                            raise StorageIntegrityError("storage pending cleanup is unavailable")
                        entry_path = Path(entry.path)
                        entry_stat = entry_path.lstat()
                        if stat.S_ISLNK(entry_stat.st_mode):
                            continue
                        if stat.S_ISDIR(entry_stat.st_mode):
                            if recursive:
                                pending_directories.append(entry_path)
                            continue
                        match = _PENDING_FILE.fullmatch(entry.name)
                        if match is None or not stat.S_ISREG(entry_stat.st_mode):
                            continue
                        if int(match.group("created_ns")) > oldest_owned_ns:
                            continue
                        self._assert_contained_without_symlinks(
                            entry_path.parent, require_directory=True
                        )
                        current_stat = entry_path.lstat()
                        if not stat.S_ISREG(current_stat.st_mode) or not os.path.samestat(
                            entry_stat, current_stat
                        ):
                            raise StorageIntegrityError("storage pending cleanup is unavailable")
                        entry_path.unlink()
                        self._fsync_directory(entry_path.parent)
        except StorageIntegrityError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise StorageIntegrityError("storage pending cleanup is unavailable") from exc

    def _assert_contained_without_symlinks(self, target: Path, *, require_directory: bool) -> None:
        try:
            relative = target.relative_to(self.root)
        except ValueError as exc:
            raise StorageIntegrityError("storage pending cleanup is unavailable") from exc
        cursor = self.root
        for part in relative.parts:
            cursor = cursor / part
            component = cursor.lstat()
            if stat.S_ISLNK(component.st_mode):
                raise StorageIntegrityError("storage pending cleanup is unavailable")
        target_stat = target.lstat()
        if require_directory and not stat.S_ISDIR(target_stat.st_mode):
            raise StorageIntegrityError("storage pending cleanup is unavailable")

    def _mkdir_parents_durable(self, directory: Path) -> None:
        """Create and persist every new path component, not only the leaf."""
        missing: list[Path] = []
        cursor = directory
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for component in reversed(missing):
            try:
                component.mkdir()
            except FileExistsError:
                pass
            self._fsync_directory(component.parent)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Persist the directory entry on production POSIX filesystems."""
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def check_ready(self) -> None:
        """Verify the canonical volume is writable without retaining probe data."""
        probe = self.root / f".connectmd-ready-{uuid4()}"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(b"ready")
            if probe.read_bytes() != b"ready":
                raise StorageIntegrityError("canonical storage readiness probe was corrupted")
        except OSError as exc:
            raise StorageIntegrityError("canonical storage is not writable") from exc
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    def read_verified(self, relative_path: str, expected_sha256: str) -> str:
        target = self._absolute(relative_path)
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise StorageIntegrityError("version Markdown file is unavailable") from exc
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_sha256:
            raise StorageIntegrityError("version Markdown hash does not match the ledger")
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StorageIntegrityError("version Markdown is not valid UTF-8") from exc
        if "\r" in decoded:
            raise StorageIntegrityError("stored Markdown does not use LF line endings")
        return decoded

    def read_verified_bytes(
        self,
        relative_path: str,
        expected_sha256: str,
        *,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> bytes:
        """Read one exact regular file without following storage-tree symlinks."""

        try:
            parts, target = self._verified_byte_target(relative_path)
            self._validate_byte_expectations(
                expected_sha256,
                expected_size_bytes=expected_size_bytes,
                max_size_bytes=max_size_bytes,
            )
            if os.name == "posix":
                payload = self._read_verified_bytes_posix(
                    parts,
                    expected_size_bytes=expected_size_bytes,
                    max_size_bytes=max_size_bytes,
                )
            else:
                payload = self._read_verified_bytes_portable(
                    parts,
                    target,
                    expected_size_bytes=expected_size_bytes,
                    max_size_bytes=max_size_bytes,
                )
            if not compare_digest(hashlib.sha256(payload).hexdigest(), expected_sha256):
                raise StorageIntegrityError("stored artifact is unavailable")
            return payload
        except StorageIntegrityError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise StorageIntegrityError("stored artifact is unavailable") from exc

    def _verified_byte_target(self, relative_path: str) -> tuple[tuple[str, ...], Path]:
        if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
            raise StorageIntegrityError("stored artifact is unavailable")
        raw = PurePosixPath(relative_path)
        if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
            raise StorageIntegrityError("stored artifact is unavailable")
        target = self.root.joinpath(*raw.parts)
        try:
            if target.relative_to(self.root).as_posix() != relative_path:
                raise StorageIntegrityError("stored artifact is unavailable")
        except ValueError as exc:
            raise StorageIntegrityError("stored artifact is unavailable") from exc
        return raw.parts, target

    @staticmethod
    def _validate_byte_expectations(
        expected_sha256: str,
        *,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> None:
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
            or isinstance(expected_size_bytes, bool)
            or not isinstance(expected_size_bytes, int)
            or expected_size_bytes < 0
            or isinstance(max_size_bytes, bool)
            or not isinstance(max_size_bytes, int)
            or max_size_bytes < 0
            or expected_size_bytes > max_size_bytes
        ):
            raise StorageIntegrityError("stored artifact is unavailable")

    def _read_verified_bytes_posix(
        self,
        parts: tuple[str, ...],
        *,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> bytes:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        directory_only = getattr(os, "O_DIRECTORY", 0)
        if not no_follow or not directory_only:  # pragma: no cover - Linux production has both
            raise StorageIntegrityError("stored artifact is unavailable")
        directory_fd = os.open(self.root, os.O_RDONLY | directory_only | no_follow)
        file_fd: int | None = None
        try:
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | directory_only | no_follow,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | no_follow, dir_fd=directory_fd)
            return self._read_bounded_descriptor(
                file_fd,
                expected_size_bytes=expected_size_bytes,
                max_size_bytes=max_size_bytes,
            )
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(directory_fd)

    def _read_verified_bytes_portable(
        self,
        parts: tuple[str, ...],
        target: Path,
        *,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> bytes:
        cursor = self.root
        for part in parts[:-1]:
            cursor = cursor / part
            component = cursor.lstat()
            if stat.S_ISLNK(component.st_mode) or not stat.S_ISDIR(component.st_mode):
                raise StorageIntegrityError("stored artifact is unavailable")
        target_before = target.lstat()
        if stat.S_ISLNK(target_before.st_mode) or not stat.S_ISREG(target_before.st_mode):
            raise StorageIntegrityError("stored artifact is unavailable")
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            if not os.path.samestat(target_before, opened):
                raise StorageIntegrityError("stored artifact is unavailable")
            payload = self._read_bounded_descriptor(
                descriptor,
                expected_size_bytes=expected_size_bytes,
                max_size_bytes=max_size_bytes,
            )
            target_after = target.lstat()
            if stat.S_ISLNK(target_after.st_mode) or not os.path.samestat(opened, target_after):
                raise StorageIntegrityError("stored artifact is unavailable")
            cursor = self.root
            for part in parts[:-1]:
                cursor = cursor / part
                component = cursor.lstat()
                if stat.S_ISLNK(component.st_mode) or not stat.S_ISDIR(component.st_mode):
                    raise StorageIntegrityError("stored artifact is unavailable")
            return payload
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_bounded_descriptor(
        descriptor: int,
        *,
        expected_size_bytes: int,
        max_size_bytes: int,
    ) -> bytes:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != expected_size_bytes
            or before.st_size > max_size_bytes
        ):
            raise StorageIntegrityError("stored artifact is unavailable")
        chunks: list[bytes] = []
        remaining = max_size_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(payload) != expected_size_bytes
            or len(payload) > max_size_bytes
            or after.st_size != before.st_size
        ):
            raise StorageIntegrityError("stored artifact is unavailable")
        return payload

    def remove_new_file(self, relative_path: str) -> None:
        """Best-effort compensation for a failed database transaction only."""
        try:
            self._absolute(relative_path).unlink(missing_ok=True)
        except OSError:
            pass

    def delete_exact(self, relative_path: str) -> None:
        """Permanently remove one canonical, regular file beneath this store only."""
        if not relative_path or "\\" in relative_path:
            raise StorageIntegrityError("storage deletion path is not canonical")
        raw = PurePosixPath(relative_path)
        if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
            raise StorageIntegrityError("storage deletion path is not canonical")
        target = self._absolute(relative_path)
        if target.relative_to(self.root).as_posix() != relative_path:
            raise StorageIntegrityError("storage deletion path is not canonical")
        try:
            cursor = self.root
            for part in raw.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise StorageIntegrityError("storage deletion path contains a symlink")
            if not target.exists():
                return
            if not target.is_file():
                raise StorageIntegrityError("storage deletion target is not a regular file")
            target.unlink()
            self._fsync_directory(target.parent)
        except StorageIntegrityError:
            raise
        except OSError as exc:
            raise StorageIntegrityError("retention artifact could not be removed") from exc
