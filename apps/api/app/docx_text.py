"""Deterministic DOCX fallback using local paragraphs, tables, headers, and footers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from docx import Document
from docx.blkcntnr import BlockItemContainer
from docx.document import Document as DocxDocument
from docx.table import Table


def _join(parts: Iterator[str], separator: str, maximum: int) -> str:
    collected: list[str] = []
    used = 0
    for part in parts:
        used += len(part.encode("utf-8")) + (len(separator) if collected else 0)
        if used > maximum:
            raise ValueError("DOCX text exceeds the extracted-text limit")
        collected.append(part)
    return separator.join(collected)


def _blocks(container: BlockItemContainer | DocxDocument, maximum: int) -> Iterator[str]:
    for block in container.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                # Merged cells share their XML node and must appear only once.
                seen: set[object] = set()
                cells = []
                for cell in row.cells:
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        cells.append(_join(_blocks(cell, maximum), " ", maximum))
                yield _join(iter(cells), " | ", maximum)
        else:
            yield block.text


def extract_docx_text(source: Path, maximum: int) -> str:
    document = Document(str(source))
    parts: list[str] = []
    used = 0

    def add(text: str) -> None:
        nonlocal used
        text = text.strip()
        if text:
            used += len(text.encode("utf-8")) + (1 if parts else 0)
            if used > maximum:
                raise ValueError("DOCX text exceeds the extracted-text limit")
            parts.append(text)

    for section in document.sections:
        for header in (section.header, section.first_page_header, section.even_page_header):
            if not header.is_linked_to_previous:
                for text in _blocks(header, maximum):
                    add(text)
    for text in _blocks(document, maximum):
        add(text)
    for section in document.sections:
        for footer in (section.footer, section.first_page_footer, section.even_page_footer):
            if not footer.is_linked_to_previous:
                for text in _blocks(footer, maximum):
                    add(text)
    return "\n".join(parts)
