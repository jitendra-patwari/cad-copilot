"""Pure, bounded artifact validation routines and streaming checksums.

Invariants:
    - Zero-I/O Purity (NFR-1) where applicable; deterministic bounded reads on disk files.
    - Explicit Constant Caps (CWE-400): MAX_STEP_TEXT_CHARS, MAX_STL_BYTES, MAX_JPG_SIZE_BYTES.
    - No Third-Party Image or Mesh Libraries: Pure standard library only (hashlib, struct, math, pathlib).
    - Robust Encoding: STEP read as bounded UTF-8 with replacement (encoding="utf-8", errors="replace").
    - Streaming Hashing: SHA-256 and byte sizes computed in bounded 64 KiB chunks with O(1) memory.
"""

from __future__ import annotations

import hashlib
import math
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from geometry.step_checker import MAX_STEP_TEXT_CHARS, validate_step_text
from interfaces.exceptions import CADExportError
from interfaces.models import ArtifactFormat, StandardInspectionReport

# ---------------------------------------------------------------------------
# Bounded Constants
# ---------------------------------------------------------------------------

DEFAULT_HASH_CHUNK_SIZE: int = 65_536  # 64 KiB
MIN_BINARY_STL_BYTES: int = 84  # 80-byte header + 4-byte uint32 count
MAX_STL_BYTES: int = 100_000_000  # 100 MB cap on STL files
MAX_ASCII_STL_LINES: int = 1_000_000  # 1 million lines bounded scan
MAX_ASCII_STL_LINE_CHARS: int = 1024  # Max length per ASCII STL line
MIN_JPG_SIZE_BYTES: int = 100  # Minimum plausible JPEG file size
MAX_JPG_SIZE_BYTES: int = 20_000_000  # 20 MB cap on preview JPEG
JPEG_SOI: bytes = b"\xff\xd8"
JPEG_EOI: bytes = b"\xff\xd9"


class ArtifactValidationError(CADExportError):
    """Raised when an artifact file fails existence, format, or bounds validation."""

    error_code: str = "ARTIFACT_EXPORT_FAILED"


# ---------------------------------------------------------------------------
# File Stability Metadata Snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileSnapshot:
    """Lightweight metadata snapshot binding an artifact's filesystem state."""

    st_dev: int
    st_ino: int
    st_size: int
    st_mtime_ns: int

    @classmethod
    def capture(cls, path: Path) -> FileSnapshot:
        try:
            st = path.stat()
            return cls(
                st_dev=st.st_dev,
                st_ino=st.st_ino,
                st_size=st.st_size,
                st_mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
            )
        except OSError as exc:
            raise ArtifactValidationError(
                f"Cannot inspect file metadata snapshot for '{path.name}': {type(exc).__name__}"
            ) from exc


class ValidatedArtifact(tuple[str, int]):
    """Tuple of (sha256, size_bytes) with attached file stability snapshot."""

    _snapshot: FileSnapshot

    def __new__(cls, sha256: str, size_bytes: int, snapshot: FileSnapshot) -> ValidatedArtifact:
        instance = super().__new__(cls, (sha256, size_bytes))
        instance._snapshot = snapshot
        return instance

    @property
    def snapshot(self) -> FileSnapshot:
        return self._snapshot


# ---------------------------------------------------------------------------
# Streaming Checksum & Size
# ---------------------------------------------------------------------------


def compute_file_sha256_and_size(
    file_path: Path,
    chunk_size: int = DEFAULT_HASH_CHUNK_SIZE,
) -> tuple[str, int]:
    """Compute lowercase SHA-256 hex digest and byte size in a single streaming pass."""
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be strictly positive, got {chunk_size}")

    hasher = hashlib.sha256()
    total_bytes = 0

    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                total_bytes += len(chunk)
    except OSError as exc:
        raise ArtifactValidationError(
            f"Failed to read file for hashing at '{file_path.name}': {type(exc).__name__}"
        ) from exc

    return hasher.hexdigest().lower(), total_bytes


# ---------------------------------------------------------------------------
# Common Regular-File Gate
# ---------------------------------------------------------------------------


