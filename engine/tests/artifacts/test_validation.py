"""Unit tests for pure, bounded artifact validation routines and streaming checksums.

Invariants:
    - Verifies all explicit limits: MAX_STEP_TEXT_CHARS, MAX_STL_BYTES, MAX_JPG_SIZE_BYTES, MAX_ASCII_STL_LINE_CHARS.
    - Tests UTF-8 with replacement on invalid bytes for STEP Part 21.
    - Tests binary STL detection via count/length structure even if starting with "solid".
    - Tests binary STL full facet finite float validation (not just facet 1).
    - Tests ASCII STL facet framing, completed 3-vertex loop requirement, triangle count, and finite vertex checks.
    - Tests JPEG SOI/EOI marker checks and size thresholds without third-party image libraries.
    - Tests streaming SHA-256 and byte sizes with O(1) memory and positive chunk size assertion.
    - Tests sanitized error reporting: no raw file contents, no absolute paths.
    - Tests fail-closed symlink and Windows reparse point rejection.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

from artifacts.validation import (
    DEFAULT_HASH_CHUNK_SIZE,
    JPEG_EOI,
    JPEG_SOI,
    MAX_ASCII_STL_LINE_CHARS,
    MIN_JPG_SIZE_BYTES,
    ArtifactValidationError,
    ValidatedArtifact,
    compute_file_sha256_and_size,
    validate_artifact_file,
    validate_common_file,
    validate_jpg_artifact,
    validate_par_artifact,
    validate_step_artifact,
    validate_stl_artifact,
)
from interfaces.models import StandardInspectionReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_minimal_valid_step_content(
    length_mm: float = 30.0,
    width_mm: float = 20.0,
    height_mm: float = 10.0,
    extra_comments: str = "",
) -> str:
    """Create a syntactically valid ISO-10303-21 STEP string with a MANIFOLD_SOLID_BREP."""
    return f"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('CAD Copilot Test STEP'),'2;1');
FILE_NAME('test.step','2026-09-02T12:00:00',('Tester'),('CAD Copilot'),'Preprocessor','OriginatingSystem','Authorization');
FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));
ENDSEC;
DATA;
{extra_comments}
#1 = CARTESIAN_POINT('',(0.0,0.0,0.0));
#2 = CARTESIAN_POINT('',({length_mm},{width_mm},{height_mm}));
#10 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );
#20 = MANIFOLD_SOLID_BREP('Body1',#30);
#30 = CLOSED_SHELL('Shell1',());
#40 = ADVANCED_FACE('Face1',(),#50,.T.);
#50 = PLANE('Plane1',#60);
#60 = AXIS2_PLACEMENT_3D('Placement1',#1,#70,#80);
#70 = DIRECTION('Axis',(0.0,0.0,1.0));
#80 = DIRECTION('RefDirection',(1.0,0.0,0.0));
ENDSEC;
END-ISO-10303-21;
"""


def _create_binary_stl_bytes(
    triangle_count: int = 1,
    header_prefix: bytes = b"CAD Copilot Binary STL",
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
    v1: tuple[float, float, float] = (0.0, 0.0, 0.0),
    v2: tuple[float, float, float] = (10.0, 0.0, 0.0),
    v3: tuple[float, float, float] = (0.0, 10.0, 0.0),
    truncate_bytes: int = 0,
) -> bytes:
    """Create binary STL byte sequence with 80-byte header and triangle_count facets."""
    header = header_prefix.ljust(80, b"\x00")[:80]
    data = bytearray(header)
    data.extend(struct.pack("<I", triangle_count))

    for _ in range(triangle_count):
        data.extend(struct.pack("<3f", *normal))
        data.extend(struct.pack("<3f", *v1))
        data.extend(struct.pack("<3f", *v2))
        data.extend(struct.pack("<3f", *v3))
        data.extend(struct.pack("<H", 0))  # attribute byte count

    if truncate_bytes > 0:
        return bytes(data[:-truncate_bytes])
    return bytes(data)


def _create_valid_ascii_stl_content(
    name: str = "test_mesh",
    v1: tuple[float, float, float] = (0.0, 0.0, 0.0),
    v2: tuple[float, float, float] = (10.0, 0.0, 0.0),
    v3: tuple[float, float, float] = (0.0, 10.0, 0.0),
) -> str:
    """Create valid ASCII STL text representation with 1 facet."""
    return f"""solid {name}
  facet normal 0.0 0.0 1.0
    outer loop
      vertex {v1[0]} {v1[1]} {v1[2]}
      vertex {v2[0]} {v2[1]} {v2[2]}
      vertex {v3[0]} {v3[1]} {v3[2]}
    endloop
  endfacet
endsolid {name}
"""


def _create_valid_jpeg_bytes(length: int = 256) -> bytes:
    """Create minimal plausible JPEG file byte sequence with SOI and EOI markers."""
    assert length >= MIN_JPG_SIZE_BYTES
    body = b"\x00" * (length - 4)
    return JPEG_SOI + body + JPEG_EOI


# ---------------------------------------------------------------------------
# 1. Streaming SHA-256 and Size Tests
# ---------------------------------------------------------------------------


def test_compute_file_sha256_and_size_matches_hashlib(tmp_path: Path) -> None:
    """Proves compute_file_sha256_and_size calculates exact SHA-256 hex and size."""
    file_path = tmp_path / "sample.bin"
    payload = b"CAD Copilot pure artifact validation and checksum test string."
    file_path.write_bytes(payload)

    expected_sha256 = hashlib.sha256(payload).hexdigest().lower()
    sha256, size = compute_file_sha256_and_size(file_path)

    assert sha256 == expected_sha256
    assert size == len(payload)


