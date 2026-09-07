"""Tests for curated progress events, fatal diagnostics, and bounded descriptor I/O."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from ipc.progress import (
    FATAL_DIAGNOSTIC_BYTES,
    FATAL_DIAGNOSTIC_MESSAGE,
    FIXED_PROGRESS_BYTES,
    PROGRESS_MESSAGES,
    PROGRESS_PHASES,
    ProgressPhase,
    emit_fatal_diagnostic,
    emit_progress,
    format_fatal_diagnostic,
    format_progress_event,
    write_all,
)

# ---------------------------------------------------------------------------
# 1. Progress Event Formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", PROGRESS_PHASES)
def test_format_progress_event_matches_contract(phase: ProgressPhase) -> None:
    """Proves each progress phase emits compact ASCII JSON with exactly three required keys."""
    raw = format_progress_event(phase)
    assert raw.endswith(b"\n")
    # Must be 7-bit ASCII
    assert all(b < 128 for b in raw)

    parsed: dict[str, Any] = json.loads(raw.decode("ascii"))
    assert set(parsed.keys()) == {"type", "phase", "message"}
    assert parsed["type"] == "progress"
    assert parsed["phase"] == phase
    assert parsed["message"] == PROGRESS_MESSAGES[phase]


def test_format_progress_event_rejects_unknown_phase() -> None:
    """Proves unknown progress phases raise ValueError."""
    with pytest.raises(ValueError, match="Unknown progress phase"):
        format_progress_event("unknown_phase")  # type: ignore[arg-type]


def test_fixed_progress_bytes_are_byte_identical() -> None:
    """Proves precomputed bytes match dynamic formatting."""
    for phase in PROGRESS_PHASES:
        assert format_progress_event(phase) == FIXED_PROGRESS_BYTES[phase]


# ---------------------------------------------------------------------------
# 2. Fatal Diagnostic Formatting
# ---------------------------------------------------------------------------


def test_format_fatal_diagnostic_matches_contract() -> None:
    """Proves fatal diagnostic emits compact ASCII JSON with exact three keys."""
    raw = format_fatal_diagnostic()
    assert raw == FATAL_DIAGNOSTIC_BYTES
    assert raw.endswith(b"\n")
    assert all(b < 128 for b in raw)

    parsed: dict[str, Any] = json.loads(raw.decode("ascii"))
    assert set(parsed.keys()) == {"type", "phase", "message"}
    assert parsed["type"] == "diagnostic"
    assert parsed["phase"] == "fatal"
    assert parsed["message"] == FATAL_DIAGNOSTIC_MESSAGE


# ---------------------------------------------------------------------------
# 3. Bounded Descriptor write_all Helper
# ---------------------------------------------------------------------------


def test_write_all_to_pipe() -> None:
    """Proves write_all pushes full byte buffer to an OS pipe descriptor."""
    r_fd, w_fd = os.pipe()
    try:
        data = b"Hello from write_all descriptor test!\n"
        write_all(w_fd, data)
        read_back = os.read(r_fd, len(data) + 10)
        assert read_back == data
    finally:
        os.close(r_fd)
        os.close(w_fd)


def test_write_all_handles_partial_writes() -> None:
    """Proves write_all loops correctly when os.write performs partial writes."""
    written_chunks: list[bytes] = []

    def mock_partial_write(fd: int, buf: bytes) -> int:
        # Simulate writing only 3 bytes at a time
        chunk = buf[:3]
        written_chunks.append(chunk)
        return len(chunk)

    test_data = b"ABCDEFGHIJKL"  # 12 bytes = 4 chunks of 3
    with patch("os.write", side_effect=mock_partial_write):
        write_all(999, test_data)

    assert len(written_chunks) == 4
    assert b"".join(written_chunks) == test_data


def test_write_all_zero_progress_raises_os_error() -> None:
    """Proves write_all raises OSError if os.write returns 0 without progressing."""
    with patch("os.write", return_value=0), pytest.raises(OSError, match="made no progress"):
        write_all(999, b"test_payload")


def test_write_all_exceeds_max_bytes_raises_value_error() -> None:
    """Proves write_all rejects payloads exceeding the maximum byte safety limit."""
    with pytest.raises(ValueError, match="exceeds maximum permitted limit"):
        write_all(999, b"x" * 100, max_bytes=50)


# ---------------------------------------------------------------------------
# 4. emit_progress and emit_fatal_diagnostic
# ---------------------------------------------------------------------------


def test_emit_progress_to_pipe() -> None:
    """Proves emit_progress writes exact progress bytes to pipe descriptor."""
    r_fd, w_fd = os.pipe()
    try:
        emit_progress(w_fd, "request_received")
        read_back = os.read(r_fd, 512)
        assert read_back == FIXED_PROGRESS_BYTES["request_received"]
    finally:
        os.close(r_fd)
        os.close(w_fd)


def test_emit_fatal_diagnostic_to_pipe() -> None:
    """Proves emit_fatal_diagnostic writes exact fatal diagnostic bytes to pipe descriptor."""
    r_fd, w_fd = os.pipe()
    try:
        emit_fatal_diagnostic(w_fd)
        read_back = os.read(r_fd, 512)
        assert read_back == FATAL_DIAGNOSTIC_BYTES
    finally:
        os.close(r_fd)
        os.close(w_fd)