def validate_common_file(file_path: Path, expected_extension: str | tuple[str, ...]) -> int:
    try:
        lstat_result = file_path.lstat()
    except FileNotFoundError:
        raise ArtifactValidationError(f"Artifact file does not exist: '{file_path.name}'") from None
    except OSError as exc:
        raise ArtifactValidationError(
            f"Cannot verify file status for '{file_path.name}': {type(exc).__name__}"
        ) from exc

    if stat.S_ISLNK(lstat_result.st_mode):
        raise ArtifactValidationError(f"Artifact file must not be a symbolic link: '{file_path.name}'")

    if sys.platform == "win32":
        reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        file_attrs = getattr(lstat_result, "st_file_attributes", 0)
        if (file_attrs & reparse_attr) != 0 or getattr(lstat_result, "st_reparse_tag", 0) != 0:
            raise ArtifactValidationError(f"Artifact file must not be a reparse point or link: '{file_path.name}'")

    if not file_path.is_file():
        raise ArtifactValidationError(f"Artifact path is not a regular file: '{file_path.name}'")

    if isinstance(expected_extension, str):
        norm_ext = expected_extension.lower()
        if not norm_ext.startswith("."):
            norm_ext = f".{norm_ext}"
        if file_path.suffix.lower() != norm_ext:
            raise ArtifactValidationError(
                f"Artifact file extension mismatch: expected '{norm_ext}', got '{file_path.suffix}'"
            )
    else:
        norm_exts = tuple((ext if ext.startswith(".") else f".{ext}").lower() for ext in expected_extension)
        if file_path.suffix.lower() not in norm_exts:
            expected_desc = " or ".join(f"'{ext}'" for ext in norm_exts)
            raise ArtifactValidationError(
                f"Artifact file extension mismatch: expected {expected_desc}, got '{file_path.suffix}'"
            )

    try:
        stat_result = file_path.stat()
    except OSError as exc:
        raise ArtifactValidationError(
            f"Cannot inspect artifact metadata for '{file_path.name}': {type(exc).__name__}"
        ) from exc

    if stat_result.st_size <= 0:
        raise ArtifactValidationError(f"Artifact file is empty (0 bytes): '{file_path.name}'")

    return stat_result.st_size


# ---------------------------------------------------------------------------
# Native Solid Edge PAR Validation
# ---------------------------------------------------------------------------


def validate_par_artifact(
    file_path: Path,
    inspection_report: StandardInspectionReport | None,
) -> int:
    """Validate native .par artifact combining common file checks and M3.2 inspection authority."""
    size_bytes = validate_common_file(file_path, ".par")

    if inspection_report is None:
        raise ArtifactValidationError(f"Native PAR artifact '{file_path.name}' requires a valid CAD inspection report.")

    if inspection_report.solid_body_count != 1:
        raise ArtifactValidationError(
            f"Native PAR artifact '{file_path.name}' must have exactly 1 solid body, "
            f"got {inspection_report.solid_body_count}."
        )

    if inspection_report.body_count != 1:
        raise ArtifactValidationError(
            f"Native PAR artifact '{file_path.name}' must have exactly 1 total body, "
            f"got {inspection_report.body_count}."
        )

    if (inspection_report.sheet_body_count is not None and inspection_report.sheet_body_count != 0) or (
        inspection_report.wire_body_count is not None and inspection_report.wire_body_count != 0
    ):
        raise ArtifactValidationError(
            f"Native PAR artifact '{file_path.name}' contains invalid sheet or wire bodies "
            f"(sheet={inspection_report.sheet_body_count}, wire={inspection_report.wire_body_count})."
        )

    if not math.isfinite(inspection_report.volume_mm3) or inspection_report.volume_mm3 <= 0:
        raise ArtifactValidationError(
            f"Native PAR artifact '{file_path.name}' has non-positive or non-finite volume "
            f"({inspection_report.volume_mm3} mm3)."
        )

    return size_bytes


# ---------------------------------------------------------------------------
# STEP Validation
# ---------------------------------------------------------------------------


