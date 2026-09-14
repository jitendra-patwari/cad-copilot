"""Pure bounded batch format validation routines and output snapshot production.

Invariants:
- Supported formats: step, stl, pdf, dxf, parasolid (BatchOutputFormat).
- Requires exact expected file extension (.step, .stl, .pdf, .dxf, .x_t).
- Requires regular non-reparse file with positive size.
- Drawing size cap: MAX_BATCH_DRAWING_BYTES = 100_000_000 (100 MB) for PDF and DXF.
- Zero raw workstation paths or COM text in diagnostics (SEC-07).
- Race-aware streaming OutputSnapshot capture immediately after structural validation.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Final, Protocol

from artifacts.validation import (
    ArtifactValidationError,
    validate_step_artifact,
    validate_stl_artifact,
)
from batch.filesystem import is_stat_reparse_point
from batch.models import BatchOutputFormat
from batch.output_snapshot import OutputSnapshot, capture_output_snapshot
from batch.parasolid_validation import (
    PARASOLID_VALIDATION_FAILED_MESSAGE,
    ParasolidValidationError,
    validate_parasolid_artifact,
)
from batch.source_integrity import SourceIntegrityError

MAX_BATCH_DRAWING_BYTES: Final[int] = 100_000_000  # 100 MB cap for PDF and DXF
MIN_PDF_BYTES: Final[int] = 16
PDF_MAGIC_PREFIX: Final[bytes] = b"%PDF-"
PDF_EOF_MARKER: Final[bytes] = b"%%EOF"
PDF_TAIL_SCAN_BYTES: Final[int] = 1024
PDF_VERSION_PATTERN: Final[re.Pattern[bytes]] = re.compile(rb"^%PDF-(?:1\.[0-7]|2\.0)(?:\r?\n|\r)")

MAX_DXF_LINES: Final[int] = 1_000_000
MAX_DXF_LINE_CHARS: Final[int] = 2048
DXF_BINARY_SENTINEL: Final[bytes] = b"AutoCAD Binary DXF"

EXPECTED_FORMAT_EXTENSIONS: Final[dict[BatchOutputFormat, str]] = {
    "step": ".step",
    "stl": ".stl",
    "parasolid": ".x_t",
    "pdf": ".pdf",
    "dxf": ".dxf",
}

SUPPORTED_BATCH_OUTPUT_FORMATS: Final[frozenset[str]] = frozenset(EXPECTED_FORMAT_EXTENSIONS.keys())


class BatchFormatValidationError(Exception):
    """Raised when generated batch output fails format validation or snapshot capture."""

    def __init__(
        self,
        format: BatchOutputFormat,
        phase: str,
        message: str = "Artifact validation failed.",
        *,
        code: str = "ARTIFACT_EXPORT_FAILED",
    ) -> None:
        super().__init__(message)
        self.format: Final[BatchOutputFormat] = format
        self.phase: Final[str] = phase
        self.message: Final[str] = message
        self.code: Final[str] = code


class BatchFormatValidator(Protocol):
    """Protocol for batch format validation callables."""

    def __call__(
        self,
        format_id: BatchOutputFormat,
        work_path: Path,
    ) -> OutputSnapshot: ...


STEP_VALIDATION_FAILED_MESSAGE: Final[str] = "STEP artifact failed structural or geometry validation"
STL_VALIDATION_FAILED_MESSAGE: Final[str] = "STL artifact failed mesh structure or facet validation"


def _validate_common_work_file(file_path: Path, expected_format: BatchOutputFormat) -> os.stat_result:
    """Perform common regular-file, suffix, reparse-point, and positive-size checks."""
    expected_ext = EXPECTED_FORMAT_EXTENSIONS[expected_format]
    if file_path.suffix.lower() != expected_ext:
        raise BatchFormatValidationError(
            format=expected_format,
            phase="validation",
            message=f"Output file extension mismatch for format '{expected_format}': expected '{expected_ext}', got '{file_path.suffix}'",
        )

    try:
        lstat_result = file_path.lstat()
    except FileNotFoundError:
        raise BatchFormatValidationError(
            format=expected_format,
            phase="validation",
            message=f"Generated {expected_format} output file does not exist",
        ) from None
    except PermissionError:
        raise BatchFormatValidationError(
            format=expected_format,
            phase="validation",
            message=f"Permission denied accessing generated {expected_format} output file",
        ) from None
    except OSError as exc:
        raise BatchFormatValidationError(
            format=expected_format,
            phase="validation",
            message=f"Cannot inspect generated {expected_format} output file metadata: {type(exc).__name__}",
        ) from exc

    if is_stat_reparse_point(lstat_result):
        raise BatchFormatValidationError(
            format=expected_format,
            phase="validation",
            message=f"Generated {expected_format} output file must not be a symbolic link or reparse point",
        )

    if not stat.S_ISREG(lstat_result.st_mode):
        raise BatchFormatValidationError(
            format=expected_format,
            phase="validation",
            message=f"Generated {expected_format} output file is not a regular file",
        )

    if lstat_result.st_size <= 0:
        raise BatchFormatValidationError(
            format=expected_format,
            phase="validation",
            message=f"Generated {expected_format} output file is empty (0 bytes)",
        )

    return lstat_result


def _validate_pdf_artifact(work_path: Path, size_bytes: int) -> None:
    """Validate bounded PDF drawing artifact: magic prefix, supported version, and EOF marker."""
    if size_bytes > MAX_BATCH_DRAWING_BYTES:
        raise BatchFormatValidationError(
            format="pdf",
            phase="validation",
            message=f"Generated pdf output file exceeds maximum allowed size ({size_bytes} > {MAX_BATCH_DRAWING_BYTES} bytes)",
        )

    if size_bytes < MIN_PDF_BYTES:
        raise BatchFormatValidationError(
            format="pdf",
            phase="validation",
            message=f"Generated pdf output file is too small ({size_bytes} < {MIN_PDF_BYTES} bytes)",
        )

    try:
        with open(work_path, "rb") as f:
            header = f.read(1024)
            if not PDF_VERSION_PATTERN.match(header):
                raise BatchFormatValidationError(
                    format="pdf",
                    phase="validation",
                    message="Generated pdf output file missing valid PDF signature or version token",
                )

            tail_scan_len = min(size_bytes, PDF_TAIL_SCAN_BYTES)
            f.seek(size_bytes - tail_scan_len)
            tail = f.read(tail_scan_len)
            if PDF_EOF_MARKER not in tail:
                raise BatchFormatValidationError(
                    format="pdf",
                    phase="validation",
                    message="Generated pdf output file missing terminal EOF marker",
                )
    except OSError as exc:
        raise BatchFormatValidationError(
            format="pdf",
            phase="validation",
            message=f"Failed reading generated pdf output file: {type(exc).__name__}",
        ) from exc


def _validate_dxf_artifact(work_path: Path, size_bytes: int) -> None:
    """Validate bounded DXF text drawing artifact: group-code pairing, SECTION structure, and terminal EOF."""
    if size_bytes > MAX_BATCH_DRAWING_BYTES:
        raise BatchFormatValidationError(
            format="dxf",
            phase="validation",
            message=f"Generated dxf output file exceeds maximum allowed size ({size_bytes} > {MAX_BATCH_DRAWING_BYTES} bytes)",
        )

    try:
        with open(work_path, "rb") as bf:
            lead = bf.read(32)
            if lead.startswith(DXF_BINARY_SENTINEL):
                raise BatchFormatValidationError(
                    format="dxf",
                    phase="validation",
                    message="Binary DXF format is not supported",
                )
    except OSError as exc:
        raise BatchFormatValidationError(
            format="dxf",
            phase="validation",
            message=f"Failed reading generated dxf output file: {type(exc).__name__}",
        ) from exc

    try:
        with open(work_path, encoding="utf-8", errors="replace") as f:
            lines_scanned = 0
            in_section = False
            expecting_section_name = False
            section_count = 0
            has_terminal_eof = False
            expecting_code = True
            current_group_code: int | None = None

            while True:
                line = f.readline(MAX_DXF_LINE_CHARS + 1)
                if not line:
                    break

                if len(line) > MAX_DXF_LINE_CHARS and not line.endswith("\n"):
                    raise BatchFormatValidationError(
                        format="dxf",
                        phase="validation",
                        message=f"DXF drawing artifact exceeds maximum line length ({MAX_DXF_LINE_CHARS} chars)",
                    )

                lines_scanned += 1
                if lines_scanned > MAX_DXF_LINES:
                    raise BatchFormatValidationError(
                        format="dxf",
                        phase="validation",
                        message=f"DXF drawing artifact exceeds maximum line count ({MAX_DXF_LINES})",
                    )

                if lines_scanned == 1:
                    line = line.lstrip("\ufeff")

                stripped = line.strip()

                if has_terminal_eof:
                    if stripped != "":
                        raise BatchFormatValidationError(
                            format="dxf",
                            phase="validation",
                            message="Unexpected content after terminal EOF in DXF drawing artifact",
                        )
                    continue

                if expecting_code:
                    if not stripped:
                        continue
                    try:
                        current_group_code = int(stripped)
                    except ValueError:
                        raise BatchFormatValidationError(
                            format="dxf",
                            phase="validation",
                            message="Malformed DXF: non-integer group code encountered",
                        ) from None
                    expecting_code = False
                else:
                    value = stripped.upper()
                    if expecting_section_name:
                        if current_group_code != 2 or not stripped:
                            raise BatchFormatValidationError(
                                format="dxf",
                                phase="validation",
                                message="Malformed DXF: SECTION header missing valid section name",
                            )
                        expecting_section_name = False
                    elif current_group_code == 0:
                        if value == "SECTION":
                            if in_section:
                                raise BatchFormatValidationError(
                                    format="dxf",
                                    phase="validation",
                                    message="Malformed DXF: nested SECTION encountered",
                                )
                            in_section = True
                            expecting_section_name = True
                        elif value == "ENDSEC":
                            if not in_section:
                                raise BatchFormatValidationError(
                                    format="dxf",
                                    phase="validation",
                                    message="Malformed DXF: ENDSEC encountered outside active section",
                                )
                            in_section = False
                            section_count += 1
                        elif value == "EOF":
                            if in_section:
                                raise BatchFormatValidationError(
                                    format="dxf",
                                    phase="validation",
                                    message="Malformed DXF: terminal EOF encountered inside active section",
                                )
                            has_terminal_eof = True

                    expecting_code = True
                    current_group_code = None

            if not expecting_code:
                raise BatchFormatValidationError(
                    format="dxf",
                    phase="validation",
                    message="Malformed DXF: file ended with group code but missing value line",
                )

            if in_section:
                raise BatchFormatValidationError(
                    format="dxf",
                    phase="validation",
                    message="Malformed DXF: unclosed SECTION at end of file",
                )

            if section_count < 1:
                raise BatchFormatValidationError(
                    format="dxf",
                    phase="validation",
                    message="DXF drawing artifact missing required SECTION structure",
                )

            if not has_terminal_eof:
                raise BatchFormatValidationError(
                    format="dxf",
                    phase="validation",
                    message="DXF drawing artifact missing terminal EOF marker",
                )

    except OSError as exc:
        raise BatchFormatValidationError(
            format="dxf",
            phase="validation",
            message=f"Failed reading generated dxf output file: {type(exc).__name__}",
        ) from exc


def validate_batch_output(
    format_id: BatchOutputFormat,
    work_path: Path,
) -> OutputSnapshot:
    """Validate generated batch output artifact and capture its immutable OutputSnapshot.

    Dispatches across supported batch formats (step, stl, parasolid, pdf, dxf).
    Reuses established ISO 10303-21 STEP and STL validators without parser duplication.
    Validates bounded PDF, text DXF, and Parasolid transmission structural requirements.
    Fails closed with sanitized BatchFormatValidationError on any violation.
    """
    if format_id not in SUPPORTED_BATCH_OUTPUT_FORMATS:
        raise BatchFormatValidationError(
            format=format_id,
            phase="validation",
            message=f"Unsupported batch output format: '{format_id}'",
        )

    lstat_before = _validate_common_work_file(work_path, format_id)
    size_before = lstat_before.st_size
    mtime_before = lstat_before.st_mtime_ns
    dev_before = lstat_before.st_dev
    ino_before = lstat_before.st_ino

    if format_id == "step":
        try:
            validate_step_artifact(work_path)
        except ArtifactValidationError as exc:
            raise BatchFormatValidationError(
                format="step",
                phase="validation",
                message=STEP_VALIDATION_FAILED_MESSAGE,
            ) from exc
        except OSError as exc:
            raise BatchFormatValidationError(
                format="step",
                phase="validation",
                message="Failed reading STEP artifact file",
            ) from exc

    elif format_id == "stl":
        try:
            validate_stl_artifact(work_path)
        except ArtifactValidationError as exc:
            raise BatchFormatValidationError(
                format="stl",
                phase="validation",
                message=STL_VALIDATION_FAILED_MESSAGE,
            ) from exc
        except OSError as exc:
            raise BatchFormatValidationError(
                format="stl",
                phase="validation",
                message="Failed reading STL artifact file",
            ) from exc

    elif format_id == "pdf":
        _validate_pdf_artifact(work_path, size_before)

    elif format_id == "dxf":
        _validate_dxf_artifact(work_path, size_before)

    elif format_id == "parasolid":
        try:
            validate_parasolid_artifact(work_path, size_before)
        except ParasolidValidationError as exc:
            raise BatchFormatValidationError(
                format="parasolid",
                phase="validation",
                message=exc.message,
            ) from exc
        except OSError as exc:
            raise BatchFormatValidationError(
                format="parasolid",
                phase="validation",
                message="Failed reading Parasolid artifact file",
            ) from exc

    # Capture streaming OutputSnapshot and verify stability across validation
    try:
        snapshot = capture_output_snapshot(work_path)
    except SourceIntegrityError as exc:
        raise BatchFormatValidationError(
            format=format_id,
            phase="snapshot",
            message=f"Generated {format_id} output snapshot could not be captured: {exc.reason}",
        ) from exc
    except (OSError, ValueError) as exc:
        raise BatchFormatValidationError(
            format=format_id,
            phase="snapshot",
            message=f"Generated {format_id} output snapshot could not be captured: {type(exc).__name__}",
        ) from exc

    if (
        snapshot.identity.device != dev_before
        or snapshot.identity.inode != ino_before
        or snapshot.size_bytes != size_before
        or snapshot.mtime_ns != mtime_before
    ):
        raise BatchFormatValidationError(
            format=format_id,
            phase="snapshot",
            message=f"Generated {format_id} output was modified during validation",
        )

    return snapshot


__all__ = [
    "MAX_BATCH_DRAWING_BYTES",
    "PARASOLID_VALIDATION_FAILED_MESSAGE",
    "STEP_VALIDATION_FAILED_MESSAGE",
    "STL_VALIDATION_FAILED_MESSAGE",
    "BatchFormatValidationError",
    "BatchFormatValidator",
    "validate_batch_output",
]
