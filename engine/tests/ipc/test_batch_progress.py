"""Tests for batch IPC progress encoding, fatal diagnostics, and stderr emission."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from batch.execution import BatchProgressUpdate
from ipc.batch_progress import (
    BATCH_FATAL_DIAGNOSTIC_BYTES,
    BATCH_FATAL_DIAGNOSTIC_MESSAGE,
    MAX_BATCH_PROGRESS_PAYLOAD_BYTES,
    emit_batch_fatal_diagnostic,
    emit_batch_progress,
    format_batch_fatal_diagnostic,
    format_batch_progress,
)
from tests.ipc.support import drain_pipe, make_pipes

# ---------------------------------------------------------------------------
# 1. Progress Event Formatting
# ---------------------------------------------------------------------------


def test_format_batch_progress_minimal() -> None:
    """Proves format_batch_progress formats minimal update omitting None fields."""
    update = BatchProgressUpdate(
        request_id="req-batch-001",
        phase="batch_started",
        total_files=5,
        completed_files=0,
    )
    raw = format_batch_progress(update)
    assert raw.endswith(b"\n")
    assert all(b < 128 for b in raw)

    parsed: dict[str, Any] = json.loads(raw.decode("ascii"))
    assert parsed == {
        "type": "progress",
        "request_id": "req-batch-001",
        "phase": "batch_started",
        "total_files": 5,
        "completed_files": 0,
    }
    assert "current_file" not in parsed
    assert "current_format" not in parsed
    assert "file_status" not in parsed


def test_format_batch_progress_all_fields() -> None:
    """Proves format_batch_progress includes optional fields when present."""
    update = BatchProgressUpdate(
        request_id="req-batch-002",
        phase="file_finished",
        total_files=10,
        completed_files=2,
        current_file="part1.par",
        file_status="accepted",
    )
    raw = format_batch_progress(update)
    assert raw.endswith(b"\n")

    parsed: dict[str, Any] = json.loads(raw.decode("ascii"))
    assert parsed == {
        "type": "progress",
        "request_id": "req-batch-002",
        "phase": "file_finished",
        "total_files": 10,
        "completed_files": 2,
        "current_file": "part1.par",
        "file_status": "accepted",
    }


def test_format_batch_progress_format_phase() -> None:
    """Proves format_batch_progress handles format_started with current_format."""
    update = BatchProgressUpdate(
        request_id="req-batch-003",
        phase="format_started",
        total_files=5,
        completed_files=1,
        current_file="part1.par",
        current_format="step",
    )
    raw = format_batch_progress(update)
    assert raw.endswith(b"\n")
    parsed: dict[str, Any] = json.loads(raw.decode("ascii"))
    assert parsed["phase"] == "format_started"
    assert parsed["current_format"] == "step"


@pytest.mark.parametrize(
    ("phase", "current_file", "current_format", "file_status", "total", "completed"),
    [
        ("batch_started", None, None, None, 1, 0),
        ("file_started", "part1.par", None, None, 1, 0),
        ("format_started", "part1.par", "step", None, 1, 0),
        ("format_finished", "part1.par", "step", None, 1, 0),
        ("file_finished", "part1.par", None, "accepted", 1, 1),
        ("batch_finished", None, None, None, 1, 1),
    ],
)
def test_format_batch_progress_all_valid_phases(
    phase: str,
    current_file: str | None,
    current_format: str | None,
    file_status: str | None,
    total: int,
    completed: int,
) -> None:
    """Proves all canonical batch phases format successfully with their valid fields."""
    update = BatchProgressUpdate(
        request_id="req-phases",
        phase=phase,  # type: ignore[arg-type]
        total_files=total,
        completed_files=completed,
        current_file=current_file,
        current_format=current_format,
        file_status=file_status,  # type: ignore[arg-type]
    )
    raw = format_batch_progress(update)
    assert raw.endswith(b"\n")
    parsed = json.loads(raw.decode("ascii"))
    assert parsed["phase"] == phase


def test_format_batch_progress_type_check() -> None:
    """Proves non-BatchProgressUpdate raises TypeError."""
    with pytest.raises(TypeError, match="Expected BatchProgressUpdate"):
        format_batch_progress({"invalid": "type"})  # type: ignore[arg-type]


def test_format_batch_progress_payload_limit_enforced() -> None:
    """Proves oversized progress payloads exceeding 4 KiB raise ValueError."""
    update = BatchProgressUpdate(
        request_id="req-huge",
        phase="file_started",
        total_files=1,
        completed_files=0,
        current_file="part1.par",
    )
    # Bypass post_init to inject an oversized field and verify format_batch_progress check
    object.__setattr__(update, "current_file", "a" * (MAX_BATCH_PROGRESS_PAYLOAD_BYTES + 10))
    with pytest.raises(ValueError, match="exceeds limit"):
        format_batch_progress(update)


# ---------------------------------------------------------------------------
# 2. Fatal Diagnostic Formatting
# ---------------------------------------------------------------------------


def test_format_batch_fatal_diagnostic() -> None:
    """Proves fixed fatal diagnostic byte sequence is compact, valid ASCII JSON + LF."""
    raw = format_batch_fatal_diagnostic()
    assert raw == BATCH_FATAL_DIAGNOSTIC_BYTES
    assert raw.endswith(b"\n")
    assert all(b < 128 for b in raw)

    parsed: dict[str, Any] = json.loads(raw.decode("ascii"))
    assert parsed == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": BATCH_FATAL_DIAGNOSTIC_MESSAGE,
    }


# ---------------------------------------------------------------------------
# 3. Best-Effort Emitters
# ---------------------------------------------------------------------------


def test_emit_batch_progress_to_pipe() -> None:
    """Proves emit_batch_progress successfully writes JSONL line to file descriptor."""
    out_r, out_w, _, _ = make_pipes()
    try:
        update = BatchProgressUpdate(
            request_id="req-emit-1",
            phase="batch_started",
            total_files=3,
            completed_files=0,
        )
        emit_batch_progress(out_w, update)
        os.close(out_w)

        received = drain_pipe(out_r)
        assert received.endswith(b"\n")
        parsed = json.loads(received.decode("ascii"))
        assert parsed["request_id"] == "req-emit-1"
    finally:
        os.close(out_r)


def test_emit_batch_progress_suppresses_broken_pipe() -> None:
    """Proves emit_batch_progress suppresses OSError if write end is closed/broken."""
    out_r, out_w, _, _ = make_pipes()
    os.close(out_r)
    os.close(out_w)

    update = BatchProgressUpdate(
        request_id="req-broken",
        phase="batch_started",
        total_files=1,
        completed_files=0,
    )
    # Must not raise despite invalid file descriptor
    emit_batch_progress(out_w, update)


def test_emit_batch_fatal_diagnostic_to_pipe() -> None:
    """Proves emit_batch_fatal_diagnostic successfully writes diagnostic line to file descriptor."""
    out_r, out_w, _, _ = make_pipes()
    try:
        emit_batch_fatal_diagnostic(out_w)
        os.close(out_w)

        received = drain_pipe(out_r)
        assert received == BATCH_FATAL_DIAGNOSTIC_BYTES
    finally:
        os.close(out_r)


def test_emit_batch_fatal_diagnostic_suppresses_broken_pipe() -> None:
    """Proves emit_batch_fatal_diagnostic suppresses OSError if write end is closed/broken."""
    out_r, out_w, _, _ = make_pipes()
    os.close(out_r)
    os.close(out_w)

    # Must not raise
    emit_batch_fatal_diagnostic(out_w)
