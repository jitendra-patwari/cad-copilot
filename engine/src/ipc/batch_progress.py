"""Curated progress and diagnostic protocol for CAD Copilot batch IPC.

Encodes discrete BatchProgressUpdate events into compact, ASCII-safe JSONL lines
on stderr and provides the fixed fatal diagnostic payload.
"""

from __future__ import annotations

import contextlib
from typing import Any, Final

from batch.execution import BatchProgressUpdate
from ipc.wire import (
    serialize_json_line as _serialize_json_line,
)
from ipc.wire import (
    write_all as _write_all,
)

MAX_BATCH_PROGRESS_PAYLOAD_BYTES: Final[int] = 4096  # 4 KiB event cap

BATCH_FATAL_DIAGNOSTIC_MESSAGE: Final[str] = "Batch process failed before a contract response could be produced."
BATCH_FATAL_DIAGNOSTIC_BYTES: Final[bytes] = (
    b'{"type":"diagnostic","phase":"fatal","message":"Batch process failed before a contract response could be produced."}\n'
)


def format_batch_progress(update: BatchProgressUpdate) -> bytes:
    """Format a discrete BatchProgressUpdate as compact ASCII JSON followed by LF.

    Args:
        update: Validated BatchProgressUpdate event.

    Returns:
        Exact byte sequence for the JSONL line (max 4 KiB).
    """
    if not isinstance(update, BatchProgressUpdate):
        raise TypeError(f"Expected BatchProgressUpdate, got {type(update).__name__}")

    data: dict[str, Any] = {
        "type": "progress",
        "request_id": update.request_id,
        "phase": update.phase,
        "total_files": update.total_files,
        "completed_files": update.completed_files,
    }
    if update.current_file is not None:
        data["current_file"] = update.current_file
    if update.current_format is not None:
        data["current_format"] = update.current_format
    if update.file_status is not None:
        data["file_status"] = update.file_status

    encoded = _serialize_json_line(data)
    if len(encoded) > MAX_BATCH_PROGRESS_PAYLOAD_BYTES:
        raise ValueError(
            f"Progress event size ({len(encoded)} bytes) exceeds limit ({MAX_BATCH_PROGRESS_PAYLOAD_BYTES} bytes)"
        )
    return encoded


def format_batch_fatal_diagnostic() -> bytes:
    """Format the fixed batch fatal diagnostic as compact ASCII JSON followed by LF.

    Returns:
        Exact byte sequence for the fatal diagnostic JSONL line.
    """
    return BATCH_FATAL_DIAGNOSTIC_BYTES


def emit_batch_progress(fd: int, update: BatchProgressUpdate) -> None:
    """Emit a formatted progress event to the specified file descriptor.

    Best-effort write: suppresses OSError so a broken or closed stderr pipe never
    interrupts execution, prevents cleanup, or alters terminal accounting.

    Args:
        fd: Preserved stderr file descriptor.
        update: Validated BatchProgressUpdate event.
    """
    with contextlib.suppress(OSError):
        payload = format_batch_progress(update)
        _write_all(fd, payload, max_bytes=MAX_BATCH_PROGRESS_PAYLOAD_BYTES)


def emit_batch_fatal_diagnostic(fd: int) -> None:
    """Emit the fixed fatal diagnostic event to the specified file descriptor.

    Args:
        fd: Preserved stderr file descriptor.
    """
    with contextlib.suppress(OSError):
        _write_all(fd, BATCH_FATAL_DIAGNOSTIC_BYTES, max_bytes=MAX_BATCH_PROGRESS_PAYLOAD_BYTES)


__all__ = [
    "BATCH_FATAL_DIAGNOSTIC_BYTES",
    "BATCH_FATAL_DIAGNOSTIC_MESSAGE",
    "MAX_BATCH_PROGRESS_PAYLOAD_BYTES",
    "emit_batch_fatal_diagnostic",
    "emit_batch_progress",
    "format_batch_fatal_diagnostic",
    "format_batch_progress",
]
