"""Tests for strict generation stdio IPC full child process isolation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ipc.progress import FATAL_DIAGNOSTIC_MESSAGE, ProgressPhase


def test_subprocess_execution_with_all_contamination_sources() -> None:
    """Proves full child process isolates stdout/stderr against all Python, C, and Win32 contamination."""
    runner_script = """
import json
import logging
import os
import sys

from application.models import GenerationRequest
from ipc.stdio import main

def noisy_handler(req: GenerationRequest):
    # 1. Python standard print
    print("SUBPROCESS_PRINT_CONTAMINATION")

    # 2. Python explicit stdout.write
    sys.stdout.write("SUBPROCESS_SYS_STDOUT_WRITE_CONTAMINATION\\n")
    sys.stdout.flush()

    # 3. Python standard stderr.write
    sys.stderr.write("SUBPROCESS_STDERR_CONTAMINATION\\n")
    sys.stderr.flush()

    # 4. Standard library logging (to default stderr handler)
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.INFO)
    logger.info("SUBPROCESS_LOGGING_INFO_CONTAMINATION")
    logger.error("SUBPROCESS_LOGGING_ERROR_CONTAMINATION")

    # 5. C-runtime descriptor 1 and 2 writes
    os.write(1, b"SUBPROCESS_C_FD1_CONTAMINATION\\n")
    os.write(2, b"SUBPROCESS_C_FD2_CONTAMINATION\\n")

    # 6. Win32 WriteFile to console handles
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_char_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL

        h_out = kernel32.GetStdHandle(0xFFFFFFF5)  # STD_OUTPUT_HANDLE
        h_err = kernel32.GetStdHandle(0xFFFFFFF4)  # STD_ERROR_HANDLE
        written = wintypes.DWORD(0)

        msg_out = b"SUBPROCESS_WIN32_STDOUT_POISON\\n"
        ret1 = kernel32.WriteFile(h_out, msg_out, len(msg_out), ctypes.byref(written), None)
        assert ret1 != 0, f"WriteFile(STD_OUTPUT_HANDLE) failed with error {ctypes.get_last_error()}"

        msg_err = b"SUBPROCESS_WIN32_STDERR_POISON\\n"
        ret2 = kernel32.WriteFile(h_err, msg_err, len(msg_err), ctypes.byref(written), None)
        assert ret2 != 0, f"WriteFile(STD_ERROR_HANDLE) failed with error {ctypes.get_last_error()}"

    return {
        "contract_version": "1.0",
        "request_id": req.request_id,
        "status": "accepted",
        "data": {
            "artifacts": [
                {"type": "native_part", "format": "par", "path": "p.par", "origin": "cad_copilot"},
                {"type": "geometry_step", "format": "step", "path": "p.step", "origin": "cad_copilot"},
                {"type": "mesh_stl", "format": "stl", "path": "p.stl", "origin": "cad_copilot"},
            ]
        },
        "warnings": []
    }

sys.exit(main(argv=[], _composition_handler=noisy_handler))
"""
    input_payload = json.dumps(
        {
            "contract_version": "1.0",
            "request_id": "req-subproc-01",
            "kind": "example_plan",
            "unit": "mm",
            "example_id": "spur_gear",
        }
    ).encode("utf-8")

    engine_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(engine_root, "src")

    proc = subprocess.run(
        [sys.executable, "-c", runner_script],
        input=input_payload,
        capture_output=True,
        env=env,
    )

    assert proc.returncode == 0, (
        f"Subprocess failed:\nStdout: {proc.stdout.decode('utf-8', errors='replace')}\n"
        f"Stderr: {proc.stderr.decode('utf-8', errors='replace')}"
    )

    # Stdout: byte-exact single line ending in LF
    assert proc.stdout.endswith(b"\n")
    assert b"CONTAMINATION" not in proc.stdout
    assert b"POISON" not in proc.stdout
    resp = json.loads(proc.stdout.decode("utf-8"))
    assert resp["status"] == "accepted"
    assert resp["request_id"] == "req-subproc-01"

    # Stderr: exactly 4 JSONL lines in canonical sequence
    assert b"CONTAMINATION" not in proc.stderr
    assert b"POISON" not in proc.stderr
    stderr_lines = [json.loads(line) for line in proc.stderr.decode("ascii").strip().split("\n")]
    assert len(stderr_lines) == 4
    expected_phases: list[ProgressPhase] = [
        "request_received",
        "request_validated",
        "generation_started",
        "response_ready",
    ]
    for idx, phase in enumerate(expected_phases):
        assert stderr_lines[idx]["phase"] == phase


def test_subprocess_unexpected_argv_exits_1() -> None:
    """Proves subprocess with extra CLI arguments exits 1 with fatal diagnostic."""
    runner_script = """
