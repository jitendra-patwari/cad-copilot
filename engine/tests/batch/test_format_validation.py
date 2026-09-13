"""Unit tests for batch format validation dispatcher, PDF/DXF structural checks, and snapshot production.

Invariants:
- Four guaranteed batch formats: step, stl, pdf, dxf.
- Exact suffix checks (.step, .stl, .pdf, .dxf).
- Regular non-reparse file requirement.
- Exact bounds: MAX_BATCH_DRAWING_BYTES = 100_000_000 (100 MB).
- Race-aware OutputSnapshot capture with positive size, nonzero inode, and valid SHA-256.
- Sanitized diagnostics with zero raw workstation paths or COM text (SEC-07).
"""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from batch.format_validation import (
    DXF_BINARY_SENTINEL,
    MAX_BATCH_DRAWING_BYTES,
    MAX_DXF_LINE_CHARS,
    MIN_PDF_BYTES,
    PARASOLID_VALIDATION_FAILED_MESSAGE,
    STEP_VALIDATION_FAILED_MESSAGE,
    STL_VALIDATION_FAILED_MESSAGE,
    BatchFormatValidationError,
    BatchFormatValidator,
    validate_batch_output,
)
from batch.models import BatchOutputFormat
from batch.output_snapshot import OutputSnapshot, capture_output_snapshot

# ---------------------------------------------------------------------------
# Test Artifact Generators
# ---------------------------------------------------------------------------


def _write_minimal_valid_step(path: Path) -> None:
    """Write syntactically valid ISO-10303-21 STEP artifact with a solid B-Rep."""
    content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('CAD Copilot Batch Test STEP'),'2;1');
FILE_NAME('test.step','2026-09-10T12:00:00',('Tester'),('CAD Copilot'),'Preprocessor','OriginatingSystem','Authorization');
FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));
ENDSEC;
DATA;
#1 = CARTESIAN_POINT('',(0.0,0.0,0.0));
#2 = CARTESIAN_POINT('',(10.0,10.0,10.0));
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
    path.write_text(content, encoding="utf-8")


def _write_minimal_binary_stl(
    path: Path,
    triangle_count: int = 1,
    header_prefix: bytes = b"CAD Copilot Binary STL",
    truncate_bytes: int = 0,
) -> None:
    """Write binary STL artifact with 80-byte header and triangle_count facets."""
    header = header_prefix.ljust(80, b"\x00")[:80]
    data = bytearray(header)
    data.extend(struct.pack("<I", triangle_count))

    for _ in range(triangle_count):
        data.extend(struct.pack("<3f", 0.0, 0.0, 1.0))  # normal
        data.extend(struct.pack("<3f", 0.0, 0.0, 0.0))  # v1
        data.extend(struct.pack("<3f", 10.0, 0.0, 0.0))  # v2
        data.extend(struct.pack("<3f", 0.0, 10.0, 0.0))  # v3
        data.extend(struct.pack("<H", 0))  # attribute byte count

    if truncate_bytes > 0:
        data = data[:-truncate_bytes]

    path.write_bytes(bytes(data))


def _write_minimal_ascii_stl(path: Path, name: str = "batch_test_mesh") -> None:
    """Write valid ASCII STL artifact with 1 facet."""
    content = f"""solid {name}
  facet normal 0.0 0.0 1.0
    outer loop
      vertex 0.0 0.0 0.0
      vertex 10.0 0.0 0.0
      vertex 0.0 10.0 0.0
    endloop
  endfacet
endsolid {name}
"""
    path.write_text(content, encoding="utf-8")


def _write_minimal_valid_pdf(path: Path, version: str = "1.4", padding_bytes: int = 0) -> None:
    """Write minimal valid PDF file."""
    header = f"%PDF-{version}\n".encode("ascii")
    body = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    if padding_bytes > 0:
        body += b"% " + b"X" * padding_bytes + b"\n"
    footer = b"xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 1 >>\nstartxref\n9\n%%EOF\n"
    path.write_bytes(header + body + footer)


def _write_minimal_valid_dxf(
    path: Path,
    with_bom: bool = False,
    line_ending: str = "\r\n",
) -> None:
    """Write minimal valid ASCII text DXF file."""
    lines = [
        "0",
        "SECTION",
        "2",
        "HEADER",
        "0",
        "ENDSEC",
        "0",
        "SECTION",
        "2",
        "ENTITIES",
        "0",
        "ENDSEC",
        "0",
        "EOF",
    ]
    raw = line_ending.join(lines) + line_ending
    if with_bom:
        raw = "\ufeff" + raw
    path.write_bytes(raw.encode("utf-8"))


