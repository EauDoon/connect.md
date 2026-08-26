"""Pure JSON schema contract validators for the platform checker."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

ErrorReporter = Callable[[list[str], str, str], None]


def schema_is_expected(
    schema: Any,
    errors: list[str],
    *,
    error: ErrorReporter,
) -> None:
    if not isinstance(schema, dict):
        error(errors, "schema", "must be a JSON object")
        return
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        error(errors, "schema.$schema", "must declare JSON Schema draft 2020-12")
    if (
        schema.get("$id")
        != "https://connect.md/schemas/platform-feature-registry.schema.json"
    ):
        error(errors, "schema.$id", "does not identify the platform registry schema")
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
    ):
        error(errors, "schema", "must be a closed object schema")
    required = schema.get("required")
    if required != ["schema_version", "registry_id", "features"]:
        error(
            errors,
            "schema.required",
            "does not require the registry identity and features",
        )
    stages = (
        schema.get("$defs", {})
        .get("feature", {})
        .get("properties", {})
        .get("stage", {})
        .get("enum")
    )
    if stages != [
        "design",
        "implemented",
        "feature_gated",
        "repository_verified",
        "deployment_verified",
        "releasable",
        "disabled",
    ]:
        error(
            errors,
            "schema.$defs.feature.properties.stage",
            "does not define the required stage vocabulary",
        )


def evidence_schema_is_expected(
    schema: Any,
    errors: list[str],
    *,
    error: ErrorReporter,
    evidence_receipt_fields: set[str],
    evidence_check_fields: set[str],
    id_pattern: str,
    revision_pattern: str,
    sha256_pattern: str,
) -> None:
    location = "evidence_schema"
    if not isinstance(schema, dict):
        error(errors, location, "must be an object")
        return
    expected_scalars = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://connect.md/schemas/platform-evidence-receipt.schema.json",
        "type": "object",
        "additionalProperties": False,
    }
    for field, expected in expected_scalars.items():
        if schema.get(field) != expected:
            error(errors, f"{location}.{field}", f"must equal {expected!r}")
    if set(schema.get("required", [])) != evidence_receipt_fields:
        error(
            errors, f"{location}.required", "does not define the closed receipt fields"
        )
    properties = schema.get("properties")
    if not isinstance(properties, dict) or set(properties) != evidence_receipt_fields:
        error(
            errors,
            f"{location}.properties",
            "does not define the closed receipt fields",
        )
        return
    if properties.get("schema_version", {}).get("const") != 1:
        error(errors, f"{location}.properties.schema_version", "must require version 1")
    if properties.get("evidence_type", {}).get("enum") != [
        "repository",
        "deployment",
    ]:
        error(
            errors,
            f"{location}.properties.evidence_type",
            "must define repository and deployment evidence",
        )
    if properties.get("source_revision", {}).get("pattern") != revision_pattern:
        error(
            errors,
            f"{location}.properties.source_revision",
            "must require a full lowercase Git revision",
        )
    checks = properties.get("checks")
    if not isinstance(checks, dict) or checks.get("minItems") != 1:
        error(errors, f"{location}.properties.checks", "must be non-empty")
        return
    item = checks.get("items")
    if not isinstance(item, dict):
        error(errors, f"{location}.properties.checks.items", "must be an object")
        return
    if item.get("additionalProperties") is not False:
        error(
            errors,
            f"{location}.properties.checks.items.additionalProperties",
            "must be false",
        )
    if set(item.get("required", [])) != evidence_check_fields:
        error(
            errors,
            f"{location}.properties.checks.items.required",
            "does not define the closed check fields",
        )
    check_properties = item.get("properties")
    if (
        not isinstance(check_properties, dict)
        or set(check_properties) != evidence_check_fields
    ):
        error(
            errors,
            f"{location}.properties.checks.items.properties",
            "does not define the closed check fields",
        )
        return
    if check_properties.get("result", {}).get("const") != "pass":
        error(errors, f"{location}.properties.checks.result", "must require pass")
    if check_properties.get("check_id", {}).get("pattern") != id_pattern:
        error(
            errors,
            f"{location}.properties.checks.check_id",
            "must require a lowercase hyphenated control identifier",
        )
    if check_properties.get("output_sha256", {}).get("pattern") != sha256_pattern:
        error(
            errors,
            f"{location}.properties.checks.output_sha256",
            "must require a lowercase SHA-256 digest",
        )
