"""Tests for strict batch stdio IPC full child process isolation."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from ipc.batch_contracts import MAX_BATCH_REQUEST_BYTES
from ipc.batch_progress import BATCH_FATAL_DIAGNOSTIC_MESSAGE


def _build_test_env() -> dict[str, str]:
    """Construct isolated environment for child processes with src on PYTHONPATH."""
    engine_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(engine_root, "src")
    return env


def test_batch_subprocess_execution_with_all_contamination_sources() -> None:
    """Proves child process isolates stdout/stderr against all Python, C, and Win32 contamination."""
    runner_script = """
import json
import logging
import os
import sys

from batch.execution import BatchProgressUpdate
from ipc.batch_stdio import main

def noisy_handler(req, *, cancellation_check, observer):
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
        assert ret1 != 0

        msg_err = b"SUBPROCESS_WIN32_STDERR_POISON\\n"
        ret2 = kernel32.WriteFile(h_err, msg_err, len(msg_err), ctypes.byref(written), None)
        assert ret2 != 0

    # Emit verified progress
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="batch_started",
        total_files=1,
        completed_files=0,
    ))

    from batch.models import (
        BatchArtifactRecord,
        BatchFileResult,
        BatchManifestReference,
        BatchResponse,
        BatchSummary,
    )
    from batch.projection import project_batch_response

    resp = BatchResponse(
        contract_version="1.0",
        request_id=req.request_id,
        status="completed",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        results=(
            BatchFileResult(
                input="test.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path="E:/out/test.step"),),
                errors=(),
                warnings=(),
            ),
        ),
        manifest=BatchManifestReference(path="E:/out/req-subproc-01.batch_manifest.json"),
        unprocessed_files=(),
        cancelled_files=(),
        errors=(),
        warnings=(),
    )
    return project_batch_response(resp)

sys.exit(main(argv=[], _composition_handler=noisy_handler))
"""
    input_payload = json.dumps(
        {
            "contract_version": "1.0",
            "request_id": "req-subproc-01",
            "kind": "batch_operation",
            "operation": {
                "type": "export_3d",
                "formats": ["step"],
            },
            "input": {
                "root": "E:/models",
                "files": ["test.par"],
            },
            "output_root": "E:/output",
        }
    ).encode("utf-8")

    proc = subprocess.run(
        [sys.executable, "-c", runner_script],
        input=input_payload,
        capture_output=True,
        env=_build_test_env(),
        timeout=15,
    )

    assert proc.returncode == 0, (
        f"Subprocess failed:\nStdout: {proc.stdout.decode('utf-8', errors='replace')}\n"
        f"Stderr: {proc.stderr.decode('utf-8', errors='replace')}"
    )

    # Stdout: byte-exact single line ending in LF with zero contamination
    assert proc.stdout.endswith(b"\n")
    assert b"CONTAMINATION" not in proc.stdout
    assert b"POISON" not in proc.stdout

    resp = json.loads(proc.stdout.decode("utf-8"))
    assert resp["status"] == "completed"
    assert resp["request_id"] == "req-subproc-01"

    # Stderr: exactly 1 JSONL progress line without contamination
    assert b"CONTAMINATION" not in proc.stderr
    assert b"POISON" not in proc.stderr

    stderr_lines = [json.loads(line) for line in proc.stderr.decode("ascii").strip().split("\n")]
    assert len(stderr_lines) == 1
    assert stderr_lines[0]["phase"] == "batch_started"
    assert stderr_lines[0]["request_id"] == "req-subproc-01"


def test_batch_subprocess_module_cli_rejection() -> None:
    """Proves running 'python -m ipc.batch_stdio' directly rejects invalid input and exits 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "ipc.batch_stdio"],
        input=b'{"invalid":"request"}',
        capture_output=True,
        env=_build_test_env(),
        timeout=15,
    )

    assert proc.returncode == 0
    resp = json.loads(proc.stdout.decode("utf-8"))
    assert resp["status"] == "rejected"
    assert resp["errors"][0]["code"] == "INVALID_SCHEMA"


def test_batch_subprocess_module_cli_oversize_rejection() -> None:
    """Proves running 'python -m ipc.batch_stdio' directly rejects oversized payload and exits 0."""
    oversize = b" " * (MAX_BATCH_REQUEST_BYTES + 50)
    proc = subprocess.run(
        [sys.executable, "-m", "ipc.batch_stdio"],
        input=oversize,
        capture_output=True,
        env=_build_test_env(),
        timeout=15,
    )

    assert proc.returncode == 0
    resp = json.loads(proc.stdout.decode("utf-8"))
    assert resp["status"] == "rejected"
    assert resp["errors"][0]["code"] == "PAYLOAD_TOO_LARGE"


def test_batch_subprocess_unexpected_flags_exit_1() -> None:
    """Proves unexpected CLI flags emit fatal diagnostic and exit 1 with empty stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "ipc.batch_stdio", "--unexpected-flag"],
        input=b"",
        capture_output=True,
        env=_build_test_env(),
        timeout=15,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag["type"] == "diagnostic"
    assert diag["phase"] == "fatal"
    assert diag["message"] == BATCH_FATAL_DIAGNOSTIC_MESSAGE
