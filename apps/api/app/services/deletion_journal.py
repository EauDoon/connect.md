"""External append-only account-deletion commitments and rollback gates."""

from __future__ import annotations

import importlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    decrypt_lifecycle_provider_subject,
    encrypt_lifecycle_provider_subject,
    lifecycle_hmac,
)
from app.config import Settings
from app.models import AccountAccessDeny, AccountLifecycle

_FORMAT = "connectmd-deletion-journal-v1"
_WITNESS_FORMAT = "connectmd-deletion-head-witness-v1"
_ZERO_DIGEST = "0" * 64
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY_NAME = re.compile(r"([0-9]{20})\.json\Z")

# Operational rollback and restore scripts probe this before allowing a target
# image to interpret the external deletion authorities.
DELETION_AUTHORITY_CONTRACT_VERSION = 1


class DeletionJournalError(RuntimeError):
    """The external deletion authority is unavailable or inconsistent."""


@dataclass(frozen=True)
class DeletionCommitment:
    sequence: int
    deletion_id: str
    subject_hmac: str
    subject_ciphertext: str
    backup_generation_id: str
    backup_generation_created_at: datetime
    committed_at: datetime
    policy_version: str


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _utc_text(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DeletionJournalError(f"deletion journal {field} is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DeletionJournalError(f"deletion journal {field} is invalid") from exc
    if _utc_text(parsed) != value:
        raise DeletionJournalError(f"deletion journal {field} is not canonical UTC")
    return parsed


def _credential_fingerprint(value: str | None, name: str) -> str:
    if value is None or len(value.encode("utf-8")) < 32:
        raise DeletionJournalError(f"{name} is unavailable for deletion journal verification")
    return sha256(value.encode("utf-8")).hexdigest()


class DeletionCommitmentJournal:
    """HMAC-chained files outside every restorable database/storage generation."""

    def __init__(self, settings: Settings):
        if settings.deletion_journal_path is None:
            raise DeletionJournalError("deletion journal path is not configured")
        if settings.deletion_witness_path is None:
            raise DeletionJournalError("deletion witness path is not configured")
        self.root = settings.deletion_journal_path
        self.entries_root = self.root / "entries"
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".lock"
        self.witness_root = settings.deletion_witness_path
        self.witness_entries_root = self.witness_root / "entries"
        self.witness_lock_path = self.witness_root / ".lock"
        journal_resolved = self.root.resolve(strict=False)
        witness_resolved = self.witness_root.resolve(strict=False)
        if (
            journal_resolved == witness_resolved
            or journal_resolved.is_relative_to(witness_resolved)
            or witness_resolved.is_relative_to(journal_resolved)
        ):
            raise DeletionJournalError(
                "deletion witness authority is not independent from the journal"
            )
        self._hmac_key = settings.lifecycle_hmac_key
        self._witness_hmac_key = settings.deletion_witness_hmac_key
        self.hmac_fingerprint = _credential_fingerprint(
            settings.lifecycle_hmac_key, "lifecycle HMAC key"
        )
        self.aead_fingerprint = _credential_fingerprint(
            settings.lifecycle_aead_key, "lifecycle AEAD key"
        )
        self.witness_hmac_fingerprint = _credential_fingerprint(
            settings.deletion_witness_hmac_key, "deletion witness HMAC key"
        )
        self.settings = settings

    def _mac(self, label: str, payload: dict[str, Any]) -> str:
        assert self._hmac_key is not None
        return hmac_new(
            self._hmac_key.encode("utf-8"),
            b"connect.md:deletion-journal:v1:" + label.encode("ascii") + b":" + _canonical(payload),
            sha256,
        ).hexdigest()

    def _witness_mac(self, payload: dict[str, Any]) -> str:
        assert self._witness_hmac_key is not None
        return hmac_new(
            self._witness_hmac_key.encode("utf-8"),
            b"connect.md:deletion-head-witness:v1:" + _canonical(payload),
            sha256,
        ).hexdigest()

    @contextmanager
    def _authority_locked(
        self, *, root: Path, lock_path: Path, label: str, exclusive: bool
    ) -> Iterator[None]:
        if not root.is_dir() or root.is_symlink():
            raise DeletionJournalError(f"{label} is missing or unsafe")
        if lock_path.is_symlink():
            raise DeletionJournalError(f"{label} lock path is unsafe")
        flags = os.O_RDWR | os.O_CREAT if exclusive or os.name == "nt" else os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise DeletionJournalError(f"{label} lock path is unsafe") from exc
        if os.name != "nt":
            lock_metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(lock_metadata.st_mode)
                or stat.S_IMODE(lock_metadata.st_mode) != 0o600
                or lock_metadata.st_nlink != 1
            ):
                os.close(descriptor)
                raise DeletionJournalError(f"{label} lock permissions are unsafe")
        handle = os.fdopen(descriptor, "a+b" if exclusive or os.name == "nt" else "rb")
        try:
            if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI only
                import msvcrt

                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - production path
                fcntl_module: Any = importlib.import_module("fcntl")

                fcntl_module.flock(
                    handle.fileno(),
                    fcntl_module.LOCK_EX if exclusive else fcntl_module.LOCK_SH,
                )
            yield
        finally:
            if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI only
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - production path
                fcntl_module = importlib.import_module("fcntl")

                fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_UN)
            handle.close()

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        with self._authority_locked(
            root=self.root,
            lock_path=self.lock_path,
            label="deletion journal",
            exclusive=exclusive,
        ):
            yield

    @contextmanager
    def _witness_locked(self, *, exclusive: bool) -> Iterator[None]:
        with self._authority_locked(
            root=self.witness_root,
            lock_path=self.witness_lock_path,
            label="deletion witness authority",
            exclusive=exclusive,
        ):
            yield

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_new(path: Path, payload: bytes) -> None:
        temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path)
        except FileExistsError as exc:
            raise DeletionJournalError(
                f"refusing to overwrite deletion journal path: {path.name}"
            ) from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        DeletionCommitmentJournal._fsync_directory(path.parent)

    def _replace_state(self, payload: bytes) -> None:
        temporary = self.root / f".state.{os.getpid()}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
            self._fsync_directory(self.root)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _witness_payload(
        self,
        *,
        sequence: int,
        journal_head_digest: str,
        prior_witness_digest: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format": _WITNESS_FORMAT,
            "sequence": sequence,
            "journal_head_digest": journal_head_digest,
            "prior_witness_digest": prior_witness_digest,
            "observed_at": _utc_text(observed_at),
            "journal_hmac_key_sha256": self.hmac_fingerprint,
            "journal_aead_key_sha256": self.aead_fingerprint,
            "witness_hmac_key_sha256": self.witness_hmac_fingerprint,
        }
        payload["witness_mac"] = self._witness_mac(payload)
        return payload

    def _verify_witness_locked(
        self, state: dict[str, Any], commitments: list[DeletionCommitment]
    ) -> None:
        if not self.witness_entries_root.is_dir() or self.witness_entries_root.is_symlink():
            raise DeletionJournalError("deletion witness entries path is missing or unsafe")
        if os.name != "nt":
            if stat.S_IMODE(self.witness_root.stat(follow_symlinks=False).st_mode) != 0o700:
                raise DeletionJournalError("deletion witness root permissions are unsafe")
            if stat.S_IMODE(self.witness_entries_root.stat(follow_symlinks=False).st_mode) != 0o700:
                raise DeletionJournalError("deletion witness entries permissions are unsafe")
        head_sequence = int(state["head_sequence"])
        head_digest = str(state["head_digest"])
        paths = sorted(self.witness_entries_root.iterdir(), key=lambda item: item.name)
        if len(paths) != head_sequence + 1:
            raise DeletionJournalError("deletion witness and journal head sequences do not match")
        prior_witness_digest = _ZERO_DIGEST
        expected_keys = {
            "format",
            "sequence",
            "journal_head_digest",
            "prior_witness_digest",
            "observed_at",
            "journal_hmac_key_sha256",
            "journal_aead_key_sha256",
            "witness_hmac_key_sha256",
            "witness_mac",
        }
        for sequence, path in enumerate(paths):
            match = _ENTRY_NAME.fullmatch(path.name)
            if match is None or int(match.group(1)) != sequence:
                raise DeletionJournalError("deletion witness sequence is not contiguous")
            witness = self._read_json(path)
            if set(witness) != expected_keys or witness.get("format") != _WITNESS_FORMAT:
                raise DeletionJournalError("deletion witness schema is invalid")
            supplied_mac = witness.pop("witness_mac", None)
            valid_mac = self._witness_mac(witness)
            witness["witness_mac"] = supplied_mac
            if not isinstance(supplied_mac, str) or not compare_digest(supplied_mac, valid_mac):
                raise DeletionJournalError("deletion witness authentication failed")
            expected_journal_digest = (
                _ZERO_DIGEST
                if sequence == 0
                else sha256((self.entries_root / f"{sequence:020d}.json").read_bytes()).hexdigest()
            )
            expected_observed_at = (
                _parse_utc(state.get("created_at"), "creation time")
                if sequence == 0
                else commitments[sequence - 1].committed_at
            )
            if (
                witness.get("sequence") != sequence
                or witness.get("journal_head_digest") != expected_journal_digest
                or witness.get("prior_witness_digest") != prior_witness_digest
                or witness.get("journal_hmac_key_sha256") != self.hmac_fingerprint
                or witness.get("journal_aead_key_sha256") != self.aead_fingerprint
                or witness.get("witness_hmac_key_sha256") != self.witness_hmac_fingerprint
                or _parse_utc(witness.get("observed_at"), "witness observation time")
                != expected_observed_at
            ):
                raise DeletionJournalError("deletion witness does not match journal history")
            encoded = _canonical(witness) + b"\n"
            prior_witness_digest = sha256(encoded).hexdigest()
        if paths[-1].name != f"{head_sequence:020d}.json":
            raise DeletionJournalError("deletion witness head is invalid")
        final_witness = self._read_json(paths[-1])
        if final_witness.get("journal_head_digest") != head_digest:
            raise DeletionJournalError("deletion witness and journal head digests do not match")

    def initialize(self, *, created_at: datetime | None = None) -> None:
        try:
            if self.root.exists() and (not self.root.is_dir() or self.root.is_symlink()):
                raise DeletionJournalError("deletion journal root is unsafe")
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.entries_root.mkdir(mode=0o700, exist_ok=True)
            if self.witness_root.exists() and (
                not self.witness_root.is_dir() or self.witness_root.is_symlink()
            ):
                raise DeletionJournalError("deletion witness root is unsafe")
            self.witness_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.witness_entries_root.mkdir(mode=0o700, exist_ok=True)
            if os.name != "nt":
                self.root.chmod(0o700)
                self.entries_root.chmod(0o700)
                self.witness_root.chmod(0o700)
                self.witness_entries_root.chmod(0o700)
            if self.entries_root.is_symlink() or self.witness_entries_root.is_symlink():
                raise DeletionJournalError("deletion journal entries path is unsafe")
            with self._locked(exclusive=True):
                with self._witness_locked(exclusive=True):
                    if self.state_path.exists():
                        self._verify_locked()
                        return
                    witness_paths = sorted(
                        self.witness_entries_root.iterdir(), key=lambda item: item.name
                    )
                    if witness_paths:
                        if len(witness_paths) != 1 or witness_paths[0].name != f"{0:020d}.json":
                            raise DeletionJournalError("deletion witness is partially initialized")
                        witness_zero = self._read_json(witness_paths[0])
                        created = _parse_utc(
                            witness_zero.get("observed_at"), "witness observation time"
                        )
                    else:
                        created = created_at or datetime.now(UTC)
                    state = {
                        "format": _FORMAT,
                        "created_at": _utc_text(created),
                        "hmac_key_sha256": self.hmac_fingerprint,
                        "aead_key_sha256": self.aead_fingerprint,
                        "witness_hmac_key_sha256": self.witness_hmac_fingerprint,
                        "head_sequence": 0,
                        "head_digest": _ZERO_DIGEST,
                    }
                    state["state_mac"] = self._mac("state", state)
                    if not witness_paths:
                        witness_zero = self._witness_payload(
                            sequence=0,
                            journal_head_digest=_ZERO_DIGEST,
                            prior_witness_digest=_ZERO_DIGEST,
                            observed_at=created,
                        )
                        self._write_new(
                            self.witness_entries_root / f"{0:020d}.json",
                            _canonical(witness_zero) + b"\n",
                        )
                    self._verify_witness_locked(state, [])
                    self._write_new(self.state_path, _canonical(state) + b"\n")
                    self._verify_locked()
        except DeletionJournalError:
            raise
        except OSError as exc:
            raise DeletionJournalError("deletion journal filesystem operation failed") from exc

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            raise DeletionJournalError(f"deletion journal path is missing or unsafe: {path.name}")
        if os.name != "nt":
            metadata = path.stat(follow_symlinks=False)
            if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
                raise DeletionJournalError(f"deletion journal permissions are unsafe: {path.name}")
        try:
            raw = path.read_bytes()
            if not raw.endswith(b"\n") or len(raw) > 16 * 1024:
                raise ValueError
            payload = json.loads(raw)
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise DeletionJournalError(f"deletion journal record is invalid: {path.name}") from exc
        if not isinstance(payload, dict):
            raise DeletionJournalError(f"deletion journal record is invalid: {path.name}")
        return payload

    def _verify_locked(self) -> tuple[dict[str, Any], list[DeletionCommitment]]:
        if not self.entries_root.is_dir() or self.entries_root.is_symlink():
            raise DeletionJournalError("deletion journal entries path is missing or unsafe")
        if os.name != "nt":
            if stat.S_IMODE(self.root.stat(follow_symlinks=False).st_mode) != 0o700:
                raise DeletionJournalError("deletion journal root permissions are unsafe")
            if stat.S_IMODE(self.entries_root.stat(follow_symlinks=False).st_mode) != 0o700:
                raise DeletionJournalError("deletion journal entries permissions are unsafe")
        state = self._read_json(self.state_path)
        expected_state_keys = {
            "format",
            "created_at",
            "hmac_key_sha256",
            "aead_key_sha256",
            "witness_hmac_key_sha256",
            "head_sequence",
            "head_digest",
            "state_mac",
        }
        if set(state) != expected_state_keys or state.get("format") != _FORMAT:
            raise DeletionJournalError("deletion journal state schema is invalid")
        _parse_utc(state.get("created_at"), "creation time")
        if state.get("hmac_key_sha256") != self.hmac_fingerprint:
            raise DeletionJournalError("lifecycle HMAC key does not match pinned journal state")
        if state.get("aead_key_sha256") != self.aead_fingerprint:
            raise DeletionJournalError("lifecycle AEAD key does not match pinned journal state")
        if state.get("witness_hmac_key_sha256") != self.witness_hmac_fingerprint:
            raise DeletionJournalError(
                "deletion witness HMAC key does not match pinned journal state"
            )
        supplied_state_mac = state.pop("state_mac", None)
        valid_state_mac = self._mac("state", state)
        state["state_mac"] = supplied_state_mac
        if not isinstance(supplied_state_mac, str) or not compare_digest(
            supplied_state_mac, valid_state_mac
        ):
            raise DeletionJournalError("deletion journal state authentication failed")
        head_sequence = state.get("head_sequence")
        head_digest = state.get("head_digest")
        if (
            not isinstance(head_sequence, int)
            or isinstance(head_sequence, bool)
            or head_sequence < 0
            or not isinstance(head_digest, str)
            or not _DIGEST.fullmatch(head_digest)
        ):
            raise DeletionJournalError("deletion journal head is invalid")

        paths = sorted(self.entries_root.iterdir(), key=lambda item: item.name)
        if len(paths) != head_sequence:
            raise DeletionJournalError("deletion journal entry count does not match its head")
        prior_digest = _ZERO_DIGEST
        commitments: list[DeletionCommitment] = []
        seen_deletions: set[str] = set()
        for sequence, path in enumerate(paths, start=1):
            match = _ENTRY_NAME.fullmatch(path.name)
            if match is None or int(match.group(1)) != sequence:
                raise DeletionJournalError("deletion journal sequence is not contiguous")
            entry = self._read_json(path)
            expected_entry_keys = {
                "format",
                "sequence",
                "deletion_id",
                "subject_hmac",
                "subject_ciphertext",
                "backup_generation_id",
                "backup_generation_created_at",
                "committed_at",
                "policy_version",
                "prior_digest",
                "entry_mac",
            }
            if set(entry) != expected_entry_keys or entry.get("format") != _FORMAT:
                raise DeletionJournalError("deletion journal entry schema is invalid")
            supplied_entry_mac = entry.pop("entry_mac", None)
            valid_entry_mac = self._mac("entry", entry)
            entry["entry_mac"] = supplied_entry_mac
            if not isinstance(supplied_entry_mac, str) or not compare_digest(
                supplied_entry_mac, valid_entry_mac
            ):
                raise DeletionJournalError("deletion journal entry authentication failed")
            if entry.get("sequence") != sequence or entry.get("prior_digest") != prior_digest:
                raise DeletionJournalError("deletion journal chain is invalid")
            deletion_id = entry.get("deletion_id")
            subject_hmac = entry.get("subject_hmac")
            subject_ciphertext = entry.get("subject_ciphertext")
            generation_id = entry.get("backup_generation_id")
            policy_version = entry.get("policy_version")
            if (
                not isinstance(deletion_id, str)
                or not deletion_id
                or deletion_id in seen_deletions
                or not isinstance(subject_hmac, str)
                or not _DIGEST.fullmatch(subject_hmac)
                or not isinstance(subject_ciphertext, str)
                or not subject_ciphertext.startswith("v1.")
                or not isinstance(generation_id, str)
                or not generation_id
                or not isinstance(policy_version, str)
                or not policy_version
            ):
                raise DeletionJournalError("deletion journal entry values are invalid")
            seen_deletions.add(deletion_id)
            generation_created_at = _parse_utc(
                entry.get("backup_generation_created_at"), "backup generation creation time"
            )
            committed_at = _parse_utc(entry.get("committed_at"), "commitment time")
            try:
                subject = decrypt_lifecycle_provider_subject(
                    self.settings,
                    deletion_id=deletion_id,
                    ciphertext=subject_ciphertext,
                )
            except Exception as exc:
                raise DeletionJournalError(
                    "deletion journal subject ciphertext is invalid"
                ) from exc
            if not compare_digest(subject_hmac, lifecycle_hmac(self.settings, "subject", subject)):
                raise DeletionJournalError(
                    "deletion journal subject ciphertext does not match its keyed digest"
                )
            commitments.append(
                DeletionCommitment(
                    sequence=sequence,
                    deletion_id=deletion_id,
                    subject_hmac=subject_hmac,
                    subject_ciphertext=subject_ciphertext,
                    backup_generation_id=generation_id,
                    backup_generation_created_at=generation_created_at,
                    committed_at=committed_at,
                    policy_version=policy_version,
                )
            )
            prior_digest = sha256(_canonical(entry) + b"\n").hexdigest()
        if prior_digest != head_digest:
            raise DeletionJournalError("deletion journal head digest is invalid")
        self._verify_witness_locked(state, commitments)
        return state, commitments

    def verify(self) -> list[DeletionCommitment]:
        try:
            with self._locked(exclusive=False):
                with self._witness_locked(exclusive=False):
                    _, commitments = self._verify_locked()
                    return commitments
        except DeletionJournalError:
            raise
        except OSError as exc:
            raise DeletionJournalError("deletion journal filesystem operation failed") from exc

    def checkpoint(self) -> tuple[int, str]:
        try:
            with self._locked(exclusive=False):
                with self._witness_locked(exclusive=False):
                    state, _ = self._verify_locked()
                    return int(state["head_sequence"]), str(state["head_digest"])
        except DeletionJournalError:
            raise
        except OSError as exc:
            raise DeletionJournalError("deletion journal filesystem operation failed") from exc

    def assert_checkpoint(self, *, head_sequence: int, head_digest: str) -> None:
        current_sequence, current_digest = self.checkpoint()
        if head_sequence != current_sequence or not compare_digest(head_digest, current_digest):
            raise DeletionJournalError(
                "backup generation does not cover the current deletion journal head"
            )

    def append(
        self,
        *,
        deletion_id: str,
        subject: str,
        subject_hmac: str,
        backup_generation_id: str,
        backup_generation_created_at: datetime,
        committed_at: datetime,
        policy_version: str,
    ) -> DeletionCommitment:
        expected_subject_hmac = lifecycle_hmac(self.settings, "subject", subject)
        if not compare_digest(subject_hmac, expected_subject_hmac):
            raise DeletionJournalError("deletion commitment subject digest does not match")
        try:
            with self._locked(exclusive=True):
                with self._witness_locked(exclusive=True):
                    state, commitments = self._verify_locked()
                    for existing in commitments:
                        if existing.deletion_id != deletion_id:
                            continue
                        if (
                            existing.subject_hmac != subject_hmac
                            or not compare_digest(
                                decrypt_lifecycle_provider_subject(
                                    self.settings,
                                    deletion_id=existing.deletion_id,
                                    ciphertext=existing.subject_ciphertext,
                                ),
                                subject,
                            )
                            or existing.backup_generation_id != backup_generation_id
                            or existing.backup_generation_created_at
                            != (
                                backup_generation_created_at
                                if backup_generation_created_at.tzinfo is not None
                                else backup_generation_created_at.replace(tzinfo=UTC)
                            ).astimezone(UTC)
                            or existing.policy_version != policy_version
                        ):
                            raise DeletionJournalError("deletion commitment replay does not match")
                        return existing
                    sequence = int(state["head_sequence"]) + 1
                    entry: dict[str, Any] = {
                        "format": _FORMAT,
                        "sequence": sequence,
                        "deletion_id": deletion_id,
                        "subject_hmac": subject_hmac,
                        "subject_ciphertext": encrypt_lifecycle_provider_subject(
                            self.settings, deletion_id=deletion_id, subject=subject
                        ),
                        "backup_generation_id": backup_generation_id,
                        "backup_generation_created_at": _utc_text(backup_generation_created_at),
                        "committed_at": _utc_text(committed_at),
                        "policy_version": policy_version,
                        "prior_digest": state["head_digest"],
                    }
                    entry["entry_mac"] = self._mac("entry", entry)
                    encoded_entry = _canonical(entry) + b"\n"
                    entry_path = self.entries_root / f"{sequence:020d}.json"
                    self._write_new(entry_path, encoded_entry)
                    new_state = {key: value for key, value in state.items() if key != "state_mac"}
                    new_state["head_sequence"] = sequence
                    new_state["head_digest"] = sha256(encoded_entry).hexdigest()
                    new_state["state_mac"] = self._mac("state", new_state)
                    self._replace_state(_canonical(new_state) + b"\n")
                    prior_witness_path = self.witness_entries_root / f"{sequence - 1:020d}.json"
                    prior_witness_digest = sha256(prior_witness_path.read_bytes()).hexdigest()
                    witness = self._witness_payload(
                        sequence=sequence,
                        journal_head_digest=str(new_state["head_digest"]),
                        prior_witness_digest=prior_witness_digest,
                        observed_at=committed_at,
                    )
                    self._write_new(
                        self.witness_entries_root / f"{sequence:020d}.json",
                        _canonical(witness) + b"\n",
                    )
                    return self._verify_locked()[1][-1]
        except DeletionJournalError:
            raise
        except OSError as exc:
            raise DeletionJournalError("deletion journal filesystem operation failed") from exc


