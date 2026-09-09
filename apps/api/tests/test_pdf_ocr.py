from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.pdf_ocr as ocr
from app.ingest import _convert_binary
from tests.fixtures.ingest.generate import VALID_TEXT, _docx_bytes, _pdf_bytes


def fake_pdf(monkeypatch, pages=1, encrypted=False):
    monkeypatch.setattr(
        ocr, "PdfReader", lambda _: SimpleNamespace(pages=range(pages), is_encrypted=encrypted)
    )


def fake_commands(monkeypatch, text=b"Extracted text", raster_bytes=100):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "pdftoppm":
            Path(args[-1] + ".pgm").write_bytes(b"x" * raster_bytes)
        else:
            Path(args[2] + ".txt").write_bytes(text)

    monkeypatch.setattr(ocr.subprocess, "run", run)
    return calls


def test_ocr_runs_sequential_fixed_local_commands_and_cleans_outputs(tmp_path, monkeypatch):
    fake_pdf(monkeypatch, pages=2)
    calls = fake_commands(monkeypatch)
    source = tmp_path / "source.pdf"
    assert ocr.extract_pdf_ocr(source, 1024) == "Extracted text\nExtracted text"
    assert [args[0] for args, _ in calls] == ["pdftoppm", "tesseract"] * 2
    for index in (0, 2):
        args, kwargs = calls[index]
        assert args[1:6] == ["-f", str(index // 2 + 1), "-l", str(index // 2 + 1), "-singlefile"]
        assert args[6:9] == ["-scale-to", "2400", "-gray"]
        assert args[9] == str(source)
        assert kwargs["shell"] is False
        assert kwargs["check"] is True
        assert 0 < kwargs["timeout"] <= ocr.OCR_TIMEOUT_SECONDS
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert "start_new_session" not in kwargs
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("pages,encrypted", [(0, False), (31, False), (1, True)])
def test_ocr_rejects_page_limits_and_encryption_before_native_calls(
    tmp_path, monkeypatch, pages, encrypted
):
    fake_pdf(monkeypatch, pages, encrypted)
    calls = fake_commands(monkeypatch)
    with pytest.raises(ValueError):
        ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1024)
    assert calls == []


@pytest.mark.parametrize("text,maximum,pages", [(b"abcd", 3, 1), (b"abc", 6, 2), (b"\xff", 100, 1)])
def test_ocr_rejects_text_overflow_including_page_separator_and_invalid_utf8(
    tmp_path, monkeypatch, text, maximum, pages
):
    fake_pdf(monkeypatch, pages)
    fake_commands(monkeypatch, text=text)
    with pytest.raises(ValueError):
        ocr.extract_pdf_ocr(tmp_path / "source.pdf", maximum)
    assert list(tmp_path.iterdir()) == []


def test_ocr_rejects_oversized_raster_before_tesseract(tmp_path, monkeypatch):
    fake_pdf(monkeypatch)
    monkeypatch.setattr(ocr, "MAX_OCR_IMAGE_BYTES", 20)
    calls = fake_commands(monkeypatch, raster_bytes=21)
    with pytest.raises(ValueError, match="raster"):
        ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1024)
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(),
        subprocess.TimeoutExpired("native", 1),
        subprocess.CalledProcessError(1, "native"),
    ],
)
def test_ocr_native_failure_cleans_partial_files(tmp_path, monkeypatch, error):
    fake_pdf(monkeypatch)

    def fail(args, **kwargs):
        (tmp_path / "ocr-page.pgm").write_bytes(b"partial")
        raise error

    monkeypatch.setattr(ocr.subprocess, "run", fail)
    with pytest.raises(type(error)):
        ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1024)
    assert list(tmp_path.iterdir()) == []


def test_ocr_total_deadline_prevents_next_command(tmp_path, monkeypatch):
    fake_pdf(monkeypatch)
    calls = fake_commands(monkeypatch)
    times = iter([0, 1, ocr.OCR_TIMEOUT_SECONDS + 1])
    monkeypatch.setattr(ocr, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError):
        ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1024)
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_ocr_accepts_exact_page_and_utf8_limits(tmp_path, monkeypatch):
    fake_pdf(monkeypatch, pages=ocr.MAX_OCR_PAGES)
    calls = fake_commands(monkeypatch, text="é".encode())
    maximum = ocr.MAX_OCR_PAGES * 3 - 1
    result = ocr.extract_pdf_ocr(tmp_path / "source.pdf", maximum)
    assert len(result.encode("utf-8")) == maximum
    assert len(calls) == ocr.MAX_OCR_PAGES * 2


