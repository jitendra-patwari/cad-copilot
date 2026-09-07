"""Curated progress and diagnostic protocol for CAD Copilot generation IPC.

This module defines the fixed progress phases, fatal diagnostic payload,
deterministic ASCII-safe JSONL formatting, and bounded descriptor I/O helpers.
"""

from __future__ import annotations

import os
from typing import Final, Literal

ProgressPhase = Literal[
    "request_received",
    "request_validated",
    "generation_started",
    "response_ready",
]

PROGRESS_PHASES: Final[tuple[ProgressPhase, ...]] = (
    "request_received",
    "request_validated",
    "generation_started",
    "response_ready",
)

PROGRESS_MESSAGES: Final[dict[ProgressPhase, str]] = {
    "request_received": "Request received.",
    "request_validated": "Request validated.",
    "generation_started": "Generation started.",
    "response_ready": "Contract response ready.",
}

FATAL_DIAGNOSTIC_MESSAGE: Final[str] = "Generation process failed before a contract response could be produced."

# Deterministic pre-computed ASCII JSONL bytes (ending with LF)
FIXED_PROGRESS_BYTES: Final[dict[ProgressPhase, bytes]] = {
    "request_received": b'{"type":"progress","phase":"request_received","message":"Request received."}\n',
    "request_validated": b'{"type":"progress","phase":"request_validated","message":"Request validated."}\n',
    "generation_started": b'{"type":"progress","phase":"generation_started","message":"Generation started."}\n',
    "response_ready": b'{"type":"progress","phase":"response_ready","message":"Contract response ready."}\n',
}

FATAL_DIAGNOSTIC_BYTES: Final[bytes] = (
    b'{"type":"diagnostic","phase":"fatal","message":"Generation process failed before a contract response could be produced."}\n'
)

MAX_PROGRESS_PAYLOAD_BYTES: Final[int] = 1024
DEFAULT_MAX_WRITE_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MiB safety cap


def format_progress_event(phase: ProgressPhase) -> bytes:
    """Format a fixed progress event as compact ASCII JSON followed by LF.

    Args:
        phase: One of the four canonical progress phases.

    Returns:
        Exact byte sequence for the JSONL line.
    """
    if phase not in FIXED_PROGRESS_BYTES:
        raise ValueError(f"Unknown progress phase: {phase}")
    return FIXED_PROGRESS_BYTES[phase]


def format_fatal_diagnostic() -> bytes:
    """Format the fixed fatal diagnostic as compact ASCII JSON followed by LF.

    Returns:
        Exact byte sequence for the fatal diagnostic JSONL line.
    """
    return FATAL_DIAGNOSTIC_BYTES


def write_all(fd: int, data: bytes, *, max_bytes: int = DEFAULT_MAX_WRITE_BYTES) -> None:
    """Write entire byte buffer to an OS file descriptor in a bounded loop.

    Handles partial writes from os.write and verifies that total bytes
    written does not exceed max_bytes.

    Args:
        fd: Target OS file descriptor.
        data: Byte buffer to write.
        max_bytes: Maximum total bytes permitted to be written.

    Raises:
        ValueError: If data length exceeds max_bytes.
        OSError: If an underlying os.write call fails or makes no forward progress.
    """
    if len(data) > max_bytes:
        raise ValueError(f"Data length ({len(data)} bytes) exceeds maximum permitted limit ({max_bytes} bytes).")

    total_written = 0
    total_to_write = len(data)

    while total_written < total_to_write:
        chunk = data[total_written:]
        written = os.write(fd, chunk)
        if written <= 0:
            raise OSError(f"Write to descriptor {fd} made no progress (os.write returned {written}).")
        total_written += written


def emit_progress(fd: int, phase: ProgressPhase) -> None:
    """Emit a fixed progress event to the specified file descriptor.

    Args:
        fd: Preserved stderr file descriptor.
        phase: One of the four canonical progress phases.
    """
    payload = format_progress_event(phase)
    write_all(fd, payload, max_bytes=MAX_PROGRESS_PAYLOAD_BYTES)


def emit_fatal_diagnostic(fd: int) -> None:
    """Emit the fixed fatal diagnostic event to the specified file descriptor.

    Args:
        fd: Preserved stderr file descriptor.
    """
    payload = format_fatal_diagnostic()
    write_all(fd, payload, max_bytes=MAX_PROGRESS_PAYLOAD_BYTES)
