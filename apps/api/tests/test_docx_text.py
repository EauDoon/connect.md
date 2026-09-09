from __future__ import annotations

import io
import socket
import zipfile
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi import HTTPException

from app.config import Settings
from app.docx_text import extract_docx_text
from app.ingest import _convert_binary, _validate_file_signature


def document_bytes():
    document = Document()
    document.sections[0].header.paragraphs[0].text = "Ada Lovelace"
    document.add_paragraph("First paragraph")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Python"
    table.cell(0, 1).text = "Systems"
    document.add_paragraph("Last paragraph")
    document.sections[0].footer.paragraphs[0].text = "Portfolio"
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_actual_docx_fallback_is_offline_and_preserves_order(monkeypatch):
    import markitdown

    def unavailable(*args, **kwargs):
        raise RuntimeError("primary unavailable")

    def reject_network(*args, **kwargs):
        pytest.fail("local DOCX fallback attempted network access")

    monkeypatch.setattr(markitdown.MarkItDown, "convert_local", unavailable)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    contents = document_bytes()
    _validate_file_signature(
        contents, ".docx", Settings(api_key_pepper="test-only-pepper-is-long-enough")
    )
    text, converter, warnings = _convert_binary(contents, ".docx")
    assert text == "Ada Lovelace\nFirst paragraph\nPython | Systems\nLast paragraph\nPortfolio"
    assert converter == "python-docx-local"
    assert warnings == ["MarkItDown conversion was unavailable: RuntimeError."]


def test_docx_fallback_rejects_utf8_overflow_without_a_partial_draft(monkeypatch):
    import markitdown

    monkeypatch.setattr(
        markitdown.MarkItDown, "convert_local", lambda *_: SimpleNamespace(text_content="")
    )
    with pytest.raises(HTTPException) as failure:
        _convert_binary(document_bytes(), ".docx", max_extracted_bytes=12)
    assert failure.value.status_code == 422
    assert failure.value.detail["provenance"]["converter"] == "none"


def test_docx_exact_utf8_limit_and_nested_merged_tables(tmp_path):
    document = Document()
    document.add_paragraph("é")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Merged"
    nested = table.cell(0, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "Nested"
    source = tmp_path / "source.docx"
    document.save(source)
    expected = "é\nMerged Nested"
    text = extract_docx_text(source, len(expected.encode()))
    assert text == expected
    with pytest.raises(ValueError, match="limit"):
        extract_docx_text(source, len(expected.encode()) - 1)


def test_docx_xml_does_not_resolve_external_entities(tmp_path):
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("SYNTHETIC_ENTITY_SENTINEL", encoding="utf-8")
    source = tmp_path / "source.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<!DOCTYPE w:document [<!ENTITY external SYSTEM "{sentinel.as_uri()}">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&external;</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(io.BytesIO(document_bytes())) as original:
        with zipfile.ZipFile(source, "w") as output:
            for member in original.infolist():
                output.writestr(
                    member,
                    xml.encode()
                    if member.filename == "word/document.xml"
                    else original.read(member),
                )
    assert "SYNTHETIC_ENTITY_SENTINEL" not in extract_docx_text(source, 1024)


@pytest.mark.parametrize("rows,columns", [(1, 3), (3, 1), (3, 3)])
def test_merged_cells_appear_once_across_the_entire_table(tmp_path, rows, columns):
    document = Document()
    table = document.add_table(rows=rows, cols=columns)
    table.cell(0, 0).merge(table.cell(rows - 1, columns - 1)).text = "é"
    document.add_paragraph("Tail")
    source = tmp_path / "merged.docx"
    document.save(source)
    expected = "é\nTail"
    assert extract_docx_text(source, len(expected.encode())) == expected
    with pytest.raises(ValueError, match="limit"):
        extract_docx_text(source, len(expected.encode()) - 1)


def test_nested_vertical_merges_and_empty_blocks_do_not_consume_the_text_limit(tmp_path):
    document = Document()
    outer = document.add_table(rows=3, cols=2)
    cell = outer.cell(0, 0).merge(outer.cell(2, 1))
    cell.text = "Outer"
    nested = cell.add_table(rows=3, cols=1)
    nested.cell(0, 0).merge(nested.cell(2, 0)).text = "Inner"
    cell.add_paragraph("   ")
    document.add_paragraph("")
    source = tmp_path / "nested.docx"
    document.save(source)
    expected = "Outer Inner"
    assert extract_docx_text(source, len(expected.encode())) == expected


def test_vertically_merged_cell_does_not_hide_other_rows_or_other_tables(tmp_path):
    document = Document()
    for _ in range(2):
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).merge(table.cell(1, 0)).text = "Shared"
        table.cell(0, 1).text = "First"
        table.cell(1, 1).text = "Second"
    source = tmp_path / "tables.docx"
    document.save(source)
    expected = "Shared | First\nSecond\nShared | First\nSecond"
    assert extract_docx_text(source, len(expected.encode())) == expected


