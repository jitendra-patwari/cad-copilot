"""Opt-in live integration tests for Milestone 5.6 batch stdio IPC against Solid Edge 2026.

These tests execute ONLY when running with `pytest -m com` and `CAD_COPILOT_RUN_LIVE_COM=1`
on Windows with a licensed Siemens Solid Edge installation.

Tests:
- B-LIVE-M56-01: Native 3D model export (.par, .psm, .asm to STEP, STL, Parasolid),
  durable manifest assembly, exact streaming stdout/stderr framing and complete progress event ordering,
  exact response-manifest parity, and source immutability.
- B-LIVE-M56-02: 2D drawing publication (.dft to PDF and DXF), in-memory view refresh,
  close-without-save source immutability, exact progress event ordering, and response-manifest parity.
- B-LIVE-M56-03: Pre-existing manifest sentinel collision, normal artifact completion before publication,
  graceful terminal failure, clean diagnostic sanitization, and subsequent healthy batch execution.
- B-LIVE-M56-04: Multi-file cooperative CTRL_BREAK cancellation, bounded synchronization, guaranteed child cleanup,
  clean teardown, exact cancelled manifest accounting and parity, partial artifact non-leakage, and zero orphan processes.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, Final

import pytest

from batch.execution import BatchProgressUpdate
from batch.format_validation import validate_batch_output
from batch.models import BatchManifest
from batch.parsing import parse_batch_manifest, parse_batch_response
from batch.schemas import get_batch_manifest_validator, get_batch_response_validator
from batch.source_integrity import (
    SourceSnapshot,
    capture_source_snapshot,
    verify_snapshot_equality,
)
from ipc.batch_progress import format_batch_progress

pytestmark = [pytest.mark.com]

LIVE_PROCESS_TIMEOUT_SECONDS: Final[float] = 180.0
GRACEFUL_TEARDOWN_SECONDS: Final[float] = 30.0

FREE_TEXT_CREDENTIAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|bearer\s+[a-z0-9_\-\.]+)"
)
LOCAL_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?i)[a-z]:\\[^\"'\s<>|]+|/[^\"'\s<>|]+")

PHASE_EXACT_KEYS: Final[dict[str, frozenset[str]]] = {
    "batch_started": frozenset({"type", "request_id", "phase", "total_files", "completed_files"}),
    "file_started": frozenset({"type", "request_id", "phase", "total_files", "completed_files", "current_file"}),
    "format_started": frozenset(
        {
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
            "current_file",
            "current_format",
        }
    ),
    "format_finished": frozenset(
        {
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
            "current_file",
            "current_format",
        }
    ),
    "file_finished": frozenset(
        {
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
            "current_file",
            "file_status",
        }
    ),
    "batch_finished": frozenset({"type", "request_id", "phase", "total_files", "completed_files"}),
}


# ---------------------------------------------------------------------------
# Process Inventory & Verification Helpers
# ---------------------------------------------------------------------------


def _get_edge_pids() -> list[int]:
    """Return list of PIDs for running Edge.exe processes, failing closed on query error."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Edge.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to query process inventory via tasklist: {exc}") from exc

    if out.returncode != 0:
        raise RuntimeError(f"tasklist command failed with returncode {out.returncode}: {out.stderr.strip()}")

    pids: list[int] = []
    for line in out.stdout.strip().splitlines():
        if "Edge.exe" in line:
            parts = line.split(",")
            if len(parts) >= 2:
                with contextlib.suppress(ValueError):
                    pids.append(int(parts[1].strip(' "')))
    return pids