def _write_minimal_valid_parasolid(path: Path) -> None:
    """Write minimal valid synthetic Parasolid text transmission file (.x_t)."""
    lines = [
        "**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**************************\r\n",
        "**PARASOLID !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~0123456789**************************\r\n",
        "**PART1;\r\n",
        "FORMAT=text;\r\n",
        "GUISE=transmit;\r\n",
        "**PART2;\r\n",
        "SCH=SCH_3800150_37102;\r\n",
        "1 2 3 4 5 6 7 8 9 10 11 12 13 14 15\r\n",
        "79 16 300 SolidEdge/Styles1 0 \r\n",
    ]
    path.write_bytes("".join(lines).encode("latin-1"))


# ---------------------------------------------------------------------------
# 1. Four-Format Happy Path & OutputSnapshot Invariants
# ---------------------------------------------------------------------------


def test_validate_batch_output_step_success(tmp_path: Path) -> None:
    """Proves valid STEP output passes validation and produces valid OutputSnapshot."""
    step_file = tmp_path / "model.step"
    _write_minimal_valid_step(step_file)

    snap = validate_batch_output("step", step_file)
    assert isinstance(snap, OutputSnapshot)
    assert snap.size_bytes == step_file.stat().st_size
    assert snap.size_bytes > 0
    assert snap.identity.inode != 0
    assert snap.mtime_ns > 0
    assert len(snap.sha256) == 64
    assert snap.sha256.islower()


def test_validate_batch_output_stl_binary_success(tmp_path: Path) -> None:
    """Proves valid binary STL output passes validation and produces valid OutputSnapshot."""
    stl_file = tmp_path / "model.stl"
    _write_minimal_binary_stl(stl_file, triangle_count=2)

    snap = validate_batch_output("stl", stl_file)
    assert isinstance(snap, OutputSnapshot)
    assert snap.size_bytes == 84 + 50 * 2
    assert snap.identity.inode != 0
    assert len(snap.sha256) == 64


def test_validate_batch_output_stl_ascii_success(tmp_path: Path) -> None:
    """Proves valid ASCII STL output passes validation and produces valid OutputSnapshot."""
    stl_file = tmp_path / "model.stl"
    _write_minimal_ascii_stl(stl_file)

    snap = validate_batch_output("stl", stl_file)
    assert isinstance(snap, OutputSnapshot)
    assert snap.size_bytes > 0
    assert snap.identity.inode != 0


def test_validate_batch_output_stl_binary_with_solid_header(tmp_path: Path) -> None:
    """Proves binary STL starting with 'solid' is correctly identified and validated."""
    stl_file = tmp_path / "model.stl"
    _write_minimal_binary_stl(stl_file, triangle_count=1, header_prefix=b"solid model binary")

    snap = validate_batch_output("stl", stl_file)
    assert isinstance(snap, OutputSnapshot)
    assert snap.size_bytes == 134


@pytest.mark.parametrize("pdf_version", ["1.0", "1.4", "1.7", "2.0"])
def test_validate_batch_output_pdf_versions_success(tmp_path: Path, pdf_version: str) -> None:
    """Proves supported PDF versions (1.0-1.7, 2.0) pass validation."""
    pdf_file = tmp_path / "drawing.pdf"
    _write_minimal_valid_pdf(pdf_file, version=pdf_version)

    snap = validate_batch_output("pdf", pdf_file)
    assert isinstance(snap, OutputSnapshot)
    assert snap.size_bytes == pdf_file.stat().st_size
    assert snap.identity.inode != 0


@pytest.mark.parametrize("line_ending", ["\r\n", "\n"])
@pytest.mark.parametrize("with_bom", [False, True])
def test_validate_batch_output_dxf_success(tmp_path: Path, line_ending: str, with_bom: bool) -> None:
    """Proves valid DXF with CRLF or LF, with or without BOM, passes validation."""
    dxf_file = tmp_path / "drawing.dxf"
    _write_minimal_valid_dxf(dxf_file, with_bom=with_bom, line_ending=line_ending)

    snap = validate_batch_output("dxf", dxf_file)
    assert isinstance(snap, OutputSnapshot)
    assert snap.size_bytes == dxf_file.stat().st_size
    assert snap.identity.inode != 0


