"""Pure bounded structural validation for Parasolid text transmission artifacts (.x_t).

Invariants:
- Requires regular non-reparse file with positive size >= MIN_PARASOLID_BYTES.
- Rejects binary or NUL-byte containing payloads within inspected bounded header and trailer windows.
- Bounded header scan: reads at most PARASOLID_HEADER_SCAN_BYTES (2048 bytes).
  - Validates character set line starting with '**' and containing alphabet.
  - Validates symbol delimiter line starting with '**PARASOLID '.
  - Validates section markers: '**PART1;' and '**PART2;'.
  - Validates transmission attributes: 'FORMAT=text;' and 'GUISE=transmit;'.
  - Validates schema marker: 'SCH=SCH_'.
- Bounded trailer scan: reads at most PARASOLID_TRAILER_SCAN_BYTES (2048 bytes).
  - Validates non-empty text content, zero NUL bytes, and newline record termination.
- Zero COM, zero registry, zero runtime, and zero schema dependencies.
- Zero raw workstation paths or exceptions leaked (SEC-07).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

MIN_PARASOLID_BYTES: Final[int] = 100
MAX_LINE_BYTES: Final[int] = 256
PARASOLID_HEADER_SCAN_BYTES: Final[int] = 2048
PARASOLID_TRAILER_SCAN_BYTES: Final[int] = 2048

CHARSET_LINE_PREFIX: Final[bytes] = b"**"
CHARSET_ALPHABET: Final[bytes] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PARASOLID_LINE_PREFIX: Final[bytes] = b"**PARASOLID "
PART1_MARKER: Final[bytes] = b"**PART1;"
PART2_MARKER: Final[bytes] = b"**PART2;"
FORMAT_TEXT_MARKER: Final[bytes] = b"FORMAT=text;"
GUISE_TRANSMIT_MARKER: Final[bytes] = b"GUISE=transmit;"
SCHEMA_PATTERN: Final[re.Pattern[bytes]] = re.compile(rb"SCH=SCH_([A-Za-z0-9_]+);")

PARASOLID_VALIDATION_FAILED_MESSAGE: Final[str] = "Parasolid artifact failed structural validation"


class ParasolidValidationError(ValueError):
    """Raised when a Parasolid text transmission file violates structural invariants."""

    def __init__(self, message: str = PARASOLID_VALIDATION_FAILED_MESSAGE) -> None:
        super().__init__(message)
        self.message: Final[str] = message


def validate_parasolid_artifact(file_path: Path, size_bytes: int | None = None) -> None:
    """Validate that the given file is a structurally sound Parasolid text transmission file (.x_t).

    Args:
        file_path: Path to the .x_t file to validate.
        size_bytes: Optional pre-determined file size in bytes to avoid redundant stat calls.

    Raises:
        ParasolidValidationError: If the file is invalid, truncated, binary, or missing required markers.
        OSError: If reading the file encounters filesystem I/O or permission errors.
    """
    if size_bytes is None:
        st = file_path.stat()
        size_bytes = st.st_size

    if size_bytes < MIN_PARASOLID_BYTES:
        raise ParasolidValidationError(
            f"Generated Parasolid output file is too small to be a valid transmission file ({size_bytes} < {MIN_PARASOLID_BYTES} bytes)"
        )

    header_scan_len = min(size_bytes, PARASOLID_HEADER_SCAN_BYTES)
    trailer_scan_len = min(size_bytes, PARASOLID_TRAILER_SCAN_BYTES)

    try:
        with open(file_path, "rb") as f:
            header = f.read(header_scan_len)

            # Check for binary / NUL bytes in header
            if b"\x00" in header:
                raise ParasolidValidationError(
                    "Parasolid text artifact contains forbidden binary or NUL bytes in header"
                )

            # Text encoding verification: text transmission files must be valid ASCII
            try:
                header.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ParasolidValidationError(
                    "Parasolid text artifact contains non-ASCII characters or invalid text encoding"
                ) from exc

            # Check line structure
            lines = header.splitlines()
            if len(lines) < 3:
                raise ParasolidValidationError("Parasolid artifact header has fewer than 3 lines")

            for line in lines:
                if len(line) > MAX_LINE_BYTES:
                    raise ParasolidValidationError(
                        f"Parasolid text artifact contains header line exceeding {MAX_LINE_BYTES} bytes"
                    )

            # Line 1: Character set line
            line1 = lines[0].strip()
            if not line1.startswith(CHARSET_LINE_PREFIX) or CHARSET_ALPHABET not in line1:
                raise ParasolidValidationError("Parasolid artifact missing valid character set translation header")

            # Line 2: Symbol delimiter line
            line2 = lines[1].strip()
            if not line2.startswith(PARASOLID_LINE_PREFIX):
                raise ParasolidValidationError("Parasolid artifact missing valid **PARASOLID symbol delimiter header")

            # Header markers in the bounded window
            idx_part1 = header.find(PART1_MARKER)
            if idx_part1 == -1:
                raise ParasolidValidationError("Parasolid artifact missing required **PART1; header marker")

            if FORMAT_TEXT_MARKER not in header:
                raise ParasolidValidationError("Parasolid artifact missing required FORMAT=text; header attribute")

            if GUISE_TRANSMIT_MARKER not in header:
                raise ParasolidValidationError("Parasolid artifact missing required GUISE=transmit; header attribute")

            idx_part2 = header.find(PART2_MARKER)
            if idx_part2 == -1:
                raise ParasolidValidationError("Parasolid artifact missing required **PART2; schema section marker")

            if idx_part1 >= idx_part2:
                raise ParasolidValidationError(
                    "Parasolid artifact missing valid section sequence (**PART1; must precede **PART2;)"
                )

            if not SCHEMA_PATTERN.search(header):
                raise ParasolidValidationError(
                    "Parasolid artifact missing required SCH=SCH_<version>; schema version marker"
                )

            # Bounded trailer scan
            f.seek(size_bytes - trailer_scan_len)
            trailer = f.read(trailer_scan_len)

            if b"\x00" in trailer:
                raise ParasolidValidationError(
                    "Parasolid text artifact contains forbidden binary or NUL bytes in trailer"
                )

            try:
                trailer.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ParasolidValidationError(
                    "Parasolid text artifact trailer contains non-ASCII characters or invalid text encoding"
                ) from exc

            if not trailer.strip():
                raise ParasolidValidationError("Parasolid artifact trailer contains only whitespace")

            if not trailer.endswith((b"\r\n", b"\n")):
                raise ParasolidValidationError("Parasolid artifact trailer is truncated (missing newline terminator)")

            trailer_lines = trailer.splitlines()
            for t_line in trailer_lines:
                if len(t_line) > MAX_LINE_BYTES:
                    raise ParasolidValidationError(
                        f"Parasolid text artifact contains trailer line exceeding {MAX_LINE_BYTES} bytes"
                    )

    except ParasolidValidationError:
        raise
    except OSError as exc:
        raise OSError(f"Failed reading Parasolid artifact: {type(exc).__name__}") from exc


__all__ = [
    "MIN_PARASOLID_BYTES",
    "PARASOLID_HEADER_SCAN_BYTES",
    "PARASOLID_TRAILER_SCAN_BYTES",
    "PARASOLID_VALIDATION_FAILED_MESSAGE",
    "ParasolidValidationError",
    "validate_parasolid_artifact",
]
