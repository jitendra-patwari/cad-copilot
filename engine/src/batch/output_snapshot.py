"""Immutable output snapshot models and race-aware streaming capture.

Guarantees output artifact content integrity before atomic publication:
- OutputSnapshot: frozen value recording device/inode identity, size, nanosecond mtime, and SHA-256.
- capture_output_snapshot: race-aware multi-chunk streaming hashing requiring positive file size.
- verify_output_snapshot_equality: exact comparison of identity, size, modification time, and cryptographic digest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from batch.filesystem import PathIdentity
from batch.source_integrity import DEFAULT_STREAM_BUFFER_SIZE, capture_source_snapshot

# Lowercase 64-character hex string pattern for SHA-256 digests
HEX_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class OutputSnapshot:
    """Immutable snapshot capturing exact generated output file identity and digest."""

    identity: PathIdentity
    size_bytes: int
    mtime_ns: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, PathIdentity):
            raise TypeError(f"identity must be PathIdentity, got {type(self.identity).__name__}")
        if not isinstance(self.size_bytes, int) or self.size_bytes <= 0:
            raise ValueError(f"size_bytes must be a positive int, got {self.size_bytes!r}")
        if not isinstance(self.mtime_ns, int):
            raise TypeError(f"mtime_ns must be int, got {type(self.mtime_ns).__name__}")
        if not isinstance(self.sha256, str) or not HEX_SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError(f"sha256 must be a 64-character lowercase hex string, got {self.sha256!r}")


def capture_output_snapshot(
    path: Path,
    *,
    buffer_size: int = DEFAULT_STREAM_BUFFER_SIZE,
) -> OutputSnapshot:
    """Capture a race-aware streaming SHA-256 snapshot of a generated output file.

    Requires the file to exist, be a regular non-reparse file, and have a positive size.
    """
    source_snap = capture_source_snapshot(path, buffer_size=buffer_size)
    if source_snap.size_bytes <= 0:
        raise ValueError(f"Generated output file cannot be zero bytes: '{path.name}'")
    return OutputSnapshot(
        identity=source_snap.identity,
        size_bytes=source_snap.size_bytes,
        mtime_ns=source_snap.mtime_ns,
        sha256=source_snap.sha256,
    )


def verify_output_snapshot_equality(before: OutputSnapshot, after: OutputSnapshot) -> bool:
    """Return True if after snapshot exactly equals before snapshot across all fields."""
    if not isinstance(before, OutputSnapshot) or not isinstance(after, OutputSnapshot):
        return False
    return (
        before.identity == after.identity
        and before.size_bytes == after.size_bytes
        and before.mtime_ns == after.mtime_ns
        and before.sha256 == after.sha256
    )
