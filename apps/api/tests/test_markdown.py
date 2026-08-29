from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from app.markdown import (
    MarkdownSizeError,
    MarkdownValidationError,
    canonical_document_max_utf8_bytes,
    load_schema,
    prepare_client_document,
    require_canonical_document_size,
    split_markdown,
    validate_canonical,
)

from .helpers import profile_markdown, resume_markdown


def test_valid_schema_examples_pass() -> None:
    root = Path(__file__).resolve().parents[3] / "packages" / "markdown-schemas" / "examples"
    validate_canonical("profile", (root / "profile.md").read_text(encoding="utf-8"))
    validate_canonical("resume", (root / "resume.md").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "schema_name", ["profile.v2", "resume.v2", "profile.v2.write", "resume.v2.write"]
)
def test_v2_schemas_are_valid_draft_2020_12(schema_name: str) -> None:
    Draft202012Validator.check_schema(load_schema(schema_name))


@pytest.mark.parametrize("kind", ["profile", "resume"])
def test_v2_write_schemas_accept_examples_without_server_fields(kind: str) -> None:
    root = Path(__file__).resolve().parents[3] / "packages" / "markdown-schemas" / "examples"
    frontmatter, _ = split_markdown((root / f"{kind}.md").read_text(encoding="utf-8"))
    for field in ("id", "owner_id", "version", "updated_at"):
        frontmatter.pop(field)
    Draft202012Validator(load_schema(f"{kind}.v2.write")).validate(frontmatter)


def test_v2_round_trip_preserves_structured_discovery_fields() -> None:
    root = Path(__file__).resolve().parents[3] / "packages" / "markdown-schemas" / "examples"
    source = (root / "profile.md").read_text(encoding="utf-8")
    frontmatter, _ = split_markdown(source)
    rendered, prepared = prepare_client_document(
        "profile",
        source,
        document_id=frontmatter["id"],
        owner_id=frontmatter["owner_id"],
        version=frontmatter["version"],
        expected_server_fields={
            field: frontmatter[field] for field in ("id", "owner_id", "version", "updated_at")
        },
    )
    assert prepared["schema_version"] == 2
    assert prepared["skills"][0]["scheme"] == "esco"
    assert prepared["location"]["country_code"] == "GB"
    validate_canonical("profile", rendered)


@pytest.mark.parametrize("kind", ["profile", "resume"])
def test_v2_work_modes_may_be_an_explicit_empty_disclosure(kind: str) -> None:
    root = Path(__file__).resolve().parents[3] / "packages" / "markdown-schemas" / "examples"
    source = (root / f"{kind}.md").read_text(encoding="utf-8")
    empty_modes = source.replace("work_modes:\n  - hybrid\n  - remote\n", "work_modes: []\n")
    validate_canonical(kind, empty_modes)
    frontmatter, _ = split_markdown(empty_modes)
    for field in ("id", "owner_id", "version", "updated_at"):
        frontmatter.pop(field)
    Draft202012Validator(load_schema(f"{kind}.v2.write")).validate(frontmatter)


@pytest.mark.parametrize("kind", ["profile", "resume"])
def test_v2_rejects_duplicate_stable_references_with_different_labels(kind: str) -> None:
    root = Path(__file__).resolve().parents[3] / "packages" / "markdown-schemas" / "examples"
    source = (root / f"{kind}.md").read_text(encoding="utf-8")
    duplicate = source.replace(
        "    label: Mathematics\n    version: '1.2'\n",
        "    label: Mathematics\n    version: '1.2'\n"
        "  - scheme: esco\n"
        "    id: 7239f7c9-0a0d-4f09-b780-16e5f2772ea1\n"
        "    label: Contradictory label\n"
        "    version: '1.2'\n",
        1,
    )
    with pytest.raises(MarkdownValidationError, match="duplicate stable reference 'esco:"):
        validate_canonical(kind, duplicate)


@pytest.mark.parametrize(
    "mutation",
    [
        ("    label: Programming\n", "    label: Programming\n    surprise: no\n"),
        ("  - scheme: esco\n", "  - scheme: ESCO\n"),
        ("  disclosure: public\n", "  disclosure: none\n"),
    ],
)
def test_v2_rejects_unknown_nested_fields_and_invalid_structures(
    mutation: tuple[str, str],
) -> None:
    root = Path(__file__).resolve().parents[3] / "packages" / "markdown-schemas" / "examples"
    source = (root / "profile.md").read_text(encoding="utf-8")
    invalid = source.replace(*mutation, 1)
    with pytest.raises(MarkdownValidationError, match="frontmatter validation"):
        validate_canonical("profile", invalid)