@pytest.mark.parametrize(
    "raw,maximum,pages,expected",
    [
        (b"abcd\n", 4, 1, "abcd"),
        (b"abcd\n\n", 9, 2, "abcd\nabcd"),
        (" \n\u2003é🙂\n\u3000".encode(), 6, 1, "é🙂"),
        (b"a" + b" " * 20000, 1, 1, "a"),
    ],
    ids=["trailing-newline", "page-separators", "unicode", "long-trailing-whitespace"],
)
def test_native_whitespace_does_not_reject_exact_normalized_text_limits(
    tmp_path, monkeypatch, raw, maximum, pages, expected
):
    fake_pdf(monkeypatch, pages)
    fake_commands(monkeypatch, text=raw)
    assert ocr.extract_pdf_ocr(tmp_path / "source.pdf", maximum) == expected
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("chunk_size", [1, 3, 8])
@pytest.mark.parametrize("text", ["  é🙂\n", "A \t\n B  ", "\u2003A\u3000B\u3000", " \n\t "])
def test_incremental_ocr_normalization_matches_strip_at_utf8_boundaries(
    tmp_path, monkeypatch, chunk_size, text
):
    monkeypatch.setattr(ocr, "OCR_READ_CHUNK_BYTES", chunk_size)
    output = tmp_path / "page.txt"
    output.write_bytes(text.encode())
    expected = text.strip()
    assert ocr._read_ocr_text(output, len(expected.encode()), float("inf")) == expected


@pytest.mark.parametrize("invalid", [b"\xff", b"\xc3", b"\xf0\x9f\x99"])
def test_ocr_rejects_invalid_utf8_even_after_exact_text_and_discarded_whitespace(
    tmp_path, monkeypatch, invalid
):
    fake_pdf(monkeypatch)
    fake_commands(monkeypatch, text=b"A" + b" " * 20000 + invalid)
    with pytest.raises(UnicodeDecodeError):
        ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1)
    assert list(tmp_path.iterdir()) == []


def test_ocr_retains_internal_whitespace_in_its_output_limit(tmp_path, monkeypatch):
    fake_pdf(monkeypatch)
    fake_commands(monkeypatch, text=b"A" + b" " * 20000 + b"B\n")
    with pytest.raises(ValueError, match="extracted-text limit"):
        ocr.extract_pdf_ocr(tmp_path / "source.pdf", 2)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("extra", [0, 1])
def test_raw_native_output_keeps_a_separate_hard_byte_limit(tmp_path, monkeypatch, extra):
    monkeypatch.setattr(ocr, "MAX_OCR_RAW_TEXT_BYTES", 32)
    fake_pdf(monkeypatch)
    fake_commands(monkeypatch, text=b"A" + b" " * (31 + extra))
    if extra:
        with pytest.raises(ValueError, match="raw output"):
            ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1)
    else:
        assert ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1) == "A"
    assert list(tmp_path.iterdir()) == []


def test_ocr_reading_obeys_the_total_deadline_and_cleans_files(tmp_path, monkeypatch):
    fake_pdf(monkeypatch)
    fake_commands(monkeypatch, text=b"A\n")
    times = iter([0, 1, 2, ocr.OCR_TIMEOUT_SECONDS + 1])
    monkeypatch.setattr(ocr, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError):
        ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1)
    assert list(tmp_path.iterdir()) == []


def test_blank_later_page_does_not_add_a_separator_after_filling_the_budget(tmp_path, monkeypatch):
    fake_pdf(monkeypatch, pages=2)
    calls = fake_commands(monkeypatch, text=b"A\n")
    run = ocr.subprocess.run

    def blank_second_page(args, **kwargs):
        run(args, **kwargs)
        if len(calls) == 4:
            Path(args[2] + ".txt").write_bytes(b" \n\t")

    monkeypatch.setattr(ocr.subprocess, "run", blank_second_page)
    assert ocr.extract_pdf_ocr(tmp_path / "source.pdf", 1) == "A"
    assert list(tmp_path.iterdir()) == []


