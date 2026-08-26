"""Strict parsing and canonical rendering for connect.md documents."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from markdown_it import MarkdownIt
from yaml.events import AliasEvent
from yaml.nodes import Node


class MarkdownValidationError(ValueError):
    """Raised with an actionable document-contract validation error."""


class MarkdownSizeError(MarkdownValidationError):
    """Raised when Markdown input or canonical output exceeds its byte contract."""


class MarkdownVersionConflictError(MarkdownValidationError):
    """Raised when canonical server fields describe a stale or different resource."""


PUBLIC_MARKDOWN_VALIDATION_DETAIL = "the Markdown payload failed canonical validation"


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses ambiguous duplicate mapping keys."""

    def compose_node(self, parent: Node | None, index: int) -> Node | None:
        """Reject aliases before PyYAML can construct an expanded object graph."""
        if self.check_event(AliasEvent):
            raise MarkdownValidationError("YAML aliases are not allowed in frontmatter")
        return super().compose_node(parent, index)


def _construct_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise MarkdownValidationError("frontmatter keys must be strings")
        if key in mapping:
            raise MarkdownValidationError(f"frontmatter contains duplicate key '{key}'")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)

_FRONTMATTER_RE = re.compile(r"\A---\n(?P<frontmatter>[\s\S]*?)\n---\n(?P<body>[\s\S]*)\Z")
_MARKDOWN_PARSER = MarkdownIt("commonmark")
_REFERENCE_ARRAY_FIELDS = (
    "occupations",
    "industries",
    "skills",
    "languages",
    "open_to",
    "organizations",
)


def _schemas_directory() -> Path:
    configured = os.environ.get("CONNECTMD_SCHEMA_PATH")
    module_path = Path(__file__).resolve()
    local_root = module_path.parents[3] if len(module_path.parents) > 3 else Path("/")
    candidates = [
        Path(configured) if configured else None,
        local_root / "packages" / "markdown-schemas" / "schemas",
        Path("/opt/connectmd/packages/markdown-schemas/schemas"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate
    raise RuntimeError("cannot locate canonical Markdown schema package")


@lru_cache
def canonical_document_max_utf8_bytes() -> int:
    """Load the package-owned Profile/Resume canonical byte limit."""
    path = _schemas_directory().parent / "canonical-markdown-limits.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot load canonical Markdown limits manifest") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("canonical Markdown limits manifest is invalid")
    contract_version = manifest.get("contract_version")
    limit = manifest.get("profile_resume_max_utf8_bytes")
    if (
        isinstance(contract_version, bool)
        or contract_version != 1
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
    ):
        raise RuntimeError("canonical Markdown limits manifest is invalid")
    return limit


def _utf8_byte_length(markdown: str) -> int:
    if not isinstance(markdown, str):
        raise MarkdownValidationError("Markdown must be a UTF-8 text value")
    try:
        return len(markdown.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise MarkdownValidationError("Markdown must contain valid UTF-8 text") from exc


def require_canonical_document_size(markdown: str) -> None:
    """Require final canonical Markdown to fit the package byte contract."""
    byte_length = _utf8_byte_length(markdown)
    limit = canonical_document_max_utf8_bytes()
    if byte_length > limit:
        raise MarkdownSizeError(f"canonical Profile/Resume Markdown exceeds {limit} UTF-8 bytes")


@lru_cache
def load_contract(kind: str, schema_version: int = 1) -> dict[str, Any]:
    if kind not in {"profile", "resume", "post"}:
        raise MarkdownValidationError("Markdown kind must be 'profile', 'resume', or 'post'")
    if schema_version not in {1, 2}:
        raise MarkdownValidationError("schema_version must be the supported integer 1 or 2")
    if kind == "post" and schema_version != 1:
        raise MarkdownValidationError("post schema_version must be 1")
    return load_schema(kind if schema_version == 1 else f"{kind}.v2")


@lru_cache
def load_schema(name: str) -> dict[str, Any]:
    if name not in {
        "profile",
        "resume",
        "profile.write",
        "resume.write",
        "profile.v2",
        "resume.v2",
        "profile.v2.write",
        "resume.v2.write",
        "post",
        "post.write",
    }:
        raise MarkdownValidationError("unknown Markdown schema")
    path = _schemas_directory() / f"{name}.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load {name} Markdown contract") from exc


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _bounded_normalized_markdown(markdown: str, *, kind: str | None = None) -> str:
    """Bound raw and normalized UTF-8 bytes before any YAML parsing."""
    limit = canonical_document_max_utf8_bytes()
    size_subject = (
        "canonical Profile/Resume Markdown"
        if kind in {"profile", "resume"}
        else "canonical post Markdown"
        if kind == "post"
        else "Markdown input"
    )
    # CRLF can be twice the size of canonical LF-only input. This raw bound
    # preserves every input that could fit after newline normalization while
    # preventing an unbounded normalization allocation.
    raw_limit = limit * 2
    if _utf8_byte_length(markdown) > raw_limit:
        raise MarkdownSizeError(f"{size_subject} exceeds {limit} UTF-8 bytes")
    normalized = normalize_newlines(markdown)
    if _utf8_byte_length(normalized) > limit:
        raise MarkdownSizeError(f"{size_subject} exceeds {limit} UTF-8 bytes")
    return normalized


def split_markdown(markdown: str, *, kind: str | None = None) -> tuple[dict[str, Any], str]:
    normalized = _bounded_normalized_markdown(markdown, kind=kind)
    match = _FRONTMATTER_RE.match(normalized)
    if not match:
        raise MarkdownValidationError(
            "document must begin with YAML frontmatter delimited by '---'"
        )
    try:
        frontmatter = yaml.load(match.group("frontmatter"), Loader=UniqueKeyLoader)
    except MarkdownValidationError:
        raise
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None)
        raise MarkdownValidationError(f"invalid YAML frontmatter: {problem or str(exc)}") from exc
    if not isinstance(frontmatter, dict):
        raise MarkdownValidationError("frontmatter must be a YAML mapping")
    return frontmatter, match.group("body")


