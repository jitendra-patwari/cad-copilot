"""Tests for Windows source-run launcher batch.cmd and cad-copilot-batch console script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ipc.batch_progress import BATCH_FATAL_DIAGNOSTIC_BYTES, BATCH_FATAL_DIAGNOSTIC_MESSAGE


def test_console_script_unexpected_argv_exits_1_with_fatal_diagnostic() -> None:
    """Proves installed console script cad-copilot-batch rejects arguments with code 1 and fatal diagnostic."""
    venv_scripts = Path(sys.executable).parent
    exe_path = venv_scripts / "cad-copilot-batch.exe"
    if not exe_path.exists():
        exe_path = venv_scripts / "cad-copilot-batch"

    assert exe_path.exists(), f"Console script {exe_path} not found"

    proc = subprocess.run(
        [str(exe_path), "--unsupported-arg"],
        capture_output=True,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr
    assert proc.stderr == BATCH_FATAL_DIAGNOSTIC_BYTES

    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": BATCH_FATAL_DIAGNOSTIC_MESSAGE,
    }


def test_console_script_execution_via_stdin_from_outside_cwd(tmp_path: Path) -> None:
    """Proves installed console script executes end-to-end processing request from outside repo CWD."""
    venv_scripts = Path(sys.executable).parent
    exe_path = venv_scripts / "cad-copilot-batch.exe"
    if not exe_path.exists():
        exe_path = venv_scripts / "cad-copilot-batch"

    assert exe_path.exists(), f"Console script {exe_path} not found"

    # Minimal payload that fails schema validation emits schema-valid INVALID_SCHEMA rejection and exits 0
    input_payload = json.dumps(
        {
            "contract_version": "1.0",
            "request_id": "req-script-01",
            "kind": "batch_operation",
            # Missing operation, input, output_root
        }
    ).encode("utf-8")

    proc = subprocess.run(
        [str(exe_path)],
        input=input_payload,
        capture_output=True,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0
    assert proc.stdout.endswith(b"\n")
    assert b"Traceback" not in proc.stderr

    res = json.loads(proc.stdout.decode("utf-8"))
    assert res["status"] == "rejected"
    assert res["request_id"] == "req-script-01"
    assert res["errors"][0]["code"] == "INVALID_SCHEMA"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script")
def test_cmd_launcher_unexpected_argv_exits_1_with_fatal_diagnostic() -> None:
    """Proves batch.cmd rejects arguments with code 1, empty stdout, and fatal diagnostic."""
    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "batch.cmd"
    assert cmd_path.exists(), f"Launcher {cmd_path} not found"

    proc = subprocess.run(
        [str(cmd_path), "--unsupported-arg"],
        capture_output=True,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr

    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": BATCH_FATAL_DIAGNOSTIC_MESSAGE,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script")
def test_cmd_launcher_empty_string_arg_exits_1() -> None:
    """Proves batch.cmd rejects empty-string argument with code 1, empty stdout, and fatal diagnostic."""
    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "batch.cmd"
    assert cmd_path.exists(), f"Launcher {cmd_path} not found"

    proc = subprocess.run(
        [str(cmd_path), ""],
        capture_output=True,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr

    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": BATCH_FATAL_DIAGNOSTIC_MESSAGE,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script")
def test_cmd_launcher_succeeds_from_outside_cwd(tmp_path: Path) -> None:
    """Proves batch.cmd succeeds when invoked from an arbitrary working directory outside repo."""
    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "batch.cmd"
    assert cmd_path.exists(), f"Launcher {cmd_path} not found"

    input_payload = json.dumps(
        {
            "contract_version": "1.0",
            "request_id": "req-cmd-01",
            "kind": "batch_operation",
        }
    ).encode("utf-8")

    proc = subprocess.run(
        [str(cmd_path)],
        input=input_payload,
        capture_output=True,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0
    assert proc.stdout.endswith(b"\n")
    assert b"Traceback" not in proc.stderr

    res = json.loads(proc.stdout.decode("utf-8"))
    assert res["status"] == "rejected"
    assert res["request_id"] == "req-cmd-01"
    assert res["errors"][0]["code"] == "INVALID_SCHEMA"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script")
def test_cmd_launcher_missing_python_emits_fatal_diagnostic(tmp_path: Path) -> None:
    """Proves batch.cmd with missing Python interpreter emits fatal diagnostic without traceback."""
    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "batch.cmd"
    assert cmd_path.exists(), f"Launcher {cmd_path} not found"

    script_content = cmd_path.read_text(encoding="utf-8")
    broken_content = script_content.replace(
        'set "PYTHON_EXE=%~dp0..\\..\\.venv\\Scripts\\python.exe"',
        'set "PYTHON_EXE=%~dp0..\\..\\.venv\\Scripts\\nonexistent_python.exe"',
    )

    test_cmd = tmp_path / "batch_broken.cmd"
    test_cmd.write_text(broken_content, encoding="utf-8")

    proc = subprocess.run(
        [str(test_cmd)],
        capture_output=True,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr

    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": BATCH_FATAL_DIAGNOSTIC_MESSAGE,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script")
def test_cmd_launcher_preflight_failure_emits_fatal_diagnostic(tmp_path: Path) -> None:
    """Proves batch.cmd preflight failure emits fatal diagnostic."""
    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "batch.cmd"
    assert cmd_path.exists(), f"Launcher {cmd_path} not found"

    content = cmd_path.read_text(encoding="utf-8")

    # Rewrite PYTHON_EXE to the active test interpreter so interpreter-existence check passes
    python_exe_line = f'set "PYTHON_EXE={sys.executable}"'
    orig_exe_line = r'set "PYTHON_EXE=%~dp0..\..\.venv\Scripts\python.exe"'
    orig_meta_probe = "importlib.metadata.version('cad-copilot')"
    tampered_meta_probe = "importlib.metadata.version('nonexistent-cad-copilot-package')"

    assert orig_exe_line in content
    assert orig_meta_probe in content

    tampered_content = content.replace(orig_exe_line, python_exe_line).replace(orig_meta_probe, tampered_meta_probe)
    assert python_exe_line in tampered_content
    assert tampered_meta_probe in tampered_content
    assert orig_exe_line not in tampered_content
    assert orig_meta_probe not in tampered_content

    test_cmd = tmp_path / "batch_preflight_fail.cmd"
    test_cmd.write_text(tampered_content, encoding="utf-8")

    proc = subprocess.run(
        [str(test_cmd)],
        capture_output=True,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr

    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": BATCH_FATAL_DIAGNOSTIC_MESSAGE,
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script")
def test_cmd_launcher_child_nonzero_exit_code_propagated(tmp_path: Path) -> None:
    """Proves batch.cmd propagates a nonzero exit code from the child process without fatal fallback."""
    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "batch.cmd"
    assert cmd_path.exists(), f"Launcher {cmd_path} not found"

    content = cmd_path.read_text(encoding="utf-8")
    python_exe_line = f'set "PYTHON_EXE={sys.executable}"'
    orig_exe_line = r'set "PYTHON_EXE=%~dp0..\..\.venv\Scripts\python.exe"'
    orig_child_exec = r'"%PYTHON_EXE%" -m ipc.batch_stdio %*'
    tampered_child_exec = r'"%PYTHON_EXE%" -c "import sys; sys.exit(42)" %*'

    assert orig_exe_line in content
    assert orig_child_exec in content

    tampered_content = content.replace(orig_exe_line, python_exe_line).replace(orig_child_exec, tampered_child_exec)
    assert tampered_child_exec in tampered_content
    assert orig_child_exec not in tampered_content

    test_cmd = tmp_path / "batch_exit_prop.cmd"
    test_cmd.write_text(tampered_content, encoding="utf-8")

    proc = subprocess.run(
        [str(test_cmd)],
        capture_output=True,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 42
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr
    assert b"diagnostic" not in proc.stderr