def validate_step_artifact(
    file_path: Path,
    max_chars: int = MAX_STEP_TEXT_CHARS,
) -> int:
    """Validate bounded ISO 10303-21 STEP artifact with UTF-8 replacement and solid B-Rep presence."""
    size_bytes = validate_common_file(file_path, (".step", ".stp"))

    # Read explicitly with UTF-8 and replacement; read at most max_chars + 1 to detect overflow
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            step_text = f.read(max_chars + 1)
    except OSError as exc:
        raise ArtifactValidationError(f"Failed to read STEP artifact '{file_path.name}': {type(exc).__name__}") from exc

    if len(step_text) > max_chars:
        raise ArtifactValidationError(
            f"STEP artifact '{file_path.name}' exceeds maximum allowed text length ({max_chars} chars)."
        )

    result = validate_step_text(step_text)
    if not result.is_valid:
        if result.errors:
            error_summary = "; ".join(result.errors[:5])
            if len(result.errors) > 5:
                error_summary += f" (and {len(result.errors) - 5} more errors)"
            raise ArtifactValidationError(f"STEP validation failed for '{file_path.name}': {error_summary}")
        if not result.solid_brep_detected:
            raise ArtifactValidationError(f"STEP artifact '{file_path.name}' contains no MANIFOLD_SOLID_BREP entities.")
        raise ArtifactValidationError(f"STEP validation failed for '{file_path.name}': Invalid ISO-10303-21 structure")

    if not result.solid_brep_detected or result.signals is None or result.signals.manifold_solid_breps < 1:
        raise ArtifactValidationError(f"STEP artifact '{file_path.name}' contains no MANIFOLD_SOLID_BREP entities.")

    if result.unit_scale_to_mm is None:
        raise ArtifactValidationError(f"STEP artifact '{file_path.name}' has unresolved length units.")

    if result.bounding_box_mm is None:
        raise ArtifactValidationError(f"STEP artifact '{file_path.name}' has no resolvable 3D coordinate envelope.")

    bbox = result.bounding_box_mm
    coords = (bbox.min_x, bbox.max_x, bbox.min_y, bbox.max_y, bbox.min_z, bbox.max_z)
    spans = (bbox.size_x, bbox.size_y, bbox.size_z)
    if not all(math.isfinite(c) for c in coords) or not all(math.isfinite(s) for s in spans):
        raise ArtifactValidationError(f"STEP artifact '{file_path.name}' contains non-finite bounding coordinates.")

    if any(s < 0 for s in spans) or sum(1 for s in spans if s > 0) < 2:
        raise ArtifactValidationError(
            f"STEP artifact '{file_path.name}' has degenerate zero or negative bounding span "
            f"({bbox.size_x} x {bbox.size_y} x {bbox.size_z} mm)."
        )

    return size_bytes


# ---------------------------------------------------------------------------
# STL Validation
# ---------------------------------------------------------------------------