def _schema_version(frontmatter: dict[str, Any]) -> int:
    schema_version = frontmatter.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in {1, 2}
    ):
        raise MarkdownValidationError("schema_version must be the supported integer 1 or 2")
    return schema_version


def _json_errors(kind: str, frontmatter: dict[str, Any]) -> list[str]:
    contract = load_contract(kind, _schema_version(frontmatter))
    validator = Draft202012Validator(contract, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(frontmatter),
        key=lambda error: tuple(str(part) for part in error.absolute_schema_path),
    )
    messages: list[str] = []
    for error in errors:
        schema_path = tuple(error.absolute_schema_path)
        fields = [
            schema_path[index + 1]
            for index, part in enumerate(schema_path[:-1])
            if part == "properties" and isinstance(schema_path[index + 1], str)
        ]
        constraint = error.validator if isinstance(error.validator, str) else "schema"
        message = (
            f"frontmatter field '{'.'.join(fields)}' failed the {constraint} constraint"
            if fields
            else f"frontmatter failed the {constraint} constraint"
        )
        if message not in messages:
            messages.append(message)
    return messages


def _validate_reference_uniqueness(frontmatter: dict[str, Any]) -> None:
    """Reject contradictory v2 labels/metadata for one stable taxonomy identity."""
    if _schema_version(frontmatter) != 2:
        return
    for field in _REFERENCE_ARRAY_FIELDS:
        seen: set[tuple[str, str]] = set()
        values = frontmatter.get(field, [])
        if not isinstance(values, list):
            continue
        for reference in values:
            if not isinstance(reference, dict):
                continue
            scheme = reference.get("scheme")
            reference_id = reference.get("id")
            if not isinstance(scheme, str) or not isinstance(reference_id, str):
                continue
            identity = (scheme, reference_id)
            if identity in seen:
                raise MarkdownValidationError(
                    f"frontmatter field '{field}' contains duplicate stable reference "
                    f"'{scheme}:{reference_id}'"
                )
            seen.add(identity)


def _validate_post_topics(kind: str, frontmatter: dict[str, Any]) -> None:
    if kind != "post":
        return
    topics = frontmatter.get("topics")
    if not isinstance(topics, list):
        return
    normalized = [topic.casefold() for topic in topics if isinstance(topic, str)]
    if len(normalized) != len(set(normalized)):
        raise MarkdownValidationError("frontmatter field 'topics' must not contain duplicates")


