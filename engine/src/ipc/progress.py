"""Curated progress and diagnostic protocol for CAD Copilot generation IPC.

This module defines the fixed progress phases, fatal diagnostic payload,
deterministic ASCII-safe JSONL formatting, and bounded descriptor I/O helpers.
"""

from __future__ import annotations

from typing import Final, Literal

from ipc.wire import write_all as _write_all

__all__ = [
    "FATAL_DIAGNOSTIC_BYTES",
    "FATAL_DIAGNOSTIC_MESSAGE",
    "FIXED_PROGRESS_BYTES",
    "MAX_PROGRESS_PAYLOAD_BYTES",
    "PROGRESS_MESSAGES",
    "PROGRESS_PHASES",
    "ProgressPhase",
    "emit_fatal_diagnostic",
    "emit_progress",
    "format_fatal_diagnostic",
    "format_progress_event",
]

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


def emit_progress(fd: int, phase: ProgressPhase) -> None:
    """Emit a fixed progress event to the specified file descriptor.

    Args:
        fd: Preserved stderr file descriptor.
        phase: One of the four canonical progress phases.
    """
    payload = format_progress_event(phase)
    _write_all(fd, payload, max_bytes=MAX_PROGRESS_PAYLOAD_BYTES)


def emit_fatal_diagnostic(fd: int) -> None:
    """Emit the fixed fatal diagnostic event to the specified file descriptor.

    Args:
        fd: Preserved stderr file descriptor.
    """
    payload = format_fatal_diagnostic()
    _write_all(fd, payload, max_bytes=MAX_PROGRESS_PAYLOAD_BYTES)
