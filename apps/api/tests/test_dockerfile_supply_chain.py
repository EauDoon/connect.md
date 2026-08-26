from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPOSITORY_ROOT / "apps" / "api" / "Dockerfile"


def test_api_dockerfile_uses_one_immutable_debian_snapshot_and_exact_packages() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    assert (
        "python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b"
    ) in source
    assert source.count("ARG DEBIAN_SNAPSHOT=20260805T010740Z") == 1
    assert source.count("https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}") == 1
    assert "Suites: trixie" in source
    assert "Check-Valid-Until: no" in source
    assert "rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*" in source
    assert "deb.debian.org" not in source
    assert "security.debian.org" not in source
    assert "apt-get upgrade" not in source

    install = re.search(
        r"apt-get install -y --no-install-recommends(?P<body>.*?)"
        r"\s+&& test \"\$\(dpkg-query",
        source,
        flags=re.DOTALL,
    )
    assert install is not None
    package_lines = {
        line.strip().removesuffix(" \\")
        for line in install.group("body").splitlines()
        if line.strip() and line.strip() != "\\"
    }
    assert package_lines == {
        "libmagic1t64=1:5.46-5",
        "poppler-utils=25.03.0-5+deb13u4",
        "tesseract-ocr=5.5.0-1+b1",
    }
    assert "dpkg-query -W" in source
    for package in package_lines:
        assert source.count(f"'{package}'") == 1


def test_api_dockerfile_rejects_bare_or_moving_apt_inputs() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    assert re.search(r"(?m)^\s+libmagic1(?:\s|\\|$)", source) is None
    assert re.search(r"(?m)^\s+poppler-utils(?:\s|\\|$)", source) is None
    assert re.search(r"(?m)^\s+tesseract-ocr(?:\s|\\|$)", source) is None
    assert "snapshot.debian.org/archive/debian/latest" not in source