def _validate_headings(kind: str, body: str, frontmatter: dict[str, Any]) -> None:
    tokens = _MARKDOWN_PARSER.parse(body)
    lines = body.splitlines()
    headings: list[tuple[int, str]] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        if inline is None or inline.type != "inline":
            raise MarkdownValidationError("heading content could not be parsed")
        level = int(token.tag.removeprefix("h"))
        text = inline.content.strip()
        source_line = lines[token.map[0]] if token.map is not None else ""
        if source_line != f"{'#' * level} {text}":
            raise MarkdownValidationError(
                "headings must use exact ATX syntax without closing markers"
            )
        headings.append((level, text))
    requirements = load_contract(kind, _schema_version(frontmatter))["x-connectmd"][
        "required_headings"
    ]
    expected: list[tuple[int, str]] = []
    for template in requirements:
        rendered = template.format(**frontmatter)
        match = re.fullmatch(r"(#{1,6})\s+(.+)", rendered)
        if match is None:
            raise RuntimeError(f"invalid required heading in {kind} contract: {rendered}")
        expected.append((len(match.group(1)), match.group(2)))
    if not headings or headings[0] != expected[0]:
        level, text = expected[0]
        raise MarkdownValidationError(f"first heading must be '{'#' * level} {text}'")
    if sum(1 for level, _ in headings if level == 1) != 1:
        raise MarkdownValidationError("document must contain exactly one level-one heading")
    positions: list[int] = []
    for level, text in expected[1:]:
        matching = [index for index, heading in enumerate(headings) if heading == (level, text)]
        if len(matching) != 1:
            rendered = f"{'#' * level} {text}"
            raise MarkdownValidationError(f"required heading '{rendered}' must appear exactly once")
        positions.append(matching[0])
    if positions != sorted(positions):
        raise MarkdownValidationError("required headings must follow the contract order")


def validate_canonical(kind: str, markdown: str) -> tuple[dict[str, Any], str]:
    """Validate a complete canonical document and return parsed components."""
    frontmatter, body = split_markdown(markdown, kind=kind)
    errors = _json_errors(kind, frontmatter)
    if errors:
        raise MarkdownValidationError("frontmatter validation failed: " + "; ".join(errors))
    _validate_reference_uniqueness(frontmatter)
    _validate_post_topics(kind, frontmatter)
    _validate_headings(kind, body, frontmatter)
    return frontmatter, body