import sys
from ipc.stdio import main
sys.exit(main())
"""
    engine_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(engine_root, "src")

    proc = subprocess.run(
        [sys.executable, "-c", runner_script, "--not-allowed"],
        capture_output=True,
        env=env,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag["type"] == "diagnostic"
    assert diag["phase"] == "fatal"
    assert diag["message"] == FATAL_DIAGNOSTIC_MESSAGE


def test_subprocess_forced_bootstrap_failure_restores_descriptors_and_emits_diagnostic() -> None:
    """Proves bootstrap failure restores descriptors and writes fatal diagnostic to parent stderr."""
    runner_script = """
import sys
from unittest.mock import patch
import ipc.stdio

with patch("ipc.stdio._redirect_windows_handles", side_effect=OSError("Forced handle setup failure")):
    sys.exit(ipc.stdio.main([]))
"""
    engine_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(engine_root, "src")

    proc = subprocess.run(
        [sys.executable, "-c", runner_script],
        capture_output=True,
        env=env,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert proc.stderr.endswith(b"\n")
    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag["type"] == "diagnostic"
    assert diag["phase"] == "fatal"
    assert diag["message"] == FATAL_DIAGNOSTIC_MESSAGE


def _install_recording_popen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    signal_failure: bool = False,
    signal_marker: Path | None = None,
) -> tuple[list[subprocess.Popen[Any]], list[str]]:
    """Record timeout cleanup events while preserving real child-process behavior."""
    original_popen = subprocess.Popen
    spawned: list[subprocess.Popen[Any]] = []
    events: list[str] = []

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = original_popen(*args, **kwargs)
        original_send_signal = process.send_signal
        original_kill = process.kill

        def recording_send_signal(sig: Any) -> None:
            events.append("signal")
            if signal_failure:
                raise OSError("Simulated send_signal delivery failure")
            if signal_marker is not None:
                signal_marker.write_text("signalled", encoding="ascii")
                return
            original_send_signal(sig)

        def recording_kill() -> None:
            events.append("kill")
            original_kill()

        process.send_signal = recording_send_signal  # type: ignore[method-assign]
        process.kill = recording_kill  # type: ignore[method-assign]
        spawned.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    return spawned, events


def test_run_live_subprocess_timeout_graceful_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves _run_live_subprocess sends cancellation signal and handles graceful exit."""
    from tests.ipc.test_solidedge_live import _run_live_subprocess

    runner_script = """
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    if marker.is_file():
        sys.exit(130)
    time.sleep(0.01)
"""
    signal_marker = tmp_path / "graceful-cancellation.marker"
    spawned, events = _install_recording_popen(monkeypatch, signal_marker=signal_marker)

    with pytest.raises(AssertionError, match=r"Live process timed out after 0\.5 seconds"):
        _run_live_subprocess(
            [sys.executable, "-c", runner_script, str(signal_marker)],
            input_bytes=b"",
            env=os.environ.copy(),
            cwd=tmp_path,
            timeout=0.5,
            grace_seconds=2.0,
        )

    assert events == ["signal"]
    assert signal_marker.read_text(encoding="ascii") == "signalled"
    assert len(spawned) == 1
    assert spawned[0].poll() is not None
    assert spawned[0].returncode == 130


def test_run_live_subprocess_timeout_forced_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves _run_live_subprocess kills process when grace period expires."""
    from tests.ipc.test_solidedge_live import _run_live_subprocess

    runner_script = """
import signal
import sys
import time

if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, signal.SIG_IGN)
else:
    signal.signal(signal.SIGINT, signal.SIG_IGN)

time.sleep(30)
"""
    spawned, events = _install_recording_popen(monkeypatch)

    with pytest.raises(AssertionError, match=r"Live process timed out after 0\.5 seconds"):
        _run_live_subprocess(
            [sys.executable, "-c", runner_script],
            input_bytes=b"",
            env=os.environ.copy(),
            cwd=tmp_path,
            timeout=0.5,
            grace_seconds=0.2,
        )

    assert events == ["signal", "kill"]
    assert len(spawned) == 1
    assert spawned[0].poll() is not None


def test_run_live_subprocess_signal_failure_guarantees_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves _run_live_subprocess guarantees kill even if send_signal raises."""
    from tests.ipc.test_solidedge_live import _run_live_subprocess

    runner_script = """
import time
time.sleep(30)
"""
    spawned, events = _install_recording_popen(monkeypatch, signal_failure=True)

    with pytest.raises(AssertionError, match=r"Live process timed out after 0\.2 seconds"):
        _run_live_subprocess(
            [sys.executable, "-c", runner_script],
            input_bytes=b"",
            env=os.environ.copy(),
            cwd=tmp_path,
            timeout=0.2,
            grace_seconds=0.2,
        )

    assert events == ["signal", "kill"]
    assert len(spawned) == 1
    assert spawned[0].poll() is not None