def test_compute_file_sha256_and_size_streaming_large_file(tmp_path: Path) -> None:
    """Proves streaming computation processes files larger than chunk size."""
    file_path = tmp_path / "large.bin"
    # Create 150 KiB file (greater than 64 KiB chunk size)
    payload = b"X" * (DEFAULT_HASH_CHUNK_SIZE * 2 + 1024)
    file_path.write_bytes(payload)

    expected_sha256 = hashlib.sha256(payload).hexdigest().lower()
    sha256, size = compute_file_sha256_and_size(file_path)

    assert sha256 == expected_sha256
    assert size == len(payload)


def test_compute_file_sha256_and_size_rejects_non_positive_chunk_size(tmp_path: Path) -> None:
    """Proves compute_file_sha256_and_size rejects non-positive chunk sizes."""
    file_path = tmp_path / "sample.bin"
    file_path.write_bytes(b"DATA")

    with pytest.raises(ValueError, match="chunk_size must be strictly positive"):
        compute_file_sha256_and_size(file_path, chunk_size=0)

    with pytest.raises(ValueError, match="chunk_size must be strictly positive"):
        compute_file_sha256_and_size(file_path, chunk_size=-1)


def test_compute_file_sha256_and_size_nonexistent_file_raises(tmp_path: Path) -> None:
    """Proves compute_file_sha256_and_size raises ArtifactValidationError with sanitized error."""
    with pytest.raises(ArtifactValidationError) as exc_info:
        compute_file_sha256_and_size(tmp_path / "missing.bin")
    err_msg = str(exc_info.value)
    assert "Failed to read file for hashing at 'missing.bin': FileNotFoundError" in err_msg
    # Proves no full path is leaked in exception message
    assert str(tmp_path) not in err_msg


# ---------------------------------------------------------------------------
# 2. Common Regular-File Gate Tests
# ---------------------------------------------------------------------------


def test_validate_common_file_success(tmp_path: Path) -> None:
    """Proves validate_common_file succeeds on a regular non-empty file with matching extension."""
    file_path = tmp_path / "model.step"
    file_path.write_bytes(b"DATA")
    size = validate_common_file(file_path, ".step")
    assert size == 4


def test_validate_common_file_case_insensitive_extension(tmp_path: Path) -> None:
    """Proves validate_common_file accepts uppercase extension matching lowercase expected."""
    file_path = tmp_path / "MODEL.PAR"
    file_path.write_bytes(b"BINARY_PAR")
    size = validate_common_file(file_path, "par")
    assert size == 10


def test_validate_common_file_rejects_missing_file(tmp_path: Path) -> None:
    """Proves validate_common_file raises when file does not exist."""
    with pytest.raises(ArtifactValidationError, match="does not exist"):
        validate_common_file(tmp_path / "nonexistent.par", ".par")


def test_validate_common_file_rejects_directory(tmp_path: Path) -> None:
    """Proves validate_common_file raises when path is a directory."""
    dir_path = tmp_path / "folder.step"
    dir_path.mkdir()
    with pytest.raises(ArtifactValidationError, match="not a regular file"):
        validate_common_file(dir_path, ".step")


def test_validate_common_file_rejects_empty_file(tmp_path: Path) -> None:
    """Proves validate_common_file raises when file is 0 bytes."""
    empty_file = tmp_path / "empty.stl"
    empty_file.write_bytes(b"")
    with pytest.raises(ArtifactValidationError, match=r"empty \(0 bytes\)"):
        validate_common_file(empty_file, ".stl")


def test_validate_common_file_rejects_wrong_extension(tmp_path: Path) -> None:
    """Proves validate_common_file raises when extension does not match."""
    file_path = tmp_path / "model.txt"
    file_path.write_bytes(b"some content")
    with pytest.raises(ArtifactValidationError, match="extension mismatch"):
        validate_common_file(file_path, ".step")


def test_validate_common_file_fails_closed_on_indeterminate_link_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves validate_common_file fails closed if lstat raises OSError."""
    file_path = tmp_path / "model.step"
    file_path.write_bytes(b"DATA")

    def mock_lstat(self: Path) -> Any:
        raise OSError("Simulated permission error")

    monkeypatch.setattr(Path, "lstat", mock_lstat)
    with pytest.raises(ArtifactValidationError, match=r"Cannot verify file status for 'model\.step': OSError"):
        validate_common_file(file_path, ".step")


def test_validate_common_file_rejects_reparse_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves validate_common_file rejects files flagged as reparse points on Windows."""
    file_path = tmp_path / "model.step"
    file_path.write_bytes(b"DATA")

    orig_lstat = file_path.lstat()

    class FakeStat:
        st_mode = orig_lstat.st_mode
        st_file_attributes = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(Path, "lstat", lambda self: FakeStat())
    with pytest.raises(ArtifactValidationError, match="must not be a reparse point or link"):
        validate_common_file(file_path, ".step")


# ---------------------------------------------------------------------------
# 3. Native Solid Edge PAR Validation Tests
# ---------------------------------------------------------------------------


def test_validate_par_artifact_success(tmp_path: Path) -> None:
    """Proves validate_par_artifact succeeds with valid file and 1-solid inspection report."""
    par_path = tmp_path / "model.par"
    par_path.write_bytes(b"\x00" * 512)

    report = StandardInspectionReport(
        volume_mm3=160000.0,
        mass_kg=1.0,
        feature_count=1,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )
    size = validate_par_artifact(par_path, report)
    assert size == 512


