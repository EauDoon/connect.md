"""Bounded local-file ingestion that always returns an unpublished draft."""

from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from time import monotonic, sleep
from time import time as wall_time
from uuid import uuid4

import anyio
from fastapi import HTTPException, UploadFile, status

from app.config import Settings
from app.markdown import (
    PUBLIC_MARKDOWN_VALIDATION_DETAIL,
    MarkdownValidationError,
    canonical_document_max_utf8_bytes,
    client_template,
    prepare_client_document,
)

SUPPORTED_UPLOAD_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".markdown": {"text/markdown", "text/plain", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
}

WORKER_HEARTBEAT_MAX_AGE_SECONDS = 2.0


def _worker_heartbeat_ready(root: Path) -> bool:
    """Return only recent worker liveness, not conversion success readiness."""
    try:
        age_seconds = wall_time() - (root / ".worker-ready").stat().st_mtime
    except OSError:
        return False
    return abs(age_seconds) <= WORKER_HEARTBEAT_MAX_AGE_SECONDS


def ingest_capabilities(settings: Settings) -> dict[str, object]:
    """Return the configured ingestion contract without duplicating its limits."""
    worker_configured = settings.ingest_jobs_path is not None
    worker_heartbeat_ready = (
        _worker_heartbeat_ready(settings.ingest_jobs_path)
        if settings.ingest_jobs_path is not None
        else False
    )
    return {
        "formats": [
            {
                "source_type": suffix.removeprefix("."),
                "extensions": [suffix],
                "mime_types": sorted(media_types),
            }
            for suffix, media_types in SUPPORTED_UPLOAD_TYPES.items()
        ],
        "limits": {
            "max_upload_bytes": settings.max_upload_bytes,
            "max_extracted_bytes": settings.max_extracted_bytes,
            "canonical_document_max_utf8_bytes": canonical_document_max_utf8_bytes(),
            "conversion_timeout_seconds": settings.ingest_timeout_seconds,
            "max_docx_entries": settings.max_docx_entries,
            "max_docx_uncompressed_bytes": settings.max_docx_uncompressed_bytes,
        },
        "binary_conversion": {
            "requires_isolated_worker": True,
            "worker_configured": worker_configured,
            "worker_heartbeat_ready": worker_heartbeat_ready,
            "unconfigured_behavior": "fail_closed",
        },
    }


def _ingest_error(
    status_code: int,
    message: str,
    *,
    source_type: str = "unknown",
    converter: str = "none",
    warnings: list[str] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "message": message,
            "warnings": warnings or [],
            "provenance": {"source_type": source_type, "converter": converter},
        },
    )


