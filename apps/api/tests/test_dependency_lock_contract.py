from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "apps" / "api"
REQUIREMENT_START = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[^\s\\]+(?:\s+\\)?$")
SHA256_HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s+\\)?$")
LINUX_DEPENDENCY_PINS = {
    "cuda-bindings": "13.3.1",
    "cuda-pathfinder": "1.8.0",
    "cuda-toolkit": "13.0.3.0",
    "nvidia-cublas": "13.1.1.3",
    "nvidia-cuda-cupti": "13.0.85",
    "nvidia-cuda-nvrtc": "13.0.88",
    "nvidia-cuda-runtime": "13.0.96",
    "nvidia-cudnn-cu13": "9.20.0.48",
    "nvidia-cufft": "12.0.0.61",
    "nvidia-cufile": "1.15.1.6",
    "nvidia-curand": "10.4.0.35",
    "nvidia-cusolver": "12.0.4.66",
    "nvidia-cusparse": "12.6.3.3",
    "nvidia-cusparselt-cu13": "0.8.1",
    "nvidia-nccl-cu13": "2.29.7",
    "nvidia-nvjitlink": "13.3.33",
    "nvidia-nvshmem-cu13": "3.4.5",
    "nvidia-nvtx": "13.0.85",
    "triton": "3.7.1",
    "uvloop": "0.22.1",
}


def assert_complete_hash_lock(content: str) -> None:
    assert any("--generate-hashes" in line for line in content.splitlines()[:8])
    lines = content.splitlines()
    requirement_indexes = [
        index for index, line in enumerate(lines) if REQUIREMENT_START.fullmatch(line.strip())
    ]
    assert requirement_indexes, "lock contains no pinned requirements"

    for position, index in enumerate(requirement_indexes):
        end = (
            requirement_indexes[position + 1]
            if position + 1 < len(requirement_indexes)
            else len(lines)
        )
        requirement_block = lines[index:end]
        assert any(SHA256_HASH.fullmatch(line.strip()) for line in requirement_block), (
            f"unhashed requirement: {lines[index].strip()}"
        )


@pytest.mark.parametrize("filename", ["requirements.lock", "requirements-test.lock"])
def test_python_dependency_locks_are_complete_sha256_manifests(filename: str) -> None:
    assert_complete_hash_lock((API_ROOT / filename).read_text(encoding="utf-8"))


def test_linux_dependency_closure_is_present_in_both_locks() -> None:
    for filename in ("requirements.lock", "requirements-test.lock"):
        lock = (API_ROOT / filename).read_text(encoding="utf-8")
        for package, version in LINUX_DEPENDENCY_PINS.items():
            assert re.search(
                rf"^{re.escape(package)}=={re.escape(version)} \\$",
                lock,
                re.MULTILINE,
            ), f"missing Linux dependency pin: {package}=={version}"


def test_hash_lock_parser_fails_closed_on_missing_requirement_hash() -> None:
    with pytest.raises(AssertionError, match="unhashed requirement"):
        assert_complete_hash_lock(
            "# pip-compile --generate-hashes\n"
            "safe-package==1.0.0 \\\n"
            "    --hash=sha256:" + "a" * 64 + "\n"
            "substituted-package==2.0.0\n"
        )


def test_build_and_ci_require_authenticated_locks() -> None:
    dockerfile = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "pip install --no-cache-dir --require-hashes -r requirements.lock" in dockerfile
    assert "python -m pip install --require-hashes -r requirements-test.lock" in workflow
    assert "python -m pip_audit -r requirements.lock" in workflow
    assert "pip install --upgrade pip" not in workflow
    assert "pip install pip-audit" not in workflow


def test_audit_tool_is_part_of_the_authenticated_test_lock() -> None:
    pyproject = tomllib.loads((API_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    test_dependencies = pyproject["project"]["optional-dependencies"]["test"]
    assert "pip-audit==2.9.0" in test_dependencies

    test_lock = (API_ROOT / "requirements-test.lock").read_text(encoding="utf-8")
    assert re.search(r"^pip-audit==2\.9\.0 \\$", test_lock, re.MULTILINE)