def test_validate_par_artifact_success_with_none_sheet_wire_counts(tmp_path: Path) -> None:
    """Proves validate_par_artifact succeeds when sheet and wire body counts are unspecified (None)."""
    par_path = tmp_path / "model.par"
    par_path.write_bytes(b"\x00" * 512)

    report = StandardInspectionReport(
        volume_mm3=160000.0,
        mass_kg=1.0,
        feature_count=1,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=None,
        wire_body_count=None,
    )
    size = validate_par_artifact(par_path, report)
    assert size == 512


def test_validate_par_artifact_missing_report_raises(tmp_path: Path) -> None:
    """Proves validate_par_artifact raises when inspection report is None."""
    par_path = tmp_path / "model.par"
    par_path.write_bytes(b"\x00" * 512)
    with pytest.raises(ArtifactValidationError, match="requires a valid CAD inspection report"):
        validate_par_artifact(par_path, None)


@pytest.mark.parametrize(
    "solids,sheets,wires,vol,err_match",
    [
        (0, 0, 0, 1000.0, "must have exactly 1 solid body, got 0"),
        (2, 0, 0, 1000.0, "must have exactly 1 solid body, got 2"),
        (1, 1, 0, 1000.0, "contains invalid sheet or wire bodies"),
        (1, 0, 1, 1000.0, "contains invalid sheet or wire bodies"),
        (1, 0, 0, 0.0, "has non-positive or non-finite volume"),
        (1, 0, 0, -50.0, "has non-positive or non-finite volume"),
        (1, 0, 0, float("nan"), "has non-positive or non-finite volume"),
        (1, 0, 0, float("inf"), "has non-positive or non-finite volume"),
    ],
)
def test_validate_par_artifact_rejects_unhealthy_inspection_reports(
    tmp_path: Path,
    solids: int,
    sheets: int,
    wires: int,
    vol: float,
    err_match: str,
) -> None:
    """Proves validate_par_artifact enforces 1 solid body, 0 sheet, 0 wire, and finite positive volume."""
    par_path = tmp_path / "model.par"
    par_path.write_bytes(b"\x00" * 256)
    report = StandardInspectionReport(
        volume_mm3=vol,
        mass_kg=1.0,
        feature_count=1,
        body_count=1,
        solid_body_count=solids,
        sheet_body_count=sheets,
        wire_body_count=wires,
    )
    with pytest.raises(ArtifactValidationError, match=err_match):
        validate_par_artifact(par_path, report)


def test_validate_par_artifact_rejects_inconsistent_total_body_count(tmp_path: Path) -> None:
    """Proves validate_par_artifact rejects reports where body_count != 1 even if solid_body_count == 1."""
    par_path = tmp_path / "model.par"
    par_path.write_bytes(b"\x00" * 256)
    report = StandardInspectionReport(
        volume_mm3=1000.0,
        mass_kg=1.0,
        feature_count=1,
        body_count=2,  # Inconsistent: 2 total bodies
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )
    with pytest.raises(ArtifactValidationError, match="must have exactly 1 total body"):
        validate_par_artifact(par_path, report)


# ---------------------------------------------------------------------------
# 4. STEP Validation Tests
# ---------------------------------------------------------------------------


def test_validate_step_artifact_success(tmp_path: Path) -> None:
    """Proves validate_step_artifact succeeds with valid Part 21 text."""
    step_path = tmp_path / "model.step"
    step_path.write_text(_create_minimal_valid_step_content(), encoding="utf-8")
    size = validate_step_artifact(step_path)
    assert size > 0


def test_validate_step_artifact_utf8_replacement_on_invalid_bytes(tmp_path: Path) -> None:
    """Proves STEP reader decodes with utf-8 replacement on invalid bytes without crashing."""
    step_path = tmp_path / "model_corrupt_utf8.step"
    valid_step = _create_minimal_valid_step_content()
    # Inject non-UTF-8 bytes (0x80, 0xFF) inside a comment
    corrupted_step_bytes = (
        valid_step.replace("CAD Copilot Test STEP", "CAD Copilot Test STEP \x80\xff\xfe invalid bytes").encode(
            "utf-8", errors="replace"
        )
        + b"/* trailing invalid: \x80\xff */"
    )
    step_path.write_bytes(corrupted_step_bytes)

    # Must succeed via encoding="utf-8", errors="replace"
    size = validate_step_artifact(step_path)
    assert size > 0


def test_validate_step_artifact_rejects_oversized_text(tmp_path: Path) -> None:
    """Proves validate_step_artifact rejects files exceeding max_chars bound before full parsing."""
    step_path = tmp_path / "oversized.step"
    step_path.write_text(_create_minimal_valid_step_content(), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="exceeds maximum allowed text length"):
        validate_step_artifact(step_path, max_chars=100)


def test_validate_step_artifact_rejects_missing_envelope(tmp_path: Path) -> None:
    """Proves validate_step_artifact rejects corrupted STEP headers/envelope."""
    step_path = tmp_path / "broken.step"
    step_path.write_text("NOT A STEP FILE\nHEADER;\nENDSEC;\n", encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="STEP validation failed"):
        validate_step_artifact(step_path)


def test_validate_step_artifact_rejects_missing_solid_brep(tmp_path: Path) -> None:
    """Proves validate_step_artifact rejects STEP files lacking a MANIFOLD_SOLID_BREP."""
    step_path = tmp_path / "no_brep.step"
    text = _create_minimal_valid_step_content().replace("MANIFOLD_SOLID_BREP", "SHELL_BASED_SURFACE_MODEL")
    step_path.write_text(text, encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="contains no MANIFOLD_SOLID_BREP entities"):
        validate_step_artifact(step_path)