def test_raw_output_reader_never_requests_an_unbounded_read(monkeypatch):
    monkeypatch.setattr(ocr, "MAX_OCR_RAW_TEXT_BYTES", 32)
    monkeypatch.setattr(ocr, "OCR_READ_CHUNK_BYTES", 7)
    read_sizes = []
    returned_sizes = []

    class RecordedStream(io.BytesIO):
        def read(self, size=-1):
            read_sizes.append(size)
            result = super().read(size)
            returned_sizes.append(len(result))
            return result

    output = SimpleNamespace(open=lambda _: RecordedStream(b"A" + b" " * 100))
    with pytest.raises(ValueError, match="raw output"):
        ocr._read_ocr_text(output, 1, float("inf"))
    assert read_sizes == [7, 7, 7, 7, 5]
    assert sum(returned_sizes) == 33


def test_pdf_ocr_failure_returns_no_draft_or_sensitive_diagnostics(monkeypatch):
    import markitdown
    from fastapi import HTTPException

    private_detail = "private-path-and-document-detail"
    monkeypatch.setattr(
        markitdown.MarkItDown, "convert_local", lambda *_: SimpleNamespace(text_content="")
    )

    def fail(path, maximum):
        raise RuntimeError(private_detail)

    monkeypatch.setattr(ocr, "extract_pdf_ocr", fail)
    with pytest.raises(HTTPException) as failure:
        _convert_binary(b"%PDF-test", ".pdf")
    assert failure.value.status_code == 422
    assert failure.value.detail["provenance"]["converter"] == "none"
    assert private_detail not in str(failure.value.detail)


@pytest.mark.parametrize(
    "suffix,contents", [(".pdf", _pdf_bytes(VALID_TEXT)), (".docx", _docx_bytes(VALID_TEXT))]
)
def test_actual_primary_pdf_and_docx_extraction(suffix, contents):
    text, converter, _ = _convert_binary(contents, suffix)
    assert "Ada Lovelace" in text
    assert "Python systems engineer" in text
    assert converter == "markitdown-local"


def test_pdf_fallback_receives_configured_limit_and_reports_provenance(monkeypatch):
    import markitdown

    monkeypatch.setattr(
        markitdown.MarkItDown, "convert_local", lambda *_: SimpleNamespace(text_content="")
    )
    received = []

    def extract(path, maximum):
        received.append((path.read_bytes(), maximum))
        return "Scanned resume"

    monkeypatch.setattr(ocr, "extract_pdf_ocr", extract)
    result = _convert_binary(b"%PDF-test", ".pdf", max_extracted_bytes=1024)
    assert result[:2] == ("Scanned resume", "tesseract-local")
    assert received == [(b"%PDF-test", 1024)]


@pytest.mark.skipif(
    not shutil.which("pdftoppm"), reason="requires the image's native Poppler binary"
)
def test_actual_poppler_raster_is_grayscale_and_bounded(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "source.pdf"
    source.write_bytes(_pdf_bytes(VALID_TEXT))
    native_run = subprocess.run
    dimensions = []

    def run(args, **kwargs):
        if args[0] == "tesseract":
            with Image.open(args[1]) as raster:
                dimensions.append(raster.size)
                assert raster.mode == "L"
                assert max(raster.size) <= ocr.MAX_OCR_SIDE_PIXELS
            Path(args[2] + ".txt").write_text("simulated OCR text", encoding="utf-8")
        else:
            native_run(args, **kwargs)

    monkeypatch.setattr(ocr.subprocess, "run", run)
    assert ocr.extract_pdf_ocr(source, 1024) == "simulated OCR text"
    assert len(dimensions) == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == ["source.pdf"]


@pytest.mark.skipif(
    not shutil.which("pdftoppm") or not shutil.which("tesseract"),
    reason="requires the image's native Poppler and Tesseract binaries",
)
def test_actual_scanned_pdf_ocr(tmp_path):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1200, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 100), "Ada Lovelace", font=ImageFont.load_default(size=64), fill="black")
    source = tmp_path / "scan.pdf"
    image.save(source, "PDF", resolution=150)
    text, converter, _ = _convert_binary(source.read_bytes(), ".pdf")
    assert "Ada Lovelace" in text
    assert converter == "tesseract-local"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["scan.pdf"]