def prepare_client_document(
    kind: str,
    markdown: str,
    *,
    document_id: str,
    owner_id: str,
    version: int,
    updated_at: datetime | None = None,
    expected_server_fields: dict[str, Any] | None = None,
    author_profile_handle: str | None = None,
    published_at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Validate client input and construct server-owned canonical Markdown.

    Creates reject every server field. Updates may round-trip a canonical GET
    response when each supplied server field exactly matches the current version.
    """
    frontmatter, body = split_markdown(markdown, kind=kind)
    contract = load_contract(kind, _schema_version(frontmatter))
    server_fields = set(contract["x-connectmd"]["server_fields"])
    forged = sorted(server_fields.intersection(frontmatter))
    if forged and expected_server_fields is None:
        raise MarkdownValidationError(
            "server-assigned frontmatter cannot be supplied by clients: " + ", ".join(forged)
        )
    if expected_server_fields is not None:
        mismatched = [
            field
            for field in forged
            if field not in expected_server_fields
            or not _server_field_matches(field, frontmatter[field], expected_server_fields[field])
        ]
        if mismatched:
            raise MarkdownVersionConflictError(
                "server-assigned frontmatter does not match the current document: "
                + ", ".join(mismatched)
            )
        for field in server_fields:
            frontmatter.pop(field, None)
    allowed = set(contract["properties"])
    unknown = sorted(set(frontmatter) - allowed)
    if unknown:
        raise MarkdownValidationError("unknown frontmatter fields: " + ", ".join(unknown))
    canonical = deepcopy(frontmatter)
    timestamp = (updated_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    if kind == "post":
        if not author_profile_handle:
            raise MarkdownValidationError("post requires a server-selected public author profile")
        canonical.update(
            {
                "id": document_id,
                "author_profile_handle": author_profile_handle,
                "version": version,
                "published_at": (published_at or updated_at or datetime.now(UTC))
                .isoformat()
                .replace("+00:00", "Z"),
                "updated_at": timestamp,
            }
        )
    else:
        canonical.update(
            {
                "id": document_id,
                "owner_id": owner_id,
                "version": version,
                "updated_at": timestamp,
            }
        )
    errors = _json_errors(kind, canonical)
    if errors:
        raise MarkdownValidationError("frontmatter validation failed: " + "; ".join(errors))
    _validate_reference_uniqueness(canonical)
    _validate_post_topics(kind, canonical)
    _validate_headings(kind, body, canonical)
    ordered = {key: canonical[key] for key in contract["properties"]}
    rendered = (
        "---\n"
        + yaml.safe_dump(ordered, allow_unicode=True, default_flow_style=False, sort_keys=False)
        + "---\n"
        + body.rstrip("\n")
        + "\n"
    )
    if kind in {"profile", "resume"}:
        require_canonical_document_size(rendered)
    if kind == "post" and len(rendered.encode("utf-8")) > 10_240:
        raise MarkdownSizeError("canonical post Markdown must not exceed 10240 bytes")
    return rendered, canonical


def _server_field_matches(field: str, supplied: Any, expected: Any) -> bool:
    """Compare canonical identity fields after YAML timestamp normalization."""
    if field not in {"updated_at", "published_at"}:
        return supplied == expected
    if isinstance(supplied, datetime):
        supplied = supplied.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(expected, datetime):
        expected = expected.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(supplied) == str(expected)


_SECTION_ALIASES = {
    "about": {"about", "about me", "profile", "professional profile"},
    "summary": {"summary", "professional summary", "career summary", "objective"},
    "experience": {
        "experience",
        "employment",
        "employment history",
        "professional experience",
        "work experience",
        "work history",
    },
    "education": {"education", "academic background", "qualifications"},
    "skills": {
        "skills",
        "core competencies",
        "competencies",
        "technical skills",
        "technologies",
    },
}


def _plain_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _source_heading(line: str) -> str | None:
    match = re.match(r"^\s{0,3}#{1,6}[ \t]+(.+?)\s*#*\s*$", line)
    return match.group(1).strip() if match else None


def _safe_import_line(line: str) -> str:
    """Prevent imported syntax from adding contract-level headings or fences."""
    heading = _source_heading(line)
    if heading is not None:
        return f"### {heading}"
    if re.fullmatch(r"\s*(?:=+|-+)\s*", line):
        return "***"
    if re.match(r"^\s{0,3}(?:`{3,}|~{3,})", line):
        return re.sub(r"(`|~)", r"\\\1", line)
    return line.rstrip()


def _draft_name(lines: list[str]) -> tuple[str, int]:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        candidate = _source_heading(line) or stripped
        candidate = re.sub(r"[*_`\[\]{}<>]", "", candidate)
        candidate = " ".join(candidate.split()).strip(" -|,:;#")[:160].rstrip("# ")
        return candidate or "Untitled", index + 1
    return "Untitled", len(lines)


def _import_sections(text: str) -> tuple[str, str, dict[str, list[str]], list[str]]:
    normalized = normalize_newlines(text).lstrip("\ufeff")
    lines = normalized.splitlines()
    if lines and lines[0].strip() == "---":
        closing = next(
            (index for index in range(1, len(lines)) if lines[index].strip() == "---"), None
        )
        if closing is not None:
            lines = lines[closing + 1 :]

    name, start = _draft_name(lines)
    sections: dict[str, list[str]] = {key: [] for key in _SECTION_ALIASES}
    general: list[str] = []
    current: str | None = None
    headline = "Imported draft"

    for line in lines[start:]:
        heading = _source_heading(line)
        # Unstructured/PDF extraction commonly loses heading markers, so exact
        # standalone section labels are recognized with or without ATX syntax.
        normalized_heading = _plain_heading(heading or line.strip())
        matched = next(
            (key for key, aliases in _SECTION_ALIASES.items() if normalized_heading in aliases),
            None,
        )
        if matched is not None:
            current = matched
            continue
        safe_line = _safe_import_line(line)
        destination = sections[current] if current else general
        destination.append(safe_line)
        if current is None and headline == "Imported draft" and safe_line.strip():
            candidate = re.sub(r"^[>*+\-\d. )]+", "", safe_line).strip()
            if candidate and len(candidate) <= 160:
                headline = candidate

    return name, headline[:160].strip() or "Imported draft", sections, general


def _render_imported(lines: list[str], fallback: str) -> str:
    rendered = "\n".join(lines).strip()
    return rendered or fallback


def _imported_skills(lines: list[str]) -> list[str]:
    skills: list[str] = []
    seen: set[str] = set()
    for line in lines:
        value = re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "", line).strip()
        for candidate in re.split(r"\s*[,|;]\s*", value):
            candidate = " ".join(candidate.split()).strip(" -:.")
            if not candidate or len(candidate) > 80 or candidate.casefold() in seen:
                continue
            seen.add(candidate.casefold())
            skills.append(candidate)
            if len(skills) == 50:
                return skills
    return skills


def _ingest_reference_id(label: str, *, field: str, used_ids: set[str]) -> str:
    normalized = label.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:180].rstrip("-")
    base = f"connectmd-user-{field}-{slug or 'not-disclosed'}"
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _ingest_skill_references(skills: list[str]) -> list[dict[str, str]]:
    labels = skills or ["Not disclosed"]
    used_ids: set[str] = set()
    return [
        {
            "scheme": "connectmd-user",
            "id": _ingest_reference_id(label, field="skill", used_ids=used_ids),
            "label": label,
        }
        for label in labels
    ]


def _ingest_neutral_reference(field: str) -> dict[str, str]:
    return {
        "scheme": "connectmd-user",
        "id": f"connectmd-user-{field}-not-disclosed",
        "label": "Not disclosed",
    }


def client_template(kind: str, text: str, *, schema_version: int = 2) -> str:
    """Produce a deterministic, unpersisted draft from extracted text."""
    if kind not in {"profile", "resume"}:
        raise MarkdownValidationError("ingestion supports profile and resume drafts")
    if schema_version not in {1, 2}:
        raise MarkdownValidationError("ingestion schema_version must be 1 or 2")
    name, headline, sections, general = _import_sections(text)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:63].rstrip("-") or "untitled"
    skills = _imported_skills(sections["skills"])
    if schema_version == 2:
        frontmatter: dict[str, Any] = {
            "schema": f"connect.md/{kind}",
            "schema_version": 2,
            ("handle" if kind == "profile" else "slug"): slug,
            "name": name,
            "headline": headline[:280],
            "occupations": [_ingest_neutral_reference("occupation")],
            "industries": [],
            "location": _ingest_neutral_reference("location"),
            "skills": _ingest_skill_references(skills),
            "languages": [],
            "seniority": _ingest_neutral_reference("seniority"),
            "work_modes": [],
            "availability": {"status": "not_disclosed"},
            "open_to": [],
            "organizations": [],
            "public_representation": {"status": "not_disclosed"},
            "contact": {"disclosure": "none"},
            "visibility": "private",
        }
        if kind == "resume":
            frontmatter["title"] = headline
    legacy_skills = skills or ["Unspecified"]
    if kind == "profile":
        if schema_version == 1:
            frontmatter = {
                "schema": "connect.md/profile",
                "schema_version": 1,
                "handle": slug,
                "name": name,
                "headline": headline[:280],
                "location": "Unspecified",
                "skills": legacy_skills,
                "visibility": "private",
            }
        about = _render_imported(
            sections["about"] + sections["summary"] + general,
            "No summary was detected in the imported source.",
        )
        experience = _render_imported(
            sections["experience"] + sections["education"],
            "No experience details were detected in the imported source.",
        )
        return (
            "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n"
            f"# {name}\n\n## About\n\n{about}\n\n"
            f"## Experience\n\n{experience}\n\n## Skills\n\n"
            + "\n".join(f"- {skill}" for skill in legacy_skills)
            + "\n"
        )
    if schema_version == 1:
        frontmatter = {
            "schema": "connect.md/resume",
            "schema_version": 1,
            "slug": slug,
            "name": name,
            "title": headline,
            "headline": headline[:280],
            "location": "Unspecified",
            "skills": legacy_skills,
            "visibility": "private",
        }
    summary = _render_imported(
        sections["summary"] + sections["about"] + general,
        "No summary was detected in the imported source.",
    )
    experience = _render_imported(
        sections["experience"], "No experience details were detected in the imported source."
    )
    education = _render_imported(
        sections["education"], "No education details were detected in the imported source."
    )
    return (
        "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n"
        f"# {name}\n\n## Summary\n\n{summary}\n\n"
        f"## Experience\n\n{experience}\n\n## Education\n\n{education}\n\n## Skills\n\n"
        + "\n".join(f"- {skill}" for skill in legacy_skills)
        + "\n"
    )