def test_validate_step_artifact_rejects_degenerate_bounding_box(tmp_path: Path) -> None:
    """Proves validate_step_artifact rejects STEP files with degenerate zero-span bounding boxes."""
    step_path = tmp_path / "degenerate.step"
    # Point 1 and Point 2 are identical -> size_x = size_y = size_z = 0.0
    text = _create_minimal_valid_step_content(length_mm=0.0, width_mm=0.0, height_mm=0.0)
    step_path.write_text(text, encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="degenerate zero or negative bounding span"):
        validate_step_artifact(step_path)


# ---------------------------------------------------------------------------
# 5. STL Validation Tests
# ---------------------------------------------------------------------------


def test_validate_stl_artifact_binary_success(tmp_path: Path) -> None:
    """Proves validate_stl_artifact validates binary STL via header + count + facet length formula."""
    stl_path = tmp_path / "model.stl"
    stl_bytes = _create_binary_stl_bytes(triangle_count=2)
    stl_path.write_bytes(stl_bytes)
    assert len(stl_bytes) == 84 + 50 * 2  # 184 bytes

    size = validate_stl_artifact(stl_path)
    assert size == 184


def test_validate_stl_artifact_binary_starting_with_solid_detected_as_binary(tmp_path: Path) -> None:
    """Proves binary STL starting with 'solid' is detected as binary via count/length structure."""
    stl_path = tmp_path / "solid_binary.stl"
    # Header starts with "solid my_binary_part", which looks like ASCII to naive detectors
    stl_bytes = _create_binary_stl_bytes(triangle_count=1, header_prefix=b"solid my_binary_part")
    stl_path.write_bytes(stl_bytes)

    size = validate_stl_artifact(stl_path)
    assert size == 134


