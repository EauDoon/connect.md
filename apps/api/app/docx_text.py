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
            # Horizontal and vertical merge aliases share a node across the table.
            seen: set[object] = set()
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        cells.append(_join(_blocks(cell, maximum), " ", maximum))
                if cells:
                    yield _join(iter(cells), " | ", maximum)
        elif block.text.strip():
            yield block.text


def _header_footer_blocks(document: DocxDocument, maximum: int, *, footers: bool) -> Iterator[str]:
    effective: dict[int, BlockItemContainer] = {}
    seen: set[object] = set()
    for section in document.sections:
        variants = (
            (section.footer, section.first_page_footer, section.even_page_footer)
            if footers
            else (section.header, section.first_page_header, section.even_page_header)
        )
        enabled = (
            True,
            section.different_first_page_header_footer,
            document.settings.odd_and_even_pages_header_footer,
        )
        for index, (variant, active) in enumerate(zip(variants, enabled, strict=True)):
            # Inactive definitions can still be inherited by a later active section.
            if not variant.is_linked_to_previous:
                effective[index] = variant
            definition = effective.get(index)
            if active and definition is not None and definition.part not in seen:
                seen.add(definition.part)
                yield from _blocks(definition, maximum)


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

    for text in _header_footer_blocks(document, maximum, footers=False):
        add(text)
    for text in _blocks(document, maximum):
        add(text)
    for text in _header_footer_blocks(document, maximum, footers=True):
        add(text)
    return "\n".join(parts)
