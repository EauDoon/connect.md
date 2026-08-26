from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from app.services.backup_archive import BackupArchiveError, validate_backup_archive


def write_archive(path: Path, members: list[tarfile.TarInfo]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            payload = b"x" * member.size if member.isreg() else None
            archive.addfile(member, io.BytesIO(payload) if payload is not None else None)


def directory(name: str) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = 0o755
    return member


def regular_file(name: str, size: int = 1) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = tarfile.REGTYPE
    member.mode = 0o600
    member.size = size
    return member


def test_accepts_canonical_backup_members(tmp_path: Path) -> None:
    archive = tmp_path / "storage.tar.gz"
    write_archive(
        archive,
        [
            directory("."),
            directory("./profiles/"),
            regular_file("./profiles/10000000-0000-4000-8000-000000000001-v1.md"),
        ],
    )

    validate_backup_archive(archive)


@pytest.mark.parametrize(
    "member",
    [
        regular_file("../escape.md"),
        regular_file("/absolute.md"),
        regular_file("profiles//duplicate-separator.md"),
        regular_file("profiles\\windows-separator.md"),
        regular_file("profiles/newline\nname.md"),
        regular_file("profiles/non-ascii-\u00e9.md"),
    ],
)
def test_rejects_noncanonical_or_confusable_names(tmp_path: Path, member: tarfile.TarInfo) -> None:
    archive = tmp_path / "storage.tar.gz"
    write_archive(archive, [member])

    with pytest.raises(BackupArchiveError, match="member_name_invalid"):
        validate_backup_archive(archive)


@pytest.mark.parametrize(
    "member_type",
    [
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.FIFOTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        b"S",
    ],
)
def test_rejects_links_and_special_files(tmp_path: Path, member_type: bytes) -> None:
    archive = tmp_path / "storage.tar.gz"
    member = tarfile.TarInfo("./unsafe")
    member.type = member_type
    member.linkname = "./target"
    write_archive(archive, [member])

    with pytest.raises(BackupArchiveError, match="member_type_invalid"):
        validate_backup_archive(archive)


def test_rejects_duplicate_normalized_names(tmp_path: Path) -> None:
    archive = tmp_path / "storage.tar.gz"
    write_archive(archive, [regular_file("./same.md"), regular_file("same.md")])

    with pytest.raises(BackupArchiveError, match="duplicate_member"):
        validate_backup_archive(archive)


def test_rejects_duplicate_root_markers(tmp_path: Path) -> None:
    archive = tmp_path / "storage.tar.gz"
    write_archive(archive, [directory("."), directory("./")])

    with pytest.raises(BackupArchiveError, match="duplicate_member"):
        validate_backup_archive(archive)


@pytest.mark.parametrize("file_first", [True, False])
def test_rejects_regular_file_as_member_ancestor(tmp_path: Path, file_first: bool) -> None:
    archive = tmp_path / "storage.tar.gz"
    file = regular_file("./node")
    descendant = regular_file("./node/child.md")
    write_archive(archive, [file, descendant] if file_first else [descendant, file])

    with pytest.raises(BackupArchiveError, match="member_structure_invalid"):
        validate_backup_archive(archive)


def test_rejects_excessive_member_count_before_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "storage.tar.gz"
    write_archive(archive, [regular_file("./one.md"), regular_file("./two.md")])

    with pytest.raises(BackupArchiveError, match="member_count_exceeded"):
        validate_backup_archive(archive, max_members=1)


def test_rejects_excessive_expanded_size_before_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "storage.tar.gz"
    write_archive(archive, [regular_file("./large.md", size=2)])

    with pytest.raises(BackupArchiveError, match="expanded_size_exceeded"):
        validate_backup_archive(archive, max_expanded_bytes=1)


def test_rejects_excessive_archive_size_before_opening(tmp_path: Path) -> None:
    archive = tmp_path / "storage.tar.gz"
    write_archive(archive, [regular_file("./small.md")])

    with pytest.raises(BackupArchiveError, match="archive_size_exceeded"):
        validate_backup_archive(archive, max_archive_bytes=1)


def test_rejects_unreadable_archive_with_generic_error(tmp_path: Path) -> None:
    archive = tmp_path / "storage.tar.gz"
    archive.write_bytes(b"not a gzip tar archive")

    with pytest.raises(BackupArchiveError, match="archive_unreadable"):
        validate_backup_archive(archive)