def validate_stl_artifact(
    file_path: Path,
    max_bytes: int = MAX_STL_BYTES,
    max_ascii_lines: int = MAX_ASCII_STL_LINES,
) -> int:
    """Validate bounded STL artifact (binary preferred via count/length structure; ASCII fallback)."""
    size_bytes = validate_common_file(file_path, ".stl")

    if size_bytes > max_bytes:
        raise ArtifactValidationError(
            f"STL artifact '{file_path.name}' exceeds maximum allowed size ({size_bytes} > {max_bytes} bytes)."
        )

    # 1. Attempt binary STL validation first via exact header + count + facet length formula
    if size_bytes >= MIN_BINARY_STL_BYTES:
        try:
            with open(file_path, "rb") as f:
                header = f.read(MIN_BINARY_STL_BYTES)
                if len(header) == MIN_BINARY_STL_BYTES:
                    (triangle_count,) = struct.unpack("<I", header[80:84])
                    expected_binary_size = 84 + 50 * triangle_count
                    if expected_binary_size == size_bytes and triangle_count >= 1:
                        # Verify all triangles in bounded batches of up to 1,000 facets (50 KB)
                        facets_remaining = triangle_count
                        while facets_remaining > 0:
                            batch_count = min(facets_remaining, 1000)
                            batch_bytes = f.read(batch_count * 50)
                            if len(batch_bytes) != batch_count * 50:
                                raise ArtifactValidationError(f"Binary STL artifact '{file_path.name}' is truncated.")
                            for entry in struct.iter_unpack("<12fH", batch_bytes):
                                if not all(math.isfinite(val) for val in entry[:12]):
                                    raise ArtifactValidationError(
                                        f"Binary STL artifact '{file_path.name}' contains non-finite facet floats."
                                    )
                            facets_remaining -= batch_count
                        return size_bytes
        except struct.error:
            pass  # Fall through to ASCII scan
        except OSError as exc:
            raise ArtifactValidationError(
                f"Failed reading binary STL '{file_path.name}': {type(exc).__name__}"
            ) from exc

    # 2. Attempt ASCII STL validation
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            first_line = ""
            for _ in range(50):
                line = f.readline(MAX_ASCII_STL_LINE_CHARS + 1)
                if not line:
                    break
                stripped = line.lstrip("\ufeff").strip()
                if stripped:
                    first_line = stripped
                    break

            if not first_line.lower().startswith("solid"):
                raise ArtifactValidationError(
                    f"STL artifact '{file_path.name}' is neither valid binary nor recognizable ASCII STL."
                )

            # Scan facet blocks incrementally
            triangle_count = 0
            has_endsolid = False
            lines_scanned = 0

            # Reset file pointer to beginning for complete parse
            f.seek(0)
            in_solid = False
            in_facet = False
            in_loop = False
            loop_completed = False
            vertex_count = 0

            while True:
                line = f.readline(MAX_ASCII_STL_LINE_CHARS + 1)
                if not line:
                    break
                if len(line) > MAX_ASCII_STL_LINE_CHARS and not line.endswith("\n"):
                    raise ArtifactValidationError(
                        f"ASCII STL artifact '{file_path.name}' exceeds maximum line length ({MAX_ASCII_STL_LINE_CHARS} chars)."
                    )

                lines_scanned += 1
                if lines_scanned > max_ascii_lines:
                    raise ArtifactValidationError(
                        f"ASCII STL artifact '{file_path.name}' exceeds maximum line limit ({max_ascii_lines})."
                    )

                stripped = line.lstrip("\ufeff").strip()
                if not stripped:
                    continue

                if has_endsolid:
                    raise ArtifactValidationError(
                        f"Malformed ASCII STL '{file_path.name}': unexpected non-blank content after endsolid."
                    )

                parts = stripped.split()
                first_tok = parts[0].lower()

                if first_tok == "solid":
                    if in_solid:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': duplicate or nested solid declaration."
                        )
                    in_solid = True
                elif not in_solid:
                    raise ArtifactValidationError(
                        f"Malformed ASCII STL '{file_path.name}': content encountered before solid declaration."
                    )
                elif first_tok == "facet":
                    if in_facet:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': nested facet declaration."
                        )
                    in_facet = True
                    in_loop = False
                    loop_completed = False
                    vertex_count = 0
                elif first_tok == "outer":
                    if len(parts) != 2 or parts[1].lower() != "loop":
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': invalid outer loop declaration."
                        )
                    if not in_facet or in_loop or loop_completed:
                        raise ArtifactValidationError(f"Malformed ASCII STL '{file_path.name}': unexpected outer loop.")
                    in_loop = True
                    vertex_count = 0
                elif first_tok == "vertex":
                    if not in_loop:
                        raise ArtifactValidationError(f"Malformed ASCII STL '{file_path.name}': vertex outside loop.")
                    if len(parts) != 4:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': invalid vertex line format."
                        )
                    try:
                        coords = [float(parts[1]), float(parts[2]), float(parts[3])]
                        if not all(math.isfinite(c) for c in coords):
                            raise ArtifactValidationError(
                                f"ASCII STL '{file_path.name}' contains non-finite vertex float."
                            )
                    except ValueError as exc:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': non-numeric vertex coordinates."
                        ) from exc
                    vertex_count += 1
                elif first_tok == "endloop":
                    if len(parts) != 1:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': invalid endloop line format."
                        )
                    if not in_loop or vertex_count != 3:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': endloop without exactly 3 vertices."
                        )
                    in_loop = False
                    loop_completed = True
                elif first_tok == "endfacet":
                    if len(parts) != 1:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': invalid endfacet line format."
                        )
                    if not in_facet or in_loop or not loop_completed:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': endfacet without a completed 3-vertex loop."
                        )
                    in_facet = False
                    loop_completed = False
                    triangle_count += 1
                elif first_tok == "endsolid":
                    if in_facet or in_loop:
                        raise ArtifactValidationError(
                            f"Malformed ASCII STL '{file_path.name}': unclosed facet or loop before endsolid."
                        )
                    has_endsolid = True
                else:
                    raise ArtifactValidationError(
                        f"Malformed ASCII STL '{file_path.name}': unrecognized token on line {lines_scanned}."
                    )

            if not in_solid or not has_endsolid:
                raise ArtifactValidationError(
                    f"ASCII STL artifact '{file_path.name}' missing closing 'endsolid' statement."
                )

            if triangle_count < 1:
                raise ArtifactValidationError(f"ASCII STL artifact '{file_path.name}' contains 0 triangular facets.")

            return size_bytes

    except OSError as exc:
        raise ArtifactValidationError(f"Failed reading ASCII STL '{file_path.name}': {type(exc).__name__}") from exc