def test_v1_does_not_silently_accept_v2_only_fields() -> None:
    document = profile_markdown().replace(
        "visibility: private", "occupations: []\nvisibility: private"
    )
    with pytest.raises(MarkdownValidationError, match="unknown frontmatter fields"):
        prepare_client_document(
            "profile",
            document,
            document_id=str(uuid4()),
            owner_id="owner-test",
            version=1,
        )


@pytest.mark.parametrize(
    ("kind", "document"), [("profile", profile_markdown()), ("resume", resume_markdown())]
)
def test_client_examples_validate_against_write_schemas(kind: str, document: str) -> None:
    frontmatter, _ = split_markdown(document)
    Draft202012Validator(load_schema(f"{kind}.write")).validate(frontmatter)


def test_unrepresentable_heading_name_is_rejected() -> None:
    document = profile_markdown().replace("name: Ada Lovelace", "name: 'Ada Lovelace #'")
    document = document.replace("# Ada Lovelace", "# Ada Lovelace #")
    with pytest.raises(MarkdownValidationError, match="frontmatter validation"):
        prepare_client_document(
            "profile",
            document,
            document_id=str(uuid4()),
            owner_id="owner-test",
            version=1,
        )


@pytest.mark.parametrize(
    ("kind", "fixture"),
    [
        ("profile", "profile-unknown-field.md"),
        ("profile", "profile-fenced-heading.md"),
        ("profile", "profile-duplicate-h1.md"),
        ("profile", "profile-setext-h1.md"),
        ("profile", "profile-closing-heading.md"),
        ("profile", "profile-invalid-yaml.md"),
        ("profile", "profile-duplicate-key.md"),
        ("profile", "profile-wrong-schema.md"),
        ("profile", "profile-invalid-visibility.md"),
        ("profile", "profile-malformed-timestamp.md"),
        ("resume", "resume-missing-heading.md"),
    ],
)
def test_invalid_schema_fixtures_fail(kind: str, fixture: str) -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "markdown-schemas"
        / "fixtures"
        / "invalid"
        / fixture
    )
    with pytest.raises(MarkdownValidationError):
        validate_canonical(kind, path.read_text(encoding="utf-8"))


def test_client_cannot_forge_server_fields_and_output_is_canonical() -> None:
    with pytest.raises(MarkdownValidationError, match="server-assigned"):
        prepare_client_document(
            "profile",
            profile_markdown().replace("handle: ada-lovelace", "id: forged\nhandle: ada-lovelace"),
            document_id=str(uuid4()),
            owner_id="user_test",
            version=1,
        )
    markdown, frontmatter = prepare_client_document(
        "resume", resume_markdown(), document_id=str(uuid4()), owner_id="user_test", version=1
    )
    assert "\r" not in markdown
    assert frontmatter["owner_id"] == "user_test"
    validate_canonical("resume", markdown)


def test_canonical_utf8_byte_boundary_and_multibyte_counting() -> None:
    limit = canonical_document_max_utf8_bytes()
    require_canonical_document_size("a" * limit)
    with pytest.raises(MarkdownSizeError, match=f"{limit} UTF-8 bytes"):
        require_canonical_document_size("a" * (limit + 1))

    emoji = "😀"
    require_canonical_document_size("a" * (limit - len(emoji.encode("utf-8"))) + emoji)
    with pytest.raises(MarkdownSizeError):
        require_canonical_document_size("a" * (limit - 3) + emoji)