def test_validate_batch_output_parasolid_success(tmp_path: Path) -> None:
    """Proves valid Parasolid text transmission output passes validation and produces valid OutputSnapshot."""
    parasolid_file = tmp_path / "model.x_t"
    _write_minimal_valid_parasolid(parasolid_file)

    snap = validate_batch_output("parasolid", parasolid_file)
    assert isinstance(snap, OutputSnapshot)
    assert snap.size_bytes == parasolid_file.stat().st_size
    assert snap.size_bytes > 0
    assert snap.identity.inode != 0
    assert snap.mtime_ns > 0
    assert len(snap.sha256) == 64
    assert snap.sha256.islower()


def test_validate_batch_output_satisfies_protocol() -> None:
    """Proves validate_batch_output conforms to BatchFormatValidator protocol."""
    validator: BatchFormatValidator = validate_batch_output
    assert callable(validator)


# ---------------------------------------------------------------------------
# 2. Common File & Dispatcher Checks
# ---------------------------------------------------------------------------


def test_validate_batch_output_unsupported_format(tmp_path: Path) -> None:
    """Proves passing an unsupported format raises BatchFormatValidationError."""
    file_path = tmp_path / "output.iges"
    file_path.write_text("dummy", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("iges", file_path)  # type: ignore[arg-type]

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.phase == "validation"
    assert "Unsupported batch output format" in exc_info.value.message


@pytest.mark.parametrize(
    "fmt,wrong_ext",
    [
        ("step", ".stp"),
        ("step", ".txt"),
        ("stl", ".mesh"),
        ("parasolid", ".step"),
        ("parasolid", ".x_b"),
        ("pdf", ".dxf"),
        ("dxf", ".pdf"),
    ],
)
def test_validate_batch_output_extension_mismatch(tmp_path: Path, fmt: BatchOutputFormat, wrong_ext: str) -> None:
    """Proves non-matching file extensions fail validation even if contents are valid."""
    work_file = tmp_path / f"output{wrong_ext}"
    work_file.write_bytes(b"dummy content")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output(fmt, work_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.format == fmt
    assert "extension mismatch" in exc_info.value.message.lower()


def test_validate_batch_output_file_not_found(tmp_path: Path) -> None:
    """Proves non-existent file raises sanitized BatchFormatValidationError."""
    missing = tmp_path / "missing.step"

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("step", missing)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "does not exist" in exc_info.value.message


def test_validate_batch_output_directory_rejected(tmp_path: Path) -> None:
    """Proves directory path is rejected as non-regular file."""
    dir_path = tmp_path / "fake.pdf"
    dir_path.mkdir()

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("pdf", dir_path)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "not a regular file" in exc_info.value.message


def test_validate_batch_output_zero_bytes(tmp_path: Path) -> None:
    """Proves 0-byte file fails with empty file diagnostic."""
    empty_file = tmp_path / "empty.stl"
    empty_file.write_bytes(b"")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("stl", empty_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "empty" in exc_info.value.message


def test_validate_batch_output_symlink_rejected(tmp_path: Path) -> None:
    """Proves symbolic links fail validation immediately."""
    target_file = tmp_path / "real.dxf"
    _write_minimal_valid_dxf(target_file)

    link_file = tmp_path / "link.dxf"
    try:
        link_file.symlink_to(target_file)
    except OSError:
        pytest.skip("Symlinks not supported in this test environment without elevation")
    except NotImplementedError:
        pytest.skip("Symlinks not supported in this test environment without elevation")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", link_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "symbolic link" in exc_info.value.message


# ---------------------------------------------------------------------------
# 3. STEP Structural Edge Cases & Failures
# ---------------------------------------------------------------------------


def test_validate_batch_output_step_missing_header(tmp_path: Path) -> None:
    """Proves STEP without HEADER fails validation."""
    step_file = tmp_path / "invalid.step"
    step_file.write_text("ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("step", step_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == STEP_VALIDATION_FAILED_MESSAGE


def test_validate_batch_output_step_missing_brep(tmp_path: Path) -> None:
    """Proves STEP without MANIFOLD_SOLID_BREP fails validation."""
    step_file = tmp_path / "no_brep.step"
    content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Test'),'2;1');
FILE_NAME('test.step','2026-09-10T12:00:00',('Tester'),('CAD Copilot'),'','','');
FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));
ENDSEC;
DATA;
#1 = CARTESIAN_POINT('',(0.0,0.0,0.0));
#10 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );
ENDSEC;
END-ISO-10303-21;
"""
    step_file.write_text(content, encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("step", step_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == STEP_VALIDATION_FAILED_MESSAGE


def test_validate_batch_output_step_unresolved_units(tmp_path: Path) -> None:
    """Proves STEP without resolved length units fails validation."""
    step_file = tmp_path / "no_units.step"
    content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Test'),'2;1');
FILE_NAME('test.step','2026-09-10T12:00:00',('Tester'),('CAD Copilot'),'','','');
FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));
ENDSEC;
DATA;
#1 = CARTESIAN_POINT('',(0.0,0.0,0.0));
#20 = MANIFOLD_SOLID_BREP('Body1',#30);
#30 = CLOSED_SHELL('Shell1',());
ENDSEC;
END-ISO-10303-21;
"""
    step_file.write_text(content, encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("step", step_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == STEP_VALIDATION_FAILED_MESSAGE


# ---------------------------------------------------------------------------
# 4. STL Structural Edge Cases & Failures
# ---------------------------------------------------------------------------


def test_validate_batch_output_stl_zero_triangles(tmp_path: Path) -> None:
    """Proves binary STL with 0 triangles fails validation."""
    stl_file = tmp_path / "zero_tris.stl"
    _write_minimal_binary_stl(stl_file, triangle_count=0)

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("stl", stl_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == STL_VALIDATION_FAILED_MESSAGE


def test_validate_batch_output_stl_truncated_facets(tmp_path: Path) -> None:
    """Proves binary STL truncated mid-facet fails validation."""
    stl_file = tmp_path / "truncated.stl"
    _write_minimal_binary_stl(stl_file, triangle_count=2, truncate_bytes=25)

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("stl", stl_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == STL_VALIDATION_FAILED_MESSAGE


def test_validate_batch_output_stl_non_finite_float(tmp_path: Path) -> None:
    """Proves binary STL containing NaN or Inf floats fails validation."""
    stl_file = tmp_path / "nan.stl"
    header = b"CAD Copilot Binary STL".ljust(80, b"\x00")
    data = bytearray(header)
    data.extend(struct.pack("<I", 1))
    data.extend(struct.pack("<3f", 0.0, 0.0, 1.0))
    data.extend(struct.pack("<3f", float("nan"), 0.0, 0.0))  # NaN coordinate
    data.extend(struct.pack("<3f", 10.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 0.0, 10.0, 0.0))
    data.extend(struct.pack("<H", 0))
    stl_file.write_bytes(bytes(data))

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("stl", stl_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == STL_VALIDATION_FAILED_MESSAGE


def test_validate_batch_output_stl_malformed_ascii(tmp_path: Path) -> None:
    """Proves malformed ASCII STL (missing endsolid) fails validation."""
    stl_file = tmp_path / "malformed.stl"
    stl_file.write_text("solid incomplete\nfacet normal 0 0 1\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("stl", stl_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == STL_VALIDATION_FAILED_MESSAGE


# ---------------------------------------------------------------------------
# 5. PDF Structural Edge Cases & Bounds
# ---------------------------------------------------------------------------


def test_validate_batch_output_pdf_leading_signature_failure(tmp_path: Path) -> None:
    """Proves PDF missing %PDF- prefix at offset 0 fails validation."""
    pdf_file = tmp_path / "bad_magic.pdf"
    pdf_file.write_bytes(b"JUNK%PDF-1.4\n1 0 obj\nendobj\n%%EOF\n")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("pdf", pdf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "PDF signature" in exc_info.value.message


@pytest.mark.parametrize("bad_ver", ["3.0", "1.70bad", "1.4abc"])
def test_validate_batch_output_pdf_unsupported_version(tmp_path: Path, bad_ver: str) -> None:
    """Proves PDF with unsupported or suffixed version token fails validation."""
    pdf_file = tmp_path / "bad_ver.pdf"
    pdf_file.write_bytes(f"%PDF-{bad_ver}\n1 0 obj\nendobj\n%%EOF\n".encode("ascii"))

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("pdf", pdf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "version token" in exc_info.value.message


def test_validate_batch_output_pdf_missing_eof_marker(tmp_path: Path) -> None:
    """Proves PDF missing %%EOF marker in tail window fails validation."""
    pdf_file = tmp_path / "no_eof.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nstartxref\n9\n")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("pdf", pdf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "EOF marker" in exc_info.value.message


def test_validate_batch_output_pdf_too_small(tmp_path: Path) -> None:
    """Proves PDF smaller than MIN_PDF_BYTES fails validation."""
    pdf_file = tmp_path / "tiny.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n")
    assert pdf_file.stat().st_size < MIN_PDF_BYTES

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("pdf", pdf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "too small" in exc_info.value.message


def test_validate_batch_output_pdf_drawing_byte_caps(tmp_path: Path) -> None:
    """Proves MAX_BATCH_DRAWING_BYTES bound: 100,000,000 accepted, 100,000,001 rejected."""
    pdf_file = tmp_path / "capped.pdf"
    header = b"%PDF-1.4\n"
    footer = b"\n%%EOF\n"

    # 1. Exactly 100,000,000 bytes: must be accepted
    with open(pdf_file, "wb") as f:
        f.write(header)
        f.seek(MAX_BATCH_DRAWING_BYTES - len(footer))
        f.write(footer)

    assert pdf_file.stat().st_size == MAX_BATCH_DRAWING_BYTES
    snap = validate_batch_output("pdf", pdf_file)
    assert snap.size_bytes == MAX_BATCH_DRAWING_BYTES

    # 2. Exactly 100,000,001 bytes: must be rejected
    with open(pdf_file, "wb") as f:
        f.write(header)
        f.seek(MAX_BATCH_DRAWING_BYTES + 1 - len(footer))
        f.write(footer)

    assert pdf_file.stat().st_size == MAX_BATCH_DRAWING_BYTES + 1
    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("pdf", pdf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "exceeds maximum allowed size" in exc_info.value.message


# ---------------------------------------------------------------------------
# 6. DXF Structural Edge Cases & Bounds
# ---------------------------------------------------------------------------


def test_validate_batch_output_dxf_binary_sentinel_rejected(tmp_path: Path) -> None:
    """Proves binary DXF file is rejected explicitly."""
    dxf_file = tmp_path / "binary.dxf"
    dxf_file.write_bytes(DXF_BINARY_SENTINEL + b"\r\n\x1a\x00extra binary data")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "Binary DXF format is not supported" in exc_info.value.message


def test_validate_batch_output_dxf_missing_section(tmp_path: Path) -> None:
    """Proves DXF without SECTION structure fails validation."""
    dxf_file = tmp_path / "no_sec.dxf"
    dxf_file.write_text("0\nHEADER\n0\nEOF\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "SECTION structure" in exc_info.value.message


def test_validate_batch_output_dxf_missing_eof(tmp_path: Path) -> None:
    """Proves DXF missing terminal EOF marker fails validation."""
    dxf_file = tmp_path / "no_eof.dxf"
    dxf_file.write_text("0\nSECTION\n2\nHEADER\n0\nENDSEC\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "terminal EOF marker" in exc_info.value.message


def test_validate_batch_output_dxf_non_integer_group_code(tmp_path: Path) -> None:
    """Proves non-integer group code fails with static message and never reflects input tokens."""
    dxf_file = tmp_path / "bad_code.dxf"
    sensitive_token = "CONFIDENTIAL_TOKEN_SECRET_98765"
    dxf_file.write_text(f"{sensitive_token}\nSECTION\n0\nEOF\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == "Malformed DXF: non-integer group code encountered"
    assert sensitive_token not in exc_info.value.message


def test_validate_batch_output_dxf_section_missing_name_followed_by_eof(tmp_path: Path) -> None:
    """Proves SECTION immediately followed by EOF without section name or ENDSEC is rejected."""
    dxf_file = tmp_path / "unnamed_sec.dxf"
    dxf_file.write_text("0\nSECTION\n0\nEOF\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "SECTION header missing valid section name" in exc_info.value.message


def test_validate_batch_output_dxf_unclosed_section_at_eof(tmp_path: Path) -> None:
    """Proves unclosed SECTION at EOF without ENDSEC is rejected."""
    dxf_file = tmp_path / "unclosed.dxf"
    dxf_file.write_text("0\nSECTION\n2\nHEADER\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "unclosed SECTION" in exc_info.value.message


def test_validate_batch_output_dxf_eof_inside_active_section(tmp_path: Path) -> None:
    """Proves terminal EOF encountered while inside an active section is rejected."""
    dxf_file = tmp_path / "eof_in_sec.dxf"
    dxf_file.write_text("0\nSECTION\n2\nHEADER\n0\nEOF\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "inside active section" in exc_info.value.message


def test_validate_batch_output_dxf_nested_section_rejected(tmp_path: Path) -> None:
    """Proves nested SECTION declarations are rejected."""
    dxf_file = tmp_path / "nested.dxf"
    dxf_file.write_text(
        "0\nSECTION\n2\nHEADER\n0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nENDSEC\n0\nEOF\n",
        encoding="utf-8",
    )

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "nested SECTION" in exc_info.value.message


def test_validate_batch_output_dxf_endsec_outside_section_rejected(tmp_path: Path) -> None:
    """Proves ENDSEC encountered outside any active section is rejected."""
    dxf_file = tmp_path / "orphan_endsec.dxf"
    dxf_file.write_text("0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "outside active section" in exc_info.value.message


def test_validate_batch_output_dxf_odd_lines_truncated(tmp_path: Path) -> None:
    """Proves DXF ending with a group code line without a value line fails validation."""
    dxf_file = tmp_path / "odd.dxf"
    dxf_file.write_text("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "missing value line" in exc_info.value.message


def test_validate_batch_output_dxf_content_after_eof(tmp_path: Path) -> None:
    """Proves unexpected non-blank content after terminal EOF fails validation."""
    dxf_file = tmp_path / "after_eof.dxf"
    dxf_file.write_text("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\nEXTRA_GARBAGE\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "Unexpected content after terminal EOF" in exc_info.value.message


def test_validate_batch_output_dxf_line_length_cap(tmp_path: Path) -> None:
    """Proves line exceeding MAX_DXF_LINE_CHARS fails validation."""
    dxf_file = tmp_path / "long_line.dxf"
    oversized_line = "A" * (MAX_DXF_LINE_CHARS + 10)
    dxf_file.write_text(f"0\nSECTION\n999\n{oversized_line}\n0\nEOF\n", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "maximum line length" in exc_info.value.message


def test_validate_batch_output_dxf_drawing_byte_caps(tmp_path: Path) -> None:
    """Proves MAX_BATCH_DRAWING_BYTES bound for DXF: 100,000,000 accepted, 100,000,001 rejected."""
    dxf_file = tmp_path / "capped.dxf"

    # 1. Exactly 100,000,000 bytes: must be accepted when structurally valid
    prefix = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
    suffix = b"0\nEOF\n"
    needed = MAX_BATCH_DRAWING_BYTES - len(prefix) - len(suffix)
    n_chunks = needed // 2000
    rem = needed % 2000
    chunk = b"999\n" + b"X" * 1995 + b"\n"
    rem_chunk = b"999\n" + b"X" * (rem - 5) + b"\n" if rem >= 5 else b""
    with open(dxf_file, "wb") as f:
        f.write(prefix)
        f.write(chunk * n_chunks)
        if rem_chunk:
            f.write(rem_chunk)
        f.write(suffix)

    assert dxf_file.stat().st_size == MAX_BATCH_DRAWING_BYTES
    snap = validate_batch_output("dxf", dxf_file)
    assert snap.size_bytes == MAX_BATCH_DRAWING_BYTES

    # 2. Exactly 100,000,001 bytes: must be rejected before parsing
    with open(dxf_file, "wb") as f:
        f.seek(MAX_BATCH_DRAWING_BYTES)  # size = 100,000,001
        f.write(b"\n")

    assert dxf_file.stat().st_size == MAX_BATCH_DRAWING_BYTES + 1
    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("dxf", dxf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert "exceeds maximum allowed size" in exc_info.value.message


# ---------------------------------------------------------------------------
# 7. Parasolid Structural Edge Cases & Dispatcher Integration
# ---------------------------------------------------------------------------


def test_validate_batch_output_parasolid_corrupted(tmp_path: Path) -> None:
    """Proves corrupted Parasolid output raises BatchFormatValidationError with sanitized message."""
    parasolid_file = tmp_path / "corrupted.x_t"
    parasolid_file.write_bytes(b"INVALID PARASOLID DATA PAYLOAD" * 5)

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("parasolid", parasolid_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.format == "parasolid"
    assert exc_info.value.phase == "validation"
    assert (
        exc_info.value.message == PARASOLID_VALIDATION_FAILED_MESSAGE or "Parasolid artifact" in exc_info.value.message
    )


def test_validate_batch_output_parasolid_empty_fails(tmp_path: Path) -> None:
    """Proves 0-byte Parasolid output fails common positive size check."""
    parasolid_file = tmp_path / "empty.x_t"
    parasolid_file.write_bytes(b"")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("parasolid", parasolid_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.format == "parasolid"
    assert "empty" in exc_info.value.message


def test_validate_batch_output_parasolid_mutation_detected(tmp_path: Path) -> None:
    """Proves modification between Parasolid structural validation and snapshot is caught."""
    parasolid_file = tmp_path / "mutated.x_t"
    _write_minimal_valid_parasolid(parasolid_file)

    orig_capture = capture_output_snapshot

    def _tampering_capture(p: Path) -> OutputSnapshot:
        real_snap = orig_capture(p)
        return OutputSnapshot(
            identity=real_snap.identity,
            size_bytes=real_snap.size_bytes + 10,
            mtime_ns=real_snap.mtime_ns,
            sha256=real_snap.sha256,
        )

    with (
        patch("batch.format_validation.capture_output_snapshot", side_effect=_tampering_capture),
        pytest.raises(BatchFormatValidationError) as exc_info,
    ):
        validate_batch_output("parasolid", parasolid_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.format == "parasolid"
    assert exc_info.value.phase == "snapshot"
    assert "modified during validation" in exc_info.value.message


# ---------------------------------------------------------------------------
# 8. Snapshot Stability & Mutation Guard
# ---------------------------------------------------------------------------


def test_validate_batch_output_detects_mutation_during_validation(tmp_path: Path) -> None:
    """Proves modification between structural validation and snapshot is caught."""
    pdf_file = tmp_path / "mutated.pdf"
    _write_minimal_valid_pdf(pdf_file)

    # Patch capture_output_snapshot to simulate file modification by returning altered snapshot
    orig_capture = capture_output_snapshot

    def _tampering_capture(p: Path) -> OutputSnapshot:
        real_snap = orig_capture(p)
        # Return different size to simulate modification
        return OutputSnapshot(
            identity=real_snap.identity,
            size_bytes=real_snap.size_bytes + 10,
            mtime_ns=real_snap.mtime_ns,
            sha256=real_snap.sha256,
        )

    with (
        patch("batch.format_validation.capture_output_snapshot", side_effect=_tampering_capture),
        pytest.raises(BatchFormatValidationError) as exc_info,
    ):
        validate_batch_output("pdf", pdf_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.phase == "snapshot"
    assert "modified during validation" in exc_info.value.message


def test_validate_batch_output_snapshot_capture_failure(tmp_path: Path) -> None:
    """Proves snapshot capture error raises sanitized BatchFormatValidationError."""
    step_file = tmp_path / "snap_fail.step"
    _write_minimal_valid_step(step_file)

    with (
        patch("batch.format_validation.capture_output_snapshot", side_effect=OSError("Device failure")),
        pytest.raises(BatchFormatValidationError) as exc_info,
    ):
        validate_batch_output("step", step_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.phase == "snapshot"
    assert "could not be captured" in exc_info.value.message


# ---------------------------------------------------------------------------
# 9. Diagnostic Sanitization & Bounded Length (SEC-07)
# ---------------------------------------------------------------------------


def test_validate_batch_output_sanitization_no_raw_paths(tmp_path: Path) -> None:
    """Proves diagnostic messages use stable constant strings and never leak raw paths or HRESULTs."""
    # Create invalid STEP in deeply nested path
    nested_dir = tmp_path / "SecretDir" / "Users" / "cad_user" / "Projects"
    nested_dir.mkdir(parents=True)
    step_file = nested_dir / "confidential_model.step"
    step_file.write_text("INVALID NON STEP DATA", encoding="utf-8")

    with pytest.raises(BatchFormatValidationError) as exc_info:
        validate_batch_output("step", step_file)

    assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.message == STEP_VALIDATION_FAILED_MESSAGE
    msg = exc_info.value.message
    # Must not contain Windows drive letters with paths or SecretDir
    assert "C:\\" not in msg
    assert "E:\\" not in msg
    assert "SecretDir" not in msg
    assert len(msg) <= 512
