#!/usr/bin/env bash
# Exercise the built API image and isolated converter without network access.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly IMAGE="connectmd-api:local"
readonly COMPOSE_ARGS=(
  --env-file "$REPO_ROOT/.env"
  -f "$REPO_ROOT/compose.yaml"
  -f "$REPO_ROOT/compose.prod.yaml"
)

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

temp_root="${TMPDIR:-/tmp}"
scratch="$(mktemp -d "$temp_root/connectmd-converter-built-image.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT
  case "$scratch" in
    "$temp_root"/connectmd-converter-built-image.*) rm -rf -- "$scratch" ;;
    *) printf 'ERROR: Refusing unsafe test cleanup: %s\n' "$scratch" >&2; status=1 ;;
  esac
  exit "$status"
}
trap cleanup EXIT

fixtures="$scratch/fixtures"
python3 "$REPO_ROOT/apps/api/tests/fixtures/ingest/generate.py" "$fixtures"
printf '%s\n' '# Ada Lovelace' 'Python systems engineer' > "$fixtures/valid.md"

converter_id="$(docker compose "${COMPOSE_ARGS[@]}" ps -q converter)"
[ -n "$converter_id" ] || die "converter service is not running"
network_mode="$(docker inspect "$converter_id" --format '{{.HostConfig.NetworkMode}}')"
[ "$network_mode" = "none" ] || die "converter service is not network-isolated: $network_mode"
converter_image="$(docker inspect "$converter_id" --format '{{.Config.Image}}')"
[ "$converter_image" = "$IMAGE" ] || die "converter service is not using the built image: $converter_image"
ingest_volume="$(docker inspect "$converter_id" --format '{{range .Mounts}}{{if eq .Destination "/ingest-jobs"}}{{.Name}}{{end}}{{end}}')"
[ -n "$ingest_volume" ] || die "converter shared ingest volume was not found"

run_ingest_case() {
  local fixture="$1" suffix="$2" maximum="$3" expected="$4" expected_message="$5"
  docker run --rm \
    --network none \
    --user 10001:10001 \
    --read-only \
    --tmpfs /tmp:size=128m,mode=1777 \
    --volume "$ingest_volume:/ingest-jobs" \
    --volume "$fixtures:/fixtures:ro" \
    --env CONNECTMD_INGEST_JOBS_PATH=/ingest-jobs \
    "$IMAGE" python - "$fixture" "$suffix" "$maximum" "$expected" "$expected_message" <<'PY'
import asyncio
import sys
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.config import Settings
from app.ingest import build_ingest_draft


fixture, suffix, maximum, expected, expected_message = sys.argv[1:]
content_type = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
}[suffix]
settings = Settings(
    ingest_jobs_path=Path("/ingest-jobs"),
    ingest_timeout_seconds=10,
    max_extracted_bytes=int(maximum),
)


async def run() -> None:
    upload = UploadFile(
        file=BytesIO((Path("/fixtures") / fixture).read_bytes()),
        filename=fixture,
        headers=Headers({"content-type": content_type}),
    )
    try:
        draft, _warnings, provenance = await build_ingest_draft(
            upload, "profile", 2, settings
        )
    except HTTPException as exc:
        if expected == "valid":
            raise SystemExit(f"valid {fixture} was rejected: {exc.status_code}")
        if exc.status_code != 422:
            raise SystemExit(f"{fixture} returned unexpected status {exc.status_code}")
        detail = exc.detail
        if not isinstance(detail, dict):
            raise SystemExit(f"{fixture} did not return structured provenance")
        if detail.get("message") != expected_message:
            raise SystemExit(
                f"{fixture} returned an unexpected bounded error: {detail.get('message')!r}"
            )
        source_type = detail.get("provenance", {}).get("source_type")
        if source_type != suffix.removeprefix("."):
            raise SystemExit(f"{fixture} provenance was not source-faithful")
    else:
        if expected != "valid":
            raise SystemExit(f"invalid {fixture} unexpectedly converted")
        if "Ada Lovelace" not in draft:
            raise SystemExit(f"{fixture} lost the fixture text")
        if provenance.get("source_type") != suffix.removeprefix("."):
            raise SystemExit(f"{fixture} source provenance was not preserved")
        expected_converters = (
            {"direct"} if suffix == ".md" else {"markitdown-local", "unstructured-local"}
        )
        if provenance.get("converter") not in expected_converters:
            raise SystemExit(f"{fixture} used an unexpected converter")

    residue = sorted(
        path.name
        for path in Path("/ingest-jobs").iterdir()
        if path.name != ".worker-ready"
    )
    if residue:
        raise SystemExit(f"orphan ingest protocol files remain: {residue}")