def test_canonical_size_counts_frontmatter_and_body_and_rejects_lone_surrogates() -> None:
    limit = canonical_document_max_utf8_bytes()
    frontmatter = "---\nschema: connect.md/profile\n---\n"
    require_canonical_document_size(frontmatter + "a" * (limit - len(frontmatter.encode())))
    with pytest.raises(MarkdownSizeError):
        require_canonical_document_size(frontmatter + "a" * (limit + 1))

    with pytest.raises(MarkdownValidationError) as caught:
        require_canonical_document_size("contains\ud800")
    assert "\ud800" not in str(caught.value)

    with pytest.raises(MarkdownValidationError) as caught_prepare:
        prepare_client_document(
            "profile",
            profile_markdown().replace("Ada Lovelace", "\ud800"),
            document_id=str(uuid4()),
            owner_id="owner-test",
            version=1,
        )
    assert "\ud800" not in str(caught_prepare.value)


def test_split_rejects_oversized_utf8_input_before_yaml_load(monkeypatch) -> None:
    limit = canonical_document_max_utf8_bytes()
    prefix = "---\nschema: connect.md/profile\n---\n"
    oversized = prefix + ("x" * (limit - len(prefix.encode("utf-8")) + 1))

    def unexpected_yaml_load(*_args: object, **_kwargs: object) -> object:
        pytest.fail("oversized Markdown reached yaml.load")

    monkeypatch.setattr("app.markdown.yaml.load", unexpected_yaml_load)
    with pytest.raises(
        MarkdownSizeError,
        match=f"Markdown input exceeds {limit} UTF-8 bytes",
    ):
        split_markdown(oversized)


def test_split_rejects_yaml_alias_expansion() -> None:
    layers = ["seed: &seed [safe]"]
    previous = "seed"
    for level in range(1, 10):
        current = f"level_{level}"
        layers.append(f"{current}: &{current} [*{previous}, *{previous}]")
        previous = current
    document = "---\n" + "\n".join(layers) + "\n---\n# Alias expansion\n"

    with pytest.raises(MarkdownValidationError, match="aliases are not allowed"):
        split_markdown(document)


def test_split_names_yaml_parse_line_and_cause() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "markdown-schemas"
        / "fixtures"
        / "invalid"
        / "profile-invalid-yaml.md"
    )
    with pytest.raises(MarkdownValidationError) as caught:
        split_markdown(path.read_text(encoding="utf-8"))
    message = str(caught.value)
    assert "invalid YAML frontmatter at line 3, column " in message
    assert "while parsing a flow sequence" in message
    assert "expected ',' or ']'" in message
    assert "schema_version: [1" not in message


def test_split_names_duplicate_key_and_alias_lines() -> None:
    duplicate = "---\nname: Ada Lovelace\nname: Grace Hopper\n---\n# Ada Lovelace\n"
    with pytest.raises(
        MarkdownValidationError,
        match=r"frontmatter contains duplicate key 'name' at line 3, column 1\Z",
    ):
        split_markdown(duplicate)

    aliased = "---\nname: &person Ada Lovelace\nalias_name: *person\n---\n# Ada Lovelace\n"
    with pytest.raises(
        MarkdownValidationError,
        match=r"YAML aliases are not allowed in frontmatter at line 3, column \d+\Z",
    ):
        split_markdown(aliased)


def test_prepare_client_document_checks_final_lf_canonical_output_only() -> None:
    source = profile_markdown().replace("\n", "\r\n")
    rendered, _ = prepare_client_document(
        "profile", source, document_id=str(uuid4()), owner_id="owner-test", version=1
    )
    assert "\r" not in rendered
    require_canonical_document_size(rendered)

    oversized = profile_markdown() + "\n" + ("x" * canonical_document_max_utf8_bytes())
    with pytest.raises(MarkdownSizeError):
        prepare_client_document(
            "profile", oversized, document_id=str(uuid4()), owner_id="owner-test", version=1
        )

    historical_over_limit = rendered + ("x" * canonical_document_max_utf8_bytes())
    with pytest.raises(MarkdownSizeError):
        validate_canonical("profile", historical_over_limit)


@pytest.mark.parametrize(
    "schema_name",
    [
        "profile",
        "profile.write",
        "profile.v2",
        "profile.v2.write",
        "resume",
        "resume.write",
        "resume.v2",
        "resume.v2.write",
    ],
)
def test_profile_resume_schemas_reference_the_package_byte_manifest(schema_name: str) -> None:
    extension = load_schema(schema_name)["x-connectmd"]
    assert extension["canonical_limits_ref"] == "canonical-markdown-limits.json"
    assert extension["canonical_size_scope"] == "final_utf8_bytes_after_lf_canonicalization"
