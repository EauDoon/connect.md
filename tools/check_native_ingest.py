"""Require actual scanned-PDF conversion in the built, offline API image."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.ingest import _convert_binary


def main() -> None:
    for executable in ("pdftoppm", "tesseract"):
        if shutil.which(executable) is None:
            raise RuntimeError(
                f"required native ingestion executable is missing: {executable}"
            )

    with tempfile.TemporaryDirectory(prefix="connectmd-native-check-") as directory:
        source = Path(directory) / "scan.pdf"
        image = Image.new("RGB", (1200, 600), "white")
        ImageDraw.Draw(image).text(
            (80, 100),
            "Ada Lovelace",
            font=ImageFont.load_default(size=64),
            fill="black",
        )
        image.save(source, "PDF", resolution=150)
        text, converter, _warnings = _convert_binary(
            source.read_bytes(),
            ".pdf",
            max_extracted_bytes=len("Ada Lovelace".encode("utf-8")),
        )
        if converter != "tesseract-local" or "Ada Lovelace" not in text:
            raise RuntimeError(
                "scanned PDF did not complete actual local Tesseract OCR"
            )
    print("Native ingestion passed: scanned PDF -> Poppler -> Tesseract -> draft text.")


if __name__ == "__main__":
    main()
