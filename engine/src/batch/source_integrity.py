"""Immutable source snapshot models, race-aware streaming capture, and integrity verification.

Guarantees byte-for-byte immutability across CAD processing:
- SourceSnapshot: frozen value recording device/inode identity, size, nanosecond mtime, and SHA-256.
- capture_source_snapshot: race-aware multi-chunk streaming hashing with pre/post stat verification.
- verify_snapshot_equality: exact comparison of identity, size, modification time, and cryptographic digest.
"""

from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from batch.filesystem import PathIdentity, is_stat_reparse_point

HEX_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_STREAM_BUFFER_SIZE: Final[int] = 1024 * 1024  # 1 MiB chunks

SourceIntegrityReason = Literal["not_found", "unreadable", "unstable", "reparse_point", "non_regular"]


class SourceIntegrityError(Exception):
    """Raised when source snapshot capture fails due to file access, race condition, or reparse violation."""

    def __init__(self, reason: SourceIntegrityReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class SourceSnapshot:
    """Immutable snapshot capturing exact file identity and cryptographic content digest."""

    identity: PathIdentity
    size_bytes: int
    mtime_ns: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, PathIdentity):
            raise TypeError(f"identity must be PathIdentity, got {type(self.identity).__name__}")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError(f"size_bytes must be a non-negative int, got {self.size_bytes!r}")
        if not isinstance(self.mtime_ns, int):
            raise TypeError(f"mtime_ns must be int, got {type(self.mtime_ns).__name__}")
        if not isinstance(self.sha256, str) or not HEX_SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError(f"sha256 must be a 64-character lowercase hex string, got {self.sha256!r}")


def capture_source_snapshot(
    path: Path,
    *,
    buffer_size: int = DEFAULT_STREAM_BUFFER_SIZE,
) -> SourceSnapshot:
    """Capture a race-aware streaming SHA-256 snapshot of path.

    Reads path in chunks of buffer_size without loading the whole file into memory.
    Validates identity, size, and mtime before and after reading; raises SourceIntegrityError
    with a typed reason if the file was modified, moved, replaced, or cannot be read cleanly.
    """
    if not isinstance(path, Path):
        raise TypeError(f"path must be a Path, got {type(path).__name__}")
    if buffer_size <= 0:
        raise ValueError("buffer_size must be positive")

    try:
        try:
            stat_before = path.lstat()
        except FileNotFoundError as exc:
            raise SourceIntegrityError("not_found", f"Source file does not exist: '{path.name}'") from exc
        except PermissionError as exc:
            raise SourceIntegrityError("unreadable", f"Permission denied reading source file: '{path.name}'") from exc
        except (OSError, ValueError) as exc:
            raise SourceIntegrityError(
                "unreadable",
                f"Failed to access source metadata: {type(exc).__name__}",
            ) from exc

        if is_stat_reparse_point(stat_before):
            raise SourceIntegrityError(
                "reparse_point",
                f"Cannot capture snapshot of symbolic link or reparse point: '{path.name}'",
            )

        if not stat.S_ISREG(stat_before.st_mode):
            raise SourceIntegrityError("non_regular", f"Source path is not a regular file: '{path.name}'")

        identity_before = PathIdentity(
            device=stat_before.st_dev,
            inode=stat_before.st_ino,
            mode=stat_before.st_mode,
        )
        size_before = stat_before.st_size
        mtime_before = stat_before.st_mtime_ns

        hasher = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(buffer_size):
                hasher.update(chunk)
        digest = hasher.hexdigest().lower()

        try:
            stat_after = path.lstat()
        except FileNotFoundError as exc:
            raise SourceIntegrityError("not_found", f"Source file does not exist: '{path.name}'") from exc
        except PermissionError as exc:
            raise SourceIntegrityError("unreadable", f"Permission denied reading source file: '{path.name}'") from exc
        except (OSError, ValueError) as exc:
            raise SourceIntegrityError(
                "unreadable",
                f"Failed to access source metadata: {type(exc).__name__}",
            ) from exc

        if is_stat_reparse_point(stat_after):
            raise SourceIntegrityError(
                "reparse_point",
                f"Source file became a symbolic link or reparse point during capture: '{path.name}'",
            )

        identity_after = PathIdentity(
            device=stat_after.st_dev,
            inode=stat_after.st_ino,
            mode=stat_after.st_mode,
        )
        size_after = stat_after.st_size
        mtime_after = stat_after.st_mtime_ns

        if identity_before != identity_after or size_before != size_after or mtime_before != mtime_after:
            raise SourceIntegrityError(
                "unstable",
                f"Source file was modified during snapshot capture: '{path.name}'",
            )

        return SourceSnapshot(
            identity=identity_before,
            size_bytes=size_before,
            mtime_ns=mtime_before,
            sha256=digest,
        )

    except SourceIntegrityError:
        raise
    except FileNotFoundError as exc:
        raise SourceIntegrityError("not_found", f"Source file does not exist: '{path.name}'") from exc
    except PermissionError as exc:
        raise SourceIntegrityError("unreadable", f"Permission denied reading source file: '{path.name}'") from exc
    except (OSError, ValueError) as exc:
        raise SourceIntegrityError(
            "unreadable",
            f"Failed to capture source snapshot for '{path.name}': {type(exc).__name__}",
        ) from exc


def verify_snapshot_equality(before: SourceSnapshot, after: SourceSnapshot) -> bool:
    """Return True if after snapshot exactly equals before snapshot across all fields."""
    if not isinstance(before, SourceSnapshot) or not isinstance(after, SourceSnapshot):
        return False
    return (
        before.identity == after.identity
        and before.size_bytes == after.size_bytes
        and before.mtime_ns == after.mtime_ns
        and before.sha256 == after.sha256
    )