async def build_ingest_draft(
    upload: UploadFile,
    target_schema: str,
    schema_version: int,
    settings: Settings,
    limiter: anyio.CapacityLimiter | None = None,
) -> tuple[str, list[str], dict[str, str]]:
    filename = upload.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_TYPES:
        raise _ingest_error(415, "supported uploads are PDF, DOCX, Markdown, and plain text")
    source_type = suffix.removeprefix(".")
    content_type = (
        (upload.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    )
    if content_type not in SUPPORTED_UPLOAD_TYPES[suffix]:
        raise _ingest_error(
            415, "upload MIME type does not match its permitted file type", source_type=source_type
        )
    contents = await upload.read(settings.max_upload_bytes + 1)
    if len(contents) > settings.max_upload_bytes:
        raise _ingest_error(
            413, "upload exceeds the configured size limit", source_type=source_type
        )
    if not contents:
        raise _ingest_error(422, "upload is empty", source_type=source_type)
    _validate_file_signature(contents, suffix, settings)
    warnings: list[str] = []
    provenance = {"source_type": source_type, "converter": "direct"}
    if suffix in {".md", ".markdown", ".txt"}:
        try:
            extracted = contents.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _ingest_error(422, "text uploads must be UTF-8", source_type=source_type) from exc
    else:
        if settings.ingest_jobs_path is None:
            raise _ingest_error(
                503,
                "binary conversion requires a configured isolated worker",
                source_type=source_type,
            )
        extracted, converter, conversion_warnings = await anyio.to_thread.run_sync(
            _convert_isolated,
            contents,
            suffix,
            settings,
            limiter=limiter,
        )
        warnings.extend(conversion_warnings)
        provenance["converter"] = converter
    if len(extracted.encode("utf-8")) > settings.max_extracted_bytes:
        raise _ingest_error(
            422,
            "converted text exceeds the configured extracted-text limit",
            source_type=source_type,
            converter=provenance["converter"],
            warnings=warnings,
        )
    draft = client_template(target_schema, extracted, schema_version=schema_version)
    try:
        # Validate the draft deterministically without persisting or publishing it.
        prepare_client_document(
            target_schema,
            draft,
            document_id=str(uuid4()),
            owner_id="ingest_draft",
            version=1,
        )
    except MarkdownValidationError as exc:
        raise _ingest_error(
            422,
            PUBLIC_MARKDOWN_VALIDATION_DETAIL,
            source_type=source_type,
            converter=provenance["converter"],
            warnings=warnings,
        ) from exc
    provenance["schema_version"] = str(schema_version)
    return draft, warnings, provenance


def _convert_isolated(
    contents: bytes, suffix: str, settings: Settings
) -> tuple[str, str, list[str]]:
    """Exchange one binary job with the no-network conversion worker."""
    assert settings.ingest_jobs_path is not None
    try:
        root = settings.ingest_jobs_path.resolve()
        root.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError) as exc:
        raise _ingest_error(
            503,
            "isolated conversion worker is unavailable",
            source_type=suffix.removeprefix("."),
        ) from exc
    if not _worker_heartbeat_ready(root):
        raise _ingest_error(
            503,
            "isolated conversion worker is unavailable",
            source_type=suffix.removeprefix("."),
        )
    job_id = str(uuid4())
    input_path = root / f"{job_id}.input"
    request_path = root / f"{job_id}.request.json"
    result_path = root / f"{job_id}.result.json"
    input_pending = root / f".{job_id}.input.pending"
    request_pending = root / f".{job_id}.request.pending"
    processing_path = root / f"{job_id}.request.processing"
    try:
        input_pending.write_bytes(contents)
        os.replace(input_pending, input_path)
        request_pending.write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "suffix": suffix,
                    "timeout_seconds": settings.ingest_timeout_seconds,
                    "max_extracted_bytes": settings.max_extracted_bytes,
                }
            ),
            encoding="utf-8",
        )
        os.replace(request_pending, request_path)
        deadline = monotonic() + settings.ingest_timeout_seconds + 10
        while monotonic() < deadline:
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("invalid isolated-worker result")
                raw_warnings = result.get("warnings", [])
                if not isinstance(raw_warnings, list):
                    raise ValueError("invalid isolated-worker warnings")
                result_warnings = [str(item) for item in raw_warnings]
                if result.get("ok") is True and isinstance(result.get("text"), str):
                    return (
                        result["text"],
                        str(result.get("converter", "isolated-worker")),
                        result_warnings,
                    )
                result_status = result.get("status_code", 422)
                if type(result_status) is not int or result_status not in {422, 503}:
                    raise ValueError("invalid isolated-worker status")
                raise _ingest_error(
                    result_status,
                    str(result.get("message", "binary conversion failed")),
                    source_type=suffix.removeprefix("."),
                    warnings=result_warnings,
                )
            sleep(0.05)
    except HTTPException:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _ingest_error(
            503,
            "isolated conversion worker is unavailable",
            source_type=suffix.removeprefix("."),
        ) from exc
    finally:
        for path in (
            input_pending,
            request_pending,
            input_path,
            request_path,
            processing_path,
            result_path,
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    raise _ingest_error(
        503,
        "isolated conversion worker did not return before the deadline",
        source_type=suffix.removeprefix("."),
    )


def _validate_file_signature(contents: bytes, suffix: str, settings: Settings) -> None:
    if suffix == ".pdf":
        if not contents.startswith(b"%PDF-"):
            raise _ingest_error(
                422, "PDF upload does not have a valid PDF signature", source_type="pdf"
            )
        return
    if suffix != ".docx":
        return
    if not contents.startswith(b"PK\x03\x04"):
        raise _ingest_error(
            422, "DOCX upload does not have a valid ZIP signature", source_type="docx"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            entries = archive.infolist()
            if len(entries) > settings.max_docx_entries:
                raise _ingest_error(422, "DOCX archive has too many entries", source_type="docx")
            total_size = sum(entry.file_size for entry in entries)
            if total_size > settings.max_docx_uncompressed_bytes:
                raise _ingest_error(
                    422,
                    "DOCX archive expands beyond the configured safety limit",
                    source_type="docx",
                )
            if "word/document.xml" not in {entry.filename for entry in entries}:
                raise _ingest_error(
                    422, "DOCX archive is missing word/document.xml", source_type="docx"
                )
    except HTTPException:
        raise
    except zipfile.BadZipFile as exc:
        raise _ingest_error(
            422, "DOCX upload is not a valid ZIP archive", source_type="docx"
        ) from exc


def _convert_binary(
    contents: bytes,
    suffix: str,
    *,
    failure_reporter: Callable[[str, BaseException], None] | None = None,
) -> tuple[str, str, list[str]]:
    """Use only server-created local files; never pass a user path/URL to converters."""
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="connectmd-ingest-") as directory:
        local_path = Path(directory) / f"source{suffix}"
        local_path.write_bytes(contents)
        try:
            from markitdown import MarkItDown

            converted = MarkItDown().convert_local(str(local_path))
            text = getattr(converted, "text_content", "")
            if isinstance(text, str) and text.strip():
                return text, "markitdown-local", warnings
            warnings.append("MarkItDown returned no text; tried layout-aware fallback.")
        except Exception as exc:  # Converter errors are surfaced as non-sensitive draft warnings.
            if failure_reporter is not None:
                failure_reporter("markitdown", exc)
            warnings.append(f"MarkItDown conversion was unavailable: {type(exc).__name__}.")
        try:
            from unstructured.partition.auto import partition

            if suffix == ".pdf":
                # MarkItDown already handled text PDFs. The fallback is therefore
                # explicitly local OCR and never downloads a layout model.
                elements = partition(filename=str(local_path), strategy="ocr_only")
            else:
                elements = partition(filename=str(local_path))
            text = "\n".join(str(element) for element in elements).strip()
            if text:
                return text, "unstructured-local", warnings
            warnings.append("Layout-aware conversion returned no text.")
        except Exception as exc:
            if failure_reporter is not None:
                failure_reporter("unstructured", exc)
            warnings.append(f"Layout-aware conversion was unavailable: {type(exc).__name__}.")
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "message": "the upload could not be converted into a validated draft",
            "warnings": warnings,
            "provenance": {"source_type": suffix.removeprefix("."), "converter": "none"},
        },
    )