def _wait_for_edge_exit(timeout: float = 10.0) -> bool:
    """Wait for all Edge.exe processes to exit naturally within bounded timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if not _get_edge_pids():
            return True
        time.sleep(0.5)
    return not bool(_get_edge_pids())


@pytest.fixture(scope="module", autouse=True)
def _verify_solidedge_environment() -> Generator[None]:
    """Enforce opt-in, Windows platform, and zero-baseline Edge.exe precondition."""
    if sys.platform != "win32":
        pytest.skip("Live Solid Edge COM tests require Windows platform")
    if os.environ.get("CAD_COPILOT_RUN_LIVE_COM") != "1":
        pytest.skip("Skipping live Solid Edge COM tests: CAD_COPILOT_RUN_LIVE_COM is not set to '1'")

    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        pytest.skip("pywin32 is not installed in the current Python environment")

    # Refuse to start when baseline is nonzero; never force-kill user sessions
    initial_pids = _get_edge_pids()
    if initial_pids:
        pytest.skip(
            f"Live Solid Edge tests require zero running Edge.exe instances at baseline; found active PIDs: {initial_pids}. "
            "Refusing to attach to or terminate existing sessions."
        )

    yield

    # After entire module, ensure no orphan processes were left behind
    _wait_for_edge_exit(timeout=5.0)
    remaining = _get_edge_pids()
    if remaining:
        pytest.fail(f"Live Solid Edge test suite leaked Edge.exe processes: PIDs {remaining}")


@pytest.fixture
def clean_solidedge_session() -> Generator[None]:
    """Enforce clean baseline before each test and verify natural zero-orphan exit after."""
    existing_pids = _get_edge_pids()
    if existing_pids:
        pytest.fail(
            f"Dirty test baseline: unexpected Edge.exe already running before test execution: PIDs {existing_pids}."
        )

    try:
        yield
    finally:
        # Bounded wait for child process's Solid Edge teardown to complete naturally
        natural_exit = _wait_for_edge_exit(timeout=15.0)
        remaining_pids = _get_edge_pids()
        if not natural_exit or remaining_pids:
            # Gate failed: treat every forced fallback as a failed live gate
            pytest.fail(f"Solid Edge process leaked after test execution: PIDs {remaining_pids}")


# ---------------------------------------------------------------------------
# Reusable Stream, Progress, and Parity Helpers
# ---------------------------------------------------------------------------


def _get_batch_cmd() -> Path:
    engine_root = Path(__file__).resolve().parents[2]
    cmd_path = engine_root / "scripts" / "batch.cmd"
    if not cmd_path.is_file():
        pytest.skip(f"batch.cmd not found at {cmd_path}")
    return cmd_path


def _get_installed_entrypoint() -> Path:
    prefix = Path(sys.executable).parent
    exe_name = "cad-copilot-batch.exe" if sys.platform == "win32" else "cad-copilot-batch"
    exe_path = prefix / exe_name
    if not exe_path.is_file():
        pytest.skip(f"Installed entrypoint not found at {exe_path}")
    return exe_path


def _copy_fixtures(dest_dir: Path) -> Path:
    fixtures_dir = Path(__file__).resolve().parents[1] / "live_fixtures"
    if not fixtures_dir.is_dir():
        pytest.skip(f"Live fixtures directory not found: {fixtures_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in fixtures_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, dest_dir / f.name)
    return fixtures_dir


def _start_stream_reader(stream: Any) -> tuple[queue.Queue[bytes], threading.Thread]:
    """Start a daemon thread to read lines from a binary stream into a queue."""
    q: queue.Queue[bytes] = queue.Queue()

    def _reader() -> None:
        try:
            for line in iter(stream.readline, b""):
                q.put(line)
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    return q, thread


def _parse_strict_progress_line(line_bytes: bytes, expected_req_id: str) -> BatchProgressUpdate:
    """Strictly decode and parse a progress line into a validated BatchProgressUpdate."""
    assert line_bytes.endswith(b"\n"), f"Progress line missing trailing LF: {line_bytes!r}"
    assert not line_bytes.endswith(b"\r\n"), f"Progress line has forbidden CRLF termination: {line_bytes!r}"

    try:
        line_str = line_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"Non-ASCII bytes in progress line: {line_bytes!r}") from exc

    stripped_str = line_str[:-1]
    assert stripped_str.strip(), "Received empty or whitespace-only line on stderr"

    try:
        record = json.loads(stripped_str)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Malformed JSON on stderr: {line_str!r}") from exc

    assert isinstance(record, dict), f"Progress record must be a JSON object, got {type(record).__name__}"

    # Reject null field values anywhere in record (all present keys must have concrete values)
    null_keys = [k for k, v in record.items() if v is None]
    assert not null_keys, f"Forbidden null values in progress event for keys {null_keys}: {record}"

    phase = record.get("phase")
    assert phase in PHASE_EXACT_KEYS, f"Unknown or missing progress phase: {phase!r}"
    expected_keys = PHASE_EXACT_KEYS[phase]
    actual_keys = set(record.keys())
    assert actual_keys == expected_keys, (
        f"Progress keys mismatch for phase {phase!r}: expected {expected_keys}, got {actual_keys}"
    )

    assert record["type"] == "progress", f"Expected type 'progress', got {record.get('type')!r}"
    assert record["request_id"] == expected_req_id, (
        f"Mismatched request_id in progress: expected {expected_req_id}, got {record['request_id']}"
    )

    # Privacy scan
    assert not FREE_TEXT_CREDENTIAL_PATTERN.search(stripped_str), (
        f"Credential pattern leaked in progress: {stripped_str}"
    )
    assert not LOCAL_PATH_PATTERN.search(stripped_str), f"Local path leaked in progress: {stripped_str}"

    # Construct and validate BatchProgressUpdate dataclass
    update = BatchProgressUpdate(
        request_id=record["request_id"],
        phase=record["phase"],
        total_files=record["total_files"],
        completed_files=record["completed_files"],
        current_file=record.get("current_file"),
        current_format=record.get("current_format"),
        file_status=record.get("file_status"),
    )

    # Canonical byte comparison: compare raw bytes against canonical format_batch_progress(update)
    canonical = format_batch_progress(update)
    assert line_bytes == canonical, (
        f"Raw progress line does not match canonical serialization:\n"
        f"  raw:       {line_bytes!r}\n"
        f"  canonical: {canonical!r}"
    )

    return update


def _verify_exact_progress_stream(
    raw_stderr: bytes,
    expected_updates: list[BatchProgressUpdate],
    expected_req_id: str,
) -> list[BatchProgressUpdate]:
    """Verify exact raw stderr progress framing without discarding blank lines or whitespace.

    Ensures every raw line matches the canonical byte-for-byte serialization of the expected
    BatchProgressUpdate, with LF termination and zero extra or blank lines.
    """
    raw_lines = raw_stderr.splitlines(keepends=True)
    assert len(raw_lines) == len(expected_updates), (
        f"Stderr line count mismatch: expected {len(expected_updates)} lines, got {len(raw_lines)}. "
        f"Raw lines: {raw_lines!r}"
    )

    parsed_updates: list[BatchProgressUpdate] = []
    for idx, (raw_line, expected_update) in enumerate(zip(raw_lines, expected_updates, strict=True)):
        parsed = _parse_strict_progress_line(raw_line, expected_req_id=expected_req_id)
        assert parsed == expected_update, f"Progress update mismatch at index {idx}: {parsed} != {expected_update}"
        parsed_updates.append(parsed)

    return parsed_updates


def _build_expected_progress(
    req_id: str,
    files: tuple[str, ...],
    formats: tuple[str, ...],
) -> list[BatchProgressUpdate]:
    """Construct the exact expected ordered list of BatchProgressUpdate events for a batch."""
    total = len(files)
    updates: list[BatchProgressUpdate] = [
        BatchProgressUpdate(request_id=req_id, phase="batch_started", total_files=total, completed_files=0)
    ]
    for idx, f in enumerate(files):
        updates.append(
            BatchProgressUpdate(
                request_id=req_id,
                phase="file_started",
                total_files=total,
                completed_files=idx,
                current_file=f,
            )
        )
        for fmt in formats:
            updates.append(
                BatchProgressUpdate(
                    request_id=req_id,
                    phase="format_started",
                    total_files=total,
                    completed_files=idx,
                    current_file=f,
                    current_format=fmt,
                )
            )
            updates.append(
                BatchProgressUpdate(
                    request_id=req_id,
                    phase="format_finished",
                    total_files=total,
                    completed_files=idx,
                    current_file=f,
                    current_format=fmt,
                )
            )
        updates.append(
            BatchProgressUpdate(
                request_id=req_id,
                phase="file_finished",
                total_files=total,
                completed_files=idx + 1,
                current_file=f,
                file_status="accepted",
            )
        )
    updates.append(
        BatchProgressUpdate(request_id=req_id, phase="batch_finished", total_files=total, completed_files=total)
    )
    return updates


def _verify_response_manifest_parity(
    resp_payload: dict[str, Any],
    manifest: BatchManifest,
    manifest_file_path: Path,
    output_root: Path,
) -> None:
    """Verify exact semantic parity between canonical response and published manifest."""
    # 1. Parse response semantically via canonical parse_batch_response
    parsed_resp = parse_batch_response(resp_payload)

    # 2. Manifest reference parity
    assert parsed_resp.manifest is not None, "Response must contain manifest reference"
    assert parsed_resp.manifest.path == manifest_file_path.as_posix(), (
        f"Manifest path mismatch: {parsed_resp.manifest.path} != {manifest_file_path.as_posix()}"
    )

    # 3. Request ID and Terminal status
    assert parsed_resp.request_id == manifest.request_id
    assert parsed_resp.status == manifest.status

    # 4. Summary parity across all 6 fields
    resp_sum = parsed_resp.summary
    assert resp_sum is not None, "Response must contain summary"
    man_sum = manifest.summary
    assert resp_sum.total == man_sum.total
    assert resp_sum.accepted == man_sum.accepted
    assert resp_sum.failed == man_sum.failed
    assert resp_sum.cancelled == man_sum.cancelled
    assert resp_sum.unprocessed == man_sum.unprocessed
    assert resp_sum.partial == man_sum.partial

    # 5. Shared terminal accounting lists
    assert parsed_resp.cancelled_files == manifest.cancelled_files
    assert parsed_resp.unprocessed_files == manifest.unprocessed_files

    # 6. Top-level errors and warnings
    assert parsed_resp.errors == manifest.errors
    assert parsed_resp.warnings == manifest.warnings

    # 7. Per-file results parity
    manifest_by_input = {r.input: r for r in manifest.results}
    assert len(parsed_resp.results) == len(manifest.results)

    for r_resp in parsed_resp.results:
        inp = r_resp.input
        assert inp in manifest_by_input, f"Response input '{inp}' missing in manifest"
        r_man = manifest_by_input[inp]

        assert r_resp.status == r_man.status
        assert r_resp.errors == r_man.errors
        assert r_resp.warnings == r_man.warnings
        assert len(r_resp.artifacts) == len(r_man.artifacts)

        # Match each response artifact with manifest artifact record
        man_arts_by_fmt = {a.format: a for a in r_man.artifacts}
        for art_resp in r_resp.artifacts:
            fmt = art_resp.format
            assert fmt in man_arts_by_fmt, f"Artifact format '{fmt}' missing in manifest for '{inp}'"
            art_man = man_arts_by_fmt[fmt]
            expected_abs_path = (output_root / art_man.relative_path).as_posix()
            assert art_resp.path == expected_abs_path, (
                f"Artifact path mismatch for {inp}/{fmt}: {art_resp.path} != {expected_abs_path}"
            )


def _run_batch_subprocess(
    cmd: list[str],
    input_bytes: bytes,
    cwd: Path,
    timeout: float = LIVE_PROCESS_TIMEOUT_SECONDS,
    grace_seconds: float = GRACEFUL_TEARDOWN_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    """Execute batch subprocess with bounded deadlines, graceful signalling, and guaranteed cleanup."""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        creationflags=creationflags,
    )
    try:
        stdout, stderr = proc.communicate(input=input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            # 1. Attempt graceful cancellation signal
            with contextlib.suppress(OSError, ValueError):
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.send_signal(signal.SIGINT)

            # 2. Allow bounded grace period to unwind teardown
            with contextlib.suppress(subprocess.TimeoutExpired):
                stdout, stderr = proc.communicate(timeout=grace_seconds)
        finally:
            # 3. Guaranteed bounded child cleanup: ensure child is never left running
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                    stdout, stderr = proc.communicate(timeout=5.0)

        raise AssertionError(f"Live process timed out after {timeout}s: {cmd[0]}") from None

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_live_m56_01_3d_cli_manifest_and_stdout_stderr(clean_solidedge_session: None, tmp_path: Path) -> None:
    """B-LIVE-M56-01: Native 3D model export (.par, .psm, .asm to STEP, STL, Parasolid)."""
    input_root = tmp_path / "inputs"
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    _copy_fixtures(input_root)

    files_to_test = ("Bed.par", "SE_Transition_RR.psm", "carrier.asm")
    pre_snapshots: dict[str, SourceSnapshot] = {}
    for filename in (*files_to_test, "chead.par", "mtgpin.par", "carrier.cfg"):
        file_path = input_root / filename
        assert file_path.is_file(), f"Fixture {filename} missing"
        pre_snapshots[filename] = capture_source_snapshot(file_path)

    req_id = "req-live-m56-01"
    request_payload: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": req_id,
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step", "stl", "parasolid"],
        },
        "input": {
            "root": str(input_root).replace("\\", "/"),
            "files": list(files_to_test),
        },
        "output_root": str(output_root).replace("\\", "/"),
    }
    input_bytes = json.dumps(request_payload).encode("utf-8")

    cmd = [str(_get_batch_cmd())]
    proc = _run_batch_subprocess(cmd, input_bytes, cwd=tmp_path)

    assert proc.returncode == 0
    assert proc.stdout.endswith(b"\n"), "Stdout must end with LF"
    assert b"\n" not in proc.stdout[:-1], "Stdout must be exactly one response line"
    assert b"Traceback" not in proc.stderr

    resp = json.loads(proc.stdout.decode("utf-8"))
    get_batch_response_validator().validate(resp)
    assert resp["status"] == "completed"
    assert resp["request_id"] == req_id
    assert resp["summary"] == {
        "total": 3,
        "accepted": 3,
        "failed": 0,
        "cancelled": 0,
        "unprocessed": 0,
        "partial": 0,
    }

    # Verify every response path
    assert len(resp["results"]) == 3
    for r in resp["results"]:
        assert r["input"] in files_to_test
        assert r["status"] == "accepted"
        assert len(r["artifacts"]) == 3
        for art in r["artifacts"]:
            art_path = Path(art["path"])
            assert art_path.is_absolute(), f"Response artifact path must be absolute: {art['path']}"
            assert art_path.is_file(), f"Response artifact missing on disk: {art_path}"
            assert art_path.stat().st_size > 0

    # Parse and verify complete ordered progress events on stderr
    expected_updates = _build_expected_progress(req_id, files_to_test, ("step", "stl", "parasolid"))
    _verify_exact_progress_stream(proc.stderr, expected_updates, expected_req_id=req_id)

    # Manifest verification
    manifest_path = output_root / f"{req_id}.batch_manifest.json"
    assert manifest_path.is_file(), f"Manifest missing at {manifest_path}"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    get_batch_manifest_validator().validate(manifest_data)
    manifest = parse_batch_manifest(manifest_data)
    assert manifest.status == "completed"
    assert manifest.request_id == req_id
    assert manifest.summary.total == 3
    assert manifest.summary.accepted == 3
    assert manifest.summary.failed == 0

    # Verify response-manifest parity across all terminal fields
    _verify_response_manifest_parity(resp, manifest, manifest_path, output_root)

    # Verify 9 artifacts generated (3 files * 3 formats) + 1 manifest = 10 files in output_root
    output_files = [p for p in output_root.iterdir() if p.is_file()]
    assert len(output_files) == 10, f"Expected 10 files in output root, found {len(output_files)}"

    for f_res in manifest.results:
        for art in f_res.artifacts:
            assert not Path(art.relative_path).is_absolute()
            assert "\\" not in art.relative_path
            art_file = output_root / art.relative_path
            assert art_file.is_file()
            assert art_file.stat().st_size == art.size_bytes
            assert art.size_bytes > 0
            assert capture_source_snapshot(art_file).sha256 == art.sha256
            validate_batch_output(art.format, art_file)

    # Zero temporary staging files
    temp_files = [p for p in output_root.glob("*.tmp*")]
    assert len(temp_files) == 0, f"Leaked temp files: {temp_files}"

    # Source immutability
    for filename, pre_snap in pre_snapshots.items():
        post_snap = capture_source_snapshot(input_root / filename)
        verify_snapshot_equality(pre_snap, post_snap)


def test_live_m56_02_drawing_cli(clean_solidedge_session: None, tmp_path: Path) -> None:
    """B-LIVE-M56-02: Drawing publication (.dft to PDF and DXF)."""
    input_root = tmp_path / "inputs"
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    _copy_fixtures(input_root)

    pre_dft = capture_source_snapshot(input_root / "Bed.dft")
    pre_par = capture_source_snapshot(input_root / "Bed.par")

    req_id = "req-live-m56-02"
    request_payload: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": req_id,
        "kind": "batch_operation",
        "operation": {
            "type": "publish_drawing",
            "formats": ["pdf", "dxf"],
        },
        "input": {
            "root": str(input_root).replace("\\", "/"),
            "files": ["Bed.dft"],
        },
        "output_root": str(output_root).replace("\\", "/"),
    }
    input_bytes = json.dumps(request_payload).encode("utf-8")

    cmd = [str(_get_batch_cmd())]
    proc = _run_batch_subprocess(cmd, input_bytes, cwd=tmp_path)

    assert proc.returncode == 0
    assert proc.stdout.endswith(b"\n"), "Stdout must end with LF"
    assert b"\n" not in proc.stdout[:-1], "Stdout must be exactly one response line"
    assert b"Traceback" not in proc.stderr

    resp = json.loads(proc.stdout.decode("utf-8"))
    get_batch_response_validator().validate(resp)
    assert resp["status"] == "completed"
    assert resp["summary"] == {
        "total": 1,
        "accepted": 1,
        "failed": 0,
        "cancelled": 0,
        "unprocessed": 0,
        "partial": 0,
    }

    # Verify every response path
    assert len(resp["results"]) == 1
    assert resp["results"][0]["status"] == "accepted"
    assert len(resp["results"][0]["artifacts"]) == 2
    for art in resp["results"][0]["artifacts"]:
        art_path = Path(art["path"])
        assert art_path.is_absolute()
        assert art_path.is_file()
        assert art_path.stat().st_size > 0

    # Parse and verify complete ordered progress events on stderr
    expected_updates = _build_expected_progress(req_id, ("Bed.dft",), ("pdf", "dxf"))
    _verify_exact_progress_stream(proc.stderr, expected_updates, expected_req_id=req_id)

    # 2 artifacts (PDF, DXF) + 1 manifest = 3 files in output_root
    output_files = [p for p in output_root.iterdir() if p.is_file()]
    assert len(output_files) == 3, f"Expected 3 files in output root, found {len(output_files)}"

    manifest_path = output_root / f"{req_id}.batch_manifest.json"
    assert manifest_path.is_file()
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    get_batch_manifest_validator().validate(manifest_data)
    manifest = parse_batch_manifest(manifest_data)
    assert manifest.status == "completed"
    assert manifest.summary.total == 1
    assert manifest.summary.accepted == 1

    # Verify response-manifest parity across all terminal fields
    _verify_response_manifest_parity(resp, manifest, manifest_path, output_root)

    for f_res in manifest.results:
        for art in f_res.artifacts:
            assert not Path(art.relative_path).is_absolute()
            assert "\\" not in art.relative_path
            art_file = output_root / art.relative_path
            assert art_file.is_file()
            assert art_file.stat().st_size == art.size_bytes
            assert art.size_bytes > 0
            assert capture_source_snapshot(art_file).sha256 == art.sha256
            validate_batch_output(art.format, art_file)

    # Zero temporary staging files
    temp_files = [p for p in output_root.glob("*.tmp*")]
    assert len(temp_files) == 0, f"Leaked temp files: {temp_files}"

    # Source immutability
    verify_snapshot_equality(pre_dft, capture_source_snapshot(input_root / "Bed.dft"))
    verify_snapshot_equality(pre_par, capture_source_snapshot(input_root / "Bed.par"))


def test_live_m56_03_isolation_and_manifest_collision(clean_solidedge_session: None, tmp_path: Path) -> None:
    """B-LIVE-M56-03: Pre-existing manifest sentinel collision, failure handling, and retry."""
    input_root = tmp_path / "inputs"
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    _copy_fixtures(input_root)

    req_id = "req-live-m56-03"
    sentinel_path = output_root / f"{req_id}.batch_manifest.json"
    sentinel_content = b'{"sentinel":"pre_existing_data"}'
    sentinel_path.write_bytes(sentinel_content)
    sentinel_snap = capture_source_snapshot(sentinel_path)

    request_payload: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": req_id,
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
        "input": {
            "root": str(input_root).replace("\\", "/"),
            "files": ["Bed.par"],
        },
        "output_root": str(output_root).replace("\\", "/"),
    }
    input_bytes = json.dumps(request_payload).encode("utf-8")

    cmd = [str(_get_batch_cmd())]
    proc = _run_batch_subprocess(cmd, input_bytes, cwd=tmp_path)

    assert proc.returncode == 0
    assert proc.stdout.endswith(b"\n")
    assert b"\n" not in proc.stdout[:-1]
    resp = json.loads(proc.stdout.decode("utf-8"))
    get_batch_response_validator().validate(resp)

    # Progressed failure with MANIFEST_PUBLICATION_FAILED and no manifest reference
    assert resp["status"] == "failed"
    assert resp.get("manifest") is None
    error_codes = [e["code"] for e in resp.get("errors", [])]
    assert "MANIFEST_PUBLICATION_FAILED" in error_codes
    assert resp["summary"]["total"] == 1
    assert resp["summary"]["accepted"] == 1

    # Prove normal artifact completed before publication failed
    normal_art_path = output_root / "Bed.step"
    assert normal_art_path.is_file(), (
        f"Normal artifact Bed.step must complete before publication failure: {normal_art_path}"
    )
    assert normal_art_path.stat().st_size > 0
    validate_batch_output("step", normal_art_path)

    # Validate complete progress event records on stderr
    expected_updates = _build_expected_progress(req_id, ("Bed.par",), ("step",))
    _verify_exact_progress_stream(proc.stderr, expected_updates, expected_req_id=req_id)

    # Diagnostic sanitization: ensure no paths or secrets leak
    for err in resp.get("errors", []):
        assert not FREE_TEXT_CREDENTIAL_PATTERN.search(err["message"])
        assert not LOCAL_PATH_PATTERN.search(err["message"])

    # Sentinel must remain completely unchanged
    verify_snapshot_equality(sentinel_snap, capture_source_snapshot(sentinel_path))

    # Zero temporary staging files remaining
    temp_candidates = [p for p in output_root.glob("*.tmp*")]
    assert len(temp_candidates) == 0, f"Temporary files leaked: {temp_candidates}"

    # Subsequent healthy request with distinct request_id and distinct file succeeds in the same output root
    healthy_req_id = "req-live-m56-03-healthy"
    healthy_payload: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": healthy_req_id,
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
        "input": {
            "root": str(input_root).replace("\\", "/"),
            "files": ["SE_Transition_RR.psm"],
        },
        "output_root": str(output_root).replace("\\", "/"),
    }
    healthy_bytes = json.dumps(healthy_payload).encode("utf-8")
    proc_healthy = _run_batch_subprocess(cmd, healthy_bytes, cwd=tmp_path)

    assert proc_healthy.returncode == 0
    assert proc_healthy.stdout.endswith(b"\n")
    assert b"\n" not in proc_healthy.stdout[:-1]
    resp_healthy = json.loads(proc_healthy.stdout.decode("utf-8"))
    get_batch_response_validator().validate(resp_healthy)
    assert resp_healthy["status"] == "completed"
    assert resp_healthy["summary"]["accepted"] == 1
    healthy_manifest_path = output_root / f"{healthy_req_id}.batch_manifest.json"
    assert healthy_manifest_path.is_file()
    healthy_manifest_data = json.loads(healthy_manifest_path.read_text(encoding="utf-8"))
    healthy_manifest = parse_batch_manifest(healthy_manifest_data)
    _verify_response_manifest_parity(resp_healthy, healthy_manifest, healthy_manifest_path, output_root)

    expected_healthy_updates = _build_expected_progress(healthy_req_id, ("SE_Transition_RR.psm",), ("step",))
    _verify_exact_progress_stream(proc_healthy.stderr, expected_healthy_updates, expected_req_id=healthy_req_id)


def test_live_m56_04_cooperative_ctrl_break_cancellation(clean_solidedge_session: None, tmp_path: Path) -> None:
    """B-LIVE-M56-04: Multi-file cooperative CTRL_BREAK cancellation and clean teardown."""
    input_root = tmp_path / "inputs"
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    _copy_fixtures(input_root)

    req_id = "req-live-m56-04"
    files_to_test = ["Bed.par", "SE_Transition_RR.psm", "carrier.asm"]
    pre_snapshots = {f: capture_source_snapshot(input_root / f) for f in files_to_test}

    request_payload: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": req_id,
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step", "stl", "parasolid"],
        },
        "input": {
            "root": str(input_root).replace("\\", "/"),
            "files": files_to_test,
        },
        "output_root": str(output_root).replace("\\", "/"),
    }
    input_bytes = json.dumps(request_payload).encode("utf-8")

    cmd = [str(_get_installed_entrypoint())]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmp_path),
        creationflags=creationflags,
    )
    assert proc.stdin is not None
    proc.stdin.write(input_bytes)
    proc.stdin.close()

    assert proc.stderr is not None
    stderr_queue, stderr_thread = _start_stream_reader(proc.stderr)

    forced_fallback = False
    collected_stderr_updates: list[BatchProgressUpdate] = []
    remaining_stdout = b""

    try:
        # Bounded reader loop waiting for file_started synchronization record on Bed.par
        saw_file_started = False
        deadline = time.time() + 60.0

        while time.time() < deadline:
            remaining_time = max(0.1, deadline - time.time())
            try:
                line_bytes = stderr_queue.get(timeout=min(remaining_time, 1.0))
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue

            update = _parse_strict_progress_line(line_bytes, expected_req_id=req_id)
            collected_stderr_updates.append(update)
            if update.phase == "file_started" and update.current_file == "Bed.par":
                saw_file_started = True
                # Send CTRL_BREAK to initiate cooperative cancellation
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                break

        assert saw_file_started, (
            f"Never received matching file_started synchronization event on stderr within 60s for {req_id}"
        )

        # Bounded wait for process to terminate naturally without communicate()
        try:
            proc.wait(timeout=GRACEFUL_TEARDOWN_SECONDS)
        except subprocess.TimeoutExpired:
            forced_fallback = True
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5.0)
            raise AssertionError("Cancelled process did not terminate within graceful deadline") from None

        # Read stdout separately
        assert proc.stdout is not None
        remaining_stdout = proc.stdout.read()

        # Join the dedicated stderr reader thread and drain remaining queue
        stderr_thread.join(timeout=5.0)
        while not stderr_queue.empty():
            line_bytes = stderr_queue.get_nowait()
            collected_stderr_updates.append(_parse_strict_progress_line(line_bytes, expected_req_id=req_id))

    finally:
        if proc.poll() is None:
            forced_fallback = True
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5.0)

    if forced_fallback:
        pytest.fail("Cancellation test failed: child process had to be forcefully killed")

    assert proc.returncode == 0
    assert remaining_stdout.endswith(b"\n"), "Stdout must end with LF"
    assert b"\n" not in remaining_stdout[:-1], "Stdout must be exactly one response line"

    resp = json.loads(remaining_stdout.decode("utf-8"))
    get_batch_response_validator().validate(resp)

    # Strictly require cancelled terminal status on this healthy gate
    assert resp["status"] == "cancelled", (
        f"Expected cooperative cancellation status 'cancelled', got {resp['status']!r}. Errors: {resp.get('errors')}"
    )
    assert resp["request_id"] == req_id
    assert resp["summary"]["total"] == 3
    assert resp["summary"]["cancelled"] >= 1
    assert "carrier.asm" in resp["cancelled_files"]
    assert resp.get("manifest") is not None

    # Verify published cancelled manifest exists and validates
    manifest_path = output_root / f"{req_id}.batch_manifest.json"
    assert manifest_path.is_file(), f"Cancelled manifest missing at {manifest_path}"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    get_batch_manifest_validator().validate(manifest_data)
    manifest = parse_batch_manifest(manifest_data)
    assert manifest.status == "cancelled"
    assert manifest.request_id == req_id
    assert manifest.summary.total == 3
    assert manifest.summary.cancelled == resp["summary"]["cancelled"]

    # Verify full response-manifest parity on the cancelled result
    _verify_response_manifest_parity(resp, manifest, manifest_path, output_root)

    # Verify collected stderr records satisfy sequence invariants
    assert len(collected_stderr_updates) >= 2
    assert collected_stderr_updates[0].phase == "batch_started"
    assert collected_stderr_updates[-1].phase == "batch_finished"

    # Partial artifact leak verification
    temp_candidates = [p for p in output_root.glob("*.tmp*")]
    assert len(temp_candidates) == 0, f"Temporary files leaked: {temp_candidates}"

    # Verify unstarted file (carrier.asm) has zero artifacts in output root
    for art_file in output_root.glob("carrier*"):
        if not art_file.name.endswith(".batch_manifest.json"):
            pytest.fail(f"Artifact for cancelled carrier.asm leaked into output root: {art_file}")

    # Source immutability
    for filename, pre_snap in pre_snapshots.items():
        post_snap = capture_source_snapshot(input_root / filename)
        verify_snapshot_equality(pre_snap, post_snap)
