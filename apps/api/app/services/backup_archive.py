from __future__ import annotations

import argparse
import tarfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_MEMBERS = 200_000
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 64 * 1024 * 1024 * 1024


class BackupArchiveError(RuntimeError):
    pass


def _canonical_member_name(raw_name: str, *, is_directory: bool) -> str:
    if not raw_name or "\\" in raw_name:
        raise BackupArchiveError("member_name_invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_name):
        raise BackupArchiveError("member_name_invalid")
    try:
        raw_name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise BackupArchiveError("member_name_invalid") from exc

    candidate = raw_name[2:] if raw_name.startswith("./") else raw_name
    if candidate in {"", "."}:
        if is_directory and raw_name in {".", "./"}:
            return "."
        raise BackupArchiveError("member_name_invalid")
    if is_directory and candidate.endswith("/"):
        candidate = candidate[:-1]

    path = PurePosixPath(candidate)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupArchiveError("member_name_invalid")
    canonical = path.as_posix()
    if candidate != canonical:
        raise BackupArchiveError("member_name_invalid")
    return canonical


def validate_backup_archive(
    archive_path: Path,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_archive_bytes: int = MAX_ARCHIVE_BYTES,
    max_expanded_bytes: int = MAX_ARCHIVE_EXPANDED_BYTES,
) -> None:
    if max_members < 1 or max_archive_bytes < 1 or max_expanded_bytes < 1:
        raise ValueError("archive validation bounds must be positive")

    members: dict[str, bool] = {}
    member_count = 0
    expanded_bytes = 0
    try:
        if archive_path.is_symlink() or not archive_path.is_file():
            raise BackupArchiveError("archive_unreadable")
        if archive_path.stat().st_size > max_archive_bytes:
            raise BackupArchiveError("archive_size_exceeded")
        with tarfile.open(archive_path, mode="r|gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > max_members:
                    raise BackupArchiveError("member_count_exceeded")
                if not (member.isdir() or member.isreg()):
                    raise BackupArchiveError("member_type_invalid")
                if getattr(member, "sparse", None) is not None:
                    raise BackupArchiveError("member_type_invalid")
                if member.isdir() and member.size != 0:
                    raise BackupArchiveError("member_size_invalid")

                canonical = _canonical_member_name(member.name, is_directory=member.isdir())
                if canonical in members:
                    raise BackupArchiveError("duplicate_member")
                if canonical != ".":
                    for parent in PurePosixPath(canonical).parents:
                        parent_name = parent.as_posix()
                        if parent_name == ".":
                            break
                        if members.get(parent_name) is False:
                            raise BackupArchiveError("member_structure_invalid")
                    if member.isreg() and any(name.startswith(f"{canonical}/") for name in members):
                        raise BackupArchiveError("member_structure_invalid")
                members[canonical] = member.isdir()

                if member.isreg():
                    expanded_bytes += member.size
                    if expanded_bytes > max_expanded_bytes:
                        raise BackupArchiveError("expanded_size_exceeded")
    except BackupArchiveError:
        raise
    except (OSError, tarfile.TarError, UnicodeError, ValueError) as exc:
        raise BackupArchiveError("archive_unreadable") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        validate_backup_archive(args.archive)
    except BackupArchiveError:
        parser.exit(1, "Backup archive validation failed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
