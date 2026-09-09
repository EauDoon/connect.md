"""Deterministic DOCX fallback using local paragraphs, tables, headers, and footers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from docx import Document
from docx.blkcntnr import BlockItemContainer
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table


def _join(parts: Iterator[str], separator: str, maximum: int) -> str:
    collected: list[str] = []
    used = 0
    pending = ""
    pending_bytes = 0
    for index, part in enumerate(parts):
        chunk = (separator if index else "") + part
        if not collected:
            chunk = chunk.lstrip()
        content = chunk.rstrip()
        if content:
            used += pending_bytes + len(content.encode("utf-8"))
            if used > maximum:
                raise ValueError("DOCX text exceeds the extracted-text limit")
            collected.extend((pending, content))
            pending = ""
            pending_bytes = 0
        trailing = chunk[len(content) :]
        pending_bytes += len(trailing.encode("utf-8"))
        # Trailing whitespace is discarded unless later content makes it internal.
        # Remember overflow numerically without retaining an oversized whitespace buffer.
        if pending_bytes <= maximum - used:
            pending += trailing
        else:
            pending = ""
    return "".join(collected)


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
                if any(cell.strip() for cell in cells):
                    yield _join(iter(cells), " | ", maximum)
        elif block.text.strip():
            yield block.text


def docx_body_has_content(source: Path) -> bool:
    """Distinguish an empty body from primary-converter table scaffolding.

    Inspect source content, not Markdown punctuation. Keep primary-only content
    such as hyperlinks, images, equations, symbols, and referenced notes eligible.
    Header/footer text is handled by the layout-aware fallback when the body is empty.
    """
    document = Document(str(source))
    text_tags = {qn("w:t"), qn("m:t")}
    rich_tags = {
        qn(name)
        for name in (
            "a:blip", "m:oMath", "w:footnoteReference", "w:endnoteReference",
            "w:sym", "w:object", "w:altChunk", "w:noBreakHyphen", "w:softHyphen",
        )
    }
    rich_tags.add("{urn:schemas-microsoft-com:vml}imagedata")
    return any(
        node.tag in rich_tags or (node.tag in text_tags and bool((node.text or "").strip()))
        for node in document.element.body.iter()
    )


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
