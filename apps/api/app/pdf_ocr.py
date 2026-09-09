"""Bounded local OCR without model downloads or machine-learning runtimes."""

from __future__ import annotations

import subprocess
from pathlib import Path
from time import monotonic

from pypdf import PdfReader

MAX_OCR_PAGES = 30
MAX_OCR_SIDE_PIXELS = 2400
MAX_OCR_IMAGE_BYTES = MAX_OCR_SIDE_PIXELS**2 + 1024
OCR_TIMEOUT_SECONDS = 45


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
            # Do not load an unbounded converter result into the Python process.
            with output.open("rb") as stream:
                data = stream.read(maximum - used + 1)
            if len(data) + used > maximum:
                raise ValueError("local OCR text exceeds the extracted-text limit")
            text = data.decode("utf-8").strip()
            if text:
                used += len(text.encode("utf-8")) + (1 if parts else 0)
                if used > maximum:
                    raise ValueError("local OCR text exceeds the extracted-text limit")
                parts.append(text)
        finally:
            raster.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
    return "\n".join(parts)
