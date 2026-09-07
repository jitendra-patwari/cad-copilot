"""Tests for Windows source-run launcher generate.cmd and cad-copilot-generate console script."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ipc.progress import FATAL_DIAGNOSTIC_MESSAGE


def test_console_script_unexpected_argv_exits_1_with_fatal_diagnostic() -> None:
    """Proves installed console script cad-copilot-generate rejects arguments with code 1 and fatal diagnostic."""
    venv_scripts = Path(sys.executable).parent
    exe_path = venv_scripts / "cad-copilot-generate.exe"
    if not exe_path.exists():
        exe_path = venv_scripts / "cad-copilot-generate"

    assert exe_path.exists(), f"Console script {exe_path} not found"

    proc = subprocess.run(
        [str(exe_path), "--unsupported-arg"],
        capture_output=True,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr

    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": FATAL_DIAGNOSTIC_MESSAGE,
    }


def test_console_script_execution_via_stdin(tmp_path: Path) -> None:
    """Proves installed console script executes end-to-end processing request on stdin."""
    venv_scripts = Path(sys.executable).parent
    exe_path = venv_scripts / "cad-copilot-generate.exe"
    if not exe_path.exists():
        exe_path = venv_scripts / "cad-copilot-generate"

    assert exe_path.exists(), f"Console script {exe_path} not found"

    # Input request with unconfigured output root returns failed/OUTPUT_PATH_NOT_ALLOWED
    input_payload = json.dumps(
        {
            "contract_version": "1.0",
            "request_id": "req-script-01",
            "kind": "example_plan",
            "unit": "mm",
            "example_id": "spur_gear",
        }
    ).encode("utf-8")

    env = os.environ.copy()
    env.pop("CAD_OUTPUT_ROOT", None)

    proc = subprocess.run(
        [str(exe_path)],
        input=input_payload,
        capture_output=True,
        env=env,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0
    assert proc.stdout.endswith(b"\n")
    assert b"Traceback" not in proc.stderr

    res = json.loads(proc.stdout.decode("utf-8"))
    assert res["status"] == "failed"
    assert res["request_id"] == "req-script-01"
    assert res["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"


def test_cmd_launcher_unexpected_argv_exits_1_with_fatal_diagnostic() -> None:
    """Proves generate.cmd rejects arguments with code 1, empty stdout, and fatal diagnostic."""
    if sys.platform != "win32":
        return

    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "generate.cmd"
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
        "message": FATAL_DIAGNOSTIC_MESSAGE,
    }


def test_cmd_launcher_empty_string_argv_exits_1_with_fatal_diagnostic() -> None:
    """Proves generate.cmd rejects empty-string argument with code 1, empty stdout, and fatal diagnostic."""
    if sys.platform != "win32":
        return

    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "generate.cmd"
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
        "message": FATAL_DIAGNOSTIC_MESSAGE,
    }


def test_cmd_launcher_execution_from_outside_working_directory(tmp_path: Path) -> None:
    """Proves generate.cmd succeeds when invoked from an arbitrary working directory outside repo."""
    if sys.platform != "win32":
        return

    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "generate.cmd"
    assert cmd_path.exists(), f"Launcher {cmd_path} not found"

    input_payload = json.dumps(
        {
            "contract_version": "1.0",
            "request_id": "req-cmd-01",
            "kind": "example_plan",
            "unit": "mm",
            "example_id": "spur_gear",
        }
    ).encode("utf-8")

    env = os.environ.copy()
    env.pop("CAD_OUTPUT_ROOT", None)
    # Ensure PYTHONPATH is unset to verify editable installation discovery
    env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [str(cmd_path)],
        input=input_payload,
        capture_output=True,
        env=env,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0
    assert proc.stdout.endswith(b"\n")
    assert b"Traceback" not in proc.stderr

    res = json.loads(proc.stdout.decode("utf-8"))
    assert res["status"] == "failed"
    assert res["request_id"] == "req-cmd-01"
    assert res["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"


def test_cmd_launcher_missing_interpreter_exits_1_with_fatal_diagnostic(tmp_path: Path) -> None:
    """Proves generate.cmd with missing Python interpreter emits fatal diagnostic without traceback."""
    if sys.platform != "win32":
        return

    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "generate.cmd"
    content = cmd_path.read_text(encoding="utf-8")

    # Simulate missing python executable by replacing interpreter path
    tampered_cmd = tmp_path / "generate_missing_py.cmd"
    tampered_content = content.replace(
        r'set "PYTHON_EXE=%~dp0..\..\.venv\Scripts\python.exe"',
        r'set "PYTHON_EXE=%~dp0..\nonexistent\python.exe"',
    )
    tampered_cmd.write_text(tampered_content, encoding="utf-8")

    proc = subprocess.run(
        [str(tampered_cmd)],
        capture_output=True,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr

    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": FATAL_DIAGNOSTIC_MESSAGE,
    }


def test_cmd_launcher_missing_distribution_metadata_exits_1_with_fatal_diagnostic(tmp_path: Path) -> None:
    """Proves generate.cmd preflight failure on uninstalled package emits fatal diagnostic."""
    if sys.platform != "win32":
        return

    cmd_path = Path(__file__).resolve().parents[2] / "scripts" / "generate.cmd"
    content = cmd_path.read_text(encoding="utf-8")

    # Rewrite PYTHON_EXE to the real test interpreter so interpreter check passes
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

    tampered_cmd = tmp_path / "generate_missing_meta.cmd"
    tampered_cmd.write_text(tampered_content, encoding="utf-8")

    proc = subprocess.run(
        [str(tampered_cmd)],
        capture_output=True,
    )

    assert proc.returncode == 1
    assert proc.stdout == b""
    assert b"Traceback" not in proc.stderr

    diag = json.loads(proc.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": FATAL_DIAGNOSTIC_MESSAGE,
    }
