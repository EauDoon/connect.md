"""Bounded local OCR without model downloads or machine-learning runtimes."""

from __future__ import annotations

import codecs
import subprocess
from pathlib import Path
from time import monotonic

from pypdf import PdfReader

MAX_OCR_PAGES = 30
MAX_OCR_SIDE_PIXELS = 2400
MAX_OCR_IMAGE_BYTES = MAX_OCR_SIDE_PIXELS**2 + 1024
MAX_OCR_RAW_TEXT_BYTES = 16 * 1024 * 1024
OCR_READ_CHUNK_BYTES = 8192
OCR_TIMEOUT_SECONDS = 45


def _read_ocr_text(output: Path, maximum: int, deadline: float) -> str:
    """Apply strip semantics incrementally without retaining unbounded whitespace."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    parts: list[str] = []
    pending: list[str] = []
    used = pending_bytes = raw_bytes = 0
    with output.open("rb") as stream:
        while True:
            if monotonic() >= deadline:
                raise TimeoutError("local OCR deadline exceeded")
            data = stream.read(min(OCR_READ_CHUNK_BYTES, MAX_OCR_RAW_TEXT_BYTES - raw_bytes + 1))
            raw_bytes += len(data)
            if raw_bytes > MAX_OCR_RAW_TEXT_BYTES:
                raise ValueError("local OCR raw output exceeds the size limit")
            chunk = decoder.decode(data, final=not data)
            if not parts:
                chunk = chunk.lstrip()
            content = chunk.rstrip()
            if content:
                used += pending_bytes + len(content.encode("utf-8"))
                if used > maximum:
                    raise ValueError("local OCR text exceeds the extracted-text limit")
                parts.extend(pending)
                parts.append(content)
                pending.clear()
                pending_bytes = 0
            trailing = chunk[len(content) :]
            if trailing:
                pending_bytes += len(trailing.encode("utf-8"))
                if pending_bytes <= maximum - used:
                    pending.append(trailing)
                else:
                    # Oversized trailing whitespace can be discarded at EOF.
                    # If content follows, its pending byte count rejects overflow.
                    pending.clear()
            if not data:
                break
    return "".join(parts)


def extract_pdf_ocr(source: Path, maximum: int) -> str:
    """Run within the worker's existing process group and hard deadline.

    Poppler scales one grayscale raster at a time. All paths are server-created,
    stderr is discarded, and no native command starts a separate process group.
    """
    deadline = monotonic() + OCR_TIMEOUT_SECONDS
    reader = PdfReader(source)
    if reader.is_encrypted:
        raise ValueError("encrypted PDFs are unsupported by local OCR")
    page_count = len(reader.pages)
    if not 1 <= page_count <= MAX_OCR_PAGES:
        raise ValueError("PDF exceeds the local OCR page limit")
    if maximum <= 0:
        raise ValueError("invalid OCR text limit")

    def run(arguments: list[str]) -> None:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("local OCR deadline exceeded")
        subprocess.run(
            arguments,
            check=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=remaining,
        )

    parts: list[str] = []
    used = 0
    raster = source.parent / "ocr-page.pgm"
    output = source.parent / "ocr-page.txt"
    for page in range(1, page_count + 1):
        try:
            run(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-scale-to",
                    str(MAX_OCR_SIDE_PIXELS),
                    "-gray",
                    str(source),
                    str(raster.with_suffix("")),
                ]
            )
            if not 0 < raster.stat().st_size <= MAX_OCR_IMAGE_BYTES:
                raise ValueError("local OCR raster exceeds the size limit")
            run(["tesseract", str(raster), str(output.with_suffix("")), "-l", "eng"])
            text = _read_ocr_text(output, maximum - used, deadline)
            if text:
                used += len(text.encode("utf-8")) + (1 if parts else 0)
                if used > maximum:
                    raise ValueError("local OCR text exceeds the extracted-text limit")
                parts.append(text)
        finally:
            raster.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
    return "\n".join(parts)
