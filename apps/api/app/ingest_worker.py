"""No-network binary conversion worker with per-job process isolation."""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.ingest import _convert_binary


def _report_conversion_failure(converter: str, exc: BaseException) -> None:
    """Write worker-only stack diagnostics without exception text or local values."""
    print(
        f"event=ingest_conversion_failed converter={converter} exception_type={type(exc).__name__}",
        file=sys.stderr,
        flush=True,
    )
    print("Traceback (most recent call last):", file=sys.stderr)
    traceback.print_tb(exc.__traceback__, file=sys.stderr)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    pending = path.with_name(f".{path.name}.pending-{os.getpid()}")
    pending.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(pending, path)


def _cleanup_pending_result_artifacts(root: Path, job_id: str | None = None) -> None:
    """Best-effort cleanup of only atomic result-write leftovers."""
    pattern = (
        f".{job_id}.result.json.pending-*" if job_id is not None else "*.result.json.pending-*"
    )
    failures = 0
    for path in root.glob(pattern):
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        except OSError:
            failures += 1
    if failures:
        print(
            "WARNING: unable to remove pending ingest result artifacts; cleanup is incomplete",
            file=sys.stderr,
        )


def _convert_job(input_path: str, suffix: str, output_path: str, maximum: int) -> None:
    try:
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        if os.name == "posix":
            # Converter helpers (Tesseract/poppler) inherit this dedicated group
            # so the hard deadline can terminate the complete process tree.
            getattr(os, "setsid")()  # noqa: B009 - absent from Windows type stubs
        text, converter, warnings = _convert_binary(
            Path(input_path).read_bytes(),
            suffix,
            failure_reporter=_report_conversion_failure,
        )
        if len(text.encode("utf-8")) > maximum:
            _atomic_json(
                Path(output_path),
                {
                    "ok": False,
                    "status_code": 422,
                    "message": "converted text exceeds the configured extracted-text limit",
                    "warnings": warnings,
                },
            )
            return
        _atomic_json(
            Path(output_path),
            {"ok": True, "text": text, "converter": converter, "warnings": warnings},
        )
    except HTTPException as exc:
        detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        status_code = exc.status_code if exc.status_code in {422, 503} else 422
        _atomic_json(
            Path(output_path),
            {
                "ok": False,
                "status_code": status_code,
                "message": str(detail.get("message", "binary conversion failed")),
                "warnings": [str(item) for item in detail.get("warnings", [])],
            },
        )
    except BaseException as exc:
        _atomic_json(
            Path(output_path),
            {
                "ok": False,
                "status_code": 503,
                "message": "binary conversion failed in the isolated worker",
                "warnings": [f"Converter process returned {type(exc).__name__}."],
            },
        )


def _terminate_process_tree(process: Any) -> None:
    if os.name == "posix" and process.pid is not None:
        try:
            getattr(os, "killpg")(  # noqa: B009 - absent from Windows type stubs
                process.pid, signal.SIGTERM
            )
        except ProcessLookupError:
            pass
    else:
        process.terminate()
    process.join(5)
    if os.name == "posix" and process.pid is not None:
        # Kill the group even if the Python child exited after SIGTERM; native
        # converter grandchildren may still be alive in the same session.
        try:
            getattr(os, "killpg")(  # noqa: B009 - absent from Windows type stubs
                process.pid,
                getattr(signal, "SIGKILL"),  # noqa: B009 - absent from Windows type stubs
            )
        except ProcessLookupError:
            pass
    if process.is_alive():
        process.kill()
    process.join()


def _process_request(request_path: Path, heartbeat: Path) -> None:
    processing = request_path.with_suffix(".processing")
    try:
        os.replace(request_path, processing)
    except FileNotFoundError:
        return
    job_id = request_path.name.removesuffix(".request.json")
    root = request_path.parent
    input_path = root / f"{job_id}.input"
    output_path = root / f"{job_id}.result.json"
    process: Any | None = None
    process_started = False
    try:
        request = json.loads(processing.read_text(encoding="utf-8"))
        suffix = request["suffix"]
        timeout = int(request["timeout_seconds"])
        maximum = int(request["max_extracted_bytes"])
        if suffix not in {".pdf", ".docx"} or not (5 <= timeout <= 120):
            raise ValueError("invalid conversion job")
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_convert_job,
            args=(str(input_path), suffix, str(output_path), maximum),
        )
        process.start()
        process_started = True
        deadline = time.monotonic() + timeout
        while process.is_alive() and time.monotonic() < deadline:
            process.join(0.5)
            heartbeat.touch()
        if process.is_alive():
            _terminate_process_tree(process)
            _atomic_json(
                output_path,
                {
                    "ok": False,
                    "status_code": 503,
                    "message": "binary conversion exceeded the hard time limit",
                    "warnings": [],
                },
            )
        elif not output_path.exists():
            _atomic_json(
                output_path,
                {
                    "ok": False,
                    "status_code": 503,
                    "message": "binary conversion process exited without a result",
                    "warnings": [],
                },
            )
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        _atomic_json(
            output_path,
            {
                "ok": False,
                "status_code": 503,
                "message": "isolated worker rejected an invalid conversion job",
                "warnings": [type(exc).__name__],
            },
        )
    finally:
        try:
            if process_started and process is not None and process.is_alive():
                _terminate_process_tree(process)
        finally:
            _cleanup_pending_result_artifacts(root, job_id)
            for path in (input_path, processing):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def _recover_orphaned_jobs(root: Path) -> None:
    """Requeue jobs interrupted when this serial worker was stopped."""
    for processing in root.glob("*.request.processing"):
        job_id = processing.name.removesuffix(".request.processing")
        input_path = root / f"{job_id}.input"
        request_path = root / f"{job_id}.request.json"
        result_path = root / f"{job_id}.result.json"
        if result_path.exists() or not input_path.exists():
            input_path.unlink(missing_ok=True)
            processing.unlink(missing_ok=True)
            continue
        if request_path.exists():
            processing.unlink(missing_ok=True)
            continue
        os.replace(processing, request_path)


def _cleanup_stale_jobs(root: Path, maximum_age_seconds: int = 300) -> None:
    """Remove abandoned protocol files after every possible API waiter expired."""
    cutoff = time.time() - maximum_age_seconds
    patterns = (
        "*.input",
        "*.request.json",
        "*.request.processing",
        "*.result.json",
        ".*.pending*",
    )
    for pattern in patterns:
        for path in root.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass


def main() -> None:
    root_value = os.environ.get("CONNECTMD_INGEST_JOBS_PATH")
    if not root_value:
        raise SystemExit("CONNECTMD_INGEST_JOBS_PATH is required")
    root = Path(root_value).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _cleanup_pending_result_artifacts(root)
    _recover_orphaned_jobs(root)
    _cleanup_stale_jobs(root)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    heartbeat = root / ".worker-ready"
    last_cleanup = time.monotonic()
    while not stopping:
        heartbeat.touch()
        for request in root.glob("*.request.json"):
            _process_request(request, heartbeat)
            if stopping:
                break
        if time.monotonic() - last_cleanup >= 60:
            _cleanup_stale_jobs(root)
            last_cleanup = time.monotonic()
        time.sleep(0.1)
    heartbeat.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
