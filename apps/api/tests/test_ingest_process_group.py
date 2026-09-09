from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.ingest_worker import _terminate_process_tree


def _conversion_with_resistant_descendant(contents, suffix, **kwargs):
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, signal, sys, time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "p = Path(sys.argv[1]); pending = p.with_suffix('.pending'); "
            "pending.write_text(str(os.getpid())); pending.rename(p); time.sleep(60)",
            contents.decode(),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(60)
    return "unreachable", "test", []


def _run_isolated_conversion(input_path, output_path):
    import app.ingest_worker as worker

    worker._convert_binary = _conversion_with_resistant_descendant
    worker._convert_job(input_path, ".pdf", output_path, 1024)


def _descendant_is_running(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1]
    except FileNotFoundError:
        return False
    return not state.startswith("Z ")


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux process-group semantics")
def test_real_worker_group_termination_kills_sigterm_resistant_descendant(tmp_path):
    descendant_path = tmp_path / "descendant.pid"
    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(str(descendant_path).encode())
    process = multiprocessing.get_context("spawn").Process(
        target=_run_isolated_conversion,
        args=(str(input_path), str(tmp_path / "result.json")),
    )
    process.start()
    try:
        deadline = time.monotonic() + 10
        while not descendant_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert descendant_path.exists(), "isolated worker did not start the synthetic converter"
        descendant_pid = int(descendant_path.read_text())
        assert os.getpgid(descendant_pid) == process.pid
        _terminate_process_tree(process)
        assert not process.is_alive()
        # An orphan can briefly remain a zombie until its reaper runs; it must
        # never remain a running or sleeping converter after group termination.
        deadline = time.monotonic() + 2
        while _descendant_is_running(descendant_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _descendant_is_running(descendant_pid)
    finally:
        _terminate_process_tree(process)