@pytest.mark.parametrize("first_enabled", [False, True])
@pytest.mark.parametrize("even_enabled", [False, True])
def test_header_footer_layout_flags_control_stored_variants(
    tmp_path, monkeypatch, first_enabled, even_enabled
):
    import markitdown

    document = Document()
    section = document.sections[0]
    section.different_first_page_header_footer = first_enabled
    document.settings.odd_and_even_pages_header_footer = even_enabled
    for kind in ("header", "footer"):
        for prefix in ("", "first_page_", "even_page_"):
            getattr(section, prefix + kind).paragraphs[0].text = (prefix + kind).upper()
    document.add_paragraph("Body")
    source = tmp_path / "variants.docx"
    document.save(source)
    headers = ["HEADER"] + (["FIRST_PAGE_HEADER"] if first_enabled else [])
    headers += ["EVEN_PAGE_HEADER"] if even_enabled else []
    footers = ["FOOTER"] + (["FIRST_PAGE_FOOTER"] if first_enabled else [])
    footers += ["EVEN_PAGE_FOOTER"] if even_enabled else []
    expected = "\n".join([*headers, "Body", *footers])
    monkeypatch.setattr(
        markitdown.MarkItDown, "convert_local", lambda *_: SimpleNamespace(text_content="")
    )
    text, converter, _ = _convert_binary(
        source.read_bytes(), ".docx", max_extracted_bytes=len(expected.encode())
    )
    assert text == expected
    assert converter == "python-docx-local"
    with pytest.raises(ValueError, match="limit"):
        extract_docx_text(source, len(expected.encode()) - 1)


def test_linked_variants_are_exported_once_when_first_activated(tmp_path):
    document = Document()
    document.add_paragraph("Body")
    document.add_section()
    document.add_section()
    document.settings.odd_and_even_pages_header_footer = True
    first, second, third = document.sections
    first.different_first_page_header_footer = False
    second.different_first_page_header_footer = True
    third.different_first_page_header_footer = True
    for kind in ("header", "footer"):
        for prefix in ("", "first_page_", "even_page_"):
            getattr(first, prefix + kind).paragraphs[0].text = (prefix + kind).upper()
            assert getattr(second, prefix + kind).is_linked_to_previous
            assert getattr(third, prefix + kind).is_linked_to_previous
    source = tmp_path / "inherited.docx"
    document.save(source)
    expected = (
        "HEADER\nEVEN_PAGE_HEADER\nFIRST_PAGE_HEADER\nBody\n"
        "FOOTER\nEVEN_PAGE_FOOTER\nFIRST_PAGE_FOOTER"
    )
    assert extract_docx_text(source, len(expected.encode())) == expected


@pytest.mark.parametrize("activate_last", [False, True])
def test_inactive_redefinition_is_used_only_by_a_later_active_section(tmp_path, activate_last):
    document = Document()
    document.add_paragraph("Body")
    document.add_section()
    document.add_section()
    first, second, third = document.sections
    first.different_first_page_header_footer = True
    second.different_first_page_header_footer = False
    third.different_first_page_header_footer = activate_last
    for kind in ("header", "footer"):
        getattr(first, "first_page_" + kind).paragraphs[0].text = "OLD_" + kind.upper()
        replacement = getattr(second, "first_page_" + kind)
        replacement.is_linked_to_previous = False
        replacement.paragraphs[0].text = "NEW_" + kind.upper()
    source = tmp_path / "replacement.docx"
    document.save(source)
    headers = ["OLD_HEADER"] + (["NEW_HEADER"] if activate_last else [])
    footers = ["OLD_FOOTER"] + (["NEW_FOOTER"] if activate_last else [])
    expected = "\n".join([*headers, "Body", *footers])
    assert extract_docx_text(source, len(expected.encode())) == expected


def test_explicit_empty_variants_override_inherited_inactive_content(tmp_path):
    document = Document()
    document.add_paragraph("Body")
    document.add_section()
    document.add_section()
    first, second, third = document.sections
    first.different_first_page_header_footer = False
    second.different_first_page_header_footer = False
    third.different_first_page_header_footer = True
    for kind in ("header", "footer"):
        getattr(first, "first_page_" + kind).paragraphs[0].text = "INACTIVE_SENTINEL_" + kind
        getattr(second, "first_page_" + kind).is_linked_to_previous = False
    source = tmp_path / "empty-override.docx"
    document.save(source)
    assert extract_docx_text(source, 4) == "Body"


def test_default_header_footer_redefinitions_preserve_section_linkage(tmp_path):
    document = Document()
    document.add_paragraph("Body")
    document.add_section()
    document.add_section()
    first, second, third = document.sections
    for kind in ("header", "footer"):
        getattr(first, kind).paragraphs[0].text = "OLD_" + kind.upper()
        replacement = getattr(second, kind)
        replacement.is_linked_to_previous = False
        replacement.paragraphs[0].text = "NEW_" + kind.upper()
        assert getattr(third, kind).is_linked_to_previous
    source = tmp_path / "default-overrides.docx"
    document.save(source)
    expected = "OLD_HEADER\nNEW_HEADER\nBody\nOLD_FOOTER\nNEW_FOOTER"
    assert extract_docx_text(source, len(expected.encode())) == expected