async def verify_live_deletion_mirror(
    session: AsyncSession, journal: DeletionCommitmentJournal
) -> int:
    """Require every external commitment to have a concealed live DB mirror."""
    commitments = journal.verify()
    committed_by_id = {item.deletion_id: item for item in commitments}
    lifecycles = (
        await session.scalars(
            select(AccountLifecycle).where(AccountLifecycle.state != "confirmation_pending")
        )
    ).all()
    denies = (await session.scalars(select(AccountAccessDeny))).all()
    lifecycle_by_id = {item.id: item for item in lifecycles}
    deny_by_id = {item.deletion_id: item for item in denies}
    if set(committed_by_id) != set(lifecycle_by_id) or set(committed_by_id) != set(deny_by_id):
        raise DeletionJournalError(
            "deletion journal and live database commitment sets do not match"
        )
    for commitment in commitments:
        lifecycle = lifecycle_by_id[commitment.deletion_id]
        deny = deny_by_id[commitment.deletion_id]
        if (
            lifecycle.subject_hmac != commitment.subject_hmac
            or lifecycle.state == "confirmation_pending"
            or lifecycle.policy_version != commitment.policy_version
            or lifecycle.confirmed_at is None
            or lifecycle.concealed_at is None
            or deny.subject_hmac != commitment.subject_hmac
            or (
                lifecycle.confirmed_at
                if lifecycle.confirmed_at.tzinfo is not None
                else lifecycle.confirmed_at.replace(tzinfo=UTC)
            ).astimezone(UTC)
            != commitment.committed_at
            or (
                lifecycle.concealed_at
                if lifecycle.concealed_at.tzinfo is not None
                else lifecycle.concealed_at.replace(tzinfo=UTC)
            ).astimezone(UTC)
            != commitment.committed_at
            or (
                deny.denied_at
                if deny.denied_at.tzinfo is not None
                else deny.denied_at.replace(tzinfo=UTC)
            ).astimezone(UTC)
            != commitment.committed_at
        ):
            raise DeletionJournalError(
                "live database has not applied every durable deletion commitment"
            )
    return len(commitments)