def test_validate_stl_artifact_binary_truncated_rejected(tmp_path: Path) -> None:
    """Proves binary STL with truncated facet data is rejected."""
    stl_path = tmp_path / "truncated.stl"
    # Triangle count says 1 (needs 134 bytes), but we truncate 10 bytes
    stl_bytes = _create_binary_stl_bytes(triangle_count=1, truncate_bytes=10)
    stl_path.write_bytes(stl_bytes)

    with pytest.raises(ArtifactValidationError, match="neither valid binary nor recognizable ASCII STL"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_binary_non_finite_coordinates_rejected(tmp_path: Path) -> None:
    """Proves binary STL containing NaN coordinates is rejected."""
    stl_path = tmp_path / "nan_binary.stl"
    stl_bytes = _create_binary_stl_bytes(
        triangle_count=1,
        v1=(float("nan"), 0.0, 0.0),
    )
    stl_path.write_bytes(stl_bytes)

    with pytest.raises(ArtifactValidationError, match="contains non-finite facet floats"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_binary_rejects_nan_in_subsequent_triangle(tmp_path: Path) -> None:
    """Proves binary STL validation scans all facets and rejects NaN in later triangles."""
    stl_path = tmp_path / "second_triangle_nan.stl"
    header = b"CAD Copilot Binary STL".ljust(80, b"\x00")[:80]
    data = bytearray(header)
    data.extend(struct.pack("<I", 2))
    # Triangle 1: valid
    data.extend(struct.pack("<3f", 0.0, 0.0, 1.0))
    data.extend(struct.pack("<3f", 0.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 10.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 0.0, 10.0, 0.0))
    data.extend(struct.pack("<H", 0))
    # Triangle 2: NaN vertex float
    data.extend(struct.pack("<3f", 0.0, 0.0, 1.0))
    data.extend(struct.pack("<3f", 0.0, float("nan"), 0.0))
    data.extend(struct.pack("<3f", 10.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 0.0, 10.0, 0.0))
    data.extend(struct.pack("<H", 0))
    stl_path.write_bytes(bytes(data))

    with pytest.raises(ArtifactValidationError, match="contains non-finite facet floats"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_ascii_success(tmp_path: Path) -> None:
    """Proves validate_stl_artifact validates well-formed ASCII STL with 1 facet."""
    stl_path = tmp_path / "model_ascii.stl"
    stl_path.write_text(_create_valid_ascii_stl_content(), encoding="utf-8")

    size = validate_stl_artifact(stl_path)
    assert size > 0


def test_validate_stl_artifact_ascii_zero_facets_rejected(tmp_path: Path) -> None:
    """Proves ASCII STL with 0 facets is rejected."""
    stl_path = tmp_path / "empty_ascii.stl"
    stl_path.write_text("solid empty\nendsolid empty\n", encoding="utf-8")

    with pytest.raises(ArtifactValidationError, match="contains 0 triangular facets"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_ascii_rejects_empty_facet(tmp_path: Path) -> None:
    """Proves ASCII STL with empty facet (no loop or vertices) is rejected."""
    stl_path = tmp_path / "empty_facet.stl"
    bad_content = """solid empty_facet
  facet normal 0 0 1
  endfacet
endsolid empty_facet
"""
    stl_path.write_text(bad_content, encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="endfacet without a completed 3-vertex loop"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_ascii_rejects_unfinished_facet_before_endsolid(tmp_path: Path) -> None:
    """Proves ASCII STL with unfinished facet before endsolid is rejected."""
    stl_path = tmp_path / "unfinished_facet.stl"
    bad_content = """solid unfinished
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 10 0 0
      vertex 0 10 0
    endloop
  endfacet
  facet normal 0 0 1
endsolid unfinished
"""
    stl_path.write_text(bad_content, encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="unclosed facet or loop before endsolid"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_ascii_rejects_line_exceeding_bound(tmp_path: Path) -> None:
    """Proves ASCII STL line exceeding MAX_ASCII_STL_LINE_CHARS is rejected."""
    stl_path = tmp_path / "long_line.stl"
    long_line = "solid " + ("A" * (MAX_ASCII_STL_LINE_CHARS + 50))
    stl_path.write_text(long_line, encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="exceeds maximum line length"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_ascii_invalid_vertex_format_sanitized(tmp_path: Path) -> None:
    """Proves ASCII STL invalid vertex line formats do not echo raw line content into error."""
    stl_path = tmp_path / "bad_vertex.stl"
    secret_text = "SECRET_CAD_COORDINATE_PAYLOAD"
    bad_content = f"""solid test
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 1 1 {secret_text}
      vertex 2 2 2
    endloop
  endfacet
endsolid test
"""
    stl_path.write_text(bad_content, encoding="utf-8")
    with pytest.raises(ArtifactValidationError) as exc_info:
        validate_stl_artifact(stl_path)
    assert secret_text not in str(exc_info.value)
    assert "invalid vertex line format" in str(exc_info.value)


def test_validate_stl_artifact_ascii_missing_endsolid_rejected(tmp_path: Path) -> None:
    """Proves ASCII STL missing closing endsolid is rejected."""
    stl_path = tmp_path / "missing_endsolid.stl"
    bad_content = """solid broken
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 0 1 0
    endloop
  endfacet
"""
    stl_path.write_text(bad_content, encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="missing closing 'endsolid' statement"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_ascii_non_finite_vertex_rejected(tmp_path: Path) -> None:
    """Proves ASCII STL with NaN or Inf vertex coordinates is rejected."""
    stl_path = tmp_path / "nan_ascii.stl"
    stl_path.write_text(_create_valid_ascii_stl_content(v1=(10.0, float("nan"), 5.0)), encoding="utf-8")

    with pytest.raises(ArtifactValidationError, match="contains non-finite vertex float"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_rejects_oversized_file(tmp_path: Path) -> None:
    """Proves validate_stl_artifact enforces MAX_STL_BYTES ceiling."""
    stl_path = tmp_path / "oversized.stl"
    stl_path.write_bytes(b"\x00" * 1024)
    with pytest.raises(ArtifactValidationError, match="exceeds maximum allowed size"):
        validate_stl_artifact(stl_path, max_bytes=512)


# ---------------------------------------------------------------------------
# 6. JPG Validation Tests
# ---------------------------------------------------------------------------


def test_validate_jpg_artifact_success(tmp_path: Path) -> None:
    """Proves validate_jpg_artifact succeeds on valid JPEG with SOI/EOI markers and minimum size."""
    jpg_path = tmp_path / "preview.jpg"
    jpg_path.write_bytes(_create_valid_jpeg_bytes(length=200))

    size = validate_jpg_artifact(jpg_path)
    assert size == 200


def test_validate_jpg_artifact_jpeg_extension_accepted(tmp_path: Path) -> None:
    """Proves validate_jpg_artifact accepts .jpeg extension."""
    jpg_path = tmp_path / "preview.jpeg"
    jpg_path.write_bytes(_create_valid_jpeg_bytes(length=150))

    size = validate_jpg_artifact(jpg_path)
    assert size == 150


def test_validate_jpg_artifact_rejects_too_small_file(tmp_path: Path) -> None:
    """Proves validate_jpg_artifact rejects files smaller than MIN_JPG_SIZE_BYTES."""
    jpg_path = tmp_path / "small.jpg"
    # 20 bytes (SOI + 16 zeroes + EOI) < 100 bytes minimum
    jpg_path.write_bytes(JPEG_SOI + b"\x00" * 16 + JPEG_EOI)

    with pytest.raises(ArtifactValidationError, match="is too small"):
        validate_jpg_artifact(jpg_path)


def test_validate_jpg_artifact_rejects_missing_soi(tmp_path: Path) -> None:
    """Proves validate_jpg_artifact rejects files lacking SOI marker 0xFFD8."""
    jpg_path = tmp_path / "bad_soi.jpg"
    # Valid length and EOI, but bad SOI
    jpg_path.write_bytes(b"\x00\x00" + b"\x00" * 100 + JPEG_EOI)

    with pytest.raises(ArtifactValidationError, match="missing valid SOI marker"):
        validate_jpg_artifact(jpg_path)


def test_validate_jpg_artifact_rejects_missing_eoi(tmp_path: Path) -> None:
    """Proves validate_jpg_artifact rejects truncated files lacking EOI marker 0xFFD9."""
    jpg_path = tmp_path / "bad_eoi.jpg"
    # Valid SOI and length, but missing EOI
    jpg_path.write_bytes(JPEG_SOI + b"\x00" * 100 + b"\x00\x00")

    with pytest.raises(ArtifactValidationError, match="missing valid EOI marker"):
        validate_jpg_artifact(jpg_path)


def test_validate_jpg_artifact_rejects_oversized_file(tmp_path: Path) -> None:
    """Proves validate_jpg_artifact enforces MAX_JPG_SIZE_BYTES ceiling."""
    jpg_path = tmp_path / "large.jpg"
    jpg_path.write_bytes(_create_valid_jpeg_bytes(length=200))

    with pytest.raises(ArtifactValidationError, match="exceeds maximum size"):
        validate_jpg_artifact(jpg_path, max_bytes=150)


def test_validate_jpg_artifact_rejects_reparse_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves validate_jpg_artifact rejects files flagged as reparse points on Windows."""
    jpg_path = tmp_path / "preview.jpg"
    jpg_path.write_bytes(_create_valid_jpeg_bytes(length=200))

    orig_lstat = jpg_path.lstat()

    class FakeStat:
        st_mode = orig_lstat.st_mode
        st_file_attributes = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(Path, "lstat", lambda self: FakeStat())
    with pytest.raises(ArtifactValidationError, match="must not be a reparse point or link"):
        validate_jpg_artifact(jpg_path)


# ---------------------------------------------------------------------------
# 7. Unified Dispatcher Tests
# ---------------------------------------------------------------------------


def test_validate_artifact_file_dispatcher_all_formats(tmp_path: Path) -> None:
    """Proves validate_artifact_file dispatches correctly across par, step, stl, and jpg."""
    # PAR
    par_file = tmp_path / "part.par"
    par_file.write_bytes(b"\x00" * 256)
    report = StandardInspectionReport(
        volume_mm3=500.0,
        mass_kg=1.0,
        feature_count=1,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )
    par_sha, par_size = validate_artifact_file("par", par_file, report)
    assert len(par_sha) == 64
    assert par_size == 256

    # STEP
    step_file = tmp_path / "geometry.step"
    step_file.write_text(_create_minimal_valid_step_content(), encoding="utf-8")
    step_sha, step_size = validate_artifact_file("step", step_file)
    assert len(step_sha) == 64
    assert step_size > 0

    # STL
    stl_file = tmp_path / "mesh.stl"
    stl_file.write_bytes(_create_binary_stl_bytes(triangle_count=1))
    stl_sha, stl_size = validate_artifact_file("stl", stl_file)
    assert len(stl_sha) == 64
    assert stl_size == 134

    # JPG
    jpg_file = tmp_path / "snapshot.jpg"
    jpg_file.write_bytes(_create_valid_jpeg_bytes(length=128))
    jpg_sha, jpg_size = validate_artifact_file("jpg", jpg_file)
    assert len(jpg_sha) == 64
    assert jpg_size == 128


def test_validate_artifact_file_dispatcher_unsupported_format(tmp_path: Path) -> None:
    """Proves validate_artifact_file rejects unsupported format IDs."""
    with pytest.raises(ArtifactValidationError, match="Unsupported artifact format: 'obj'"):
        validate_artifact_file("obj", tmp_path / "model.obj")  # type: ignore[arg-type]


def test_validate_stl_artifact_ascii_rejects_duplicate_outer_loop_in_facet(tmp_path: Path) -> None:
    """Proves ASCII STL rejecting multiple outer loop declarations inside a single facet."""
    stl_path = tmp_path / "multi_loop.stl"
    bad_content = """solid multi_loop
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 10 0 0
      vertex 0 10 0
    endloop
    outer loop
      vertex 1 1 1
      vertex 2 2 2
      vertex 3 3 3
    endloop
  endfacet
endsolid multi_loop
"""
    stl_path.write_text(bad_content, encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="unexpected outer loop"):
        validate_stl_artifact(stl_path)


def test_artifacts_package_exports_all_public_symbols() -> None:
    """Proves artifacts package exports all public validation symbols and constants."""
    import artifacts

    expected_symbols = [
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
        "compute_file_sha256_and_size",
        "validate_artifact_file",
        "validate_common_file",
        "validate_jpg_artifact",
        "validate_par_artifact",
        "validate_step_artifact",
        "validate_stl_artifact",
    ]
    for sym in expected_symbols:
        assert hasattr(artifacts, sym), f"Missing public export in artifacts: {sym}"


def test_validate_artifact_file_dispatcher_case_insensitive(tmp_path: Path) -> None:
    """Proves validate_artifact_file accepts format identifiers regardless of case or whitespace."""
    step_file = tmp_path / "model.step"
    step_file.write_text(_create_minimal_valid_step_content(), encoding="utf-8")

    # Pass uppercase and padded format string
    sha, size = validate_artifact_file("  STEP  ", step_file)  # type: ignore[arg-type]
    assert len(sha) == 64
    assert size > 0


def test_validate_step_artifact_bounds_large_error_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves validate_step_artifact caps the displayed error list to 5 items when many errors are reported."""
    from geometry.step_checker import StepValidationResult

    step_file = tmp_path / "corrupt.step"
    step_file.write_text("ISO-10303-21; HEADER; ENDSEC; DATA; ENDSEC; END-ISO-10303-21;", encoding="utf-8")

    many_errors = [f"Simulated error {i}" for i in range(12)]
    mock_result = StepValidationResult(
        is_valid=False,
        solid_brep_detected=False,
        errors=many_errors,
    )

    import artifacts.validation as val_mod

    monkeypatch.setattr(val_mod, "validate_step_text", lambda text: mock_result)

    with pytest.raises(ArtifactValidationError) as exc_info:
        validate_step_artifact(step_file)

    msg = str(exc_info.value)
    assert "Simulated error 0" in msg
    assert "Simulated error 4" in msg
    assert "and 7 more errors" in msg
    assert "Simulated error 10" not in msg


def test_validate_stl_artifact_ascii_rejects_malformed_tokens(tmp_path: Path) -> None:
    """Proves ASCII STL parser rejects lines with corrupted/appended tokens like vertexBROKEN."""
    cases = [
        (
            "vertexBROKEN 1.0 2.0 3.0",
            "solid test\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertexBROKEN 1.0 2.0 3.0\nvertex 0 1 0\nendloop\nendfacet\nendsolid test\n",
        ),
        (
            "endfacetBROKEN",
            "solid test\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacetBROKEN\nendsolid test\n",
        ),
        (
            "endloopBROKEN",
            "solid test\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloopBROKEN\nendfacet\nendsolid test\n",
        ),
        (
            "outerLOOP",
            "solid test\nfacet normal 0 0 1\nouterLOOP\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid test\n",
        ),
    ]
    for tag, content in cases:
        stl_file = tmp_path / f"corrupt_{tag[:8]}.stl"
        stl_file.write_text(content, encoding="ascii")
        with pytest.raises(ArtifactValidationError) as exc_info:
            validate_stl_artifact(stl_file)
        assert "unrecognized token on line" in str(exc_info.value) or "invalid outer loop" in str(exc_info.value)


def test_validate_stl_artifact_ascii_unrecognized_token_sanitized(tmp_path: Path) -> None:
    """Proves unrecognized token errors do not reflect raw payload content or paths into error messages."""
    secret_payload = "SECRET_ORGANIZATION_PATH_C:\\CorporateConfidential"
    stl_file = tmp_path / "secret_token.stl"
    content = f"""solid test
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 0 1 0
    endloop
  endfacet
  {secret_payload}
endsolid test
"""
    stl_file.write_text(content, encoding="ascii")
    with pytest.raises(ArtifactValidationError) as exc_info:
        validate_stl_artifact(stl_file)

    err_msg = str(exc_info.value)
    assert secret_payload not in err_msg
    assert "unrecognized token on line" in err_msg


def test_validate_stl_artifact_ascii_rejects_trailing_content_after_endsolid(tmp_path: Path) -> None:
    """Proves ASCII STL parser rejects any unexpected non-blank lines after endsolid through EOF."""
    stl_file = tmp_path / "trailing.stl"
    content = """solid test
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 0 1 0
    endloop
  endfacet
endsolid test
unexpected_trailing_content
"""
    stl_file.write_text(content, encoding="ascii")
    with pytest.raises(ArtifactValidationError) as exc_info:
        validate_stl_artifact(stl_file)
    assert "unexpected non-blank content after endsolid" in str(exc_info.value)


def test_validate_step_artifact_rejects_overflowing_infinite_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves validate_step_artifact rejects bounding boxes where subtraction overflows to infinity."""
    from geometry.step_checker import StepBoundingBox, StepValidationResult

    step_file = tmp_path / "overflow.step"
    step_file.write_text("ISO-10303-21; HEADER; ENDSEC; DATA; ENDSEC; END-ISO-10303-21;", encoding="utf-8")

    # min_x = -1e308 and max_x = 1e308 produce size_x = 2e308 -> inf
    overflow_bbox = StepBoundingBox(
        min_x=-1e308,
        max_x=1e308,
        min_y=0.0,
        max_y=10.0,
        min_z=0.0,
        max_z=10.0,
        size_x=float("inf"),
        size_y=10.0,
        size_z=10.0,
    )
    assert math.isinf(overflow_bbox.size_x)

    mock_result = StepValidationResult(
        is_valid=True,
        solid_brep_detected=True,
        unit_scale_to_mm=1.0,
        bounding_box_mm=overflow_bbox,
        signals=type("Signals", (), {"manifold_solid_breps": 1})(),
    )

    import artifacts.validation as val_mod

    monkeypatch.setattr(val_mod, "validate_step_text", lambda text: mock_result)

    with pytest.raises(ArtifactValidationError) as exc_info:
        validate_step_artifact(step_file)
    assert "contains non-finite bounding coordinates" in str(exc_info.value)


def test_validate_stl_artifact_binary_zero_triangles_rejected(tmp_path: Path) -> None:
    """Proves binary STL with uint32 count=0 is rejected as neither valid binary nor ASCII."""
    stl_path = tmp_path / "zero_triangles.stl"
    stl_bytes = _create_binary_stl_bytes(triangle_count=0)
    stl_path.write_bytes(stl_bytes)
    assert len(stl_bytes) == 84

    with pytest.raises(ArtifactValidationError, match="neither valid binary nor recognizable ASCII STL"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_binary_multi_batch_facets(tmp_path: Path) -> None:
    """Proves binary STL with > 1,000 triangles exercises multi-batch facet validation."""
    stl_path = tmp_path / "multi_batch.stl"
    triangle_count = 1005  # Crosses 1,000 facet boundary
    stl_bytes = _create_binary_stl_bytes(triangle_count=triangle_count)
    stl_path.write_bytes(stl_bytes)
    assert len(stl_bytes) == 84 + 50 * triangle_count

    size = validate_stl_artifact(stl_path)
    assert size == len(stl_bytes)


def test_validate_stl_artifact_ascii_with_utf8_bom_accepted(tmp_path: Path) -> None:
    """Proves ASCII STL files with leading UTF-8 BOM are accepted and validated."""
    stl_path = tmp_path / "bom_mesh.stl"
    ascii_content = _create_valid_ascii_stl_content()
    bom_bytes = b"\xef\xbb\xbf" + ascii_content.encode("utf-8")
    stl_path.write_bytes(bom_bytes)

    size = validate_stl_artifact(stl_path)
    assert size == len(bom_bytes)


def test_validate_common_file_tuple_extensions(tmp_path: Path) -> None:
    """Proves validate_common_file supports a tuple of allowed extensions."""
    file_path = tmp_path / "preview.jpeg"
    file_path.write_bytes(b"content")

    size = validate_common_file(file_path, (".jpg", ".jpeg"))
    assert size == 7

    with pytest.raises(
        ArtifactValidationError,
        match=r"Artifact file extension mismatch: expected '\.png' or '\.gif'",
    ):
        validate_common_file(file_path, (".png", ".gif"))


def test_validate_common_file_rejects_dangling_symlink(tmp_path: Path) -> None:
    """Proves validate_common_file rejects broken/dangling symlinks as symbolic links."""
    broken_link = tmp_path / "broken.par"
    target = tmp_path / "nonexistent.par"
    try:
        os.symlink(target, broken_link)
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")

    with pytest.raises(ArtifactValidationError) as exc_info:
        validate_common_file(broken_link, ".par")
    assert "must not be a symbolic link" in str(exc_info.value)


def test_validate_common_file_rejects_reparse_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves validate_common_file rejects files with non-zero st_reparse_tag on Windows."""
    test_file = tmp_path / "reparse_tag.step"
    test_file.write_bytes(b"content")

    orig_lstat = test_file.lstat()

    class FakeStat:
        def __getattr__(self, name: str) -> Any:
            if name == "st_reparse_tag":
                return 0xA000000C  # IO_REPARSE_TAG_FILE_PLACEHOLDER
            return getattr(orig_lstat, name)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(Path, "lstat", lambda self: FakeStat())

    with pytest.raises(ArtifactValidationError) as exc_info:
        validate_common_file(test_file, ".step")
    assert "must not be a reparse point or link" in str(exc_info.value)


def test_validate_stl_artifact_binary_count_size_mismatch(tmp_path: Path) -> None:
    """Proves binary STL with triangle count not matching file size is rejected."""
    stl_path = tmp_path / "count_mismatch.stl"
    stl_bytes = _create_binary_stl_bytes(triangle_count=1)
    bad_bytes = bytearray(stl_bytes)
    bad_bytes[80:84] = struct.pack("<I", 2)
    stl_path.write_bytes(bytes(bad_bytes))

    with pytest.raises(ArtifactValidationError, match="neither valid binary nor recognizable ASCII STL"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_binary_trailing_bytes(tmp_path: Path) -> None:
    """Proves binary STL with trailing bytes after declared facets is rejected."""
    stl_path = tmp_path / "trailing_bytes.stl"
    stl_bytes = _create_binary_stl_bytes(triangle_count=1)  # 134 bytes
    stl_path.write_bytes(stl_bytes + b"EXTRA_TRAILING_BYTES")

    with pytest.raises(ArtifactValidationError, match="neither valid binary nor recognizable ASCII STL"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_binary_overflow_triangle_count(tmp_path: Path) -> None:
    """Proves binary STL with 32-bit overflow scale triangle count is rejected."""
    stl_path = tmp_path / "overflow_count.stl"
    stl_bytes = _create_binary_stl_bytes(triangle_count=1)
    bad_bytes = bytearray(stl_bytes)
    bad_bytes[80:84] = struct.pack("<I", 0xFFFFFFFF)
    stl_path.write_bytes(bytes(bad_bytes))

    with pytest.raises(ArtifactValidationError, match="neither valid binary nor recognizable ASCII STL"):
        validate_stl_artifact(stl_path)


def test_validate_stl_artifact_ascii_max_lines_exceeded(tmp_path: Path) -> None:
    """Proves ASCII STL exceeding max_ascii_lines limit is rejected."""
    stl_path = tmp_path / "many_lines.stl"
    content = _create_valid_ascii_stl_content()
    stl_path.write_text(content, encoding="utf-8")

    with pytest.raises(ArtifactValidationError, match="exceeds maximum line limit"):
        validate_stl_artifact(stl_path, max_ascii_lines=3)


def test_validate_stl_artifact_ascii_multiple_facets_success(tmp_path: Path) -> None:
    """Proves ASCII STL with multiple well-formed facets validates successfully."""
    stl_path = tmp_path / "multi_facet.stl"
    content = """solid multi_facet
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 10 0 0
      vertex 0 10 0
    endloop
  endfacet
  facet normal 0 0 1
    outer loop
      vertex 10 0 0
      vertex 10 10 0
      vertex 0 10 0
    endloop
  endfacet
  facet normal 0 1 0
    outer loop
      vertex 0 0 0
      vertex 10 0 0
      vertex 5 0 10
    endloop
  endfacet
endsolid multi_facet
"""
    content_bytes = content.encode("utf-8")
    stl_path.write_bytes(content_bytes)
    size = validate_stl_artifact(stl_path)
    assert size == len(content_bytes)


def test_validate_artifact_file_rejects_mutation_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves that file modification between initial snapshot and hashing raises ArtifactValidationError."""
    par_path = tmp_path / "model.par"
    par_path.write_bytes(b"Initial valid content")

    report = StandardInspectionReport(
        volume_mm3=1000.0,
        mass_kg=1.0,
        feature_count=1,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )

    import artifacts.validation

    orig_validate_par = artifacts.validation.validate_par_artifact

    def _tampering_par_validator(path: Path, insp: Any) -> int:
        ret = orig_validate_par(path, insp)
        # Mutate the file immediately after format validation before hashing
        with open(path, "ab") as f:
            f.write(b"_MODIFIED")
        return ret

    monkeypatch.setattr(artifacts.validation, "validate_par_artifact", _tampering_par_validator)

    with pytest.raises(ArtifactValidationError, match=r"modified during validation|Streamed size mismatch"):
        validate_artifact_file("par", par_path, report)


def test_validate_artifact_file_returns_validated_artifact_with_snapshot(tmp_path: Path) -> None:
    """Proves validate_artifact_file returns ValidatedArtifact with accessible snapshot and transparent unpacking."""
    par_path = tmp_path / "valid.par"
    par_path.write_bytes(b"Solid Edge native model data")

    report = StandardInspectionReport(
        volume_mm3=500.0,
        mass_kg=0.5,
        feature_count=1,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )

    res = validate_artifact_file("par", par_path, report)
    # Transparent unpacking
    sha256, size_bytes = res
    assert len(sha256) == 64
    assert size_bytes == len(b"Solid Edge native model data")
    # Snapshot properties
    assert isinstance(res, ValidatedArtifact)
    assert res.snapshot.st_size == size_bytes
    assert res.snapshot.st_dev > 0 or sys.platform == "win32"
