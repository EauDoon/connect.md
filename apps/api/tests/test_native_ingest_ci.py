from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def smoke():
    spec = importlib.util.spec_from_file_location(
        "check_native_ingest", ROOT / "tools" / "check_native_ingest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("missing", ["pdftoppm", "tesseract"])
def test_native_smoke_fails_if_either_binary_is_absent(smoke, monkeypatch, missing):
    monkeypatch.setattr(smoke.shutil, "which", lambda name: None if name == missing else name)
    with pytest.raises(RuntimeError, match="required native ingestion executable is missing"):
        smoke.main()


@pytest.mark.parametrize(
    "text,converter", [("Ada Lovelace", "markitdown-local"), ("", "tesseract-local")]
)
def test_native_smoke_requires_scanned_text_and_actual_ocr_provenance(
    smoke, monkeypatch, text, converter
):
    monkeypatch.setattr(smoke.shutil, "which", lambda name: name)
    monkeypatch.setattr(smoke, "_convert_binary", lambda *args, **kwargs: (text, converter, []))
    with pytest.raises(RuntimeError, match="did not complete actual local Tesseract OCR"):
        smoke.main()


def test_native_ci_gate_builds_the_pinned_image_inside_the_required_backend_job():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert set(workflow["jobs"]) == {"secret-scan", "platform-contract", "backend", "frontend"}
    backend = workflow["jobs"]["backend"]
    assert backend["name"] == "API checks (pytest, ruff, mypy, pip-audit)"
    gate = next(
        step
        for step in backend["steps"]
        if step.get("name") == "Build pinned API image and verify offline native ingestion"
    )
    assert gate["working-directory"] == "."
    assert "docker build --file apps/api/Dockerfile --tag connectmd-api-native:ci ." in gate["run"]
    assert "docker run --rm --network none --read-only" in gate["run"]
    assert "connectmd-api-native:ci python /checks/check_native_ingest.py" in gate["run"]
    assert "continue-on-error" not in gate
    assert "|| true" not in gate["run"]