asyncio.run(run())
PY
}

run_ingest_case valid.pdf .pdf 8192 valid ""
run_ingest_case valid.docx .docx 8192 valid ""
run_ingest_case valid.md .md 8192 valid ""
run_ingest_case malformed.pdf .pdf 8192 invalid "PDF upload does not have a valid PDF signature"
run_ingest_case malformed.docx .docx 8192 invalid "DOCX upload does not have a valid ZIP signature"
run_ingest_case oversized.pdf .pdf 1024 oversized "converted text exceeds the configured extracted-text limit"
run_ingest_case oversized.docx .docx 1024 oversized "converted text exceeds the configured extracted-text limit"

# The true timeout/crash branches are simulated with the real worker finalizer
# and bounded fake processes; no unsafe kill or converter dependency is needed.
docker run --rm \
  --network none \
  --user 10001:10001 \
  --read-only \
  --tmpfs /tmp:size=128m,mode=1777 \
  "$IMAGE" python - <<'PY'
import json
from pathlib import Path

import app.ingest_worker as worker


root = Path("/tmp/converter-supervision-sim")
root.mkdir(parents=True, exist_ok=True)
heartbeat = root / ".worker-ready"
heartbeat.touch()


class FakeProcess:
    def __init__(self, alive: bool) -> None:
        self.alive = alive
        self.pid = None

    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return self.alive

    def join(self, _timeout: float | None = None) -> None:
        return None


class FakeContext:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process

    def Process(self, *, target: object, args: tuple[object, ...]) -> FakeProcess:
        del target, args
        return self.process


def run_case(job_id: str, *, alive: bool, timeout: bool) -> None:
    request = root / f"{job_id}.request.json"
    request.write_text(
        json.dumps({"suffix": ".pdf", "timeout_seconds": 5, "max_extracted_bytes": 1024}),
        encoding="utf-8",
    )
    (root / f"{job_id}.input").write_bytes(b"not-converted")
    process = FakeProcess(alive)
    worker.multiprocessing.get_context = lambda _name: FakeContext(process)
    if timeout:
        worker.time.monotonic = iter((100.0, 106.0)).__next__

        def terminate(process_to_terminate: FakeProcess) -> None:
            process_to_terminate.alive = False

        worker._terminate_process_tree = terminate
    else:
        worker.time.monotonic = lambda: 100.0
    worker._process_request(request, heartbeat)
    result_path = root / f"{job_id}.result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status_code") != 503:
        raise SystemExit(f"{job_id} did not fail closed")
    expected_message = (
        "binary conversion exceeded the hard time limit"
        if timeout
        else "binary conversion process exited without a result"
    )
    if result.get("message") != expected_message:
        raise SystemExit(f"{job_id} returned the wrong bounded message")
    leftovers = [
        root / f"{job_id}.input",
        root / f"{job_id}.request.processing",
        root / f".{job_id}.result.json.pending-123",
    ]
    if any(path.exists() for path in leftovers):
        raise SystemExit(f"{job_id} left an orphan protocol file")
    result_path.unlink()


run_case("timeout", alive=True, timeout=True)
run_case("crash", alive=False, timeout=False)
print("CONVERTER_SUPERVISION_SIMULATION=PASS")
PY

printf 'CONVERTER_BUILT_IMAGE=PASS\n'