# ---------------------------------------------------------------------------
# JPG Preview Validation
# ---------------------------------------------------------------------------


def validate_jpg_artifact(
    file_path: Path,
    min_bytes: int = MIN_JPG_SIZE_BYTES,
    max_bytes: int = MAX_JPG_SIZE_BYTES,
) -> int:
    """Validate lightweight JPEG preview file: regular file, non-empty, size limits, and SOI/EOI markers."""
    size_bytes = validate_common_file(file_path, (".jpg", ".jpeg"))

    if size_bytes < min_bytes:
        raise ArtifactValidationError(
            f"Preview JPEG artifact '{file_path.name}' is too small ({size_bytes} < {min_bytes} bytes)."
        )

    if size_bytes > max_bytes:
        raise ArtifactValidationError(
            f"Preview JPEG artifact '{file_path.name}' exceeds maximum size ({size_bytes} > {max_bytes} bytes)."
        )

    try:
        with open(file_path, "rb") as f:
            soi = f.read(2)
            if soi != JPEG_SOI:
                raise ArtifactValidationError(
                    f"Preview JPEG artifact '{file_path.name}' missing valid SOI marker (0xFFD8)."
                )

            f.seek(-2, 2)  # 2 bytes from EOF
            eoi = f.read(2)
            if eoi != JPEG_EOI:
                raise ArtifactValidationError(
                    f"Preview JPEG artifact '{file_path.name}' missing valid EOI marker (0xFFD9)."
                )
    except OSError as exc:
        raise ArtifactValidationError(
            f"Failed reading JPEG bytes from '{file_path.name}': {type(exc).__name__}"
        ) from exc

    return size_bytes


# ---------------------------------------------------------------------------
# Unified Artifact Dispatcher
# ---------------------------------------------------------------------------


def validate_artifact_file(
    format_id: ArtifactFormat | Literal["jpg"],
    file_path: Path,
    inspection_report: StandardInspectionReport | None = None,
) -> ValidatedArtifact:
    """Validate artifact file by format and compute its lowercase SHA-256 and byte size with stability check."""
    norm_fmt = str(format_id).strip().lower()
    if norm_fmt not in ("par", "step", "stl", "jpg"):
        raise ArtifactValidationError(f"Unsupported artifact format: '{format_id}'")

    snap_before = FileSnapshot.capture(file_path)

    if norm_fmt == "par":
        validate_par_artifact(file_path, inspection_report)
    elif norm_fmt == "step":
        validate_step_artifact(file_path)
    elif norm_fmt == "stl":
        validate_stl_artifact(file_path)
    elif norm_fmt == "jpg":
        validate_jpg_artifact(file_path)

    sha256, streamed_size = compute_file_sha256_and_size(file_path)

    if streamed_size != snap_before.st_size:
        raise ArtifactValidationError(
            f"Streamed size mismatch for '{file_path.name}': initial {snap_before.st_size} bytes, streamed {streamed_size} bytes"
        )

    snap_after = FileSnapshot.capture(file_path)
    if snap_before != snap_after:
        raise ArtifactValidationError(f"Artifact file modified during validation and hashing: '{file_path.name}'")

    return ValidatedArtifact(sha256, streamed_size, snap_after)


__all__ = [
    "DEFAULT_HASH_CHUNK_SIZE",
    "JPEG_EOI",
    "JPEG_SOI",
    "MAX_ASCII_STL_LINES",
    "MAX_ASCII_STL_LINE_CHARS",
    "MAX_JPG_SIZE_BYTES",
    "MAX_STEP_TEXT_CHARS",
    "MAX_STL_BYTES",
    "MIN_BINARY_STL_BYTES",
    "MIN_JPG_SIZE_BYTES",
    "ArtifactValidationError",
    "FileSnapshot",
    "ValidatedArtifact",
    "compute_file_sha256_and_size",
    "validate_artifact_file",
    "validate_common_file",
    "validate_jpg_artifact",
    "validate_par_artifact",
    "validate_step_artifact",
    "validate_stl_artifact",
]
