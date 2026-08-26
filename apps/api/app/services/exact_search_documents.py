"""Pure document materialization for the canonical exact-search projection."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from typing import Any

from markdown_it import MarkdownIt

from app.markdown import normalize_newlines

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MARKDOWN = MarkdownIt("commonmark")


class ExactSearchUnavailable(RuntimeError):
    """The exact projection is absent, non-ready, non-PostgreSQL, or corrupt."""


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    if "\x00" in normalized or any(
        ord(character) < 0x20 and character not in "\t\n\r" for character in normalized
    ):
        raise ExactSearchUnavailable("canonical searchable text contains an invalid control")
    return " ".join(normalized.split())


def _display_location(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        label = value.get("label")
        if isinstance(label, str):
            return label
    return ""


def _reference_label(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("label"), str):
        return value["label"]
    return None


def _frontmatter_search_terms(frontmatter: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for field in ("name", "title", "headline"):
        value = frontmatter.get(field)
        if isinstance(value, str):
            terms.append(value)
    location = frontmatter.get("location")
    if isinstance(location, str):
        terms.append(location)
    elif isinstance(location, dict):
        for field in ("label", "country_code", "region", "city"):
            value = location.get(field)
            if isinstance(value, str):
                terms.append(value)
    for field in (
        "occupations",
        "industries",
        "skills",
        "languages",
        "open_to",
        "organizations",
    ):
        values = frontmatter.get(field)
        if isinstance(values, list):
            for value in values:
                label = _reference_label(value)
                if label is not None:
                    terms.append(label)
                if isinstance(value, dict):
                    relationship = value.get("relationship")
                    if isinstance(relationship, str):
                        terms.append(relationship)
    seniority = _reference_label(frontmatter.get("seniority"))
    if seniority is not None:
        terms.append(seniority)
    work_modes = frontmatter.get("work_modes")
    if isinstance(work_modes, list):
        terms.extend(value for value in work_modes if isinstance(value, str))
    availability = frontmatter.get("availability")
    if isinstance(availability, dict):
        status = availability.get("status")
        if isinstance(status, str):
            terms.append(status)
    representation = frontmatter.get("public_representation")
    if isinstance(representation, dict):
        for field in ("status", "public_label"):
            value = representation.get(field)
            if isinstance(value, str):
                terms.append(value)
        label = _reference_label(representation.get("representative"))
        if label is not None:
            terms.append(label)
    contact = frontmatter.get("contact")
    if isinstance(contact, dict) and isinstance(contact.get("disclosure"), str):
        terms.append(contact["disclosure"])
    return terms


def _visible_markdown_text(body: str) -> str:
    """Extract visible text, inline code, and image alt without link targets/HTML."""

    def visit(tokens: list[Any]) -> list[str]:
        parts: list[str] = []
        for token in tokens:
            token_type = getattr(token, "type", "")
            if token_type in {"html_block", "html_inline", "link_open", "link_close"}:
                continue
            if token_type in {"text", "code_inline", "image"}:
                content = getattr(token, "content", "")
                if isinstance(content, str):
                    parts.append(content)
                continue
            if token_type in {"softbreak", "hardbreak", "paragraph_open", "paragraph_close"}:
                parts.append(" ")
                continue
            children = getattr(token, "children", None)
            if isinstance(children, list):
                parts.extend(visit(children))
        return parts

    return " ".join(visit(_MARKDOWN.parse(normalize_newlines(body))))


def _compact_values(frontmatter: dict[str, Any]) -> list[tuple[str, str, int]]:
    values: list[tuple[str, str, int]] = []
    skills = frontmatter.get("skills")
    if isinstance(skills, list):
        for ordinal, value in enumerate(skills):
            label = _reference_label(value)
            if label is not None:
                values.append(("skill", label, ordinal))
    location = _display_location(frontmatter.get("location"))
    if location:
        values.append(("location", location, 0))
    return values


def _snapshot_values(
    kind: str,
    identifier: str,
    frontmatter: dict[str, Any],
    body: str,
    source_sha256: str,
    document_version: int,
    updated_at: datetime,
) -> tuple[dict[str, Any], list[tuple[str, str, int]]]:
    if kind not in {"profile", "resume"}:
        raise ExactSearchUnavailable("exact search only supports Profile and Resume")
    schema_version = frontmatter.get("schema_version")
    if schema_version not in {1, 2}:
        raise ExactSearchUnavailable("exact search document schema is invalid")
    if not _SHA256_RE.fullmatch(source_sha256):
        raise ExactSearchUnavailable("exact search source digest is invalid")
    name = frontmatter.get("name")
    headline = frontmatter.get("headline")
    title = frontmatter.get("title")
    if not isinstance(name, str) or not isinstance(headline, str):
        raise ExactSearchUnavailable("exact search display fields are invalid")
    if title is not None and not isinstance(title, str):
        raise ExactSearchUnavailable("exact search display fields are invalid")
    location = _display_location(frontmatter.get("location"))
    terms = _frontmatter_search_terms(frontmatter)
    terms.append(_visible_markdown_text(body))
    normalized_search_text = _normalize_search_text(" ".join(terms))
    if not normalized_search_text:
        raise ExactSearchUnavailable("exact search text is empty")
    search_sha256 = hashlib.sha256(normalized_search_text.encode("utf-8")).hexdigest()
    availability = frontmatter.get("availability")
    availability_status = availability.get("status") if isinstance(availability, dict) else None
    availability_from = (
        availability.get("available_from") if isinstance(availability, dict) else None
    )
    if schema_version == 2 and not isinstance(availability_status, str):
        raise ExactSearchUnavailable("exact search availability is invalid")
    if availability_from is not None and not isinstance(availability_from, (str, date, datetime)):
        raise ExactSearchUnavailable("exact search availability is invalid")
    if isinstance(availability_from, (date, datetime)):
        availability_from = availability_from.isoformat()
    representation = frontmatter.get("public_representation")
    contact = frontmatter.get("contact")
    representation_status = (
        representation.get("status") if isinstance(representation, dict) else None
    )
    contact_disclosure = contact.get("disclosure") if isinstance(contact, dict) else None
    if schema_version == 2 and (
        not isinstance(representation_status, str) or not isinstance(contact_disclosure, str)
    ):
        raise ExactSearchUnavailable("exact search disclosure fields are invalid")
    return (
        {
            "document_version": document_version,
            "source_sha256": source_sha256,
            "search_sha256": search_sha256,
            "kind": kind,
            "schema_version": int(schema_version),
            "identifier": identifier,
            "name": name,
            "headline": headline,
            "title": title,
            "location": location,
            "availability_status": availability_status,
            "availability_from": None if availability_from is None else str(availability_from),
            "representation_status": representation_status,
            "contact_disclosure": contact_disclosure,
            "updated_at": updated_at,
            "normalized_search_text": normalized_search_text,
        },
        _compact_values(frontmatter),
    )
